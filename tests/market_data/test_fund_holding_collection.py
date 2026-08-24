from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from threading import Event, Thread

import pytest
from sqlalchemy import select

from research_system.market_data.bundles import BundlePublisher
from research_system.market_data.collection import (
    CollectionJobError,
    MarketDataCollectionCoordinator,
)
from research_system.market_data.contracts import (
    CollectionStatus,
    EvidenceGrade,
    EvidenceInput,
    SnapshotIngestInput,
    SnapshotStatus,
    canonical_hash,
    cutoff_at_for_date,
)
from research_system.market_data.dimension_contracts import DEFAULT_DIMENSION_REGISTRY
from research_system.market_data.fund_holding_collection import (
    FUND_HOLDING_JOB_TYPE,
    FundHoldingCollectionCoordinator,
)
from research_system.market_data.fund_holdings_contracts import (
    FUND_HOLDINGS_CONTRACT_VERSION,
    FUND_TOP_HOLDINGS_REGISTRY,
    FundHoldingsLineage,
    FundTopHolding,
    FundTopHoldingsPayload,
)
from research_system.market_data.ingestion import SnapshotIngestionService
from research_system.market_data.models import (
    MarketDataBundle,
    MarketDataBundleItem,
    MarketDataCollectionJob,
    MarketDataCollectionTask,
    MarketDataCollectionTaskResult,
    MarketDataSnapshot,
    MarketDataSubscription,
)
from research_system.market_data.repository import MarketDataRepository
from research_system.strategies.database import StrategyDatabase

HOLDING_SYMBOLS = [f"600{index:03d}.SH" for index in range(1, 11)]


class _FundSkill:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def run(self, **kwargs):
        self.calls.append(dict(kwargs))
        return {"fixture": "fund_snapshot_skill_output"}


class _MutableClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current


class _AdvanceClockFundSkill(_FundSkill):
    def __init__(self, clock: _MutableClock, completed_at: datetime) -> None:
        super().__init__()
        self.clock = clock
        self.completed_at = completed_at

    def run(self, **kwargs):
        self.clock.current = self.completed_at
        return super().run(**kwargs)


class _FundAdapter:
    def __init__(self, *, holding_symbols: list[str] | None = None) -> None:
        self.calls: list[dict] = []
        self.holding_symbols = list(holding_symbols or HOLDING_SYMBOLS)

    def adapt(self, payload, **kwargs):
        self.calls.append({"payload": payload, **kwargs})
        as_of = kwargs["as_of_date"]
        observed_at = kwargs["observed_at"]
        disclosure_date = as_of
        holdings = tuple(
            FundTopHolding(
                rank=index,
                source_rank=index,
                symbol=symbol,
                code=symbol.split(".", 1)[0],
                name=f"fixture-{index}",
                exchange=symbol.rsplit(".", 1)[1],
                fund_weight_pct=1.0,
                disclosed_weight_pct=1.0,
                holding_source="direct_equity_disclosure",
                disclosure_date=disclosure_date,
            )
            for index, symbol in enumerate(self.holding_symbols, start=1)
        )
        fund_payload = FundTopHoldingsPayload(
            fund_code=kwargs["fund_code"],
            fund_name="测试权益基金",
            fund_type="股票型",
            asset_type="otc_fund",
            holding_source="direct_equity_disclosure",
            is_fund_of_funds=False,
            analysis_as_of_date=as_of,
            cutoff_at=cutoff_at_for_date(as_of, kwargs["exchange_timezone"]),
            disclosure_date=disclosure_date,
            availability_at=observed_at,
            holdings=holdings,
            lineage=(
                FundHoldingsLineage(
                    role="parent_holdings",
                    instrument_code=kwargs["fund_code"],
                    period_end=disclosure_date,
                    published_at=observed_at,
                    source_id="fixture.fund:holdings",
                    provider="fixture.fund",
                    publication_time_basis="observed_at_current_day",
                ),
            ),
        ).model_dump(mode="json", exclude_none=False)
        proof = {
            "query_window": {"start": as_of.isoformat(), "end": as_of.isoformat()},
            "required_sources": ["fixture.fund"],
            "sources": [
                {"source": "fixture.fund", "status": "success", "result_count": 10}
            ],
            "result_count": 10,
        }
        return SnapshotIngestInput(
            symbol=kwargs["fund_code"],
            market="CN_FUND",
            exchange_timezone=kwargs["exchange_timezone"],
            dimension="fund_top_holdings",
            contract_version=FUND_HOLDINGS_CONTRACT_VERSION,
            as_of_date=as_of,
            status=SnapshotStatus.AVAILABLE,
            payload=fund_payload,
            effective_at=observed_at,
            published_at=observed_at,
            observed_at=observed_at,
            ingested_at=observed_at,
            source_version="fixture-fund-v1",
            source_priority=10,
            origin="provider",
            query_manifest={
                "coverage": {
                    "pit_visibility": "observed_by_cutoff",
                    "asset_contract": "fund_top_holdings",
                    "selection_rule": "latest_disclosure_published_by_cutoff",
                    "holding_source": "direct_equity_disclosure",
                },
                "selection": {
                    "cutoff_at": cutoff_at_for_date(
                        as_of, kwargs["exchange_timezone"]
                    ).isoformat(),
                    "complete": True,
                    "selected_source_ids": ["fixture.fund:holdings"],
                    "candidates": [
                        {
                            "source_id": "fixture.fund:holdings",
                            "selected": True,
                            "reason": "latest_eligible_disclosure",
                        }
                    ],
                },
                "field_queries": {
                    "holdings": proof,
                    "lineage": {
                        **proof,
                        "sources": [
                            {
                                "source": "fixture.fund",
                                "status": "success",
                                "result_count": 1,
                            }
                        ],
                        "result_count": 1,
                    },
                },
            },
            validation={"adapter": "fixture"},
            field_sources={"$": "fixture.fund"},
            evidence=[
                EvidenceInput(
                    provider="fixture.fund",
                    content_hash=canonical_hash(fund_payload),
                    published_at=observed_at,
                    observed_at=observed_at,
                    grade=EvidenceGrade.A,
                    immutable_proof=True,
                    metadata={
                        "source_id": "fixture.fund:holdings",
                        "field_paths": ["holdings", "lineage"],
                        "immutable_content": True,
                    },
                )
            ],
            collection_task_id=kwargs.get("collection_task_id"),
            provider_attempt_id=kwargs.get("provider_attempt_id"),
        )


