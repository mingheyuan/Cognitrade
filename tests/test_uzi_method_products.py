from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import get_args
import unittest
from unittest.mock import Mock, patch
import jsonschema
import yaml

from domain_packs.finance_ashare.subagents.uzi_method_products import (
    METHOD_ARTIFACT_TYPES,
    build_method_product,
)
from domain_packs.finance_ashare.subagents.uzi_tier1_methodology import (
    build_portfolio_rebalance,
)
from domain_packs.finance_ashare.subagents.uzi_panel_registry import PANEL_REGISTRY
from domain_packs.finance_ashare.skills.uzi_source_snapshot.skill import ContestsSnapshotSkill
from research_system.adapters.vendors.provider_registry import VendorProviderRegistry
from research_system.api.errors import ApiError
from research_system.api.validation import validate_research_request
from research_system.runtime.domain_packs.registry import DomainPackRegistry
from research_system.runtime.planning.capability_planner import CapabilityPlanner
from research_system.schemas.domain_pack import CapabilityPlanningRequest
from research_system.schemas.protocol import RequestEnvelope
from research_system.runtime.platform.settings import Settings
from research_system.schemas.snapshots import SnapshotType


def _rows() -> list[dict]:
    return [
        {
            "snapshot_type": "market",
            "symbol": "300001",
            "quality_status": "available",
            "facts": {"market_overview": {"name": "示例公司", "latest_price": 20}},
        },
        {
            "snapshot_type": "fundamental",
            "symbol": "300001",
            "quality_status": "available",
            "facts": {
                "financial_abstract": {
                    "revenue": 100,
                    "net_margin": 10,
                    "total_assets": 180,
                    "total_liabilities": 80,
                    "operating_cash_flow": 12,
                    "ebitda": 20,
                    "net_profit": 10,
                    "shares_outstanding": 5,
                    "segments": [
                        {"name": "产品 A", "revenue": 60, "gross_margin": 30},
                        {"name": "产品 B", "revenue": 40, "gross_margin": 20},
                    ],
                }
            },
        },
    ]


