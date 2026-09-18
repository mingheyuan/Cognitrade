from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from threading import Barrier, Event, Thread
from unittest.mock import patch

import pytest
from sqlalchemy import delete, select, update

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
    _bundle_proposal_requires_degraded_ack,
)
from research_system.market_data.fund_holdings_adapter import (
    FundTopHoldingsSnapshotAdapter,
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
    MarketDataSnapshotEvidence,
    MarketDataSubscription,
)
from research_system.market_data.repository import MarketDataRepository
from research_system.strategies.database import StrategyDatabase

HOLDING_SYMBOLS = [f"600{index:03d}.SH" for index in range(1, 11)]


def test_degraded_freeze_ack_tracks_quality_and_disclosed_gaps() -> None:
    assert not _bundle_proposal_requires_degraded_ack(
        {"degraded": False, "quality": "strict", "missing_dimensions": []}
    )
    assert _bundle_proposal_requires_degraded_ack(
        {
            "degraded": False,
            "quality": "degraded",
            "missing_dimensions": ["3_macro"],
        }
    )
    assert _bundle_proposal_requires_degraded_ack(
        {
            "degraded": False,
            "quality": "strict",
            "missing_dimensions": ["19_contests"],
        }
    )


class _FundSkill:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def run(self, **kwargs):
        self.calls.append(dict(kwargs))
        return {"fixture": "fund_snapshot_skill_output"}


class _HistoricalFundSkill:
    def __init__(self, *, provider_failure: bool = False) -> None:
        self.calls: list[dict] = []
        self.provider_failure = provider_failure

    def run_holdings_as_of(self, **kwargs):
        self.calls.append(dict(kwargs))
        as_of = kwargs["as_of_date"]
        if self.provider_failure:
            return {
                "funds": [
                    {
                        "fund_code": kwargs["fund_code"],
                        "fund_name": "测试权益基金",
                        "fund_type": "股票型",
                        "top_holdings": [],
                        "holdings_period": None,
                        "holdings_coverage": {"is_fund_of_funds": False},
                    }
                ],
                "quality_status": "degraded",
                "data_gap": ["historical_top10_holdings_unavailable"],
                "provider_errors": [
                    {
                        "stage": "fund_reports:020464",
                        "error_type": "TimeoutError",
                        "retryable": True,
                    }
                ],
                "source_refs": [],
                "historical_reconstruction": {
                    "requested_as_of_date": as_of.isoformat(),
                    "retryable": True,
                },
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
            }
        disclosure = as_of - timedelta(days=90)
        published = as_of - timedelta(days=30)
        return {
            "funds": [
                {
                    "fund_code": kwargs["fund_code"],
                    "fund_name": "测试权益基金",
                    "fund_type": "股票型",
                    "top_holdings": [
                        {
                            "rank": index,
                            "code": symbol.split(".", 1)[0],
                            "name": f"fixture-{index}",
                            "weight_pct": 1.0,
                        }
                        for index, symbol in enumerate(HOLDING_SYMBOLS, start=1)
                    ],
                    "holdings_period": disclosure.isoformat(),
                    "holdings_published_at": published.isoformat(),
                    "holdings_coverage": {"is_fund_of_funds": False},
                }
            ],
            "quality_status": "available",
            "data_gap": [],
            "provider_errors": [],
            "source_refs": [
                {
                    "type": "fund_direct_equity_holdings",
                    "provider": "official.fixture",
                    "id": "https://pdf.dfcfw.com/pdf/H2_AN_FIXTURE_1.pdf",
                    "content_ref": "https://pdf.dfcfw.com/pdf/H2_AN_FIXTURE_1.pdf",
                    "content_hash": "a" * 64,
                    "immutable_proof": True,
                    "published_at": published.isoformat(),
                }
            ],
            "historical_reconstruction": {
                "requested_as_of_date": as_of.isoformat(),
                "retryable": False,
            },
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
        }


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


class _BlockingFundSkill(_FundSkill):
    def __init__(self) -> None:
        super().__init__()
        self.started = Event()
        self.released = Event()

    def run(self, **kwargs):
        self.started.set()
        if not self.released.wait(timeout=5):
            raise TimeoutError("fund provider fixture was not released")
        return super().run(**kwargs)


class _CancellationAwareFundSkill(_FundSkill):
    def __init__(self) -> None:
        super().__init__()
        self.started = Event()
        self.cancelled = Event()

    def run(self, *, cancellation, **kwargs):
        self.started.set()
        if not cancellation.wait(timeout=5):
            raise TimeoutError("durable fund cancellation was not delivered")
        self.cancelled.set()
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
            effective_at=datetime.combine(
                disclosure_date,
                datetime.min.time(),
                tzinfo=timezone.utc,
            ),
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

        def unexpected_provider(**kwargs):
            raise AssertionError("fund fixture must not invoke a live provider")

        self.delegate = MarketDataCollectionCoordinator(
            database, collector=unexpected_provider, max_background_jobs=1
        )
        self.available: dict[str, set[str]] = defaultdict(set)
        self.blocked_symbols: set[str] = set()
        self.run_calls: list[str] = []
        self.agent_recovery_calls: list[str] = []
        self.resume_agent_recovery_calls: list[list[str]] = []

    def create_job(self, **kwargs):
        return self.delegate.create_job(**kwargs)

    def dimensions_needing_collection(self, **kwargs):
        return self.delegate.dimensions_needing_collection(**kwargs)

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
            succeeded = sum(
                task.status == CollectionStatus.SUCCEEDED.value for task in tasks
            )
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

    def start_agent_recovery(self, job_id: str, **kwargs):
        del kwargs
        self.agent_recovery_calls.append(job_id)
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
                self.available[task.symbol].add(task.dimension)
                task.status = CollectionStatus.SUCCEEDED.value
                task.retryable = False
                task.last_error_code = None
                task.last_error_message = None
                task.completed_at = now
            checkpoint = dict(job.checkpoint_json or {})
            checkpoint["agent_recovery"] = {
                "status": "succeeded",
                "attempted": len(tasks),
                "succeeded": len(tasks),
                "failed": 0,
                "completed_at": now.isoformat(),
            }
            job.checkpoint_json = checkpoint
            job.status = CollectionStatus.SUCCEEDED.value
            job.completed_at = now
        return self.get_job(job_id, tenant_id="tenant", user_id="user")

    def resume_agent_recovery_jobs(self, job_ids) -> int:
        normalized = list(dict.fromkeys(str(job_id) for job_id in job_ids))
        self.resume_agent_recovery_calls.append(normalized)
        resumed = 0
        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            for job_id in normalized:
                job = session.get(MarketDataCollectionJob, job_id)
                if job is None:
                    continue
                checkpoint = dict(job.checkpoint_json or {})
                recovery = dict(checkpoint.get("agent_recovery") or {})
                if recovery.get("status") == "cancelling":
                    recovery.update(
                        {
                            "status": "cancelled",
                            "completed_at": now.isoformat(),
                        }
                    )
                elif recovery.get("status") == "running":
                    recovery.update(
                        {
                            "status": "queued",
                            "resumed_at": now.isoformat(),
                        }
                    )
                    resumed += 1
                elif recovery.get("status") == "queued":
                    resumed += 1
                else:
                    continue
                checkpoint["agent_recovery"] = recovery
                job.checkpoint_json = checkpoint
        return resumed

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


class _DelegateAgentResumeEquityCollection(_TrackingEquityCollection):
    """Exercise the production child-resume helper with deterministic recovery."""

    resume_agent_recovery_jobs = None

    def __init__(self, database: StrategyDatabase) -> None:
        super().__init__(database)
        self.delegate_resume_submit_calls: list[str] = []
        self.delegate._submit_agent_recovery = self._complete_resumed_recovery  # type: ignore[method-assign]

    def _complete_resumed_recovery(self, job_id: str) -> None:
        self.delegate_resume_submit_calls.append(job_id)
        self.start_agent_recovery(job_id)


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


class _CompletingBlockingEquityCollection(_TrackingEquityCollection):
    """Keep a real child lease live, then complete the one provider wave."""

    def __init__(self, database: StrategyDatabase) -> None:
        super().__init__(database)
        self.started = Event()
        self.released = Event()

    def run_job_now(self, job_id: str) -> None:
        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            job = session.get(MarketDataCollectionJob, job_id)
            assert job is not None
            job.status = CollectionStatus.RUNNING.value
            job.lease_owner = "live-child-worker"
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
                task.lease_owner = "live-child-worker"
                task.lease_until = job.lease_until
        self.started.set()
        if not self.released.wait(timeout=5):
            raise TimeoutError("live child fixture was not released")
        super().run_job_now(job_id)


class _BlockingAgentEquityCollection(_TrackingEquityCollection):
    """Pause a child recovery so a second parent owner can reclaim its lease."""

    def __init__(self, database: StrategyDatabase) -> None:
        super().__init__(database)
        self.agent_started = Event()
        self.agent_released = Event()

    def start_agent_recovery(self, job_id: str, **kwargs):
        self.agent_started.set()
        if not self.agent_released.wait(timeout=5):
            raise TimeoutError("fund recovery fencing fixture was not released")
        return super().start_agent_recovery(job_id, **kwargs)


