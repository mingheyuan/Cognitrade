from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

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
    def test_collector_fetches_provider_snapshots_for_a_stock_code_in_the_query(self) -> None:
        registry = DomainPackRegistry.load(Path(__file__).resolve().parents[1] / "domain_packs")
        normalizer = registry.resolve_executor(domain_id="finance_ashare", capability="finance.query.normalize")
        collector = registry.resolve_executor(domain_id="finance_ashare", capability="finance.ashare.snapshot.collect")
        evidence_selector = registry.resolve_executor(domain_id="finance_ashare", capability="evidence.select")
        analyst = registry.resolve_executor(domain_id="finance_ashare", capability="finance.analysis.evaluate")
        report_composer = registry.resolve_executor(domain_id="finance_ashare", capability="finance.report.compose")
        normalized = normalizer(
            _PackContext({"user_request": _artifact("user_request", {"query": "Analyze 600000."})})
        )

        collected_types: list[str] = []

        def _collect_snapshots(*, query, symbols, snapshot_types, sector, cancellation):
            collected_types.extend(snapshot_types)
            return [
                {
                    "snapshot_type": "market",
                    "symbol": "600000",
                    "snapshot_date": "2026-07-10",
                    "facts": {"market_overview": {"latest_price": 10.5, "pe_ttm": 6.2}},
                    "source": {"vendor": "akshare", "provider_id": "akshare_market"},
                },
                {
                    "snapshot_type": "fundamental",
                    "symbol": "600000",
                    "snapshot_date": "2026-07-10",
                    "facts": {"financial_abstract": {"net_profit": 100}},
                    "source": {"vendor": "akshare", "provider_id": "akshare_fundamental"},
                },
            ]

        with patch.dict(collector.__globals__, {"collect_snapshots": _collect_snapshots}):
            snapshots = collector(_PackContext({"normalized_query": _artifact("normalized_query", normalized.content)}))
        evidence = evidence_selector(_PackContext({"stock_snapshot_bundle": _artifact("stock_snapshot_bundle", snapshots.content)}))
        analysis = analyst(_PackContext({"stock_snapshot_bundle": _artifact("stock_snapshot_bundle", snapshots.content)}))
        report = report_composer(
            _PackContext(
                {
                    "evidence_set": _artifact("evidence_set", evidence.content),
                    "financial_analysis": _artifact("financial_analysis", analysis.content),
                }
            )
        )

        self.assertEqual(normalized.content["symbols"], ["600000"])
        self.assertEqual(
            collected_types,
            ["market", "fundamental", "notice", "capital_flow", "sentiment", "events", "lhb"],
        )
        self.assertEqual([item["snapshot_type"] for item in snapshots.content["snapshots"]], ["market", "fundamental"])
        self.assertEqual(snapshots.source_refs[0]["type"], "financial_data_provider")
        self.assertEqual(len(report.content["evidence"]), 2)
        self.assertIn("600000 market snapshot", report.content["conclusion"])
        self.assertTrue(report.content["assessment"]["summary"])
        self.assertTrue(report.content["assessment"]["factors"])

    def test_analysis_pack_recreates_valuation_and_bank_compare_contracts(self) -> None:
        registry = DomainPackRegistry.load(Path(__file__).resolve().parents[1] / "domain_packs")
        analyst = registry.resolve_executor(domain_id="finance_ashare", capability="finance.analysis.evaluate")
        reporter = registry.resolve_executor(domain_id="finance_ashare", capability="finance.report.compose")
        snapshots = [
            {
                "snapshot_type": "market",
                "symbol": "600000",
                "facts": {"market_overview": {"latest_price": 10.0, "amount": 2_000_000, "pb": 0.5, "pe_ttm": 6.0}},
            },
            {
                "snapshot_type": "fundamental",
                "symbol": "600000",
                "facts": {"financial_abstract": {"revenue": 100, "net_profit": 20}, "banking_metrics": {"is_bank_like": True, "roe": 0.12, "nonperforming_loan_ratio": 0.01, "net_interest_spread": 0.02}},
            },
            {
                "snapshot_type": "market",
                "symbol": "000001",
                "facts": {"market_overview": {"latest_price": 11.0, "amount": 1_500_000, "pb": 0.7, "pe_ttm": 7.0}},
            },
            {
                "snapshot_type": "fundamental",
                "symbol": "000001",
                "facts": {"financial_abstract": {"revenue": 120, "net_profit": 18}, "banking_metrics": {"is_bank_like": True, "roe": 0.1, "nonperforming_loan_ratio": 0.012, "net_interest_spread": 0.018}},
            },
        ]
        bundle = _artifact("stock_snapshot_bundle", {"symbols": ["600000", "000001"], "snapshots": snapshots})
        analysis = analyst(_PackContext({"stock_snapshot_bundle": bundle}, {"investment_cycle": "long_term"}))
        report = reporter(
            _PackContext(
                {
                    "evidence_set": _artifact("evidence_set", {"items": snapshots}),
                    "financial_analysis": _artifact("financial_analysis", analysis.content),
                }
            )
        )

        self.assertEqual(len(analysis.content["methodologies"]), 6)
        self.assertEqual(
            {item["methodology_type"] for item in analysis.content["methodologies"]},
            {"comps", "dcf", "cashflow_resilience"},
        )
        self.assertEqual(analysis.content["scorecards"][0]["candidate_count"], 2)
        self.assertEqual(len(report.content["methodologies"]), 6)
        self.assertEqual(report.content["scorecard"]["scorecard_type"], "bank_compare_scorecard")

    def test_screening_pack_preserves_migrated_candidate_order_and_scorecard_contract(self) -> None:
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
        registry = DomainPackRegistry.load(Path(__file__).resolve().parents[1] / "domain_packs")
        pack_executor = registry.resolve_executor(domain_id="finance_ashare", capability="finance.stock.screen")
        packed = pack_executor(
            _PackContext(
                {"stock_snapshot_bundle": _artifact("stock_snapshot_bundle", {"snapshots": snapshots})},
                {"top_k": 2, "investment_cycle": "mid_term"},
            )
        )

        self.assertEqual(
            [item["symbol"] for item in packed.content["screened_candidates"]],
            ["300363", "300347"],
        )
        self.assertEqual(
            packed.content["scorecard"]["top_candidate_count"],
            2,
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

    def test_intent_specific_report_composers_preserve_specialist_artifacts(self) -> None:
        registry = DomainPackRegistry.load(Path(__file__).resolve().parents[1] / "domain_packs")
        screen_reporter = registry.resolve_executor(domain_id="finance_ashare", capability="finance.screen.report.compose")
        sector_reporter = registry.resolve_executor(domain_id="finance_ashare", capability="finance.sector.report.compose")
        thesis_reporter = registry.resolve_executor(domain_id="finance_ashare", capability="finance.thesis.report.compose")

        screen_report = screen_reporter(
            _PackContext(
                {"screening_result": _artifact("screening_result", {"screened_candidates": [{"symbol": "600000", "score": 1.2, "why_it_made_the_list": "reported evidence", "main_risk": "refresh data"}], "watchlist_candidates": [], "scorecard": {"candidate_count": 1}, "methodology": {"screen_style": "quality"}})}
            )
        )
        sector_report = sector_reporter(
            _PackContext(
                {"sector_analysis": _artifact("sector_analysis", {"sector": "AI", "findings": [{"snapshot_type": "sector", "facts": {"heat_score": 72}}], "sector_heatmap": [{"name": "AI", "score": 72}], "risks": ["refresh"]})}
            )
        )
        thesis_report = thesis_reporter(
            _PackContext(
                {"thesis_review": _artifact("thesis_review", {"overall_verdict": "inconclusive", "findings": [{"claim": "margin", "verdict": "unverified"}], "weaknesses": ["missing filing"], "follow_up": ["refresh filing"]})}
            )
        )

        self.assertIn("screening", screen_report.content)
        self.assertIn("sector_analysis", sector_report.content)
        self.assertIn("thesis_review", thesis_report.content)


if __name__ == "__main__":
    unittest.main()