class UziMethodProductTests(unittest.TestCase):
    def test_all_independent_products_return_a_versioned_contract(self) -> None:
        schema_root = Path(__file__).resolve().parents[1] / "domain_packs/finance_ashare/artifact_schemas"
        for method_id, artifact_type in METHOD_ARTIFACT_TYPES.items():
            with self.subTest(method_id=method_id):
                product = build_method_product(method_id, _rows(), ["300001"])
                self.assertEqual(product["method"], method_id)
                self.assertEqual(product["artifact_type"], artifact_type)
                self.assertIn(product["quality_status"], {"available", "degraded", "assumption_only", "data_gap"})
                self.assertEqual(len(product["results"]), 1)
                schema = yaml.safe_load((schema_root / f"{artifact_type}.json").read_text(encoding="utf-8"))
                jsonschema.validate(product, schema)

    def test_three_statement_never_invents_projection_assumptions(self) -> None:
        missing = build_method_product("three_statement", _rows(), ["300001"])
        supplied = build_method_product(
            "three_statement",
            _rows(),
            ["300001"],
            {
                "method_assumptions": {
                    "three_statement": {
                        "revenue_growth_path": [0.10, 0.08],
                        "capex_to_revenue": 0.05,
                        "working_capital_to_incremental_revenue": 0.10,
                    }
                }
            },
        )

        self.assertEqual(missing["results"][0]["derived"]["projection"], [])
        self.assertIn("300001:revenue_growth_path_assumption_missing", missing["data_gap"])
        self.assertEqual(len(supplied["results"][0]["derived"]["projection"]), 2)
        self.assertIn("300001:projection_growth_path", supplied["assumption_flags"])

    def test_cashflow_resilience_uses_reported_facts_without_default_stress_values(self) -> None:
        product = build_method_product("cashflow_resilience", _rows(), ["300001"])
        result = product["results"][0]

        self.assertEqual(result["observed"]["operating_cash_flow"], 12)
        self.assertEqual(result["derived"]["operating_cash_flow_to_net_profit"], 1.2)
        self.assertEqual(result["derived"]["liabilities_to_assets"], 80 / 180)
        self.assertIsNone(result["methodology"]["composite_score"])
        self.assertIsNone(result["methodology"]["stress_scenario"])
        self.assertEqual(result["assumption_flags"], [])
        self.assertIn("300001:reported_free_cash_flow_missing", product["data_gap"])

    def test_quick_lbo_requires_reported_ebitda_and_every_transaction_input(self) -> None:
        missing = build_method_product("quick_lbo", _rows(), ["300001"])
        supplied = build_method_product(
            "quick_lbo",
            _rows(),
            ["300001"],
            {
                "method_assumptions": {
                    "quick_lbo": {
                        "entry_multiple": 8,
                        "debt_multiple": 4,
                        "exit_multiple": 8,
                        "annual_ebitda_growth": 0.08,
                        "annual_debt_paydown_pct": 0.10,
                        "hold_years": 5,
                    }
                }
            },
        )

        self.assertIn("300001:entry_multiple_assumption_missing", missing["data_gap"])
        self.assertIsNone(missing["results"][0]["derived"].get("irr"))
        self.assertIsNotNone(supplied["results"][0]["derived"]["irr"])

        invalid = build_method_product(
            "quick_lbo",
            _rows(),
            ["300001"],
            {
                "method_assumptions": {
                    "quick_lbo": {
                        "entry_multiple": 3,
                        "debt_multiple": 4,
                        "exit_multiple": 8,
                        "annual_ebitda_growth": 0.08,
                        "annual_debt_paydown_pct": 0.10,
                        "hold_years": 5,
                    }
                }
            },
        )
        self.assertIn("300001:entry_equity_must_be_positive", invalid["data_gap"])

    def test_segmental_model_uses_only_reported_rows(self) -> None:
        result = build_method_product("segmental", _rows(), ["300001"])
        segments = result["results"][0]["observed"]["segments"]

        self.assertEqual([row["revenue_share_pct"] for row in segments], [60.0, 40.0])
        self.assertEqual(result["assumption_flags"], [])

    def test_unit_economics_keeps_observations_separate_from_calculations(self) -> None:
        rows = _rows()
        rows[1]["facts"]["financial_abstract"].update(
            {"customer_count": 10, "gross_margin": 40, "churn_rate": 20, "cac": 2}
        )
        result = build_method_product("unit_economics", rows, ["300001"])["results"][0]

        self.assertNotIn("arpu", result["observed"])
        self.assertEqual(result["derived"]["unit_metrics"]["arpu"], 10)
        self.assertEqual(result["derived"]["unit_metrics"]["ltv_cac_ratio"], 10)

    def test_asset_class_rebalance_requires_explicit_classification_and_targets(self) -> None:
        holdings = [
            {"ticker": "600519", "weight": 60, "asset_class": "股票", "value": 600},
            {"ticker": "511010", "weight": 40, "asset_class": "债券", "value": 400},
        ]
        missing = build_portfolio_rebalance(holdings, None)
        supplied = build_portfolio_rebalance(holdings, {"股票": 50, "债券": 50})

        self.assertEqual(missing["quality_status"], "data_gap")
        self.assertIn("asset_class_targets_missing", missing["data_gap"])
        self.assertEqual(supplied["target_mode"], "explicit_asset_class_targets")
        self.assertEqual(len(supplied["asset_classes"]), 2)


