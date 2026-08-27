from __future__ import annotations

import importlib
import json
import unittest
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

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
                "operating_cash_flow_yi": 160,
                "gross_margin": 35,
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


def _complete_method_snapshots() -> list[dict]:
    """Add only the observations needed by strict standalone-method gates.

    ``test_uzi_392_analysis`` imports the historical ``_snapshots`` fixture,
    so keep that fixture stable and enrich a private copy for method tests.
    """
    rows = _snapshots()
    enrichments = {
        "events": {
            "event_timeline": ["2026-08-01 业绩增长"],
            "recent_news": [{"title": "业绩发布"}],
        },
        "industry": {
            "tam": "万亿元级",
            "growth": "6%",
            "lifecycle": "成熟期",
            "industry_market_cap_yi": 60000,
            "cninfo_metrics": {"total_mcap_yi": 60000},
        },
        "chain": {"main_business_breakdown": ["公司金融"]},
        "moat": {
            "scores": {
                "intangible": 8,
                "switching": 8,
                "network": 7,
                "scale": 9,
            }
        },
        "research": {"consensus_rev_yi": 1350, "consensus_ni_yi": 135},
    }
    for row in rows:
        data = row["facts"]["_uzi_raw_result"]["data"]
        data.update(enrichments.get(row["snapshot_type"], {}))
    rows.extend(
        [
            _row(
                "peers",
                {
                    "peer_table": [
                        {"ticker": "601398", "name": "工商银行", "pe": 5.0}
                    ]
                },
            ),
            _row(
                "governance",
                {
                    "pledge": [{"质押比例": 10}],
                    "insider_trades_1y": [{"direction": "buy"}],
                },
            ),
            _row(
                "capital_flow",
                {
                    "main_5d": "净流入 1.2 亿元",
                    "main_fund_flow_20d": [{"净额": 1.2}],
                },
            ),
            _row("policy", {"snippets": [{"title": "金融支持实体经济"}]}),
            _row("lhb", {"lhb_count_30d": 2, "matched_youzi": ["机构专用"]}),
            _row("sentiment", {"thermometer_value": 68, "sentiment_label": "偏热"}),
            _row("trap", {"trap_level": "low", "signals": []}),
        ]
    )
    return rows


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
            "thesis_tracker": {"direction": "long"},
            "quantitative_screen": {"style": "quality"},
        },
        "model_updates": {"rev_growth": 9.0},
    }


