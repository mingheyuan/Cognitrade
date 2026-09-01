from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select

from research_system.market_data.agent_recovery import recover_collection_job_draft
from research_system.market_data.collection import MarketDataCollectionCoordinator
from research_system.market_data.models import MarketDataSnapshot
from research_system.runtime.platform.settings import Settings
from research_system.strategies.database import StrategyDatabase


def _market_collector(**kwargs):
    now = datetime.now(timezone.utc)
    return [
        {
            "snapshot_type": "market",
            "symbol": symbol,
            "retrieved_at": now.isoformat(),
            "snapshot_date": now.date().isoformat(),
            "quality_status": "available",
            "facts": {
                "availability": "available",
                "_uzi_raw_result": {
                    "ticker": symbol,
                    "source": "test",
                    "data": {
                        "code": symbol,
                        "name": "测试公司",
                        "industry": "测试行业",
                        "price": 10.0,
                        "market_cap_raw": 1000.0,
                    },
                },
            },
            "source": {"provider_id": "test.market", "chain_position": 0},
        }
        for symbol in kwargs["symbols"]
        if "market" in kwargs["snapshot_types"]
    ]


def test_agent_recovery_appends_snapshot_without_mutating_original() -> None:
    database = StrategyDatabase("sqlite://")
    database.create_schema()
    coordinator = MarketDataCollectionCoordinator(database, collector=_market_collector)
    today = datetime.now(timezone.utc).date()
    try:
        job = coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.SH"],
            start_date=today,
            end_date=today,
            dimensions=["0_basic"],
            publish_when_complete=False,
            submit=False,
        )
        coordinator.run_job_now(job["id"])
        with database.session() as session:
            original = session.scalar(select(MarketDataSnapshot))
            original_id = original.id
            original_hash = original.content_hash

        def fake_recovery(snapshots, **_kwargs):
            market = next(row for row in snapshots if row["snapshot_type"] == "market")
            market["facts"]["agent_verified_name"] = "测试公司"
            market["quality_status"] = "degraded"
            market.setdefault("source_trace", []).append(
                {
                    "source_id": "agent_test",
                    "status": "available",
                    "url": "https://example.test/company",
                }
            )
            return {
                "attempted": 1,
                "succeeded": 1,
                "failed": 0,
                "integrity_before": {"ok": False},
                "integrity_after": {"ok": True},
                "strict_recovery": {"attempted": 1, "succeeded": 1},
                "qualitative_recovery": {"attempted": 0, "succeeded": 0},
                "playwright_recovery": {"attempted": 0, "succeeded": 0},
                "qualitative_agent_evidence": {"attempted": 0, "succeeded": 0},
            }

        with patch(
            "research_system.market_data.agent_recovery.recover_uzi_draft_snapshots",
            side_effect=fake_recovery,
        ):
            result = recover_collection_job_draft(
                database,
                job_id=job["id"],
                settings=Settings.from_env(Path(__file__).resolve().parents[2]),
            )

        assert result["persisted_snapshot_count"] == 1
        with database.session() as session:
            rows = list(
                session.scalars(
                    select(MarketDataSnapshot).where(
                        MarketDataSnapshot.dimension == "0_basic"
                    )
                )
            )
            assert len(rows) == 2
            original = next(row for row in rows if row.id == original_id)
            recovered = next(row for row in rows if row.id != original_id)
            assert original.content_hash == original_hash
            assert "agent_verified_name" not in original.payload_json["raw"]
            assert recovered.payload_json["raw"]["agent_verified_name"] == "测试公司"
            assert recovered.query_manifest_json["agent_recovery"]["job_id"] == job["id"]
    finally:
        coordinator.close()
        database.close()


def test_historical_agent_recovery_is_disclosed_noop_without_current_evidence() -> None:
    database = StrategyDatabase("sqlite://")
    database.create_schema()
    coordinator = MarketDataCollectionCoordinator(database, collector=_market_collector)
    historical = datetime.now(timezone.utc).date() - timedelta(days=30)
    try:
        job = coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.SH"],
            start_date=historical,
            end_date=historical,
            dimensions=["0_basic"],
            publish_when_complete=False,
            submit=False,
        )
        coordinator.run_job_now(job["id"])
        with patch(
            "research_system.market_data.agent_recovery.recover_uzi_draft_snapshots"
        ) as native_recovery:
            result = recover_collection_job_draft(
                database,
                job_id=job["id"],
                settings=Settings.from_env(Path(__file__).resolve().parents[2]),
            )

        native_recovery.assert_not_called()
        assert result["historical_only"] is True
        assert result["attempted"] == 0
        assert result["failed"] == 0
        assert result["skipped_historical_scopes"][0]["as_of_date"] == historical.isoformat()
        assert "当前" in result["disclosure"]
    finally:
        coordinator.close()
        database.close()
