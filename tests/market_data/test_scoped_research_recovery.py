"""Offline API-to-worker recovery of one archived research dimension."""

import json
from copy import deepcopy
from threading import Event
from time import monotonic
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from research_system.api.dependencies import (
    ApiPrincipal,
    get_market_data_center,
    require_principal,
)
from research_system.api.routes.market_data_center import router
from research_system.market_data import quant_auxiliary, research_enrichment
from research_system.market_data.collection import MarketDataCollectionCoordinator
from research_system.market_data.dimension_contracts import DEFAULT_DIMENSION_REGISTRY
from research_system.market_data.ingestion import SnapshotIngestionService
from research_system.market_data.models import (
    MarketDataCollectionJob,
    MarketDataCollectionTask,
    MarketDataSnapshot,
)
from research_system.market_data.repository import MarketDataRepository
from research_system.market_data.research_archive import ArchiveCandidate
from research_system.market_data.service import (
    MarketDataCenterService,
    MarketDataServiceError,
)
from research_system.strategies.database import StrategyDatabase
from tests.market_data.test_pit_core import AS_OF, _input
from tests.market_data.test_research_archive import CUTOFF, LATER, collector, page
from tests.market_data.test_research_enrichment import _response


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("scoped recovery must not use business network or live models")

    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)


def _service(coordinator):
    service = object.__new__(MarketDataCenterService)
    service.collection = coordinator
    service.settings = SimpleNamespace(market_data_collection_concurrency=5)
    service._resolve_collection_identity = lambda *args, **kwargs: SimpleNamespace(
        is_fund=False
    )
    return service


def _client(service):
    app = FastAPI()
    app.include_router(router, prefix="/api/v2")
    app.dependency_overrides[get_market_data_center] = lambda: service
    app.dependency_overrides[require_principal] = lambda: ApiPrincipal(
        actor_id="user",
        authenticated=True,
        tenant_id="tenant",
        user_id="user",
    )
    return TestClient(app)


def _request(**kwargs):
    return {
        "symbols": ["600519.SH"],
        "asset_family": "equity",
        "start_date": AS_OF.isoformat(),
        "end_date": AS_OF.isoformat(),
        "dimensions": ["7_industry"],
        "mode": "missing_or_stale",
        **kwargs,
    }


@pytest.mark.parametrize("option", [None, False, True])
def test_api_passes_explicit_archive_option_without_changing_default(option):
    coordinator = SimpleNamespace(
        create_job=Mock(return_value={"id": "scope", "status": "queued"})
    )
    body = _request(**({} if option is None else {"include_research_archive": option}))
    with _client(_service(coordinator)) as client:
        response = client.post("/api/v2/finance/market-data/collection-jobs", json=body)
    assert response.status_code == 202, response.text
    assert (
        coordinator.create_job.call_args.kwargs.get("include_research_archive")
        is option
    )


@pytest.mark.parametrize(
    "dimensions,option,enabled",
    [
        (["7_industry"], None, False),
        (["7_industry"], False, False),
        (["7_industry"], True, True),
        ("all", None, True),
        ("all", False, False),
        ("all", True, True),
    ],
)
def test_coordinator_preserves_existing_archive_defaults(
    tmp_path, dimensions, option, enabled
):
    database = StrategyDatabase(f"sqlite:///{tmp_path / 'defaults.sqlite'}")
    database.create_schema()
    coordinator = MarketDataCollectionCoordinator(
        database, collector=lambda **kwargs: [], archive_collector=object()
    )
    try:
        job = coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="user",
            symbols=["600519.SH"],
            start_date=AS_OF,
            end_date=AS_OF,
            dimensions=dimensions,
            include_research_archive=option,
            submit=False,
        )
        with database.session() as session:
            stored = session.get(MarketDataCollectionJob, job["id"])
            assert bool(stored.request_json.get("include_research_archive")) is enabled
    finally:
        coordinator.close()
        database.close()


@pytest.mark.parametrize("option", [False, True])
def test_fund_request_cannot_silently_ignore_archive_option(option):
    coordinator = SimpleNamespace(create_job=Mock())
    service = _service(coordinator)
    service._resolve_collection_identity = lambda *args, **kwargs: SimpleNamespace(
        is_fund=True, code="020464"
    )
    with pytest.raises(MarketDataServiceError) as failure:
        service.create_collection_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="user",
            request=_request(
                symbols=["020464"],
                asset_family="fund",
                dimensions="all",
                include_research_archive=option,
            ),
        )
    assert failure.value.code == "fund_research_archive_option_unsupported"
    assert failure.value.status_code == 422
    coordinator.create_job.assert_not_called()