class _TrackingEquityCollection:
    """Persist real child jobs while replacing their provider executor in tests."""

    registry = DEFAULT_DIMENSION_REGISTRY

    def __init__(self, database: StrategyDatabase) -> None:
        self.database = database
        self.delegate = MarketDataCollectionCoordinator(database, max_background_jobs=1)
        self.available: dict[str, set[str]] = defaultdict(set)
        self.blocked_symbols: set[str] = set()
        self.run_calls: list[str] = []

    def create_job(self, **kwargs):
        return self.delegate.create_job(**kwargs)

    def run_job_now(self, job_id: str) -> None:
        self.run_calls.append(job_id)
        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            job = session.get(MarketDataCollectionJob, job_id)
            assert job is not None
            tasks = list(
                session.scalars(
                    select(MarketDataCollectionTask).where(
                        MarketDataCollectionTask.job_id == job_id
                    )
                )
            )
            for task in tasks:
                if task.symbol in self.blocked_symbols:
                    task.status = CollectionStatus.FAILED.value
                    task.retryable = True
                    task.last_error_code = "fixture_provider_gap"
                    continue
                task.status = CollectionStatus.SUCCEEDED.value
                task.retryable = False
                task.last_error_code = None
                self.available[task.symbol].add(task.dimension)
            succeeded = sum(task.status == CollectionStatus.SUCCEEDED.value for task in tasks)
            job.status = (
                CollectionStatus.SUCCEEDED.value
                if succeeded == len(tasks)
                else CollectionStatus.PARTIAL.value
                if succeeded
                else CollectionStatus.FAILED.value
            )
            job.progress_json = {"completed": len(tasks), "total": len(tasks)}
            job.started_at = job.started_at or now
            job.completed_at = now
            job.updated_at = now

    def get_job(self, job_id: str, **kwargs):
        return self.delegate.get_job(job_id, **kwargs)

    def cancel(self, job_id: str, **kwargs):
        return self.delegate.cancel(job_id, **kwargs)

    def close(self) -> None:
        self.delegate.close()


class _CrashBeforeProviderEquityCollection(_TrackingEquityCollection):
    """Persist a claimed child and emulate abrupt process death before I/O."""

    def run_job_now(self, job_id: str) -> None:
        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            job = session.get(MarketDataCollectionJob, job_id)
            assert job is not None
            job.status = CollectionStatus.RUNNING.value
            job.lease_owner = "dead-equity-worker"
            job.lease_until = now + timedelta(minutes=30)
            tasks = list(
                session.scalars(
                    select(MarketDataCollectionTask).where(
                        MarketDataCollectionTask.job_id == job_id
                    )
                )
            )
            for task in tasks:
                task.status = CollectionStatus.RUNNING.value
                task.attempt_count += 1
                task.lease_owner = "dead-equity-worker"
                task.lease_until = now + timedelta(minutes=30)
        raise KeyboardInterrupt("simulated process death before provider call")


