"""Real offline retry worker -> archive/aux acquisition authority regression."""

import json
from datetime import datetime
from threading import Event
from time import monotonic
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from research_system.market_data import collection, quant_auxiliary, research_collection
from research_system.market_data.collection import MarketDataCollectionCoordinator
from research_system.market_data.dimension_contracts import DEFAULT_DIMENSION_REGISTRY
from research_system.market_data.ingestion import SnapshotIngestionService
from research_system.market_data.models import MarketDataCollectionJob, MarketDataCollectionTask
from research_system.market_data.repository import MarketDataRepository
from research_system.strategies.database import StrategyDatabase
from tests.market_data.test_pit_core import AS_OF, NOW, _input
from tests.market_data.test_quant_auxiliary_inputs import Source


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("acquisition scope tests may only use injected offline sources")
    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)


@pytest.fixture
def wave(tmp_path, monkeypatch):
    database = StrategyDatabase(f"sqlite:///{tmp_path / 'acquisition.sqlite'}")
    database.create_schema()
    with database.session() as session:
        ingestion = SnapshotIngestionService(MarketDataRepository(session))
        for dimension in ("0_basic", "7_industry", "6_research", "6_fund_holders"):
            ingestion.ingest(_input(dimension))
    archive_calls = []
    def archive(**kwargs):
        archive_calls.append(kwargs["dimension"])
        return {"status": "evidence_gap", "evidence": [], "attempts": [{"reason": "offline_missing"}]}
    source = Source(empty=True)
    coordinator = MarketDataCollectionCoordinator(database, collector=lambda **kw: [],
        archive_collector=SimpleNamespace(collect=archive), quant_auxiliary_source=source,
        settings=SimpleNamespace(workspace_root=tmp_path))
    monkeypatch.setattr(coordinator, "_local_today", lambda zone: AS_OF)
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz is not None else NOW.replace(tzinfo=None)
    monkeypatch.setattr(quant_auxiliary, "datetime", FixedDateTime)
    job = coordinator.create_job(tenant_id="offline", user_id="offline", actor_id="offline",
        symbols=["600519.SH"], start_date=AS_OF, end_date=AS_OF,
        dimensions=["7_industry", "6_research", "6_fund_holders"],
        include_research_archive=True, submit=False)
    with database.session() as session:
        stored = session.get(MarketDataCollectionJob, job["id"])
        stored.status = "partial"
        for task in session.scalars(select(MarketDataCollectionTask).where(MarketDataCollectionTask.job_id == job["id"])):
            task.status = "partial" if task.dimension == "7_industry" else "succeeded"
            task.retryable = task.dimension == "7_industry"
            task.attempt_count = 1
    submitted = []
    monkeypatch.setattr(coordinator, "submit", submitted.append)
    yield SimpleNamespace(database=database, coordinator=coordinator, job_id=job["id"],
                          archive_calls=archive_calls, source=source, submitted=submitted)
    coordinator.close()
    database.close()


@pytest.mark.parametrize("boundary", ["archive", "quant"])
def test_real_narrow_retry_does_not_acquire_old_research_or_quant_scope(wave, record_property, boundary):
    retry = wave.coordinator.retry_failed(wave.job_id, tenant_id="offline", user_id="offline",
                                         actor_id="offline", dimensions=["7_industry"])
    assert {row["dimension_id"] for row in retry["tasks"] if row["status"] == "queued"} == {"7_industry"}
    unchanged = {row["dimension_id"]: row for row in retry["tasks"] if row["dimension_id"] != "7_industry"}
    wave.coordinator._run_job(wave.job_id, Event())
    final = wave.coordinator.get_job(wave.job_id, tenant_id="offline", user_id="offline")
    record_property("actual_acquisition", json.dumps({"retry_dimensions": ["7_industry"],
        "archive_calls": wave.archive_calls, "quant_source_calls": wave.source.calls,
        "archive_stage": final["progress"].get("research_archive"),
        "quant_stage": final["progress"].get("native_quant_auxiliary")}, ensure_ascii=False))
    if boundary == "archive":
        assert wave.archive_calls == ["7_industry"]
    else:
        assert wave.source.calls == []
        assert final["progress"]["native_quant_auxiliary"]["reason_code"] == "quant_dependency_not_requested"
    assert {row["dimension_id"]: row for row in final["tasks"] if row["dimension_id"] != "7_industry"} == unchanged


def run_direct(wave, boundary, task_ids=None, *, recovery=False):
    if boundary == "archive":
        return research_collection.recover_research_documents(wave.database, job_id=wave.job_id,
            settings=wave.coordinator.settings, registry=DEFAULT_DIMENSION_REGISTRY,
            archive_collector=wave.coordinator.archive_collector, task_ids=task_ids)
    return quant_auxiliary.collect_job_quant_auxiliary_inputs(wave.database, job_id=wave.job_id,
        registry=DEFAULT_DIMENSION_REGISTRY, deadline=monotonic() + 30, cancellation=Event(),
        source=wave.source, task_ids=task_ids, recovery=recovery)