class _TestFundHoldingCoordinator(FundHoldingCollectionCoordinator):
    def _missing_equity_dimensions(self, symbol, as_of_date, requested_dimensions):
        del as_of_date
        available = self.equity_collection.available[symbol]
        return [
            dimension
            for dimension in requested_dimensions
            if dimension not in available
        ]

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


def _wait_for_parent_recovery(coordinator, job_id: str) -> dict:
    for _ in range(200):
        payload = coordinator.get_job(job_id, tenant_id="tenant", user_id="user")
        status = str(dict(payload.get("agent_recovery") or {}).get("status") or "")
        if status not in {"queued", "running", "cancelling"}:
            return payload
        Event().wait(0.01)
    raise AssertionError("parent Agent recovery did not reach a durable terminal state")


def test_parent_completion_and_candidate_are_committed_together(harness, monkeypatch):
    _database, _skill, _adapter, _equity, coordinator = harness
    parent = _create_parent(coordinator, as_of_date=coordinator._local_today())
    original = coordinator._complete_parent
    observations = []

    def complete(*args, **kwargs):
        result = original(*args, **kwargs)
        state = coordinator.get_job(parent["id"], tenant_id="tenant", user_id="user")
        observations.append(state)
        # Observe immediately after the terminal transaction, before the
        # worker can execute any following publication/cleanup statement.
        assert state["status"] == "succeeded"
        assert state["publication_candidate"] is not None
        assert state["can_confirm_freeze"]
        return result

    monkeypatch.setattr(coordinator, "_complete_parent", complete)
    coordinator.run_job_now(parent["id"])
    assert len(observations) == 1


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


def test_current_day_persists_fund_draft_and_expands_exact_top_ten(harness) -> None:
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
    assert checkpoint.get("parent_bundle_id") is None
    assert checkpoint.get("parent_bundle_hash") is None
    assert checkpoint["child_complete_count"] == 10

    with database.session() as session:
        assert session.query(MarketDataBundle).count() == 0
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


def test_idempotency_replay_returns_existing_parent_without_resubmitting(
    harness,
) -> None:
    _database, _skill, _adapter, _equity, coordinator = harness
    with patch.object(coordinator, "submit") as submit:
        first = coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            fund_code="020464",
            as_of_date=coordinator._local_today(),
            idempotency_key="fund-idempotency-fixture",
        )
        replay = coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="other-actor",
            fund_code="020464",
            as_of_date=coordinator._local_today(),
            idempotency_key="fund-idempotency-fixture",
        )

    assert replay["id"] == first["id"]
    submit.assert_called_once_with(first["id"])


@pytest.mark.parametrize(
    "changed",
    [
        {"fund_code": "020465"},
        {"as_of_date_delta": -1},
        {"mode": "force_all"},
        {"max_concurrency": 3},
        {"publish_when_complete": True},
        {"investment_cycle": "short_term"},
    ],
)
def test_idempotency_key_rejects_a_different_normalized_fund_request(
    harness,
    changed,
) -> None:
    _database, _skill, _adapter, _equity, coordinator = harness
    today = coordinator._local_today()
    coordinator.create_job(
        tenant_id="tenant",
        user_id="user",
        actor_id="actor",
        fund_code="020464",
        as_of_date=today,
        idempotency_key="fund-request-fingerprint",
        submit=False,
    )
    retry = {
        "tenant_id": "tenant",
        "user_id": "user",
        "actor_id": "other-actor",
        "fund_code": "020464",
        "as_of_date": today,
        "idempotency_key": "fund-request-fingerprint",
        "submit": False,
    }
    delta = changed.get("as_of_date_delta")
    if delta is not None:
        retry["as_of_date"] = today + timedelta(days=delta)
    retry.update(
        {key: value for key, value in changed.items() if key != "as_of_date_delta"}
    )

    with pytest.raises(CollectionJobError) as conflict:
        coordinator.create_job(**retry)

    assert conflict.value.code == "collection_idempotency_conflict"
    assert conflict.value.status_code == 409


def test_fund_idempotency_key_rejects_cross_equity_job_collision(harness) -> None:
    _database, _skill, _adapter, equity, coordinator = harness
    today = coordinator._local_today()
    equity.create_job(
        tenant_id="tenant",
        user_id="user",
        actor_id="actor",
        symbols=["600001.SH"],
        start_date=today,
        end_date=today,
        dimensions=["0_basic"],
        idempotency_key="cross-asset-request-key",
        submit=False,
    )

    with pytest.raises(CollectionJobError) as conflict:
        coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            fund_code="020464",
            as_of_date=today,
            idempotency_key="cross-asset-request-key",
            submit=False,
        )

    assert conflict.value.code == "collection_idempotency_conflict"
    assert conflict.value.status_code == 409


