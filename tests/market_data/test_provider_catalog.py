from __future__ import annotations

import unittest
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from research_system.adapters.vendors.provider_registry import ProviderSpec
from research_system.market_data.providers import MarketDataProviderCatalog
from research_system.runtime.platform.settings import Settings


@dataclass
class _Registry:
    providers: list[ProviderSpec]

    def list_all(self):
        return list(self.providers)

    def get(self, provider_id: str):
        return next((item for item in self.providers if item.provider_id == provider_id), None)


class ProviderCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.settings = Settings.from_env(Path(self.temp_dir.name))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_credentials_are_never_exposed(self) -> None:
        settings = self.settings.model_copy(
            update={"tushare_token": "top-secret-token", "mx_api_key": "top-secret-key"}
        )
        provider = ProviderSpec(
            provider_id="mx_api_fundamental",
            source_id="mx_api",
            vendor_name="mx",
            markets=("cn_equity",),
            dims=("fundamental",),
            access="api",
            requires_key=True,
        )
        catalog = MarketDataProviderCatalog(settings, registry=_Registry([provider]))

        payload = catalog.list()[0]

        self.assertTrue(payload["credential_configured"])
        self.assertNotIn("top-secret", repr(payload))

    def test_browser_provider_remains_disabled_without_explicit_policy(self) -> None:
        provider = ProviderSpec(
            provider_id="xueqiu_contests",
            source_id="xueqiu",
            vendor_name="xueqiu",
            markets=("cn_equity",),
            dims=("contests",),
            access="browser",
        )
        catalog = MarketDataProviderCatalog(self.settings, registry=_Registry([provider]))

        payload = catalog.test_configuration(provider.provider_id)

        self.assertFalse(payload["ok"])
        self.assertIn(payload["status"], {"credential_missing", "policy_disabled"})
        self.assertFalse(payload["network_test_performed"])

    def test_unknown_provider_is_a_stable_capability_error(self) -> None:
        catalog = MarketDataProviderCatalog(self.settings, registry=_Registry([]))

        payload = catalog.test_configuration("missing")

        self.assertEqual(payload["status"], "unknown_provider")
        self.assertFalse(payload["ok"])


if __name__ == "__main__":
    unittest.main()