@pytest.mark.parametrize("boundary", ["archive", "quant"])
@pytest.mark.parametrize("bad_scope", ["malformed", "missing", "foreign"])
def test_unapproved_task_ids_cannot_reach_archive_or_quant(wave, boundary, bad_scope):
    ids = "not-an-id-list" if bad_scope == "malformed" else ["missing-task"]
    if bad_scope == "foreign":
        with wave.database.session() as session:
            job = MarketDataCollectionJob(tenant_id="offline", user_id="offline", idempotency_key="foreign",
                requested_by="offline", start_date=AS_OF, end_date=AS_OF, origin="manual")
            session.add(job)
            session.flush()
            task = MarketDataCollectionTask(job_id=job.id, symbol="600519.SH", market="CN_EQUITY",
                exchange_timezone="Asia/Shanghai", dimension="6_fund_holders" if boundary == "quant" else "6_research",
                start_date=AS_OF, end_date=AS_OF)
            session.add(task)
            session.flush()
            ids = [task.id]
    error = research_collection.ArchiveEvidenceError if boundary == "archive" else quant_auxiliary.QuantAuxiliaryInputError
    with pytest.raises(error) as rejected:
        run_direct(wave, boundary, ids)
    assert rejected.value.code == ("research_archive_task_scope_invalid" if boundary == "archive" else "quant_task_scope_invalid")
    assert wave.archive_calls == [] and wave.source.calls == []


@pytest.mark.parametrize("boundary", ["archive", "quant"])
def test_explicit_empty_scope_is_not_whole_job_acquisition(wave, boundary):
    result = run_direct(wave, boundary, ())
    assert result["status"] == "not_requested" and "scope_empty" in result["reason_code"]
    assert wave.archive_calls == [] and wave.source.calls == []


@pytest.mark.parametrize("boundary", ["archive", "quant"])
@pytest.mark.parametrize("explicit", [False, True])
def test_whole_job_compatibility_and_explicit_full_ids_keep_acquisition(wave, boundary, explicit):
    with wave.database.session() as session:
        ids = tuple(session.scalars(select(MarketDataCollectionTask.id).where(MarketDataCollectionTask.job_id == wave.job_id)))
    result = run_direct(wave, boundary, ids if explicit else None, recovery=True)
    if boundary == "archive":
        assert set(wave.archive_calls) == {"7_industry", "6_research"} and result["attempted"] == 2
    else:
        assert wave.source.calls == [("all_holding_funds", {"ticker_code": "600519", "max_funds": 80})]
        assert result["status"] == "available"
        # Fixed available receipt/response reuse remains unchanged.
        assert run_direct(wave, boundary, ids, recovery=True)["status"] == "available"
        assert len(wave.source.calls) == 1


def test_explicit_full_job_agent_recovery_passes_claimed_ids_to_actual_acquisition(wave, monkeypatch):
    with wave.database.session() as session:
        job = session.get(MarketDataCollectionJob, wave.job_id)
        job.dimensions_json = list(DEFAULT_DIMENSION_REGISTRY.dimension_ids)
        present = {task.dimension for task in session.scalars(select(MarketDataCollectionTask).where(
            MarketDataCollectionTask.job_id == wave.job_id))}
        for dimension in DEFAULT_DIMENSION_REGISTRY.dimension_ids:
            if dimension not in present:
                session.add(MarketDataCollectionTask(job_id=wave.job_id, symbol="600519.SH", market="CN_EQUITY",
                    exchange_timezone="Asia/Shanghai", dimension=dimension, start_date=AS_OF, end_date=AS_OF,
                    status="succeeded", attempt_count=1))
    scheduled = []
    monkeypatch.setattr(wave.coordinator, "_submit_agent_recovery", scheduled.append)
    def fake_provider_recovery(*args, **kwargs):
        # Provider recovery itself is out of this transport test. The
        # coordinator's actual research pipeline and both source leaves run.
        kwargs["research_pipeline"]()
        return {"attempted": 0, "succeeded": 0, "failed": 0, "persisted_snapshots": []}
    monkeypatch.setattr(collection, "recover_collection_job_draft", fake_provider_recovery)
    wave.coordinator.start_agent_recovery(wave.job_id, tenant_id="offline", user_id="offline", actor_id="offline")
    assert scheduled == [wave.job_id]
    wave.coordinator._run_agent_recovery(wave.job_id, Event())
    assert set(wave.archive_calls) == {"7_industry", "6_research"}
    assert wave.source.calls == [("all_holding_funds", {"ticker_code": "600519", "max_funds": 80})]
