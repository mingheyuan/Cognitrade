from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from time import sleep

from fastapi.testclient import TestClient

from research_system.api.app import create_app
from research_system.api.config import ApiConfig
from research_system.api.agent_os_run_service import AgentOSRunService
from research_system.runtime.agent_os import AgentOS
from research_system.runtime.platform.settings import Settings
from research_system.runtime.storage.jsonl import write_json
from research_system.runtime.storage.jsonl import read_jsonl
from research_system.schemas.artifacts import ArtifactProducer, ArtifactRecord
from research_system.schemas.execution_graph import ExecutionGraph
from research_system.schemas.execution_runtime import ExecutionRunResult, ExecutionRunSnapshot
from research_system.schemas.protocol import RequestEnvelope


class AgentOSApiTests(unittest.TestCase):
    def test_graph_delivery_marks_missing_main_agent_synthesis_without_final_event(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": repository_root / "domain_packs",
                }
            )
            agent_os = AgentOS(settings)
            agent_os.main_agent.llm_provider = None
            request = RequestEnvelope(
                intent="stock_check",
                user_query="分析 600000",
            )
            run_id, paths, _ = agent_os.run_store.create_run(request, "stock_check")
            graph = ExecutionGraph(
                domain_id="finance_ashare",
                objective=request.user_query,
                metadata={"intent": "stock_check"},
            )
            snapshot = ExecutionRunSnapshot(
                run_id=run_id,
                thread_id="thread-1",
                request_id=request.request_id,
                graph=graph,
                status="completed",
            )
            runner = agent_os._runner(run_id)
            runner.board.add(
                ArtifactRecord(
                    artifact_type="research_report",
                    run_id=run_id,
                    thread_id="thread-1",
                    request_id=request.request_id,
                    producer=ArtifactProducer(
                        executor="uzi_report_composer",
                        capability="finance.uzi.report.compose",
                        node_id="report",
                        domain_id="finance_ashare",
                    ),
                    content={"conclusion": "Pack 原始摘要", "evidence": []},
                    source_refs=[{"type": "test", "id": "source-1"}],
                    confidence=0.5,
                )
            )

            result = agent_os._ensure_graph_assistant_message(
                ExecutionRunResult(snapshot=snapshot)
            )
            assistant = next(
                artifact
                for artifact in runner.board.list_artifacts(run_id=run_id, status="active")
                if artifact.artifact_type == "assistant_message"
            )
            events = read_jsonl(paths.root / "execution_events.jsonl")

            self.assertIn(assistant.artifact_id, result.artifact_ids)
            self.assertEqual(result.snapshot.status, "completed_partial")
            self.assertEqual(assistant.content["delivery_status"], "synthesis_unavailable")
            self.assertTrue(assistant.content["synthesis_unavailable"])
            self.assertFalse(any(event["event_type"] == "assistant_message_ready" for event in events))
            self.assertTrue(any(event["event_type"] == "assistant_message_status" for event in events))

    def test_gated_graph_response_preserves_gated_status(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": repository_root / "domain_packs",
                }
            )
            agent_os = AgentOS(settings)
            request = RequestEnvelope(
                intent="domain:legal_contract",
                user_query="Review the contract.",
            )
            run_id, paths, _ = agent_os.run_store.create_run(
                request,
                "domain:legal_contract",
            )
            graph = ExecutionGraph(
                domain_id="legal_contract",
                objective=request.user_query,
            )
            snapshot = ExecutionRunSnapshot(
                run_id=run_id,
                thread_id="thread-1",
                request_id=request.request_id,
                graph=graph,
                status="gated",
            )
            write_json(
                paths.root / "execution_snapshot.json",
                snapshot.model_dump(mode="json"),
            )

            response = agent_os._response_from_graph(
                request,
                ExecutionRunResult(snapshot=snapshot),
            )

            self.assertEqual(response.status, "gated")

    def test_workspace_and_graph_endpoints_use_the_same_agent_os_state(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": repository_root / "domain_packs",
                }
            )
            client = TestClient(create_app(settings=settings))
            packs = client.get("/api/domain-packs")
            self.assertEqual(packs.status_code, 200)
            self.assertIn("legal_contract", [row["domain_id"] for row in packs.json()])
            page = client.get("/")
            self.assertEqual(page.status_code, 200)
            self.assertIn("Execution Graph", page.text)

            run = client.post(
                "/api/runs",
                json={
                    "domain_id": "legal_contract",
                    "objective": "Review a contract.",
                    "artifacts": [
                        {
                            "artifact_type": "contract_document",
                            "content": {"text": "Supplier shall deliver."},
                            "source_refs": [{"type": "contract", "id": "contract-1"}],
                        }
                    ],
                },
            )
            self.assertEqual(run.status_code, 200, run.text)
            payload = run.json()
            run_id = payload["snapshot"]["run_id"]
            self.assertEqual(payload["snapshot"]["status"], "completed")
            inspected = client.get(f"/api/runs/{run_id}")
            self.assertEqual(inspected.status_code, 200)
            self.assertEqual(inspected.json()["snapshot"]["graph"]["domain_id"], "legal_contract")
            replay = client.get(f"/api/runs/{run_id}/replay")
            self.assertEqual(replay.status_code, 200)
            self.assertEqual(replay.json()["status"], "completed")

    def test_unversioned_workspace_runs_use_the_same_identity_scope(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": repository_root / "domain_packs",
                }
            )
            with TestClient(
                create_app(
                    settings=settings,
                    api_config=ApiConfig(api_token="workspace-scope-token"),
                )
            ) as client:
                first_headers = {
                    "Authorization": "Bearer workspace-scope-token",
                    "X-User-ID": "workspace-user-a",
                    "X-Tenant-ID": "workspace-tenant-a",
                }
                second_headers = {
                    "Authorization": "Bearer workspace-scope-token",
                    "X-User-ID": "workspace-user-b",
                    "X-Tenant-ID": "workspace-tenant-b",
                }
                response = client.post(
                    "/api/runs",
                    headers=first_headers,
                    json={
                        "domain_id": "legal_contract",
                        "objective": "Review the supplied contract.",
                        "artifacts": [
                            {
                                "artifact_type": "contract_document",
                                "content": {"text": "Supplier shall deliver."},
                                "source_refs": [{"type": "contract", "id": "workspace-1"}],
                            }
                        ],
                    },
                )
                self.assertEqual(response.status_code, 200, response.text)
                run_id = response.json()["snapshot"]["run_id"]

                own = client.get(f"/api/runs/{run_id}", headers=first_headers)
                cross = client.get(f"/api/runs/{run_id}", headers=second_headers)
                history = client.get("/api/runs", headers=second_headers)

                self.assertEqual(own.status_code, 200, own.text)
                self.assertEqual(cross.status_code, 404, cross.text)
                self.assertEqual(history.json(), [])

    def test_versioned_runs_use_agent_os_execution_state(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": repository_root / "domain_packs",
                }
            )
            with TestClient(
                create_app(
                    settings=settings,
                    api_config=ApiConfig(api_token="agent-os-api-test"),
                )
            ) as client:
                headers = {"Authorization": "Bearer agent-os-api-test"}
                response = client.post(
                    "/api/v1/runs",
                    headers=headers,
                    json={
                        "user_query": "Review supplied evidence for 600000.",
                        "slots": {
                            "symbols": ["600000"],
                            "snapshots": [
                                {
                                    "snapshot_type": "market",
                                    "symbol": "600000",
                                    "facts": {"market_overview": {"spot": {"latest_price": 10}}},
                                }
                            ],
                        },
                    },
                )
                self.assertEqual(response.status_code, 200, response.text)
                payload = response.json()
                self.assertEqual(payload["result"]["protocol"], "agent_os")
                self.assertEqual(payload["intent"], "stock_check")
                run_id = payload["run_id"]

                steps = client.get(f"/api/v1/runs/{run_id}/steps", headers=headers)
                self.assertEqual(steps.status_code, 200, steps.text)
                self.assertEqual(steps.json()["domain_id"], "finance_ashare")
                self.assertTrue(steps.json()["steps"])

                events = client.get(f"/api/v1/runs/{run_id}/events", headers=headers)
                self.assertEqual(events.status_code, 200, events.text)
                event_rows = events.json()["events"]
                self.assertTrue(event_rows)
                self.assertEqual(payload["status"], "completed_partial")
                self.assertTrue(any(event["event_type"] == "assistant_message_status" for event in event_rows))
                self.assertTrue(any(event["event_type"] == "artifact_committed" for event in event_rows))
                self.assertTrue(all(event.get("run_id") == run_id for event in event_rows))
                self.assertTrue(all(event.get("thread_id") for event in event_rows))
                self.assertTrue(all(event.get("request_id") for event in event_rows))
                history = client.get("/api/v1/runs", headers=headers)
                self.assertEqual(history.status_code, 200, history.text)
                history_row = next(row for row in history.json() if row["run_id"] == run_id)
                self.assertTrue(history_row["message"])
                self.assertTrue(history_row["display_summary"])
                metrics = client.get(f"/api/v1/runs/{run_id}/metrics", headers=headers)
                self.assertEqual(metrics.status_code, 200, metrics.text)
                self.assertGreaterEqual(metrics.json()["counts"]["subagents"], 1)

    def test_chat_clarification_continues_the_same_agent_os_run(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": repository_root / "domain_packs",
                }
            )
            with TestClient(
                create_app(
                    settings=settings,
                    api_config=ApiConfig(api_token=None, allow_unauthenticated=True),
                )
            ) as client:
                thread_response = client.post("/api/v1/chat/threads")
                self.assertEqual(thread_response.status_code, 201, thread_response.text)
                thread_id = thread_response.json()["thread_id"]

                first = client.post(
                    f"/api/v1/chat/threads/{thread_id}/runs",
                    json={
                        "intent": "stock_check",
                        "user_query": "Analyze a stock.",
                    },
                )
                self.assertEqual(first.status_code, 200, first.text)
                first_payload = first.json()
                self.assertEqual(first_payload["status"], "needs_clarification")
                first_run_id = first_payload["run_id"]

                second = client.post(
                    f"/api/v1/chat/threads/{thread_id}/runs",
                    json={"user_query": "600000"},
                )
                self.assertEqual(second.status_code, 200, second.text)
                second_payload = second.json()
                self.assertEqual(second_payload["status"], "completed_partial")
                self.assertEqual(second_payload["run_id"], first_run_id)

    def test_background_run_returns_a_run_id_before_completion_and_exposes_events(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": root / "empty_packs",
                }
            )
            with TestClient(
                create_app(
                    settings=settings,
                    api_config=ApiConfig(api_token=None, allow_unauthenticated=True),
                )
            ) as client:
                response = client.post(
                    "/api/v1/runs",
                    json={
                        "user_query": "Who are you?",
                        "constraints": {"background": True},
                    },
                )
                self.assertEqual(response.status_code, 200, response.text)
                payload = response.json()
                self.assertEqual(payload["status"], "running")
                run_id = payload["run_id"]

                initial_replay = client.get(f"/api/v1/runs/{run_id}/replay")
                self.assertEqual(initial_replay.status_code, 200, initial_replay.text)
                self.assertIn(initial_replay.json()["mode"], {"pending", "core"})

                terminal = None
                for _ in range(60):
                    events = client.get(f"/api/v1/runs/{run_id}/events").json()
                    if events["status"] in {"completed", "failed", "cancelled", "timed_out", "gated"}:
                        terminal = events
                        break
                    sleep(0.1)
                self.assertIsNotNone(terminal)
                assert terminal is not None
                self.assertTrue(terminal["events"])

    def test_new_run_service_recovers_a_persisted_background_reservation(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": root / "empty_packs",
                }
            )
            request = RequestEnvelope(
                user_query="Who are you?",
                constraints={"background": True},
            )
            first = AgentOS(settings)
            run_id = first.prepare_run(request, run_id="recovered-background")
            service = AgentOSRunService(AgentOS(settings))
            try:
                for _ in range(60):
                    detail = service.agent_os.inspect_run(run_id)
                    if detail.get("mode") == "core":
                        state = detail["snapshot"]
                        if state.get("status") in {"completed", "failed", "cancelled"}:
                            break
                    sleep(0.1)
                detail = service.agent_os.inspect_run(run_id)
            finally:
                service.close()

            self.assertEqual(detail["mode"], "core")
            self.assertEqual(detail["snapshot"]["status"], "completed")

    def test_background_worker_lease_prevents_duplicate_process_claims(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": root / "empty_packs",
                }
            )
            first = AgentOSRunService(AgentOS(settings))
            second = AgentOSRunService(AgentOS(settings))
            try:
                request = RequestEnvelope(
                    user_query="Who are you?",
                    constraints={"background": True},
                )
                run_id = first.agent_os.prepare_run(request, run_id="leased-background")
                self.assertTrue(first._claim_background_lease(run_id))
                self.assertFalse(second._claim_background_lease(run_id))
                first._release_background_lease(run_id)
                self.assertTrue(second._claim_background_lease(run_id))
                second._release_background_lease(run_id)
                self.assertFalse(
                    (settings.runs_root / run_id / "agent_os_worker_lease.json").exists()
                )
            finally:
                first.close()
                second.close()


if __name__ == "__main__":
    unittest.main()
