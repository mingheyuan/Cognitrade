from __future__ import annotations

import unittest

from domain_packs.finance_ashare.reporting import (
    ReportArtifactMissingError,
    render_uzi_native_html,
)


RUN_ID = "01JREPORT000000000000000001"


def sample_uzi_artifacts() -> list[dict]:
    report = {
        "conclusion": "贵州茅台当前估值需要结合现金流与风险证据复核。",
        "evidence": [
            {
                "evidence_id": "600519-market-1",
                "source_type": "market",
                "source_name": "tushare",
                "title": "600519 市场快照",
                "summary": "现价 1450.50，PE 22.4。",
                "published_at": "2026-08-06",
                "confidence": 0.8,
                "metadata": {"degraded": False, "source_trace": [{"provider": "tushare"}]},
            }
        ],
        "risk": ["数据时效需要复核"],
        "follow_up": ["刷新公告"],
        "uzi_profile": {"selected": "deep", "source_commit": "22e65f2"},
        "valuation_model": {"models": [{"symbol": "600519", "market_price": 1450.5, "fair_value": 1502.0}]},
        "investor_panel_review": {},
        "risk_scan": {},
        "uzi_dimension_coverage": {"data_gap": []},
    }
    presentation = {
        "version": "2",
        "profile": "deep",
        "currency": "CNY",
        "currency_symbol": "¥",
        "subject": {
            "symbol": "600519",
            "name": "贵州茅台 <script>alert(1)</script>",
            "sector": "白酒",
            "as_of": "2026-08-06",
            "currency": "CNY",
            "currency_symbol": "¥",
        },
        "hero": {
            "symbol": "600519",
            "name": "贵州茅台 <script>alert(1)</script>",
            "price": 1450.5,
            "change_pct": 1.2,
            "score": 78,
            "verdict": "below_proxy_value",
            "safety": "attention",
            "currency": "CNY",
            "currency_symbol": "¥",
        },
        "core_conclusion": report["conclusion"],
        "dashboard": {
            "price": {"current": 1450.5, "change_pct": 1.2, "as_of": "2026-08-06", "currency": "CNY", "currency_symbol": "¥"},
            "valuation": {"fair_value": 1502.0, "margin_of_safety": 3.55, "currency": "CNY", "currency_symbol": "¥"},
            "fundamentals": {"revenue_yi": 1330.0, "net_profit_yi": 650.0, "roe": 31.2},
            "metrics": [{"key": "pe", "label": "PE (TTM)", "value": 22.4, "unit": "x", "status": "available"}],
            "risks": [{"summary": "估值波动"}],
            "catalysts": [],
        },
        "great_divide": {
            "bull": {"top_vote": {"perspective_label": "巴菲特视角", "headline": "现金流稳健", "score": 88, "confidence": 0.8}},
            "bear": {"top_vote": {"perspective_label": "格雷厄姆视角", "headline": "安全边际有限", "score": 42, "confidence": 0.7}},
            "rounds": [{"label": "经典价值", "average_score": 70, "bullish_count": 3, "bearish_count": 1, "neutral_count": 1}],
            "punchline": "质量共识较高，价格分歧仍然存在。",
        },
        "panel": {
            "registered_seat_count": 66,
            "average_score": 78,
            "distribution": {"bullish": 35, "bearish": 12, "neutral": 10, "insufficient_evidence": 9},
            "votes": [{
                "perspective_label": "巴菲特视角",
                "school_label": "经典价值",
                "signal": "bullish",
                "score": 88,
                "confidence": 0.8,
                "headline": "现金流稳健",
                "reasoning": "品牌与现金流证据支持长期观察。",
                "checks": [{"label": "ROE", "passed": True}],
                "required_dimensions": ["financials", "valuation"],
            }],
        },
        "deep_scan": [{
            "dimension_id": "financials",
            "label": "财报扎实度",
            "layer": "source",
            "source_type": "fundamental",
            "status": "available",
            "score": 8.5,
            "weight": 5,
            "evidence": ["ROE 31.2%"],
            "source_refs": [{"type": "api", "id": "financial-1"}],
            "data_gap": [],
        }],
        "institutional_modeling": [{
            "symbol": "600519",
            "status": "available",
            "methods": [{
                "method_id": "dcf",
                "label": "DCF",
                "status": "available",
                "summary": "每股代理价值 1502.0",
                "key_outputs": {"fair_value": 1502.0},
                "assumptions": {"wacc": 0.08},
                "sensitivity": [],
                "methodology_log": ["使用已披露现金流"],
                "data_gap": [],
                "risk_flags": [],
            }],
            "data_gap": [],
        }],
        "risk_list": [{"signal_id": "valuation", "status": "attention", "title": "估值波动", "evidence": "PE 分位需复核"}],
        "buy_zones": [{"label": "现价观察参考", "price": 1450.5, "currency": "CNY", "currency_symbol": "¥", "status": "data_gap", "rationale": "不构成买入区间。"}],
        "data_gap": ["sentiment:xueqiu_unavailable"],
        "generated_at": "2026-08-06T08:30:00+00:00",
    }
    return [
        {"artifact_id": "artifact-report", "artifact_type": "research_report", "status": "active", "confidence": 0.75, "risk_flags": [], "source_refs": [], "content": report},
        {"artifact_id": "artifact-presentation", "artifact_type": "report_presentation", "status": "active", "confidence": 0.75, "risk_flags": [], "source_refs": [], "content": presentation},
        {"artifact_id": "artifact-deep", "artifact_type": "deep_analysis", "status": "active", "confidence": 0.7, "risk_flags": ["decision_support_only"], "source_refs": [{"type": "artifact", "id": "source-1"}], "content": {"summary": "完整深度分析", "data_gap": []}},
    ]


