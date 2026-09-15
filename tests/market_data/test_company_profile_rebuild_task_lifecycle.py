"""Real offline producer -> finish -> API serialization -> explicit retry.

The collector is synthetic and submit is recorded, never executed. Raw/native
quality and TaskResult degradation must survive collection lifecycle mapping.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from research_system.market_data.collection import CollectionJobError, MarketDataCollectionCoordinator
from research_system.market_data.contracts import CollectionStatus
from research_system.market_data.job_diagnostics import task_is_retryable
from research_system.market_data.models import MarketDataCollectionTask, MarketDataCollectionTaskResult
from research_system.market_data.native_projection import project_native_result
from research_system.market_data.repository import MarketDataRepository
from tests.market_data.test_company_profile_rebuild import collected, run
from tests.market_data.test_company_profile_rebuild import state as state  # noqa: PLC0414


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("lifecycle regression must not call real sources or models")
    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)


@pytest.fixture
def coordinator(state, monkeypatch):
    value = MarketDataCollectionCoordinator(state["database"], collector=lambda **kw: pytest.fail("unexpected collection"))
    submitted = []
    monkeypatch.setattr(value, "submit", submitted.append)
    value.offline_submitted = submitted
    yield value
    value.close()


def failed_rows(options):
    return [{**row, "quality_status": "data_gap", "facts": {"_uzi_raw_result": {
        "data": {}, "error": "offline upstream failure", "source": "offline:native", "fallback": True,
    }}} for row in collected(options)]


def finish(state, coordinator):
    coordinator._finish_job(state["job_id"])
    return coordinator.get_job(state["job_id"], tenant_id="offline", user_id="offline")


def old_success_retry_flags(state):
    with state["database"].session() as session:
        for task in session.scalars(select(MarketDataCollectionTask)):
            task.retryable = False
            task.next_retry_at = None


def test_degraded_native_results_finish_as_partial_tasks_and_completed_partial_job(state, coordinator, record_property):
    old_success_retry_flags(state)
    result = run(state, failed_rows)
    final = finish(state, coordinator)
    record_property("actual_lifecycle", json.dumps({
        "producer_statuses": [row["status"] for row in result["revisions"]],
        "job_status": final["status"], "progress": final["progress"],
        "tasks": [{key: row[key] for key in ("id", "status", "retryable", "attempt_count", "resolution_status")}
                  for row in final["tasks"]],
    }, ensure_ascii=False))
    assert final["status"] == "partial"
    assert final["progress"]["completed"] == final["progress"]["total"] == 2
    assert final["progress"]["by_status"] == {"partial": 2}
    assert set(final["checkpoint"]["finished_task_ids"]) == {row["id"] for row in final["tasks"]}
    assert all(row["status"] == CollectionStatus.PARTIAL.value for row in final["tasks"])
    assert all(row["resolution_status"] == "degraded" for row in final["tasks"])
    assert all(row["status"] == "degraded" for row in result["revisions"])
    with state["database"].session() as session:
        repo = MarketDataRepository(session)
        attempts = list(session.scalars(select(MarketDataCollectionTaskResult).where(MarketDataCollectionTaskResult.attempt == 2)))
        assert len(attempts) == 2 and all(row.status == "degraded" for row in attempts)
        for row in attempts:
            native = project_native_result(repo.require_snapshot(row.snapshot_id), version="uzi-dim-result-v3")
            assert native["data"] == {} and native["error"] == "offline upstream failure" and native["fallback"] is True


def test_failed_rebuild_after_success_supports_real_scoped_retry_dispatch(state, coordinator):
    old_success_retry_flags(state)
    run(state, failed_rows)
    final = finish(state, coordinator)
    response = coordinator.retry_failed(state["job_id"], tenant_id="offline", user_id="offline",
                                        actor_id="offline", dimensions=["7_industry"])
    assert response["status"] == "queued" and coordinator.offline_submitted == [state["job_id"]]
    tasks = {row["dimension_id"]: row for row in response["tasks"]}
    assert tasks["7_industry"]["status"] == "queued" and tasks["7_industry"]["next_retry_at"] is None
    assert tasks["4_peers"]["status"] == "partial"
    assert all(task_is_retryable(row) for row in final["tasks"])


def test_retry_flags_and_backoff_are_updated_on_both_task_and_attempt(state, coordinator):
    old_success_retry_flags(state)
    run(state, failed_rows)
    final = finish(state, coordinator)
    with state["database"].session() as session:
        for task in session.scalars(select(MarketDataCollectionTask)):
            assert task.retryable and task_is_retryable(task)
            assert task.next_retry_at is not None
            assert (task.next_retry_at - task.completed_at).total_seconds() == 4
            latest = session.scalar(select(MarketDataCollectionTaskResult).where(
                MarketDataCollectionTaskResult.task_id == task.id).order_by(MarketDataCollectionTaskResult.attempt.desc()))
            assert latest.retryable is True and latest.error_code == "native_rebuild_data_gap"
    assert all(row["retryable"] and row["next_retry_at"] for row in final["tasks"])


def test_successful_rebuild_clears_stale_failure_retry_state(state, coordinator):
    with state["database"].session() as session:
        for task in session.scalars(select(MarketDataCollectionTask)):
            task.status, task.retryable = "partial", True
            task.next_retry_at = datetime.now(timezone.utc) + timedelta(days=1)
            task.last_error_code, task.last_error_message = "old_failure", "旧失败"
    run(state)
    final = finish(state, coordinator)
    assert final["status"] == "succeeded" and final["progress"]["completed"] == 2
    assert all(row["status"] == "succeeded" and row["retryable"] is False and row["next_retry_at"] is None
               and row["error_code"] is None for row in final["tasks"])
    with pytest.raises(CollectionJobError, match="没有可重试"):
        coordinator.retry_failed(state["job_id"], tenant_id="offline", user_id="offline", actor_id="offline")
    assert coordinator.offline_submitted == []


def test_exhausted_attempts_remain_terminal_partial_without_retry_schedule(state, coordinator):
    with state["database"].session() as session:
        for task in session.scalars(select(MarketDataCollectionTask)):
            task.attempt_count, task.max_attempts = 2, 3
            task.next_retry_at = datetime.now(timezone.utc) + timedelta(days=1)
    run(state, failed_rows)
    final = finish(state, coordinator)
    assert final["status"] == "partial" and final["progress"]["completed"] == 2
    assert all(row["attempt_count"] == 3 and row["next_retry_at"] is None and not task_is_retryable(row)
               for row in final["tasks"])
    with pytest.raises(CollectionJobError, match="没有可重试"):
        coordinator.retry_failed(state["job_id"], tenant_id="offline", user_id="offline", actor_id="offline")
    assert coordinator.offline_submitted == []
