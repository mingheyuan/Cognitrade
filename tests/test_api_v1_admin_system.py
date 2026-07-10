from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from api_test_support import (
    API_TOKEN,
    FakeOrchestrator,
    FakeOrchestratorFactory,
    auth_headers,
)
from research_system.api.app import create_app
from research_system.api.config import ApiConfig
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout


REPO_ROOT = Path(__file__).resolve().parents[1]
PROPOSAL_ID = "proposal_test"
TICKET_ID = "ticket_test"


class ApiV1AdminSystemTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.settings = Settings.from_env(Path(self.temp_dir.name))
        ensure_workspace_layout(self.settings)
        self.orchestrator = FakeOrchestrator()
        self.app = create_app(
            settings=self.settings,
            api_config=ApiConfig(api_token=API_TOKEN),
            orchestrator_factory=FakeOrchestratorFactory(self.orchestrator),
            repo_root=REPO_ROOT,
        )
        self.client_context = TestClient(self.app, raise_server_exceptions=False)
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.temp_dir.cleanup()

    def assert_error(self, response, *, status_code: int, code: str) -> None:
        self.assertEqual(response.status_code, status_code)
        self.assertEqual(response.json()["error"]["code"], code)

    def test_proposal_list_and_detail_forward_filters_and_identifier(self) -> None:
        rows = [
            {
                "proposal_id": PROPOSAL_ID,
                "status": "approved",
                "proposal_type": "MethodologyTemplatePatch",
            }
        ]
        detail = {
            "proposal_id": PROPOSAL_ID,
            "status": "approved",
            "title": "test proposal",
        }
        self.orchestrator.outputs["list_proposals"] = rows
        self.orchestrator.outputs["show_proposal"] = detail

        listed = self.client.get(
            "/api/v1/admin/proposals?status=approved&limit=7",
            headers=auth_headers(),
        )
        shown = self.client.get(
            f"/api/v1/admin/proposals/{PROPOSAL_ID}",
            headers=auth_headers(),
        )

        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json(), rows)
        list_call = self.orchestrator.calls_for("list_proposals")[-1]
        self.assertEqual(list_call.args, ())
        self.assertEqual(list_call.kwargs, {"status": "approved", "limit": 7})
        self.assertEqual(shown.status_code, 200)
        self.assertEqual(shown.json(), detail)
        self.assertEqual(
            self.orchestrator.calls_for("show_proposal")[-1].args,
            (PROPOSAL_ID,),
        )

    def test_proposal_review_derives_reviewer_and_same_decision_is_idempotent(self) -> None:
        self.orchestrator.outputs["show_proposal"] = {
            "proposal_id": PROPOSAL_ID,
            "status": "approved",
        }
        expected = {
            "proposal_id": PROPOSAL_ID,
            "status": "approved",
            "message": "Proposal approved",
        }
        self.orchestrator.outputs["review_proposal"] = expected

        response = self.client.post(
            f"/api/v1/admin/proposals/{PROPOSAL_ID}/reviews",
            headers=auth_headers(),
            json={"decision": "approve", "reason": "thresholds passed"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), expected)
        review_call = self.orchestrator.calls_for("review_proposal")[-1]
        self.assertEqual(review_call.args, (PROPOSAL_ID,))
        self.assertEqual(
            review_call.kwargs,
            {
                "decision": "approve",
                "reviewer": "api_v1:api_token",
                "reason": "thresholds passed",
            },
        )

    def test_proposal_review_rejects_reverse_final_decision(self) -> None:
        self.orchestrator.outputs["show_proposal"] = {
            "proposal_id": PROPOSAL_ID,
            "status": "approved",
        }

        response = self.client.post(
            f"/api/v1/admin/proposals/{PROPOSAL_ID}/reviews",
            headers=auth_headers(),
            json={"decision": "reject", "reason": "changed mind"},
        )

        self.assert_error(
            response,
            status_code=409,
            code="proposal_already_finalized",
        )
        self.assertEqual(self.orchestrator.calls_for("review_proposal"), [])

    def test_proposal_review_forbids_client_reviewer_injection(self) -> None:
        response = self.client.post(
            f"/api/v1/admin/proposals/{PROPOSAL_ID}/reviews",
            headers=auth_headers(),
            json={
                "decision": "approve",
                "reviewer": "attacker",
            },
        )

        self.assert_error(response, status_code=422, code="validation_error")
        self.assertEqual(self.orchestrator.calls_for("show_proposal"), [])
        self.assertEqual(self.orchestrator.calls_for("review_proposal"), [])

    def test_discovery_list_and_detail_forward_filters_and_identifier(self) -> None:
        rows = [
            {
                "ticket_id": TICKET_ID,
                "status": "pending",
                "capability": "market_snapshot",
            }
        ]
        detail = {
            "ticket_id": TICKET_ID,
            "status": "pending",
            "candidate_catalog_entries": [],
        }
        self.orchestrator.outputs["list_subagent_discovery_requests"] = rows
        self.orchestrator.outputs["show_subagent_discovery_request"] = detail

        listed = self.client.get(
            "/api/v1/admin/subagent-discoveries?status=pending&limit=9",
            headers=auth_headers(),
        )
        shown = self.client.get(
            f"/api/v1/admin/subagent-discoveries/{TICKET_ID}",
            headers=auth_headers(),
        )

        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json(), rows)
        list_call = self.orchestrator.calls_for("list_subagent_discovery_requests")[-1]
        self.assertEqual(list_call.args, ())
        self.assertEqual(list_call.kwargs, {"status": "pending", "limit": 9})
        self.assertEqual(shown.status_code, 200)
        self.assertEqual(shown.json(), detail)
        self.assertEqual(
            self.orchestrator.calls_for("show_subagent_discovery_request")[-1].args,
            (TICKET_ID,),
        )

    def test_discovery_review_derives_reviewer_and_does_not_accept_candidates(self) -> None:
        self.orchestrator.outputs["show_subagent_discovery_request"] = {
            "ticket_id": TICKET_ID,
            "status": "pending",
        }
        expected = {
            "ticket_id": TICKET_ID,
            "status": "approved",
            "message": "Discovery ticket approved",
        }
        self.orchestrator.outputs["review_subagent_discovery_request"] = expected

        response = self.client.post(
            f"/api/v1/admin/subagent-discoveries/{TICKET_ID}/reviews",
            headers=auth_headers(),
            json={"decision": "approve", "reason": "read-only contract"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), expected)
        review_call = self.orchestrator.calls_for("review_subagent_discovery_request")[-1]
        self.assertEqual(review_call.args, (TICKET_ID,))
        self.assertEqual(
            review_call.kwargs,
            {
                "decision": "approve",
                "reviewer": "api_v1:api_token",
                "reason": "read-only contract",
                "candidate_catalog_entries": None,
            },
        )

    def test_discovery_review_same_decision_is_idempotent_and_reverse_is_409(self) -> None:
        self.orchestrator.outputs["show_subagent_discovery_request"] = {
            "ticket_id": TICKET_ID,
            "status": "approved",
        }
        self.orchestrator.outputs["review_subagent_discovery_request"] = {
            "ticket_id": TICKET_ID,
            "status": "approved",
            "message": "Discovery ticket approved",
        }

        same = self.client.post(
            f"/api/v1/admin/subagent-discoveries/{TICKET_ID}/reviews",
            headers=auth_headers(),
            json={"decision": "approve"},
        )
        reverse = self.client.post(
            f"/api/v1/admin/subagent-discoveries/{TICKET_ID}/reviews",
            headers=auth_headers(),
            json={"decision": "reject"},
        )

        self.assertEqual(same.status_code, 200)
        self.assertEqual(same.json()["status"], "approved")
        self.assert_error(
            reverse,
            status_code=409,
            code="discovery_ticket_already_finalized",
        )
        self.assertEqual(
            len(self.orchestrator.calls_for("review_subagent_discovery_request")),
            1,
        )

    def test_discovery_review_forbids_reviewer_and_candidate_injection(self) -> None:
        injected_payloads = (
            {
                "decision": "approve",
                "reviewer": "attacker",
            },
            {
                "decision": "approve",
                "candidate_catalog_entries": [{"subagent_type": "unsafe"}],
            },
        )

        for payload in injected_payloads:
            with self.subTest(payload=payload):
                response = self.client.post(
                    f"/api/v1/admin/subagent-discoveries/{TICKET_ID}/reviews",
                    headers=auth_headers(),
                    json=payload,
                )
                self.assert_error(response, status_code=422, code="validation_error")

        self.assertEqual(
            self.orchestrator.calls_for("show_subagent_discovery_request"),
            [],
        )
        self.assertEqual(
            self.orchestrator.calls_for("review_subagent_discovery_request"),
            [],
        )

    def test_discovery_activation_returns_201_then_200_and_derives_actor(self) -> None:
        activation_body = {
            "candidate_subagent_type": "new_market_reader",
            "delegate_subagent_type": "market_snapshot_reader",
            "reason": "reuse reviewed reader",
        }
        self.orchestrator.outputs["activate_subagent_discovery_request"] = {
            "ticket_id": TICKET_ID,
            "status": "approved",
            "message": "Discovery candidate activated",
            "result": {},
        }

        created = self.client.post(
            f"/api/v1/admin/subagent-discoveries/{TICKET_ID}/activations",
            headers=auth_headers(),
            json=activation_body,
        )
        first_call = self.orchestrator.calls_for(
            "activate_subagent_discovery_request"
        )[-1]
        self.orchestrator.outputs["activate_subagent_discovery_request"] = {
            "ticket_id": TICKET_ID,
            "status": "approved",
            "message": "Discovery candidate already exists",
            "result": {},
        }
        repeated = self.client.post(
            f"/api/v1/admin/subagent-discoveries/{TICKET_ID}/activations",
            headers=auth_headers(),
            json=activation_body,
        )

        self.assertEqual(created.status_code, 201)
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(first_call.args, (TICKET_ID,))
        self.assertEqual(
            first_call.kwargs,
            {
                **activation_body,
                "activated_by": "api_v1:api_token",
            },
        )

    def test_discovery_activation_forbids_actor_reviewer_and_candidate_injection(self) -> None:
        injected_fields = (
            ("activated_by", "attacker"),
            ("reviewer", "attacker"),
            ("candidate_catalog_entries", [{"subagent_type": "unsafe"}]),
            ("openapi_spec_uri", "http://169.254.169.254/latest/meta-data"),
        )

        for field_name, value in injected_fields:
            with self.subTest(field_name=field_name):
                body = {
                    "delegate_subagent_type": "market_snapshot_reader",
                    field_name: value,
                }
                response = self.client.post(
                    f"/api/v1/admin/subagent-discoveries/{TICKET_ID}/activations",
                    headers=auth_headers(),
                    json=body,
                )
                self.assert_error(response, status_code=422, code="validation_error")

        self.assertEqual(
            self.orchestrator.calls_for("activate_subagent_discovery_request"),
            [],
        )

    def test_system_status_doctor_and_checks_return_underlying_status(self) -> None:
        status_payload = {
            "total_runs": 2,
            "status_counts": {"completed": 1, "failed": 1},
            "recent_runs": [],
        }
        doctor_payload = {
            "status": "failed",
            "checks": [{"name": "network_overseas", "status": "failed"}],
        }
        checks_payload = {
            "status": "completed",
            "checks": [{"name": "unit_tests", "status": "passed"}],
        }

        with patch(
            "research_system.api.routes.system.build_status",
            return_value=status_payload,
        ) as build_status_mock:
            status = self.client.get(
                "/api/v1/system/status",
                headers=auth_headers(),
            )
        with patch(
            "research_system.api.routes.system.run_doctor",
            return_value=doctor_payload,
        ) as run_doctor_mock:
            doctor = self.client.post(
                "/api/v1/system/doctor",
                headers=auth_headers(),
            )
        with patch(
            "research_system.api.routes.system.run_checks",
            return_value=checks_payload,
        ) as run_checks_mock:
            checks = self.client.post(
                "/api/v1/admin/system/checks",
                headers=auth_headers(),
            )

        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json(), status_payload)
        build_status_mock.assert_called_once_with(self.settings)
        self.assertEqual(doctor.status_code, 200)
        self.assertEqual(doctor.json(), doctor_payload)
        run_doctor_mock.assert_called_once_with(self.settings)
        self.assertEqual(checks.status_code, 200)
        self.assertEqual(checks.json(), checks_payload)
        run_checks_mock.assert_called_once_with(REPO_ROOT)

    def test_cleanup_and_reindex_preserve_domain_status_payloads(self) -> None:
        cleanup_payload = {
            "artifacts_retention_days": 30,
            "removed_files": [],
            "skipped_runs": [],
            "size_limit_exceeded": False,
        }
        self.orchestrator.outputs["cleanup"] = cleanup_payload

        cleanup = self.client.post(
            "/api/v1/admin/artifacts/cleanup",
            headers=auth_headers(),
        )
        self.orchestrator.outputs["rebuild_qdrant"] = {
            "status": "skipped",
            "message": "Qdrant unavailable",
            "qdrant_error": "not configured",
        }
        skipped_reindex = self.client.post(
            "/api/v1/admin/qdrant/reindex",
            headers=auth_headers(),
        )
        self.orchestrator.outputs["rebuild_qdrant"] = {
            "status": "completed",
            "message": "Qdrant collection rebuilt",
            "indexed_runs": 2,
            "indexed_entries": 10,
        }
        completed_reindex = self.client.post(
            "/api/v1/admin/qdrant/reindex",
            headers=auth_headers(),
        )

        self.assertEqual(cleanup.status_code, 200)
        self.assertEqual(cleanup.json(), cleanup_payload)
        self.assertEqual(len(self.orchestrator.calls_for("cleanup")), 1)
        self.assertEqual(skipped_reindex.status_code, 200)
        self.assertEqual(skipped_reindex.json()["status"], "skipped")
        self.assertEqual(completed_reindex.status_code, 200)
        self.assertEqual(completed_reindex.json()["status"], "completed")
        self.assertEqual(len(self.orchestrator.calls_for("rebuild_qdrant")), 2)


if __name__ == "__main__":
    unittest.main()