class UziNativeReportTests(unittest.TestCase):
    def test_renders_native_sections_from_artifact_board_without_raw_html_injection(self) -> None:
        rendered = render_uzi_native_html(sample_uzi_artifacts(), run_id=RUN_ID)

        self.assertEqual(rendered.symbol, "600519")
        self.assertEqual(rendered.filename_stem, f"uzi-report-600519-{RUN_ID}")
        self.assertIn("UZI RESEARCH TERMINAL", rendered.html)
        self.assertIn("核心仪表盘", rendered.html)
        self.assertIn("世纪分歧", rendered.html)
        self.assertIn("投资方法论评审团", rendered.html)
        self.assertIn("全维深度扫描", rendered.html)
        self.assertIn("机构级建模", rendered.html)
        self.assertIn("ArtifactBoard 审计附录", rendered.html)
        self.assertIn("sentiment:xueqiu_unavailable", rendered.html)
        self.assertIn("贵州茅台 &lt;script&gt;alert(1)&lt;/script&gt;", rendered.html)
        self.assertNotIn("贵州茅台 <script>alert(1)</script>", rendered.html)
        self.assertNotRegex(rendered.html, r"\{\{[A-Z0-9_]+\}\}")
        self.assertEqual(
            rendered.source_artifact_ids,
            ("artifact-report", "artifact-presentation", "artifact-deep"),
        )

    def test_renders_frozen_fund_holding_context_without_changing_stock_sections(self) -> None:
        artifacts = sample_uzi_artifacts()
        artifacts[0]["content"]["holding_context"] = {
            "parent_batch_id": "batch-1",
            "source_fund_run_id": "fund-run-1",
            "source_fund_artifact_id": "fund-artifact-1",
            "fund_code": "020464",
            "fund_name": "招商中证半导体产业ETF发起式联接A",
            "fund_type": "指数型-股票",
            "holding_rank": 1,
            "symbol": "600519",
            "security_name": "贵州茅台",
            "fund_weight_pct": 13.851444,
            "target_security_weight_pct": 15.02,
            "holding_source": "target_etf_lookthrough",
            "disclosure_date": "2026-06-30",
            "disclosure_age_days": 37,
            "stale_level": "current",
            "disclaimer": "基金定期披露持仓不代表当前仍持有该证券。",
        }

        rendered = render_uzi_native_html(artifacts, run_id=RUN_ID)

        self.assertIn("基金持仓来源", rendered.html)
        self.assertIn("招商中证半导体产业ETF发起式联接A", rendered.html)
        self.assertIn("目标 ETF 穿透持仓", rendered.html)
        self.assertIn("13.85%", rendered.html)
        self.assertIn("基金定期披露持仓不代表当前仍持有该证券", rendered.html)

    def test_renders_hk_currency_without_hardcoded_renminbi_symbol(self) -> None:
        artifacts = sample_uzi_artifacts()
        presentation = artifacts[1]["content"]
        presentation["currency"] = "HKD"
        presentation["currency_symbol"] = "HK$"
        for key in ("subject", "hero"):
            presentation[key]["currency"] = "HKD"
            presentation[key]["currency_symbol"] = "HK$"
        for key in ("price", "valuation"):
            presentation["dashboard"][key]["currency"] = "HKD"
            presentation["dashboard"][key]["currency_symbol"] = "HK$"
        presentation["buy_zones"][0]["currency"] = "HKD"
        presentation["buy_zones"][0]["currency_symbol"] = "HK$"

        rendered = render_uzi_native_html(artifacts, run_id=RUN_ID)

        self.assertIn("HK$1,450.50", rendered.html)
        self.assertIn("HK$1,502.00", rendered.html)
        self.assertNotIn("¥1,450.50", rendered.html)

    def test_numeric_panel_fallback_is_escaped_as_text(self) -> None:
        artifacts = sample_uzi_artifacts()
        artifacts[1]["content"]["panel"].pop("registered_seat_count")

        rendered = render_uzi_native_html(artifacts, run_id=RUN_ID)

        self.assertIn("注册席位", rendered.html)
        self.assertIn("<strong>1</strong>", rendered.html)

    def test_rejects_non_uzi_runs_and_runs_without_research_report(self) -> None:
        with self.assertRaises(ReportArtifactMissingError):
            render_uzi_native_html([], run_id=RUN_ID)
        with self.assertRaises(ReportArtifactMissingError):
            render_uzi_native_html(
                [{"artifact_type": "research_report", "status": "active", "content": {"conclusion": "generic"}}],
                run_id=RUN_ID,
            )


if __name__ == "__main__":
    unittest.main()
