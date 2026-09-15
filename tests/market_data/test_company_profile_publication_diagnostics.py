"""Dependency guard diagnostics with real SQLite/publisher and no source calls."""

from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from research_system.api.dependencies import (
    ApiPrincipal,
    get_market_data_center,
    require_principal,
)
from research_system.api.errors import install_exception_handlers
from research_system.api.routes.market_data_center import router
from research_system.market_data.bundles import (
    BundlePublisher,
    CompanyProfileDependencyError,
)
from research_system.market_data.collection import MarketDataCollectionCoordinator
from research_system.market_data.company_profile_dependencies import (
    check_company_profile_dependencies,
)
from research_system.market_data.dimension_contracts import DEFAULT_DIMENSION_REGISTRY
from research_system.market_data.ingestion import SnapshotIngestionService
from research_system.market_data.models import MarketDataCollectionJob
from research_system.market_data.repository import MarketDataRepository
from research_system.market_data.service import MarketDataCenterService
from research_system.strategies.database import StrategyDatabase
from tests.market_data.test_company_profile_dependencies import (
    DIRECT,
    rebuild,
    receipt,
    revision,
    selected,
)
from tests.market_data.test_pit_core import AS_OF, NOW, _ingest_complete_set


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("publication diagnosis must not call sources or real models")

    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)


