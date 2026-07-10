from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient

from api_test_support import API_TOKEN, auth_headers
from research_system.api.app import create_app
from research_system.api.config import ApiConfig
from research_system.runtime.core.artifact_board import ArtifactBoard
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout
from research_system.schemas.artifacts import ArtifactProducer, ArtifactRecord
from research_system.schemas.memory import MemoryEvaluation
from research_system.services.memory.layers import LayeredMemoryStore


REPO_ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "01J00000000000000000000090"


class ApiV1DashboardTests(unittest.TestCase):
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

    def _add_artifact(self, artifact_type: str, content: dict, *, created_at: datetime) -> ArtifactRecord:
        board = ArtifactBoard(self.settings.runs_root / RUN_ID / "artifact_board.jsonl")
        return board.add(
            ArtifactRecord(
                artifact_type=artifact_type,
                run_id=RUN_ID,
                thread_id="thread-dashboard",
                request_id="request-dashboard",
                producer=ArtifactProducer(
                    executor="finance_dashboard_test",
                    capability="finance.dashboard.test",
                    domain_id="finance_ashare",
                ),
                content=content,
                source_refs=[{"type": "test_source", "id": "source-dashboard"}],
                confidence=0.9,
                created_at=created_at,
            )
        )

    def test_overview_and_methodology_are_pack_configured_agent_os_views(self) -> None:
        now = datetime.now(timezone.utc)
        screening = self._add_artifact(
            "screening_result",
            {
                "screened_candidates": [
                    {
                        "symbol": "600000",
                        "name": "Dashboard Bank",
                        "score": 1.2,
                        "candidate_tier": "core",
                        "latest_pct_change": 1.5,
                        "why_it_made_the_list": "Complete source-backed test candidate.",
                    }
                ],
                "watchlist_candidates": [],
                "enrichment_targets": [],
                "scorecard": {"candidate_count": 1},
                "screening_notes": [],
            },
            created_at=now,
        )
        self._add_artifact(
            "sector_analysis",
            {
                "sector": "Semiconductor",
                "theme": "test",
                "findings": [],
                "related_snapshots": [],
                "risks": [],
                "source_count": 2,
                "analysis_notes": [],
                "sector_heatmap": [
                    {"name": "Semiconductor", "score": 73, "score_basis": "source_fact"}
                ],
            },
            created_at=now - timedelta(seconds=1),
        )
        memory = LayeredMemoryStore(self.settings.memory_root / "agent_os")
        episode = memory.add_episode(
            domain_id="finance_ashare",
            memory_type="methodology_review",
            content={"title": "Verified methodology test", "summary": "Evidence-backed."},
            source_run_id=RUN_ID,
            source_artifact_ids=[screening.artifact_id],
            gated=True,
            gate_evaluations=[{"gate_id": "finance.test", "verdict": "pass"}],
        )
        domain_memory = memory.promote(
            memory_id=episode.memory_id,
            target_layer="domain",
            evaluation=MemoryEvaluation(
                evaluation_id="domain-test",
                evaluator="test",
                verdict="pass",
                score=0.8,
                evidence_refs=[screening.artifact_id],
            ),
        )
        memory.promote(
            memory_id=domain_memory.memory_id,
            target_layer="policy",
            evaluation=MemoryEvaluation(
                evaluation_id="policy-test",
                evaluator="test",
                verdict="pass",
                score=0.9,
                evidence_refs=[screening.artifact_id],
            ),
        )

        overview = self.client.get(
            "/api/v1/dashboard/finance_ashare/overview",
            headers=auth_headers(),
        )
        methodology = self.client.get(
            "/api/v1/dashboard/finance_ashare/methodology",
            headers=auth_headers(),
        )

        self.assertEqual(overview.status_code, 200, overview.text)
        candidates = overview.json()["resources"]["candidates"]["items"]
        self.assertEqual(candidates[0]["data"]["symbol"], "600000")
        self.assertEqual(candidates[0]["provenance"]["artifact_id"], screening.artifact_id)
        heatmap = overview.json()["resources"]["sector_heatmap"]["items"]
        self.assertEqual(heatmap[0]["data"]["name"], "Semiconductor")
        self.assertEqual(heatmap[0]["data"]["score"], 73)

        self.assertEqual(methodology.status_code, 200, methodology.text)
        profile = methodology.json()["resources"]["profile"]["value"]
        self.assertEqual(profile["name"], "A-share evidence-backed research")
        self.assertIn("quality", [style["id"] for style in profile["screen_styles"]])
        policy_memory = methodology.json()["resources"]["verified_policy_memory"]["items"]
        self.assertEqual(policy_memory[0]["data"]["title"], "Verified methodology test")
        self.assertEqual(policy_memory[0]["provenance"]["status"], "verified")

    def test_unknown_pack_or_view_returns_not_found(self) -> None:
        missing_view = self.client.get(
            "/api/v1/dashboard/finance_ashare/missing",
            headers=auth_headers(),
        )
        missing_pack = self.client.get(
            "/api/v1/dashboard/missing_pack/overview",
            headers=auth_headers(),
        )

        for response in (missing_view, missing_pack):
            self.assertEqual(response.status_code, 404, response.text)
            self.assertEqual(response.json()["error"]["code"], "resource_not_found")


if __name__ == "__main__":
    unittest.main()
