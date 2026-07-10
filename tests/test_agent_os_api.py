from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from research_system.api.app import create_app
from research_system.api.config import ApiConfig
from research_system.runtime.platform.settings import Settings


class AgentOSApiTests(unittest.TestCase):
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
                        "intent": "stock_check",
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
                run_id = payload["run_id"]

                steps = client.get(f"/api/v1/runs/{run_id}/steps", headers=headers)
                self.assertEqual(steps.status_code, 200, steps.text)
                self.assertEqual(steps.json()["domain_id"], "finance_ashare")
                self.assertTrue(steps.json()["steps"])

                events = client.get(f"/api/v1/runs/{run_id}/events", headers=headers)
                self.assertEqual(events.status_code, 200, events.text)
                self.assertTrue(events.json()["events"])
                self.assertTrue(
                    all(
                        str(event["event_type"]).startswith("graph_")
                        for event in events.json()["events"]
                    )
                )


if __name__ == "__main__":
    unittest.main()
