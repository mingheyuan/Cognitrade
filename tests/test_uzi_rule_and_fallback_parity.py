from __future__ import annotations

import os
from pathlib import Path
import unittest
from unittest.mock import patch

from domain_packs.finance_ashare.subagents.uzi_methodology import select_profile
from domain_packs.finance_ashare.subagents.uzi_panel_context import YOUZI_FIT_RULES, YOUZI_SEAT_KEYWORDS
from domain_packs.finance_ashare.subagents.uzi_panel_profiles import PROFILES
from domain_packs.finance_ashare.subagents.uzi_panel_registry import PANEL_REGISTRY
from domain_packs.finance_ashare.subagents.uzi_panel_rules import (
    INVESTOR_RULES,
    coverage_stats,
    evaluate_investor,
)
from domain_packs.finance_ashare.tools.uzi_data_recovery import (
    assess_integrity,
    recover_playwright_snapshots,
    recover_qualitative_snapshots,
)
from domain_packs.finance_ashare.skills.policy_snapshot.skill import PolicySnapshotSkill
from domain_packs.finance_ashare.tools.snapshot_collector import _bundle_result, _recovery_summary
from research_system.adapters.vendors.uzi_qualitative_fallback import (
    UziQualitativeFallback,
    is_junk_fallback_text,
)
from research_system.runtime.agent_os import AgentOS
from research_system.schemas.core_runtime import RequestIntakeDecision
from research_system.schemas.protocol import RequestEnvelope
from research_system.runtime.platform.settings import Settings


def _settings(**updates) -> Settings:
    base = Settings.from_env(Path(__file__).resolve().parents[1])
    return base.model_copy(update=updates)


