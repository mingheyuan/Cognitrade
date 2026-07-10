from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest

from research_system.agents.screener.service import ScreenerService
from research_system.schemas.artifacts import ArtifactProducer, ArtifactRecord
from research_system.runtime.domain_packs.registry import DomainPackRegistry


def _artifact(artifact_type: str, content: dict) -> ArtifactRecord:
    return ArtifactRecord(
        artifact_type=artifact_type,
        run_id="run-equivalence",
        thread_id="thread-equivalence",
        request_id="request-equivalence",
        producer=ArtifactProducer(
            executor="test",
            capability="test",
            domain_id="finance_ashare",
        ),
        content=content,
        source_refs=[{"type": "test", "id": "equivalence"}],
    )


class _PackContext:
    def __init__(self, artifacts: dict[str, ArtifactRecord], inputs: dict | None = None) -> None:
        self.node = SimpleNamespace(inputs=dict(inputs or {}))
        self._artifacts = artifacts

    def require_artifact(self, artifact_type: str) -> ArtifactRecord:
        return self._artifacts[artifact_type]


class FinancePackEquivalenceTests(unittest.TestCase):
    def test_screening_pack_preserves_legacy_candidate_order_and_scorecard_contract(self) -> None:
        snapshots = [
            {
                "snapshot_type": "market",
                "symbol": "300363",
                "name": "Alpha",
                "facts": {"market_overview": {"spot": {"latest_price": 12.3, "pct_change": 2.1, "amount": 1_800_000_000}}},
            },
            {"snapshot_type": "fundamental", "symbol": "300363", "facts": {"financial_abstract": {"revenue": 100}}},
            {"snapshot_type": "notice", "symbol": "300363", "facts": {"notice_count": 3}},
            {
                "snapshot_type": "market",
                "symbol": "300347",
                "name": "Beta",
                "facts": {"market_overview": {"spot": {"latest_price": 45.6, "pct_change": 0.8, "amount": 1_100_000_000}}},
            },
            {"snapshot_type": "fundamental", "symbol": "300347", "facts": {"financial_abstract": {"revenue": 80}}},
            {"snapshot_type": "notice", "symbol": "300347", "facts": {"notice_count": 1}},
        ]
        legacy = ScreenerService().run(
            query="screen growth stocks",
            source_payloads=[{"candidate_universe": [{"symbol": "300363", "name": "Alpha"}, {"symbol": "300347", "name": "Beta"}], "investment_cycle": "mid_term"}],
            snapshot_payloads=snapshots,
            constraints={"top_k": 2},
        )
        registry = DomainPackRegistry.load(Path(__file__).resolve().parents[1] / "domain_packs")
        pack_executor = registry.resolve_executor(domain_id="finance_ashare", capability="finance.stock.screen")
        packed = pack_executor(
            _PackContext(
                {"stock_snapshot_bundle": _artifact("stock_snapshot_bundle", {"snapshots": snapshots})},
                {"top_k": 2, "investment_cycle": "mid_term"},
            )
        )

        self.assertEqual(
            [item["symbol"] for item in legacy["screened_candidates"]],
            [item["symbol"] for item in packed.content["screened_candidates"]],
        )
        self.assertEqual(
            legacy["scorecard"]["top_candidate_count"],
            packed.content["scorecard"]["top_candidate_count"],
        )
        self.assertIn("score_breakdown", packed.content["screened_candidates"][0])

    def test_sector_and_review_pack_outputs_preserve_key_legacy_contract_fields(self) -> None:
        registry = DomainPackRegistry.load(Path(__file__).resolve().parents[1] / "domain_packs")
        sector = registry.resolve_executor(domain_id="finance_ashare", capability="finance.sector.research")(
            _PackContext(
                {"stock_snapshot_bundle": _artifact("stock_snapshot_bundle", {"sector": "AI", "theme": "compute", "snapshots": [{"snapshot_type": "sector", "sector": "AI"}, {"snapshot_type": "constituent", "symbol": "300363"}]})},
                {"include_policy_snapshot": True},
            )
        )
        review = registry.resolve_executor(domain_id="finance_ashare", capability="finance.thesis.review")(
            _PackContext(
                {"evidence_set": _artifact("evidence_set", {"items": [{"claim": "direction", "verdict": "confirmed"}, {"claim": "catalyst", "verdict": "confirmed"}]})},
                {"review_focus": "cashflow_resilience"},
            )
        )

        self.assertEqual(sector.content["sector"], "AI")
        self.assertEqual(len(sector.content["related_snapshots"]), 1)
        self.assertIn("policy_snapshot_requested", sector.content["analysis_notes"])
        self.assertEqual(review.content["overall_verdict"], "confirmed")
        self.assertEqual(len(review.content["strengths"]), 2)
        self.assertIn("cashflow_resilience", review.content["follow_up"][1])


if __name__ == "__main__":
    unittest.main()
