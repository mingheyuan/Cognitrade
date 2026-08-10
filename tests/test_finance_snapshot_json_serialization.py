from __future__ import annotations

from datetime import date, datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from domain_packs.finance_ashare.tools import financial_data_provider, snapshot_collector
from domain_packs.finance_ashare.vendor.uzi_3_9_2 import Uzi392FetcherRuntime


class FinanceSnapshotJsonSerializationTests(unittest.TestCase):
    def test_uzi_chain_dates_are_normalized_before_cache_and_artifact_output(self) -> None:
        upstream = {
            "ticker": "688256.SH",
            "data": {
                "main_business_raw": [
                    {"报告日期": date(2026, 6, 30), "项目": "产品"},
                ]
            },
            "source": "akshare:stock_zygc_em",
            "fallback": False,
        }

        with TemporaryDirectory() as temporary_directory:
            cache_root = Path(temporary_directory)
            with (
                patch.object(Uzi392FetcherRuntime, "collect", return_value=upstream),
                patch.object(snapshot_collector, "initialize_akshare_js_runtime"),
            ):
                rows = snapshot_collector.collect_snapshots(
                    query="分析寒武纪",
                    symbols=["688256"],
                    snapshot_types=["chain"],
                    cache_root=cache_root,
                    research_profile="medium",
                )

            report_date = rows[0]["facts"]["main_business_raw"][0]["报告日期"]
            raw_report_date = rows[0]["facts"]["_uzi_raw_result"]["data"][
                "main_business_raw"
            ][0]["报告日期"]
            self.assertEqual(report_date, "2026-06-30")
            self.assertEqual(raw_report_date, "2026-06-30")
            json.dumps(rows)

            cache_path = snapshot_collector._snapshot_cache_path(
                cache_root=cache_root,
                snapshot_type="chain",
                target="688256",
                profile="medium",
            )
            self.assertIsNotNone(cache_path)
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            self.assertEqual(
                cached["snapshot"]["facts"]["main_business_raw"][0]["报告日期"],
                "2026-06-30",
            )

    def test_financial_tool_normalizes_the_complete_provider_payload(self) -> None:
        payload = {
            "snapshots": [
                {
                    "snapshot_type": "chain",
                    "symbol": "688256",
                    "facts": {"报告日期": date(2026, 6, 30)},
                    "source": {"provider_id": "akshare"},
                }
            ],
            "symbols": ["688256"],
            "candidate_universe": [
                {"symbol": "688256", "as_of": datetime(2026, 6, 30, tzinfo=timezone.utc)}
            ],
            "collection": {},
        }
        context = SimpleNamespace(cancellation=None, cache_root=None)

        with patch.object(financial_data_provider, "collect_snapshot_bundle", return_value=payload):
            result = financial_data_provider.invoke({}, context)

        self.assertEqual(result["snapshots"][0]["facts"]["报告日期"], "2026-06-30")
        self.assertEqual(
            result["candidate_universe"][0]["as_of"],
            "2026-06-30T00:00:00+00:00",
        )
        json.dumps(result)


if __name__ == "__main__":
    unittest.main()
