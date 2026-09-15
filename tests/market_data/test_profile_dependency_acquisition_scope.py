"""A native-usable old result is not a rebuilt profile consumer."""
from types import SimpleNamespace

from sqlalchemy import select

from research_system.market_data.collection import MarketDataCollectionCoordinator
from research_system.market_data.company_profile_dependencies import (
    check_company_profile_dependencies,
)
from research_system.market_data.fund_holding_collection import (
    FundHoldingCollectionCoordinator,
)
from research_system.market_data.ingestion import SnapshotIngestionService
from research_system.market_data.models import (
    MarketDataCollectionJob,
    MarketDataCollectionTask,
    MarketDataSnapshot,
)
from research_system.market_data.repository import MarketDataRepository
from research_system.market_data.resolver import SnapshotResolver
from research_system.market_data.service import MarketDataCenterService
from research_system.runtime.platform.settings import Settings
from tests.market_data.test_company_profile_rebuild import run
from tests.market_data.test_company_profile_rebuild import (
    state as state,  # noqa: PLC0414
)
from tests.market_data.test_fund_holding_collection import (
    HOLDING_SYMBOLS,
    _create_parent,
)
from tests.market_data.test_pit_core import AS_OF, NOW, _input


def selection(state):
    with state["database"].session() as session:
        repo = MarketDataRepository(session)
        coverage = SnapshotResolver(repo).coverage("600519.SH", "CN_EQUITY", AS_OF,
                                                  exchange_timezone="Asia/Shanghai", now=NOW)
        chosen = {row.dimension: repo.require_snapshot(row.snapshot_id)
                  for row in coverage.dimensions if row.snapshot_id}
        return check_company_profile_dependencies(chosen)


def test_missing_or_stale_dispatch_includes_usable_but_unbound_consumers(state):
    check = selection(state)
    assert "7_industry" in check["pending_dimensions"]
    coordinator = MarketDataCollectionCoordinator(state["database"])
    try:
        missing = coordinator.dimensions_needing_collection(
            symbol="600519.SH", market="CN_EQUITY", exchange_timezone="Asia/Shanghai",
            as_of_date=AS_OF, dimensions=["7_industry"], now=NOW)
        assert missing == ["7_industry"]
        # The caller's requested dimensions remain the sole authority.
        assert "10_valuation" not in missing and "0_basic" not in missing
        run(state)
        assert coordinator.dimensions_needing_collection(
            symbol="600519.SH", market="CN_EQUITY", exchange_timezone="Asia/Shanghai",
            as_of_date=AS_OF, dimensions=["7_industry"], now=NOW) == []
        with state["database"].session() as session:
            assert {row.id: row.content_hash for row in session.scalars(
                select(MarketDataSnapshot).where(MarketDataSnapshot.id.in_(state["originals"])))} == state["originals"]
    finally:
        coordinator.close()


def test_native_eligible_valuation_still_needs_profile_rebuild(state):
    with state["database"].session() as session:
        repo = MarketDataRepository(session)
        original = SnapshotIngestionService(repo).ingest(_input("10_valuation"))
        coverage = SnapshotResolver(repo).coverage("600519.SH", "CN_EQUITY", AS_OF,
                                                  exchange_timezone="Asia/Shanghai", now=NOW)
        item = next(row for row in coverage.dimensions if row.dimension == "10_valuation")
        assert item.snapshot_id == original.id and item.eligible
    assert "10_valuation" in selection(state)["pending_dimensions"]
    coordinator = MarketDataCollectionCoordinator(state["database"])
    try:
        assert coordinator.dimensions_needing_collection(
            symbol="600519.SH", market="CN_EQUITY", exchange_timezone="Asia/Shanghai",
            as_of_date=AS_OF, dimensions=["10_valuation"], now=NOW) == ["10_valuation"]
    finally:
        coordinator.close()