@pytest.fixture
def scenario(tmp_path):
    database = StrategyDatabase(f"sqlite:///{tmp_path / 'profile-publication.db'}")
    database.create_schema()

    def no_collector(**_kwargs):
        pytest.fail("publication checks must not collect")

    coordinator = MarketDataCollectionCoordinator(database, collector=no_collector)
    # Avoid constructing unrelated schedulers/providers: these are the actual
    # service methods with their real repository and collection coordinator.
    service = object.__new__(MarketDataCenterService)
    service.database = database
    service.collection = coordinator
    service.registry = DEFAULT_DIMENSION_REGISTRY
    with database.session() as session:
        repo, ingest = _ingest_complete_set(session)
        basic = revision(repo, ingest)
        state = check_company_profile_dependencies(selected(repo))
        assert state["status"] == "pending"
        job = MarketDataCollectionJob(
            tenant_id="tenant", user_id="user", requested_by="user",
            idempotency_key="profile-publication-fixture", job_type="historical_backfill",
            mode="missing_or_stale", status="succeeded",
            symbols_json=["600519.SH"], dimensions_json=list(DEFAULT_DIMENSION_REGISTRY.dimension_ids),
            start_date=AS_OF, end_date=AS_OF, max_concurrency=5,
            publish_when_complete=False, checkpoint_json={}, progress_json={},
            completed_at=NOW + timedelta(minutes=2),
        )
        session.add(job)
        session.flush()
        job_id, basic_id = job.id, basic.id
    app = FastAPI()
    install_exception_handlers(app)
    app.include_router(router, prefix="/api/v2")
    app.dependency_overrides[get_market_data_center] = lambda: service
    app.dependency_overrides[require_principal] = lambda: ApiPrincipal(
        actor_id="user", authenticated=True, tenant_id="tenant", user_id="user", roles=("admin",),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        yield SimpleNamespace(
            database=database, coordinator=coordinator, service=service,
            client=client, job_id=job_id, basic_id=basic_id, state=state,
        )
    coordinator.close()
    database.close()


def test_real_publisher_rejects_mixed_dependencies_without_native_critical(scenario):
    coverage = scenario.service.get_coverage(symbol="600519.SH", market="CN_EQUITY", as_of_date=AS_OF)
    assert coverage["report_generatable"] is True
    assert coverage["uzi_integrity"]["critical_count"] == 0
    with scenario.database.session() as session:
        with pytest.raises(CompanyProfileDependencyError) as caught:
            BundlePublisher(MarketDataRepository(session)).propose(
                symbol="600519.SH", market="CN_EQUITY", as_of_date=AS_OF,
                now=NOW + timedelta(minutes=2),
            )
        assert caught.value.company_profile_dependencies == scenario.state


def test_coverage_exposes_independent_dependency_pending_instead_of_only_native_ready(scenario):
    response = scenario.client.get("/api/v2/finance/market-data/coverage", params={
        "symbol": "600519.SH", "market": "CN_EQUITY", "as_of_date": AS_OF.isoformat(),
    })
    assert response.status_code == 200, response.text
    body = response.json()
    # Native criteria stay untouched; publication needs a separate result.
    assert body["report_generatable"] is True
    assert body["uzi_integrity"]["critical_count"] == 0
    assert body.get("company_profile_dependencies") == scenario.state


def test_candidate_failure_is_structured_in_terminal_job_without_losing_acquisition(scenario):
    assert scenario.coordinator._refresh_publication_candidate(scenario.job_id) is None
    response = scenario.client.get(f"/api/v2/finance/market-data/collection-jobs/{scenario.job_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "succeeded"  # Collection itself did not fail.
    assert body["can_confirm_freeze"] is False
    assert body["freeze_status"] == "blocked"
    assert body["checkpoint"]["publication"]["status"] == "blocked"
    assert body.get("company_profile_dependencies") == scenario.state


def test_compact_job_polling_preserves_independent_dependency_reason(scenario):
    scenario.coordinator._refresh_publication_candidate(scenario.job_id)
    response = scenario.client.get("/api/v2/finance/market-data/collection-jobs")
    assert response.status_code == 200, response.text
    body = next(row for row in response.json()["items"] if row["id"] == scenario.job_id)
    assert body["can_confirm_freeze"] is False
    assert body.get("company_profile_dependencies") == scenario.state


def test_publish_api_returns_structured_dependency_409_not_generic_invalid(scenario):
    response = scenario.client.post("/api/v2/finance/market-data/bundles/publish", json={
        "symbol": "600519.SH", "market": "CN_EQUITY", "as_of_date": AS_OF.isoformat(),
    })
    assert response.status_code == 409, response.text
    assert response.json()["error"].get("details", {}).get("company_profile_dependencies") == scenario.state


def test_confirm_freeze_rechecks_real_publisher_but_returns_409_not_500(scenario):
    response = scenario.client.post(
        f"/api/v2/finance/market-data/collection-jobs/{scenario.job_id}/confirm-freeze",
        json={"proposal_hash": "f" * 64, "acknowledge_degraded": True},
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"].get("details", {}).get("company_profile_dependencies") == scenario.state


def test_correctly_bound_failed_source_stays_a_quality_gap_not_dependency_pending(scenario):
    with scenario.database.session() as session:
        repo = MarketDataRepository(session)
        basic = repo.require_snapshot(scenario.basic_id)
        rebuild(repo, SnapshotIngestionService(repo), basic)
        rows = selected(repo)
        assert check_company_profile_dependencies(rows)["can_freeze"], check_company_profile_dependencies(rows)
        old = rows["7_industry"]
        # Detached equivalent of a newly stored failed source with a truthful
        # execution receipt. No database snapshots or old receipts are edited.
        failed = SimpleNamespace(**{
            key: deepcopy(getattr(old, key)) for key in (
                "id", "content_hash", "symbol", "market", "as_of_date", "exchange_timezone",
                "contract_version", "payload_json", "query_manifest_json", "source_version",
                "validation_json", "status", "dimension",
            )
        })
        failed.status = "data_gap"
        failed.query_manifest_json["native_quality_status"] = "data_gap"
        failed.payload_json["raw"] = {}
        failed.payload_json["_uzi_raw_result"] = {
            "data": {}, "error": "upstream_timeout", "_timeout": True,
            "source": "native-industry-fixture",
        }
        failed.query_manifest_json["company_profile_dependency_receipt"] = receipt(basic, failed)
        rows["7_industry"] = failed
        state = check_company_profile_dependencies(rows)
        assert state["required_dimensions"] == list(DIRECT)
        assert state["status"] == "ready" and state["pending_dimensions"] == []
        assert state["can_freeze"] is True
        # This asserts only dependency consistency, not overall/native approval.
        assert failed.status == "data_gap" and failed.payload_json["_uzi_raw_result"]["error"] == "upstream_timeout"
