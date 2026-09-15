"""A successful derived task's API must not claim its stored raw data is absent.

This uses the existing fictional SQLite/native-spawn harness, not production
records. It records a display regression found after a real historical run.
"""

from sqlalchemy import select

from research_system.market_data.models import MarketDataCollectionTaskResult
from research_system.market_data.repository import MarketDataRepository
from tests.market_data.test_historical_moat_collection import (
    harness,  # noqa: F401 - imported pytest fixture keeps this probe offline
    run,
    seed,
)


def test_successful_moat_task_exposes_present_raw_without_a_failure_label(harness):  # noqa: F811 - pytest injects the imported fixture
    database, coordinator, tmp_path, _ = harness
    old = seed(database, tmp_path)
    response = run(coordinator)
    task = response["tasks"][0]
    assert response["status"] == "succeeded"
    assert task["collection_outcome"] == "available"
    assert task["failure_kind"] is None
    assert task["failure_label"] is None
    with database.session() as session:
        result = session.scalar(select(MarketDataCollectionTaskResult).where(
            MarketDataCollectionTaskResult.task_id == task["id"]))
        repository = MarketDataRepository(session)
        snapshot = repository.require_snapshot(result.snapshot_id)
        assert result.metrics_json["raw_data_present"] is True
        assert snapshot.payload_json["raw"]
        assert snapshot.payload_json["_uzi_raw_result"]["data"]
        assert snapshot.origin == "derived"
        assert repository.require_snapshot(old["id"]).content_hash == old["hash"]
    # Originally RED: task_failure() returned False before looking at the task's
    # saved result/snapshot. raw_data_present is not limited to collector-only
    # data: the failure branch already reads the final snapshot's raw payload.
    assert task["raw_data_present"] is True