def test_fund_list_detail_and_manual_retry_agree_on_unbound_successful_and_unattempted_slots(state, tmp_path):
    equity = MarketDataCollectionCoordinator(state["database"])
    fund = FundHoldingCollectionCoordinator(state["database"], equity_collection=equity, clock=lambda: NOW)
    try:
        parent = _create_parent(fund, as_of_date=AS_OF)
        with state["database"].session() as session:
            repo = MarketDataRepository(session)
            SnapshotIngestionService(repo).ingest(_input("10_valuation"))
            job = session.get(MarketDataCollectionJob, parent["id"])
            job.status = "succeeded"
            job.checkpoint_json = {"holdings": [{"symbol": symbol} for symbol in ["600519.SH", *HOLDING_SYMBOLS[:9]]],
                "child_job_ids": [state["job_id"]], "child_missing_by_symbol": {}}
            child = session.get(MarketDataCollectionJob, state["job_id"])
            child.parent_job_id, child.tenant_id, child.user_id = job.id, "tenant", "user"
            for task in session.scalars(select(MarketDataCollectionTask).where(MarketDataCollectionTask.job_id == child.id)):
                task.status, task.retryable = "succeeded", False
        detail = fund.get_job(parent["id"], tenant_id="tenant", user_id="user")
        settings = Settings(workspace_root=tmp_path, **{key: tmp_path / key for key in (
            "provider_policy_path", "data_sources_path", "mcp_servers_path", "runs_root",
            "reports_root", "memory_root", "config_root", "domain_packs_root")})
        service = MarketDataCenterService(state["database"], settings,
            collection=equity, fund_collection=fund)
        listing = service.list_collection_jobs(tenant_id="tenant", user_id="user", asset_family="fund")
        listed = next(row for row in listing["items"] if row["id"] == parent["id"])
        assert detail["portfolio_summary"] == listed["portfolio_summary"]
        assert detail["portfolio_summary"]["retryable_slots"] >= 2
        assert detail["can_confirm_freeze"] is False
        retried = fund.retry_failed(parent["id"], tenant_id="tenant", user_id="user", actor_id="actor",
            symbols=["600519.SH"], dimensions=["7_industry", "10_valuation"], submit=False)
        assert retried["status"] == "queued"
        assert retried["checkpoint"]["retry_scope_by_symbol"] == {"600519.SH": ["7_industry", "10_valuation"]}
        assert "0_basic" not in retried["checkpoint"]["retry_scope_by_symbol"]["600519.SH"]
        with state["database"].session() as session:
            assert {key: session.get(MarketDataSnapshot, key).content_hash for key in state["originals"]} == state["originals"]
    finally:
        fund.close()
        equity.close()


def test_frozen_or_fixed_candidate_does_not_absorb_new_profile_gaps(state):
    for frozen in (False, True):
        checkpoint = {"holdings": [{"symbol": "600519.SH"}], "profile_dependency_gaps": {"stale": ["old"]},
            **({"publication": {"status": "published"}} if frozen else {"publication_candidate": {"proposal_hash": "fixed"}})}
        with state["database"].session() as session:
            projected = FundHoldingCollectionCoordinator.project_profile_dependency_gaps(session,
                SimpleNamespace(start_date=AS_OF), checkpoint, registry=equity_registry())
        assert "profile_dependency_gaps" not in projected
        assert checkpoint["profile_dependency_gaps"] == {"stale": ["old"]}


def equity_registry():
    from research_system.market_data.dimension_contracts import (
        DEFAULT_DIMENSION_REGISTRY,
    )
    return DEFAULT_DIMENSION_REGISTRY


def test_profile_polling_resolves_only_marked_stock_once_per_list(state, monkeypatch):
    calls = []
    original = SnapshotResolver.coverage
    def read(self, *args, **kwargs):
        calls.append(args[0])
        return original(self, *args, **kwargs)
    monkeypatch.setattr(SnapshotResolver, "coverage", read)
    cache = {}
    with state["database"].session() as session:
        project = FundHoldingCollectionCoordinator.project_profile_dependency_gaps
        # Ten legacy stocks cause one lightweight marker query, no payload resolution.
        legacy = {"holdings": [{"symbol": symbol} for symbol in HOLDING_SYMBOLS]}
        project(session, SimpleNamespace(start_date=AS_OF), legacy, registry=equity_registry(), scope_cache=cache)
        assert calls == []
        revised = {"holdings": [{"symbol": symbol} for symbol in ["600519.SH", *HOLDING_SYMBOLS[:9]]]}
        for _ in range(2):
            result = project(session, SimpleNamespace(start_date=AS_OF), revised,
                             registry=equity_registry(), scope_cache=cache)
            assert "10_valuation" in result["profile_dependency_gaps"]["600519.SH"]
    assert calls == ["600519.SH"]