class UziRuleSourceParityTests(unittest.TestCase):
    def test_all_66_seats_and_242_rules_are_present(self) -> None:
        self.assertEqual(coverage_stats(), {
            "total_investors": 66,
            "total_rules": 242,
            "avg_rules": 3.7,
            "min_rules": 2,
            "max_rules": 7,
        })
        self.assertEqual(len(PANEL_REGISTRY), 66)
        self.assertEqual(set(INVESTOR_RULES), {str(row["id"]) for row in PANEL_REGISTRY})
        self.assertEqual(len(PROFILES), 25)
        self.assertEqual(len(YOUZI_FIT_RULES), 23)
        self.assertEqual(len(YOUZI_SEAT_KEYWORDS), 23)

    def test_every_vendored_metadata_field_is_exposed(self) -> None:
        buffett = next(row for row in PANEL_REGISTRY if row["id"] == "buffett")
        ghzw = next(row for row in PANEL_REGISTRY if row["id"] == "ghzw")
        self.assertEqual(buffett["en"], "Warren Buffett")
        self.assertEqual(buffett["source"], "Berkshire Hathaway Letters")
        self.assertEqual(buffett["avatar_seed"], "Buffett-Owl-Glasses")
        self.assertEqual(ghzw["tier"], "flagship")
        self.assertIn("8951", str(ghzw["source"]))

    def test_analysis_profile_is_explicit_and_matches_uzi_medium(self) -> None:
        with self.assertRaisesRegex(ValueError, "research_profile is required"):
            select_profile({})
        with self.assertRaisesRegex(ValueError, "lite, medium or deep"):
            select_profile({"research_profile": "quick"})
        profile = select_profile({"research_profile": "medium"})
        self.assertEqual(profile["selected"], "medium")
        self.assertEqual(profile["ddg_budget"], 30)
        self.assertEqual(profile["investors_count"], 51)
        self.assertEqual(profile["playwright_mode"], "opt-in")
        self.assertEqual(len(profile["playwright_dimensions"]), 6)

    def test_all_three_profiles_match_the_pinned_uzi_contract(self) -> None:
        lite = select_profile({"research_profile": "lite"})
        medium = select_profile({"research_profile": "medium"})
        deep = select_profile({"research_profile": "deep"})

        self.assertEqual(
            set(lite["fetchers_enabled"]),
            {"0_basic", "1_financials", "2_kline", "10_valuation", "11_governance", "15_events", "16_lhb"},
        )
        self.assertEqual(len(medium["fetchers_enabled"]), 20)
        self.assertEqual(set(medium["fetchers_enabled"]), set(deep["fetchers_enabled"]))
        self.assertEqual([lite["ddg_budget"], medium["ddg_budget"], deep["ddg_budget"]], [0, 30, 60])
        self.assertEqual([lite["investors_count"], medium["investors_count"], deep["investors_count"]], [10, 51, 66])
        self.assertEqual(
            [len(lite["institutional_methods"]), len(medium["institutional_methods"]), len(deep["institutional_methods"])],
            [1, 17, 18],
        )
        self.assertEqual(
            [lite["enable_bull_bear_debate"], medium["enable_bull_bear_debate"], deep["enable_bull_bear_debate"]],
            [False, False, True],
        )
        self.assertEqual(
            [len(lite["playwright_dimensions"]), len(medium["playwright_dimensions"]), len(deep["playwright_dimensions"])],
            [0, 6, 10],
        )

    def test_snapshot_bundle_rejects_removed_or_unknown_profiles(self) -> None:
        for profile in ("quick", "unknown"):
            with self.subTest(profile=profile), self.assertRaisesRegex(
                ValueError,
                "lite, medium or deep",
            ):
                _bundle_result(
                    snapshots=[],
                    symbols=[],
                    requested=[],
                    universe_rows=[],
                    universe_source="test",
                    enrichment_targets=[],
                    query="test",
                    research_profile=profile,
                    settings=_settings(),
                )

    def test_agent_os_root_turn_uses_the_uzi_profile_budget(self) -> None:
        settings = _settings()
        agent_os = AgentOS(settings)
        decision = RequestIntakeDecision(
            status="accepted",
            request_kind="domain_task",
            recommended_mode="graph",
            intent="stock_check",
            domain_id="finance_ashare",
        )
        medium_config = agent_os._domain_turn_config(
            request=RequestEnvelope(
                intent="stock_check",
                user_query="标准分析 600519",
                constraints={"research_profile": "medium"},
            ),
            decision=decision,
            execution_mode="graph",
        )
        deep_config = agent_os._domain_turn_config(
            request=RequestEnvelope(
                intent="stock_check",
                user_query="深度分析 600519",
                constraints={"research_profile": "deep"},
            ),
            decision=decision,
            execution_mode="graph",
        )
        self.assertEqual(medium_config.timeout_seconds, 480)
        self.assertEqual(deep_config.timeout_seconds, 1200)

    def test_source_semantics_skip_only_rules_that_cannot_evaluate_missing_data(self) -> None:
        features = {
            "ticker": "000001",
            "market": "A",
            "market_cap": 20_000_000_000,
            "industry": "银行",
            "matched_youzi": [str(row["name"]) for row in PANEL_REGISTRY if row["group"] == "F"],
        }
        for investor_id, rules in INVESTOR_RULES.items():
            result = evaluate_investor(investor_id, dict(features))
            if result["signal"] == "skip":
                self.assertTrue(result["skip_reason"])
                self.assertEqual(result["score"], -1)
                continue
            self.assertEqual(
                result["pass_count"] + result["fail_count"] + result["skipped_rule_count"],
                len(rules),
                investor_id,
            )
            self.assertNotIn("skipped_rules", result)
            self.assertNotIn("data_gap_rules", result)

    def test_missing_none_is_excluded_from_rule_denominator_not_counted_as_failure(self) -> None:
        missing = evaluate_investor("buffett", {
            "ticker": "600519",
            "market": "A",
            "name": "贵州茅台",
            "industry": "白酒",
            "net_margin": None,
        })
        observed_failure = evaluate_investor("buffett", {
            "ticker": "600519",
            "market": "A",
            "name": "贵州茅台",
            "industry": "白酒",
            "net_margin": 10.0,
        })

        self.assertEqual(missing["skipped_rule_count"], 1)
        self.assertEqual(missing["fail_count"], observed_failure["fail_count"] - 1)
        self.assertEqual(missing["weight_total"], observed_failure["weight_total"] - 3)
        self.assertFalse(any(rule["rule_id"] == "net_margin_15" for rule in missing["fail_rules"]))
        self.assertTrue(any(rule["rule_id"] == "net_margin_15" for rule in observed_failure["fail_rules"]))

    def test_uzi_missing_key_defaults_and_whole_seat_skips_are_exact(self) -> None:
        chanos = evaluate_investor("chanos", {})
        self.assertEqual((chanos["signal"], chanos["score"], chanos["confidence"]), ("bullish", 100, 100))
        self.assertEqual(chanos["pass_count"], 5)

        with patch.dict(os.environ, {"UZI_SCHOOL": "A"}, clear=False):
            self.assertNotEqual(evaluate_investor("buffett", {})["signal"], "skip")
            locked = evaluate_investor("musk", {})
            self.assertEqual(locked["signal"], "skip")
            self.assertIn("用户锁定", locked["skip_reason"])

        market_skip = evaluate_investor("thorp", {"market": "A"})
        self.assertEqual(market_skip["signal"], "skip")
        self.assertIn("不看A市场", market_skip["skip_reason"])

    def test_youzi_actual_lhb_match_overrides_range_skip(self) -> None:
        outside = evaluate_investor("zhao_lg", {
            "market": "A", "market_cap": 1_000_000_000_000, "matched_youzi": [],
        })
        self.assertEqual(outside["signal"], "skip")
        matched = evaluate_investor("zhao_lg", {
            "market": "A", "market_cap": 1_000_000_000_000, "matched_youzi": ["赵老哥"],
        })
        self.assertNotEqual(matched["signal"], "skip")


