from __future__ import annotations

import unittest
from pathlib import Path
from threading import Event
from time import monotonic
from unittest.mock import patch

from domain_packs.finance_ashare.skills.capital_flow_snapshot.skill import (
    CapitalFlowSnapshotSkill,
)
from domain_packs.finance_ashare.skills.market_snapshot.skill import MarketSnapshotSkill
from domain_packs.finance_ashare.tools import snapshot_collector
from research_system.adapters.vendors.provider_execution import (
    classify_provider_exception,
    execute_provider_chain,
)
from research_system.adapters.vendors.provider_registry import (
    ProviderSpec,
    VendorProviderRegistry,
)
from research_system.runtime.platform.settings import Settings


class _FailingMarketAdapter:
    def get_market_snapshot(self, **_kwargs):
        raise ConnectionError("upstream connection aborted with secret transport detail")

    def get_market_overview(self, **_kwargs):
        raise AssertionError("overview must not run after history failure")


class _FallbackMarketAdapter:
    def get_market_snapshot(self, **_kwargs):
        return {"bars": [{"date": "2026-07-15", "close": "12.5"}], "latest_bar": {"close": "12.5"}}

    def get_market_overview(self, **_kwargs):
        return {"latest_price": 12.5}


class _FallbackCapitalFlowAdapter:
    def get_capital_flow_snapshot(self, **_kwargs):
        return {
            "latest_flow": {"date": "2026-08-21", "main_net_inflow": 1.0},
            "recent_flows": [{"date": "2026-08-21", "main_net_inflow": 1.0}],
        }


