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


class ReporterLLMUnexpectedCallStub:
    available = True

    def parse(self, **kwargs):
        raise AssertionError("LLM should not be called for deterministic bank compare reporting")


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

    def test_stock_screen_fallback_report_includes_methodology_payloads(self) -> None:
        service = ReporterService(ReporterProviderStub())

        payload = service.run(
            intent="stock_screen",
            query="screen stocks",
            retrieval_summary="Stock screening: candidates=1; methodology_count=1",
            evidences=[
                {
                    "evidence_id": "screening-scorecard",
                    "title": "screening_scorecard",
                    "summary": "screening_scorecard\n{'investment_cycle': 'mid_term', 'candidate_count': 1, 'top_candidate_count': 1, 'deep_checked_count': 1, 'summary': 'mid_term candidates ready'}",
                    "metadata": {"source_type": "screening_scorecard"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "screen-candidate-1",
                    "title": "screen_candidate:000001",
                    "summary": "screen_candidate symbol=000001 name=PingAn\nscore=2.0 latest_pct_change=1.2\nlatest_amount=1000000000 notice_count=1\ndeep_checked=True\nwhy=leader\nrisk=none",
                    "metadata": {"source_type": "screen_candidate"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "method-1",
                    "title": "comps:000001",
                    "summary": "methodology_result type=comps symbol=000001\nvaluation_band={'low': 10.0, 'base': 11.0, 'high': 12.0}\noutputs={'relative_view': 'relative_discount_like'}",
                    "metadata": {
                        "source_type": "methodology_comps",
                        "methodology_type": "comps",
                        "valuation_band_label": "10.0|11.0|12.0",
                    },
                    "source_refs": [],
                },
            ],
        )

        self.assertTrue(payload["methodologies"])
        self.assertIn("Relative valuation views:", payload["conclusion"])

    def test_stock_check_fallback_report_includes_methodology_payloads(self) -> None:
        service = ReporterService(ReporterProviderStub())

        payload = service.run(
            intent="stock_check",
            query="check 000001",
            retrieval_summary="Stock check: snapshots=3; methodology_types=['dcf']",
            evidences=[
                {
                    "evidence_id": "method-dcf",
                    "title": "dcf:000001",
                    "summary": "methodology_result type=dcf symbol=000001\nvaluation_band={'low': 8.0, 'base': 9.0, 'high': 10.0}",
                    "metadata": {
                        "source_type": "methodology_dcf",
                        "methodology_type": "dcf",
                        "valuation_band_label": "8.0|9.0|10.0",
                    },
                    "source_refs": [],
                }
            ],
        )

        self.assertIn("methodology_views=", payload["conclusion"])
        self.assertEqual(payload["methodologies"][0]["methodology_type"], "dcf")

    def test_stock_compare_fallback_report_returns_comparison_digest(self) -> None:
        service = ReporterService(ReporterProviderStub())

        payload = service.run(
            intent="stock_compare",
            query="分析茅台，宁德时代，比亚迪，谁更值得投资",
            retrieval_summary="Stock compare: snapshots=9; methodology_types=['cashflow_resilience', 'comps', 'dcf']",
            evidences=[
                {
                    "evidence_id": "compare-request",
                    "title": "stock_compare input request",
                    "summary": "stock_compare input request\n{'symbols': ['600519', '300750', '002594'], 'compare_targets': [{'symbol': '600519', 'name': '贵州茅台'}, {'symbol': '300750', 'name': '宁德时代'}, {'symbol': '002594', 'name': '比亚迪'}]}",
                    "metadata": {"source_type": "compare_request"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "comps-1",
                    "title": "comps:600519",
                    "summary": "methodology_result type=comps symbol=600519\nvaluation_band={'low': 1400.0, 'base': 1500.0, 'high': 1600.0}\noutputs={'relative_view': 'relative_fair_like'}",
                    "metadata": {"source_type": "methodology_comps", "methodology_type": "comps", "valuation_band_label": "1400.0|1500.0|1600.0"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "dcf-1",
                    "title": "dcf:600519",
                    "summary": "methodology_result type=dcf symbol=600519\nvaluation_band={'low': 1450.0, 'base': 1520.0, 'high': 1650.0}\noutputs={'market_to_intrinsic_gap': 0.03}",
                    "metadata": {"source_type": "methodology_dcf", "methodology_type": "dcf", "valuation_band_label": "1450.0|1520.0|1650.0"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "resilience-1",
                    "title": "cashflow_resilience:600519",
                    "summary": "methodology_result type=cashflow_resilience symbol=600519\nvaluation_band={'base': 1500.0}\noutputs={'debt_capacity_state': 'comfortable', 'cashflow_state': 'resilient'}",
                    "metadata": {"source_type": "methodology_cashflow_resilience", "methodology_type": "cashflow_resilience", "valuation_band_label": "None|1500.0|None"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "comps-2",
                    "title": "comps:300750",
                    "summary": "methodology_result type=comps symbol=300750\nvaluation_band={'low': 180.0, 'base': 195.0, 'high': 210.0}\noutputs={'relative_view': 'growth_supported_neutral'}",
                    "metadata": {"source_type": "methodology_comps", "methodology_type": "comps", "valuation_band_label": "180.0|195.0|210.0"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "dcf-2",
                    "title": "dcf:300750",
                    "summary": "methodology_result type=dcf symbol=300750\nvaluation_band={'low': 170.0, 'base': 190.0, 'high': 220.0}\noutputs={'market_to_intrinsic_gap': -0.18}",
                    "metadata": {"source_type": "methodology_dcf", "methodology_type": "dcf", "valuation_band_label": "170.0|190.0|220.0"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "resilience-2",
                    "title": "cashflow_resilience:300750",
                    "summary": "methodology_result type=cashflow_resilience symbol=300750\nvaluation_band={'base': 195.0}\noutputs={'debt_capacity_state': 'comfortable', 'cashflow_state': 'resilient'}",
                    "metadata": {"source_type": "methodology_cashflow_resilience", "methodology_type": "cashflow_resilience", "valuation_band_label": "None|195.0|None"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "comps-3",
                    "title": "comps:002594",
                    "summary": "methodology_result type=comps symbol=002594\nvaluation_band={'low': 210.0, 'base': 225.0, 'high': 240.0}\noutputs={'relative_view': 'relative_premium_like'}",
                    "metadata": {"source_type": "methodology_comps", "methodology_type": "comps", "valuation_band_label": "210.0|225.0|240.0"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "dcf-3",
                    "title": "dcf:002594",
                    "summary": "methodology_result type=dcf symbol=002594\nvaluation_band={'low': 205.0, 'base': 215.0, 'high': 230.0}\noutputs={'market_to_intrinsic_gap': 0.12}",
                    "metadata": {"source_type": "methodology_dcf", "methodology_type": "dcf", "valuation_band_label": "205.0|215.0|230.0"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "resilience-3",
                    "title": "cashflow_resilience:002594",
                    "summary": "methodology_result type=cashflow_resilience symbol=002594\nvaluation_band={'base': 215.0}\noutputs={'debt_capacity_state': 'watch', 'cashflow_state': 'mixed'}",
                    "metadata": {"source_type": "methodology_cashflow_resilience", "methodology_type": "cashflow_resilience", "valuation_band_label": "None|215.0|None"},
                    "source_refs": [],
                },
            ],
        )

        self.assertIn("已按同一研究口径对 3 只股票做对比", payload["conclusion"])
        self.assertIn("贵州茅台（600519）", payload["conclusion"])
        self.assertIn("宁德时代（300750）", payload["conclusion"])
        self.assertIn("比亚迪（002594）", payload["conclusion"])
        self.assertIn("暂时更占优的是 宁德时代（300750）", payload["conclusion"])
        self.assertTrue(payload["methodologies"])

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

    def test_review_thesis_report_appends_thread_aware_follow_up_for_cashflow_focus(self) -> None:
        service = ReporterService(ReporterProviderStub())

        payload = service.run(
            intent="review_thesis",
            query="review thesis",
            retrieval_summary="Posterior review summary",
            planning_context_hints={
                "recommended_next_step": "validate cashflow resilience and leverage tolerance",
                "thread_topic": "cashflow resilience follow-up",
            },
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

        self.assertIn("cashflow resilience", payload["follow_up"].lower())

    def test_review_thesis_report_can_fall_back_to_methodology_memory_hint_for_cashflow_focus(self) -> None:
        service = ReporterService(ReporterProviderStub())

        payload = service.run(
            intent="review_thesis",
            query="continue the previous work",
            retrieval_summary="Posterior review summary",
            planning_context_hints={
                "methodology_benchmark_scope": "defensive_compounder",
            },
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

        self.assertIn("cashflow resilience", payload["follow_up"].lower())

    def test_methodology_evolution_fallback_report_includes_pending_reason_digest(self) -> None:
        service = ReporterService(ReporterProviderStub())

        payload = service.run(
            intent="methodology_evolution",
            query="promote methodology learnings",
            retrieval_summary="Methodology evolution: proposals=2; statuses=['approved', 'pending']; status_counts={'pending': 1, 'approved': 1}; pending_reasons=['pending_manual_review_dimension_sample_size<2']",
            evidences=[
                {
                    "evidence_id": "proposal-1",
                    "title": "proposal_draft",
                    "summary": "proposal_draft type=MethodologyTemplatePatch status=pending\ntitle=Promote dimension: policy catalyst\nsummary=Promote candidate\nreview_reason=pending_manual_review_dimension_sample_size<2",
                    "metadata": {"source_type": "proposal_draft"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "proposal-2",
                    "title": "proposal_draft",
                    "summary": "proposal_draft type=MemoryLearningUpdate status=approved\ntitle=Capture review failure pattern\nsummary=Persist pattern\nreview_reason=auto_approve_review_pattern_threshold_met",
                    "metadata": {"source_type": "proposal_draft"},
                    "source_refs": [],
                },
            ],
        )

        self.assertIn("approved=1; pending=1", payload["conclusion"])
        self.assertIn("pending_manual_review_dimension_sample_size<2", payload["conclusion"])

    def test_stock_compare_fallback_report_prefers_bank_compare_scorecard(self) -> None:
        service = ReporterService(ReporterProviderStub())

        payload = service.run(
            intent="stock_compare",
            query="以 000001 平安银行为起点，扩到银行股，最近哪几只更值得投资",
            retrieval_summary="Stock compare: snapshots=18; bank_candidate_count=6",
            evidences=[
                {
                    "evidence_id": "compare-request",
                    "title": "stock_compare input request",
                    "summary": "stock_compare input request\n{'symbols': ['000001', '600036'], 'compare_targets': [{'symbol': '000001', 'name': '平安银行'}, {'symbol': '600036', 'name': '招商银行'}]}",
                    "metadata": {"source_type": "compare_request"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "bank-scorecard",
                    "title": "bank_compare_scorecard",
                    "summary": "bank_compare_scorecard\n{'candidate_count': 3, 'metric_weights': {'pb': 0.24, 'roe': 0.24, 'interest_margin_proxy': 0.06}, 'ranked_candidates': [{'symbol': '601838', 'name': '成都银行', 'score': 0.95, 'pb': 0.88, 'highlights': ['PB=0.88', 'ROE=12.90%', '净利率代理=0.44%']}, {'symbol': '600919', 'name': '江苏银行', 'score': 0.90, 'pb': 0.69, 'highlights': ['PB=0.69', 'ROE=12.40%', '净利率代理=0.40%']}, {'symbol': '600036', 'name': '招商银行', 'score': 0.88, 'pb': 0.78, 'highlights': ['PB=0.78', 'ROE=11.76%', '净利率代理=0.42%']}], 'missing_metrics': ['loan_to_deposit_proxy'], 'summary': 'bank peer comparison ranked 3 banks using same-basis banking metrics and valuation anchors'}",
                    "metadata": {"source_type": "bank_compare_scorecard"},
                    "source_refs": [],
                },
            ],
        )

        self.assertIn("同口径银行股指标", payload["conclusion"])
        self.assertIn("PB", payload["conclusion"])
        self.assertIn("核心权重", payload["conclusion"])
        self.assertIn("成都银行", payload["conclusion"])
        self.assertIn("江苏银行", payload["conclusion"])
        self.assertIn("招商银行", payload["conclusion"])
        self.assertIn("loan_to_deposit_proxy", payload["conclusion"])

    def test_stock_compare_with_bank_scorecard_skips_llm_and_returns_deterministic_report(self) -> None:
        service = ReporterService(ReporterLLMUnexpectedCallStub())

        payload = service.run(
            intent="stock_compare",
            query="浠?000001 骞冲畨閾惰涓鸿捣鐐癸紝鎵╁埌閾惰鑲★紝鏈€杩戝摢鍑犲彧鏇村€煎緱鎶曡祫",
            retrieval_summary="Stock compare: snapshots=18; bank_candidate_count=6",
            evidences=[
                {
                    "evidence_id": "compare-request",
                    "title": "stock_compare input request",
                    "summary": "stock_compare input request\n{'symbols': ['000001', '600036'], 'compare_targets': [{'symbol': '000001', 'name': '骞冲畨閾惰'}, {'symbol': '600036', 'name': '鎷涘晢閾惰'}]}",
                    "metadata": {"source_type": "compare_request"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "bank-scorecard",
                    "title": "bank_compare_scorecard",
                    "summary": "bank_compare_scorecard\n{'candidate_count': 3, 'metric_weights': {'pb': 0.24, 'roe': 0.24, 'interest_margin_proxy': 0.06}, 'ranked_candidates': [{'symbol': '601838', 'name': '鎴愰兘閾惰', 'score': 0.95, 'pb': 0.88, 'highlights': ['PB=0.88', 'ROE=12.90%', '鍑€鍒╃巼浠ｇ悊=0.44%']}, {'symbol': '600919', 'name': '姹熻嫃閾惰', 'score': 0.90, 'pb': 0.69, 'highlights': ['PB=0.69', 'ROE=12.40%', '鍑€鍒╃巼浠ｇ悊=0.40%']}, {'symbol': '600036', 'name': '鎷涘晢閾惰', 'score': 0.88, 'pb': 0.78, 'highlights': ['PB=0.78', 'ROE=11.76%', '鍑€鍒╃巼浠ｇ悊=0.42%']}], 'missing_metrics': ['loan_to_deposit_proxy'], 'summary': 'bank peer comparison ranked 3 banks using same-basis banking metrics and valuation anchors'}",
                    "metadata": {"source_type": "bank_compare_scorecard"},
                    "source_refs": [],
                },
            ],
        )

        self.assertIn("基于同口径银行股指标与估值锚点", payload["conclusion"])
        self.assertIn("loan_to_deposit_proxy", payload["conclusion"])
        self.assertIsNone(payload["degraded_note"])

    def test_stock_compare_fallback_report_surfaces_dividend_yield_proxy(self) -> None:
        service = ReporterService(ReporterProviderStub())

        payload = service.run(
            intent="stock_compare",
            query="以 000001 平安银行为起点，扩到银行股，最近哪几只更值得投资",
            retrieval_summary="Stock compare: snapshots=18; bank_candidate_count=6",
            evidences=[
                {
                    "evidence_id": "compare-request",
                    "title": "stock_compare input request",
                    "summary": "stock_compare input request\n{'symbols': ['000001', '600036'], 'compare_targets': [{'symbol': '000001', 'name': '平安银行'}, {'symbol': '600036', 'name': '招商银行'}]}",
                    "metadata": {"source_type": "compare_request"},
                    "source_refs": [],
                },
                {
                    "evidence_id": "bank-scorecard",
                    "title": "bank_compare_scorecard",
                    "summary": "bank_compare_scorecard\n{'candidate_count': 3, 'metric_weights': {'pb': 0.24, 'roe': 0.24, 'dividend_yield_proxy': 0.08}, 'ranked_candidates': [{'symbol': '601838', 'name': '成都银行', 'score': 0.95, 'pb': 0.88, 'highlights': ['PB=0.88', 'ROE=12.90%', '股息率代理=4.29%']}, {'symbol': '600919', 'name': '江苏银行', 'score': 0.90, 'pb': 0.69, 'highlights': ['PB=0.69', 'ROE=12.40%', '股息率代理=6.72%']}, {'symbol': '600036', 'name': '招商银行', 'score': 0.88, 'pb': 0.78, 'highlights': ['PB=0.78', 'ROE=11.76%', '股息率代理=4.36%']}], 'missing_metrics': ['loan_to_deposit_proxy'], 'summary': 'bank peer comparison ranked 3 banks using same-basis banking metrics and valuation anchors'}",
                    "metadata": {"source_type": "bank_compare_scorecard"},
                    "source_refs": [],
                },
            ],
        )

        self.assertIn("股息率代理", payload["conclusion"])
        self.assertIsNone(payload["degraded_note"])


if __name__ == "__main__":
    unittest.main()