class _BlockingEquityCollection(_TrackingEquityCollection):
    """Hold one active provider wave until parent cancellation reaches it."""

    def __init__(self, database: StrategyDatabase) -> None:
        super().__init__(database)
        self.started = Event()
        self.released = Event()
        self.cancel_calls: list[str] = []

    def run_job_now(self, job_id: str) -> None:
        self.run_calls.append(job_id)
        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            job = session.get(MarketDataCollectionJob, job_id)
            assert job is not None
            job.status = CollectionStatus.RUNNING.value
            job.lease_owner = "blocking-child-worker"
            job.lease_until = now + timedelta(minutes=30)
            tasks = list(
                session.scalars(
                    select(MarketDataCollectionTask).where(
                        MarketDataCollectionTask.job_id == job_id
                    )
                )
            )
            for task in tasks:
                task.status = CollectionStatus.RUNNING.value
                task.attempt_count += 1
                task.lease_owner = "blocking-child-worker"
                task.lease_until = now + timedelta(minutes=30)
        self.started.set()
        if not self.released.wait(timeout=5):
            raise TimeoutError("parent cancellation did not reach blocking child")

    def cancel(self, job_id: str, **kwargs):
        self.cancel_calls.append(job_id)
        result = super().cancel(job_id, **kwargs)
        self.released.set()
        return result


class _TestFundHoldingCoordinator(FundHoldingCollectionCoordinator):
    def _missing_equity_dimensions(self, symbol, as_of_date, requested_dimensions):
        del as_of_date
        available = self.equity_collection.available[symbol]
        return [dimension for dimension in requested_dimensions if dimension not in available]

    def _ensure_equity_bundle(self, symbol, as_of_date):
        del as_of_date
        if set(self.equity_collection.registry.dimension_ids).issubset(
            self.equity_collection.available[symbol]
        ):
            return type(
                "FixtureBundle",
                (),
                {
                    "id": f"bundle-{symbol}",
                    "manifest_hash": canonical_hash({"symbol": symbol}),
                },
            )()
        return None


@pytest.fixture()
def harness():
    database = StrategyDatabase("sqlite://")
    database.create_schema()
    skill = _FundSkill()
    adapter = _FundAdapter()
    equity = _TrackingEquityCollection(database)
    coordinator = _TestFundHoldingCoordinator(
        database,
        equity_collection=equity,
        fund_snapshot_loader=skill,
        adapter=adapter,
        registry=FUND_TOP_HOLDINGS_REGISTRY,
    )
    try:
        yield database, skill, adapter, equity, coordinator
    finally:
        coordinator.close()
        equity.close()
        database.close()


def _create_parent(coordinator, *, as_of_date=None):
    local_today = datetime.now(timezone.utc).astimezone().date()
    return coordinator.create_job(
        tenant_id="tenant",
        user_id="user",
        actor_id="actor",
        fund_code="020464",
        as_of_date=as_of_date or local_today,
        submit=False,
    )


def _seed_real_equity_snapshots(database, symbols: list[str]) -> tuple[datetime, date]:
    """Persist production-valid 22-slot snapshots without publishing Bundles."""

    from tests.market_data.test_pit_core import AS_OF, NOW, _input

    with database.session() as session:
        ingestion = SnapshotIngestionService(MarketDataRepository(session))
        for symbol in symbols:
            for dimension in DEFAULT_DIMENSION_REGISTRY.dimension_ids:
                value = _input(dimension)
                payload = dict(value.payload)
                if dimension == "0_basic":
                    payload["symbol"] = symbol
                persisted = ingestion.ingest(
                    value.model_copy(update={"symbol": symbol, "payload": payload})
                )
                assert persisted.status == SnapshotStatus.AVAILABLE.value
    return NOW, AS_OF


def test_current_day_publishes_one_fund_slot_and_expands_exact_top_ten(harness) -> None:
    database, skill, adapter, _equity, coordinator = harness
    today = coordinator._local_today()
    parent = _create_parent(coordinator, as_of_date=today)

    assert parent["job_type"] == FUND_HOLDING_JOB_TYPE
    assert parent["asset_family"] == "fund"
    assert parent["top_n"] == 10
    assert parent["dimensions"] == ["fund_top_holdings"]
    assert parent["tasks"][0]["market"] == "CN_FUND"

    coordinator.run_job_now(parent["id"])
    completed = coordinator.get_job(parent["id"], tenant_id="tenant", user_id="user")

    assert completed["status"] == "succeeded"
    assert len(skill.calls) == 1
    assert skill.calls[0]["fund_codes"] == ["020464"]
    assert len(adapter.calls) == 1
    assert len(completed["expanded_holdings"]) == 10
    assert {row["symbol"] for row in completed["expanded_holdings"]} == set(
        HOLDING_SYMBOLS
    )
    assert len(completed["child_collection_job_ids"]) == 1
    checkpoint = completed["checkpoint"]
    assert checkpoint["parent_snapshot_id"]
    assert checkpoint["parent_snapshot_hash"]
    assert checkpoint["parent_bundle_id"]
    assert checkpoint["parent_bundle_hash"]
    assert checkpoint["child_complete_count"] == 10

    with database.session() as session:
        bundle = session.get(MarketDataBundle, checkpoint["parent_bundle_id"])
        assert bundle is not None
        assert bundle.market == "CN_FUND"
        assert bundle.expected_dimension_count == 1
        items = list(
            session.scalars(
                select(MarketDataBundleItem).where(
                    MarketDataBundleItem.bundle_id == bundle.id
                )
            )
        )
        assert len(items) == 1
        child = session.get(
            MarketDataCollectionJob,
            completed["child_collection_job_ids"][0],
        )
        assert child is not None
        assert child.parent_job_id == parent["id"]
        assert child.mode == "missing_or_stale"
        assert child.symbols_json == HOLDING_SYMBOLS
        assert "020464" not in child.symbols_json
        assert len(child.dimensions_json) == 22
        child_tasks = list(
            session.scalars(
                select(MarketDataCollectionTask).where(
                    MarketDataCollectionTask.job_id == child.id
                )
            )
        )
        assert len(child_tasks) == 10 * 22


