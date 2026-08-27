from __future__ import annotations

import unittest
from datetime import date, datetime, timezone

from research_system.market_data.contracts import EvidenceGrade, SnapshotStatus
from research_system.market_data.uzi_adapter import UziSnapshotAdapter


class UziSnapshotAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = UziSnapshotAdapter()

    @staticmethod
    def _snapshot(
        snapshot_type: str, data: dict, *, quality: str = "available"
    ) -> dict:
        return {
            "snapshot_type": snapshot_type,
            "symbol": "600519.SH",
            "retrieved_at": "2026-08-20T10:00:00+08:00",
            "snapshot_date": "2026-08-20",
            "quality_status": quality,
            "facts": {
                "availability": quality,
                "_uzi_raw_result": {"data": data, "source": "test"},
            },
            "source": {"provider_id": f"uzi.{snapshot_type}", "chain_position": 0},
        }

    def test_current_basic_snapshot_is_normalized_with_provenance(self) -> None:
        snapshot = self._snapshot(
            "market",
            {
                "code": "600519.SH",
                "industry": "白酒",
                "price": 100.0,
                "market_cap_raw": 1_000_000.0,
            },
        )

        result = self.adapter.adapt(
            snapshot,
            symbol="600519.SH",
            market="cn_equity",
            exchange_timezone="Asia/Shanghai",
            as_of_date=date(2026, 8, 20),
        )

        self.assertEqual(result.dimension, "0_basic")
        self.assertEqual(result.status, SnapshotStatus.AVAILABLE)
        self.assertEqual(result.payload["shares_outstanding"], 10_000.0)
        self.assertEqual(
            result.payload["_uzi_raw_result"],
            snapshot["facts"]["_uzi_raw_result"],
        )
        self.assertEqual(result.evidence[0].grade, EvidenceGrade.A)
        self.assertTrue(result.query_manifest["pit_safe"])

    def test_current_fetch_keeps_uzi_status_and_records_historical_audit_gap(self) -> None:
        snapshot = self._snapshot(
            "market",
            {
                "code": "600519.SH",
                "industry": "白酒",
                "price": 100.0,
                "market_cap_raw": 1_000_000.0,
            },
        )

        result = self.adapter.adapt(
            snapshot,
            symbol="600519.SH",
            market="cn_equity",
            exchange_timezone="Asia/Shanghai",
            as_of_date=date(2025, 8, 20),
        )

        self.assertEqual(result.status, SnapshotStatus.AVAILABLE)
        self.assertFalse(result.query_manifest["pit_safe"])
        self.assertFalse(result.validation["audit_passed"])
        self.assertEqual(result.evidence[-1].grade, EvidenceGrade.C)

    def test_degraded_native_result_can_pass_the_strict_adapter_contract(self) -> None:
        snapshot = self._snapshot(
            "market",
            {
                "code": "600519.SH",
                "industry": "白酒",
                "price": 100.0,
                "market_cap_raw": 1_000_000.0,
            },
            quality="degraded",
        )

        result = self.adapter.adapt(
            snapshot,
            symbol="600519.SH",
            market="cn_equity",
            exchange_timezone="Asia/Shanghai",
            as_of_date=date(2026, 8, 20),
        )

        self.assertEqual(result.status, SnapshotStatus.AVAILABLE)
        self.assertTrue(result.validation["native_available"])
        self.assertEqual(result.query_manifest["native_quality_status"], "degraded")

    def test_partial_native_result_is_usable_with_nonblocking_audit_gap(self) -> None:
        snapshot = self._snapshot(
            "market",
            {
                "code": "600519.SH",
                "price": 100.0,
                "market_cap_raw": 1_000_000.0,
            },
            quality="partial",
        )

        result = self.adapter.adapt(
            snapshot,
            symbol="600519.SH",
            market="cn_equity",
            exchange_timezone="Asia/Shanghai",
            as_of_date=date(2026, 8, 20),
        )

        self.assertEqual(result.status, SnapshotStatus.AVAILABLE)
        self.assertTrue(result.validation["native_available"])
        self.assertFalse(result.validation["audit_passed"])
        self.assertIn(
            "missing normalized field: industry",
            result.validation["semantic_errors"],
        )

    def test_explicit_provider_failure_is_not_promoted_by_raw_data(self) -> None:
        snapshot = self._snapshot(
            "market",
            {
                "code": "600519.SH",
                "industry": "白酒",
                "price": 100.0,
                "market_cap_raw": 1_000_000.0,
            },
            quality="provider_unavailable",
        )

        result = self.adapter.adapt(
            snapshot,
            symbol="600519.SH",
            market="cn_equity",
            exchange_timezone="Asia/Shanghai",
            as_of_date=date(2026, 8, 20),
        )

        self.assertEqual(result.status, SnapshotStatus.DATA_GAP)
        self.assertFalse(result.validation["native_available"])

    def test_uzi_display_kline_keeps_native_availability_and_audits_history(self) -> None:
        snapshot = self._snapshot(
            "kline",
            {"candles_60d": [{"date": "2026-08-20", "close": 100.0}] * 60},
        )

        result = self.adapter.adapt(
            snapshot,
            symbol="600519.SH",
            market="cn_equity",
            exchange_timezone="Asia/Shanghai",
            as_of_date=date(2026, 8, 20),
        )

        self.assertEqual(result.status, SnapshotStatus.AVAILABLE)
        self.assertFalse(result.validation["audit_passed"])
        self.assertIn("fewer than 260", " ".join(result.validation["semantic_errors"]))

    def test_recovered_facts_fill_only_empty_native_fields(self) -> None:
        snapshot = self._snapshot(
            "capital_flow",
            {
                "main_fund_flow_20d": [],
                "northbound": {"flow_history": [{"date": "2026-08-20"}]},
            },
        )
        snapshot["facts"]["recent_flows"] = [
            {"date": "2026-08-20", "main_net_inflow": 10}
        ]

        result = self.adapter.adapt(
            snapshot,
            symbol="600519.SH",
            market="cn_equity",
            exchange_timezone="Asia/Shanghai",
            as_of_date=date(2026, 8, 20),
        )

        self.assertEqual(
            result.payload["money_flow"],
            [{"date": "2026-08-20", "main_net_inflow": 10}],
        )
        self.assertEqual(result.payload["northbound"], [{"date": "2026-08-20"}])

    def test_uzi_futures_history_key_is_normalized(self) -> None:
        snapshot = self._snapshot(
            "futures",
            {
                "linked_contract": "沪铜",
                "price_history_60d": list(range(60)),
            },
        )

        result = self.adapter.adapt(
            snapshot,
            symbol="600519.SH",
            market="cn_equity",
            exchange_timezone="Asia/Shanghai",
            as_of_date=date(2026, 8, 20),
        )

        self.assertEqual(result.payload["prices"], list(range(60)))

    def test_live_nonempty_event_query_gets_bounded_query_proof(self) -> None:
        snapshot = self._snapshot(
            "events",
            {
                "events": [{"date": "2026-08-01", "title": "公告"}],
                "query_status": {"status": "success"},
                "truncation": {"limited": False},
            },
        )

        result = self.adapter.adapt(
            snapshot,
            symbol="600519.SH",
            market="cn_equity",
            exchange_timezone="Asia/Shanghai",
            as_of_date=date(2026, 8, 20),
        )

        proof = result.query_manifest["field_queries"]["events"]
        self.assertEqual(proof["result_count"], 1)
        self.assertEqual(
            proof["query_window"],
            {"start": "2026-02-21", "end": "2026-08-20"},
        )

    def test_naive_publication_time_uses_exchange_timezone(self) -> None:
        snapshot = self._snapshot(
            "sentiment",
            {
                "items": [
                    {
                        "title": "盘中新闻",
                        "url": "https://finance.example.test/news/1",
                        "publish_time": "2026-08-20 09:30:00",
                    }
                ],
                "sentiment_data_available": True,
            },
        )

        result = self.adapter.adapt(
            snapshot,
            symbol="600519.SH",
            market="cn_equity",
            exchange_timezone="Asia/Shanghai",
            as_of_date=date(2026, 8, 20),
        )

        self.assertEqual(
            result.evidence[0].published_at,
            datetime(2026, 8, 20, 1, 30, tzinfo=timezone.utc),
        )
        self.assertEqual(
            result.evidence[0].metadata["published_at_basis"],
            "source_local:Asia/Shanghai",
        )
        self.assertFalse(result.validation["semantic_errors"])

    def test_row_source_timezone_overrides_exchange_timezone(self) -> None:
        snapshot = self._snapshot(
            "sentiment",
            {
                "items": [
                    {
                        "title": "海外新闻",
                        "url": "https://finance.example.test/news/2",
                        "published_at": "2026-08-19 21:00:00",
                        "source_timezone": "America/New_York",
                    }
                ],
                "sentiment_data_available": True,
            },
        )

        result = self.adapter.adapt(
            snapshot,
            symbol="600519.SH",
            market="cn_equity",
            exchange_timezone="Asia/Shanghai",
            as_of_date=date(2026, 8, 20),
        )

        self.assertEqual(
            result.evidence[0].published_at,
            datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(
            result.evidence[0].metadata["published_at_basis"],
            "source_local:America/New_York",
        )

    def test_future_evidence_is_skipped_and_downgrades_snapshot(self) -> None:
        snapshot = self._snapshot(
            "sentiment",
            {
                "items": [
                    {
                        "title": "未来新闻",
                        "url": "https://finance.example.test/news/future",
                        "publish_time": "2026-08-20 10:30:00",
                    }
                ],
                "sentiment_data_available": True,
            },
        )

        result = self.adapter.adapt(
            snapshot,
            symbol="600519.SH",
            market="cn_equity",
            exchange_timezone="Asia/Shanghai",
            as_of_date=date(2026, 8, 20),
        )

        self.assertEqual(result.status, SnapshotStatus.AVAILABLE)
        self.assertFalse(result.validation["audit_passed"])
        self.assertEqual(len(result.evidence), 1)
        self.assertIsNone(result.evidence[0].source_url)
        self.assertTrue(
            any(
                "published_at" in error and "is after observed_at" in error
                for error in result.validation["semantic_errors"]
            )
        )

    def test_invalid_explicit_source_timezone_is_a_semantic_gap(self) -> None:
        snapshot = self._snapshot(
            "sentiment",
            {
                "items": [
                    {
                        "title": "来源时区异常",
                        "url": "https://finance.example.test/news/invalid-zone",
                        "date": "2026-08-20 09:00:00",
                        "timezone": "CST",
                    }
                ],
                "sentiment_data_available": True,
            },
        )

        result = self.adapter.adapt(
            snapshot,
            symbol="600519.SH",
            market="cn_equity",
            exchange_timezone="Asia/Shanghai",
            as_of_date=date(2026, 8, 20),
        )

        self.assertEqual(result.status, SnapshotStatus.AVAILABLE)
        self.assertFalse(result.validation["audit_passed"])
        self.assertTrue(
            any(
                "invalid source timezone: CST" in error
                for error in result.validation["semantic_errors"]
            )
        )

    def test_empty_snapshot_keeps_sanitized_provider_failure_diagnostics(self) -> None:
        snapshot = self._snapshot("capital_flow", {}, quality="data_gap")
        snapshot["source_trace"] = [
            {
                "provider_id": "efinance_capital_flow",
                "status": "provider_unavailable",
                "error_type": "ConnectionError",
            },
            {
                "provider_id": "tushare_capital_flow",
                "status": "provider_unavailable",
                "error_type": "PermissionDenied",
            },
        ]

        result = self.adapter.adapt(
            snapshot,
            symbol="600519.SH",
            market="cn_equity",
            exchange_timezone="Asia/Shanghai",
            as_of_date=date(2026, 8, 20),
        )

        self.assertIn(
            "provider efinance_capital_flow: provider_unavailable (ConnectionError)",
            result.validation["semantic_errors"],
        )
        self.assertIn(
            "provider tushare_capital_flow: provider_unavailable (PermissionDenied)",
            result.validation["semantic_errors"],
        )


if __name__ == "__main__":
    unittest.main()
