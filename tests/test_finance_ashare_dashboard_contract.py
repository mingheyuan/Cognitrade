from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from research_system.runtime.agent_os import AgentOS
from research_system.runtime.core.artifact_board import ArtifactBoard
from research_system.runtime.platform.settings import Settings
from research_system.schemas.protocol import RequestEnvelope


REPO_ROOT = Path(__file__).resolve().parents[1]


class FinanceAshareDashboardContractTests(unittest.TestCase):
    def _settings(self, root: Path) -> Settings:
        return Settings.from_env(REPO_ROOT).model_copy(
            update={
                "runs_root": root / "runs",
                "memory_root": root / "memory",
                "domain_packs_root": REPO_ROOT / "domain_packs",
            }
        )

    @staticmethod
    def _screen_snapshots() -> list[dict]:
        return [
            {
                "symbol": "600000",
                "name": "Contract Bank",
                "snapshot_type": "market",
                "facts": {
                    "market_overview": {
                        "sector": "Bank",
                        "pe": 8.4,
                        "spot": {"latest_price": 10.2, "pct_change": 1.5, "amount": 1_200_000_000},
                    }
                },
            },
            {
                "symbol": "600000",
                "snapshot_type": "fundamental",
                "facts": {"financial_abstract": {"roe": 0.12, "operating_cash_flow": 123, "revenue_growth": 0.08}},
            },
            {
                "symbol": "600000",
                "snapshot_type": "notice",
                "facts": {"notice_count": 2},
            },
        ]

    def test_screening_style_and_sector_heatmap_are_written_to_canonical_artifacts(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            agent_os = AgentOS(self._settings(root))
            screening = agent_os.handle_request(
                RequestEnvelope(
                    intent="stock_screen",
                    user_query="Screen the supplied candidate.",
                    slots={"symbols": ["600000"], "snapshots": self._screen_snapshots()},
                    constraints={
                        "screen_style": "value",
                        "research_profile": "lite",
                        "investment_cycle": "long_term",
                    },
                )
            )
            screening_board = ArtifactBoard(root / "runs" / screening.run_id / "artifact_board.jsonl")
            result = screening_board.list_artifacts(artifact_type="screening_result", status="active")[0]
            candidate = result.content["screened_candidates"][0]

            self.assertEqual(result.content["methodology"]["screen_style"], "value")
            self.assertEqual(candidate["methodology"]["screen_style"], "value")
            valuation = next(
                item for item in candidate["methodology"]["criteria"] if item["id"] == "valuation_evidence"
            )
            self.assertEqual(valuation["status"], "pass")
            self.assertEqual(valuation["source_field"], "pe")

            sector = agent_os.handle_request(
                RequestEnvelope(
                    intent="sector_research",
                    user_query="Review the semiconductor sector.",
                    slots={
                        "sector": "Semiconductor",
                        "snapshots": [
                            {
                                "snapshot_type": "sector",
                                "sector": "Semiconductor",
                                "facts": {"research_heat": 73, "theme": "AI hardware"},
                            },
                            {"snapshot_type": "market", "symbol": "688001", "facts": {}},
                        ],
                    },
                    constraints={"research_profile": "medium"},
                )
            )
            sector_board = ArtifactBoard(root / "runs" / sector.run_id / "artifact_board.jsonl")
            analysis = sector_board.list_artifacts(artifact_type="sector_analysis", status="active")[0]
            heatmap = analysis.content["sector_heatmap"]

            self.assertEqual(heatmap, [{
                "name": "Semiconductor",
                "score": 73.0,
                "score_basis": "source_fact",
                "source_count": 2,
                "related_snapshot_count": 1,
                "theme": "AI hardware",
            }])


if __name__ == "__main__":
    unittest.main()