def test_fund_and_stock_may_share_six_digits_when_canonical_assets_differ() -> None:
    database = StrategyDatabase("sqlite://")
    database.create_schema()
    equity = _TrackingEquityCollection(database)
    stock_symbols = ["000001.SZ", *HOLDING_SYMBOLS[:9]]
    coordinator = _TestFundHoldingCoordinator(
        database,
        equity_collection=equity,
        fund_snapshot_loader=_FundSkill(),
        adapter=_FundAdapter(holding_symbols=stock_symbols),
        registry=FUND_TOP_HOLDINGS_REGISTRY,
    )
    try:
        parent = coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            fund_code="000001",
            as_of_date=coordinator._local_today(),
            submit=False,
        )
        coordinator.run_job_now(parent["id"])
        completed = coordinator.get_job(
            parent["id"], tenant_id="tenant", user_id="user"
        )

        assert completed["status"] == "succeeded"
        assert [
            holding["symbol"] for holding in completed["expanded_holdings"]
        ] == stock_symbols
        with database.session() as session:
            child = session.get(
                MarketDataCollectionJob,
                completed["child_collection_job_ids"][0],
            )
            assert child is not None
            assert "000001.SZ" in child.symbols_json
            assert "000001" not in child.symbols_json
    finally:
        coordinator.close()
        equity.close()
        database.close()


def test_complete_real_snapshots_publish_ten_stock_bundles_before_parent_success() -> None:
    database = StrategyDatabase("sqlite://")
    database.create_schema()
    equity = _TrackingEquityCollection(database)
    now, as_of = _seed_real_equity_snapshots(database, HOLDING_SYMBOLS)
    coordinator = FundHoldingCollectionCoordinator(
        database,
        equity_collection=equity,
        fund_snapshot_loader=_FundSkill(),
        adapter=_FundAdapter(),
        registry=FUND_TOP_HOLDINGS_REGISTRY,
        clock=lambda: now,
    )
    try:
        with database.session() as session:
            assert session.query(MarketDataBundle).count() == 0

        parent = _create_parent(coordinator, as_of_date=as_of)
        coordinator.run_job_now(parent["id"])
        completed = coordinator.get_job(
            parent["id"], tenant_id="tenant", user_id="user"
        )

        assert completed["status"] == "succeeded"
        assert completed["child_collection_job_ids"] == []
        assert equity.run_calls == []
        stock_bundles = completed["checkpoint"]["stock_bundles_by_symbol"]
        assert set(stock_bundles) == set(HOLDING_SYMBOLS)
        with database.session() as session:
            published = list(
                session.scalars(
                    select(MarketDataBundle).where(
                        MarketDataBundle.market == "CN_EQUITY",
                        MarketDataBundle.invalidated_at.is_(None),
                    )
                )
            )
            assert len(published) == 10
            for bundle in published:
                items = list(
                    session.scalars(
                        select(MarketDataBundleItem).where(
                            MarketDataBundleItem.bundle_id == bundle.id
                        )
                    )
                )
                assert len(items) == 22
    finally:
        coordinator.close()
        equity.close()
        database.close()


