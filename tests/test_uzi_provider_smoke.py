from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.runtime.diagnostics.status.uzi_provider_smoke import (
    UZI_FETCHER_DIMENSIONS,
    run_uzi_provider_smoke,
)
from research_system.runtime.platform.settings import Settings


class UziProviderSmokeTests(unittest.TestCase):
    def test_all_22_dimensions_are_audited_without_raw_provider_errors(self) -> None:
        def collector(**kwargs):
            self.assertEqual(len(kwargs["snapshot_types"]), 22)
            rows = []
            for index, snapshot_type in enumerate(kwargs["snapshot_types"]):
                if snapshot_type == "contests":
                    rows.append(
                        {
                            "snapshot_type": snapshot_type,
                            "facts": {
                                "availability": "data_gap",
                                "data_gap": ["sensitive_source_disabled:xueqiu"],
                            },
                            "degraded": True,
                            "degrade_reason": "sensitive_source_disabled",
                            "source": {"provider_id": "xueqiu_contests", "source_id": "xueqiu"},
                        }
                    )
                elif index == 4:
                    rows.append(
                        {
                            "snapshot_type": snapshot_type,
                            "facts": {"availability": "data_gap", "data_gap": ["peer_group_missing"]},
                            "degraded": True,
                            "degrade_reason": "provider_failure:RemoteDisconnected",
                            "source_trace": [
                                {
                                    "status": "provider_unavailable",
                                    "error_type": "RemoteDisconnected",
                                    "error_message": "secret raw provider response",
                                }
                            ],
                        }
                    )
                else:
                    rows.append(
                        {
                            "snapshot_type": snapshot_type,
                            "facts": {"availability": "available", "private_observation": "not emitted"},
                            "source": {
                                "skill": f"{snapshot_type}_skill",
                                "provider_id": f"provider.{snapshot_type}",
                                "source_id": "public_provider",
                            },
                        }
                    )
            return rows

        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            report = run_uzi_provider_smoke(settings, collector=collector)

        self.assertEqual(len(UZI_FETCHER_DIMENSIONS), 22)
        self.assertEqual(report["requested_count"], 22)
        self.assertEqual(report["completed_count"], 22)
        self.assertEqual(report["contract_status"], "passed")
        self.assertEqual(report["status_counts"], {"available": 20, "data_gap": 1, "disabled": 1})
        serialized = str(report)
        self.assertNotIn("secret raw provider response", serialized)
        self.assertNotIn("private_observation", serialized)
        self.assertIn("RemoteDisconnected", serialized)

    def test_dimension_without_lineage_fails_contract(self) -> None:
        def collector(**_kwargs):
            return [
                {
                    "snapshot_type": "market",
                    "facts": {"availability": "available"},
                }
            ]

        with tempfile.TemporaryDirectory() as temp_dir:
            report = run_uzi_provider_smoke(
                Settings.from_env(Path(temp_dir)),
                dimensions=["0_basic"],
                collector=collector,
            )

        self.assertEqual(report["contract_status"], "failed")
        self.assertEqual(report["contract_failures"], ["0_basic"])

    def test_unknown_dimension_is_rejected_before_provider_access(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            with self.assertRaisesRegex(ValueError, "unknown UZI dimensions"):
                run_uzi_provider_smoke(settings, dimensions=["made_up"])

    def test_parallel_smoke_is_rejected_before_unsafe_provider_initialization(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            with self.assertRaisesRegex(ValueError, "must run serially"):
                run_uzi_provider_smoke(settings, max_workers=2)


if __name__ == "__main__":
    unittest.main()
