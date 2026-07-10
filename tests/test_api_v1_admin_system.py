from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from api_test_support import API_TOKEN, auth_headers
from research_system.api.app import create_app
from research_system.api.config import ApiConfig
from research_system.runtime.core.artifact_board import ArtifactBoard
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout
from research_system.runtime.storage.jsonl import write_json
from research_system.schemas.artifacts import ArtifactProducer, ArtifactRecord
from research_system.schemas.execution_graph import ExecutionGraph, ExecutionNode
from research_system.schemas.execution_runtime import ExecutionRunSnapshot, NodeExecutionState
from research_system.services.memory.layers import LayeredMemoryStore


REPO_ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "01J00000000000000000000002"
APPROVAL_ID = "01J00000000000000000000003"


class _FakeQdrant:
    instances: list["_FakeQdrant"] = []

    def __init__(self, _settings) -> None:
        self.available = True
        self.reset_called = False
        self.upserted: list[object] = []
        self.closed = False
        self.instances.append(self)

    def reset_collection(self) -> None:
        self.reset_called = True

    def upsert_artifact(self, artifact) -> None:
        self.upserted.append(artifact)

    def close(self) -> None:
        self.closed = True


class ApiV1AdminSystemTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.settings = Settings.from_env(REPO_ROOT).model_copy(
            update={
                "workspace_root": root,
                "runs_root": root / "runs",
                "memory_root": root / "memory",
                "domain_packs_root": REPO_ROOT / "domain_packs",
            }
        )
        ensure_workspace_layout(self.settings)
        self.app = create_app(
            settings=self.settings,
            api_config=ApiConfig(api_token=API_TOKEN),
            repo_root=REPO_ROOT,
        )
        self.client_context = TestClient(self.app, raise_server_exceptions=False)
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.temp_dir.cleanup()

    def _write_high_risk_run(self) -> None:
        graph = ExecutionGraph(
            domain_id="finance_ashare",
            objective="Approval-gated test operation",
            nodes=[
                ExecutionNode(
                    node_id="publish",
                    node_kind="subagent",
                    executor="approval_test_executor",
                    capability="test.publish",
                    side_effect_level="write_external",
                    approval_id=APPROVAL_ID,
                )
            ],
        )
        snapshot = ExecutionRunSnapshot(
            run_id=RUN_ID,
            thread_id="thread-test",
            request_id="request-test",
            graph=graph,
            status="paused",
            node_states={"publish": NodeExecutionState(node_id="publish")},
        )
        root = self.settings.runs_root / RUN_ID
        root.mkdir(parents=True, exist_ok=True)
        write_json(root / "execution_snapshot.json", snapshot.model_dump(mode="json"))

    def assert_error(self, response, *, status_code: int, code: str) -> None:
        self.assertEqual(response.status_code, status_code, response.text)
        self.assertEqual(response.json()["error"]["code"], code)

    def test_domain_pack_resources_are_canonical_registry_views(self) -> None:
        listed = self.client.get("/api/v1/admin/domain-packs", headers=auth_headers())
        self.assertEqual(listed.status_code, 200, listed.text)
        domain_ids = {row["domain_id"] for row in listed.json()}
        self.assertIn("finance_ashare", domain_ids)
        self.assertIn("legal_contract", domain_ids)

        detail = self.client.get(
            "/api/v1/admin/domain-packs/finance_ashare",
            headers=auth_headers(),
        )
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["manifest"]["domain_id"], "finance_ashare")
        self.assertIn("capabilities", detail.json()["manifest"])

        missing = self.client.get(
            "/api/v1/admin/domain-packs/missing_pack",
            headers=auth_headers(),
        )
        self.assert_error(missing, status_code=404, code="resource_not_found")

    def test_run_approval_is_bound_to_the_graph_node_and_idempotent(self) -> None:
        self._write_high_risk_run()
        payload = {
            "node_id": "publish",
            "capability": "test.publish",
            "approval_id": APPROVAL_ID,
            "reason": "Approved for controlled delivery.",
        }

        created = self.client.post(
            f"/api/v1/admin/runs/{RUN_ID}/approvals",
            headers=auth_headers(),
            json=payload,
        )
        repeated = self.client.post(
            f"/api/v1/admin/runs/{RUN_ID}/approvals",
            headers=auth_headers(),
            json=payload,
        )
        listed = self.client.get(
            f"/api/v1/admin/runs/{RUN_ID}/approvals?status=approved",
            headers=auth_headers(),
        )

        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual(created.json()["approved_by"], "api_v1:api_token")
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertEqual(repeated.json(), created.json())
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.json(), [created.json()])

        mismatch = self.client.post(
            f"/api/v1/admin/runs/{RUN_ID}/approvals",
            headers=auth_headers(),
            json={**payload, "capability": "test.other"},
        )
        self.assert_error(mismatch, status_code=409, code="approval_conflict")

        unknown_node = self.client.post(
            f"/api/v1/admin/runs/{RUN_ID}/approvals",
            headers=auth_headers(),
            json={**payload, "node_id": "missing"},
        )
        self.assert_error(unknown_node, status_code=404, code="nested_resource_not_found")

    def test_memory_resource_reads_agent_os_layered_memory(self) -> None:
        memory = LayeredMemoryStore(self.settings.memory_root / "agent_os")
        record = memory.add_episode(
            domain_id="finance_ashare",
            memory_type="research_result",
            content={"conclusion": "verified"},
            source_run_id=RUN_ID,
            source_artifact_ids=["artifact-test"],
            gated=True,
            gate_evaluations=[{"gate_id": "delivery", "verdict": "pass"}],
        )

        response = self.client.get(
            "/api/v1/admin/memory?layer=episodic&domain_id=finance_ashare",
            headers=auth_headers(),
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([row["memory_id"] for row in response.json()], [record.memory_id])
        self.assertEqual(response.json()[0]["source_artifact_ids"], ["artifact-test"])

    def test_cleanup_and_reindex_use_agent_os_storage(self) -> None:
        root = self.settings.runs_root / RUN_ID
        board = ArtifactBoard(root / "artifact_board.jsonl")
        board.bind_thread("thread-test")
        board.add(
            ArtifactRecord(
                run_id=RUN_ID,
                thread_id="thread-test",
                request_id="request-test",
                artifact_type="research_note",
                schema_version="1",
                producer=ArtifactProducer(
                    executor="test_executor",
                    capability="test.note",
                    domain_id="finance_ashare",
                ),
                content={"value": "index me"},
                confidence=0.9,
            )
        )

        cleanup = self.client.post(
            "/api/v1/admin/artifacts/cleanup",
            headers=auth_headers(),
        )
        with patch("research_system.api.agent_os_admin_service.QdrantAdapter", _FakeQdrant):
            reindex = self.client.post(
                "/api/v1/admin/qdrant/reindex",
                headers=auth_headers(),
            )

        self.assertEqual(cleanup.status_code, 200, cleanup.text)
        self.assertEqual(cleanup.json()["removed_files"], [])
        self.assertEqual(reindex.status_code, 200, reindex.text)
        self.assertEqual(reindex.json()["status"], "completed")
        self.assertEqual(reindex.json()["indexed_entries"], 1)
        self.assertTrue(_FakeQdrant.instances[-1].reset_called)
        self.assertTrue(_FakeQdrant.instances[-1].closed)

    def test_retired_proposal_and_discovery_routes_are_not_exposed(self) -> None:
        for path in (
            "/api/v1/admin/proposals",
            "/api/v1/admin/subagent-discoveries",
        ):
            with self.subTest(path=path):
                response = self.client.get(path, headers=auth_headers())
                self.assertEqual(response.status_code, 404)

    def test_system_status_doctor_and_checks_return_underlying_status(self) -> None:
        status_payload = {"total_runs": 2, "status_counts": {"completed": 2}, "recent_runs": []}
        doctor_payload = {"status": "completed", "checks": []}
        checks_payload = {"status": "completed", "checks": []}

        with patch("research_system.api.routes.system.build_status", return_value=status_payload):
            status = self.client.get("/api/v1/system/status", headers=auth_headers())
        with patch("research_system.api.routes.system.run_doctor", return_value=doctor_payload):
            doctor = self.client.post("/api/v1/system/doctor", headers=auth_headers())
        with patch("research_system.api.routes.system.run_checks", return_value=checks_payload):
            checks = self.client.post("/api/v1/admin/system/checks", headers=auth_headers())

        self.assertEqual(status.json(), status_payload)
        self.assertEqual(doctor.json(), doctor_payload)
        self.assertEqual(checks.json(), checks_payload)


if __name__ == "__main__":
    unittest.main()