def test_unpublishable_stock_bundle_keeps_parent_partial_with_explicit_gap() -> None:
    database = StrategyDatabase("sqlite://")
    database.create_schema()
    equity = _TrackingEquityCollection(database)
    now, as_of = _seed_real_equity_snapshots(database, HOLDING_SYMBOLS)
    blocked_symbol = HOLDING_SYMBOLS[-1]
    with database.session() as session:
        repository = MarketDataRepository(session)
        invalidated = BundlePublisher(repository).publish(
            symbol=blocked_symbol,
            market="CN_EQUITY",
            as_of_date=as_of,
            cutoff_at=now,
            published_by="invalidated-fixture",
            now=now,
        )
        invalidated.status = "invalidated"
        invalidated.invalidated_at = now
        invalidated.invalidation_reason = "fixture integrity failure"

    coordinator = FundHoldingCollectionCoordinator(
        database,
        equity_collection=equity,
        fund_snapshot_loader=_FundSkill(),
        adapter=_FundAdapter(),
        registry=FUND_TOP_HOLDINGS_REGISTRY,
        clock=lambda: now,
    )
    try:
        parent = _create_parent(coordinator, as_of_date=as_of)
        coordinator.run_job_now(parent["id"])
        partial = coordinator.get_job(
            parent["id"], tenant_id="tenant", user_id="user"
        )

        assert partial["status"] == "partial"
        assert partial["checkpoint"]["child_complete_count"] == 9
        assert partial["checkpoint"]["child_missing_by_symbol"] == {
            blocked_symbol: ["published_bundle"]
        }
        assert blocked_symbol not in partial["checkpoint"]["stock_bundles_by_symbol"]
        assert equity.run_calls == []
        with database.session() as session:
            valid_stock_bundles = list(
                session.scalars(
                    select(MarketDataBundle).where(
                        MarketDataBundle.market == "CN_EQUITY",
                        MarketDataBundle.invalidated_at.is_(None),
                    )
                )
            )
            assert len(valid_stock_bundles) == 9
    finally:
        coordinator.close()
        equity.close()
        database.close()


def test_retry_reuses_parent_and_collects_only_the_remaining_child_gap(harness) -> None:
    database, skill, _adapter, equity, coordinator = harness
    today = coordinator._local_today()
    equity.blocked_symbols.add(HOLDING_SYMBOLS[-1])
    parent = _create_parent(coordinator, as_of_date=today)
    coordinator.run_job_now(parent["id"])
    partial = coordinator.get_job(parent["id"], tenant_id="tenant", user_id="user")

    assert partial["status"] == "partial"
    assert partial["checkpoint"]["child_complete_count"] == 9
    first_snapshot_id = partial["checkpoint"]["parent_snapshot_id"]
    first_bundle_id = partial["checkpoint"]["parent_bundle_id"]
    first_child_ids = list(partial["child_collection_job_ids"])
    assert len(first_child_ids) == 1
    assert len(skill.calls) == 1

    equity.blocked_symbols.clear()
    coordinator.retry_failed(
        parent["id"],
        tenant_id="tenant",
        user_id="user",
        actor_id="actor",
        submit=False,
    )
    coordinator.run_job_now(parent["id"])
    completed = coordinator.get_job(parent["id"], tenant_id="tenant", user_id="user")

    assert completed["status"] == "succeeded"
    assert len(skill.calls) == 1
    assert completed["checkpoint"]["parent_snapshot_id"] == first_snapshot_id
    assert completed["checkpoint"]["parent_bundle_id"] == first_bundle_id
    assert completed["checkpoint"]["child_complete_count"] == 10
    assert len(completed["child_collection_job_ids"]) == 2
    retry_child_id = completed["child_collection_job_ids"][-1]
    with database.session() as session:
        retry_child = session.get(MarketDataCollectionJob, retry_child_id)
        assert retry_child is not None
        assert retry_child.symbols_json == [HOLDING_SYMBOLS[-1]]
        assert len(retry_child.dimensions_json) == 22
        assert retry_child.parent_job_id == parent["id"]


def test_retry_of_the_same_full_gap_uses_a_new_persisted_generation(harness) -> None:
    database, _skill, _adapter, equity, coordinator = harness
    today = coordinator._local_today()
    equity.blocked_symbols.update(HOLDING_SYMBOLS)
    parent = _create_parent(coordinator, as_of_date=today)
    coordinator.run_job_now(parent["id"])
    partial = coordinator.get_job(parent["id"], tenant_id="tenant", user_id="user")

    assert partial["status"] == "partial"
    assert partial["checkpoint"]["child_complete_count"] == 0
    assert len(partial["child_collection_job_ids"]) == 1
    first_child_id = partial["child_collection_job_ids"][0]

    equity.blocked_symbols.clear()
    coordinator.retry_failed(
        parent["id"],
        tenant_id="tenant",
        user_id="user",
        actor_id="actor",
        submit=False,
    )
    coordinator.run_job_now(parent["id"])
    completed = coordinator.get_job(parent["id"], tenant_id="tenant", user_id="user")

    assert completed["status"] == "succeeded"
    assert len(completed["child_collection_job_ids"]) == 2
    second_child_id = completed["child_collection_job_ids"][-1]
    assert second_child_id != first_child_id
    with database.session() as session:
        first_child = session.get(MarketDataCollectionJob, first_child_id)
        second_child = session.get(MarketDataCollectionJob, second_child_id)
        assert first_child is not None and second_child is not None
        assert second_child.symbols_json == first_child.symbols_json
        assert second_child.dimensions_json == first_child.dimensions_json
        assert second_child.idempotency_key == f"{first_child.idempotency_key}:1"


