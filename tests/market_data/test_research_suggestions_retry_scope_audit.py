"""Remaining coordinator -> enrichment wave authority regression (offline)."""

import json
from threading import Event
from time import monotonic
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from research_system.market_data import collection, research_enrichment
from research_system.market_data.collection import MarketDataCollectionCoordinator
from research_system.market_data.dimension_contracts import DEFAULT_DIMENSION_REGISTRY
from research_system.market_data.models import MarketDataCollectionJob, MarketDataCollectionTask
from tests.market_data.test_company_profile_workflow import Provider
from tests.market_data.test_company_profile_workflow import company as company  # noqa: PLC0414
from tests.market_data.test_research_enrichment import _job
from tests.market_data.test_pit_core import AS_OF
from tests.market_data.test_research_stage_wiring import stage as stage  # noqa: PLC0414


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("retry scope audit must not call real sources or models")
    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)


def test_narrow_retry_cannot_implicitly_authorize_original_jobs_basic_profile(
    company, monkeypatch, record_property,
):
    job_id = _job(company.database, dimensions=("0_basic", "7_industry"))
    with company.database.session() as session:
        job = session.get(MarketDataCollectionJob, job_id)
        job.origin, job.status = "manual", "partial"
        job.request_json = {"include_research_archive": True}
        for task in session.scalars(select(MarketDataCollectionTask).where(MarketDataCollectionTask.job_id == job_id)):
            task.status = "succeeded" if task.dimension == "0_basic" else "partial"
            task.retryable = task.dimension == "7_industry"
            task.attempt_count = 1
    class AdaptiveProvider(Provider):
        def parse(self, **kwargs):
            # The fixture records prompts by deepcopy; a real worker's Event
            # is not serializable and is not part of the fake model payload.
            response = super().parse(**{key: value for key, value in kwargs.items() if key != "cancellation"})
            if "company_profile_proposals" not in kwargs["schema"].model_fields:
                response.pop("company_profile_proposals", None)
                response.pop("company_profile_assessments", None)
            return response
    provider = AdaptiveProvider(company)
    coordinator = MarketDataCollectionCoordinator(company.database, collector=lambda **kw: [],
        research_provider=provider, archive_collector=object(), settings=SimpleNamespace())
    monkeypatch.setattr(coordinator, "_local_today", lambda zone: AS_OF)
    monkeypatch.setattr(collection, "recover_research_documents", lambda *a, **kw: {
        "attempted": 0, "succeeded": 0, "failed": 0,
    })
    called = []
    original_run = research_enrichment.run_research_enrichment
    def run_actual(*args, **kwargs):
        called.append({"task_ids": kwargs.get("task_ids")})
        return original_run(*args, **kwargs)
    monkeypatch.setattr(research_enrichment, "run_research_enrichment", run_actual)
    monkeypatch.setattr(research_enrichment, "recompute_native_facts",
                        lambda *a, **kw: pytest.fail("narrow retry must not recompute old basic"))
    submitted = []
    monkeypatch.setattr(coordinator, "submit", submitted.append)
    try:
        retried = coordinator.retry_failed(job_id, tenant_id="offline", user_id="offline",
                                           actor_id="offline", dimensions=["7_industry"])
        assert submitted == [job_id]
        assert {row["dimension_id"] for row in retried["tasks"] if row["status"] == "queued"} == {"7_industry"}
        old_basic_task = next(row for row in retried["tasks"] if row["dimension_id"] == "0_basic")
        industry_task_id = next(row["id"] for row in retried["tasks"] if row["dimension_id"] == "7_industry")
        coordinator._run_job(job_id, Event())
        final = coordinator.get_job(job_id, tenant_id="offline", user_id="offline")
        result = final["progress"]["research_enrichment"]
        record_property("actual_scope", json.dumps({
            "retry_dimensions": ["7_industry"],
            "model_schema": [call["schema"].__name__ for call in provider.calls],
            "revised_dimensions": [row["dimension"] for row in result["revisions"]],
            "rejected": result["rejected"],
            "enrichment_authority": called,
            "groups": result["groups"],
        }, ensure_ascii=False))
        assert len(provider.calls) == 1, "the actual paired research group must run, not be skipped"
        assert not any(row["dimension"] == "0_basic" for row in result["revisions"])
        assert called == [{"task_ids": (industry_task_id,)}]
        assert provider.calls[0]["schema"] is research_enrichment.ResearchSuggestionGroup
        assert result["groups"][0]["status"] == "completed"
        assert next(row for row in final["tasks"] if row["dimension_id"] == "0_basic") == old_basic_task
    finally:
        coordinator.close()