class Uzi392NativeMethodTests(unittest.TestCase):
    def test_raw_builder_preserves_terminal_historical_scoring_exclusion(self) -> None:
        row = {
            "snapshot_type": "capital_flow",
            "symbol": "600000",
            "quality_status": "data_gap",
            "facts": {
                "availability": "historically_unavailable",
                "reason_code": "historical_reconstruction_no_proof",
            },
            "query_manifest": {
                "degradation": {
                    "safe_for_report": True,
                    "excluded_from_scoring": True,
                    "reason_code": "historical_reconstruction_no_proof",
                    "message": "目标日没有可验证记录",
                }
            },
        }

        raw = build_uzi_raw([row], "600000")

        dimension = raw["dimensions"]["12_capital_flow"]
        self.assertTrue(dimension["_excluded_from_scoring"])
        self.assertEqual(dimension["data"], {})
        self.assertEqual(
            dimension["_degradation"]["reason_code"],
            "historical_reconstruction_no_proof",
        )

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
        rows = _complete_method_snapshots()
        inputs = _stock_inputs()
        portfolio_inputs = {
            "portfolio_holdings": [
                {"ticker": "600000.SH", "weight": 0.6, "return_pct": 8, "asset_class": "equity"},
                {"ticker": "019785", "weight": 0.4, "return_pct": 2, "asset_class": "bond"},
            ],
            "benchmark_return": 4,
            "rebalance_targets": {"600000.SH": 0.5, "019785": 0.5},
            "asset_class_targets": {"equity": 50, "bond": 50},
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
        rows = _complete_method_snapshots()
        raw = build_uzi_raw(rows, "600000")
        features = importlib.import_module("lib.stock_features").extract_features(raw, {})
        expected = importlib.import_module("lib.fin_models").compute_dcf(features)
        actual = execute_native_method("dcf", rows, ["600000"], {})
        self.assertEqual(actual["results"][0]["result"], expected)

    def test_raw_builder_joins_suffixed_and_plain_a_share_symbols(self) -> None:
        rows = _complete_method_snapshots()
        rows[0]["symbol"] = "600000.SH"
        rows[1]["symbol"] = "600000"

        raw = build_uzi_raw(rows, "600000")

        self.assertIn("0_basic", raw["dimensions"])
        self.assertIn("1_financials", raw["dimensions"])
        self.assertEqual(raw["ticker"], "600000")
        self.assertEqual(raw["code"], "600000")
        self.assertEqual(raw["full"], "600000.SH")

    def test_raw_builder_uses_basic_resolved_ticker_for_a_named_entity(self) -> None:
        market = _row(
            "market",
            {"code": "600519", "name": "贵州茅台", "industry": "白酒"},
        )
        market["symbol"] = "贵州茅台"
        market["facts"]["_uzi_raw_result"].update({
            "ticker": "600519.SH",
            "market": "A",
        })

        raw = build_uzi_raw([market], "贵州茅台")

        self.assertEqual(raw["ticker"], "600519.SH")
        self.assertEqual(raw["code"], "600519")
        self.assertEqual(raw["full"], "600519.SH")

    def test_raw_builder_matches_uzi_bonus_collector_top_level_contract(self) -> None:
        raw = build_uzi_raw(_complete_method_snapshots(), "600000")

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
        rows = _complete_method_snapshots()
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

    def test_native_errors_and_exceptions_do_not_escape_or_look_available(self) -> None:
        rows = [
            _row("market", {"code": "600000", "name": "浦发银行", "price": 10}),
            _row("fundamental", {"revenue_history": [], "net_profit_history": []}),
        ]

        native_error = execute_native_method("dcf", rows, ["600000"], {})
        self.assertEqual(native_error["quality_status"], "data_gap")
        self.assertIsInstance(native_error["results"][0]["result"], dict)
        self.assertIn("error", native_error["results"][0]["result"])
        self.assertIn("native_error", native_error["results"][0]["data_gap"])

        recovered_initiation = execute_native_method(
            "initiation_report", rows, ["600000"], {}
        )
        self.assertEqual(recovered_initiation["quality_status"], "degraded")
        result = recovered_initiation["results"][0]["result"]
        self.assertEqual(result["headline"]["rating"], "未评级 (Not Rated)")
        self.assertEqual(result["headline"]["target_price"], 0)
        self.assertIn("native_warning:nested_dcf_unavailable", recovered_initiation["results"][0]["data_gap"])

    def test_accretion_adapter_maps_the_pinned_acquirer_field_names(self) -> None:
        rows = _complete_method_snapshots()
        assumptions = _stock_inputs()["method_assumptions"]["accretion_dilution"]
        payload = execute_native_method(
            "accretion_dilution",
            rows,
            ["600000"],
            {"method_assumptions": {"accretion_dilution": assumptions}},
        )
        result = payload["results"][0]["result"]

        self.assertEqual(payload["quality_status"], "available")
        self.assertGreater(result["pro_forma_shares_yi"], 300)
        self.assertIn("收购方 140.0", result["methodology_log"][2])

    def test_portfolio_rebalance_maps_public_value_fields_to_native_input(self) -> None:
        holdings = [
            {
                "ticker": "600000.SH",
                "weight": 1.0,
                "value": 100000,
                "asset_class": "A股蓝筹",
            }
        ]
        payload = execute_native_method(
            "portfolio_rebalance", [], [], {"portfolio_holdings": holdings}
        )

        self.assertEqual(payload["quality_status"], "degraded")
        self.assertEqual(
            payload["results"][0]["result"]["portfolio_total_yuan"],
            100000,
        )
        self.assertIn(
            "native_defaults_used:portfolio_rebalance.target_allocation",
            payload["data_gap"],
        )

    def test_all_23_method_results_equal_their_pinned_upstream_functions(self) -> None:
        rows = _complete_method_snapshots()
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
                {
                    **features,
                    "shares_yi": features.get("shares_outstanding_yi"),
                    "net_income_yi": features.get("net_profit_latest_yi"),
                },
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
            "asset_class_targets": {"equity": 50, "bond": 50},
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

    def test_pinned_numeric_defaults_execute_and_are_disclosed_as_degraded(self) -> None:
        rows = _complete_method_snapshots()
        expected_gaps = {
            "three_statement": "native_defaults_used:three_statement",
            "quick_lbo": "native_defaults_used:quick_lbo",
            "model_update": "native_defaults_used:model_update.demo_mode",
            "unit_economics": "native_defaults_used:unit_economics",
            "value_creation_plan": "native_defaults_used:value_creation_plan",
        }
        for method_id, expected_gap in expected_gaps.items():
            with self.subTest(method_id=method_id):
                payload = execute_native_method(method_id, rows, ["600000"], {})
                self.assertIsInstance(payload["results"][0]["result"], dict)
                self.assertEqual(payload["quality_status"], "degraded")
                self.assertIn(expected_gap, payload["data_gap"][0])

        missing_target = execute_native_method(
            "accretion_dilution", rows, ["600000"], {}
        )
        self.assertIsNone(missing_target["results"][0]["result"])
        self.assertIn(
            "explicit_assumption_missing:target",
            missing_target["results"][0]["data_gap"],
        )

    def test_thesis_and_screen_use_the_pinned_uzi_defaults_when_omitted(self) -> None:
        rows = _complete_method_snapshots()
        thesis = execute_native_method("thesis_tracker", rows, ["600000"], {})
        screen = execute_native_method("quantitative_screen", rows, ["600000"], {})

        self.assertEqual(thesis["quality_status"], "degraded")
        self.assertIn(
            "native_source_limitation:roe_last_alias",
            thesis["results"][0]["data_gap"],
        )
        self.assertEqual(thesis["results"][0]["result"]["direction"], "long")
        self.assertEqual(screen["quality_status"], "available")
        self.assertEqual(
            screen["results"][0]["result"]["method"],
            "Idea Screen (quality)",
        )

        invalid_thesis = execute_native_method(
            "thesis_tracker",
            rows,
            ["600000"],
            {"method_assumptions": {"thesis_tracker": {"direction": "sideways"}}},
        )
        invalid_screen = execute_native_method(
            "quantitative_screen",
            rows,
            ["600000"],
            {"method_assumptions": {"quantitative_screen": {"style": "momentum"}}},
        )
        self.assertEqual(
            invalid_thesis["results"][0]["data_gap"],
            ["explicit_assumption_invalid:direction"],
        )
        self.assertEqual(
            invalid_screen["results"][0]["data_gap"],
            ["explicit_assumption_invalid:style"],
        )

        no_pe = execute_native_method(
            "thesis_tracker",
            [
                row
                for row in rows
                if row["snapshot_type"] not in {"market", "valuation"}
            ],
            ["600000"],
            {},
        )
        self.assertEqual(
            no_pe["results"][0]["data_gap"],
            [
                "observation_missing:pe",
                "native_source_limitation:roe_last_alias",
            ],
        )

    def test_method_outputs_do_not_treat_uncollected_dimensions_as_real_zeroes(self) -> None:
        cases = {
            "morning_note": ("lhb", "16_lhb"),
            "quantitative_screen": ("moat", "14_moat"),
            "sector_review": ("peers", "4_peers"),
            "porter_bcg": ("moat", "14_moat"),
            "value_creation_plan": ("industry", "7_industry"),
            "ai_readiness": ("policy", "13_policy"),
        }
        for method_id, (missing_type, missing_dimension) in cases.items():
            with self.subTest(method_id=method_id):
                rows = [
                    row
                    for row in _complete_method_snapshots()
                    if row["snapshot_type"] != missing_type
                ]
                payload = execute_native_method(method_id, rows, ["600000"], {})
                self.assertEqual(payload["quality_status"], "degraded")
                self.assertIsInstance(payload["results"][0]["result"], dict)
                self.assertIn(
                    f"dimension_missing:{missing_dimension}",
                    payload["results"][0]["data_gap"],
                )

    def test_value_screen_does_not_treat_missing_pb_as_zero(self) -> None:
        rows = _complete_method_snapshots()
        for row in rows:
            if row["snapshot_type"] in {"market", "valuation"}:
                row["facts"]["_uzi_raw_result"]["data"].pop("pb", None)

        payload = execute_native_method(
            "quantitative_screen",
            rows,
            ["600000"],
            {"method_assumptions": {"quantitative_screen": {"style": "value"}}},
        )

        self.assertEqual(payload["quality_status"], "degraded")
        self.assertIsInstance(payload["results"][0]["result"], dict)
        self.assertIn(
            "observation_missing:pb",
            payload["results"][0]["data_gap"],
        )

    def test_dcf_family_discloses_missing_price_shares_and_nested_valuation(self) -> None:
        rows = _complete_method_snapshots()
        market = next(row for row in rows if row["snapshot_type"] == "market")
        market_data = market["facts"]["_uzi_raw_result"]["data"]
        market_data.pop("price", None)
        market_data.pop("market_cap_yi", None)

        for method_id in ("dcf", "initiation_report", "ic_memo"):
            with self.subTest(method_id=method_id):
                payload = execute_native_method(method_id, rows, ["600000"], {})
                gaps = payload["results"][0]["data_gap"]
                self.assertEqual(payload["quality_status"], "degraded")
                self.assertIsInstance(payload["results"][0]["result"], dict)
                self.assertIn("observation_missing:price", gaps)
                self.assertIn(
                    "observation_missing:shares_or_market_cap_and_price", gaps
                )
                if method_id != "dcf":
                    self.assertIn("observation_missing:comps.peer_count", gaps)

    def test_native_quality_envelope_exposes_proxies_placeholders_and_warnings(self) -> None:
        dcf_rows = _complete_method_snapshots()
        financial = next(
            row for row in dcf_rows if row["snapshot_type"] == "fundamental"
        )["facts"]["_uzi_raw_result"]["data"]
        financial.pop("operating_cash_flow_yi", None)
        health = financial["financial_health"]
        health.pop("total_debt", None)
        health.pop("cash", None)
        dcf = execute_native_method("dcf", dcf_rows, ["600000"], {})
        self.assertEqual(dcf["quality_status"], "degraded")
        self.assertIn(
            "native_proxy_used:dcf.fcf_from_net_income",
            dcf["results"][0]["data_gap"],
        )
        self.assertIn(
            "native_source_limitation:dcf.net_debt_bridge_missing",
            dcf["results"][0]["data_gap"],
        )

        placeholder_rows = _complete_method_snapshots()
        policy = next(
            row for row in placeholder_rows if row["snapshot_type"] == "policy"
        )
        policy["facts"]["_uzi_raw_result"]["data"] = {"placeholder": True}
        ai = execute_native_method("ai_readiness", placeholder_rows, ["600000"], {})
        self.assertEqual(ai["quality_status"], "degraded")
        self.assertIn("dimension_missing:13_policy", ai["results"][0]["data_gap"])

        preview_rows = [
            row
            for row in _complete_method_snapshots()
            if row["snapshot_type"] != "research"
        ]
        preview = execute_native_method(
            "earnings_preview", preview_rows, ["600000"], {}
        )
        self.assertEqual(preview["quality_status"], "degraded")
        self.assertIn(
            "native_warning:earnings_preview.consensus_missing",
            preview["results"][0]["data_gap"],
        )

    def test_decision_methods_do_not_label_insufficient_inputs_available(self) -> None:
        rows = _complete_method_snapshots()
        comps = execute_native_method("comps", rows, ["600000"], {})
        self.assertEqual(comps["quality_status"], "degraded")
        self.assertIn(
            "observation_missing:comps.peer_count",
            comps["results"][0]["data_gap"],
        )

        porter_rows = _complete_method_snapshots()
        market = next(
            row for row in porter_rows if row["snapshot_type"] == "market"
        )["facts"]["_uzi_raw_result"]["data"]
        market.pop("market_cap_yi", None)
        porter = execute_native_method("porter_bcg", porter_rows, ["600000"], {})
        self.assertEqual(porter["quality_status"], "degraded")
        self.assertIn(
            "observation_missing:market_cap_yi",
            porter["results"][0]["data_gap"],
        )

        lbo_rows = _complete_method_snapshots()
        fundamental = next(
            row for row in lbo_rows if row["snapshot_type"] == "fundamental"
        )
        fundamental["facts"]["_uzi_raw_result"]["data"] = {"placeholder": True}
        lbo = execute_native_method("quick_lbo", lbo_rows, ["600000"], {})
        self.assertEqual(lbo["quality_status"], "degraded")
        self.assertIn("dimension_missing:1_financials", lbo["results"][0]["data_gap"])

    def test_portfolio_public_aliases_and_pinned_defaults_reach_native_code(self) -> None:
        returns = execute_native_method(
            "returns_attribution",
            [],
            [],
            {
                "portfolio_holdings": [
                    {"symbol": "AAA", "return_pct": 5},
                    {"symbol": "BBB"},
                ]
            },
        )
        self.assertEqual(returns["quality_status"], "degraded")
        self.assertNotIn("native_exception:KeyError", returns["data_gap"])
        self.assertIn("BBB", returns["results"][0]["result"]["missing_return_tickers"])

        rebalance = execute_native_method(
            "holding_rebalance",
            [],
            [],
            {"portfolio_holdings": [{"symbol": "AAA"}, {"symbol": "BBB"}]},
        )
        self.assertEqual(rebalance["quality_status"], "degraded")
        self.assertIsInstance(rebalance["results"][0]["result"], dict)

        strategic = execute_native_method(
            "portfolio_rebalance",
            [],
            [],
            {
                "portfolio_holdings": [
                    {"symbol": "AAA", "value": 100000, "asset_class": "A股蓝筹"}
                ],
                "asset_class_targets": {"A股蓝筹": 80, "现金": 20},
            },
        )
        self.assertEqual(strategic["quality_status"], "available")
        self.assertEqual(
            {row["asset_class"] for row in strategic["results"][0]["result"]["drift_rows"]},
            {"A股蓝筹", "现金"},
        )

    def test_accretion_accepts_loss_target_but_rejects_nonpositive_shape(self) -> None:
        target = {
            "ticker": "LOSS",
            "name": "亏损标的",
            "shares_yi": 10,
            "price": 5,
            "net_income_yi": -2,
        }
        assumptions = {
            "target": target,
            "premium_pct": 0.3,
            "cash_pct": 0.5,
            "synergies_yi": 0,
            "new_debt_rate": 0.05,
        }
        accepted = execute_native_method(
            "accretion_dilution",
            _complete_method_snapshots(),
            ["600000"],
            {"method_assumptions": {"accretion_dilution": assumptions}},
        )
        self.assertEqual(accepted["quality_status"], "available")
        self.assertIsInstance(accepted["results"][0]["result"], dict)

        invalid = {**assumptions, "target": {**target, "price": 0}}
        rejected = execute_native_method(
            "accretion_dilution",
            _complete_method_snapshots(),
            ["600000"],
            {"method_assumptions": {"accretion_dilution": invalid}},
        )
        self.assertIsNone(rejected["results"][0]["result"])
        self.assertIn(
            "explicit_assumption_invalid:target.price",
            rejected["results"][0]["data_gap"],
        )

    def test_consensus_model_and_peer_baselines_are_auditable(self) -> None:
        zero_consensus_rows = _complete_method_snapshots()
        research = next(
            row for row in zero_consensus_rows if row["snapshot_type"] == "research"
        )["facts"]["_uzi_raw_result"]["data"]
        research["consensus_rev_yi"] = 0
        research["consensus_ni_yi"] = 0
        implicit = execute_native_method(
            "earnings_review", zero_consensus_rows, ["600000"], {}
        )
        self.assertEqual(implicit["quality_status"], "degraded")
        self.assertIn(
            "native_fallback_used:earnings_review.consensus",
            implicit["results"][0]["data_gap"],
        )

        explicit = execute_native_method(
            "earnings_review",
            _complete_method_snapshots(),
            ["600000"],
            {
                "method_assumptions": {
                    "earnings_review": {"consensus": {"rev": 0, "ni": 0}}
                }
            },
        )
        self.assertIsNone(explicit["results"][0]["result"])
        self.assertEqual(
            explicit["results"][0]["data_gap"],
            [
                "explicit_assumption_invalid:consensus.rev",
                "explicit_assumption_invalid:consensus.ni",
            ],
        )

        model_rows = _complete_method_snapshots()
        financial = next(
            row for row in model_rows if row["snapshot_type"] == "fundamental"
        )["facts"]["_uzi_raw_result"]["data"]
        financial.pop("revenue_history", None)
        financial.pop("gross_margin", None)
        model = execute_native_method(
            "model_update",
            model_rows,
            ["600000"],
            {"model_updates": {"rev_growth": 20, "gross_margin": 40}},
        )
        self.assertEqual(model["quality_status"], "degraded")
        self.assertIn(
            "observation_missing:model_update.rev_growth_baseline",
            model["results"][0]["data_gap"],
        )
        self.assertIn(
            "observation_missing:model_update.gross_margin_baseline",
            model["results"][0]["data_gap"],
        )

        peer_rows = _complete_method_snapshots()
        peer_data = next(
            row for row in peer_rows if row["snapshot_type"] == "similar_stocks"
        )["facts"]["_uzi_raw_result"]["data"]
        peer_data["similar_stocks"] = [
            {"ticker": "AAA", "name": "同行甲"},
            {"ticker": "BBB", "name": "同行乙"},
        ]
        comps = execute_native_method("comps", peer_rows, ["600000"], {})
        self.assertEqual(comps["quality_status"], "degraded")
        self.assertIn(
            "observation_missing:comps.peer_valuation_metrics",
            comps["results"][0]["data_gap"],
        )

    def test_pinned_proxies_and_fixed_defaults_are_disclosed(self) -> None:
        rows = _complete_method_snapshots()
        lbo = execute_native_method(
            "quick_lbo",
            rows,
            ["600000"],
            {
                "method_assumptions": {
                    "quick_lbo": _stock_inputs()["method_assumptions"]["quick_lbo"]
                }
            },
        )
        self.assertEqual(lbo["quality_status"], "degraded")
        self.assertIn(
            "native_proxy_used:quick_lbo.ebitda_from_net_income",
            lbo["results"][0]["data_gap"],
        )

        dd = execute_native_method("dd_checklist", rows, ["600000"], {})
        self.assertIn(
            "native_source_limitation:dd_checklist.roe_last_alias",
            dd["results"][0]["data_gap"],
        )
        porter = execute_native_method("porter_bcg", rows, ["600000"], {})
        self.assertIn(
            "native_defaults_used:porter_bcg.supplier_buyer_power",
            porter["results"][0]["data_gap"],
        )

    def test_unknown_nonsemantic_fields_cannot_make_screen_or_ai_available(self) -> None:
        value_rows = [
            _row("market", {"code": "600000", "pe_ttm": 10, "pb": 1}),
            _row("fundamental", {"foo": "bar"}),
        ]
        value = execute_native_method(
            "quantitative_screen",
            value_rows,
            ["600000"],
            {"method_assumptions": {"quantitative_screen": {"style": "value"}}},
        )
        self.assertEqual(value["quality_status"], "degraded")
        self.assertIn(
            "observation_missing:1_financials.operating_cash_flow_yi",
            value["results"][0]["data_gap"],
        )

        dash_value = execute_native_method(
            "quantitative_screen",
            [
                _row(
                    "market",
                    {
                        "code": "600000",
                        "pe_ttm": 10,
                        "pb": 1,
                        "dividend_yield_ttm": "—",
                    },
                ),
                _row(
                    "fundamental",
                    {
                        "operating_cash_flow_yi": "—",
                        "financial_health": {"debt_ratio": "—"},
                    },
                ),
            ],
            ["600000"],
            {"method_assumptions": {"quantitative_screen": {"style": "value"}}},
        )
        self.assertEqual(dash_value["quality_status"], "degraded")
        self.assertIn(
            "observation_missing:dividend_yield",
            dash_value["results"][0]["data_gap"],
        )

        quality_rows = [
            _row("fundamental", {"foo": "bar"}),
            _row("moat", {"foo": "bar"}),
        ]
        quality = execute_native_method(
            "quantitative_screen", quality_rows, ["600000"], {}
        )
        self.assertEqual(quality["quality_status"], "degraded")
        self.assertIn(
            "observation_missing:1_financials.roe_history_5y",
            quality["results"][0]["data_gap"],
        )

        snapshot_by_dimension = {
            "0_basic": "market",
            "1_financials": "fundamental",
            "5_chain": "chain",
            "7_industry": "industry",
            "11_governance": "governance",
            "13_policy": "policy",
            "14_moat": "moat",
            "15_events": "events",
            "17_sentiment": "sentiment",
            "18_trap": "trap",
        }
        ai_rows = [
            _row(snapshot_type, {"foo": "bar"})
            for snapshot_type in snapshot_by_dimension.values()
        ]
        ai = execute_native_method("ai_readiness", ai_rows, ["600000"], {})
        self.assertEqual(ai["quality_status"], "degraded")
        self.assertIn(
            "observation_missing:0_basic.market_cap",
            ai["results"][0]["data_gap"],
        )

        dash_history_rows = _complete_method_snapshots()
        financial = next(
            row
            for row in dash_history_rows
            if row["snapshot_type"] == "fundamental"
        )["facts"]["_uzi_raw_result"]["data"]
        financial["revenue_history"] = ["—", "—"]
        financial["net_profit_history"] = ["—", "—"]
        financial["roe_history"] = ["—"] * 5
        for method_id in (
            "earnings_review",
            "earnings_preview",
            "initiation_report",
            "ic_memo",
        ):
            with self.subTest(method_id=method_id):
                payload = execute_native_method(
                    method_id, dash_history_rows, ["600000"], {}
                )
                self.assertNotEqual(payload["quality_status"], "available")
                self.assertTrue(
                    any(
                        "1_financials" in gap
                        for gap in payload["results"][0]["data_gap"]
                    )
                )

    def test_method_gates_do_not_require_dimensions_the_pinned_function_does_not_use(self) -> None:
        rows = _complete_method_snapshots()
        cases = {
            "thesis_tracker": ({"valuation"}, "degraded"),
            "quantitative_screen": ({"market", "kline", "valuation"}, "available"),
            "sector_review": ({"market"}, "available"),
            "dd_checklist": ({"market"}, "degraded"),
        }
        for method_id, (removed_types, expected_status) in cases.items():
            with self.subTest(method_id=method_id):
                expected = execute_native_method(method_id, rows, ["600000"], {})
                reduced = execute_native_method(
                    method_id,
                    [row for row in rows if row["snapshot_type"] not in removed_types],
                    ["600000"],
                    {},
                )
                self.assertEqual(expected["quality_status"], expected_status)
                self.assertEqual(reduced["quality_status"], expected_status)
                self.assertEqual(
                    reduced["results"][0]["result"],
                    expected["results"][0]["result"],
                )

    def test_earnings_requires_real_or_explicit_consensus(self) -> None:
        rows = [
            row
            for row in _complete_method_snapshots()
            if row["snapshot_type"] != "research"
        ]
        missing = execute_native_method("earnings_review", rows, ["600000"], {})
        self.assertEqual(missing["quality_status"], "degraded")
        self.assertIsInstance(missing["results"][0]["result"], dict)
        self.assertEqual(
            missing["results"][0]["data_gap"],
            ["native_fallback_used:earnings_review.consensus"],
        )

        consensus = {"rev": 1350, "ni": 135}
        explicit = execute_native_method(
            "earnings_review",
            rows,
            ["600000"],
            {"method_assumptions": {"earnings_review": {"consensus": consensus}}},
        )
        self.assertEqual(explicit["quality_status"], "available")
        self.assertEqual(explicit["results"][0]["result"]["consensus"], consensus)

        malformed = execute_native_method(
            "earnings_review",
            rows,
            ["600000"],
            {"method_assumptions": {"earnings_review": {"consensus": {}}}},
        )
        self.assertEqual(
            malformed["results"][0]["data_gap"],
            [
                "explicit_assumption_missing:consensus.rev",
                "explicit_assumption_missing:consensus.ni",
            ],
        )

        wrong_types = execute_native_method(
            "earnings_review",
            rows,
            ["600000"],
            {
                "method_assumptions": {
                    "earnings_review": {"consensus": {"rev": "1350", "ni": "135"}}
                }
            },
        )
        self.assertEqual(
            wrong_types["results"][0]["data_gap"],
            [
                "explicit_assumption_invalid:consensus.rev",
                "explicit_assumption_invalid:consensus.ni",
            ],
        )
        wrong_shape = execute_native_method(
            "earnings_review",
            rows,
            ["600000"],
            {"method_assumptions": {"earnings_review": {"consensus": []}}},
        )
        self.assertEqual(
            wrong_shape["results"][0]["data_gap"],
            ["explicit_assumption_invalid:consensus"],
        )

        placeholder_rows = _complete_method_snapshots()
        research = next(
            row for row in placeholder_rows if row["snapshot_type"] == "research"
        )
        research["facts"]["_uzi_raw_result"]["data"]["consensus_rev_yi"] = "—"
        placeholder = execute_native_method(
            "earnings_review", placeholder_rows, ["600000"], {}
        )
        self.assertEqual(
            placeholder["results"][0]["data_gap"],
            ["native_fallback_used:earnings_review.consensus"],
        )

    def test_portfolio_defaults_and_missing_returns_are_preserved_but_degraded(self) -> None:
        holdings = [{"ticker": "600000.SH", "weight": 1.0, "return_pct": None}]
        returns = execute_native_method("returns_attribution", [], [], {"portfolio_holdings": holdings})
        self.assertEqual(returns["quality_status"], "degraded")
        self.assertIsInstance(returns["results"][0]["result"], dict)
        self.assertIn("native_warning:missing_return:600000.SH", returns["data_gap"])
        rebalance = execute_native_method("holding_rebalance", [], [], {"portfolio_holdings": holdings})
        self.assertEqual(rebalance["quality_status"], "degraded")
        self.assertIsInstance(rebalance["results"][0]["result"], dict)
        self.assertIn("native_defaults_used:holding_rebalance.equal_weight", rebalance["data_gap"])
        self.assertIn("native_defaults_used:holding_rebalance.fee_schedule", rebalance["data_gap"])


if __name__ == "__main__":
    unittest.main()