def test_historical_day_without_stored_pit_never_calls_live_skill(harness) -> None:
    database, skill, _adapter, _equity, coordinator = harness
    historical = coordinator._local_today() - timedelta(days=30)
    parent = _create_parent(coordinator, as_of_date=historical)

    coordinator.run_job_now(parent["id"])
    failed = coordinator.get_job(parent["id"], tenant_id="tenant", user_id="user")

    assert failed["status"] == "failed"
    assert failed["tasks"][0]["error_code"] == "fund_historical_pit_unavailable"
    assert failed["tasks"][0]["retryable"] is False
    assert failed["child_collection_job_ids"] == []
    assert skill.calls == []
    with database.session() as session:
        result = session.scalar(
            select(MarketDataCollectionTaskResult).where(
                MarketDataCollectionTaskResult.task_id == failed["tasks"][0]["id"]
            )
        )
        assert result is not None
        assert result.status == "data_gap"
        assert result.provider_status == "pit_not_supported"
        assert result.error_code == "fund_historical_pit_unavailable"

    with pytest.raises(CollectionJobError) as captured:
        coordinator.retry_failed(
            parent["id"],
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            submit=False,
        )
    assert captured.value.code == "collection_retry_unavailable"


def test_live_fund_collection_crossing_shanghai_midnight_is_stored_next_day() -> None:
    before_midnight = datetime(2026, 8, 20, 15, 59, tzinfo=timezone.utc)
    after_midnight = datetime(2026, 8, 20, 16, 1, tzinfo=timezone.utc)
    clock = _MutableClock(before_midnight)
    database = StrategyDatabase("sqlite://")
    database.create_schema()
    equity = _TrackingEquityCollection(database)
    skill = _AdvanceClockFundSkill(clock, after_midnight)
    coordinator = _TestFundHoldingCoordinator(
        database,
        equity_collection=equity,
        fund_snapshot_loader=skill,
        adapter=_FundAdapter(),
        registry=FUND_TOP_HOLDINGS_REGISTRY,
        clock=clock,
    )
    try:
        # Use the market timezone explicitly; the host timezone is not part of
        # the PIT contract.
        previous_day = coordinator._local_today()
        parent = _create_parent(coordinator, as_of_date=previous_day)

        coordinator.run_job_now(parent["id"])
        failed = coordinator.get_job(
            parent["id"], tenant_id="tenant", user_id="user"
        )

        assert failed["status"] == "failed"
        assert failed["tasks"][0]["error_code"] == "fund_collection_crossed_market_day"
        assert failed["tasks"][0]["retryable"] is False
        assert failed["child_collection_job_ids"] == []
        assert len(skill.calls) == 1
        with database.session() as session:
            snapshots = list(
                session.scalars(
                    select(MarketDataSnapshot).where(
                        MarketDataSnapshot.symbol == "020464",
                        MarketDataSnapshot.market == "CN_FUND",
                    )
                )
            )
            assert len(snapshots) == 1
            snapshot = snapshots[0]
            assert snapshot.as_of_date == previous_day + timedelta(days=1)
            assert snapshot.observed_at.replace(tzinfo=timezone.utc) == after_midnight
            assert snapshot.ingested_at.replace(tzinfo=timezone.utc) == after_midnight
            assert snapshot.as_of_date != previous_day
    finally:
        coordinator.close()
        equity.close()
        database.close()


def test_historical_day_reuses_an_existing_fund_pit_snapshot_without_live(harness) -> None:
    database, skill, adapter, equity, coordinator = harness
    historical = coordinator._local_today() - timedelta(days=30)
    observed_at = datetime.combine(
        historical,
        datetime.min.time(),
        tzinfo=timezone.utc,
    ) + timedelta(hours=4)
    ingest = adapter.adapt(
        {"fixture": "persisted"},
        fund_code="020464",
        as_of_date=historical,
        exchange_timezone="Asia/Shanghai",
        top_n=10,
        observed_at=observed_at,
        collection_task_id=None,
        provider_attempt_id="seed",
    )
    with database.session() as session:
        persisted = SnapshotIngestionService(
            MarketDataRepository(session), registry=FUND_TOP_HOLDINGS_REGISTRY
        ).ingest(ingest)
        assert persisted.status == "available"
    for symbol in HOLDING_SYMBOLS:
        equity.available[symbol].update(DEFAULT_DIMENSION_REGISTRY.dimension_ids)

    parent = _create_parent(coordinator, as_of_date=historical)
    coordinator.run_job_now(parent["id"])
    completed = coordinator.get_job(parent["id"], tenant_id="tenant", user_id="user")

    assert completed["status"] == "succeeded"
    assert completed["checkpoint"]["parent_snapshot_id"] == persisted.id
    assert completed["child_collection_job_ids"] == []
    assert skill.calls == []


def test_parent_requires_the_complete_equity_contract(harness) -> None:
    _database, _skill, _adapter, _equity, coordinator = harness
    with pytest.raises(CollectionJobError) as captured:
        coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            fund_code="020464",
            as_of_date=coordinator._local_today(),
            equity_dimensions=["0_basic"],
            submit=False,
        )
    assert captured.value.code == "fund_equity_contract_partial"

    with pytest.raises(CollectionJobError) as publication_error:
        coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            fund_code="020464",
            as_of_date=coordinator._local_today(),
            publish_when_complete=False,
            submit=False,
        )
    assert publication_error.value.code == "fund_bundle_publication_required"


