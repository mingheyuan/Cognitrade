"""Offline queued-wave authority, including the real retry/worker boundary."""

import json
import traceback
from datetime import datetime, timedelta, timezone
from threading import Event
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from research_system.market_data import collection, company_profile_rebuild, research_enrichment
from research_system.market_data.models import (
    MarketDataCollectionJob,
    MarketDataCollectionTask,
    MarketDataCollectionTaskResult,
)
from tests.market_data.test_company_profile_rebuild import run
from tests.market_data.test_company_profile_rebuild_task_lifecycle import (
    coordinator as coordinator,  # noqa: PLC0414
    failed_rows,
    finish,
    old_success_retry_flags,
    state as state,  # noqa: PLC0414
)
from tests.market_data.test_pit_core import AS_OF


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("wave regression must not call real sources or models")
    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)


def ids_by_dimension(state):
    with state["database"].session() as session:
        return {row.dimension: row.id for row in session.scalars(select(MarketDataCollectionTask).where(
            MarketDataCollectionTask.job_id == state["job_id"]))}


def stub_other_research(coordinator, monkeypatch):
    coordinator.settings = SimpleNamespace()
    coordinator.research_provider = object()
    monkeypatch.setattr(coordinator, "_local_today", lambda zone: AS_OF)
    monkeypatch.setattr(collection, "recover_research_documents", lambda *a, **kw: {
        "attempted": 0, "succeeded": 0, "failed": 0,
    })
    monkeypatch.setattr(research_enrichment, "run_research_enrichment", lambda *a, **kw: {
        "status": "completed", "revisions": [], "rejected": [],
    })


@pytest.mark.parametrize("dimensions", [["7_industry"], ["7_industry", "4_peers"]])
def test_real_scoped_retry_worker_does_not_rebuild_unclaimed_old_failures(
    state, coordinator, monkeypatch, record_property, dimensions,
):
    old_success_retry_flags(state)
    run(state, failed_rows)
    finish(state, coordinator)
    before = coordinator.get_job(state["job_id"], tenant_id="offline", user_id="offline")
    old_peer = next(row for row in before["tasks"] if row["dimension_id"] == "4_peers")
    with state["database"].session() as session:
        peer_results = [(row.id, row.snapshot_id, row.status) for row in session.scalars(
            select(MarketDataCollectionTaskResult).where(MarketDataCollectionTaskResult.task_id == old_peer["id"]))]
    coordinator.retry_failed(state["job_id"], tenant_id="offline", user_id="offline",
                             actor_id="offline", dimensions=dimensions)
    stub_other_research(coordinator, monkeypatch)
    calls = []
    persist_errors = []
    real_persist = company_profile_rebuild._persist
    def traced_persist(*args, **kwargs):
        try:
            return real_persist(*args, **kwargs)
        except Exception:
            persist_errors.append(traceback.format_exc())
            raise
    monkeypatch.setattr(company_profile_rebuild, "_persist", traced_persist)
    def collector(**options):
        fixed = "fixed_basic_by_symbol" in options
        calls.append({"fixed_basic": fixed, "snapshot_types": list(options["snapshot_types"])})
        return failed_rows(options) if fixed else []
    coordinator.collector = collector
    # Actual claim of queued tasks, task processing, post-research producer,
    # persistence, finish and serialization; only external work is synthetic.
    coordinator._run_job(state["job_id"], Event())
    final = coordinator.get_job(state["job_id"], tenant_id="offline", user_id="offline")
    stage = final["progress"].get("company_profile_dependency_rebuild", {})
    record_property("actual_wave", json.dumps({"requested_dimensions": dimensions, "calls": calls,
        "revisions": stage.get("revisions"), "stage_status": stage.get("status"), "scopes": stage.get("scopes"),
        "task_attempts": {row["dimension_id"]: row["attempt_count"] for row in final["tasks"]},
        "persist_errors": persist_errors}, ensure_ascii=False))
    expected = {"industry" if key == "7_industry" else "peers" for key in dimensions}
    fixed_calls = [row for row in calls if row["fixed_basic"]]
    assert fixed_calls and set(fixed_calls[0]["snapshot_types"]) == expected
    assert {row["dimension"] for row in stage["revisions"]} == set(dimensions)
    assert final["status"] == "partial" and final["progress"]["completed"] == 2
    if "4_peers" not in dimensions:
        assert next(row for row in final["tasks"] if row["dimension_id"] == "4_peers") == old_peer
        assert "4_peers" in stage["scopes"][0]["not_requested_dimensions"]
        with state["database"].session() as session:
            assert [(row.id, row.snapshot_id, row.status) for row in session.scalars(
                select(MarketDataCollectionTaskResult).where(MarketDataCollectionTaskResult.task_id == old_peer["id"]))] == peer_results


