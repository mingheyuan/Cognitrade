"""Local provider-wave diagnostics; no provider/network work or fake slot progress."""
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from research_system.market_data.collection import MarketDataCollectionCoordinator
from research_system.market_data.models import MarketDataCollectionTaskResult
from research_system.strategies.database import StrategyDatabase


@pytest.fixture
def coordinator():
    database = StrategyDatabase("sqlite://")
    database.create_schema()
    value = MarketDataCollectionCoordinator(database, collector=lambda **kwargs: [])
    try:
        yield value
    finally:
        value.close()
        database.close()


def create(coordinator, symbols):
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    return coordinator.create_job(tenant_id="tenant", user_id="user", actor_id="actor",
        symbols=symbols, start_date=today, end_date=today, dimensions="all",
        publish_when_complete=False, include_research_archive=False, submit=False)


def read(coordinator, job):
    return coordinator.get_job(job["id"], tenant_id="tenant", user_id="user")


def test_starting_to_collecting_is_immediate_without_fake_dimension_completion(coordinator):
    job = create(coordinator, ["688041.SH"])
    tick = [100.0]
    seen = []
    def collector(**kwargs):
        tick[0] += 0.01
        kwargs["progress_callback"]({"phase": "collecting", "snapshot_type": "market", "completed": 0, "total": 22})
        seen.append(read(coordinator, job))
        with coordinator.database.session() as session:
            seen.append(session.scalar(select(func.count()).select_from(MarketDataCollectionTaskResult)))
        return []
    coordinator.collector = collector
    with patch("research_system.market_data.collection.monotonic", side_effect=lambda: tick[0]):
        coordinator.run_job_now(job["id"])
    assert len(seen) == 2
    progress = seen[0]["progress"]
    assert progress["provider"]["phase"] == "collecting"
    assert progress["provider"]["completed"] == 0
    assert progress["provider"]["symbol"] == "688041.SH"
    assert progress["provider"]["as_of_date"] == job["start_date"]
    assert progress["provider"]["elapsed_seconds"] >= 0
    assert progress["provider"]["started_at"]
    assert progress["completed"] == 0 and seen[1] == 0
    assert all(task["status"] == "running" for task in seen[0]["tasks"])


def test_unchanged_callbacks_are_throttled_but_later_elapsed_is_persisted(coordinator):
    job = create(coordinator, ["688041.SH"])
    tick = [100.0]
    def collector(**kwargs):
        event = {"phase": "collecting", "snapshot_type": "market", "completed": 0, "total": 22}
        for value in (100.01, 100.1, 100.3, 101.25):
            tick[0] = value
            kwargs["progress_callback"](event)
        return []
    coordinator.collector = collector
    with patch("research_system.market_data.collection.monotonic", side_effect=lambda: tick[0]), \
            patch.object(coordinator, "_record_provider_progress", wraps=coordinator._record_provider_progress) as writes:
        coordinator.run_job_now(job["id"])
    collecting = [call.kwargs for call in writes.call_args_list if call.kwargs["phase"] == "collecting"]
    assert len(collecting) == 2
    assert collecting[-1]["elapsed_seconds"] >= 1.25
    assert collecting[0]["started_at"] == collecting[-1]["started_at"]
    assert all(row["completed"] == 0 for row in collecting)


def test_symbol_change_is_not_throttled_and_unscoped_wave_does_not_invent_active_symbol(coordinator):
    symbols = ["688041.SH", "688012.SH"]
    job = create(coordinator, symbols)
    tick = [100.0]
    seen = []
    def collector(**kwargs):
        for symbol in (None, *symbols):
            tick[0] += 0.01
            kwargs["progress_callback"]({"phase": "collecting", "snapshot_type": "market",
                "completed": 0, "total": 44, **({"symbol": symbol} if symbol else {})})
            seen.append(read(coordinator, job)["progress"]["provider"])
        return []
    coordinator.collector = collector
    with patch("research_system.market_data.collection.monotonic", side_effect=lambda: tick[0]):
        coordinator.run_job_now(job["id"])
    assert seen[0]["symbol"] is None
    assert seen[0]["scope_symbols"] == symbols
    assert seen[0]["activity_scope"] == "provider_wave"
    assert [row["symbol"] for row in seen[1:]] == symbols
    assert all(row["completed"] == 0 for row in seen)