@pytest.mark.parametrize("max_concurrency", (0, 6))
def test_parent_rejects_concurrency_outside_one_to_five(
    harness, max_concurrency
) -> None:
    _database, _skill, _adapter, _equity, coordinator = harness
    with pytest.raises(CollectionJobError) as captured:
        coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            fund_code="020464",
            as_of_date=coordinator._local_today(),
            max_concurrency=max_concurrency,
            submit=False,
        )
    assert captured.value.code == "collection_concurrency_invalid"


@pytest.mark.parametrize(
    ("subscription_enabled", "resume_subscription_jobs"),
    ((True, False), (False, True)),
)
def test_subscription_opt_out_cancels_parent_and_linked_active_children(
    harness, subscription_enabled, resume_subscription_jobs,
) -> None:
    database, skill, _adapter, equity, coordinator = harness
    today = coordinator._local_today()
    with database.session() as session:
        subscription = MarketDataSubscription(
            tenant_id="tenant",
            user_id="user",
            symbol="020464",
            market="CN_FUND",
            exchange_timezone="Asia/Shanghai",
            enabled=subscription_enabled,
            dimensions_json=["fund_top_holdings"],
            schedule_json={"local_time": "23:15", "timezone": "Asia/Shanghai"},
            max_concurrency=5,
        )
        session.add(subscription)
        session.flush()
        subscription_id = subscription.id

    parent = coordinator.create_job(
        tenant_id="tenant",
        user_id="user",
        actor_id="market_data_subscription",
        symbols=["020464"],
        start_date=today,
        end_date=today,
        dimensions=["fund_top_holdings"],
        origin="subscription",
        subscription_id=subscription_id,
        submit=False,
    )

    assert parent["origin"] == "subscription"
    assert parent["subscription_id"] == subscription_id
    assert parent["symbols"] == ["020464"]
    assert parent["start_date"] == parent["end_date"] == today.isoformat()
    child = equity.create_job(
        tenant_id="tenant",
        user_id="user",
        actor_id="market_data_subscription",
        symbols=[HOLDING_SYMBOLS[0]],
        start_date=today,
        end_date=today,
        dimensions=["0_basic"],
        origin="subscription",
        subscription_id=subscription_id,
        parent_job_id=parent["id"],
        submit=False,
    )
    with database.session() as session:
        child_row = session.get(MarketDataCollectionJob, child["id"])
        child_task = session.scalar(
            select(MarketDataCollectionTask).where(
                MarketDataCollectionTask.job_id == child["id"]
            )
        )
        child_row.status = CollectionStatus.RUNNING.value
        child_row.lease_owner = "dead-child-worker"
        child_task.status = CollectionStatus.RUNNING.value
        child_task.lease_owner = "dead-child-worker"

    assert (
        coordinator.resume_pending(
            resume_subscription_jobs=resume_subscription_jobs
        )
        == []
    )
    cancelled = coordinator.get_job(
        parent["id"], tenant_id="tenant", user_id="user"
    )
    assert cancelled["status"] == "cancelled"
    assert cancelled["cancel_requested"] is True
    assert cancelled["tasks"][0]["status"] == "cancelled"
    assert skill.calls == []
    with database.session() as session:
        child_row = session.get(MarketDataCollectionJob, child["id"])
        child_task = session.scalar(
            select(MarketDataCollectionTask).where(
                MarketDataCollectionTask.job_id == child["id"]
            )
        )
        assert child_row.status == "cancelled"
        assert child_row.cancel_requested is True
        assert child_row.lease_owner is None
        assert child_row.lease_until is None
        assert child_row.completed_at is not None
        assert child_task.status == "cancelled"
        assert child_task.retryable is False
        assert child_task.lease_owner is None
        assert child_task.lease_until is None
        assert child_task.completed_at is not None


def test_subscription_compatible_shape_rejects_multi_symbol_or_date_range(harness) -> None:
    _database, _skill, _adapter, _equity, coordinator = harness
    today = coordinator._local_today()
    with pytest.raises(CollectionJobError) as symbols_error:
        coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["020464", "020465"],
            start_date=today,
            end_date=today,
            dimensions=["fund_top_holdings"],
            submit=False,
        )
    assert symbols_error.value.code == "fund_collection_symbol_count_invalid"

    with pytest.raises(CollectionJobError) as range_error:
        coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["020464"],
            start_date=today - timedelta(days=1),
            end_date=today,
            dimensions=["fund_top_holdings"],
            submit=False,
        )
    assert range_error.value.code == "fund_collection_date_range_invalid"