def test_producer_requires_explicit_task_ids_instead_of_job_wide_fallback(state):
    result = company_profile_rebuild.run_company_profile_dependency_rebuild(
        state["database"], job_id=state["job_id"], registry=collection.DEFAULT_DIMENSION_REGISTRY,
        collector=lambda **kw: pytest.fail("missing authority cannot call collector"),
        deadline=company_profile_rebuild.monotonic() + 60,
    )
    assert result["status"] == "not_required" and result["revisions"] == []
    assert not company_profile_rebuild.has_company_profile_rebuild(
        state["database"], job_id=state["job_id"], registry=collection.DEFAULT_DIMENSION_REGISTRY)


@pytest.mark.parametrize("bad_ids", [["not-a-task"], ["not-a-task", "valid"]])
def test_invalid_task_authority_is_rejected_before_any_collection(state, bad_ids):
    task_ids = [ids_by_dimension(state)["7_industry"] if key == "valid" else key for key in bad_ids]
    result = run(state, lambda options: pytest.fail("invalid authority called collector"), task_ids=task_ids)
    assert result["status"] == "profile_rebuild_task_scope_invalid" and result["revisions"] == []


def test_explicit_empty_task_set_cannot_rebuild(state):
    result = run(state, lambda options: pytest.fail("empty authority called collector"), task_ids=[])
    assert result["status"] == "not_required" and result["revisions"] == []


@pytest.mark.parametrize("task_ids", [None, "task-id", [None], [""]])
def test_malformed_task_scope_cannot_call_collector(state, task_ids):
    result = run(state, lambda options: pytest.fail("malformed authority called collector"), task_ids=task_ids)
    assert result["status"] == "profile_rebuild_task_scope_invalid" and result["revisions"] == []


def test_task_id_from_another_job_cannot_authorize_collection(state):
    with state["database"].session() as session:
        foreign_job = MarketDataCollectionJob(tenant_id="offline", user_id="offline", idempotency_key="foreign",
            requested_by="offline", start_date=AS_OF, end_date=AS_OF, origin="manual")
        session.add(foreign_job)
        session.flush()
        foreign = MarketDataCollectionTask(job_id=foreign_job.id, symbol="600519.SH", market="CN_EQUITY",
            exchange_timezone="Asia/Shanghai", dimension="4_peers", start_date=AS_OF, end_date=AS_OF)
        session.add(foreign)
        session.flush()
        foreign_id = foreign.id
    result = run(state, lambda options: pytest.fail("foreign task called collector"),
                 task_ids=[ids_by_dimension(state)["7_industry"], foreign_id])
    assert result["status"] == "profile_rebuild_task_scope_invalid" and result["revisions"] == []


def test_explicit_recovery_captures_its_job_task_ids_at_claim(state, coordinator, monkeypatch):
    # Recovery is a separate explicit job-wide action; it must supply its
    # own fixed IDs, not rely on producer inference from old tasks.
    with state["database"].session() as session:
        job = session.get(MarketDataCollectionJob, state["job_id"])
        job.status = "partial"
        job.checkpoint_json = {"agent_recovery": {"status": "queued"}}
    captured = []
    monkeypatch.setattr(coordinator, "_run_research_archive_stage", lambda *a, **kw: captured.append(kw) or {})
    def recover(*args, **kwargs):
        kwargs["research_pipeline"]()
        return {"attempted": 0, "succeeded": 0, "failed": 0, "persisted_snapshots": []}
    monkeypatch.setattr(collection, "recover_collection_job_draft", recover)
    coordinator.settings = SimpleNamespace()
    coordinator._run_agent_recovery(state["job_id"], Event())
    assert len(captured) == 1 and captured[0]["recovery"] is True
    assert set(captured[0].get("task_ids", [])) == set(ids_by_dimension(state).values())


@pytest.mark.parametrize("lease_owner,expired,cancelled,allow_cancel,expected", [
    ("worker", False, False, False, True),
    ("other", False, False, False, False),
    ("worker", True, False, False, False),
    ("worker", False, True, False, False),
    ("worker", False, True, True, True),
])
def test_loaded_sqlite_job_fence_uses_sql_lease_conditions_without_python_date_compare(
    state, coordinator, lease_owner, expired, cancelled, allow_cancel, expected,
):
    with state["database"].session() as session:
        job = session.get(MarketDataCollectionJob, state["job_id"])
        job.status, job.lease_owner, job.cancel_requested = "running", lease_owner, cancelled
        job.lease_until = datetime.now(timezone.utc) + timedelta(minutes=-5 if expired else 5)
    with state["database"].session() as session:
        loaded = session.get(MarketDataCollectionJob, state["job_id"])
        assert loaded.lease_until.tzinfo is None  # Actual SQLite ORM shape.
        before = (loaded.updated_at, loaded.lease_until, loaded.lease_owner, loaded.status, loaded.cancel_requested)
        assert coordinator._fence_collection_worker(session, state["job_id"], "worker",
            allow_cancel_requested=allow_cancel) is expected
        session.refresh(loaded)
        assert (loaded.updated_at, loaded.lease_until, loaded.lease_owner, loaded.status, loaded.cancel_requested) == before
