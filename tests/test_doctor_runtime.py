from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_system.runtime.doctor import build_status, run_doctor
from research_system.runtime.settings import Settings, ensure_workspace_layout


class DoctorRuntimeTests(unittest.TestCase):
    def test_build_status_summarizes_recent_runs_and_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = Settings.from_env(root)
            ensure_workspace_layout(settings)
            run_a = settings.runs_root / "01B"
            run_b = settings.runs_root / "01A"
            run_a.mkdir(parents=True, exist_ok=True)
            run_b.mkdir(parents=True, exist_ok=True)
            (run_a / "summary.json").write_text('{"status":"completed","intent":"stock_check"}', encoding="utf-8")
            (run_b / "summary.json").write_text('{"status":"failed","intent":"review_thesis"}', encoding="utf-8")

            payload = build_status(settings)

            self.assertEqual(payload["total_runs"], 2)
            self.assertEqual(payload["status_counts"]["completed"], 1)
            self.assertEqual(payload["status_counts"]["failed"], 1)
            self.assertEqual(len(payload["recent_runs"]), 2)

    def test_run_doctor_exposes_router_and_provider_checks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            with patch(
                "research_system.runtime.provider_health.NetworkProfile.probe_defaults",
                return_value={"domestic_reachable": True, "overseas_reachable": False, "domestic": [], "overseas": []},
            ):
                payload = run_doctor(settings)

        check_names = {item["name"] for item in payload["checks"]}
        self.assertIn("router_runtime", check_names)
        self.assertIn("provider_chain_market", check_names)
        self.assertIn("runs_presence", check_names)


if __name__ == "__main__":
    unittest.main()
