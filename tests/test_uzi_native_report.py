from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Lock
import time
import unittest
from unittest.mock import patch

from domain_packs.finance_ashare.reporting import (
    ReportArtifactMissingError,
    render_uzi_native_html,
    render_uzi_native_pdf,
)


RUN_ID = "01JREPORT000000000000000001"


def sample_uzi_artifacts() -> list[dict]:
    dim_names = [
        "basic", "financials", "kline", "macro", "peers", "chain", "research", "industry",
        "materials", "futures", "valuation", "governance", "capital_flow", "policy", "moat",
        "events", "lhb", "sentiment", "trap", "contests",
    ]
    raw_dims = {f"{index}_{name}": {"data": {}, "source": "test", "fallback": False} for index, name in enumerate(dim_names)}
    raw_dims["0_basic"]["data"] = {
        "code": "600519", "name": "贵州茅台 <script>alert(1)</script>", "industry": "白酒",
        "price": 1450.5, "change_pct": 1.2, "market_cap": "1.82万亿", "pe_ttm": 22.4, "pb": 7.1,
    }
    raw_dims["1_financials"]["data"] = {"roe": 31.2, "roe_history": [29.0, 30.1, 31.2]}
    raw_dims["2_kline"]["data"] = {"stage": "Stage 2", "ma_align": "多头排列", "kline_stats": {}}
    raw_dims["18_trap"]["data"] = {"trap_level": "🟢 安全", "recommendation": "未发现异常推广痕迹"}
    raw_dims["20_valuation_models"] = {"data": {}, "source": "compute", "fallback": False}
    raw_dims["21_research_workflow"] = {"data": {}, "source": "compute", "fallback": False}
    raw_dims["22_deep_methods"] = {"data": {}, "source": "compute", "fallback": False}
    scores = {
        f"{index}_{name}": {"score": 6, "weight": 3, "label": "真实证据测试", "reasons_pass": [], "reasons_fail": []}
        for index, name in enumerate(dim_names) if index > 0
    }
    investors = [
        {
            "investor_id": "buffett", "name": "沃伦·巴菲特", "group": "A", "avatar": "avatars/buffett.svg",
            "signal": "bullish", "confidence": 80, "score": 88, "verdict": "买入", "reasoning": "现金流证据支持长期观察。",
            "comment": "现金流证据支持长期观察。", "headline": "ROE 31.2% 支持质量判断", "pass": [], "fail": [],
        },
        {
            "investor_id": "graham", "name": "本杰明·格雷厄姆", "group": "A", "avatar": "avatars/graham.svg",
            "signal": "bearish", "confidence": 70, "score": 42, "verdict": "观望", "reasoning": "当前价格下安全边际有限。",
            "comment": "当前价格下安全边际有限。", "headline": "PE 22.4 倍限制安全边际", "pass": [], "fail": [],
        },
    ]
    panel = {
        "investors": investors,
        "signal_distribution": {"bullish": 1, "bearish": 1, "neutral": 0, "skip": 0},
        "vote_distribution": {"strongly_buy": 0, "buy": 1, "watch": 0, "wait": 1, "avoid": 0},
        "panel_consensus": 65.0,
        "school_scores": {},
    }
    synthesis = {
        "ticker": "600519", "name": "贵州茅台 <script>alert(1)</script>", "overall_score": 72.0,
        "verdict_label": "可以蹲一蹲", "verdict_detail": "基本面 76.0 · 共识 65.0",
        "dashboard": {
            "core_conclusion": "真实数据支持质量优势，但估值安全边际仍需复核。",
            "data_perspective": {"trend": "Stage 2", "price": "1450.5", "volume": "待复核", "chips": "待复核"},
            "intelligence": {"news": "暂无重大新闻", "risks": ["估值波动"], "catalysts": []},
            "battle_plan": {"entry": "观察", "position": "审慎", "stop": "依风险预算", "target": "依估值复核"},
        },
        "debate": {
            "bull": investors[0], "bear": investors[1],
            "rounds": [
                {"round": 1, "bull_say": "ROE 31.2% 支持质量判断", "bear_say": "PE 22.4 倍限制安全边际"},
                {"round": 2, "bull_say": "品牌现金流具备持续性", "bear_say": "价格仍需留出折价"},
                {"round": 3, "bull_say": "保持看多但控制价格", "bear_say": "等待更好赔率"},
            ],
        },
        "great_divide": {
            "bull_score": 88, "bear_score": 42, "bull_signal": "bullish", "bear_signal": "bearish",
            "punchline": "好公司与好价格仍是两件事。",
        },
        "buy_zones": {
            "value": {"price": 1200, "rationale": "估值折价"}, "growth": {"price": 1350, "rationale": "增长兑现"},
            "technical": {"price": 1400, "rationale": "技术支撑"}, "youzi": {"price": 1450.5, "rationale": "现价观察"},
        },
        "risks": ["估值波动", "需求变化", "数据时效"],
        "panel_insights": "两位测试评委围绕质量与价格形成明确分歧。",
        "data_gaps": {
            "coverage_pct": 88, "total_gaps": 1, "unresolved": 0,
            "tasks": [{"dim": "17_sentiment", "field": "self_review_warning", "label": "sentiment:xueqiu_unavailable", "status": "warning"}],
        },
    }
    native = {
        "source_commit": "22e65f2e0cee2cb41cb40fdbe391c9600b7fe359",
        "methodology_version": "3.9.2-source-parity", "profile": "deep",
        "critical_count": 0, "warning_count": 1, "report_allowed": True, "quality_status": "available",
        "recovery_history": [],
        "results": [{
            "symbol": "600519", "raw": {"ticker": "600519", "market": "A", "fetched_at": "2026-08-06T08:30:00+00:00", "dimensions": raw_dims},
            "dimensions": {"dimensions": scores, "fundamental_score": 76}, "panel": panel, "synthesis": synthesis,
            "self_review": {"critical_count": 0, "warning_count": 1, "passed": True, "issues": []},
            "report_allowed": True,
        }],
    }
    return [
        {"artifact_id": "artifact-native", "artifact_type": "uzi_native_analysis", "status": "active", "confidence": 0.86, "risk_flags": [], "source_refs": [], "content": native},
        {"artifact_id": "artifact-report", "artifact_type": "research_report", "status": "active", "confidence": 0.86, "risk_flags": [], "source_refs": [], "content": {"conclusion": "已通过", "evidence": [], "risk": [], "follow_up": [], "report_allowed": True}},
    ]


