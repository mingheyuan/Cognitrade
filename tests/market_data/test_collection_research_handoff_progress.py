"""Settled source slots must be visible while slower research is still running."""
from collections import Counter
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from research_system.market_data.collection import MarketDataCollectionCoordinator
from research_system.market_data.models import MarketDataCollectionJob
from research_system.strategies.database import StrategyDatabase
from tests.market_data.test_collection_coordinator import _Collector


@pytest.mark.parametrize("symbols", [["600519.SH"], ["600519.SH", "000858.SZ"]])
def test_source_settlement_is_durable_before_entering_research(symbols, monkeypatch):
    monkeypatch.setattr("socket.socket.connect", lambda *a, **k: pytest.fail("unexpected network"))
    database = StrategyDatabase("sqlite://")
    database.create_schema()
    coordinator = MarketDataCollectionCoordinator(database, collector=_Collector())
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    job = coordinator.create_job(tenant_id="tenant", user_id="user", actor_id="actor",
        symbols=symbols, start_date=today, end_date=today, dimensions="all",
        publish_when_complete=False, include_research_archive=True, submit=False)
    observed = []

    def research(job_id, **kwargs):
        view = coordinator.get_job(job_id, tenant_id="tenant", user_id="user")
        with database.session() as session:
            row = session.get(MarketDataCollectionJob, job_id)
            durable = dict(row.progress_json)
            finished = list(row.checkpoint_json.get("finished_task_ids") or [])
            assert row.checkpoint_json["provider_progress"] == durable["provider"]
        observed.append((view, durable, finished))
        return {}

    try:
        with patch.object(coordinator, "_run_research_archive_stage", side_effect=research):
            coordinator.run_job_now(job["id"])
        assert len(observed) == 1
        view, durable, finished = observed[0]
        assert view["status"] == "running"
        assert not view["can_confirm_freeze"]
        actual = Counter(task["status"] for task in view["tasks"])
        assert actual["succeeded"] == len(symbols)
        assert not (actual["queued"] or actual["running"])
        assert durable["completed"] == len(view["tasks"]) == 22 * len(symbols)
        assert durable["total"] == len(view["tasks"])
        assert durable["by_status"] == dict(actual)
        assert set(finished) == {task["id"] for task in view["tasks"]}
        assert view["progress"] == durable
        assert durable["provider"]["phase"] == "settled"
    finally:
        coordinator.close()
        database.close()