@pytest.mark.parametrize("task_ids,status", [
    ((), "not_requested"),
    (["missing-task"], "input_invalid"),
    ("not-an-id-list", "input_invalid"),
    ([None], "input_invalid"),
    (42, "input_invalid"),
])
def test_enrichment_empty_or_malformed_authority_cannot_start_research(company, task_ids, status):
    job_id = _job(company.database, dimensions=("0_basic", "7_industry"))
    provider = SimpleNamespace(parse=lambda **kw: pytest.fail("unapproved scope reached model"))
    result = research_enrichment.run_research_enrichment(company.database, job_id=job_id,
        registry=DEFAULT_DIMENSION_REGISTRY, provider=provider, deadline_at=monotonic() + 30,
        task_ids=task_ids)
    assert result["status"] == status and result["groups"] == [] and result["revisions"] == []
    if status == "input_invalid":
        assert result["rejected"] == [{"reason": "research_task_scope_invalid"}]
    else:
        assert result["reason_code"] == "research_task_scope_empty"


def test_foreign_job_task_does_not_grant_same_stock_basic_authority(company):
    job_id = _job(company.database, dimensions=("7_industry",))
    with company.database.session() as session:
        foreign_job = MarketDataCollectionJob(tenant_id="offline", user_id="offline", idempotency_key="other-job",
            requested_by="offline", start_date=AS_OF, end_date=AS_OF, origin="manual")
        session.add(foreign_job)
        session.flush()
        foreign_task = MarketDataCollectionTask(job_id=foreign_job.id, symbol="600519.SH", market="CN_EQUITY",
            exchange_timezone="Asia/Shanghai", dimension="0_basic", start_date=AS_OF, end_date=AS_OF)
        session.add(foreign_task)
        session.flush()
        foreign_id = foreign_task.id
        own_id = session.scalar(select(MarketDataCollectionTask.id).where(MarketDataCollectionTask.job_id == job_id))
    result = research_enrichment.run_research_enrichment(company.database, job_id=job_id,
        registry=DEFAULT_DIMENSION_REGISTRY, provider=SimpleNamespace(parse=lambda **kw: pytest.fail("foreign task reached model")),
        deadline_at=monotonic() + 30, task_ids=[own_id, foreign_id])
    assert result["status"] == "input_invalid" and result["groups"] == [] and result["revisions"] == []
    assert result["rejected"] == [{"reason": "research_task_scope_invalid"}]


@pytest.mark.parametrize("explicit_ids", [False, True])
def test_explicit_basic_and_legacy_whole_job_call_still_allow_profile_revision(company, explicit_ids):
    job_id = _job(company.database, dimensions=("0_basic", "7_industry"))
    with company.database.session() as session:
        basic_id = session.scalar(select(MarketDataCollectionTask.id).where(
            MarketDataCollectionTask.job_id == job_id, MarketDataCollectionTask.dimension == "0_basic"))
    result = research_enrichment.run_research_enrichment(company.database, job_id=job_id,
        registry=DEFAULT_DIMENSION_REGISTRY, provider=Provider(company), deadline_at=monotonic() + 30,
        task_ids=(basic_id,) if explicit_ids else None,
        native_recompute=lambda *a, **kw: pytest.fail("unrebuilt dependencies cannot be scored"))
    assert [row["dimension"] for row in result["revisions"]] == ["0_basic"]
    assert result["groups"][0]["status"] == "completed" and result["status"] == "partial"


@pytest.mark.parametrize("recovery", [False, True])
def test_collection_stage_passes_its_fixed_authority_to_research_for_both_owners(stage, monkeypatch, recovery):
    coordinator, job, _provider, _source, _clock, calls = stage
    if recovery:
        monkeypatch.setattr(coordinator, "_fence_agent_worker", lambda *args, **kwargs: True)
    task_ids = tuple(row["id"] for row in job["tasks"])
    coordinator._run_research_archive_stage(job["id"], worker_id="worker", cancellation=Event(),
        recovery=recovery, task_ids=task_ids)
    assert next(options for kind, options in calls if kind == "enrichment")["task_ids"] == task_ids
