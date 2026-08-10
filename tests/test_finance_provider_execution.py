from __future__ import annotations

import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