@pytest.mark.parametrize(
    "dimensions,expected",
    [
        (["7_industry"], 0),
        (["similar_stocks"], 0),
        (["6_fund_holders"], 1),
        ("all", 1),
    ],
)
@pytest.mark.parametrize("recovery", [False, True])
def test_auxiliary_only_runs_for_explicit_fund_holder_scope(
    tmp_path, monkeypatch, dimensions, expected, recovery
):
    database = StrategyDatabase(f"sqlite:///{tmp_path / 'quant-scope.sqlite'}")
    database.create_schema()
    coordinator = MarketDataCollectionCoordinator(
        database, collector=lambda **kwargs: [], archive_collector=object()
    )
    calls = []
    monkeypatch.setattr(
        quant_auxiliary,
        "collect_quant_auxiliary_inputs",
        lambda *args, **kwargs: calls.append(kwargs) or {"status": "available"},
    )
    try:
        job = coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="user",
            symbols=["600519.SH"],
            start_date=AS_OF,
            end_date=AS_OF,
            dimensions=dimensions,
            include_research_archive=True,
            submit=False,
        )
        result = quant_auxiliary.collect_job_quant_auxiliary_inputs(
            database,
            job_id=job["id"],
            registry=DEFAULT_DIMENSION_REGISTRY,
            deadline=monotonic() + 30,
            cancellation=Event(),
            source=object(),
            recovery=recovery,
        )
        assert len(calls) == expected
        assert result["total"] == expected
    finally:
        coordinator.close()
        database.close()


def test_quant_scope_is_per_task_not_any_dimension_in_parent_job(tmp_path, monkeypatch):
    database = StrategyDatabase(f"sqlite:///{tmp_path / 'mixed.sqlite'}")
    database.create_schema()
    coordinator = MarketDataCollectionCoordinator(
        database, collector=lambda **kwargs: [], archive_collector=object()
    )
    calls = []
    monkeypatch.setattr(
        quant_auxiliary,
        "collect_quant_auxiliary_inputs",
        lambda *args, **kwargs: calls.append(kwargs) or {"status": "available"},
    )
    try:
        job = coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="user",
            symbols=["600519.SH"],
            start_date=AS_OF,
            end_date=AS_OF,
            dimensions=["6_fund_holders"],
            include_research_archive=True,
            submit=False,
        )
        with database.session() as session:
            session.add(
                MarketDataCollectionTask(
                    job_id=job["id"],
                    symbol="688041.SH",
                    market="CN_EQUITY",
                    exchange_timezone="Asia/Shanghai",
                    dimension="7_industry",
                    start_date=AS_OF,
                    end_date=AS_OF,
                )
            )
        result = quant_auxiliary.collect_job_quant_auxiliary_inputs(
            database,
            job_id=job["id"],
            registry=DEFAULT_DIMENSION_REGISTRY,
            deadline=monotonic() + 30,
            cancellation=Event(),
            source=object(),
        )
        assert result["total"] == len(calls) == 1
        assert calls[0]["raw"]["ticker"] == "600519.SH"
    finally:
        coordinator.close()
        database.close()


