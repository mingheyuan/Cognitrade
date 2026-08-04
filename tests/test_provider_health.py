from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_system.runtime.diagnostics import run_doctor
from research_system.runtime.diagnostics.status import ProviderHealthService
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout
from research_system.adapters.vendors.provider_registry import VendorProviderRegistry


class ProviderHealthTests(unittest.TestCase):
    def test_provider_health_reports_fallback_chains(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            with patch(
                "research_system.runtime.diagnostics.status.provider_health.NetworkProfile.probe_defaults",
                return_value={"domestic_reachable": True, "overseas_reachable": False, "domestic": [], "overseas": []},
            ):
                payload = ProviderHealthService(settings).build_report()

        rows = {item["dim"]: item for item in payload["provider_rows"]}
        self.assertGreaterEqual(rows["market"]["provider_count"], 2)
        self.assertTrue(rows["market"]["has_fallback"])
        self.assertEqual(rows["notice"]["provider_count"], 1)
        self.assertFalse(rows["notice"]["has_fallback"])
        self.assertGreaterEqual(rows["notice"]["configured_provider_count"], 2)
        self.assertIn("sse_notice", rows["notice"]["disabled_provider_ids"])
        self.assertEqual(rows["contests"]["provider_count"], 0)
        self.assertEqual(rows["contests"]["configured_provider_count"], 1)
        self.assertEqual(rows["fund"]["provider_count"], 2)
        self.assertEqual(rows["fund"]["configured_provider_count"], 2)
        self.assertFalse(payload["sensitive_sources"]["enabled"])

    def test_sensitive_source_requires_switch_allowlist_and_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            root.joinpath(".env").write_text(
                "COGNITRADE_SENSITIVE_SOURCES_ENABLED=true\n"
                "COGNITRADE_SENSITIVE_SOURCE_ALLOWLIST=xueqiu\n"
                "COGNITRADE_XUEQIU_COOKIE=test\n",
                encoding="utf-8",
            )
            settings = Settings.from_env(root)
            chain = VendorProviderRegistry(settings).get_provider_chain("contests", "cn_equity")

        self.assertEqual([provider.provider_id for provider in chain], ["xueqiu_contests"])

    def test_doctor_checks_include_provider_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            payload = run_doctor(settings)

        check_names = {item["name"] for item in payload["checks"]}
        self.assertIn("provider_policy", check_names)
        self.assertIn("data_sources", check_names)


if __name__ == "__main__":
    unittest.main()
