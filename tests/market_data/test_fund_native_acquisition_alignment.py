"""Fund acquisition must not confuse native disclosure with a collected slot."""

from copy import deepcopy

import pytest
from sqlalchemy import select

from research_system.market_data.collection import MarketDataCollectionCoordinator
from research_system.market_data.contracts import SnapshotStatus
from research_system.market_data.fund_holding_collection import (
    FundHoldingCollectionCoordinator,
)
from research_system.market_data.ingestion import SnapshotIngestionService
from research_system.market_data.job_diagnostics import (
    fund_retryable_scope,
    summarize_fund_portfolio,
)
from research_system.market_data.models import (
    MarketDataCollectionJob,
    MarketDataCollectionTask,
    MarketDataSnapshot,
)
from research_system.market_data.repository import MarketDataRepository
from research_system.market_data.resolver import SnapshotResolver
from research_system.strategies.database import StrategyDatabase
from tests.market_data.test_fund_acquisition_gap_projection import (
    _actual_portfolio_shape,
)
from tests.market_data.test_fund_holding_collection import (
    HOLDING_SYMBOLS,
    _create_parent,
    _FundAdapter,
    _FundSkill,
    _TrackingEquityCollection,
)
from tests.market_data.test_fund_holding_collection import (
    harness as harness,  # noqa: PLC0414
)
from tests.market_data.test_pit_core import AS_OF, NOW, _input

DERIVATION_DIMENSIONS = ["4_peers", "14_moat", "18_trap"]


def test_first_wave_creates_thirty_slots_and_preserves_the_qualified_snapshots():
    database = StrategyDatabase("sqlite://")
    database.create_schema()
    equity = _TrackingEquityCollection(database)
    skill = _FundSkill()
    fund = FundHoldingCollectionCoordinator(
        database,
        equity_collection=equity,
        fund_snapshot_loader=skill,
        adapter=_FundAdapter(),
        clock=lambda: NOW,
    )
    try:
        with database.session() as session:
            ingestion = SnapshotIngestionService(MarketDataRepository(session))
            for symbol in HOLDING_SYMBOLS:
                for dimension in equity.registry.dimension_ids:
                    value = _input(dimension)
                    payload = dict(value.payload)
                    if dimension == "0_basic":
                        payload["symbol"] = symbol
                    gap = dimension in DERIVATION_DIMENSIONS
                    manifest = {
                        **value.query_manifest,
                        **(
                            {
                                "collection_outcome": "unavailable",
                                "native_quality_status": "partial",
                            }
                            if gap
                            else {}
                        ),
                    }
                    ingestion.ingest(
                        value.model_copy(
                            update={
                                "symbol": symbol,
                                "payload": payload,
                                "query_manifest": manifest,
                                "status": SnapshotStatus.DATA_GAP
                                if gap
                                else SnapshotStatus.AVAILABLE,
                            }
                        )
                    )
            before = {
                row.id: row.content_hash
                for row in session.scalars(select(MarketDataSnapshot))
            }
        parent = _create_parent(fund, as_of_date=AS_OF)
        fund.run_job_now(parent["id"])
        result = fund.get_job(parent["id"], tenant_id="tenant", user_id="user")
        assert len(skill.calls) == 1
        assert len(result["child_collection_job_ids"]) == 1
        with database.session() as session:
            child = session.get(
                MarketDataCollectionJob, result["child_collection_job_ids"][0]
            )
            assert child.mode == "missing_or_stale"
            assert set(child.dimensions_json) == set(DERIVATION_DIMENSIONS)
            tasks = list(
                session.scalars(
                    select(MarketDataCollectionTask).where(
                        MarketDataCollectionTask.job_id == child.id
                    )
                )
            )
            assert {(task.symbol, task.dimension) for task in tasks} == {
                (symbol, dimension)
                for symbol in HOLDING_SYMBOLS
                for dimension in DERIVATION_DIMENSIONS
            }
            after = {
                row.id: row.content_hash
                for row in session.scalars(
                    select(MarketDataSnapshot).where(MarketDataSnapshot.id.in_(before))
                )
            }
            assert (
                after == before
            )  # Both good and degraded historical inputs are immutable.
    finally:
        fund.close()
        equity.close()
        database.close()


