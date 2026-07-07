from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_system.runtime.diagnostics import run_doctor
from research_system.runtime.diagnostics.status import ProviderHealthService
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout


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
        self.assertGreaterEqual(rows["notice"]["provider_count"], 2)
        self.assertTrue(rows["notice"]["has_fallback"])

    def test_doctor_checks_include_dimension_aliases_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            payload = run_doctor(settings)

        check_names = {item["name"] for item in payload["checks"]}
        self.assertIn("taxonomy_dimension_aliases", check_names)


if __name__ == "__main__":
    unittest.main()
