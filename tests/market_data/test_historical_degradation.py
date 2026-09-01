from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from jsonschema import Draft202012Validator

from domain_packs.finance_ashare.subagents.runtime import _snapshot_artifact_content
from research_system.market_data.bundles import BundleMaterializer, BundlePublisher
from research_system.market_data.dimension_contracts import DEFAULT_DIMENSION_REGISTRY
from research_system.market_data.historical import reconstruct_historical_snapshot
from research_system.market_data.ingestion import SnapshotIngestionService
from research_system.market_data.report_inputs import DatabaseReportInputGateway
from research_system.market_data.repository import MarketDataRepository
from research_system.market_data.resolver import SnapshotResolver
from research_system.market_data.uzi_adapter import UziSnapshotAdapter
from research_system.strategies.database import StrategyDatabase


class HistoricalDegradationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = StrategyDatabase("sqlite://")
        self.database.create_schema()

    def tearDown(self) -> None:
        self.database.close()

    def test_disclosed_historical_gaps_form_a_generatable_degraded_bundle(self) -> None:
        as_of = datetime.now(timezone.utc).date() - timedelta(days=7)
        adapter = UziSnapshotAdapter()
        with self.database.session() as session:
            ingestion = SnapshotIngestionService(MarketDataRepository(session))
            for dimension in DEFAULT_DIMENSION_REGISTRY.dimension_ids:
                ingestion.ingest(
                    adapter.disclosed_gap(
                        symbol="600519.SH",
                        market="CN_EQUITY",
                        exchange_timezone="Asia/Shanghai",
                        dimension=dimension,
                        as_of_date=as_of,
                        reason_code="historical_source_current_only",
                        message="历史数据不可恢复，自动排除评分",
                    )
                )

        with self.database.session() as session:
            repository = MarketDataRepository(session)
            coverage = SnapshotResolver(repository).coverage(
                "600519.SH",
                "CN_EQUITY",
                as_of,
                exchange_timezone="Asia/Shanghai",
            )
            self.assertTrue(coverage.structurally_complete)
            self.assertFalse(coverage.report_ready)
            self.assertTrue(coverage.report_generatable)
            self.assertEqual(coverage.degraded_dimension_count, 22)
            bundle = BundlePublisher(repository).publish(
                symbol="600519.SH",
                market="CN_EQUITY",
                as_of_date=as_of,
                exchange_timezone="Asia/Shanghai",
            )
            materialized = BundleMaterializer(repository).materialize(bundle.id)
            self.assertEqual(len(materialized.snapshots), 22)
            self.assertTrue(
                all(
                    row["query_manifest"]["degradation"]["excluded_from_scoring"]
                    for row in materialized.snapshots
                )
            )
            projected = _snapshot_artifact_content(materialized.snapshots[0])
            schema = json.loads(
                (
                    Path(__file__).resolve().parents[2]
                    / "domain_packs/finance_ashare/artifact_schemas/snapshot.json"
                ).read_text(encoding="utf-8")
            )
            Draft202012Validator(schema).validate(projected)
            self.assertTrue(projected["scoring_policy"]["excluded"])
            self.assertEqual(
                projected["dimension_id"],
                materialized.snapshots[0]["dimension_id"],
            )

        manifest = DatabaseReportInputGateway(self.database).freeze_report_manifest(
            symbols=["600519.SH"],
            as_of_date=as_of,
            tenant_id="tenant",
            user_id="user",
            workflow_pins={"profile": "lite"},
            context={"report_kind": "single_stock"},
        )
        self.assertEqual(manifest["status"], "frozen")
        self.assertEqual(manifest["items"][0]["bundle"]["quality"], "degraded")
        self.assertEqual(
            manifest["items"][0]["bundle"]["degraded_dimension_count"],
            22,
        )

    def test_native_capital_flow_aliases_survive_historical_projection(self) -> None:
        reconstructed = reconstruct_historical_snapshot(
            {
                "snapshot_type": "capital_flow",
                "facts": {
                    "_uzi_raw_result": {
                        "data": {
                            "main_fund_flow_20d": [
                                {"日期": "2026-08-18", "主力净流入": 1}
                            ],
                            "margin_recent": [
                                {"日期": "2026-08-18", "融资余额": 2}
                            ],
                            "holder_count_history": [
                                {"股东户数统计截止日": "2026-06-30", "股东户数": 3}
                            ],
                            "institutional_history": {
                                "quarters": ["26Q2"],
                                "fund": [4],
                                "qfii": [5],
                                "shehui": [6],
                            },
                            "block_trades_recent": [
                                {"交易日期": "2026-08-18", "成交额": 7}
                            ],
                            "unlock_recent": [
                                {"解禁日期": "2026-08-19", "解禁市值": 8}
                            ],
                        }
                    }
                },
            },
            dimension="12_capital_flow",
            as_of_date=date(2026, 8, 20),
        )

        self.assertIsNotNone(reconstructed)
        data = reconstructed["facts"]["_uzi_raw_result"]["data"]
        self.assertEqual(len(data["money_flow"]), 1)
        self.assertEqual(len(data["margin"]), 1)
        self.assertEqual(len(data["shareholder_counts"]), 1)
        self.assertEqual(data["institutions"][0]["period"], "26Q2")
        self.assertEqual(len(data["events"]), 2)


if __name__ == "__main__":
    unittest.main()