def test_file_sqlite_concurrent_same_idempotency_key_returns_one_job(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'fund-idempotency.sqlite3'}"
    first_database = StrategyDatabase(database_url)
    second_database = StrategyDatabase(database_url)
    first_database.create_schema()
    first_equity = _TrackingEquityCollection(first_database)
    second_equity = _TrackingEquityCollection(second_database)
    barrier = Barrier(2)
    first = _TestFundHoldingCoordinator(
        first_database,
        equity_collection=first_equity,
        fund_snapshot_loader=_FundSkill(),
        adapter=_FundAdapter(),
        registry=FUND_TOP_HOLDINGS_REGISTRY,
    )
    second = _TestFundHoldingCoordinator(
        second_database,
        equity_collection=second_equity,
        fund_snapshot_loader=_FundSkill(),
        adapter=_FundAdapter(),
        registry=FUND_TOP_HOLDINGS_REGISTRY,
    )

    def install_race(coordinator) -> None:
        seen = False

        def racing_lookup(session, **kwargs):
            nonlocal seen
            existing = FundHoldingCollectionCoordinator._idempotent_job(
                session,
                **kwargs,
            )
            if existing is None and not seen:
                seen = True
                barrier.wait(timeout=5)
            return existing

        coordinator._idempotent_job = racing_lookup  # type: ignore[method-assign]

    install_race(first)
    install_race(second)
    results: list[dict] = []
    errors: list[Exception] = []
    today = first._local_today()

    def create(coordinator, fund_code: str = "020464") -> None:
        try:
            results.append(
                coordinator.create_job(
                    tenant_id="tenant",
                    user_id="user",
                    actor_id="actor",
                    fund_code=fund_code,
                    as_of_date=today,
                    idempotency_key="concurrent-fund-key",
                    submit=False,
                )
            )
        except Exception as exc:  # noqa: BLE001 - test captures both workers
            errors.append(exc)

    threads = [
        Thread(target=create, args=(first,), daemon=True),
        Thread(target=create, args=(second,), daemon=True),
    ]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert all(not thread.is_alive() for thread in threads)
        assert errors == []
        assert len(results) == 2
        assert len({result["id"] for result in results}) == 1
        with first_database.session() as session:
            assert session.query(MarketDataCollectionJob).count() == 1
            assert session.query(MarketDataCollectionTask).count() == 1

        barrier = Barrier(2)
        install_race(first)
        install_race(second)
        results.clear()
        errors.clear()

        # Use a distinct key while keeping the same forced SELECT/INSERT race.
        def create_conflict(coordinator, fund_code: str) -> None:
            try:
                results.append(
                    coordinator.create_job(
                        tenant_id="tenant",
                        user_id="user",
                        actor_id="actor",
                        fund_code=fund_code,
                        as_of_date=today,
                        idempotency_key="concurrent-fund-conflict-key",
                        submit=False,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - captures expected 409
                errors.append(exc)

        conflict_threads = [
            Thread(target=create_conflict, args=(first, "020464"), daemon=True),
            Thread(target=create_conflict, args=(second, "020465"), daemon=True),
        ]
        for thread in conflict_threads:
            thread.start()
        for thread in conflict_threads:
            thread.join(timeout=10)
        assert all(not thread.is_alive() for thread in conflict_threads)
        assert len(results) == 1
        assert len(errors) == 1
        assert isinstance(errors[0], CollectionJobError)
        assert errors[0].code == "collection_idempotency_conflict"
        assert errors[0].status_code == 409
    finally:
        first.close()
        second.close()
        first_equity.close()
        second_equity.close()
        first_database.close()
        second_database.close()


def test_fund_job_lock_refreshes_a_stale_identity_map(harness) -> None:
    database, _skill, _adapter, _equity, coordinator = harness
    parent = _create_parent(coordinator, as_of_date=coordinator._local_today())
    with database.session() as session:
        cached = session.get(MarketDataCollectionJob, parent["id"])
        assert cached.error_summary is None
        session.execute(
            update(MarketDataCollectionJob)
            .where(MarketDataCollectionJob.id == parent["id"])
            .values(error_summary="fresh-from-database")
            .execution_options(synchronize_session=False)
        )
        assert cached.error_summary is None

        locked = coordinator._lock_fund_job(session, parent["id"])

        assert locked is cached
        assert locked.error_summary == "fresh-from-database"


def test_fund_agent_recovery_recomputes_parent_missing_slots_and_status(
    harness,
) -> None:
    _database, _skill, _adapter, equity, coordinator = harness
    equity.blocked_symbols.add(HOLDING_SYMBOLS[0])
    parent = _create_parent(coordinator, as_of_date=coordinator._local_today())
    coordinator.run_job_now(parent["id"])
    partial = coordinator.get_job(parent["id"], tenant_id="tenant", user_id="user")
    assert partial["status"] == "partial"
    assert partial["checkpoint"]["child_complete_count"] == 9
    assert partial["checkpoint"]["child_missing_by_symbol"][HOLDING_SYMBOLS[0]]

    queued = coordinator.start_agent_recovery(
        parent["id"],
        tenant_id="tenant",
        user_id="user",
        actor_id="actor",
    )
    assert queued["freeze_status"] == "awaiting_recovery"
    recovered = _wait_for_parent_recovery(coordinator, parent["id"])
    assert recovered["status"] == "succeeded"
    assert recovered["progress"]["child_complete"] == 10
    assert recovered["checkpoint"]["child_complete_count"] == 10
    assert recovered["checkpoint"]["child_missing_by_symbol"] == {}
    assert all(
        not holding["missing_dimensions"] for holding in recovered["expanded_holdings"]
    )
    assert recovered["agent_recovery"]["status"] == "succeeded"
    for _ in range(100):
        if (
            parent["id"] not in coordinator._recovery_futures
            and parent["id"] not in coordinator._cancellations
        ):
            break
        Event().wait(0.01)
    assert parent["id"] not in coordinator._recovery_futures
    assert parent["id"] not in coordinator._cancellations


def test_reclaimed_fund_agent_lease_fences_the_obsolete_worker() -> None:
    database = StrategyDatabase("sqlite://")
    database.create_schema()
    equity = _BlockingAgentEquityCollection(database)
    coordinator = _TestFundHoldingCoordinator(
        database,
        equity_collection=equity,
        fund_snapshot_loader=_FundSkill(),
        adapter=_FundAdapter(),
        registry=FUND_TOP_HOLDINGS_REGISTRY,
    )
    try:
        equity.blocked_symbols.add(HOLDING_SYMBOLS[0])
        parent = _create_parent(coordinator, as_of_date=coordinator._local_today())
        coordinator.run_job_now(parent["id"])
        coordinator.start_agent_recovery(
            parent["id"],
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
        )
        assert equity.agent_started.wait(timeout=5)

        with database.session() as session:
            row = session.get(MarketDataCollectionJob, parent["id"])
            checkpoint = dict(row.checkpoint_json or {})
            recovery = dict(checkpoint.get("agent_recovery") or {})
            assert recovery["status"] == "running"
            assert recovery["lease_owner"].startswith("fund-holding-agent:")
            recovery.update(
                {
                    "lease_owner": "replacement-fund-agent",
                    "lease_until": (
                        datetime.now(timezone.utc) + timedelta(minutes=30)
                    ).isoformat(),
                    "message": "replacement owner is processing",
                }
            )
            checkpoint["agent_recovery"] = recovery
            row.checkpoint_json = checkpoint

        equity.agent_released.set()
        for _ in range(200):
            future = coordinator._recovery_futures.get(parent["id"])
            if future is None or future.done():
                break
            Event().wait(0.01)

        persisted = coordinator.get_job(
            parent["id"], tenant_id="tenant", user_id="user"
        )
        assert persisted["agent_recovery"]["status"] == "running"
        assert persisted["agent_recovery"]["lease_owner"] == "replacement-fund-agent"
        assert (
            persisted["agent_recovery"]["message"] == "replacement owner is processing"
        )
    finally:
        equity.agent_released.set()
        coordinator.close()
        equity.close()
        database.close()


def test_closed_old_coordinator_delayed_agent_future_cannot_cancel_new_owner() -> None:
    database = StrategyDatabase("sqlite://")
    database.create_schema()
    equity = _TrackingEquityCollection(database)
    coordinator = _TestFundHoldingCoordinator(
        database,
        equity_collection=equity,
        fund_snapshot_loader=_FundSkill(),
        adapter=_FundAdapter(),
        registry=FUND_TOP_HOLDINGS_REGISTRY,
        max_background_jobs=1,
    )
    blocker_started = Event()
    blocker_released = Event()

    def block_executor() -> None:
        blocker_started.set()
        blocker_released.wait(timeout=5)

    try:
        parent = _create_parent(coordinator, as_of_date=coordinator._local_today())
        coordinator.run_job_now(parent["id"])
        coordinator._executor.submit(block_executor)
        assert blocker_started.wait(timeout=5)
        coordinator.start_agent_recovery(
            parent["id"],
            tenant_id="tenant",
            user_id="user",
            actor_id="old-process",
        )
        delayed = coordinator._recovery_futures[parent["id"]]

        coordinator.close()
        with database.session() as session:
            row = session.get(MarketDataCollectionJob, parent["id"])
            checkpoint = dict(row.checkpoint_json or {})
            recovery = dict(checkpoint.get("agent_recovery") or {})
            recovery.update(
                {
                    "status": "running",
                    "lease_owner": "replacement-agent-worker",
                    "lease_until": (
                        datetime.now(timezone.utc) + timedelta(minutes=30)
                    ).isoformat(),
                    "message": "replacement owner is active",
                }
            )
            checkpoint["agent_recovery"] = recovery
            row.checkpoint_json = checkpoint

        blocker_released.set()
        delayed.result(timeout=5)
        persisted = coordinator.get_job(
            parent["id"], tenant_id="tenant", user_id="user"
        )
        assert persisted["agent_recovery"]["status"] == "running"
        assert persisted["agent_recovery"]["lease_owner"] == "replacement-agent-worker"
        assert persisted["agent_recovery"]["message"] == "replacement owner is active"
    finally:
        blocker_released.set()
        coordinator.close()
        equity.close()
        database.close()


def test_restart_resumes_persisted_fund_agent_recovery(harness) -> None:
    database, _skill, _adapter, _equity, coordinator = harness
    parent = _create_parent(coordinator, as_of_date=coordinator._local_today())
    coordinator.run_job_now(parent["id"])
    completed = coordinator.get_job(parent["id"], tenant_id="tenant", user_id="user")
    child_ids = completed["child_collection_job_ids"]
    with database.session() as session:
        row = session.get(MarketDataCollectionJob, parent["id"])
        checkpoint = dict(row.checkpoint_json or {})
        checkpoint.pop("publication_candidate", None)
        checkpoint["publication"] = {"status": "blocked"}
        checkpoint["agent_recovery"] = {
            "status": "running",
            "requested_by": "actor",
            "lease_owner": "expired-fund-agent",
            "lease_until": (
                datetime.now(timezone.utc) - timedelta(seconds=1)
            ).isoformat(),
        }
        row.checkpoint_json = checkpoint

    with patch.object(coordinator, "_submit_agent_recovery") as submit:
        resumed = coordinator.resume_pending()

    assert resumed == [parent["id"]]
    submit.assert_called_once_with(
        job_id=parent["id"],
        tenant_id="tenant",
        user_id="user",
        actor_id="actor",
        child_ids=child_ids,
    )
    persisted = coordinator.get_job(parent["id"], tenant_id="tenant", user_id="user")
    assert persisted["agent_recovery"]["status"] == "queued"
    assert "重启" in persisted["agent_recovery"]["message"]


def test_restart_does_not_requeue_unexpired_fund_agent_recovery(harness) -> None:
    database, _skill, _adapter, _equity, coordinator = harness
    parent = _create_parent(coordinator, as_of_date=coordinator._local_today())
    coordinator.run_job_now(parent["id"])
    with database.session() as session:
        row = session.get(MarketDataCollectionJob, parent["id"])
        checkpoint = dict(row.checkpoint_json or {})
        checkpoint.pop("publication_candidate", None)
        checkpoint["publication"] = {"status": "blocked"}
        checkpoint["agent_recovery"] = {
            "status": "running",
            "requested_by": "actor",
            "lease_owner": "live-fund-agent",
            "lease_until": (
                datetime.now(timezone.utc) + timedelta(minutes=30)
            ).isoformat(),
        }
        row.checkpoint_json = checkpoint

    with patch.object(coordinator, "_submit_agent_recovery") as submit:
        assert coordinator.resume_pending() == []

    submit.assert_not_called()
    persisted = coordinator.get_job(parent["id"], tenant_id="tenant", user_id="user")
    assert persisted["agent_recovery"]["status"] == "running"
    assert persisted["agent_recovery"]["lease_owner"] == "live-fund-agent"


def test_restart_requeues_parented_running_child_agent_and_finishes_parent(
    harness,
) -> None:
    database, _skill, _adapter, equity, coordinator = harness
    equity.blocked_symbols.add(HOLDING_SYMBOLS[0])
    parent = _create_parent(coordinator, as_of_date=coordinator._local_today())
    coordinator.run_job_now(parent["id"])
    partial = coordinator.get_job(parent["id"], tenant_id="tenant", user_id="user")
    child_id = partial["child_collection_job_ids"][0]
    started_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    with database.session() as session:
        parent_row = session.get(MarketDataCollectionJob, parent["id"])
        child_row = session.get(MarketDataCollectionJob, child_id)
        parent_checkpoint = dict(parent_row.checkpoint_json or {})
        parent_checkpoint["agent_recovery"] = {
            "status": "running",
            "requested_by": "actor",
            "attempt": 1,
            "started_at": started_at.isoformat(),
            "children": [],
        }
        parent_checkpoint["publication"] = {"status": "blocked"}
        parent_row.checkpoint_json = parent_checkpoint
        child_checkpoint = dict(child_row.checkpoint_json or {})
        child_checkpoint["agent_recovery"] = {
            "status": "running",
            "started_at": started_at.isoformat(),
        }
        child_row.checkpoint_json = child_checkpoint

    assert coordinator.resume_pending() == [parent["id"]]
    recovered = _wait_for_parent_recovery(coordinator, parent["id"])

    assert equity.resume_agent_recovery_calls == [[child_id]]
    assert equity.agent_recovery_calls == [child_id]
    assert recovered["agent_recovery"]["status"] == "succeeded"
    assert recovered["status"] == "succeeded"


def test_restart_uses_production_child_resume_helper_before_parent_wait() -> None:
    database = StrategyDatabase("sqlite://")
    database.create_schema()
    equity = _DelegateAgentResumeEquityCollection(database)
    coordinator = _TestFundHoldingCoordinator(
        database,
        equity_collection=equity,
        fund_snapshot_loader=_FundSkill(),
        adapter=_FundAdapter(),
        registry=FUND_TOP_HOLDINGS_REGISTRY,
    )
    try:
        equity.blocked_symbols.add(HOLDING_SYMBOLS[0])
        parent = _create_parent(
            coordinator,
            as_of_date=coordinator._local_today(),
        )
        coordinator.run_job_now(parent["id"])
        partial = coordinator.get_job(parent["id"], tenant_id="tenant", user_id="user")
        child_id = partial["child_collection_job_ids"][0]
        started_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        with database.session() as session:
            parent_row = session.get(MarketDataCollectionJob, parent["id"])
            child_row = session.get(MarketDataCollectionJob, child_id)
            parent_checkpoint = dict(parent_row.checkpoint_json or {})
            parent_checkpoint["agent_recovery"] = {
                "status": "running",
                "requested_by": "actor",
                "attempt": 1,
                "started_at": started_at.isoformat(),
                "children": [],
            }
            parent_checkpoint["publication"] = {"status": "blocked"}
            parent_row.checkpoint_json = parent_checkpoint
            child_checkpoint = dict(child_row.checkpoint_json or {})
            child_checkpoint["agent_recovery"] = {
                "status": "running",
                "started_at": started_at.isoformat(),
                # Missing lease models a legacy/crashed worker and is
                # immediately reclaimable by the production helper.
                "lease_until": None,
            }
            child_row.checkpoint_json = child_checkpoint

        assert coordinator.resume_pending() == [parent["id"]]
        recovered = _wait_for_parent_recovery(coordinator, parent["id"])

        assert equity.delegate_resume_submit_calls == [child_id]
        assert recovered["agent_recovery"]["status"] == "succeeded"
        assert recovered["status"] == "succeeded"
    finally:
        coordinator.close()
        equity.close()
        database.close()


def test_restart_does_not_rerun_child_already_persisted_as_successful(harness) -> None:
    database, _skill, _adapter, equity, coordinator = harness
    parent = _create_parent(coordinator, as_of_date=coordinator._local_today())
    coordinator.run_job_now(parent["id"])
    completed = coordinator.get_job(parent["id"], tenant_id="tenant", user_id="user")
    child_id = completed["child_collection_job_ids"][0]
    started_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    child_summary = {
        "job_id": child_id,
        "status": "succeeded",
        "attempted": 220,
        "succeeded": 220,
        "failed": 0,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    with database.session() as session:
        row = session.get(MarketDataCollectionJob, parent["id"])
        checkpoint = dict(row.checkpoint_json or {})
        checkpoint["agent_recovery"] = {
            "status": "running",
            "requested_by": "actor",
            "attempt": 1,
            "started_at": started_at.isoformat(),
            "children": [child_summary],
        }
        checkpoint["publication"] = {"status": "blocked"}
        row.checkpoint_json = checkpoint

    assert coordinator.resume_pending() == [parent["id"]]
    recovered = _wait_for_parent_recovery(coordinator, parent["id"])

    assert equity.resume_agent_recovery_calls == [[child_id]]
    assert equity.agent_recovery_calls == []
    assert recovered["agent_recovery"]["children"] == [child_summary]
    assert recovered["agent_recovery"]["status"] == "succeeded"


def test_restart_finishes_parent_and_child_agent_cancellation(harness) -> None:
    database, _skill, _adapter, equity, coordinator = harness
    parent = _create_parent(coordinator, as_of_date=coordinator._local_today())
    coordinator.run_job_now(parent["id"])
    completed = coordinator.get_job(parent["id"], tenant_id="tenant", user_id="user")
    child_id = completed["child_collection_job_ids"][0]
    with database.session() as session:
        parent_row = session.get(MarketDataCollectionJob, parent["id"])
        child_row = session.get(MarketDataCollectionJob, child_id)
        parent_checkpoint = dict(parent_row.checkpoint_json or {})
        parent_checkpoint["agent_recovery"] = {
            "status": "cancelling",
            "requested_by": "actor",
        }
        parent_row.checkpoint_json = parent_checkpoint
        child_checkpoint = dict(child_row.checkpoint_json or {})
        child_checkpoint["agent_recovery"] = {"status": "running"}
        child_row.checkpoint_json = child_checkpoint

    assert coordinator.resume_pending() == []

    parent_after = coordinator.get_job(parent["id"], tenant_id="tenant", user_id="user")
    child_after = equity.get_job(child_id, tenant_id="tenant", user_id="user")
    assert parent_after["agent_recovery"]["status"] == "cancelled"
    assert child_after["agent_recovery"]["status"] == "cancelled"
    assert equity.resume_agent_recovery_calls == [[child_id]]


def test_agent_completion_rereads_durable_cross_process_cancellation(harness) -> None:
    database, _skill, _adapter, equity, coordinator = harness
    equity.blocked_symbols.add(HOLDING_SYMBOLS[0])
    parent = _create_parent(coordinator, as_of_date=coordinator._local_today())
    coordinator.run_job_now(parent["id"])
    partial = coordinator.get_job(parent["id"], tenant_id="tenant", user_id="user")
    child_ids = partial["child_collection_job_ids"]
    with database.session() as session:
        row = session.get(MarketDataCollectionJob, parent["id"])
        checkpoint = dict(row.checkpoint_json or {})
        checkpoint["agent_recovery"] = {
            "status": "queued",
            "requested_at": datetime.now(timezone.utc).isoformat(),
            "requested_by": "actor",
        }
        checkpoint["publication"] = {"status": "blocked"}
        row.checkpoint_json = checkpoint

    original_state = coordinator._portfolio_state_after_agent_recovery

    def cancel_before_final_commit(job_id):
        state = original_state(job_id)
        with database.session() as session:
            row = session.get(MarketDataCollectionJob, job_id)
            checkpoint = dict(row.checkpoint_json or {})
            recovery = dict(checkpoint.get("agent_recovery") or {})
            recovery["status"] = "cancelling"
            checkpoint["agent_recovery"] = recovery
            row.checkpoint_json = checkpoint
        return state

    with patch.object(
        coordinator,
        "_portfolio_state_after_agent_recovery",
        side_effect=cancel_before_final_commit,
    ):
        coordinator._run_agent_recovery(
            parent["id"],
            "tenant",
            "user",
            "actor",
            child_ids,
            Event(),
        )

    cancelled = coordinator.get_job(parent["id"], tenant_id="tenant", user_id="user")
    assert cancelled["agent_recovery"]["status"] == "cancelled"
    assert cancelled["freeze_status"] == "blocked"


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


def test_complete_real_snapshots_publish_only_after_explicit_parent_confirmation() -> (
    None
):
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
        assert completed["checkpoint"]["stock_bundles_by_symbol"] == {}
        assert completed["can_confirm_freeze"] is True
        with database.session() as session:
            assert session.query(MarketDataBundle).count() == 0

        frozen = coordinator.confirm_freeze(
            parent["id"],
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            proposal_hash=completed["publication_candidate"]["proposal_hash"],
            acknowledge_degraded=True,
        )
        assert frozen["freeze_status"] == "frozen"
        assert set(frozen["checkpoint"]["stock_bundles_by_symbol"]) == set(
            HOLDING_SYMBOLS
        )
        with pytest.raises(CollectionJobError) as captured:
            coordinator.confirm_freeze(
                parent["id"],
                tenant_id="tenant",
                user_id="user",
                actor_id="actor",
                proposal_hash="not-the-published-proposal",
                acknowledge_degraded=True,
            )
        assert captured.value.code == "collection_freeze_proposal_changed"
        with database.session() as session:
            published = list(session.scalars(select(MarketDataBundle)))
            assert len(published) == 11
            stock_published = [row for row in published if row.market == "CN_EQUITY"]
            assert len(stock_published) == 10
            for bundle in stock_published:
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


def test_fund_freeze_scope_locks_ignore_opposite_holding_order() -> None:
    as_of = date(2026, 8, 20)
    parent = {
        "contract_version": FUND_HOLDINGS_CONTRACT_VERSION,
        "symbol": "020464",
        "market": "CN_FUND",
        "as_of_date": as_of.isoformat(),
    }
    holdings = [
        {
            "contract_version": DEFAULT_DIMENSION_REGISTRY.contract_version,
            "symbol": symbol,
            "market": "CN_EQUITY",
            "as_of_date": as_of.isoformat(),
        }
        for symbol in ("600002.SH", "600001.SH")
    ]

    forward = FundHoldingCollectionCoordinator._publication_lock_scopes(
        {"bundles": [parent, *holdings]}
    )
    reverse = FundHoldingCollectionCoordinator._publication_lock_scopes(
        {"bundles": [parent, *reversed(holdings)]}
    )

    assert forward == reverse == sorted(forward)
    assert [scope[1] for scope in forward if scope[2] == "CN_EQUITY"] == [
        "600001.SH",
        "600002.SH",
    ]


def test_invalidated_identical_stock_bundle_blocks_explicit_parent_confirmation() -> (
    None
):
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
        partial = coordinator.get_job(parent["id"], tenant_id="tenant", user_id="user")

        assert partial["status"] == "succeeded"
        assert partial["checkpoint"]["child_complete_count"] == 10
        assert partial["freeze_status"] == "ready"
        assert partial["can_confirm_freeze"] is True
        assert partial["checkpoint"]["stock_bundles_by_symbol"] == {}
        assert equity.run_calls == []
        with pytest.raises(CollectionJobError) as captured:
            coordinator.confirm_freeze(
                parent["id"],
                tenant_id="tenant",
                user_id="user",
                actor_id="actor",
                proposal_hash=partial["publication_candidate"]["proposal_hash"],
                acknowledge_degraded=True,
            )
        assert captured.value.code == "collection_freeze_proposal_changed"
        with database.session() as session:
            valid_stock_bundles = list(
                session.scalars(
                    select(MarketDataBundle).where(
                        MarketDataBundle.market == "CN_EQUITY",
                        MarketDataBundle.invalidated_at.is_(None),
                    )
                )
            )
            assert valid_stock_bundles == []
    finally:
        coordinator.close()
        equity.close()
        database.close()


def test_stale_confirmation_persists_refreshed_candidate_for_next_attempt(
    harness,
) -> None:
    database, _skill, _adapter, _equity, coordinator = harness
    parent = _create_parent(coordinator, as_of_date=coordinator._local_today())
    coordinator.run_job_now(parent["id"])
    old_candidate = {"proposal_hash": "old", "degraded": False}
    new_candidate = {"proposal_hash": "new", "degraded": False}
    with database.session() as session:
        row = session.get(MarketDataCollectionJob, parent["id"])
        checkpoint = dict(row.checkpoint_json or {})
        checkpoint["publication_candidate"] = old_candidate
        checkpoint["publication"] = {
            "status": "pending_confirmation",
            "proposal_hash": "old",
        }
        row.checkpoint_json = checkpoint

    with (
        patch.object(
            coordinator,
            "_publication_candidate",
            return_value=new_candidate,
        ),
        pytest.raises(CollectionJobError) as captured,
    ):
        coordinator.confirm_freeze(
            parent["id"],
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            proposal_hash="old",
        )

    assert captured.value.code == "collection_freeze_proposal_changed"
    refreshed = coordinator.get_job(parent["id"], tenant_id="tenant", user_id="user")
    assert refreshed["publication_candidate"] == new_candidate
    assert refreshed["checkpoint"]["publication"]["proposal_hash"] == "new"


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
    assert partial["checkpoint"].get("parent_bundle_id") is None
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
    assert completed["checkpoint"].get("parent_bundle_id") is None
    assert completed["checkpoint"]["child_complete_count"] == 10
    assert len(completed["child_collection_job_ids"]) == 2
    retry_child_id = completed["child_collection_job_ids"][-1]
    with database.session() as session:
        retry_child = session.get(MarketDataCollectionJob, retry_child_id)
        assert retry_child is not None
        assert retry_child.symbols_json == [HOLDING_SYMBOLS[-1]]
        assert len(retry_child.dimensions_json) == 22
        assert retry_child.parent_job_id == parent["id"]


def test_report_ready_parent_retries_only_candidate_data_gaps(harness) -> None:
    database, skill, _adapter, _equity, coordinator = harness
    parent = _create_parent(coordinator, as_of_date=coordinator._local_today())
    coordinator.run_job_now(parent["id"])
    completed = coordinator.get_job(parent["id"], tenant_id="tenant", user_id="user")
    assert completed["status"] == "succeeded"
    assert len(skill.calls) == 1

    selected_symbol = HOLDING_SYMBOLS[0]
    selected_dimension = "12_capital_flow"
    audit_only_dimension = "11_governance"
    with database.session() as session:
        row = session.get(MarketDataCollectionJob, parent["id"])
        assert row is not None
        checkpoint = dict(row.checkpoint_json or {})
        checkpoint["publication_candidate"] = {
            "bundles": [
                {
                    "asset_role": "holding",
                    "symbol": symbol,
                    "missing_dimensions": [selected_dimension] if symbol == selected_symbol else [],
                }
                for symbol in HOLDING_SYMBOLS
            ],
            "missing_dimensions": [f"{selected_symbol}:{selected_dimension}"],
        }
        checkpoint["publication"] = {
            "status": "pending_confirmation",
            "proposal_hash": "a" * 64,
        }
        row.checkpoint_json = checkpoint
        child_ids = list(checkpoint["child_job_ids"])
        selected_task = session.scalar(
            select(MarketDataCollectionTask).where(
                MarketDataCollectionTask.job_id.in_(child_ids),
                MarketDataCollectionTask.symbol == selected_symbol,
                MarketDataCollectionTask.dimension == selected_dimension,
            )
        )
        audit_only_task = session.scalar(
            select(MarketDataCollectionTask).where(
                MarketDataCollectionTask.job_id.in_(child_ids),
                MarketDataCollectionTask.symbol == selected_symbol,
                MarketDataCollectionTask.dimension == audit_only_dimension,
            )
        )
        assert selected_task is not None and audit_only_task is not None
        selected_task.status = CollectionStatus.FAILED.value
        selected_task.retryable = True
        selected_task.attempt_count = 1
        selected_task.last_error_code = "historical_provider_failure"
        audit_only_task.status = CollectionStatus.PARTIAL.value
        audit_only_task.retryable = True
        audit_only_task.attempt_count = 1
        audit_only_task.last_error_code = "historical_partial"

    detail = coordinator.get_job(parent["id"], tenant_id="tenant", user_id="user")
    assert detail["portfolio_summary"]["total_slots"] == 220
    assert detail["portfolio_summary"]["ready_slots"] == 219
    assert detail["portfolio_summary"]["missing_slots"] == 1
    assert detail["portfolio_summary"]["retryable_slots"] == 1
    assert detail["portfolio_summary"]["blocked_slots"] == 0

    queued = coordinator.retry_failed(
        parent["id"],
        tenant_id="tenant",
        user_id="user",
        actor_id="actor",
        submit=False,
    )
    assert queued["status"] == "queued"
    assert queued["checkpoint"]["retry_scope_by_symbol"] == {
        selected_symbol: [selected_dimension]
    }
    assert queued["checkpoint"]["publication"]["status"] == "blocked"
    assert "publication_candidate" not in queued["checkpoint"]

    coordinator.run_job_now(parent["id"])
    retried = coordinator.get_job(parent["id"], tenant_id="tenant", user_id="user")
    assert retried["status"] == "succeeded"
    assert len(skill.calls) == 1
    retry_child_id = retried["child_collection_job_ids"][-1]
    with database.session() as session:
        retry_child = session.get(MarketDataCollectionJob, retry_child_id)
        assert retry_child is not None
        assert retry_child.mode == "force_all"
        assert retry_child.symbols_json == [selected_symbol]
        assert retry_child.dimensions_json == [selected_dimension]


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


def test_scoped_retry_collects_only_selected_holding_dimensions(harness) -> None:
    database, _skill, _adapter, equity, coordinator = harness
    today = coordinator._local_today()
    equity.blocked_symbols.update(HOLDING_SYMBOLS)
    parent = _create_parent(coordinator, as_of_date=today)
    coordinator.run_job_now(parent["id"])
    partial = coordinator.get_job(parent["id"], tenant_id="tenant", user_id="user")

    assert partial["status"] == "partial"
    selected_symbol = HOLDING_SYMBOLS[3]
    selected_dimensions = list(equity.registry.dimension_ids[:2])
    equity.blocked_symbols.remove(selected_symbol)

    coordinator.retry_failed(
        parent["id"],
        tenant_id="tenant",
        user_id="user",
        actor_id="actor",
        symbols=[selected_symbol],
        dimensions=selected_dimensions,
        submit=False,
    )
    queued = coordinator.get_job(parent["id"], tenant_id="tenant", user_id="user")
    assert queued["checkpoint"]["retry_scope_by_symbol"] == {
        selected_symbol: selected_dimensions
    }

    coordinator.run_job_now(parent["id"])
    retried = coordinator.get_job(parent["id"], tenant_id="tenant", user_id="user")

    assert retried["status"] == "partial"
    assert "retry_scope_by_symbol" not in retried["checkpoint"]
    retry_child_id = retried["child_collection_job_ids"][-1]
    with database.session() as session:
        retry_child = session.get(MarketDataCollectionJob, retry_child_id)
        assert retry_child is not None
        assert retry_child.symbols_json == [selected_symbol]
        assert retry_child.dimensions_json == selected_dimensions


def test_scoped_retry_rejects_a_non_holding_symbol(harness) -> None:
    _database, _skill, _adapter, equity, coordinator = harness
    today = coordinator._local_today()
    equity.blocked_symbols.update(HOLDING_SYMBOLS)
    parent = _create_parent(coordinator, as_of_date=today)
    coordinator.run_job_now(parent["id"])

    with pytest.raises(CollectionJobError) as captured:
        coordinator.retry_failed(
            parent["id"],
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600999.SH"],
            submit=False,
        )

    assert captured.value.code == "collection_retry_scope_invalid"


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


def test_historical_day_reconstructs_grade_b_parent_and_expands_children() -> None:
    database = StrategyDatabase("sqlite://")
    database.create_schema()
    equity = _TrackingEquityCollection(database)
    skill = _HistoricalFundSkill()
    coordinator = _TestFundHoldingCoordinator(
        database,
        equity_collection=equity,
        fund_snapshot_loader=skill,
        adapter=FundTopHoldingsSnapshotAdapter(),
        registry=FUND_TOP_HOLDINGS_REGISTRY,
    )
    try:
        historical = coordinator._local_today() - timedelta(days=45)
        parent = _create_parent(coordinator, as_of_date=historical)

        coordinator.run_job_now(parent["id"])
        completed = coordinator.get_job(
            parent["id"], tenant_id="tenant", user_id="user"
        )

        assert completed["status"] == "succeeded"
        assert skill.calls[0]["as_of_date"] == historical
        assert len(completed["expanded_holdings"]) == 10
        assert completed["child_collection_job_ids"]
        with database.session() as session:
            snapshot = session.get(
                MarketDataSnapshot,
                completed["checkpoint"]["parent_snapshot_id"],
            )
            assert snapshot is not None
            assert snapshot.evidence_grade == EvidenceGrade.B.value
            assert snapshot.as_of_date == historical
            assert snapshot.observed_at.date() > historical
    finally:
        coordinator.close()
        equity.close()
        database.close()


def test_historical_provider_failure_remains_retryable() -> None:
    database = StrategyDatabase("sqlite://")
    database.create_schema()
    equity = _TrackingEquityCollection(database)
    coordinator = _TestFundHoldingCoordinator(
        database,
        equity_collection=equity,
        fund_snapshot_loader=_HistoricalFundSkill(provider_failure=True),
        adapter=FundTopHoldingsSnapshotAdapter(),
        registry=FUND_TOP_HOLDINGS_REGISTRY,
    )
    try:
        historical = coordinator._local_today() - timedelta(days=45)
        parent = _create_parent(coordinator, as_of_date=historical)

        coordinator.run_job_now(parent["id"])
        failed = coordinator.get_job(parent["id"], tenant_id="tenant", user_id="user")

        assert failed["status"] == "failed"
        assert failed["tasks"][0]["error_code"] == "fund_top_holdings_contract_gap"
        assert failed["tasks"][0]["retryable"] is True
        with database.session() as session:
            result = session.scalar(
                select(MarketDataCollectionTaskResult).where(
                    MarketDataCollectionTaskResult.task_id == failed["tasks"][0]["id"]
                )
            )
            assert result is not None
            assert result.provider_status == "historical_provider_error"
    finally:
        coordinator.close()
        equity.close()
        database.close()


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
        failed = coordinator.get_job(parent["id"], tenant_id="tenant", user_id="user")

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


def test_historical_day_reuses_an_existing_fund_pit_snapshot_without_live(
    harness,
) -> None:
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


def test_historical_force_all_refreshes_instead_of_reusing_existing_snapshot() -> None:
    database = StrategyDatabase("sqlite://")
    database.create_schema()
    equity = _TrackingEquityCollection(database)
    skill = _HistoricalFundSkill()
    adapter = FundTopHoldingsSnapshotAdapter()
    coordinator = _TestFundHoldingCoordinator(
        database,
        equity_collection=equity,
        fund_snapshot_loader=skill,
        adapter=adapter,
        registry=FUND_TOP_HOLDINGS_REGISTRY,
    )
    try:
        historical = coordinator._local_today() - timedelta(days=45)
        seed_raw = skill.run_holdings_as_of(
            fund_code="020464",
            as_of_date=historical,
        )
        seed_ingest = adapter.adapt(
            seed_raw,
            fund_code="020464",
            as_of_date=historical,
            observed_at=datetime.now(timezone.utc),
        )
        with database.session() as session:
            SnapshotIngestionService(
                MarketDataRepository(session), registry=FUND_TOP_HOLDINGS_REGISTRY
            ).ingest(seed_ingest)
        skill.calls.clear()
        for symbol in HOLDING_SYMBOLS:
            equity.available[symbol].update(DEFAULT_DIMENSION_REGISTRY.dimension_ids)

        parent = coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            fund_code="020464",
            as_of_date=historical,
            mode="force_all",
            submit=False,
        )
        coordinator.run_job_now(parent["id"])
        completed = coordinator.get_job(
            parent["id"], tenant_id="tenant", user_id="user"
        )

        assert completed["status"] == "succeeded"
        assert len(skill.calls) == 1
        assert skill.calls[0]["as_of_date"] == historical
        assert len(equity.run_calls) == 1
        with database.session() as session:
            child = session.get(MarketDataCollectionJob, equity.run_calls[0])
            assert child is not None
            assert child.mode == "force_all"
            assert child.parent_job_id == parent["id"]
            assert (
                len(
                    list(
                        session.scalars(
                            select(MarketDataCollectionTask).where(
                                MarketDataCollectionTask.job_id == child.id
                            )
                        )
                    )
                )
                == 10 * 22
            )
    finally:
        coordinator.close()
        equity.close()
        database.close()


def test_invalid_checkpoint_and_resolver_snapshot_are_recollected() -> None:
    database = StrategyDatabase("sqlite://")
    database.create_schema()
    equity = _TrackingEquityCollection(database)
    skill = _HistoricalFundSkill()
    adapter = FundTopHoldingsSnapshotAdapter()
    coordinator = _TestFundHoldingCoordinator(
        database,
        equity_collection=equity,
        fund_snapshot_loader=skill,
        adapter=adapter,
        registry=FUND_TOP_HOLDINGS_REGISTRY,
    )
    try:
        historical = coordinator._local_today() - timedelta(days=45)
        seed_raw = skill.run_holdings_as_of(
            fund_code="020464",
            as_of_date=historical,
        )
        seed_ingest = adapter.adapt(
            seed_raw,
            fund_code="020464",
            as_of_date=historical,
            observed_at=datetime.now(timezone.utc),
        )
        with database.session() as session:
            persisted = SnapshotIngestionService(
                MarketDataRepository(session), registry=FUND_TOP_HOLDINGS_REGISTRY
            ).ingest(seed_ingest)
            snapshot_id = persisted.id
            session.execute(
                delete(MarketDataSnapshotEvidence).where(
                    MarketDataSnapshotEvidence.snapshot_id == snapshot_id
                )
            )
        skill.calls.clear()

        assert (
            coordinator._checkpoint_snapshot(
                {
                    "checkpoint": {"parent_snapshot_id": snapshot_id},
                    "fund_code": "020464",
                    "as_of_date": historical,
                }
            )
            is None
        )
        for symbol in HOLDING_SYMBOLS:
            equity.available[symbol].update(DEFAULT_DIMENSION_REGISTRY.dimension_ids)
        parent = _create_parent(coordinator, as_of_date=historical)
        coordinator.run_job_now(parent["id"])
        completed = coordinator.get_job(
            parent["id"], tenant_id="tenant", user_id="user"
        )

        assert completed["status"] == "succeeded"
        assert len(skill.calls) == 1
    finally:
        coordinator.close()
        equity.close()
        database.close()


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

    draft = coordinator.create_job(
        tenant_id="tenant",
        user_id="user",
        actor_id="actor",
        fund_code="020464",
        as_of_date=coordinator._local_today(),
        publish_when_complete=False,
        submit=False,
    )
    assert draft["publish_when_complete"] is False


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
    harness,
    subscription_enabled,
    resume_subscription_jobs,
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
        coordinator.resume_pending(resume_subscription_jobs=resume_subscription_jobs)
        == []
    )
    cancelled = coordinator.get_job(parent["id"], tenant_id="tenant", user_id="user")
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


def test_subscription_compatible_shape_rejects_multi_symbol_or_date_range(
    harness,
) -> None:
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


def test_restart_finalizes_persisted_cancel_requested_parent(harness) -> None:
    database, _skill, _adapter, _equity, coordinator = harness
    parent = _create_parent(coordinator, as_of_date=coordinator._local_today())
    with database.session() as session:
        row = session.get(MarketDataCollectionJob, parent["id"])
        task = session.get(MarketDataCollectionTask, parent["tasks"][0]["id"])
        row.status = CollectionStatus.RUNNING.value
        row.cancel_requested = True
        row.lease_owner = "crashed-worker"
        row.lease_until = datetime.now(timezone.utc) + timedelta(minutes=30)
        task.status = CollectionStatus.RUNNING.value
        task.lease_owner = "crashed-worker"
        task.lease_until = row.lease_until

    assert coordinator.resume_pending() == []
    cancelled = coordinator.get_job(parent["id"], tenant_id="tenant", user_id="user")
    assert cancelled["status"] == "cancelled"
    assert cancelled["tasks"][0]["status"] == "cancelled"
    assert cancelled["completed_at"] is not None


def test_restart_only_requeues_parent_after_lease_expiry(harness) -> None:
    database, _skill, _adapter, _equity, coordinator = harness
    parent = _create_parent(coordinator, as_of_date=coordinator._local_today())
    with database.session() as session:
        row = session.get(MarketDataCollectionJob, parent["id"])
        task = session.get(MarketDataCollectionTask, parent["tasks"][0]["id"])
        row.status = CollectionStatus.RUNNING.value
        row.lease_owner = "possibly-live-worker"
        row.lease_until = datetime.now(timezone.utc) + timedelta(minutes=30)
        task.status = CollectionStatus.RUNNING.value
        task.lease_owner = row.lease_owner
        task.lease_until = row.lease_until

    with patch.object(coordinator, "submit") as submit:
        assert coordinator.resume_pending() == []
        submit.assert_not_called()

    expired_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    with database.session() as session:
        row = session.get(MarketDataCollectionJob, parent["id"])
        task = session.get(MarketDataCollectionTask, parent["tasks"][0]["id"])
        row.lease_until = expired_at
        task.lease_until = expired_at

    with patch.object(coordinator, "submit") as submit:
        assert coordinator.resume_pending() == [parent["id"]]
        submit.assert_called_once_with(parent["id"])


def test_running_fund_parent_renews_its_durable_lease(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "research_system.market_data.fund_holding_collection."
        "_FUND_LEASE_RENEW_INTERVAL_SECONDS",
        0.02,
    )
    # A heartbeat and worker transact concurrently. StaticPool's single
    # in-memory connection cannot isolate their commits/rollbacks; use the
    # file-backed WAL database and separate connections used by the service.
    database = StrategyDatabase(f"sqlite:///{tmp_path / 'fund-lease.sqlite3'}")
    database.create_schema()
    equity = _BlockingEquityCollection(database)
    coordinator = _TestFundHoldingCoordinator(
        database,
        equity_collection=equity,
        fund_snapshot_loader=_FundSkill(),
        adapter=_FundAdapter(),
        registry=FUND_TOP_HOLDINGS_REGISTRY,
    )
    # Create outside the thread so the durable job id remains available for
    # lease inspection and cancellation.
    parent = _create_parent(coordinator, as_of_date=coordinator._local_today())
    worker = Thread(target=coordinator.run_job_now, args=(parent["id"],), daemon=True)
    try:
        worker.start()
        assert equity.started.wait(timeout=5)
        with database.session() as session:
            first = session.get(MarketDataCollectionJob, parent["id"]).lease_until
        Event().wait(0.08)
        with database.session() as session:
            renewed = session.get(MarketDataCollectionJob, parent["id"]).lease_until
        assert first is not None
        assert renewed is not None
        assert renewed > first

        coordinator.cancel(
            parent["id"],
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
        )
        worker.join(timeout=5)
        assert not worker.is_alive()
    finally:
        equity.released.set()
        worker.join(timeout=5)
        coordinator.close()
        equity.close()
        database.close()


def test_fund_heartbeat_db_errors_stop_at_local_deadline_without_hot_loop(
    harness,
    monkeypatch,
) -> None:
    _database, _skill, _adapter, _equity, coordinator = harness
    monkeypatch.setattr(
        "research_system.market_data.fund_holding_collection."
        "_FUND_DURABLE_SIGNAL_POLL_SECONDS",
        0.01,
    )
    stop = Event()
    lost = Event()
    cancelled = Event()
    with patch.object(
        coordinator,
        "_inspect_fund_collection_lease",
        side_effect=RuntimeError("transient database outage"),
    ) as inspect:
        heartbeat = Thread(
            target=coordinator._heartbeat_fund_collection_lease,
            args=(
                "missing-job",
                "missing-task",
                "worker",
                datetime.now(timezone.utc) + timedelta(seconds=0.08),
                stop,
                lost,
                cancelled,
            ),
            daemon=True,
        )
        heartbeat.start()
        assert lost.wait(timeout=1)
        heartbeat.join(timeout=1)

    assert not heartbeat.is_alive()
    assert not cancelled.is_set()
    assert 1 < inspect.call_count < 30


def test_reclaimed_fund_parent_lease_fences_obsolete_worker() -> None:
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
    parent = _create_parent(coordinator, as_of_date=coordinator._local_today())
    worker = Thread(target=coordinator.run_job_now, args=(parent["id"],), daemon=True)
    try:
        worker.start()
        assert equity.started.wait(timeout=5)
        with database.session() as session:
            row = session.get(MarketDataCollectionJob, parent["id"])
            task = session.get(MarketDataCollectionTask, parent["tasks"][0]["id"])
            row.lease_owner = "replacement-fund-worker"
            row.lease_until = datetime.now(timezone.utc) + timedelta(minutes=30)
            task.lease_owner = "replacement-fund-worker"
            task.lease_until = row.lease_until
        equity.released.set()
        worker.join(timeout=5)
        assert not worker.is_alive()

        with database.session() as session:
            row = session.get(MarketDataCollectionJob, parent["id"])
            task = session.get(MarketDataCollectionTask, parent["tasks"][0]["id"])
            assert row.status == CollectionStatus.RUNNING.value
            assert row.lease_owner == "replacement-fund-worker"
            assert task.status == CollectionStatus.RUNNING.value
            assert task.lease_owner == "replacement-fund-worker"
    finally:
        equity.released.set()
        worker.join(timeout=5)
        coordinator.close()
        equity.close()
        database.close()


def test_restart_reclaims_checkpointed_child_once_without_duplicate_provider_wave() -> (
    None
):
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
            parent_row = session.get(MarketDataCollectionJob, parent["id"])
            parent_task = session.get(
                MarketDataCollectionTask,
                crashed["tasks"][0]["id"],
            )
            child_tasks = list(
                session.scalars(
                    select(MarketDataCollectionTask).where(
                        MarketDataCollectionTask.job_id == child_id
                    )
                )
            )
            expired_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            parent_row.lease_until = expired_at
            parent_task.lease_until = expired_at
            child.lease_until = expired_at
            for child_task in child_tasks:
                child_task.lease_until = expired_at

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


def test_cross_process_parent_cancel_is_observed_without_local_event() -> None:
    database = StrategyDatabase("sqlite://")
    database.create_schema()
    equity = _BlockingEquityCollection(database)
    worker_coordinator = _TestFundHoldingCoordinator(
        database,
        equity_collection=equity,
        fund_snapshot_loader=_FundSkill(),
        adapter=_FundAdapter(),
        registry=FUND_TOP_HOLDINGS_REGISTRY,
    )
    cancelling_coordinator = _TestFundHoldingCoordinator(
        database,
        equity_collection=equity,
        fund_snapshot_loader=_FundSkill(),
        adapter=_FundAdapter(),
        registry=FUND_TOP_HOLDINGS_REGISTRY,
    )
    parent = _create_parent(
        worker_coordinator,
        as_of_date=worker_coordinator._local_today(),
    )
    worker = Thread(
        target=worker_coordinator.run_job_now,
        args=(parent["id"],),
        daemon=True,
    )
    try:
        worker.start()
        assert equity.started.wait(timeout=5)
        assert parent["id"] not in cancelling_coordinator._cancellations

        cancelling_coordinator.cancel(
            parent["id"],
            tenant_id="tenant",
            user_id="user",
            actor_id="other-process",
        )
        worker.join(timeout=5)
        assert not worker.is_alive()

        cancelled = worker_coordinator.get_job(
            parent["id"], tenant_id="tenant", user_id="user"
        )
        assert cancelled["status"] == CollectionStatus.CANCELLED.value
        assert cancelled["error_summary"] is None
    finally:
        equity.released.set()
        worker.join(timeout=5)
        worker_coordinator.close()
        cancelling_coordinator.close()
        equity.close()
        database.close()


def test_cross_process_cancel_after_provider_return_prevents_fund_snapshot() -> None:
    database = StrategyDatabase("sqlite://")
    database.create_schema()
    skill = _BlockingFundSkill()
    equity = _TrackingEquityCollection(database)
    worker_coordinator = _TestFundHoldingCoordinator(
        database,
        equity_collection=equity,
        fund_snapshot_loader=skill,
        adapter=_FundAdapter(),
        registry=FUND_TOP_HOLDINGS_REGISTRY,
    )
    cancelling_coordinator = _TestFundHoldingCoordinator(
        database,
        equity_collection=equity,
        fund_snapshot_loader=skill,
        adapter=_FundAdapter(),
        registry=FUND_TOP_HOLDINGS_REGISTRY,
    )
    parent = _create_parent(
        worker_coordinator, as_of_date=worker_coordinator._local_today()
    )
    worker = Thread(
        target=worker_coordinator.run_job_now, args=(parent["id"],), daemon=True
    )
    try:
        worker.start()
        assert skill.started.wait(timeout=5)
        cancelling_coordinator.cancel(
            parent["id"],
            tenant_id="tenant",
            user_id="user",
            actor_id="other-process",
        )
        skill.released.set()
        worker.join(timeout=5)
        assert not worker.is_alive()

        cancelled = worker_coordinator.get_job(
            parent["id"], tenant_id="tenant", user_id="user"
        )
        assert cancelled["status"] == CollectionStatus.CANCELLED.value
        with database.session() as session:
            assert (
                session.scalar(
                    select(MarketDataSnapshot).where(
                        MarketDataSnapshot.symbol == "020464",
                        MarketDataSnapshot.market == "CN_FUND",
                    )
                )
                is None
            )
    finally:
        skill.released.set()
        worker.join(timeout=5)
        worker_coordinator.close()
        cancelling_coordinator.close()
        equity.close()
        database.close()


def test_cross_process_cancel_wakes_cancellable_fund_provider_without_release() -> None:
    database = StrategyDatabase("sqlite://")
    database.create_schema()
    skill = _CancellationAwareFundSkill()
    equity = _TrackingEquityCollection(database)
    worker_coordinator = _TestFundHoldingCoordinator(
        database,
        equity_collection=equity,
        fund_snapshot_loader=skill,
        adapter=_FundAdapter(),
        registry=FUND_TOP_HOLDINGS_REGISTRY,
    )
    cancelling_coordinator = _TestFundHoldingCoordinator(
        database,
        equity_collection=equity,
        fund_snapshot_loader=skill,
        adapter=_FundAdapter(),
        registry=FUND_TOP_HOLDINGS_REGISTRY,
    )
    parent = _create_parent(
        worker_coordinator, as_of_date=worker_coordinator._local_today()
    )
    worker = Thread(
        target=worker_coordinator.run_job_now, args=(parent["id"],), daemon=True
    )
    try:
        worker.start()
        assert skill.started.wait(timeout=5)
        cancelling_coordinator.cancel(
            parent["id"],
            tenant_id="tenant",
            user_id="user",
            actor_id="other-process",
        )
        assert skill.cancelled.wait(timeout=2)
        worker.join(timeout=5)
        assert not worker.is_alive()

        cancelled = worker_coordinator.get_job(
            parent["id"], tenant_id="tenant", user_id="user"
        )
        assert cancelled["status"] == CollectionStatus.CANCELLED.value
        with database.session() as session:
            row = session.get(MarketDataCollectionJob, parent["id"])
            task = session.get(MarketDataCollectionTask, parent["tasks"][0]["id"])
            assert row.lease_owner is None
            assert row.lease_until is None
            assert task.lease_owner is None
            assert task.lease_until is None
    finally:
        worker.join(timeout=5)
        worker_coordinator.close()
        cancelling_coordinator.close()
        equity.close()
        database.close()


def test_reclaimed_parent_after_provider_return_cannot_append_snapshot() -> None:
    database = StrategyDatabase("sqlite://")
    database.create_schema()
    skill = _BlockingFundSkill()
    equity = _TrackingEquityCollection(database)
    coordinator = _TestFundHoldingCoordinator(
        database,
        equity_collection=equity,
        fund_snapshot_loader=skill,
        adapter=_FundAdapter(),
        registry=FUND_TOP_HOLDINGS_REGISTRY,
    )
    parent = _create_parent(coordinator, as_of_date=coordinator._local_today())
    worker = Thread(target=coordinator.run_job_now, args=(parent["id"],), daemon=True)
    try:
        worker.start()
        assert skill.started.wait(timeout=5)
        with database.session() as session:
            row = session.get(MarketDataCollectionJob, parent["id"])
            task = session.get(MarketDataCollectionTask, parent["tasks"][0]["id"])
            row.lease_owner = "replacement-provider-worker"
            row.lease_until = datetime.now(timezone.utc) + timedelta(minutes=30)
            task.lease_owner = row.lease_owner
            task.lease_until = row.lease_until
        skill.released.set()
        worker.join(timeout=5)
        assert not worker.is_alive()

        with database.session() as session:
            row = session.get(MarketDataCollectionJob, parent["id"])
            assert row.status == CollectionStatus.RUNNING.value
            assert row.lease_owner == "replacement-provider-worker"
            assert (
                session.scalar(
                    select(MarketDataSnapshot).where(
                        MarketDataSnapshot.symbol == "020464",
                        MarketDataSnapshot.market == "CN_FUND",
                    )
                )
                is None
            )
    finally:
        skill.released.set()
        worker.join(timeout=5)
        coordinator.close()
        equity.close()
        database.close()


def test_restart_waits_for_unexpired_child_without_duplicate_provider_wave() -> None:
    database = StrategyDatabase("sqlite://")
    database.create_schema()
    equity = _CompletingBlockingEquityCollection(database)
    first = _TestFundHoldingCoordinator(
        database,
        equity_collection=equity,
        fund_snapshot_loader=_FundSkill(),
        adapter=_FundAdapter(),
        registry=FUND_TOP_HOLDINGS_REGISTRY,
    )
    second = _TestFundHoldingCoordinator(
        database,
        equity_collection=equity,
        fund_snapshot_loader=_FundSkill(),
        adapter=_FundAdapter(),
        registry=FUND_TOP_HOLDINGS_REGISTRY,
    )
    parent = _create_parent(first, as_of_date=first._local_today())
    first_worker = Thread(target=first.run_job_now, args=(parent["id"],), daemon=True)
    resume_result: list[list[str]] = []
    resume_worker = Thread(
        target=lambda: resume_result.append(second.resume_pending()),
        daemon=True,
    )
    try:
        first_worker.start()
        assert equity.started.wait(timeout=5)
        with database.session() as session:
            row = session.get(MarketDataCollectionJob, parent["id"])
            task = session.get(MarketDataCollectionTask, parent["tasks"][0]["id"])
            original_owner = row.lease_owner
            expired = datetime.now(timezone.utc) - timedelta(seconds=1)
            row.lease_until = expired
            task.lease_until = expired

        second.submit = second.run_job_now  # type: ignore[method-assign]
        resume_worker.start()
        for _ in range(200):
            with database.session() as session:
                owner = session.get(MarketDataCollectionJob, parent["id"]).lease_owner
            if owner and owner != original_owner:
                break
            Event().wait(0.01)
        assert owner != original_owner

        equity.released.set()
        first_worker.join(timeout=5)
        if resume_worker.ident is not None:
            resume_worker.join(timeout=5)
        assert not first_worker.is_alive()
        assert not resume_worker.is_alive()
        assert resume_result == [[parent["id"]]]

        completed = second.get_job(parent["id"], tenant_id="tenant", user_id="user")
        assert completed["status"] == CollectionStatus.SUCCEEDED.value
        assert len(completed["child_collection_job_ids"]) == 1
        assert equity.run_calls == completed["child_collection_job_ids"]
    finally:
        equity.released.set()
        first_worker.join(timeout=5)
        if resume_worker.ident is not None:
            resume_worker.join(timeout=5)
        first.close()
        second.close()
        equity.close()
        database.close()
