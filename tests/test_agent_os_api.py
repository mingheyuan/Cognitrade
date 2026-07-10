from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from research_system.api.app import create_app
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


if __name__ == "__main__":
    unittest.main()