class UziNativeReportTests(unittest.TestCase):
    def test_renders_native_sections_from_artifact_board_without_raw_html_injection(self) -> None:
        rendered = render_uzi_native_html(sample_uzi_artifacts(), run_id=RUN_ID)

        self.assertEqual(rendered.symbol, "600519")
        self.assertEqual(rendered.filename_stem, f"uzi-report-600519-{RUN_ID}")
        self.assertIn("游资 SKILLS", rendered.html)
        self.assertIn("50 INVESTORS", rendered.html)
        self.assertIn("UZI V3.9.4", rendered.html.upper())
        self.assertIn("data-gap-banner", rendered.html)
        self.assertIn("机构级估值建模", rendered.html)
        self.assertIn("sentiment:xueqiu_unavailable", rendered.html)
        self.assertIn("贵州茅台 &lt;script&gt;alert(1)&lt;/script&gt;", rendered.html)
        self.assertNotIn("贵州茅台 <script>alert(1)</script>", rendered.html)
        self.assertNotRegex(rendered.html, r"\{\{[A-Z0-9_]+\}\}")
        self.assertNotRegex(rendered.html, r"<!-- INJECT_[A-Z0-9_]+ -->")
        self.assertIn("data:image/svg+xml;base64,", rendered.html)
        self.assertIn(".dim-card:has(.dim-score .num.high)::before", rendered.html)
        self.assertIn(".lbo-block .lbo-spark-grid", rendered.html)
        self.assertIn("scroll-behavior: smooth", rendered.html)
        self.assertEqual(
            rendered.source_artifact_ids,
            ("artifact-native", "artifact-report"),
        )

    def test_critical_quality_gate_cannot_be_bypassed_by_completed_report(self) -> None:
        artifacts = sample_uzi_artifacts()
        native = artifacts[0]["content"]
        native["report_allowed"] = False
        native["critical_count"] = 1
        native["quality_status"] = "quality_gate_blocked"
        native["results"][0]["report_allowed"] = False
        native["results"][0]["self_review"]["critical_count"] = 1
        artifacts[1]["content"]["report_allowed"] = True

        with self.assertRaises(ReportArtifactMissingError):
            render_uzi_native_html(artifacts, run_id=RUN_ID)

    def test_validated_segmental_artifact_uses_native_uzi_segmental_renderer(self) -> None:
        artifacts = sample_uzi_artifacts()
        artifacts.append({
            "artifact_id": "artifact-segmental",
            "artifact_type": "uzi_native_command_result",
            "status": "active",
            "content": {
                "command_id": "segmental-model",
                "phase": "validated",
                "results": [{
                    "symbol": "600519",
                    "data_gap": [],
                    "result": {
                        "phase": "validated",
                        "filled_model": {
                            "name": "贵州茅台",
                            "currency": "CNY",
                            "core_thesis": "核心产品贡献主要收入，系列酒提供增量。",
                            "total_revenue_latest_yi": 1000.0,
                            "total_revenue_history_yi": [800.0, 900.0, 1000.0],
                            "source_notes": ["基金披露与公司年报"],
                            "segments": [{
                                "name": "核心产品",
                                "latest_revenue_yi": 800.0,
                                "latest_share_pct": 80.0,
                                "drivers": ["真实披露销量"],
                                "thesis_tag": "cash_cow",
                                "bull_growth_3y_cagr": 12.0,
                                "base_growth_3y_cagr": 8.0,
                                "bear_growth_3y_cagr": 2.0,
                                "revenue_history_yi": [650.0, 720.0, 800.0],
                                "history_periods": ["2023", "2024", "2025"],
                            }],
                        },
                        "validation": {
                            "passed": True,
                            "warnings": [],
                            "summary": {"reconciliation_gap_pct": 0.0, "base_3y_total_growth_pct": 25.97},
                        },
                    },
                }],
            },
        })

        rendered = render_uzi_native_html(artifacts, run_id=RUN_ID)

        self.assertIn("SEGMENTAL · 分业务建模", rendered.html)
        self.assertIn("核心产品", rendered.html)
        self.assertIn("3 情景 × 3 年营收预测", rendered.html)

    def test_renders_hk_currency_without_hardcoded_renminbi_symbol(self) -> None:
        artifacts = sample_uzi_artifacts()
        result = artifacts[0]["content"]["results"][0]
        result["raw"]["market"] = "H"
        result["raw"]["ticker"] = "00700.HK"

        rendered = render_uzi_native_html(artifacts, run_id=RUN_ID)

        self.assertIn("HK$", rendered.html)
        self.assertNotIn("¥1,450.5", rendered.html)

    def test_rejects_non_uzi_runs_and_legacy_custom_reports(self) -> None:
        with self.assertRaises(ReportArtifactMissingError):
            render_uzi_native_html([], run_id=RUN_ID)
        with self.assertRaises(ReportArtifactMissingError):
            render_uzi_native_html(
                [{"artifact_type": "research_report", "status": "active", "content": {"conclusion": "generic"}}],
                run_id=RUN_ID,
            )

    def test_single_stock_export_rejects_comparison_analysis(self) -> None:
        artifacts = sample_uzi_artifacts()
        second = dict(artifacts[0]["content"]["results"][0])
        second["symbol"] = "000001"
        artifacts[0]["content"]["results"].append(second)

        with self.assertRaisesRegex(ReportArtifactMissingError, "且仅允许一个"):
            render_uzi_native_html(artifacts, run_id=RUN_ID)

    def test_pdf_pool_bounds_ten_concurrent_exports_and_caches_identical_html(self) -> None:
        state = {
            "active": 0,
            "max_active": 0,
            "renders": 0,
            "evaluations": [],
            "media_options": [],
        }
        lock = Lock()

        class FakePage:
            def set_content(self, *_args, **_kwargs) -> None:
                pass

            def evaluate(self, script: str) -> None:
                state["evaluations"].append(script)

            def emulate_media(self, **kwargs) -> None:
                state["media_options"].append(kwargs)

            def pdf(self, **_kwargs) -> bytes:
                with lock:
                    state["active"] += 1
                    state["renders"] += 1
                    state["max_active"] = max(state["max_active"], state["active"])
                time.sleep(0.02)
                with lock:
                    state["active"] -= 1
                return b"%PDF-1.7\nstrict-uzi-test"

        class FakeBrowser:
            def new_page(self, **_kwargs) -> FakePage:
                return FakePage()

            def close(self) -> None:
                return None

        class FakeChromium:
            def launch(self, **_kwargs) -> FakeBrowser:
                return FakeBrowser()

        class FakePlaywright:
            chromium = FakeChromium()

        @contextmanager
        def fake_sync_playwright():
            yield FakePlaywright()

        html = "<html><body>ten-concurrent-uzi-pdf-pool-test</body></html>"
        with patch("playwright.sync_api.sync_playwright", fake_sync_playwright):
            with ThreadPoolExecutor(max_workers=10) as pool:
                payloads = list(pool.map(render_uzi_native_pdf, [html] * 10))
            cached = render_uzi_native_pdf(html)

        self.assertEqual(len(payloads), 10)
        self.assertTrue(all(payload.startswith(b"%PDF") for payload in payloads))
        self.assertEqual(cached, payloads[0])
        self.assertLessEqual(state["max_active"], 2)
        self.assertLessEqual(state["renders"], 2)
        self.assertTrue(any("boot-overlay" in script for script in state["evaluations"]))
        self.assertTrue(any("document.fonts.ready" in script for script in state["evaluations"]))
        self.assertTrue(state["media_options"])
        self.assertTrue(all(options["media"] == "print" for options in state["media_options"]))
        self.assertTrue(all(options["color_scheme"] == "light" for options in state["media_options"]))
        self.assertTrue(all(options["reduced_motion"] == "reduce" for options in state["media_options"]))


if __name__ == "__main__":
    unittest.main()