def test_single_industry_recovery_reuses_raw_and_other_archives_then_runs_only_group_b(
    tmp_path, monkeypatch
):
    database = StrategyDatabase(f"sqlite:///{tmp_path / 'recovery.sqlite'}")
    database.create_schema()
    archived = {}
    for dimension in ("7_industry", "8_materials", "9_futures", "15_events"):
        body = page(modified="2026-08-19", document_id=f"offline-{dimension}").replace(
            "原始政策正文，公布经济统计与货币政策措施。".encode(),
            f"600519 {dimension} 原文披露行业收入增长25%，集中度40%，公司公告、营业成本与期货套期保值情况。".encode(),
        )
        archived[dimension] = collector(tmp_path / "docs", body).archive(
            ArchiveCandidate(f"https://www.cninfo.com.cn/release/{dimension}.html"),
            dimension=dimension,
            symbol="600519.SH",
            cutoff_at=CUTOFF,
        )
    with database.session() as session:
        ingestion = SnapshotIngestionService(MarketDataRepository(session))
        original = {}
        for dimension in (
            "0_basic",
            "7_industry",
            "8_materials",
            "9_futures",
            "15_events",
        ):
            kwargs = (
                {
                    "evidence": [archived[dimension]],
                    "observed_at": LATER,
                    "ingested_at": LATER,
                }
                if dimension in archived and dimension != "7_industry"
                else {}
            )
            row = ingestion.ingest(_input(dimension, **kwargs))
            original[dimension] = (row.id, row.content_hash, deepcopy(row.payload_json))

    archive_calls, model_calls, write_scopes = [], [], []
    raw_collector = Mock(
        side_effect=AssertionError("already-succeeded structured input must be reused")
    )
    auxiliary = Mock(
        side_effect=AssertionError(
            "industry recovery cannot collect fund or quant inputs"
        )
    )
    monkeypatch.setattr(quant_auxiliary, "collect_quant_auxiliary_inputs", auxiliary)

    def collect_archive(**kwargs):
        archive_calls.append(kwargs)
        assert kwargs["dimension"] == "7_industry"
        return {"status": "ready", "evidence": [archived["7_industry"]], "attempts": []}

    def parse(**kwargs):
        model_calls.append(
            {
                key: deepcopy(value)
                for key, value in kwargs.items()
                if key != "cancellation"
            }
        )
        response = _response(None, archived["7_industry"].source_url)
        response["proposals"] = []
        return response

    persist = research_enrichment.persist_fact_revisions

    def capture_scope(repo, **kwargs):
        write_scopes.append(
            {
                repo.require_snapshot(item).dimension
                for item in kwargs["allowed_snapshot_ids"]
            }
        )
        return persist(repo, **kwargs)

    monkeypatch.setattr(research_enrichment, "persist_fact_revisions", capture_scope)

    class OfflineProvider:
        available = True
        settings = SimpleNamespace(uzi_llm_transient_retries=0)

        def parse(self, **kwargs):
            return parse(**kwargs)

    provider = OfflineProvider()
    coordinator = MarketDataCollectionCoordinator(
        database,
        collector=raw_collector,
        archive_collector=SimpleNamespace(collect=collect_archive),
        research_provider=provider,
        quant_auxiliary_source=object(),
        settings=SimpleNamespace(workspace_root=tmp_path),
    )
    monkeypatch.setattr(coordinator, "submit", lambda job_id: None)
    try:
        prior = coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="user",
            symbols=["600519.SH"],
            start_date=AS_OF,
            end_date=AS_OF,
            dimensions=["7_industry"],
            include_research_archive=False,
            submit=False,
        )
        coordinator.run_job_now(prior["id"])
        completed = coordinator.get_job(prior["id"], tenant_id="tenant", user_id="user")
        assert completed["tasks"][0]["status"] == "succeeded", completed
        assert completed["tasks"][0]["retryable"] is False
        with database.session() as session:
            row = session.get(MarketDataCollectionJob, prior["id"])
            row.checkpoint_json = {
                **row.checkpoint_json,
                "research_archive": {
                    "status": "partial",
                    "attempts": [
                        {
                            "dimension": "7_industry",
                            "status": "failed",
                            "reason": "gkpj",
                        }
                    ],
                },
            }

        with _client(_service(coordinator)) as client:
            response = client.post(
                "/api/v2/finance/market-data/collection-jobs",
                json=_request(include_research_archive=True),
            )
        assert response.status_code == 202, response.text
        job = response.json()
        coordinator.run_job_now(job["id"])
        result = coordinator.get_job(job["id"], tenant_id="tenant", user_id="user")
        assert result["tasks"][0]["status"] == "succeeded", result
        assert len(result["tasks"]) == len(archive_calls) == len(model_calls) == 1, (
            json.dumps(result["progress"])
        )
        assert result["research_archive"]["succeeded"] == 1
        assert result["progress"]["native_quant_auxiliary"]["total"] == 0
        assert [
            group["group"]
            for group in result["progress"]["research_enrichment"]["groups"]
        ] == ["B · Industry-Events"]
        prompt = model_calls[0]["user_prompt"].split(
            "\nREAD_ONLY_RESEARCH_CONTEXT=", 1
        )[0]
        evidence = json.loads(prompt.split("\n本组证据=", 1)[1])
        assert evidence["7_industry"]["documents"]
        assert evidence["15_events"]["documents"], (
            "native B group must reuse its stored companion documents"
        )
        assert write_scopes == [{"7_industry"}]
        raw_collector.assert_not_called()
        auxiliary.assert_not_called()
        with database.session() as session:
            repo = MarketDataRepository(session)
            for snapshot_id, content_hash, payload in original.values():
                row = repo.require_snapshot(snapshot_id)
                assert (row.content_hash, row.payload_json) == (content_hash, payload)
            rows = list(session.scalars(select(MarketDataSnapshot)))
            assert len(rows) == len(original) + 1
            assert (
                next(
                    row
                    for row in rows
                    if row.id not in {value[0] for value in original.values()}
                ).dimension
                == "7_industry"
            )
            tasks = list(
                session.scalars(
                    select(MarketDataCollectionTask).where(
                        MarketDataCollectionTask.job_id == job["id"]
                    )
                )
            )
            assert [task.dimension for task in tasks] == ["7_industry"]
    finally:
        coordinator.close()
        database.close()