class UziFallbackParityTests(unittest.TestCase):
    def test_policy_dimension_is_collected_per_symbol_without_sector_scope(self) -> None:
        skill = PolicySnapshotSkill()
        with patch.object(skill.adapter, "get_policy_snapshot", return_value={"policy_items": []}), patch(
            "domain_packs.finance_ashare.skills.policy_snapshot.skill.UziWebSearchAdapter.search",
            return_value={"results": [{"title": "监管原文", "url": "https://example.test"}], "source_trace": []},
        ), patch(
            "domain_packs.finance_ashare.skills.policy_snapshot.skill.UziNewsAdapter.get_news",
            return_value={"sources": {}, "source_trace": []},
        ):
            result = skill.run(query="贵州茅台政策", symbol="600519")
        self.assertEqual(result["symbol"], "600519")
        self.assertNotIn("sector_missing", result.get("degrade_reason") or "")
        self.assertEqual(result["facts"]["availability"], "available")

    def test_recovery_summary_preserves_both_stage_payloads(self) -> None:
        qualitative = {"enabled": True, "attempts": [{"source_id": "ddgs"}]}
        playwright = {"enabled": False, "disabled_reason": "policy"}
        summary = _recovery_summary([], qualitative=qualitative, playwright=playwright)
        self.assertEqual(summary["qualitative_stage"], qualitative)
        self.assertEqual(summary["playwright_stage"], playwright)

    def test_junk_filter_matches_uzi_contract(self) -> None:
        for value in ("", "abc", "类型；类型", "暂无数据", "抱歉，我无法回答这个问题"):
            self.assertTrue(is_junk_fallback_text(value))
        self.assertFalse(is_junk_fallback_text("光模块 800G 需求增长，行业订单已有公开证据"))

    def test_mx_success_stops_before_ddgs_and_browser(self) -> None:
        fallback = UziQualitativeFallback(_settings(mx_api_key="configured"))
        with patch(
            "research_system.adapters.vendors.uzi_qualitative_fallback.MxApiAdapter.query_text",
            return_value={"text": "真实的妙想 API 行业信息，包含足够上下文"},
        ) as mx, patch(
            "research_system.adapters.vendors.uzi_qualitative_fallback.UziWebSearchAdapter.search"
        ) as ddgs:
            result = fallback.collect(query="测试", dimension="industry", symbol="600519")
        self.assertEqual(result["source_id"], "mx_api")
        mx.assert_called_once()
        ddgs.assert_not_called()

    def test_ddgs_is_second_and_failed_chain_is_explicit(self) -> None:
        fallback = UziQualitativeFallback(_settings(mx_api_key="configured"))
        with patch(
            "research_system.adapters.vendors.uzi_qualitative_fallback.MxApiAdapter.query_text",
            return_value={"text": "暂无数据"},
        ), patch(
            "research_system.adapters.vendors.uzi_qualitative_fallback.UziWebSearchAdapter.search",
            return_value={
                "results": [{"title": "真实行业报告", "body": "公开数据显示行业收入持续增长"}],
                "source_trace": [{"source_id": "ddgs", "status": "available"}],
            },
        ):
            result = fallback.collect(query="测试", dimension="industry", symbol="600519")
        self.assertEqual(result["source_id"], "ddgs")

        with patch(
            "research_system.adapters.vendors.uzi_qualitative_fallback.MxApiAdapter.query_text",
            side_effect=ConnectionError("down"),
        ), patch(
            "research_system.adapters.vendors.uzi_qualitative_fallback.UziWebSearchAdapter.search",
            return_value={"results": [], "source_trace": []},
        ):
            failed = fallback.collect(query="测试", dimension="industry", symbol="600519")
        self.assertEqual(failed["status"], "data_gap")
        self.assertEqual(failed["data_gap"], ["mx_ddgs_exhausted"])

    def test_six_dimension_recovery_does_not_run_browser_early(self) -> None:
        snapshots = [{
            "snapshot_type": "macro", "symbol": "600519", "facts": {"availability": "data_gap"}, "degraded": True,
        }]
        with patch(
            "domain_packs.finance_ashare.tools.uzi_data_recovery.UziQualitativeFallback.collect",
            return_value={
                "status": "available", "source_id": "ddgs", "text": "真实宏观数据与利率周期公开信息",
                "query": "q", "source_trace": [{"source_id": "ddgs", "status": "available"}],
            },
        ) as collect:
            result = recover_qualitative_snapshots(
                snapshots, query="研究茅台", symbols=["600519"], profile="deep", settings=_settings(),
            )
        self.assertEqual(result["order"], ["mx_api", "ddgs"])
        self.assertNotIn("allow_browser", collect.call_args.kwargs)
        self.assertNotIn("browser_source_ids", collect.call_args.kwargs)
        self.assertTrue(snapshots[0]["fallback_applied"])
        self.assertEqual(snapshots[0]["quality_status"], "degraded")

    def test_playwright_is_final_profile_scoped_structured_recovery(self) -> None:
        snapshots = [{
            "snapshot_type": "peers", "symbol": "600519", "facts": {"availability": "data_gap"}, "degraded": True,
        }]
        browser_payload = {
            "evidence": {
                "xueqiu_f10": {
                    "text_excerpt": "同行股票",
                    "links": [{"text": "五粮液", "url": "https://xueqiu.com/S/SZ000858"}],
                }
            },
            "source_trace": [{"source_id": "xueqiu_f10", "strategy_id": "xueqiu_f10", "status": "available"}],
        }
        with patch(
            "domain_packs.finance_ashare.tools.uzi_data_recovery.UziBrowserAdapter.collect_many",
            return_value=browser_payload,
        ):
            result = recover_playwright_snapshots(
                snapshots,
                query="研究茅台",
                symbols=["600519"],
                profile="medium",
                settings=_settings(
                    browser_sources_enabled=True,
                    sensitive_sources_enabled=True,
                    sensitive_source_allowlist=["xueqiu"],
                    xueqiu_cookie="xq_a_token=test",
                ),
            )
        self.assertEqual(result["succeeded"], 1)
        self.assertEqual(snapshots[0]["facts"]["peer_table_playwright"][0]["code"], "000858.SZ")
        self.assertIn("xueqiu_f10", snapshots[0]["facts"]["browser_evidence"])

    def test_integrity_keeps_recovery_tasks_and_never_fills_fake_values(self) -> None:
        snapshots = [{
            "snapshot_type": "market", "symbol": "600519", "facts": {"info": {"股票简称": "贵州茅台"}}, "degraded": False,
        }]
        report = assess_integrity(snapshots, ["600519"])
        symbol = report["symbols"][0]
        self.assertTrue(symbol["critical_missing"])
        self.assertGreater(len(symbol["recovery_tasks"]), 0)
        self.assertTrue(all(task["status"] == "pending" for task in symbol["recovery_tasks"]))
        self.assertNotIn("price", snapshots[0]["facts"])


if __name__ == "__main__":
    unittest.main()
