from __future__ import annotations

import unittest

from research_system.agents.reporter.service import ReporterService
from research_system.runtime.provider_registry import LLMProviderError


class ReporterProviderStub:
    available = False


class ReporterLLMSuccessStub:
    available = True

    def parse(self, **kwargs):
        class Parsed:
            conclusion = "posterior review completed"
            evidence_ids = ["review-direction", "review-catalyst", "review-window"]
            risk = "use review findings as primary source"
            follow_up = "inspect rejected claims if any"
            degraded_note = None

        return Parsed()


class ReporterLLMErrorStub:
    available = True

    def parse(self, **kwargs):
        raise LLMProviderError("simulated llm error")


class ReporterServiceTests(unittest.TestCase):
    def test_stock_screen_fallback_report_returns_candidate_digest(self) -> None:
        service = ReporterService(ReporterProviderStub())

        payload = service.run(
            intent="stock_screen",
            query="最近有什么股票值得投入",
            retrieval_summary="Stock screening: candidates=5",
            evidences=[
                {
                    "evidence_id": "screening-scorecard",
                    "title": "screening_scorecard",
                    "summary": "screening_scorecard\n{'investment_cycle': 'mid_term', 'candidate_count': 8, 'top_candidate_count': 5, 'deep_checked_count': 1, 'average_score': 0.8, 'sector_context_present': False, 'summary': 'mid_term 候选已生成，优先关注: 京东方Ａ, 兆易创新, 长电科技'}",
                    "metadata": {"source_type": "screening_scorecard"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "screen-candidate-1",
                    "title": "screen_candidate:000725",
                    "summary": "screen_candidate symbol=000725 name=京东方Ａ\nscore=1.947 latest_pct_change=3.59\nlatest_amount=35676682214.0 notice_count=0\ndeep_checked=True\nwhy=京东方Ａ: 最新涨跌幅为 3.59%，成交额约 356.77 亿元\nrisk=关键证据缺失: market_snapshot, fundamental_snapshot, notice_snapshot\nmissing_evidence=['market_snapshot', 'fundamental_snapshot', 'notice_snapshot']",
                    "metadata": {"source_type": "screen_candidate"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "screen-candidate-2",
                    "title": "screen_candidate:603986",
                    "summary": "screen_candidate symbol=603986 name=兆易创新\nscore=0.526 latest_pct_change=-0.672\nlatest_amount=37418302893.0 notice_count=0\nwhy=兆易创新: 最新涨跌幅为 -0.672%，但仍保留在观察池中，成交额约 374.18 亿元\nrisk=关键证据缺失: market_snapshot, fundamental_snapshot, notice_snapshot\nmissing_evidence=['market_snapshot', 'fundamental_snapshot', 'notice_snapshot']",
                    "metadata": {"source_type": "screen_candidate"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "watch-candidate-1",
                    "title": "watchlist_candidate:300308",
                    "summary": "watchlist_candidate symbol=300308 name=中际旭创\nscore=0.31 latest_pct_change=-5.252\nlatest_amount=44480805076.0 notice_count=0\ndeep_checked=False\nwhy=中际旭创: 最新涨跌幅为 -5.252%，但仍保留在观察池中，成交额约 444.81 亿元\nrisk=关键证据缺失: market_snapshot, fundamental_snapshot, notice_snapshot\nmissing_evidence=['market_snapshot', 'fundamental_snapshot', 'notice_snapshot']",
                    "metadata": {"source_type": "watchlist_candidate"},
                    "source_refs": [],
                },
            ],
        )

        self.assertIn("当前更适合作为", payload["conclusion"])
        self.assertIn("优先关注：京东方Ａ, 兆易创新", payload["conclusion"])
        self.assertIn("次级观察：中际旭创", payload["conclusion"])
        self.assertIn("1 只候选已经完成市场、财务和公告的深度核查", payload["conclusion"])
        self.assertEqual(payload["screening_scorecard"]["investment_cycle"], "mid_term")
        self.assertEqual(payload["candidates"][0]["name"], "京东方Ａ")
        self.assertTrue(payload["candidates"][0]["deep_checked"])
        self.assertEqual(payload["candidates"][-1]["candidate_tier"], "watchlist")

    def test_stock_screen_llm_error_report_still_returns_candidate_digest(self) -> None:
        service = ReporterService(ReporterLLMErrorStub())

        payload = service.run(
            intent="stock_screen",
            query="最近有什么股票值得投入",
            retrieval_summary="Stock screening: candidates=5",
            evidences=[
                {
                    "evidence_id": "screening-scorecard",
                    "title": "screening_scorecard",
                    "summary": "screening_scorecard\n{'investment_cycle': 'mid_term', 'candidate_count': 8, 'top_candidate_count': 5, 'average_score': 0.8, 'sector_context_present': False, 'summary': 'mid_term 候选已生成，优先关注: 京东方Ａ, 兆易创新, 长电科技'}",
                    "metadata": {"source_type": "screening_scorecard"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "screen-candidate-1",
                    "title": "screen_candidate:000725",
                    "summary": "screen_candidate symbol=000725 name=京东方Ａ\nscore=1.947 latest_pct_change=3.59\nlatest_amount=35676682214.0 notice_count=0\nwhy=京东方Ａ: 最新涨跌幅为 3.59%，成交额约 356.77 亿元\nrisk=关键证据缺失: market_snapshot, fundamental_snapshot, notice_snapshot\nmissing_evidence=['market_snapshot', 'fundamental_snapshot', 'notice_snapshot']",
                    "metadata": {"source_type": "screen_candidate"},
                    "source_refs": [],
                },
            ],
        )

        self.assertIn("优先关注：京东方Ａ", payload["conclusion"])
        self.assertTrue(str(payload["degraded_note"]).startswith("llm_error:"))
        self.assertEqual(payload["candidates"][0]["symbol"], "000725")

    def test_stock_screen_candidate_payload_parses_real_dict_summary(self) -> None:
        service = ReporterService(ReporterProviderStub())

        candidates = service._screening_candidates_payload(
            [
                {
                    "evidence_id": "screen-candidate-1",
                    "title": "screen_candidate:000725",
                    "summary": "screen_candidate:000725\n{'symbol': '000725', 'name': '京东方Ａ', 'score': 1.947, 'deep_checked': True, 'why_it_made_the_list': '京东方Ａ: 最新涨跌幅为 3.59%，成交额约 356.77 亿元', 'main_risk': '关键证据缺失: market_snapshot, fundamental_snapshot, notice_snapshot', 'missing_evidence': ['market_snapshot', 'fundamental_snapshot', 'notice_snapshot']}",
                    "metadata": {"source_type": "screen_candidate"},
                    "source_refs": [],
                }
            ]
        )

        self.assertEqual(candidates[0]["name"], "京东方Ａ")
        self.assertEqual(candidates[0]["score"], 1.947)
        self.assertTrue(candidates[0]["deep_checked"])
        self.assertIn("356.77", candidates[0]["why_it_made_the_list"])
        self.assertIn("market_snapshot", str(candidates[0]["missing_evidence"]))

    def test_stock_screen_fallback_report_includes_review_summary_when_present(self) -> None:
        service = ReporterService(ReporterProviderStub())

        payload = service.run(
            intent="stock_screen",
            query="最近有什么股票值得投入 中线",
            retrieval_summary="Stock screening: candidates=1; overall_verdict=confirmed_with_caveats",
            evidences=[
                {
                    "evidence_id": "screening-scorecard",
                    "title": "screening_scorecard",
                    "summary": "screening_scorecard\n{'investment_cycle': 'mid_term', 'candidate_count': 1, 'top_candidate_count': 1, 'watchlist_candidate_count': 0, 'deep_checked_count': 1, 'summary': 'mid_term 候选已生成，优先关注: 京东方Ａ'}",
                    "metadata": {"source_type": "screening_scorecard"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "screen-candidate-1",
                    "title": "screen_candidate:000725",
                    "summary": "screen_candidate symbol=000725 name=京东方Ａ\nscore=1.947 latest_pct_change=3.59\nlatest_amount=35676682214.0 notice_count=0\ndeep_checked=True\nwhy=京东方Ａ: 最新涨跌幅为 3.59%，成交额约 356.77 亿元\nrisk=关键证据缺失: notice_snapshot\nmissing_evidence=['notice_snapshot']",
                    "metadata": {"source_type": "screen_candidate"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "screen-review-summary",
                    "title": "review_summary",
                    "summary": "review_summary\n{'review_required': true, 'finding_count': 1, 'reviewable_claim_count': 1, 'claim_type_counts': {'catalyst': 1}, 'benchmark_scope_counts': {'screen_candidate': 1}, 'benchmark_scope_explanations': {'screen_candidate': '按筛选候选的证据完整度、降级状态和主风险做轻量复核。'}, 'verdict_by_claim_type': {'catalyst': {'confirmed': 0, 'rejected': 0, 'inconclusive': 1}}, 'overall_verdict': 'confirmed_with_caveats', 'summary_text': 'overall_verdict=confirmed_with_caveats; findings=1; confirmed=0; rejected=0; inconclusive=1; benchmark_scopes=screen_candidate', 'confirmed': 0, 'rejected': 0, 'inconclusive': 1, 'notes': ['core_candidates=1; watchlist_candidates=0']}",
                    "metadata": {"source_type": "review_summary"},
                    "source_refs": [],
                },
            ],
        )

        self.assertIsInstance(payload["review_summary"], dict)
        self.assertEqual(payload["review_summary"]["overall_verdict"], "confirmed_with_caveats")
        self.assertTrue(payload["review_summary"]["review_required"])

    def test_distillation_fallback_report_includes_attribution_digest(self) -> None:
        service = ReporterService(ReporterProviderStub())

        payload = service.run(
            intent="distill_kol_logic",
            query="distill attribution report",
            retrieval_summary="Distillation scorecard",
            evidences=[
                {
                    "evidence_id": "scorecard-1",
                    "title": "distillation_scorecard",
                    "summary": "distillation_scorecard\n{'setup_label': 'mixed_attribution', 'author_ratio': 0.33, 'review_strength': 'high'}",
                    "metadata": {"source_type": "distillation_scorecard"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "logic-author",
                    "title": "logic_block:claim",
                    "summary": "logic_block type=claim source_record_id=src-1 speaker_type=author\ntext: author claim",
                    "metadata": {"source_type": "logic_block"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "logic-reply",
                    "title": "logic_block:claim",
                    "summary": "logic_block type=claim source_record_id=src-2 speaker_type=reply\ntext: reply claim",
                    "metadata": {"source_type": "logic_block"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "logic-repost",
                    "title": "logic_block:claim",
                    "summary": "logic_block type=claim source_record_id=src-3 speaker_type=repost\ntext: repost claim",
                    "metadata": {"source_type": "logic_block"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "review-1",
                    "title": "review_finding:quoted_attribution",
                    "summary": "review finding",
                    "metadata": {"source_type": "review_finding"},
                    "source_refs": [],
                },
            ],
        )

        self.assertIn("Scorecard:", payload["conclusion"])
        self.assertIn("mixed_attribution", payload["conclusion"])
        self.assertIn("author_ratio", payload["conclusion"])
        self.assertIsInstance(payload["scorecard"], dict)
        self.assertEqual(payload["scorecard"]["setup_label"], "mixed_attribution")
        self.assertIn("author-original versus reply/repost/reported content", payload["follow_up"])

    def test_review_thesis_fallback_report_counts_confirmed_findings(self) -> None:
        service = ReporterService(ReporterProviderStub())

        payload = service.run(
            intent="review_thesis",
            query="review thesis",
            retrieval_summary="Posterior review: review_findings=3; claim_type_counts={'direction': 1, 'catalyst': 1, 'time_window': 1}",
            evidences=[
                {
                    "evidence_id": "review-direction",
                    "title": "review_finding",
                    "summary": "review_finding claim_type=direction verdict=confirmed\nbenchmark=HS300 scope=sector\nexpected_direction=underperform\nexcess_metric=-0.82",
                    "metadata": {"source_type": "review_finding"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "review-catalyst",
                    "title": "review_finding",
                    "summary": "review_finding claim_type=catalyst verdict=confirmed\nbenchmark=HS300 scope=sector\nnotes: policy_signal_count=1",
                    "metadata": {"source_type": "review_finding"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "review-window",
                    "title": "review_finding",
                    "summary": "review_finding claim_type=time_window verdict=confirmed\nbenchmark=HS300 scope=sector\nnotes: theme_news_count=1",
                    "metadata": {"source_type": "review_finding"},
                    "source_refs": [],
                },
            ],
        )

        self.assertIn("confirmed=3", payload["conclusion"])
        self.assertIn("claim_type_distribution={'direction': {'confirmed': 1, 'rejected': 0, 'inconclusive': 0}", payload["conclusion"])
        self.assertEqual(
            payload["review_summary"]["verdict_by_claim_type"],
            {
                "direction": {"confirmed": 1, "rejected": 0, "inconclusive": 0},
                "catalyst": {"confirmed": 1, "rejected": 0, "inconclusive": 0},
                "time_window": {"confirmed": 1, "rejected": 0, "inconclusive": 0},
            },
        )
        self.assertFalse(payload["review_summary"]["review_required"])
        self.assertEqual(payload["review_summary"]["reviewable_claim_count"], 3)
        self.assertEqual(
            payload["review_summary"]["claim_type_counts"],
            {"direction": 1, "catalyst": 1, "time_window": 1},
        )
        self.assertEqual(
            payload["review_summary"]["benchmark_scope_counts"],
            {"sector": 3},
        )
        self.assertEqual(
            payload["review_summary"]["benchmark_scope_explanations"],
            {"sector": "按当前 review policy 的默认 benchmark 口径复核。"},
        )
        self.assertEqual(payload["review_summary"]["overall_verdict"], "confirmed")
        self.assertEqual(
            payload["review_summary"]["summary_text"],
            "overall_verdict=confirmed; findings=3; confirmed=3; rejected=0; inconclusive=0; benchmark_scopes=sector",
        )
        self.assertEqual(payload["degraded_note"], "llm_unavailable")

    def test_review_thesis_llm_success_still_returns_review_summary(self) -> None:
        service = ReporterService(ReporterLLMSuccessStub())

        payload = service.run(
            intent="review_thesis",
            query="review thesis",
            retrieval_summary="Posterior review summary",
            evidences=[
                {
                    "evidence_id": "review-direction",
                    "title": "review_finding",
                    "summary": "review_finding claim_type=direction verdict=confirmed\nbenchmark=HS300 scope=sector",
                    "metadata": {"source_type": "review_finding"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "review-catalyst",
                    "title": "review_finding",
                    "summary": "review_finding claim_type=catalyst verdict=rejected\nbenchmark=HS300 scope=sector",
                    "metadata": {"source_type": "review_finding"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "review-window",
                    "title": "review_finding",
                    "summary": "review_finding claim_type=time_window verdict=inconclusive\nbenchmark=HS300 scope=sector",
                    "metadata": {"source_type": "review_finding"},
                    "source_refs": [],
                },
            ],
        )

        self.assertEqual(payload["conclusion"], "overall_verdict=mixed. posterior review completed")
        self.assertEqual(
            payload["review_summary"],
            {
                "review_required": False,
                "finding_count": 3,
                "reviewable_claim_count": 3,
                "claim_type_counts": {
                    "direction": 1,
                    "catalyst": 1,
                    "time_window": 1,
                },
                "benchmark_scope_counts": {
                    "sector": 3,
                },
                "benchmark_scope_explanations": {
                    "sector": "按当前 review policy 的默认 benchmark 口径复核。",
                },
                "overall_verdict": "mixed",
                "summary_text": "overall_verdict=mixed; findings=3; confirmed=1; rejected=1; inconclusive=1; benchmark_scopes=sector",
                "confirmed": 1,
                "rejected": 1,
                "inconclusive": 1,
                "verdict_by_claim_type": {
                    "direction": {"confirmed": 1, "rejected": 0, "inconclusive": 0},
                    "catalyst": {"confirmed": 0, "rejected": 1, "inconclusive": 0},
                    "time_window": {"confirmed": 0, "rejected": 0, "inconclusive": 1},
                },
                "notes": [],
            },
        )
        self.assertIsNone(payload["degraded_note"])

    def test_review_thesis_llm_error_still_returns_review_summary(self) -> None:
        service = ReporterService(ReporterLLMErrorStub())

        payload = service.run(
            intent="review_thesis",
            query="review thesis",
            retrieval_summary="Posterior review summary",
            evidences=[
                {
                    "evidence_id": "review-direction",
                    "title": "review_finding",
                    "summary": "review_finding claim_type=direction verdict=confirmed\nbenchmark=HS300 scope=sector",
                    "metadata": {"source_type": "review_finding"},
                    "source_refs": [],
                }
            ],
        )

        self.assertEqual(payload["review_summary"]["finding_count"], 1)
        self.assertEqual(payload["review_summary"]["confirmed"], 1)
        self.assertEqual(payload["review_summary"]["overall_verdict"], "confirmed")
        self.assertTrue(str(payload["degraded_note"]).startswith("llm_error:"))


if __name__ == "__main__":
    unittest.main()