@pytest.mark.parametrize("outcome", ["unavailable", None])
def test_fund_schedules_all_thirty_native_eligible_but_missing_slots(outcome):
    database = StrategyDatabase("sqlite://")
    database.create_schema()
    equity = MarketDataCollectionCoordinator(database)
    fund = FundHoldingCollectionCoordinator(database, equity_collection=equity)
    try:
        with database.session() as session:
            ingestion = SnapshotIngestionService(MarketDataRepository(session))
            for symbol in HOLDING_SYMBOLS:
                for dimension in DERIVATION_DIMENSIONS:
                    value = _input(dimension, status=SnapshotStatus.DATA_GAP)
                    manifest = {
                        **value.query_manifest,
                        "native_quality_status": "partial",
                    }
                    if outcome:
                        manifest["collection_outcome"] = outcome
                    ingestion.ingest(
                        value.model_copy(
                            update={"symbol": symbol, "query_manifest": manifest}
                        )
                    )
        for symbol in HOLDING_SYMBOLS:
            with database.session() as session:
                coverage = SnapshotResolver(MarketDataRepository(session)).coverage(
                    symbol,
                    "CN_EQUITY",
                    AS_OF,
                    now=NOW,
                )
                assert all(
                    item.eligible
                    for item in coverage.dimensions
                    if item.dimension in DERIVATION_DIMENSIONS
                )
            assert (
                fund._missing_equity_dimensions(symbol, AS_OF, DERIVATION_DIMENSIONS)
                == DERIVATION_DIMENSIONS
            )
            for dimension in DERIVATION_DIMENSIONS:
                assert equity._needs_provider(
                    {
                        "symbol": symbol,
                        "market": "CN_EQUITY",
                        "dimension": dimension,
                        "exchange_timezone": "Asia/Shanghai",
                    },
                    mode="missing_or_stale",
                    as_of_date=AS_OF,
                )
    finally:
        fund.close()
        equity.close()
        database.close()


def test_qualified_governance_audit_partial_is_reused_by_both_entrypoints():
    database = StrategyDatabase("sqlite://")
    database.create_schema()
    equity = MarketDataCollectionCoordinator(database)
    fund = FundHoldingCollectionCoordinator(database, equity_collection=equity)
    try:
        with database.session() as session:
            value = _input("11_governance")
            manifest = {**value.query_manifest, "collection_outcome": "partial"}
            row = SnapshotIngestionService(MarketDataRepository(session)).ingest(
                value.model_copy(update={"query_manifest": manifest})
            )
            assert row.status == "available"
        assert (
            fund._missing_equity_dimensions("600519.SH", AS_OF, ["11_governance"]) == []
        )
        assert not equity._needs_provider(
            {
                "symbol": "600519.SH",
                "market": "CN_EQUITY",
                "dimension": "11_governance",
                "exchange_timezone": "Asia/Shanghai",
            },
            mode="missing_or_stale",
            as_of_date=AS_OF,
        )
    finally:
        fund.close()
        equity.close()
        database.close()


