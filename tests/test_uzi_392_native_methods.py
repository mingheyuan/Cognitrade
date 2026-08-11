from __future__ import annotations

import importlib
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator
import yaml

from domain_packs.finance_ashare.subagents.uzi_392_native_methods import (
    UZI_NATIVE_METHOD_IDS,
    build_uzi_raw,
    execute_native_method,
)
from domain_packs.finance_ashare.vendor.uzi_3_9_2.runtime import (
    UZI_SOURCE_COMMIT,
    Uzi392FetcherRuntime,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads(
    (ROOT / "domain_packs/finance_ashare/artifact_schemas/uzi_native_method_result.json").read_text(
        encoding="utf-8"
    )
)


def _row(snapshot_type: str, data: dict) -> dict:
    return {
        "snapshot_type": snapshot_type,
        "symbol": "600000",
        "facts": {
            "_uzi_raw_result": {
                "data": data,
                "sources": [{"provider": "fixture", "snapshot_type": snapshot_type}],
            }
        },
    }


def _snapshots() -> list[dict]:
    return [
        _row(
            "market",
            {
                "code": "600000",
                "name": "浦发银行",
                "industry": "银行",
                "price": 10.0,
                "market_cap_yi": 3000.0,
                "pe_ttm": 6.0,
                "pb": 0.6,
            },
        ),
        _row(
            "fundamental",
            {
                "roe_history": [10, 11, 12, 13, 14],
                "revenue_history": [1000, 1100, 1200, 1300, 1400],
                "net_profit_history": [100, 110, 120, 130, 140],
                "financial_health": {
                    "debt_ratio": 40,
                    "current_ratio": 1.2,
                    "total_debt": 500,
                    "cash": 200,
                },
            },
        ),
        _row(
            "kline",
            {
                "stage": "Stage 2",
                "ma_align": "多头排列",
                "macd": "水上金叉",
                "rsi": 55,
                "kline_stats": {"ytd_return": 5, "volatility": 20, "max_drawdown": -10},
            },
        ),
        _row("valuation", {"pe": "6", "pb": "0.6", "industry_pe": 8}),
        _row("events", {"events": [{"date": "2026-08-01", "event": "业绩发布"}]}),
        _row("industry", {"industry": "银行", "industry_overview": "银行业公开数据"}),
        _row("chain", {"upstream": ["存款"], "downstream": ["贷款"]}),
        _row("moat", {"moat_signals": ["规模"]}),
        _row("research", {"reports": [{"title": "一致预期", "target_price": 12.0}]}),
        _row(
            "similar_stocks",
            {"similar_stocks": [{"ticker": "601398", "name": "工商银行", "pe": 5.0, "market_cap_yi": 10000}]},
        ),
        _row(
            "fund_holders",
            {
                "top_holders": [{"name": "测试基金", "shares": 100}],
                "fund_managers": [{"name": "测试经理", "fund": "测试基金"}],
            },
        ),
    ]


def _stock_inputs() -> dict:
    return {
        "method_assumptions": {
            "three_statement": {
                "revenue_growth_y1": 0.08,
                "revenue_growth_y2": 0.07,
                "revenue_growth_y3": 0.06,
                "revenue_growth_y4": 0.05,
                "revenue_growth_y5": 0.04,
                "gross_margin": 0.45,
                "opex_pct_revenue": 0.2,
                "tax_rate": 0.25,
                "capex_pct_revenue": 0.05,
                "dep_pct_revenue": 0.03,
                "nwc_pct_revenue": 0.1,
            },
            "quick_lbo": {
                "entry_multiple": 8,
                "debt_multiple": 5,
                "exit_multiple": 8,
                "hold_years": 5,
                "ebitda_growth": 0.08,
                "interest_rate": 0.06,
            },
            "accretion_dilution": {
                "target": {
                    "ticker": "000001",
                    "name": "平安银行",
                    "net_income_yi": 100,
                    "shares_yi": 200,
                    "price": 10,
                    "eps": 0.5,
                    "pe": 20,
                },
                "premium_pct": 0.3,
                "cash_pct": 0.5,
                "synergies_yi": 10,
                "new_debt_rate": 0.05,
            },
            "unit_economics": {"accept_uzi_native_defaults": True},
            "value_creation_plan": {"accept_uzi_native_defaults": True},
            "thesis_tracker": {"direction": "long"},
            "quantitative_screen": {"style": "quality"},
        },
        "model_updates": {"rev_growth": 9.0},
    }


class Uzi392NativeMethodTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        Uzi392FetcherRuntime(workspace_root=ROOT / "workspace")

    def test_method_inventory_and_manifest_capabilities_are_exact(self) -> None:
        self.assertEqual(len(UZI_NATIVE_METHOD_IDS), 23)
        self.assertEqual(len(set(UZI_NATIVE_METHOD_IDS)), 23)
        manifest = yaml.safe_load(
            (ROOT / "domain_packs/finance_ashare/domain.yaml").read_text(encoding="utf-8")
        )
        native_capabilities = {
            str(row.get("capability"))
            for row in manifest.get("capabilities") or []
            if str(row.get("capability") or "").startswith("finance.uzi.native.")
        }
        method_capabilities = {
            capability
            for capability in native_capabilities
            if capability.removeprefix("finance.uzi.native.") in UZI_NATIVE_METHOD_IDS
        }
        self.assertEqual(
            method_capabilities,
            {f"finance.uzi.native.{method_id}" for method_id in UZI_NATIVE_METHOD_IDS},
        )
        self.assertEqual(
            native_capabilities - method_capabilities,
            {
                "finance.uzi.native.analyze_stock",
                "finance.uzi.native.report.compose",
                "finance.uzi.native.report.gate",
            },
        )

    def test_every_native_method_has_a_real_result_or_explicit_data_gap(self) -> None:
        rows = _snapshots()
        inputs = _stock_inputs()
        portfolio_inputs = {
            "portfolio_holdings": [
                {"ticker": "600000.SH", "weight": 0.6, "return_pct": 8, "asset_class": "equity"},
                {"ticker": "019785", "weight": 0.4, "return_pct": 2, "asset_class": "bond"},
            ],
            "benchmark_return": 4,
            "rebalance_targets": {"600000.SH": 0.5, "019785": 0.5},
            "asset_class_targets": {"equity": 0.5, "bond": 0.5},
            "accept_uzi_native_fee_schedule": True,
        }
        for method_id in UZI_NATIVE_METHOD_IDS:
            with self.subTest(method_id=method_id):
                payload = execute_native_method(
                    method_id,
                    rows,
                    ["600000"],
                    portfolio_inputs if method_id in {"returns_attribution", "holding_rebalance", "portfolio_rebalance"} else inputs,
                )
                Draft202012Validator(SCHEMA).validate(payload)
                self.assertEqual(payload["source_commit"], UZI_SOURCE_COMMIT)
                self.assertEqual(payload["implementation"], "vendored_uzi_3_9_2_pure_function")
                self.assertTrue(payload["results"])
                row = payload["results"][0]
                self.assertTrue(isinstance(row["result"], dict) or row["data_gap"])

    def test_adapter_preserves_upstream_dcf_result_byte_for_byte(self) -> None:
        rows = _snapshots()
        raw = build_uzi_raw(rows, "600000")
        features = importlib.import_module("lib.stock_features").extract_features(raw, {})
        expected = importlib.import_module("lib.fin_models").compute_dcf(features)
        actual = execute_native_method("dcf", rows, ["600000"], {})
        self.assertEqual(actual["results"][0]["result"], expected)

    def test_raw_builder_joins_suffixed_and_plain_a_share_symbols(self) -> None:
        rows = _snapshots()
        rows[0]["symbol"] = "600000.SH"
        rows[1]["symbol"] = "600000"

        raw = build_uzi_raw(rows, "600000")

        self.assertIn("0_basic", raw["dimensions"])
        self.assertIn("1_financials", raw["dimensions"])
        self.assertEqual(raw["ticker"], "600000")
        self.assertEqual(raw["code"], "600000")
        self.assertEqual(raw["full"], "600000.SH")

    def test_raw_builder_matches_uzi_bonus_collector_top_level_contract(self) -> None:
        raw = build_uzi_raw(_snapshots(), "600000")

        self.assertEqual(raw["similar_stocks"][0]["ticker"], "601398")
        self.assertNotIn("similar_stocks", raw["dimensions"])
        self.assertEqual(raw["fund_managers"][0]["name"], "测试经理")
        self.assertIn("6_fund_holders", raw["dimensions"])
        self.assertNotIn("fund_managers", raw["dimensions"]["6_fund_holders"]["data"])
        self.assertEqual(
            raw["dimensions"]["6_fund_holders"]["data"]["top_holders"][0]["name"],
            "测试基金",
        )

    def test_adapter_preserves_workflow_decision_and_tier1_results(self) -> None:
        rows = _snapshots()
        raw = build_uzi_raw(rows, "600000")
        features = importlib.import_module("lib.stock_features").extract_features(raw, {})
        cases = {
            "catalyst_calendar": importlib.import_module("lib.research_workflow").build_catalyst_calendar(features, raw),
            "dd_checklist": importlib.import_module("lib.deep_analysis_methods").build_dd_checklist(features, raw),
            "ai_readiness": importlib.import_module("lib.tier1.ai_readiness").build_ai_readiness(features, raw),
        }
        for method_id, expected in cases.items():
            with self.subTest(method_id=method_id):
                actual = execute_native_method(method_id, rows, ["600000"], {})
                self.assertEqual(actual["results"][0]["result"], expected)

    def test_all_23_method_results_equal_their_pinned_upstream_functions(self) -> None:
        rows = _snapshots()
        raw = build_uzi_raw(rows, "600000")
        features = importlib.import_module("lib.stock_features").extract_features(raw, {})
        fin = importlib.import_module("lib.fin_models")
        workflow = importlib.import_module("lib.research_workflow")
        deep = importlib.import_module("lib.deep_analysis_methods")
        inputs = _stock_inputs()
        assumptions = inputs["method_assumptions"]
        peer_features = [
            {
                **row,
                "ticker": row.get("ticker") or row.get("code"),
                "pe": row.get("pe") or row.get("pe_ttm"),
                "market_cap_yi": row.get("market_cap_yi") or row.get("market_cap"),
            }
            for row in raw["similar_stocks"]
        ]
        expected = {
            "dcf": fin.compute_dcf(features, None),
            "comps": fin.build_comps_table(features, peer_features),
            "three_statement": fin.project_three_stmt(features, assumptions["three_statement"]),
            "quick_lbo": fin.quick_lbo(features, **assumptions["quick_lbo"]),
            "accretion_dilution": fin.accretion_dilution(
                features,
                assumptions["accretion_dilution"]["target"],
                **{key: value for key, value in assumptions["accretion_dilution"].items() if key != "target"},
            ),
            "initiation_report": workflow.build_initiating_coverage(
                features, raw, fin.compute_dcf(features), fin.build_comps_table(features, peer_features)
            ),
            "earnings_review": workflow.build_earnings_analysis(features, raw, consensus=None),
            "catalyst_calendar": workflow.build_catalyst_calendar(features, raw),
            "thesis_tracker": workflow.build_thesis_tracker(features, raw, direction="long"),
            "morning_note": workflow.build_morning_note(features, raw),
            "quantitative_screen": workflow.run_idea_screen(features, style="quality"),
            "sector_review": workflow.build_sector_overview(features, raw),
            "ic_memo": deep.build_ic_memo(
                features, raw, fin.compute_dcf(features), fin.build_comps_table(features, peer_features)
            ),
            "porter_bcg": deep.build_competitive_analysis(features, raw),
            "dd_checklist": deep.build_dd_checklist(features, raw),
            "unit_economics": deep.build_unit_economics(features, raw),
            "value_creation_plan": deep.build_value_creation_plan(features, raw),
            "ai_readiness": importlib.import_module("lib.tier1.ai_readiness").build_ai_readiness(features, raw),
            "earnings_preview": importlib.import_module("lib.tier1.earnings_preview").build_earnings_preview(features, raw),
            "model_update": importlib.import_module("lib.tier1.model_update").build_model_update(
                features,
                raw,
                inputs["model_updates"],
                fin.compute_dcf(features),
                fin.build_comps_table(features, peer_features),
            ),
        }
        portfolio_inputs = {
            "portfolio_holdings": [
                {"ticker": "600000.SH", "weight": 0.6, "return_pct": 8, "asset_class": "equity"},
                {"ticker": "019785", "weight": 0.4, "return_pct": 2, "asset_class": "bond"},
            ],
            "benchmark_return": 4,
            "rebalance_targets": {"600000.SH": 0.5, "019785": 0.5},
            "asset_class_targets": {"equity": 0.5, "bond": 0.5},
            "accept_uzi_native_fee_schedule": True,
        }
        expected.update({
            "returns_attribution": importlib.import_module("lib.tier1.returns_attrib").build_returns_attribution(
                portfolio_inputs["portfolio_holdings"], portfolio_inputs["benchmark_return"]
            ),
            "holding_rebalance": importlib.import_module("lib.tier1.rebalance").build_rebalance(
                portfolio_inputs["portfolio_holdings"], portfolio_inputs["rebalance_targets"], 5.0
            ),
            "portfolio_rebalance": deep.build_portfolio_rebalance(
                portfolio_inputs["portfolio_holdings"], portfolio_inputs["asset_class_targets"]
            ),
        })

        self.assertEqual(set(expected), set(UZI_NATIVE_METHOD_IDS))
        for method_id in UZI_NATIVE_METHOD_IDS:
            with self.subTest(method_id=method_id):
                actual = execute_native_method(
                    method_id,
                    rows,
                    ["600000"],
                    portfolio_inputs if method_id in {
                        "returns_attribution", "holding_rebalance", "portfolio_rebalance"
                    } else inputs,
                )
                self.assertEqual(actual["results"][0]["result"], expected[method_id])

    def test_numeric_defaults_that_stand_in_for_user_decisions_are_blocked(self) -> None:
        rows = _snapshots()
        expected_gaps = {
            "three_statement": "explicit_assumption_missing:revenue_growth_y1",
            "quick_lbo": "explicit_assumption_missing:entry_multiple",
            "accretion_dilution": "explicit_assumption_missing:target",
            "model_update": "explicit_assumption_missing:__non_empty__",
            "unit_economics": "accept_uzi_native_defaults_required",
            "value_creation_plan": "accept_uzi_native_defaults_required",
        }
        for method_id, expected_gap in expected_gaps.items():
            with self.subTest(method_id=method_id):
                payload = execute_native_method(method_id, rows, ["600000"], {})
                self.assertIsNone(payload["results"][0]["result"])
                self.assertIn(expected_gap, payload["data_gap"][0])

    def test_missing_returns_are_not_coerced_to_zero_and_fee_schedule_is_explicit(self) -> None:
        holdings = [{"ticker": "600000.SH", "weight": 1.0, "return_pct": None}]
        returns = execute_native_method("returns_attribution", [], [], {"portfolio_holdings": holdings})
        self.assertEqual(returns["quality_status"], "data_gap")
        self.assertIn("return_pct_missing", returns["data_gap"])
        rebalance = execute_native_method("holding_rebalance", [], [], {"portfolio_holdings": holdings})
        self.assertIn("accept_uzi_native_fee_schedule_required", rebalance["data_gap"])


if __name__ == "__main__":
    unittest.main()
