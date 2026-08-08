from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_system.runtime.diagnostics.status.uzi_provider_smoke import (
    UZI_FETCHER_DIMENSIONS,
    main,
    run_uzi_provider_smoke,
    run_uzi_provider_smoke_isolated,
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

    def test_disabled_sensitive_source_observation_fails_contract(self) -> None:
        def collector(**_kwargs):
            return [
                {
                    "snapshot_type": "contests",
                    "facts": {
                        "availability": "available",
                        "_uzi_raw_result": {
                            "data": {
                                "xueqiu_cubes": [{"name": "must not be exposed"}],
                                "xueqiu_meta": {"source": "authenticated"},
                            }
                        },
                    },
                    "source": {"source_id": "uzi:19_contests"},
                }
            ]

        with tempfile.TemporaryDirectory() as temp_dir:
            report = run_uzi_provider_smoke(
                Settings.from_env(Path(temp_dir)),
                dimensions=["19_contests"],
                collector=collector,
            )

        self.assertEqual(report["contract_status"], "failed")
        self.assertEqual(report["contract_failures"], ["19_contests"])
        audit = report["dimensions"][0]["sensitive_source_audit"]
        self.assertFalse(audit["configured_enabled"])
        self.assertTrue(audit["observation_present"])
        self.assertNotIn("must not be exposed", str(report))

    def test_parallel_smoke_is_rejected_before_unsafe_provider_initialization(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            with self.assertRaisesRegex(ValueError, "must run serially"):
                run_uzi_provider_smoke(settings, max_workers=2)

    def test_cli_writes_redacted_json_evidence_file(self) -> None:
        report = {
            "contract_status": "passed",
            "dimensions": [{"fetcher_id": "0_basic", "status": "available"}],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "evidence" / "provider-smoke.json"
            with patch(
                "research_system.runtime.diagnostics.status.uzi_provider_smoke.run_uzi_provider_smoke",
                return_value=report,
            ):
                exit_code = main(["--dimensions", "0_basic", "--output", str(output)])

            self.assertEqual(exit_code, 0)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), report)

    def test_isolated_smoke_records_timeout_and_continues(self) -> None:
        def runner(**kwargs):
            if kwargs["fetcher_id"] == "0_basic":
                raise subprocess.TimeoutExpired(cmd=["provider-probe"], timeout=35)
            return {
                "dimensions": [
                    {
                        "fetcher_id": kwargs["fetcher_id"],
                        "snapshot_type": "fundamental",
                        "status": "available",
                        "degraded": False,
                        "degrade_reason": None,
                        "data_gap": [],
                        "source_refs": [{"source_id": "public_provider"}],
                        "lineage_ok": True,
                    }
                ]
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "provider-smoke.json"
            report = run_uzi_provider_smoke_isolated(
                Settings.from_env(Path(temp_dir)),
                dimensions=["0_basic", "1_financials"],
                output=output,
                runner=runner,
            )

            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), report)

        self.assertEqual(report["requested_count"], 2)
        self.assertEqual(report["completed_count"], 2)
        self.assertEqual(report["contract_status"], "passed")
        self.assertEqual(report["status_counts"], {"available": 1, "data_gap": 1})
        timeout_row = report["dimensions"][0]
        self.assertEqual(timeout_row["degrade_reason"], "provider_chain_timeout")
        self.assertEqual(timeout_row["source_refs"][0]["error_type"], "TimeoutExpired")


if __name__ == "__main__":
    unittest.main()