class FinanceProviderExecutionTests(unittest.TestCase):
    def _registry(self, *, tushare_token: str | None = None) -> VendorProviderRegistry:
        settings = Settings.from_env(Path(__file__).resolve().parents[1]).model_copy(
            update={"tushare_token": tushare_token}
        )
        return VendorProviderRegistry(settings)

    def test_market_skill_uses_baostock_after_primary_failure(self) -> None:
        skill = MarketSnapshotSkill(self._registry())
        skill.adapters = {
            "akshare_market": _FailingMarketAdapter(),
            "baostock_market": _FallbackMarketAdapter(),
        }

        snapshot = skill.run(query="分析 600000", symbol="600000")

        self.assertEqual(snapshot["facts"]["availability"], "available")
        self.assertEqual(snapshot["source"]["provider_id"], "baostock_market")
        self.assertTrue(snapshot["fallback_applied"])
        self.assertEqual(
            [row["provider_id"] for row in snapshot["source_trace"]],
            ["akshare_market", "efinance_market", "baostock_market"],
        )
        self.assertEqual(
            [row["status"] for row in snapshot["source_trace"]],
            ["provider_unavailable", "provider_unavailable", "available"],
        )
        self.assertNotIn("secret transport detail", str(snapshot))

    def test_keyed_provider_is_only_in_chain_when_credential_exists(self) -> None:
        without_key = self._registry().get_provider_chain("fundamental", "cn_equity")
        with_key = self._registry(tushare_token="test-token").get_provider_chain("fundamental", "cn_equity")

        self.assertNotIn("tushare_fundamental", [item.provider_id for item in without_key])
        self.assertIn("tushare_fundamental", [item.provider_id for item in with_key])

    def test_capital_flow_uses_efinance_before_tushare_after_akshare_failure(self) -> None:
        registry = self._registry(tushare_token="configured")
        self.assertEqual(
            [item.provider_id for item in registry.get_provider_chain("capital_flow", "cn_equity")],
            ["akshare_capital_flow", "efinance_capital_flow", "tushare_capital_flow"],
        )
        skill = CapitalFlowSnapshotSkill(registry)
        skill.adapters = {
            "akshare_capital_flow": _FailingMarketAdapter(),
            "efinance_capital_flow": _FallbackCapitalFlowAdapter(),
        }

        snapshot = skill.run(query="分析 600519", symbol="600519")

        self.assertEqual(snapshot["facts"]["availability"], "available")
        self.assertEqual(snapshot["source"]["provider_id"], "efinance_capital_flow")
        self.assertTrue(snapshot["fallback_applied"])
        self.assertEqual(
            [row["status"] for row in snapshot["source_trace"][:2]],
            ["provider_unavailable", "available"],
        )

    def test_provider_override_cannot_activate_disabled_or_uncredentialed_adapters(self) -> None:
        registry = self._registry()
        registry.settings.provider_policy_overrides["market"] = "efinance_market,baostock_market"
        registry.settings.provider_policy_overrides["fundamental"] = "tushare_fundamental"

        self.assertEqual(
            [item.provider_id for item in registry.get_provider_chain("market", "cn_equity")],
            ["efinance_market", "baostock_market"],
        )
        self.assertEqual(
            [item.provider_id for item in registry.get_provider_chain("fundamental", "cn_equity")],
            ["akshare_fundamental", "baostock_fundamental"],
        )

    def test_failure_taxonomy_distinguishes_no_data_rate_limit_and_parse_failure(self) -> None:
        self.assertEqual(classify_provider_exception(ValueError("no data returned")), "no_data")
        self.assertEqual(classify_provider_exception(RuntimeError("HTTP 429 rate limit")), "rate_limited")
        self.assertEqual(classify_provider_exception(ValueError("invalid json parse response")), "parse_failed")

    def test_provider_failure_payload_never_exposes_raw_exception_text(self) -> None:
        provider = ProviderSpec(
            provider_id="test_provider",
            source_id="test",
            vendor_name="test",
            markets=("cn_equity",),
            dims=("market",),
            access="http",
        )
        result = execute_provider_chain(
            providers=[provider],
            loaders={"test_provider": lambda: (_ for _ in ()).throw(ConnectionError("private upstream detail"))},
        )

        self.assertEqual(result.facts["availability"], "provider_unavailable")
        self.assertNotIn("private upstream detail", str(result.facts))

    def test_snapshot_collector_injects_provider_registry_into_pack_skills(self) -> None:
        seen: list[VendorProviderRegistry | None] = []

        class RegistryAwareSkill:
            def __init__(self, provider_registry=None) -> None:
                seen.append(provider_registry)

            def run(self, *, query: str, symbol: str):
                return {
                    "snapshot_type": "market",
                    "symbol": symbol,
                    "facts": {"availability": "available", "price": 1},
                }

        registry = self._registry()
        with patch.dict(snapshot_collector._SYMBOL_SKILLS, {"market": RegistryAwareSkill}, clear=True):
            snapshot_collector.collect_snapshots(
                query="分析 600000",
                symbols=["600000"],
                snapshot_types=["market"],
                provider_registry=registry,
                max_workers=1,
            )

        self.assertEqual(seen, [registry])

    def test_snapshot_wave_timeout_becomes_a_non_cached_data_gap(self) -> None:
        release = Event()

        def blocked_snapshot(*_args, **_kwargs):
            release.wait(timeout=1)
            return {
                "snapshot_type": "notice",
                "symbol": "600000",
                "facts": {"availability": "available"},
            }

        try:
            with (
                patch.object(snapshot_collector, "_run_snapshot_skill", side_effect=blocked_snapshot),
                patch.object(snapshot_collector, "_UZI_FETCHER_WAVE_TIMEOUT_SECONDS", 0.01),
            ):
                rows = snapshot_collector.collect_snapshots(
                    query="分析 600000",
                    symbols=["600000"],
                    snapshot_types=["notice"],
                    max_workers=1,
                )
        finally:
            release.set()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["quality_status"], "data_gap")
        self.assertTrue(rows[0]["facts"]["_uzi_raw_result"]["_timeout"])
        self.assertIn("timeout", rows[0]["degrade_reason"])

    def test_uzi_market_recovery_fills_missing_fields_without_mutating_native_result(self) -> None:
        upstream = {
            "ticker": "600519.SH",
            "data": {
                "name": "贵州茅台",
                "industry": "白酒",
                "price": None,
                "market_cap_raw": "1.78万亿",
            },
            "source": "akshare:A",
            "fallback": True,
        }
        recovery = {
            "facts": {
                "availability": "available",
                "market_overview": {
                    "security_name": "贵州茅台",
                    "industry": "白酒",
                    "latest_price": 1418.2,
                    "market_cap": 1_780_000_000_000,
                    "pb": 7.1,
                    "pe_dynamic": 21.8,
                    "spot": {"今开": 1400.0},
                },
                "market_hist": {
                    "latest_bar": {"close": 1418.2, "high": 1420.0, "low": 1390.0}
                },
            },
            "source": {
                "provider_id": "efinance_market",
                "source_id": "efinance",
                "vendor": "efinance",
            },
            "source_trace": [
                {
                    "provider_id": "efinance_market",
                    "source_id": "efinance",
                    "status": "available",
                }
            ],
        }

        with (
            patch.object(snapshot_collector.Uzi392FetcherRuntime, "collect", return_value=upstream),
            patch.object(snapshot_collector.MarketSnapshotSkill, "run", return_value=recovery),
        ):
            result = snapshot_collector._run_uzi_fetcher_snapshot(
                query="分析贵州茅台",
                symbol="600519",
                snapshot_type="market",
                provider_registry=self._registry(),
            )

        self.assertEqual(result["facts"]["_uzi_raw_result"], upstream)
        self.assertIsNone(upstream["data"]["price"])
        self.assertEqual(result["facts"]["price"], 1418.2)
        self.assertEqual(result["facts"]["market_overview"]["latest_price"], 1418.2)
        self.assertEqual(result["field_sources"]["price"], "efinance_market")
        self.assertEqual(result["quality_status"], "available")

    def test_uzi_valuation_timeout_recovers_current_multiples_but_marks_history_gap(self) -> None:
        upstream = {
            "ticker": "600519.SH",
            "data": {},
            "source": "akshare:valuation",
            "fallback": True,
        }
        dependencies = {
            "0_basic": {
                "source": "efinance",
                "data": {
                    "price": 1418.2,
                    "pe_dynamic": 21.8,
                    "pb": 7.1,
                    "market_cap_raw": 1_780_000_000_000,
                    "industry": "白酒",
                },
            }
        }

        with patch.object(snapshot_collector.Uzi392FetcherRuntime, "collect", return_value=upstream):
            result = snapshot_collector._run_uzi_fetcher_snapshot(
                query="分析贵州茅台",
                symbol="600519",
                snapshot_type="valuation",
                provider_registry=self._registry(),
                dependencies=dependencies,
            )

        self.assertEqual(result["facts"]["_uzi_raw_result"], upstream)
        self.assertEqual(result["facts"]["pe"], "21.8")
        self.assertEqual(result["facts"]["pe_basis"], "dynamic")
        self.assertEqual(result["field_sources"]["pe"], "dependency.0_basic")
        self.assertEqual(result["quality_status"], "degraded")
        self.assertIn("valuation.pe_history", result["facts"]["data_gap"])

    def test_uzi_lhb_recovery_uses_event_count_not_seat_row_count(self) -> None:
        upstream = {
            "ticker": "600519.SH",
            "data": {"lhb_count_30d": 0, "lhb_records": [], "matched_youzi": []},
            "source": "akshare:native_lhb",
            "fallback": False,
        }
        recovery = {
            "facts": {
                "availability": "available",
                "buy_detail": [{"营业部名称": "A"}, {"营业部名称": "B"}],
                "sell_detail": [{"营业部名称": "C"}],
                "lhb_statistics": [{"上榜次数": 1}],
                "lhb_count_30d": 1,
                "latest_lhb_date": "20260821",
                "observation_status": "verified_non_empty",
                "observation_window_days": 30,
            },
            "source": {"provider_id": "akshare_lhb", "source_id": "em_data"},
            "source_trace": [
                {"provider_id": "akshare_lhb", "source_id": "em_data", "status": "available"}
            ],
        }

        with (
            patch.object(snapshot_collector.Uzi392FetcherRuntime, "collect", return_value=upstream),
            patch.object(snapshot_collector.LHBSnapshotSkill, "run", return_value=recovery),
        ):
            result = snapshot_collector._run_uzi_fetcher_snapshot(
                query="分析贵州茅台",
                symbol="600519",
                snapshot_type="lhb",
                provider_registry=self._registry(),
            )

        self.assertEqual(result["facts"]["_uzi_raw_result"], upstream)
        self.assertEqual(result["facts"]["lhb_count_30d"], 1)
        self.assertEqual(len(result["facts"]["lhb_records"]), 3)
        self.assertEqual(result["quality_status"], "available")

    def test_uzi_governance_recovery_can_verify_a_true_empty_observation(self) -> None:
        upstream = {
            "ticker": "600519.SH",
            "data": {
                "pledge": [],
                "insider_trades_1y": [],
                "qualitative_search": ["600519 股权质押"],
            },
            "source": "akshare:native_governance",
            "fallback": False,
        }
        recovery = {
            "facts": {
                "availability": "available",
                "pledge": [],
                "insider_trades_1y": [],
                "observation_status": "verified_empty",
                "source_coverage": {
                    "pledge": "available",
                    "insider_trades_1y": "available",
                },
            },
            "source": {"provider_id": "uzi_governance_multi", "source_id": "em_data"},
            "source_trace": [
                {"provider_id": "uzi_governance_multi", "source_id": "em_data", "status": "available"}
            ],
        }

        with (
            patch.object(snapshot_collector.Uzi392FetcherRuntime, "collect", return_value=upstream),
            patch.object(snapshot_collector.GovernanceSnapshotSkill, "run", return_value=recovery),
        ):
            result = snapshot_collector._run_uzi_fetcher_snapshot(
                query="分析贵州茅台",
                symbol="600519",
                snapshot_type="governance",
                provider_registry=self._registry(),
            )

        self.assertEqual(result["facts"]["_uzi_raw_result"], upstream)
        self.assertEqual(result["facts"]["observation_status"], "verified_empty")
        self.assertEqual(result["quality_status"], "available")

    def test_empty_native_observations_are_not_reported_as_available(self) -> None:
        cases = {
            "governance": {
                "pledge": [],
                "insider_trades_1y": [],
                "qualitative_search": ["query only"],
            },
            "lhb": {"lhb_count_30d": 0, "lhb_records": []},
            "peers": {"peer_table": [{"code": "600519", "is_self": True}]},
            "materials": {"core_material": "高粱"},
        }
        for snapshot_type, data in cases.items():
            with self.subTest(snapshot_type=snapshot_type):
                snapshot = {
                    "snapshot_type": snapshot_type,
                    "facts": {
                        "availability": "available",
                        "_uzi_raw_result": {"data": data},
                    },
                    "quality_status": "available",
                    "degraded": False,
                    "missing_fields": [],
                }
                result = snapshot_collector._apply_dimension_quality_contract(snapshot)
                self.assertEqual(result["quality_status"], "degraded")
                self.assertTrue(result["facts"]["data_gap"])
                if snapshot_type in {"governance", "lhb"}:
                    self.assertEqual(result["facts"]["observation_status"], "unverified_empty")

        verified_lhb = snapshot_collector._apply_dimension_quality_contract(
            {
                "snapshot_type": "lhb",
                "facts": {
                    "availability": "available",
                    "_uzi_raw_result": {
                        "data": {
                            "lhb_count_30d": 0,
                            "lhb_records": [],
                            "observation_status": "verified_empty",
                            "observation_window_days": 30,
                        }
                    },
                },
                "quality_status": "available",
                "degraded": False,
                "missing_fields": [],
            }
        )
        self.assertEqual(verified_lhb["quality_status"], "available")

        verified_governance = snapshot_collector._apply_dimension_quality_contract(
            {
                "snapshot_type": "governance",
                "facts": {
                    "availability": "available",
                    "_uzi_raw_result": {
                        "data": {
                            "pledge": [],
                            "insider_trades_1y": [],
                            "observation_status": "verified_empty",
                            "source_coverage": {
                                "pledge": "available",
                                "insider_trades_1y": "available",
                            },
                        }
                    },
                },
                "quality_status": "available",
                "degraded": False,
                "missing_fields": [],
            }
        )
        self.assertEqual(verified_governance["quality_status"], "available")

    def test_uzi_serial_timeout_does_not_queue_the_next_locked_dimensions(self) -> None:
        release = Event()
        calls: list[str] = []

        def blocked_snapshot(_query, _symbol, snapshot_type, *_args, **_kwargs):
            calls.append(snapshot_type)
            release.wait(timeout=1)
            return {"snapshot_type": snapshot_type, "facts": {"availability": "available"}}

        def timeout_snapshot(**kwargs):
            snapshot_type = kwargs["snapshot_type"]
            return {
                "snapshot_type": snapshot_type,
                "symbol": kwargs["symbol"],
                "facts": {"availability": "data_gap", "_uzi_raw_result": {"data": {}}},
                "quality_status": "data_gap",
                "degraded": True,
                "missing_fields": [snapshot_type],
            }

        snapshot_collector._UZI_MINI_RACER_TIMEOUT_ACTIVE.clear()
        started = monotonic()
        try:
            with (
                patch.object(snapshot_collector, "initialize_akshare_js_runtime"),
                patch.object(snapshot_collector, "_run_snapshot_skill", side_effect=blocked_snapshot),
                patch.object(snapshot_collector, "_fetcher_timeout_snapshot", side_effect=timeout_snapshot) as timeout,
                patch.object(
                    snapshot_collector,
                    "_scoped_capital_flow_recovery_snapshot",
                    side_effect=lambda **kwargs: timeout_snapshot(
                        snapshot_type="capital_flow",
                        **kwargs,
                    ),
                ),
                patch.object(snapshot_collector, "_UZI_SERIAL_FETCHER_TIMEOUT_SECONDS", 0.01),
            ):
                rows = snapshot_collector.collect_snapshots(
                    query="分析 600519",
                    symbols=["600519"],
                    snapshot_types=["industry", "valuation", "capital_flow"],
                    max_workers=3,
                )
        finally:
            release.set()
            snapshot_collector._UZI_MINI_RACER_TIMEOUT_ACTIVE.clear()

        self.assertLess(monotonic() - started, 0.3)
        self.assertEqual(calls, ["industry"])
        self.assertEqual(timeout.call_count, 2)
        self.assertEqual(len(rows), 3)


if __name__ == "__main__":
    unittest.main()