def test_omitted_child_is_requestable_once_not_a_provider_failure():
    checkpoint, tasks, dimensions = _actual_portfolio_shape()
    tasks = [task for task in tasks if task["dimension"] not in DERIVATION_DIMENSIONS]
    unchanged = deepcopy(checkpoint)
    scope = fund_retryable_scope(checkpoint=checkpoint, child_tasks=tasks)
    assert {symbol: set(rows) for symbol, rows in scope.items()} == {
        symbol: {"3_macro", *DERIVATION_DIMENSIONS} for symbol in HOLDING_SYMBOLS
    }
    summary = summarize_fund_portfolio(
        checkpoint=checkpoint,
        child_tasks=tasks,
        dimensions_per_holding=len(dimensions),
        dimension_ids=dimensions,
    )
    assert summary["retryable_slots"] == 40
    assert summary["blocked_slots"] == 26
    assert summary["ready_slots"] == 154
    assert checkpoint == unchanged

    # An actual inputs-only attempt is not a missing task and cannot be
    # promoted to a network retry merely because the native gate allows it.
    tasks.extend(
        {
            "id": f"{symbol}:{dimension}",
            "job_id": "latest",
            "symbol": symbol,
            "dimension": dimension,
            "status": "partial",
            "retryable": False,
            "attempt_count": 1,
            "max_attempts": 3,
            "last_error_code": "normalization_or_derivation_missing",
        }
        for symbol in HOLDING_SYMBOLS
        for dimension in DERIVATION_DIMENSIONS
    )
    assert fund_retryable_scope(checkpoint=checkpoint, child_tasks=tasks) == {
        symbol: ["3_macro"] for symbol in HOLDING_SYMBOLS
    }


def test_absent_historical_current_only_and_unknown_slots_are_not_requestable():
    checkpoint = {
        "publication_candidate": {
            "bundles": [
                {
                    "asset_role": "holding",
                    "symbol": HOLDING_SYMBOLS[0],
                    "items": [
                        {"dimension": dimension, "status": "data_gap"}
                        for dimension in ["19_contests", "unknown"]
                    ],
                }
            ]
        }
    }
    assert fund_retryable_scope(checkpoint=checkpoint, child_tasks=[]) == {}


def test_legacy_missing_child_scoped_retry_reuses_holdings_and_forces_only_scope(
    harness,
):
    database, skill, _adapter, _equity, coordinator = harness
    parent = _create_parent(coordinator, as_of_date=coordinator._local_today())
    coordinator.run_job_now(parent["id"])
    symbol = HOLDING_SYMBOLS[0]
    with database.session() as session:
        job = session.get(MarketDataCollectionJob, parent["id"])
        checkpoint = dict(job.checkpoint_json)
        checkpoint["publication_candidate"] = {
            "bundles": [
                {
                    "asset_role": "holding",
                    "symbol": symbol,
                    "missing_dimensions": [],
                    "items": [
                        {"dimension": dimension, "status": "data_gap"}
                        for dimension in DERIVATION_DIMENSIONS
                    ],
                }
            ]
        }
        checkpoint["publication"] = {"status": "pending_confirmation"}
        job.checkpoint_json = checkpoint
        for task in session.scalars(
            select(MarketDataCollectionTask).where(
                MarketDataCollectionTask.job_id.in_(checkpoint["child_job_ids"]),
                MarketDataCollectionTask.symbol == symbol,
                MarketDataCollectionTask.dimension.in_(DERIVATION_DIMENSIONS),
            )
        ):
            session.delete(task)
    queued = coordinator.retry_failed(
        parent["id"],
        tenant_id="tenant",
        user_id="user",
        actor_id="actor",
        symbols=[symbol],
        dimensions=["14_moat"],
        submit=False,
    )
    assert queued["checkpoint"]["retry_scope_by_symbol"] == {symbol: ["14_moat"]}
    assert queued["can_confirm_freeze"] is False
    assert "publication_candidate" not in queued["checkpoint"]
    coordinator.run_job_now(parent["id"])
    result = coordinator.get_job(parent["id"], tenant_id="tenant", user_id="user")
    assert len(skill.calls) == 1
    with database.session() as session:
        child = session.get(
            MarketDataCollectionJob, result["child_collection_job_ids"][-1]
        )
        assert child.mode == "force_all"
        assert child.symbols_json == [symbol]
        assert child.dimensions_json == ["14_moat"]
