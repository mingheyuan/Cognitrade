from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from research_system.api.app import create_app
from research_system.api.config import ApiConfig
from research_system.runtime.intake.task_intake import TaskIntakeService
from research_system.runtime.domain_packs.registry import DomainPackRegistry
from research_system.runtime.platform.settings import Settings
from research_system.schemas.protocol import RequestEnvelope


class AgentOSTaskIntakeTests(unittest.TestCase):
    def test_finance_pack_resolves_company_aliases_and_comparison_intent(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        service = TaskIntakeService(DomainPackRegistry.load(repository_root / "domain_packs"))

        decision = service.resolve(
            RequestEnvelope(user_query="\u5bf9\u6bd4\u5e73\u5b89\u94f6\u884c\u548c\u62db\u5546\u94f6\u884c")
        )

        self.assertEqual(decision.status, "accepted")
        self.assertEqual(decision.intent, "stock_compare")
        self.assertEqual(decision.slots["symbols"], ["000001", "600036"])

    def test_finance_pack_returns_clarification_for_incomplete_target(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        service = TaskIntakeService(DomainPackRegistry.load(repository_root / "domain_packs"))

        decision = service.resolve(RequestEnvelope(user_query="compare one bank"))

        self.assertEqual(decision.status, "needs_clarification")
        self.assertEqual(decision.intent, "stock_compare")
        self.assertEqual(decision.clarification.missing_fields, ["symbols"])

    def test_versioned_api_uses_pack_intake_when_intent_is_omitted(self) -> None:
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
                create_app(settings=settings, api_config=ApiConfig(api_token="task-intake-test"))
            ) as client:
                response = client.post(
                    "/api/v1/runs",
                    headers={"Authorization": "Bearer task-intake-test"},
                    json={
                        "user_query": "\u5bf9\u6bd4\u5e73\u5b89\u94f6\u884c\u548c\u62db\u5546\u94f6\u884c",
                        "slots": {
                            "snapshots": [
                                {"snapshot_type": "market", "symbol": "000001", "facts": {"market_overview": {"latest_price": 10}}},
                                {"snapshot_type": "fundamental", "symbol": "000001", "facts": {"financial_abstract": {"net_profit": 1}}},
                                {"snapshot_type": "market", "symbol": "600036", "facts": {"market_overview": {"latest_price": 20}}},
                                {"snapshot_type": "fundamental", "symbol": "600036", "facts": {"financial_abstract": {"net_profit": 2}}},
                            ]
                        },
                    },
                )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["intent"], "stock_compare")
        self.assertEqual(response.json()["status"], "completed")

    def test_versioned_api_returns_clarification_without_creating_a_run(self) -> None:
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
                create_app(settings=settings, api_config=ApiConfig(api_token="task-intake-test"))
            ) as client:
                response = client.post(
                    "/api/v1/runs",
                    headers={"Authorization": "Bearer task-intake-test"},
                    json={"user_query": "analyze a company"},
                )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "needs_clarification")
        self.assertIsNone(response.json()["run_id"])

    def test_explicit_thesis_review_without_target_is_a_pack_clarification(self) -> None:
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
                create_app(settings=settings, api_config=ApiConfig(api_token="task-intake-test"))
            ) as client:
                response = client.post(
                    "/api/v1/runs",
                    headers={"Authorization": "Bearer task-intake-test"},
                    json={"intent": "review_thesis", "user_query": "review the previous thesis"},
                )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "needs_clarification")
        self.assertEqual(response.json()["clarification"]["missing_fields"], ["review_target_run_id"])


if __name__ == "__main__":
    unittest.main()