def test_restart_reclaims_checkpointed_child_once_without_duplicate_provider_wave() -> None:
    database = StrategyDatabase("sqlite://")
    database.create_schema()
    skill = _FundSkill()
    adapter = _FundAdapter()
    crashed_equity = _CrashBeforeProviderEquityCollection(database)
    crashed_parent = _TestFundHoldingCoordinator(
        database,
        equity_collection=crashed_equity,
        fund_snapshot_loader=skill,
        adapter=adapter,
        registry=FUND_TOP_HOLDINGS_REGISTRY,
    )
    restarted_equity = None
    restarted_parent = None
    try:
        parent = _create_parent(
            crashed_parent,
            as_of_date=crashed_parent._local_today(),
        )
        with pytest.raises(KeyboardInterrupt, match="simulated process death"):
            crashed_parent.run_job_now(parent["id"])

        crashed = crashed_parent.get_job(
            parent["id"], tenant_id="tenant", user_id="user"
        )
        assert crashed["status"] == "running"
        assert len(crashed["child_collection_job_ids"]) == 1
        child_id = crashed["child_collection_job_ids"][0]
        with database.session() as session:
            child = session.get(MarketDataCollectionJob, child_id)
            assert child is not None
            assert child.status == "running"
            assert child.parent_job_id == parent["id"]

        # A new process starts both coordinators in the production order.  The
        # generic equity pass must not submit the parent-owned child.
        restarted_equity = _TrackingEquityCollection(database)
        restarted_parent = _TestFundHoldingCoordinator(
            database,
            equity_collection=restarted_equity,
            fund_snapshot_loader=skill,
            adapter=adapter,
            registry=FUND_TOP_HOLDINGS_REGISTRY,
        )
        generic_submissions: list[str] = []
        restarted_equity.delegate.submit = generic_submissions.append  # type: ignore[method-assign]
        assert restarted_equity.delegate.resume_pending() == []
        assert generic_submissions == []

        restarted_parent.submit = restarted_parent.run_job_now  # type: ignore[method-assign]
        assert restarted_parent.resume_pending() == [parent["id"]]

        completed = restarted_parent.get_job(
            parent["id"], tenant_id="tenant", user_id="user"
        )
        assert completed["status"] == "succeeded"
        assert completed["child_collection_job_ids"] == [child_id]
        assert restarted_equity.run_calls == [child_id]
        assert len(skill.calls) == 1
        with database.session() as session:
            children = list(
                session.scalars(
                    select(MarketDataCollectionJob).where(
                        MarketDataCollectionJob.parent_job_id == parent["id"]
                    )
                )
            )
            assert [child.id for child in children] == [child_id]
    finally:
        crashed_parent.close()
        crashed_equity.close()
        if restarted_parent is not None:
            restarted_parent.close()
        if restarted_equity is not None:
            restarted_equity.close()
        database.close()


def test_runtime_parent_cancel_stops_active_child_and_cannot_finish_succeeded() -> None:
    database = StrategyDatabase("sqlite://")
    database.create_schema()
    equity = _BlockingEquityCollection(database)
    coordinator = _TestFundHoldingCoordinator(
        database,
        equity_collection=equity,
        fund_snapshot_loader=_FundSkill(),
        adapter=_FundAdapter(),
        registry=FUND_TOP_HOLDINGS_REGISTRY,
    )
    try:
        parent = _create_parent(
            coordinator,
            as_of_date=coordinator._local_today(),
        )
        worker = Thread(
            target=coordinator.run_job_now,
            args=(parent["id"],),
            daemon=True,
        )
        worker.start()
        assert equity.started.wait(timeout=5), "child provider wave did not start"

        coordinator.cancel(
            parent["id"],
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
        )
        worker.join(timeout=5)

        assert not worker.is_alive()
        cancelled = coordinator.get_job(
            parent["id"], tenant_id="tenant", user_id="user"
        )
        assert cancelled["status"] == "cancelled"
        assert cancelled["cancel_requested"] is True
        assert cancelled["tasks"][0]["status"] == "cancelled"
        assert len(cancelled["child_collection_job_ids"]) == 1
        child_id = cancelled["child_collection_job_ids"][0]
        assert equity.run_calls == [child_id]
        assert equity.cancel_calls == [child_id]

        with database.session() as session:
            child = session.get(MarketDataCollectionJob, child_id)
            tasks = list(
                session.scalars(
                    select(MarketDataCollectionTask).where(
                        MarketDataCollectionTask.job_id == child_id
                    )
                )
            )
            assert child is not None
            assert child.status == "cancelled"
            assert child.cancel_requested is True
            assert child.lease_owner is None
            assert child.lease_until is None
            assert tasks
            assert {task.status for task in tasks} == {"cancelled"}
            assert all(task.lease_owner is None for task in tasks)
            assert (
                session.scalar(
                    select(MarketDataBundle).where(
                        MarketDataBundle.market == "CN_EQUITY"
                    )
                )
                is None
            )
    finally:
        equity.released.set()
        coordinator.close()
        equity.close()
        database.close()