class UziMethodProductIntegrationTests(unittest.TestCase):
    def test_migration_inventory_counts_and_targets_are_exact(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        ledger = yaml.safe_load(
            (repository_root / "domain_packs/finance_ashare/migration/uzi_3_9_2_capability_ledger.yaml").read_text(encoding="utf-8")
        )
        manifest = yaml.safe_load(
            (repository_root / "domain_packs/finance_ashare/domain.yaml").read_text(encoding="utf-8")
        )
        capabilities = {row["capability"] for row in manifest["capabilities"]}
        skills = {row["skill_name"] for row in manifest["skills"]}
        intents = {row["intent_id"] for row in manifest["intake"]["intents"]}

        self.assertEqual(len(ledger["fetchers"]), 22)
        self.assertEqual(len(ledger["commands"]), 20)
        self.assertEqual(len(ledger["methods"]), 23)
        self.assertEqual(len(PANEL_REGISTRY), 66)
        self.assertEqual(len({row["id"] for row in PANEL_REGISTRY}), 66)
        self.assertEqual({row["target"] for row in ledger["fetchers"]} - skills, set())
        for group in ("commands", "methods"):
            targets = {
                row["target"] for row in ledger[group]
                if str(row["target"]).startswith("finance.")
            }
            self.assertEqual(targets - capabilities, set())
        command_intents = {
            str(row["target"]).split("+", 1)[0]
            for row in ledger["commands"]
            if row.get("status") != "removed"
            and row.get("target")
            and not str(row["target"]).startswith("finance.")
        }
        self.assertEqual(command_intents - intents, set())
        self.assertEqual(len(ledger["upstream_3_9_2_regressions"]), 8)

    def test_every_declared_artifact_has_a_schema(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        pack_root = repository_root / "domain_packs/finance_ashare"
        manifest = yaml.safe_load((pack_root / "domain.yaml").read_text(encoding="utf-8"))
        declared = {row["artifact_type"] for row in manifest["artifact_types"]}
        schema_types = {path.stem for path in (pack_root / "artifact_schemas").glob("*.json")}

        self.assertEqual(schema_types, declared)

    def test_production_pack_has_no_external_uzi_runtime_boundary(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        pack_root = repository_root / "domain_packs/finance_ashare"
        forbidden = (
            "cloudflared", "full-report-standalone", "agent_analysis.json",
            "_tmp_uzi_skill", "codex-uzi-latest", "subprocess.run(", "subprocess.Popen(",
        )
        violations: list[str] = []
        for path in pack_root.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".yaml", ".json"}:
                continue
            relative_parts = path.relative_to(pack_root).parts
            if "migration" in relative_parts or "vendor" in relative_parts:
                continue
            content = path.read_text(encoding="utf-8")
            for marker in forbidden:
                if marker in content:
                    violations.append(f"{path.relative_to(repository_root)}:{marker}")

        self.assertEqual(violations, [])

    def test_legacy_methodology_runtime_is_deleted(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        legacy_files = [
            repository_root / "research_system/services/methodologies/__init__.py",
            repository_root / "research_system/services/methodologies/valuation/__init__.py",
            repository_root / "research_system/services/methodologies/valuation/comps_service.py",
            repository_root / "research_system/services/methodologies/valuation/dcf_service.py",
            repository_root / "research_system/services/methodologies/valuation/cashflow_resilience_service.py",
            repository_root / "research_system/schemas/valuation.py",
        ]

        self.assertEqual([path for path in legacy_files if path.exists()], [])
        runtime = (
            repository_root / "domain_packs/finance_ashare/subagents/runtime.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("research_system.services.methodologies", runtime)
        self.assertNotIn("finance.analysis.evaluate", runtime)

    def test_every_pack_snapshot_dimension_is_accepted_by_api_and_schema(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        manifest = yaml.safe_load(
            (repository_root / "domain_packs/finance_ashare/domain.yaml").read_text(encoding="utf-8")
        )
        dimensions = set(manifest["planner_policies"]["snapshot_dimensions"])
        expected = {
            "market", "fundamental", "notice", "sector", "lhb", "capital_flow",
            "sentiment", "events", "policy", "kline", "macro", "peers", "chain",
            "research", "industry", "materials", "futures", "valuation", "governance",
            "moat", "trap", "contests", "fund_holders", "similar_stocks",
        }

        self.assertEqual(dimensions, expected)
        self.assertEqual(set(get_args(SnapshotType)), expected)
        validated = validate_research_request(
            RequestEnvelope(
                intent="stock_check",
                user_query="读取全部已迁入数据维度",
                constraints={
                    "symbol": "300001",
                    "research_profile": "medium",
                    "snapshot_types": sorted(expected),
                },
            )
        )
        self.assertEqual(set(validated.constraints["snapshot_types"]), expected)

    def test_profiles_match_the_uzi_three_tier_output_contract(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        manifest = yaml.safe_load(
            (repository_root / "domain_packs/finance_ashare/domain.yaml").read_text(encoding="utf-8")
        )
        policies = manifest["planner_policies"]
        self.assertEqual(set(policies["research_profiles"]), {"lite", "medium", "deep"})
        self.assertNotIn("default_profile", policies)
        self.assertEqual(policies["research_profiles"]["lite"]["methods"], [])
        self.assertEqual(policies["profile_outputs"]["lite"], ["research_report"])
        self.assertEqual(policies["profile_outputs"]["medium"], ["research_report"])
        self.assertEqual(policies["profile_outputs"]["deep"], ["research_report"])

    def test_migration_ledger_has_no_planned_or_partial_capability(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        ledger = yaml.safe_load(
            (repository_root / "domain_packs/finance_ashare/migration/uzi_3_9_2_capability_ledger.yaml").read_text(encoding="utf-8")
        )
        rows = [*ledger["fetchers"], *ledger["commands"], *ledger["methods"]]
        unfinished = [
            f"{row['id']}:{row['status']}"
            for row in rows
            if "planned" in str(row.get("status")) or "partial" in str(row.get("status"))
        ]
        self.assertEqual(unfinished, [])

    def test_validation_accepts_explicit_method_and_asset_policy_inputs(self) -> None:
        stock = validate_research_request(
            RequestEnvelope(
                intent="uzi_three_statement_method",
                user_query="为 300001 建立三表",
                constraints={
                    "symbol": "300001",
                    "method_assumptions": {
                        "three_statement": {
                            "revenue_growth_y1": 0.10,
                            "revenue_growth_y2": 0.08,
                            "revenue_growth_y3": 0.07,
                            "revenue_growth_y4": 0.06,
                            "revenue_growth_y5": 0.05,
                            "gross_margin": 0.35,
                            "opex_pct_revenue": 0.18,
                            "tax_rate": 0.25,
                            "capex_pct_revenue": 0.05,
                            "dep_pct_revenue": 0.03,
                            "nwc_pct_revenue": 0.10,
                        }
                    },
                },
            )
        )
        portfolio = validate_research_request(
            RequestEnvelope(
                intent="uzi_portfolio_rebalance_method",
                user_query="资产配置再平衡",
                constraints={
                    "portfolio_holdings": [
                        {"ticker": "600519", "weight": 60, "asset_class": "股票"},
                        {"ticker": "511010", "weight": 40, "asset_class": "债券"},
                    ],
                    "asset_class_targets": {"股票": 50, "债券": 50},
                },
            )
        )

        self.assertIn("three_statement", stock.constraints["method_assumptions"])
        self.assertEqual(portfolio.constraints["asset_class_targets"]["股票"], 50)
        with self.assertRaises(ApiError):
            validate_research_request(
                RequestEnvelope(
                    intent="stock_check",
                    user_query="旧 requested_methods 入口不再受支持",
                    constraints={
                        "symbol": "300001",
                        "research_profile": "medium",
                        "requested_methods": ["three_statement"],
                    },
                )
            )
        with self.assertRaises(ApiError):
            validate_research_request(
                RequestEnvelope(
                    intent="uzi_lbo",
                    user_query="拒绝无效 LBO 假设",
                    constraints={
                        "symbol": "300001",
                        "method_assumptions": {
                            "quick_lbo": {
                                "entry_multiple": 8,
                                "debt_multiple": 4,
                                "exit_multiple": 8,
                                "annual_ebitda_growth": 0.08,
                                "annual_debt_paydown_pct": 1.5,
                                "hold_years": 5,
                            }
                        },
                    },
                )
            )

    def test_legacy_requested_method_catalog_is_not_active(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        manifest = yaml.safe_load(
            (repository_root / "domain_packs/finance_ashare/domain.yaml").read_text(encoding="utf-8")
        )
        policies = manifest["planner_policies"]
        self.assertNotIn("method_catalog", policies)
        self.assertNotIn("methodology_outputs", policies)
        self.assertNotIn("retired_method_catalog", policies)
        self.assertNotIn("retired_methodology_outputs", policies)
        self.assertNotIn("3.9.2-adapted", (repository_root / "domain_packs/finance_ashare/domain.yaml").read_text(encoding="utf-8"))

    def test_portfolio_method_adds_asset_class_artifact_to_intent_outputs(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        planner = CapabilityPlanner(DomainPackRegistry.load(repository_root / "domain_packs"))
        graph = planner.build_graph(
            CapabilityPlanningRequest(
                domain_id="finance_ashare",
                objective="运行战略资产配置再平衡",
                available_artifact_types=["user_request"],
                required_artifact_types=["uzi_native_method_result"],
                metadata={
                    "intent": "uzi_portfolio_rebalance_method",
                    "required_outputs_source": "intent_output_contract",
                    "intent_inputs": {
                        "intent": "uzi_portfolio_rebalance_method",
                        "portfolio_holdings": [{"ticker": "600519", "weight": 1, "asset_class": "股票"}],
                        "asset_class_targets": {"股票": 1},
                    },
                },
            )
        )

        self.assertIn("uzi_native_method_result", graph.metadata["required_artifact_types"])
        self.assertIn("finance.uzi.native.portfolio_rebalance", {node.capability for node in graph.nodes})

    def test_contests_source_is_disabled_by_default_and_uses_real_payload_when_enabled(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            disabled = Settings.from_env(root)
            with patch(
                "research_system.adapters.vendors.uzi_contest_adapter.UziContestAdapter._taoguba",
                return_value=[],
            ), patch(
                "research_system.adapters.vendors.uzi_contest_adapter.UziContestAdapter._ths",
                return_value=[],
            ), patch(
                "research_system.adapters.vendors.uzi_contest_adapter.UziContestAdapter._dpswang",
                return_value=[],
            ):
                disabled_result = ContestsSnapshotSkill(
                    provider_registry=VendorProviderRegistry(disabled)
                ).run(query="查看实盘组合", symbol="600519")
            root.joinpath(".env").write_text(
                "COGNITRADE_SENSITIVE_SOURCES_ENABLED=true\n"
                "COGNITRADE_SENSITIVE_SOURCE_ALLOWLIST=xueqiu\n"
                "COGNITRADE_XUEQIU_COOKIE=test\n",
                encoding="utf-8",
            )
            enabled = Settings.from_env(root)
            with patch(
                "research_system.adapters.vendors.uzi_contest_adapter.UziContestAdapter._xueqiu_http",
                return_value=[{"name": "真实接口组合", "symbol": "ZH001", "owner": "接口用户", "total_gain": 61.2}],
            ), patch(
                "research_system.adapters.vendors.uzi_contest_adapter.UziContestAdapter._taoguba",
                return_value=[],
            ), patch(
                "research_system.adapters.vendors.uzi_contest_adapter.UziContestAdapter._ths",
                return_value=[],
            ), patch(
                "research_system.adapters.vendors.uzi_contest_adapter.UziContestAdapter._dpswang",
                return_value=[],
            ):
                enabled_result = ContestsSnapshotSkill(
                    provider_registry=VendorProviderRegistry(enabled)
                ).run(query="查看实盘组合", symbol="600519")
            with patch(
                "research_system.adapters.vendors.uzi_contest_adapter.UziContestAdapter._xueqiu_http",
                side_effect=RuntimeError("private upstream response"),
            ), patch(
                "research_system.adapters.vendors.uzi_contest_adapter.UziContestAdapter._taoguba",
                return_value=[],
            ), patch(
                "research_system.adapters.vendors.uzi_contest_adapter.UziContestAdapter._ths",
                return_value=[],
            ), patch(
                "research_system.adapters.vendors.uzi_contest_adapter.UziContestAdapter._dpswang",
                return_value=[],
            ):
                failed_result = ContestsSnapshotSkill(
                    provider_registry=VendorProviderRegistry(enabled)
                ).run(query="查看实盘组合", symbol="600519")

        self.assertIn("sensitive_source_disabled:xueqiu", disabled_result["facts"]["data_gap"])
        self.assertEqual(enabled_result["facts"]["xueqiu_cubes"][0]["name"], "真实接口组合")
        self.assertEqual(enabled_result["source"]["provider_id"], "uzi_contests_multi")
        self.assertNotIn("test-only", str(enabled_result))
        self.assertNotIn("private upstream response", str(failed_result))
        self.assertIn("sensitive_source_request_failed:xueqiu", failed_result["facts"]["data_gap"])


if __name__ == "__main__":
    unittest.main()
