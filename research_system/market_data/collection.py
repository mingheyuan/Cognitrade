from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from datetime import date, datetime, timedelta, timezone
from threading import Event, RLock, Thread
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from domain_packs.finance_ashare.tools.snapshot_collector import collect_snapshots
from research_system.market_data.agent_recovery import (
    DraftAgentRecoveryError,
    recover_collection_job_draft,
)
from research_system.market_data.bundles import (
    BundleIntegrityError,
    BundlePublisher,
    IncompleteBundleError,
)
from research_system.market_data.contracts import (
    CollectionStatus,
    SnapshotStatus,
    canonical_hash,
)
from research_system.market_data.dimension_contracts import (
    DEFAULT_DIMENSION_REGISTRY,
    DimensionRegistry,
)
from research_system.market_data.historical import (
    capability_for_dimension,
    is_safe_degradation_manifest,
    reconstruct_historical_snapshot,
)
from research_system.market_data.ingestion import (
    SnapshotIngestionError,
    SnapshotIngestionService,
)
from research_system.market_data.job_diagnostics import classify_collection_failure
from research_system.market_data.models import (
    MarketDataCollectionJob,
    MarketDataCollectionTask,
    MarketDataCollectionTaskResult,
    MarketDataSnapshot,
    MarketDataSubscription,
)
from research_system.market_data.repository import MarketDataRepository
from research_system.market_data.resolver import SnapshotResolver
from research_system.market_data.uzi_adapter import UziSnapshotAdapter
from research_system.runtime.core.id_utils import generate_ulid
from research_system.runtime.platform.settings import Settings
from research_system.strategies.database import StrategyDatabase

SnapshotCollector = Callable[..., list[dict[str, Any]]]
MIN_COLLECTION_CONCURRENCY = 1
MAX_COLLECTION_CONCURRENCY = 5
DEFAULT_COLLECTION_CONCURRENCY = 5
COLLECTION_LEASE_DURATION = timedelta(minutes=30)
COLLECTION_LEASE_HEARTBEAT_SECONDS = 60.0
UZI_BASIC_DEPENDENT_SNAPSHOT_TYPES = frozenset(
    {"macro", "industry", "futures", "policy"}
)
EQUITY_COLLECTION_JOB_TYPES = frozenset({"current_refresh", "historical_backfill"})
TERMINAL_COLLECTION_STATUSES = frozenset(
    {
        CollectionStatus.SUCCEEDED.value,
        CollectionStatus.PARTIAL.value,
        CollectionStatus.FAILED.value,
        CollectionStatus.CANCELLED.value,
    }
)


class CollectionJobError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class _CancellationView:
    """Expose one cancellation signal for user cancellation and lease loss."""

    def __init__(self, *events: Event) -> None:
        self._events = events

    def is_set(self) -> bool:
        return any(event.is_set() for event in self._events)


class _LeaseSignals:
    """Keep durable cancellation distinct from ownership/lease loss."""

    def __init__(self) -> None:
        self.lost = Event()
        self.cancel_requested = Event()


class MarketDataCollectionCoordinator:
    """Persist and execute explicit market-data collection jobs.

    One database task represents ``symbol × dimension × date range``.  The
    physical UZI call batches every requested dimension for the job and uses
    the configured UZI worker ceiling, which keeps total provider concurrency
    bounded while preserving independent retry/checkpoint state per dimension.
    """

    def __init__(
        self,
        database: StrategyDatabase,
        *,
        collector: SnapshotCollector = collect_snapshots,
        registry: DimensionRegistry = DEFAULT_DIMENSION_REGISTRY,
        settings: Settings | None = None,
        max_background_jobs: int = 1,
    ) -> None:
        self.database = database
        self.collector = collector
        self.registry = registry
        self.settings = settings
        self.adapter = UziSnapshotAdapter(registry=registry)
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, int(max_background_jobs)),
            thread_name_prefix="market-data-job",
        )
        self._futures: dict[str, Future[None]] = {}
        self._recovery_futures: dict[str, Future[None]] = {}
        self._cancellations: dict[str, Event] = {}
        self._lock = RLock()

    def create_job(
        self,
        *,
        tenant_id: str,
        user_id: str,
        actor_id: str,
        symbols: Iterable[str],
        start_date: date,
        end_date: date,
        dimensions: Iterable[str] | str = "all",
        mode: str = "missing_or_stale",
        max_concurrency: int = DEFAULT_COLLECTION_CONCURRENCY,
        publish_when_complete: bool = False,
        idempotency_key: str | None = None,
        origin: str = "manual",
        subscription_id: str | None = None,
        subscription_claim_until: datetime | None = None,
        parent_job_id: str | None = None,
        submit: bool = True,
    ) -> dict[str, Any]:
        normalized_symbols = self._symbols(symbols)
        normalized_dimensions = self._dimensions(dimensions)
        if not normalized_symbols:
            raise CollectionJobError(
                "collection_symbols_missing", "采集任务至少需要一个标的"
            )
        if end_date < start_date:
            raise CollectionJobError(
                "collection_date_range_invalid", "结束日期不能早于开始日期"
            )
        future_symbols = [
            symbol
            for symbol in normalized_symbols
            if end_date > self._local_today(market_scope(symbol)[1])
        ]
        if future_symbols:
            raise CollectionJobError(
                "collection_future_date",
                "采集任务不能请求标的交易所尚未发生的未来日期："
                + ", ".join(future_symbols),
            )
        if (end_date - start_date).days > 3650:
            raise CollectionJobError(
                "collection_range_too_large", "单个历史回补任务最多十年"
            )
        if mode not in {"missing_or_stale", "force_all"}:
            raise CollectionJobError("collection_mode_invalid", "不支持的采集模式")
        normalized_origin = str(origin or "manual").strip().lower()
        if normalized_origin not in {"manual", "subscription"}:
            raise CollectionJobError(
                "collection_origin_invalid", "不支持的采集任务来源"
            )
        if (
            normalized_origin == "subscription"
            and not str(subscription_id or "").strip()
        ):
            raise CollectionJobError(
                "collection_subscription_missing",
                "订阅采集任务必须绑定 subscription_id",
            )
        if normalized_origin == "manual" and subscription_id is not None:
            raise CollectionJobError(
                "collection_subscription_invalid",
                "人工采集任务不能绑定 subscription_id",
            )
        if normalized_origin == "manual" and subscription_claim_until is not None:
            raise CollectionJobError(
                "collection_subscription_claim_invalid",
                "人工采集任务不能绑定订阅认领窗口",
            )
        normalized_claim_until = None
        if subscription_claim_until is not None:
            if subscription_claim_until.tzinfo is None:
                raise CollectionJobError(
                    "collection_subscription_claim_invalid",
                    "订阅认领时间必须包含时区",
                )
            normalized_claim_until = subscription_claim_until.astimezone(timezone.utc)
        concurrency = int(max_concurrency)
        if not MIN_COLLECTION_CONCURRENCY <= concurrency <= MAX_COLLECTION_CONCURRENCY:
            raise CollectionJobError(
                "collection_concurrency_invalid",
                "采集并发必须在 1 到 5 之间",
                status_code=422,
            )
        request = {
            "symbols": normalized_symbols,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "dimensions": normalized_dimensions,
            "mode": mode,
            "max_concurrency": concurrency,
            "publish_when_complete": bool(publish_when_complete),
            "origin": normalized_origin,
            "subscription_id": str(subscription_id or "").strip() or None,
            "parent_job_id": str(parent_job_id or "").strip() or None,
        }
        expected_job_type = (
            "current_refresh"
            if start_date == end_date
            and all(
                end_date == self._local_today(market_scope(symbol)[1])
                for symbol in normalized_symbols
            )
            else "historical_backfill"
        )
        resolved_key = str(idempotency_key or generate_ulid())
        created = False
        normalized_parent_job_id = str(parent_job_id or "").strip() or None
        with self.database.session() as session:
            existing = self._find_idempotent_job(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                idempotency_key=resolved_key,
            )
            if existing is not None:
                self._validate_idempotent_job(existing, request=request)
                job_id = existing.id
            elif normalized_parent_job_id is not None:
                parent = session.get(MarketDataCollectionJob, normalized_parent_job_id)
                if (
                    parent is None
                    or parent.tenant_id != tenant_id
                    or parent.user_id != user_id
                ):
                    raise CollectionJobError(
                        "collection_parent_job_not_found",
                        "未找到同一主体下的父采集任务",
                        status_code=404,
                    )
        if existing is None:
            try:
                with self.database.session() as session:
                    if (
                        normalized_origin == "subscription"
                        and normalized_claim_until is not None
                    ):
                        launch = session.execute(
                            update(MarketDataSubscription)
                            .where(
                                MarketDataSubscription.id == str(subscription_id),
                                MarketDataSubscription.tenant_id == tenant_id,
                                MarketDataSubscription.user_id == user_id,
                                MarketDataSubscription.enabled.is_(True),
                                MarketDataSubscription.next_run_at
                                == normalized_claim_until,
                                MarketDataSubscription.last_status == "authorized",
                            )
                            .values(
                                last_status="launching",
                                updated_at=datetime.now(timezone.utc),
                            )
                        )
                        if launch.rowcount != 1:
                            raise CollectionJobError(
                                "collection_subscription_claim_lost",
                                "订阅在任务创建前已关闭、改期或被其他调度器接管",
                                status_code=409,
                            )
                    job = MarketDataCollectionJob(
                        tenant_id=tenant_id,
                        user_id=user_id,
                        idempotency_key=resolved_key,
                        origin=normalized_origin,
                        subscription_id=str(subscription_id or "").strip() or None,
                        parent_job_id=normalized_parent_job_id,
                        job_type=expected_job_type,
                        mode=mode,
                        status=CollectionStatus.QUEUED.value,
                        symbols_json=normalized_symbols,
                        dimensions_json=normalized_dimensions,
                        start_date=start_date,
                        end_date=end_date,
                        max_concurrency=concurrency,
                        publish_when_complete=bool(publish_when_complete),
                        request_json=request,
                        progress_json={
                            "completed": 0,
                            "total": len(normalized_symbols)
                            * len(normalized_dimensions),
                        },
                        checkpoint_json={},
                        requested_by=actor_id,
                    )
                    session.add(job)
                    session.flush()
                    for symbol in normalized_symbols:
                        market, exchange_timezone = market_scope(symbol)
                        for dimension in normalized_dimensions:
                            session.add(
                                MarketDataCollectionTask(
                                    job_id=job.id,
                                    symbol=symbol,
                                    market=market,
                                    exchange_timezone=exchange_timezone,
                                    dimension=dimension,
                                    start_date=start_date,
                                    end_date=end_date,
                                    status=CollectionStatus.QUEUED.value,
                                )
                            )
                    job_id = job.id
                    created = True
            except IntegrityError:
                job_id = self._idempotent_replay_job_id(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    idempotency_key=resolved_key,
                    request=request,
                )
                if job_id is None:
                    raise
                created = False
            except CollectionJobError as exc:
                if (
                    exc.code != "collection_subscription_claim_lost"
                    or (
                        job_id := self._idempotent_replay_job_id(
                            tenant_id=tenant_id,
                            user_id=user_id,
                            idempotency_key=resolved_key,
                            request=request,
                        )
                    )
                    is None
                ):
                    raise
                created = False
        # An idempotency replay is observational: the original durable job owns
        # its worker lifecycle.  Re-submitting it here could rerun a terminal job
        # or race the worker already holding its lease.
        if submit and created:
            self.submit(job_id)
        return self.get_job(job_id, tenant_id=tenant_id, user_id=user_id)

    def submit(self, job_id: str) -> None:
        with self._lock:
            future = self._futures.get(job_id)
            if future is not None and not future.done():
                return
            with self.database.session() as session:
                job = session.get(MarketDataCollectionJob, job_id)
                if (
                    job is None
                    or job.job_type not in EQUITY_COLLECTION_JOB_TYPES
                    or job.status != CollectionStatus.QUEUED.value
                    or job.cancel_requested
                ):
                    return
            cancellation = self._cancellations.setdefault(job_id, Event())
            cancellation.clear()
            future = self._executor.submit(self._run_job, job_id, cancellation)
            self._futures[job_id] = future
            future.add_done_callback(
                lambda completed, queued_job_id=job_id: self._cleanup_future(
                    queued_job_id, completed, recovery=False
                )
            )

    def run_job_now(self, job_id: str) -> None:
        """Synchronous executor used by a CLI/worker process and deterministic tests."""

        cancellation = self._cancellations.setdefault(job_id, Event())
        cancellation.clear()
        try:
            self._run_job(job_id, cancellation)
        finally:
            self._cleanup_cancellation_if_idle(job_id)

    def resume_pending(self, *, resume_subscription_jobs: bool = True) -> list[str]:
        """Resume only jobs that were explicitly created before restart."""

        with self.database.session() as session:
            now = datetime.now(timezone.utc)
            self._reconcile_terminal_job_tasks(session, now=now)
            cancelled_jobs = list(
                session.scalars(
                    select(MarketDataCollectionJob).where(
                        MarketDataCollectionJob.job_type.in_(
                            EQUITY_COLLECTION_JOB_TYPES
                        ),
                        MarketDataCollectionJob.parent_job_id.is_(None),
                        MarketDataCollectionJob.status.in_(
                            (
                                CollectionStatus.QUEUED.value,
                                CollectionStatus.RUNNING.value,
                            )
                        ),
                        MarketDataCollectionJob.cancel_requested.is_(True),
                    )
                )
            )
            for job in cancelled_jobs:
                locked = session.execute(
                    update(MarketDataCollectionJob)
                    .where(
                        MarketDataCollectionJob.id == job.id,
                        MarketDataCollectionJob.job_type.in_(
                            EQUITY_COLLECTION_JOB_TYPES
                        ),
                    )
                    .values(updated_at=MarketDataCollectionJob.updated_at)
                )
                if locked.rowcount != 1:
                    continue
                session.refresh(job)
                if (
                    job.status
                    not in {
                        CollectionStatus.QUEUED.value,
                        CollectionStatus.RUNNING.value,
                    }
                    or not job.cancel_requested
                ):
                    continue
                self._finalize_cancelled_job(session, job, now=now)
            jobs = list(
                session.scalars(
                    select(MarketDataCollectionJob).where(
                        MarketDataCollectionJob.job_type.in_(
                            EQUITY_COLLECTION_JOB_TYPES
                        ),
                        # Parented equity jobs are synchronously owned by their
                        # fund expansion.  Resuming them here as well would race
                        # the parent recovery worker and can execute one provider
                        # wave twice after a process restart.
                        MarketDataCollectionJob.parent_job_id.is_(None),
                        MarketDataCollectionJob.status.in_(
                            (
                                CollectionStatus.QUEUED.value,
                                CollectionStatus.RUNNING.value,
                            )
                        ),
                        MarketDataCollectionJob.cancel_requested.is_(False),
                    )
                )
            )
            resumable_jobs: list[MarketDataCollectionJob] = []
            for job in jobs:
                locked = session.execute(
                    update(MarketDataCollectionJob)
                    .where(
                        MarketDataCollectionJob.id == job.id,
                        MarketDataCollectionJob.job_type.in_(
                            EQUITY_COLLECTION_JOB_TYPES
                        ),
                    )
                    .values(updated_at=MarketDataCollectionJob.updated_at)
                )
                if locked.rowcount != 1:
                    continue
                session.refresh(job)
                if (
                    job.status
                    not in {
                        CollectionStatus.QUEUED.value,
                        CollectionStatus.RUNNING.value,
                    }
                    or job.cancel_requested
                ):
                    continue
                if (
                    job.status == CollectionStatus.RUNNING.value
                    and not self._lease_expired(job.lease_until, now=now)
                ):
                    # Another worker still owns an unexpired durable lease.
                    # Startup must not create a duplicate provider wave.
                    continue
                if job.origin == "subscription":
                    subscription = (
                        session.get(MarketDataSubscription, job.subscription_id)
                        if job.subscription_id
                        else None
                    )
                    subscription_valid = bool(
                        resume_subscription_jobs
                        and subscription is not None
                        and subscription.enabled
                        and subscription.tenant_id == job.tenant_id
                        and subscription.user_id == job.user_id
                    )
                    if not subscription_valid:
                        job.status = CollectionStatus.CANCELLED.value
                        job.cancel_requested = True
                        job.lease_owner = None
                        job.lease_until = None
                        job.error_summary = "subscription job not resumed because global or per-symbol opt-in is disabled"
                        job.updated_at = now
                        job.completed_at = now
                        session.query(MarketDataCollectionTask).filter(
                            MarketDataCollectionTask.job_id == job.id,
                            MarketDataCollectionTask.status.in_(
                                (
                                    CollectionStatus.QUEUED.value,
                                    CollectionStatus.RUNNING.value,
                                )
                            ),
                        ).update(
                            {
                                "status": CollectionStatus.CANCELLED.value,
                                "lease_owner": None,
                                "lease_until": None,
                                "updated_at": now,
                                "completed_at": now,
                            },
                            synchronize_session=False,
                        )
                        continue
                if job.status == CollectionStatus.RUNNING.value:
                    job.status = CollectionStatus.QUEUED.value
                    job.lease_owner = None
                    job.lease_until = None
                    job.updated_at = datetime.now(timezone.utc)
                session.query(MarketDataCollectionTask).filter(
                    MarketDataCollectionTask.job_id == job.id,
                    MarketDataCollectionTask.status == CollectionStatus.RUNNING.value,
                ).update(
                    {
                        "status": CollectionStatus.QUEUED.value,
                        "lease_owner": None,
                        "lease_until": None,
                    },
                    synchronize_session=False,
                )
                resumable_jobs.append(job)
            job_ids = [job.id for job in resumable_jobs]
            terminal_jobs = list(
                session.scalars(
                    select(MarketDataCollectionJob).where(
                        MarketDataCollectionJob.job_type.in_(
                            EQUITY_COLLECTION_JOB_TYPES
                        ),
                        MarketDataCollectionJob.parent_job_id.is_(None),
                        MarketDataCollectionJob.status.in_(
                            (
                                CollectionStatus.SUCCEEDED.value,
                                CollectionStatus.PARTIAL.value,
                                CollectionStatus.FAILED.value,
                            )
                        ),
                    )
                )
            )
            recovery_candidates = [job.id for job in terminal_jobs]
        for job_id in job_ids:
            self.submit(job_id)
        recovery_ids = self._resume_agent_recovery_job_ids(recovery_candidates)
        return list(dict.fromkeys([*job_ids, *recovery_ids]))

    def resume_agent_recovery_jobs(self, job_ids: Iterable[str]) -> int:
        """Resume selected durable Agent stages, including fund-owned children.

        A running stage is reclaimable only after its checkpoint lease expires.
        This explicit entry point lets the fund coordinator recover its parented
        equity children without making the general startup scanner race them.
        """

        return len(self._resume_agent_recovery_job_ids(job_ids))

    def _resume_agent_recovery_job_ids(self, job_ids: Iterable[str]) -> list[str]:
        normalized_ids = list(
            dict.fromkeys(
                str(job_id).strip() for job_id in job_ids if str(job_id).strip()
            )
        )
        if not normalized_ids:
            return []
        resumed: list[str] = []
        now = datetime.now(timezone.utc)
        with self._lock:
            with self.database.session() as session:
                jobs = list(
                    session.scalars(
                        select(MarketDataCollectionJob).where(
                            MarketDataCollectionJob.id.in_(normalized_ids),
                            MarketDataCollectionJob.job_type.in_(
                                EQUITY_COLLECTION_JOB_TYPES
                            ),
                        )
                    )
                )
                for job in jobs:
                    locked = session.execute(
                        update(MarketDataCollectionJob)
                        .where(
                            MarketDataCollectionJob.id == job.id,
                            MarketDataCollectionJob.job_type.in_(
                                EQUITY_COLLECTION_JOB_TYPES
                            ),
                        )
                        .values(updated_at=MarketDataCollectionJob.updated_at)
                    )
                    if locked.rowcount != 1:
                        continue
                    session.refresh(job)
                    checkpoint = dict(job.checkpoint_json or {})
                    recovery = dict(checkpoint.get("agent_recovery") or {})
                    status = str(recovery.get("status") or "")
                    if job.cancel_requested or status == "cancelling":
                        checkpoint.pop("publication_candidate", None)
                        recovery.update(
                            {
                                "status": "cancelled",
                                "lease_owner": None,
                                "lease_until": None,
                                "completed_at": iso(now),
                                "message": "服务重启时完成 Agent 补采取消",
                            }
                        )
                        checkpoint["agent_recovery"] = recovery
                        checkpoint["publication"] = {
                            "status": "blocked",
                            "error_code": "agent_recovery_cancelled",
                            "error_message": "Agent 补采已取消，可重新启动补采或重新采集后再冻结",
                            "updated_at": iso(now),
                        }
                        job.checkpoint_json = checkpoint
                        job.updated_at = now
                        continue
                    if (
                        status not in {"queued", "running"}
                        or dict(checkpoint.get("publication") or {}).get("status")
                        == "published"
                    ):
                        continue
                    if status == "running" and not self._lease_expired(
                        recovery.get("lease_until"), now=now
                    ):
                        continue
                    recovery.update(
                        {
                            "status": "queued",
                            "lease_owner": None,
                            "lease_until": None,
                            "resumed_at": iso(now),
                            "message": "服务重启后继续执行 UZI Agent 缺口补采",
                        }
                    )
                    checkpoint["agent_recovery"] = recovery
                    job.checkpoint_json = checkpoint
                    job.updated_at = now
                    resumed.append(job.id)
            for job_id in resumed:
                self._submit_agent_recovery(job_id)
        return resumed

    def _finalize_cancelled_job(
        self,
        session,
        job: MarketDataCollectionJob,
        *,
        now: datetime,
    ) -> None:
        tasks = list(
            session.scalars(
                select(MarketDataCollectionTask).where(
                    MarketDataCollectionTask.job_id == job.id
                )
            )
        )
        for task in tasks:
            if task.status not in {
                CollectionStatus.QUEUED.value,
                CollectionStatus.RUNNING.value,
            }:
                continue
            task.status = CollectionStatus.CANCELLED.value
            task.retryable = False
            task.next_retry_at = None
            task.last_error_code = "collection_cancelled"
            task.last_error_message = "采集任务已取消"
            task.lease_owner = None
            task.lease_until = None
            task.updated_at = now
            task.completed_at = now
        job.status = CollectionStatus.CANCELLED.value
        job.lease_owner = None
        job.lease_until = None
        job.updated_at = now
        job.completed_at = now
        self._set_job_progress(job, tasks)

    def _reconcile_terminal_job_tasks(self, session, *, now: datetime) -> None:
        """Repair active task rows left behind by an older terminal job.

        A terminal job is never resumed implicitly.  Its orphaned queued/running
        tasks are instead made terminal so API progress and retry eligibility
        reflect durable state after a service restart.
        """

        jobs = list(
            session.scalars(
                select(MarketDataCollectionJob).where(
                    MarketDataCollectionJob.job_type.in_(EQUITY_COLLECTION_JOB_TYPES),
                    MarketDataCollectionJob.status.in_(
                        {
                            CollectionStatus.FAILED.value,
                            CollectionStatus.PARTIAL.value,
                            CollectionStatus.CANCELLED.value,
                        }
                    ),
                )
            )
        )
        for job in jobs:
            locked_job = self._lock_equity_job(session, job.id)
            if locked_job is None or locked_job.status not in {
                CollectionStatus.FAILED.value,
                CollectionStatus.PARTIAL.value,
                CollectionStatus.CANCELLED.value,
            }:
                continue
            job = locked_job
            tasks = list(
                session.scalars(
                    select(MarketDataCollectionTask).where(
                        MarketDataCollectionTask.job_id == job.id
                    )
                )
            )
            active_tasks = [
                task
                for task in tasks
                if task.status
                in {CollectionStatus.QUEUED.value, CollectionStatus.RUNNING.value}
            ]
            if not active_tasks:
                continue
            cancelled = job.status == CollectionStatus.CANCELLED.value
            for task in active_tasks:
                task.status = (
                    CollectionStatus.CANCELLED.value
                    if cancelled
                    else CollectionStatus.FAILED.value
                )
                task.retryable = (
                    not cancelled and task.attempt_count < task.max_attempts
                )
                task.next_retry_at = None
                task.last_error_code = (
                    "collection_cancelled"
                    if cancelled
                    else "terminal_job_task_reconciled"
                )
                task.last_error_message = (
                    "采集任务已取消"
                    if cancelled
                    else "服务启动时发现所属作业已终态化，已将遗留活动任务标记为失败"
                )
                task.lease_owner = None
                task.lease_until = None
                task.updated_at = now
                task.completed_at = now
            job.lease_owner = None
            job.lease_until = None
            job.updated_at = now
            job.completed_at = job.completed_at or now
            self._set_job_progress(job, tasks)

    def retry_failed(
        self,
        job_id: str,
        *,
        tenant_id: str,
        user_id: str,
        actor_id: str,
        symbols: list[str] | None = None,
        dimensions: list[str] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            return self._retry_failed_locked(
                job_id,
                tenant_id=tenant_id,
                user_id=user_id,
                actor_id=actor_id,
                symbols=symbols,
                dimensions=dimensions,
            )

    def _retry_failed_locked(
        self,
        job_id: str,
        *,
        tenant_id: str,
        user_id: str,
        actor_id: str,
        symbols: list[str] | None = None,
        dimensions: list[str] | None = None,
    ) -> dict[str, Any]:
        requested_symbols = {
            str(symbol or "").strip().upper()
            for symbol in (symbols or [])
            if str(symbol or "").strip()
        }
        requested_dimensions = {
            str(dimension or "").strip()
            for dimension in (dimensions or [])
            if str(dimension or "").strip()
        }
        unknown_dimensions = requested_dimensions.difference(
            self.registry.dimension_ids
        )
        if unknown_dimensions:
            raise CollectionJobError(
                "collection_retry_scope_invalid",
                "重试范围包含未知维度：" + "、".join(sorted(unknown_dimensions)),
                status_code=422,
            )
        with self.database.session() as session:
            job = self._owned_job(
                session, job_id, tenant_id=tenant_id, user_id=user_id, lock=True
            )
            checkpoint = dict(job.checkpoint_json or {})
            if dict(checkpoint.get("publication") or {}).get("status") == "published":
                raise CollectionJobError(
                    "collection_retry_bundle_frozen",
                    "Bundle 已冻结；补采需要创建新的采集任务与 Bundle 修订版",
                    status_code=409,
                )
            if dict(checkpoint.get("agent_recovery") or {}).get("status") in {
                "queued",
                "running",
                "cancelling",
            }:
                raise CollectionJobError(
                    "collection_retry_recovery_running",
                    "Agent 补采尚未结束，不能同时重试采集槽",
                    status_code=409,
                )
            failed_query = select(MarketDataCollectionTask).where(
                MarketDataCollectionTask.job_id == job.id,
                MarketDataCollectionTask.status.in_(
                    (CollectionStatus.FAILED.value, CollectionStatus.PARTIAL.value)
                ),
            )
            if requested_symbols:
                failed_query = failed_query.where(
                    MarketDataCollectionTask.symbol.in_(requested_symbols)
                )
            if requested_dimensions:
                failed_query = failed_query.where(
                    MarketDataCollectionTask.dimension.in_(requested_dimensions)
                )
            failed = list(session.scalars(failed_query))
            retryable = [
                task
                for task in failed
                if task.retryable and task.attempt_count < task.max_attempts
            ]
            if not retryable:
                raise CollectionJobError(
                    "collection_retry_unavailable",
                    "没有可重试的 UZI 原生失败维度；扩展审计提示不需要重采",
                    status_code=409,
                )
            now = datetime.now(timezone.utc)
            checkpoint.pop("publication_candidate", None)
            previous_recovery = dict(checkpoint.get("agent_recovery") or {})
            if previous_recovery:
                checkpoint["agent_recovery"] = {
                    **previous_recovery,
                    "status": "superseded_by_retry",
                    "message": "新一轮 provider 重试已启动，旧 Agent 补采结论不再作为冻结依据",
                }
            checkpoint["publication"] = {
                "status": "blocked",
                "error_code": "collection_retry_pending",
                "error_message": "重试完成并重新计算候选前不能冻结",
                "updated_at": iso(now),
            }
            job.checkpoint_json = checkpoint
            for task in retryable:
                task.status = CollectionStatus.QUEUED.value
                task.next_retry_at = None
                task.lease_owner = None
                task.lease_until = None
                task.completed_at = None
                task.checkpoint_json = {}
                task.updated_at = now
            job.status = CollectionStatus.QUEUED.value
            job.cancel_requested = False
            job.error_summary = None
            job.completed_at = None
            job.requested_by = actor_id
            job.updated_at = now
            tasks = list(
                session.scalars(
                    select(MarketDataCollectionTask).where(
                        MarketDataCollectionTask.job_id == job.id
                    )
                )
            )
            self._set_job_progress(job, tasks)
        self.submit(job_id)
        return self.get_job(job_id, tenant_id=tenant_id, user_id=user_id)

    def cancel(
        self,
        job_id: str,
        *,
        tenant_id: str,
        user_id: str,
        actor_id: str,
    ) -> dict[str, Any]:
        with self._lock:
            return self._cancel_locked(
                job_id,
                tenant_id=tenant_id,
                user_id=user_id,
                actor_id=actor_id,
            )

    def _cancel_locked(
        self,
        job_id: str,
        *,
        tenant_id: str,
        user_id: str,
        actor_id: str,
    ) -> dict[str, Any]:
        del actor_id
        cancelling_recovery = False
        with self.database.session() as session:
            job = self._owned_job(
                session, job_id, tenant_id=tenant_id, user_id=user_id, lock=True
            )
            checkpoint = dict(job.checkpoint_json or {})
            recovery = dict(checkpoint.get("agent_recovery") or {})
            if recovery.get("status") in {"queued", "running"}:
                now = datetime.now(timezone.utc)
                recovery.update(
                    {
                        "status": "cancelling",
                        "cancel_requested_at": iso(now),
                        "message": "正在取消 Agent 缺口补采",
                    }
                )
                checkpoint["agent_recovery"] = recovery
                job.checkpoint_json = checkpoint
                job.updated_at = now
                cancelling_recovery = True
            if (
                job.status
                in {
                    CollectionStatus.SUCCEEDED.value,
                    CollectionStatus.FAILED.value,
                    CollectionStatus.PARTIAL.value,
                    CollectionStatus.CANCELLED.value,
                }
                and not cancelling_recovery
            ):
                return self._job_payload(session, job)
            if not cancelling_recovery:
                now = datetime.now(timezone.utc)
                job.cancel_requested = True
                self._finalize_cancelled_job(session, job, now=now)
        self._cancellations.setdefault(job_id, Event()).set()
        return self.get_job(job_id, tenant_id=tenant_id, user_id=user_id)

    def get_job(self, job_id: str, *, tenant_id: str, user_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            job = self._owned_job(session, job_id, tenant_id=tenant_id, user_id=user_id)
            return self._job_payload(session, job)

    def confirm_freeze(
        self,
        job_id: str,
        *,
        tenant_id: str,
        user_id: str,
        actor_id: str,
        proposal_hash: str,
        acknowledge_degraded: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            with self.database.session() as session:
                self._owned_job(session, job_id, tenant_id=tenant_id, user_id=user_id)
            # Refresh first so a stale client hash is compared with a durable,
            # current candidate that the next GET can expose after the 409.
            self._refresh_publication_candidate(job_id)
            self._confirm_freeze_job(
                job_id,
                proposal_hash=proposal_hash,
                acknowledge_degraded=acknowledge_degraded,
                published_by=actor_id,
            )
            return self.get_job(job_id, tenant_id=tenant_id, user_id=user_id)

    def start_agent_recovery(
        self,
        job_id: str,
        *,
        tenant_id: str,
        user_id: str,
        actor_id: str,
    ) -> dict[str, Any]:
        """Queue the explicit UZI Agent recovery stage for a mutable draft."""

        with self._lock:
            return self._start_agent_recovery_locked(
                job_id,
                tenant_id=tenant_id,
                user_id=user_id,
                actor_id=actor_id,
            )

    def _start_agent_recovery_locked(
        self,
        job_id: str,
        *,
        tenant_id: str,
        user_id: str,
        actor_id: str,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            job = self._owned_job(
                session,
                job_id,
                tenant_id=tenant_id,
                user_id=user_id,
                lock=True,
            )
            if job.status not in {
                CollectionStatus.SUCCEEDED.value,
                CollectionStatus.PARTIAL.value,
                CollectionStatus.FAILED.value,
            }:
                raise CollectionJobError(
                    "agent_recovery_not_ready",
                    "采集任务尚未结束，不能启动 Agent 补采",
                    status_code=409,
                )
            if (
                set(job.dimensions_json or []) != set(self.registry.dimension_ids)
                and job.parent_job_id is None
            ):
                raise CollectionJobError(
                    "agent_recovery_requires_full_uzi_job",
                    "独立 Agent 完整性补采只适用于 22 维完整任务；单维任务请使用失败重试",
                    status_code=409,
                )
            checkpoint = dict(job.checkpoint_json or {})
            if dict(checkpoint.get("publication") or {}).get("status") == "published":
                raise CollectionJobError(
                    "agent_recovery_bundle_frozen",
                    "Bundle 已冻结；如需补采请创建新的采集任务与 Bundle 修订版",
                    status_code=409,
                )
            recovery = dict(checkpoint.get("agent_recovery") or {})
            if recovery.get("status") not in {"queued", "running", "cancelling"}:
                now = datetime.now(timezone.utc)
                checkpoint.pop("publication_candidate", None)
                checkpoint["publication"] = {
                    "status": "blocked",
                    "error_code": "agent_recovery_pending",
                    "error_message": "Agent 补采完成并重新核查前不能冻结",
                    "updated_at": iso(now),
                }
                checkpoint["agent_recovery"] = {
                    "status": "queued",
                    "requested_at": iso(now),
                    "requested_by": actor_id,
                    "attempted": 0,
                    "succeeded": 0,
                    "failed": 0,
                    "message": "等待执行 UZI Agent 缺口补采",
                }
                job.checkpoint_json = checkpoint
                job.updated_at = now
        self._submit_agent_recovery(job_id)
        return self.get_job(job_id, tenant_id=tenant_id, user_id=user_id)

    def run_agent_recovery_now(self, job_id: str) -> None:
        """Synchronous Agent recovery entry used by workers and tests."""

        cancellation = self._cancellations.setdefault(job_id, Event())
        cancellation.clear()
        try:
            self._run_agent_recovery(job_id, cancellation)
        finally:
            self._cleanup_cancellation_if_idle(job_id)

    def _submit_agent_recovery(self, job_id: str) -> None:
        with self._lock:
            future = self._recovery_futures.get(job_id)
            if future is not None and not future.done():
                return
            with self.database.session() as session:
                job = session.get(MarketDataCollectionJob, job_id)
                recovery = (
                    dict(dict(job.checkpoint_json or {}).get("agent_recovery") or {})
                    if job is not None
                    else {}
                )
                if (
                    job is None
                    or job.job_type not in EQUITY_COLLECTION_JOB_TYPES
                    or recovery.get("status") != "queued"
                ):
                    return
            cancellation = self._cancellations.setdefault(job_id, Event())
            cancellation.clear()
            future = self._executor.submit(
                self._run_agent_recovery, job_id, cancellation
            )
            self._recovery_futures[job_id] = future
            future.add_done_callback(
                lambda completed, queued_job_id=job_id: self._cleanup_future(
                    queued_job_id, completed, recovery=True
                )
            )

    def _cleanup_future(
        self,
        job_id: str,
        completed: Future[None],
        *,
        recovery: bool,
    ) -> None:
        with self._lock:
            futures = self._recovery_futures if recovery else self._futures
            if futures.get(job_id) is completed:
                futures.pop(job_id, None)
            self._cleanup_cancellation_if_idle(job_id)

    def _cleanup_cancellation_if_idle(self, job_id: str) -> None:
        with self._lock:
            collection = self._futures.get(job_id)
            recovery = self._recovery_futures.get(job_id)
            if (collection is None or collection.done()) and (
                recovery is None or recovery.done()
            ):
                self._cancellations.pop(job_id, None)

    @contextmanager
    def _lease_heartbeat(
        self,
        job_id: str,
        worker_id: str,
        *,
        recovery: bool,
        lease_deadline: datetime,
    ) -> Iterator[_LeaseSignals]:
        """Renew one durable worker lease until the guarded operation exits."""

        stop = Event()
        signals = _LeaseSignals()
        heartbeat = Thread(
            target=self._heartbeat_lease,
            args=(job_id, worker_id, stop, signals),
            kwargs={"recovery": recovery, "lease_deadline": lease_deadline},
            name=f"market-data-lease-{job_id}",
            daemon=True,
        )
        heartbeat.start()
        try:
            yield signals
        finally:
            stop.set()
            heartbeat.join(timeout=1.0)

    def _heartbeat_lease(
        self,
        job_id: str,
        worker_id: str,
        stop: Event,
        signals: _LeaseSignals,
        *,
        recovery: bool,
        lease_deadline: datetime,
    ) -> None:
        renew = (
            self._renew_agent_recovery_lease
            if recovery
            else self._renew_collection_lease
        )
        deadline = self._aware_datetime(lease_deadline, fallback=None)
        while True:
            remaining = max(
                0.0, (deadline - datetime.now(timezone.utc)).total_seconds()
            )
            if remaining <= 0:
                signals.lost.set()
                return
            if stop.wait(min(COLLECTION_LEASE_HEARTBEAT_SECONDS, remaining)):
                return
            try:
                if renew(job_id, worker_id):
                    deadline = datetime.now(timezone.utc) + COLLECTION_LEASE_DURATION
                    continue
            except Exception:  # noqa: BLE001 - retry transient DB errors
                if datetime.now(timezone.utc) >= deadline:
                    signals.lost.set()
                    return
                continue
            try:
                cancelled = self._durable_worker_cancel_requested(
                    job_id, worker_id, recovery=recovery
                )
            except Exception:  # noqa: BLE001 - fencing still protects final writes
                cancelled = False
            if cancelled:
                signals.cancel_requested.set()
            else:
                signals.lost.set()
            return

    def _durable_worker_cancel_requested(
        self, job_id: str, worker_id: str, *, recovery: bool
    ) -> bool:
        with self.database.session() as session:
            job = session.get(MarketDataCollectionJob, job_id)
            if job is None:
                return False
            if not recovery:
                return bool(job.cancel_requested or job.status == "cancelled")
            stage = dict(dict(job.checkpoint_json or {}).get("agent_recovery") or {})
            return bool(
                job.cancel_requested
                or stage.get("status") in {"cancelling", "cancelled"}
                and stage.get("lease_owner") in {None, worker_id}
            )

    def _renew_collection_lease(self, job_id: str, worker_id: str) -> bool:
        now = datetime.now(timezone.utc)
        lease_until = now + COLLECTION_LEASE_DURATION
        with self.database.session() as session:
            renewed = session.execute(
                update(MarketDataCollectionJob)
                .where(
                    MarketDataCollectionJob.id == job_id,
                    MarketDataCollectionJob.job_type.in_(EQUITY_COLLECTION_JOB_TYPES),
                    MarketDataCollectionJob.status == CollectionStatus.RUNNING.value,
                    MarketDataCollectionJob.lease_owner == worker_id,
                    MarketDataCollectionJob.lease_until.is_not(None),
                    MarketDataCollectionJob.lease_until > now,
                    MarketDataCollectionJob.cancel_requested.is_(False),
                )
                .values(updated_at=now, lease_until=lease_until)
            )
            if renewed.rowcount != 1:
                return False
            session.execute(
                update(MarketDataCollectionTask)
                .where(
                    MarketDataCollectionTask.job_id == job_id,
                    MarketDataCollectionTask.status == CollectionStatus.RUNNING.value,
                    MarketDataCollectionTask.lease_owner == worker_id,
                )
                .values(updated_at=now, lease_until=lease_until)
            )
            return True

    def _renew_agent_recovery_lease(self, job_id: str, worker_id: str) -> bool:
        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            job = self._lock_equity_job(session, job_id)
            if job is None:
                return False
            checkpoint = dict(job.checkpoint_json or {})
            recovery = dict(checkpoint.get("agent_recovery") or {})
            if (
                recovery.get("status") != "running"
                or recovery.get("lease_owner") != worker_id
                or self._lease_expired(recovery.get("lease_until"), now=now)
                or job.cancel_requested
            ):
                return False
            recovery["lease_until"] = iso(now + COLLECTION_LEASE_DURATION)
            checkpoint["agent_recovery"] = recovery
            job.checkpoint_json = checkpoint
            job.updated_at = now
            return True

    @staticmethod
    def _fence_collection_worker(
        session,
        job_id: str,
        worker_id: str,
        *,
        allow_cancel_requested: bool = False,
    ) -> bool:
        now = datetime.now(timezone.utc)
        conditions = [
            MarketDataCollectionJob.id == job_id,
            MarketDataCollectionJob.job_type.in_(EQUITY_COLLECTION_JOB_TYPES),
            MarketDataCollectionJob.status == CollectionStatus.RUNNING.value,
            MarketDataCollectionJob.lease_owner == worker_id,
            MarketDataCollectionJob.lease_until.is_not(None),
            MarketDataCollectionJob.lease_until > now,
        ]
        if not allow_cancel_requested:
            conditions.append(MarketDataCollectionJob.cancel_requested.is_(False))
        owned = session.execute(
            update(MarketDataCollectionJob)
            .where(*conditions)
            .values(updated_at=MarketDataCollectionJob.updated_at)
        )
        return owned.rowcount == 1

    @classmethod
    def _fence_agent_worker(cls, session, job_id: str, worker_id: str) -> bool:
        job = cls._lock_equity_job(session, job_id)
        if job is None:
            return False
        recovery = dict(dict(job.checkpoint_json or {}).get("agent_recovery") or {})
        return (
            recovery.get("lease_owner") == worker_id
            and recovery.get("status") in {"running", "cancelling"}
            and not cls._lease_expired(
                recovery.get("lease_until"), now=datetime.now(timezone.utc)
            )
        )

    def list_jobs(
        self,
        *,
        tenant_id: str,
        user_id: str,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            filters = [
                MarketDataCollectionJob.tenant_id == tenant_id,
                MarketDataCollectionJob.user_id == user_id,
                MarketDataCollectionJob.job_type.in_(EQUITY_COLLECTION_JOB_TYPES),
            ]
            if status:
                filters.append(MarketDataCollectionJob.status == status)
            total = int(
                session.scalar(
                    select(func.count())
                    .select_from(MarketDataCollectionJob)
                    .where(*filters)
                )
                or 0
            )
            jobs = list(
                session.scalars(
                    select(MarketDataCollectionJob)
                    .where(*filters)
                    .order_by(MarketDataCollectionJob.created_at.desc())
                    .offset(max(0, int(offset)))
                    .limit(max(1, min(int(limit), 200)))
                )
            )
            return {
                "items": [
                    self._job_payload(session, job, include_tasks=False) for job in jobs
                ],
                "total_count": total,
                "limit": limit,
                "offset": offset,
            }

    def close(self) -> None:
        with self._lock:
            for cancellation in self._cancellations.values():
                cancellation.set()
        self._executor.shutdown(wait=False, cancel_futures=False)

    def _run_agent_recovery(self, job_id: str, cancellation: Event) -> None:
        started_at = datetime.now(timezone.utc)
        worker_id = f"market-data-agent:{generate_ulid()}"
        if cancellation.is_set():
            # This Event belongs only to the local coordinator/future.  Before
            # claiming a durable worker token it conveys no authority to mutate
            # the checkpoint: another process may already own a running lease.
            return
        try:
            with self._lock, self.database.session() as session:
                locked = session.execute(
                    update(MarketDataCollectionJob)
                    .where(
                        MarketDataCollectionJob.id == job_id,
                        MarketDataCollectionJob.job_type.in_(
                            EQUITY_COLLECTION_JOB_TYPES
                        ),
                    )
                    .values(updated_at=MarketDataCollectionJob.updated_at)
                )
                if locked.rowcount != 1:
                    return
                job = session.get(MarketDataCollectionJob, job_id)
                if job is None or job.job_type not in EQUITY_COLLECTION_JOB_TYPES:
                    return
                checkpoint = dict(job.checkpoint_json or {})
                recovery = dict(checkpoint.get("agent_recovery") or {})
                if recovery.get("status") != "queued":
                    return
                recovery.update(
                    {
                        "status": "running",
                        "started_at": iso(started_at),
                        "attempt": int(recovery.get("attempt") or 0) + 1,
                        "lease_owner": worker_id,
                        "lease_until": iso(started_at + COLLECTION_LEASE_DURATION),
                        "message": "正在按 UZI 自查问题执行 provider、WebSearch、MX 与浏览器补采",
                    }
                )
                checkpoint["agent_recovery"] = recovery
                job.checkpoint_json = checkpoint
                job.updated_at = started_at

            with self._lease_heartbeat(
                job_id,
                worker_id,
                recovery=True,
                lease_deadline=started_at + COLLECTION_LEASE_DURATION,
            ) as lease_signals:
                worker_cancellation = _CancellationView(
                    cancellation,
                    lease_signals.lost,
                    lease_signals.cancel_requested,
                )
                result = recover_collection_job_draft(
                    self.database,
                    job_id=job_id,
                    settings=self.settings or Settings.from_env(),
                    registry=self.registry,
                    collector=self.collector,
                    max_workers=self._job_concurrency(job_id),
                    cancellation=worker_cancellation,
                )
            if cancellation.is_set() or lease_signals.cancel_requested.is_set():
                self._cancel_agent_recovery(job_id, worker_id=worker_id)
                return
            if lease_signals.lost.is_set():
                return
            completed_at = datetime.now(timezone.utc)
            cancelled = False
            with self._lock:
                with self.database.session() as session:
                    if not self._fence_agent_worker(session, job_id, worker_id):
                        return
                    job = session.get(MarketDataCollectionJob, job_id)
                    if job is None:
                        return
                    checkpoint = dict(job.checkpoint_json or {})
                    previous = dict(checkpoint.get("agent_recovery") or {})
                    # Cancellation may have been requested by another process,
                    # so the in-memory Event is insufficient.  The durable
                    # checkpoint is authoritative immediately before completion.
                    cancelled = bool(
                        cancellation.is_set()
                        or previous.get("status") == "cancelling"
                        or job.cancel_requested
                    )
                    checkpoint.pop("publication_candidate", None)
                    if cancelled:
                        checkpoint["agent_recovery"] = {
                            **previous,
                            "status": "cancelled",
                            "lease_owner": None,
                            "lease_until": None,
                            "completed_at": iso(completed_at),
                            "message": "Agent 缺口补采已取消；草稿 Snapshot 保持不变",
                        }
                        checkpoint["publication"] = {
                            "status": "blocked",
                            "error_code": "agent_recovery_cancelled",
                            "error_message": "Agent 补采已取消，可重新启动补采或重新采集后再冻结",
                            "updated_at": iso(completed_at),
                        }
                    else:
                        provider = dict(result.get("provider_recovery") or {})
                        checkpoint["agent_recovery"] = {
                            "status": "succeeded",
                            "requested_at": previous.get("requested_at"),
                            "requested_by": previous.get("requested_by"),
                            "attempt": int(previous.get("attempt") or 1),
                            "started_at": iso(started_at),
                            "completed_at": iso(completed_at),
                            "lease_owner": None,
                            "lease_until": None,
                            "attempted": int(result.get("attempted") or 0)
                            + int(provider.get("attempted_dimensions") or 0),
                            "succeeded": int(result.get("succeeded") or 0)
                            + int(provider.get("persisted_snapshot_count") or 0),
                            "failed": int(result.get("failed") or 0)
                            + len(list(provider.get("failures") or [])),
                            "persisted_snapshot_count": int(
                                result.get("persisted_snapshot_count") or 0
                            )
                            + int(provider.get("persisted_snapshot_count") or 0),
                            "coverage": list(result.get("coverage") or []),
                            "stages": dict(result.get("stages") or {}),
                            "provider_recovery": {
                                "attempted_dimensions": int(
                                    provider.get("attempted_dimensions") or 0
                                ),
                                "persisted_snapshot_count": int(
                                    provider.get("persisted_snapshot_count") or 0
                                ),
                                "failure_count": len(
                                    list(provider.get("failures") or [])
                                ),
                            },
                            "historical_only": bool(result.get("historical_only")),
                            "disclosure": result.get("disclosure"),
                            "message": (
                                "历史截面不可使用当前网络证据补采；已保留并披露降级数据"
                                if result.get("historical_only")
                                else "Agent 补采完成；请核对新的冻结候选"
                            ),
                        }
                        checkpoint["publication"] = {
                            "status": "blocked",
                            "error_code": "publication_candidate_recomputing",
                            "error_message": "正在根据补采结果重新计算冻结候选",
                            "updated_at": iso(completed_at),
                        }
                    job.checkpoint_json = checkpoint
                    # Agent recovery is part of the mutable draft lifecycle.  Move
                    # the candidate evaluation anchor forward so snapshots appended
                    # by this recovery wave are eligible for the next freeze proposal.
                    job.completed_at = completed_at
                    job.updated_at = completed_at
                if not cancelled:
                    self._refresh_publication_candidate(job_id)
        except DraftAgentRecoveryError as exc:
            self._fail_agent_recovery(job_id, exc.code, worker_id=worker_id)
        except Exception as exc:  # noqa: BLE001 - persist only a sanitized worker failure
            self._fail_agent_recovery(
                job_id, exc.__class__.__name__, worker_id=worker_id
            )

    def _fail_agent_recovery(
        self,
        job_id: str,
        error_code: str,
        *,
        worker_id: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        with self._lock, self.database.session() as session:
            if worker_id is not None and not self._fence_agent_worker(
                session, job_id, worker_id
            ):
                return
            job = session.get(MarketDataCollectionJob, job_id, with_for_update=True)
            if job is None:
                return
            checkpoint = dict(job.checkpoint_json or {})
            previous = dict(checkpoint.get("agent_recovery") or {})
            checkpoint.pop("publication_candidate", None)
            if previous.get("status") == "cancelling" or job.cancel_requested:
                checkpoint["agent_recovery"] = {
                    **previous,
                    "status": "cancelled",
                    "lease_owner": None,
                    "lease_until": None,
                    "completed_at": iso(now),
                    "message": "Agent 缺口补采已取消；草稿 Snapshot 保持不变",
                }
                checkpoint["publication"] = {
                    "status": "blocked",
                    "error_code": "agent_recovery_cancelled",
                    "error_message": "Agent 补采已取消，可重新启动补采或重新采集后再冻结",
                    "updated_at": iso(now),
                }
            else:
                checkpoint["agent_recovery"] = {
                    **previous,
                    "status": "failed",
                    "lease_owner": None,
                    "lease_until": None,
                    "completed_at": iso(now),
                    "failed": max(int(previous.get("failed") or 0), 1),
                    "error_code": str(error_code),
                    "message": "Agent 补采未完成，可稍后重试；详细上游文本未持久化",
                }
                checkpoint["publication"] = {
                    "status": "blocked",
                    "error_code": "agent_recovery_failed",
                    "error_message": "Agent 补采未完成，不能冻结",
                    "updated_at": iso(now),
                }
            job.checkpoint_json = checkpoint
            job.updated_at = now

    def _cancel_agent_recovery(
        self, job_id: str, *, worker_id: str | None = None
    ) -> None:
        now = datetime.now(timezone.utc)
        with self._lock, self.database.session() as session:
            if worker_id is not None and not self._fence_agent_worker(
                session, job_id, worker_id
            ):
                return
            job = session.get(MarketDataCollectionJob, job_id, with_for_update=True)
            if job is None:
                return
            checkpoint = dict(job.checkpoint_json or {})
            previous = dict(checkpoint.get("agent_recovery") or {})
            checkpoint.pop("publication_candidate", None)
            checkpoint["agent_recovery"] = {
                **previous,
                "status": "cancelled",
                "lease_owner": None,
                "lease_until": None,
                "completed_at": iso(now),
                "message": "Agent 缺口补采已取消；草稿 Snapshot 保持不变",
            }
            checkpoint["publication"] = {
                "status": "blocked",
                "error_code": "agent_recovery_cancelled",
                "error_message": "Agent 补采已取消，可重新启动补采或重新采集后再冻结",
                "updated_at": iso(now),
            }
            job.checkpoint_json = checkpoint
            job.updated_at = now

    def _run_job(self, job_id: str, cancellation: Event) -> None:
        worker_id = f"market-data:{generate_ulid()}"
        leases = ExitStack()
        lease_signals = _LeaseSignals()
        claimed_lease_deadline = datetime.now(timezone.utc)
        try:
            cancel_before_start = False
            task_specs: list[dict[str, Any]] = []
            job_mode = "missing_or_stale"
            with self.database.session() as session:
                job = session.get(MarketDataCollectionJob, job_id)
                if (
                    job is None
                    or job.job_type not in EQUITY_COLLECTION_JOB_TYPES
                    or job.status != CollectionStatus.QUEUED.value
                ):
                    return
                cancel_before_start = job.cancel_requested
                if not cancel_before_start:
                    now = datetime.now(timezone.utc)
                    claimed = session.execute(
                        update(MarketDataCollectionJob)
                        .where(
                            MarketDataCollectionJob.id == job_id,
                            MarketDataCollectionJob.status
                            == CollectionStatus.QUEUED.value,
                            MarketDataCollectionJob.cancel_requested.is_(False),
                        )
                        .values(
                            status=CollectionStatus.RUNNING.value,
                            started_at=func.coalesce(
                                MarketDataCollectionJob.started_at, now
                            ),
                            updated_at=now,
                            lease_owner=worker_id,
                            lease_until=now + COLLECTION_LEASE_DURATION,
                        )
                    )
                    if claimed.rowcount != 1:
                        return
                    claimed_lease_deadline = now + COLLECTION_LEASE_DURATION
                    session.refresh(job)
                    tasks = list(
                        session.scalars(
                            select(MarketDataCollectionTask).where(
                                MarketDataCollectionTask.job_id == job.id,
                                MarketDataCollectionTask.status
                                == CollectionStatus.QUEUED.value,
                            )
                        )
                    )
                    task_specs = [
                        {
                            "id": task.id,
                            "job_id": job.id,
                            "symbol": task.symbol,
                            "market": task.market,
                            "exchange_timezone": task.exchange_timezone,
                            "dimension": task.dimension,
                            "start_date": task.start_date,
                            "end_date": task.end_date,
                        }
                        for task in tasks
                    ]
                    job_mode = job.mode
                    for task in tasks:
                        task.status = CollectionStatus.RUNNING.value
                        task.attempt_count += 1
                        task.lease_owner = worker_id
                        task.lease_until = now + COLLECTION_LEASE_DURATION
                        task.completed_at = None
                        task.updated_at = now
            if cancel_before_start:
                self._finish_job(job_id, cancelled=True)
                return
            if not task_specs:
                self._finish_job(
                    job_id,
                    cancelled=cancellation.is_set(),
                    worker_id=worker_id,
                )
                return

            lease_signals = leases.enter_context(
                self._lease_heartbeat(
                    job_id,
                    worker_id,
                    recovery=False,
                    lease_deadline=claimed_lease_deadline,
                )
            )
            worker_cancellation = _CancellationView(
                cancellation,
                lease_signals.lost,
                lease_signals.cancel_requested,
            )
            by_key: dict[tuple[str, str, date], dict[str, Any]] = {}
            provider_errors: dict[date, Exception] = {}
            requested_dates = sorted(
                {
                    as_of
                    for spec in task_specs
                    for as_of in date_range(spec["start_date"], spec["end_date"])
                }
            )
            for as_of in requested_dates:
                provider_specs = [
                    spec
                    for spec in task_specs
                    if spec["start_date"] <= as_of <= spec["end_date"]
                    and self._needs_provider(spec, mode=job_mode, as_of_date=as_of)
                    and (
                        as_of == self._local_today(spec["exchange_timezone"])
                        or capability_for_dimension(spec["dimension"]).can_reconstruct
                    )
                ]
                if not provider_specs or worker_cancellation.is_set():
                    continue
                symbols = list(dict.fromkeys(spec["symbol"] for spec in provider_specs))
                dimensions = list(
                    dict.fromkeys(spec["dimension"] for spec in provider_specs)
                )
                snapshot_types = [
                    self.registry.get(dimension).snapshot_type
                    for dimension in dimensions
                ]
                full_workflow_snapshot_types = {
                    contract.snapshot_type for contract in self.registry
                }
                use_uzi_root_route = full_workflow_snapshot_types.issubset(
                    set(snapshot_types)
                )
                if (
                    "market" not in snapshot_types
                    and UZI_BASIC_DEPENDENT_SNAPSHOT_TYPES.intersection(snapshot_types)
                ):
                    # Native UZI dimensions such as industry/futures consume
                    # dimension 0 as a dependency inside the same provider wave.
                    snapshot_types.insert(0, "market")
                try:
                    snapshots = self.collector(
                        query=(
                            "显式 Point-in-Time 行情数据采集"
                            if all(
                                as_of == self._local_today(spec["exchange_timezone"])
                                for spec in provider_specs
                            )
                            else f"历史 Point-in-Time 自动重建，截至 {as_of.isoformat()}"
                        ),
                        symbols=symbols,
                        snapshot_types=snapshot_types,
                        cache_root=None,
                        research_profile="deep",
                        max_workers=self._job_concurrency(job_id),
                        # A complete 22-dimension run follows native UZI's root
                        # Agent workflow.  A scoped retry must call only the
                        # requested dimension (+ market dependency), otherwise
                        # retrying one failed slot silently reruns all fetchers.
                        uzi_root_route=use_uzi_root_route,
                        uzi_root_route_mode="single",
                        no_resume=use_uzi_root_route,
                        cancellation=worker_cancellation,
                    )
                except Exception as exc:  # noqa: BLE001 - provider failure becomes task state
                    provider_errors[as_of] = exc
                    continue
                dimension_by_type = {
                    self.registry.get(spec["dimension"]).snapshot_type: spec[
                        "dimension"
                    ]
                    for spec in provider_specs
                }
                historical = any(
                    as_of != self._local_today(spec["exchange_timezone"])
                    for spec in provider_specs
                )
                for snapshot in snapshots:
                    if not isinstance(snapshot, Mapping):
                        continue
                    snapshot_type = str(snapshot.get("snapshot_type") or "")
                    dimension = dimension_by_type.get(snapshot_type)
                    prepared = dict(snapshot)
                    if historical:
                        if dimension is None:
                            continue
                        reconstructed = reconstruct_historical_snapshot(
                            prepared,
                            dimension=dimension,
                            as_of_date=as_of,
                        )
                        if reconstructed is None:
                            continue
                        prepared = reconstructed
                    key = (
                        symbol_key(str(prepared.get("symbol") or "")),
                        snapshot_type,
                        as_of,
                    )
                    by_key[key] = prepared

            for spec in task_specs:
                if worker_cancellation.is_set():
                    self._cancel_task(
                        spec["id"],
                        job_id=job_id,
                        worker_id=worker_id,
                    )
                    continue
                try:
                    self._process_task(
                        spec,
                        by_key=by_key,
                        provider_errors=provider_errors,
                        worker_id=worker_id,
                    )
                except Exception as exc:  # noqa: BLE001 - isolate one task from the batch
                    self._fail_task(
                        spec["id"],
                        exc,
                        stage="task",
                        job_id=job_id,
                        worker_id=worker_id,
                    )
            leases.close()
            if lease_signals.lost.is_set():
                return
            self._finish_job(
                job_id,
                cancelled=(
                    cancellation.is_set() or lease_signals.cancel_requested.is_set()
                ),
                worker_id=worker_id,
            )
        except Exception as exc:  # noqa: BLE001 - worker boundary persists terminal failure
            leases.close()
            if not lease_signals.lost.is_set():
                self._fail_job(job_id, exc, worker_id=worker_id)
        finally:
            leases.close()

    def _process_task(
        self,
        spec: dict[str, Any],
        *,
        by_key: dict[tuple[str, str, date], dict[str, Any]],
        provider_errors: dict[date, Exception],
        worker_id: str,
    ) -> None:
        with self.database.session() as session:
            if not self._fence_collection_worker(session, spec["job_id"], worker_id):
                return
            task = session.get(MarketDataCollectionTask, spec["id"])
            if (
                task is None
                or task.status != CollectionStatus.RUNNING.value
                or task.lease_owner != worker_id
            ):
                return
            repository = MarketDataRepository(session)
            ingestion = SnapshotIngestionService(repository, registry=self.registry)
            resolver = SnapshotResolver(repository, registry=self.registry)
            attempt = task.attempt_count
            results: list[str] = []
            finished_dates: list[str] = []
            retryable = False
            last_error_code: str | None = None
            last_error_message: str | None = None
            for as_of in date_range(task.start_date, task.end_date):
                started_at = datetime.now(timezone.utc)
                local_today = self._local_today(task.exchange_timezone)
                historical = as_of != local_today
                existing_coverage = resolver.coverage(
                    symbol=task.symbol,
                    market=task.market,
                    as_of_date=as_of,
                    exchange_timezone=task.exchange_timezone,
                )
                existing_item = next(
                    row
                    for row in existing_coverage.dimensions
                    if row.dimension == task.dimension
                )
                job = session.get(MarketDataCollectionJob, task.job_id)
                if (
                    existing_item.snapshot_id is not None
                    and (existing_item.eligible or existing_item.degraded)
                    and job is not None
                    and job.mode == "missing_or_stale"
                ):
                    session.add(
                        MarketDataCollectionTaskResult(
                            task_id=task.id,
                            as_of_date=as_of,
                            attempt=attempt,
                            status="skipped",
                            provider_status=(
                                "existing_degraded_resolution"
                                if existing_item.degraded
                                else "existing_report_ready"
                            ),
                            snapshot_id=existing_item.snapshot_id,
                            metrics_json={
                                "reason": "existing_safe_resolution",
                                "degraded": existing_item.degraded,
                            },
                            retryable=False,
                            started_at=started_at,
                        )
                    )
                    results.append("skipped")
                    finished_dates.append(as_of.isoformat())
                    continue
                capability = capability_for_dimension(task.dimension)
                if historical and not capability.can_reconstruct:
                    ingest = self.adapter.disclosed_gap(
                        symbol=task.symbol,
                        market=task.market,
                        exchange_timezone=task.exchange_timezone,
                        dimension=task.dimension,
                        as_of_date=as_of,
                        reason_code="historical_source_current_only",
                        message="该维度的数据源只提供当前状态，历史日已自动降级并从评分中排除",
                        collection_task_id=task.id,
                        provider_attempt_id=f"{task.id}:{attempt}",
                    )
                    persisted = ingestion.ingest(ingest)
                    session.add(
                        MarketDataCollectionTaskResult(
                            task_id=task.id,
                            as_of_date=as_of,
                            attempt=attempt,
                            status="degraded",
                            provider_status="historical_unavailable",
                            snapshot_id=persisted.id,
                            metrics_json={
                                "excluded_from_scoring": True,
                                "historical_strategy": capability.strategy,
                            },
                            retryable=False,
                            started_at=started_at,
                        )
                    )
                    results.append("degraded")
                    finished_dates.append(as_of.isoformat())
                    continue
                provider_error = provider_errors.get(as_of)
                if provider_error is not None:
                    gap = self.adapter.disclosed_gap(
                        symbol=task.symbol,
                        market=task.market,
                        exchange_timezone=task.exchange_timezone,
                        dimension=task.dimension,
                        as_of_date=as_of,
                        reason_code="provider_error",
                        message=(
                            "UZI 采集器本次调用失败；按原生结果契约记录为空维度，"
                            "不再由 Cognitrade 扩展契约重复阻断"
                        ),
                        collection_task_id=task.id,
                        provider_attempt_id=f"{task.id}:{attempt}",
                    )
                    persisted = ingestion.ingest(gap)
                    session.add(
                        MarketDataCollectionTaskResult(
                            task_id=task.id,
                            as_of_date=as_of,
                            attempt=attempt,
                            status="failed",
                            provider_status="provider_error",
                            error_code=provider_error.__class__.__name__,
                            error_message="采集器调用失败；详细上游文本未持久化以避免泄露敏感信息",
                            snapshot_id=persisted.id,
                            retryable=True,
                            started_at=started_at,
                        )
                    )
                    results.append("failed")
                    finished_dates.append(as_of.isoformat())
                    retryable = True
                    last_error_code = "provider_error"
                    last_error_message = (
                        f"采集器调用失败（{provider_error.__class__.__name__}）；"
                        "详细上游文本未持久化以避免泄露敏感信息"
                    )
                    continue

                snapshot_type = self.registry.get(task.dimension).snapshot_type
                native = by_key.get((symbol_key(task.symbol), snapshot_type, as_of))
                if native is None:
                    if historical:
                        ingest = self.adapter.disclosed_gap(
                            symbol=task.symbol,
                            market=task.market,
                            exchange_timezone=task.exchange_timezone,
                            dimension=task.dimension,
                            as_of_date=as_of,
                            reason_code="historical_reconstruction_no_proof",
                            message="历史来源已自动查询，但没有返回可验证的目标日记录；已从评分中排除",
                            collection_task_id=task.id,
                            provider_attempt_id=f"{task.id}:{attempt}",
                            attempted_reconstruction=True,
                        )
                        persisted = ingestion.ingest(ingest)
                        session.add(
                            MarketDataCollectionTaskResult(
                                task_id=task.id,
                                as_of_date=as_of,
                                attempt=attempt,
                                status="degraded",
                                provider_status="historical_unavailable",
                                snapshot_id=persisted.id,
                                metrics_json={
                                    "excluded_from_scoring": True,
                                    "attempted_reconstruction": True,
                                },
                                retryable=False,
                                started_at=started_at,
                            )
                        )
                        results.append("degraded")
                        finished_dates.append(as_of.isoformat())
                        continue
                    session.add(
                        MarketDataCollectionTaskResult(
                            task_id=task.id,
                            as_of_date=as_of,
                            attempt=attempt,
                            status="failed",
                            provider_status="missing_result",
                            error_code="collector_result_missing",
                            error_message=f"采集结果缺少 {task.dimension}",
                            retryable=True,
                            started_at=started_at,
                            snapshot_id=ingestion.ingest(
                                self.adapter.disclosed_gap(
                                    symbol=task.symbol,
                                    market=task.market,
                                    exchange_timezone=task.exchange_timezone,
                                    dimension=task.dimension,
                                    as_of_date=as_of,
                                    reason_code="collector_result_missing",
                                    message=f"UZI 采集结果缺少 {task.dimension}",
                                    collection_task_id=task.id,
                                    provider_attempt_id=f"{task.id}:{attempt}",
                                )
                            ).id,
                        )
                    )
                    results.append("failed")
                    finished_dates.append(as_of.isoformat())
                    retryable = True
                    last_error_code = "collector_result_missing"
                    last_error_message = f"采集结果缺少 {task.dimension}"
                    continue
                stage = "adapter"
                try:
                    # Ingestion can flush evidence and snapshots before it fails.  A
                    # savepoint keeps one malformed native result from poisoning the
                    # task session or leaking a half-persisted snapshot.
                    with session.begin_nested():
                        ingest = self.adapter.adapt(
                            native,
                            symbol=task.symbol,
                            market=task.market,
                            exchange_timezone=task.exchange_timezone,
                            as_of_date=as_of,
                            collection_task_id=task.id,
                            provider_attempt_id=f"{task.id}:{attempt}",
                        )
                        stage = "ingestion"
                        persisted = ingestion.ingest(ingest)
                        complete = persisted.status in {
                            SnapshotStatus.AVAILABLE.value,
                            SnapshotStatus.VERIFIED_EMPTY.value,
                        }
                        result_status = "succeeded" if complete else "data_gap"
                        safe_degradation = is_safe_degradation_manifest(
                            persisted.query_manifest_json
                        )
                        if safe_degradation:
                            result_status = "degraded"
                        semantic_errors = list(
                            (persisted.validation_json or {}).get("errors") or []
                        )
                        audit_errors = list(
                            (persisted.validation_json or {}).get("audit_errors") or []
                        )
                        native_quality = str(native.get("quality_status") or "")
                        raw_payload = ingest.payload.get("raw")
                        raw_data_present = bool(raw_payload)
                        can_retry = (
                            not complete
                            and not safe_degradation
                            and native_quality
                            in {
                                "provider_unavailable",
                                "rate_limited",
                                "parse_failed",
                                "degraded",
                                "data_gap",
                            }
                        )
                        session.add(
                            MarketDataCollectionTaskResult(
                                task_id=task.id,
                                as_of_date=as_of,
                                attempt=attempt,
                                status=result_status,
                                provider_status=native_quality or persisted.status,
                                snapshot_id=persisted.id,
                                metrics_json={
                                    "content_hash": persisted.content_hash,
                                    "evidence_grade": persisted.evidence_grade,
                                    "raw_data_present": raw_data_present,
                                    "native_available": bool(
                                        ingest.validation.get("native_available")
                                    ),
                                    "validation_passed": bool(
                                        (persisted.validation_json or {}).get("passed")
                                    ),
                                    "audit_passed": not audit_errors,
                                    "audit_errors": audit_errors[:50],
                                    "degraded": safe_degradation,
                                    "excluded_from_scoring": safe_degradation,
                                },
                                error_code=(
                                    None
                                    if complete or safe_degradation
                                    else "provider_data_gap"
                                ),
                                error_message=(
                                    None
                                    if complete or safe_degradation
                                    else "; ".join(semantic_errors)[:4000]
                                ),
                                retryable=can_retry,
                                started_at=started_at,
                            )
                        )
                except Exception as exc:  # noqa: BLE001 - persist a per-date failure
                    error_code, error_message, can_retry = self._exception_details(
                        exc, stage=stage
                    )
                    session.add(
                        MarketDataCollectionTaskResult(
                            task_id=task.id,
                            as_of_date=as_of,
                            attempt=attempt,
                            status="failed",
                            provider_status=f"{stage}_error",
                            error_code=error_code,
                            error_message=error_message,
                            retryable=can_retry,
                            started_at=started_at,
                        )
                    )
                    results.append("failed")
                    finished_dates.append(as_of.isoformat())
                    retryable = retryable or can_retry
                    last_error_code = error_code
                    last_error_message = error_message
                    continue
                results.append(result_status)
                finished_dates.append(as_of.isoformat())
                retryable = retryable or can_retry
                if not complete and not safe_degradation:
                    last_error_code = "provider_data_gap"
                    last_error_message = "; ".join(semantic_errors)[:4000]
            task.retryable = retryable
            if results and all(
                result in {"succeeded", "skipped", "degraded"} for result in results
            ):
                task.status = CollectionStatus.SUCCEEDED.value
            elif any(result in {"succeeded", "skipped"} for result in results):
                task.status = CollectionStatus.PARTIAL.value
            else:
                task.status = CollectionStatus.FAILED.value
            task.last_error_code = (
                None
                if task.status == CollectionStatus.SUCCEEDED.value
                else last_error_code or "collection_incomplete"
            )
            task.last_error_message = (
                None
                if task.status == CollectionStatus.SUCCEEDED.value
                else last_error_message
                or "一个或多个日期的 UZI 原生数据源未返回可用结果"
            )
            task.next_retry_at = (
                datetime.now(timezone.utc) + timedelta(seconds=min(60, 2**attempt))
                if retryable and attempt < task.max_attempts
                else None
            )
            task.checkpoint_json = {
                "attempt": attempt,
                "finished_dates": finished_dates,
            }
            task.lease_owner = None
            task.lease_until = None
            task.updated_at = datetime.now(timezone.utc)
            task.completed_at = datetime.now(timezone.utc)

    def _needs_provider(
        self, spec: dict[str, Any], *, mode: str, as_of_date: date
    ) -> bool:
        """Return false before the provider wave when a fresh slot already exists."""

        if mode == "force_all":
            return True
        with self.database.session() as session:
            coverage = SnapshotResolver(
                MarketDataRepository(session), registry=self.registry
            ).coverage(
                symbol=spec["symbol"],
                market=spec["market"],
                as_of_date=as_of_date,
                exchange_timezone=spec["exchange_timezone"],
            )
            existing = next(
                item
                for item in coverage.dimensions
                if item.dimension == spec["dimension"]
            )
            return not (existing.eligible or existing.degraded)

    def _finish_job(
        self,
        job_id: str,
        *,
        cancelled: bool = False,
        worker_id: str | None = None,
    ) -> None:
        publish_when_complete = False
        with self.database.session() as session:
            if worker_id is not None and not self._fence_collection_worker(
                session,
                job_id,
                worker_id,
                allow_cancel_requested=True,
            ):
                return
            job = session.get(MarketDataCollectionJob, job_id)
            if job is None:
                return
            tasks = list(
                session.scalars(
                    select(MarketDataCollectionTask).where(
                        MarketDataCollectionTask.job_id == job.id
                    )
                )
            )
            now = datetime.now(timezone.utc)
            was_cancelled = cancelled or job.cancel_requested
            if was_cancelled:
                for task in tasks:
                    if task.status not in {
                        CollectionStatus.QUEUED.value,
                        CollectionStatus.RUNNING.value,
                    }:
                        continue
                    task.status = CollectionStatus.CANCELLED.value
                    task.retryable = False
                    task.next_retry_at = None
                    task.last_error_code = "collection_cancelled"
                    task.last_error_message = "采集任务已取消"
                    task.lease_owner = None
                    task.lease_until = None
                    task.updated_at = now
                    task.completed_at = now
            if was_cancelled:
                final_status = CollectionStatus.CANCELLED.value
            elif tasks and all(
                task.status == CollectionStatus.SUCCEEDED.value for task in tasks
            ):
                final_status = CollectionStatus.SUCCEEDED.value
            elif any(
                task.status
                in {CollectionStatus.SUCCEEDED.value, CollectionStatus.PARTIAL.value}
                for task in tasks
            ):
                final_status = CollectionStatus.PARTIAL.value
            else:
                final_status = CollectionStatus.FAILED.value
            publish_when_complete = bool(
                job.publish_when_complete and not was_cancelled
            )
            job.status = final_status
            self._set_job_progress(job, tasks)
            job.lease_owner = None
            job.lease_until = None
            job.updated_at = now
            job.completed_at = now
        candidate = self._refresh_publication_candidate(job_id)
        if publish_when_complete and candidate is not None:
            try:
                self._confirm_freeze_job(
                    job_id,
                    proposal_hash=candidate["proposal_hash"],
                    acknowledge_degraded=True,
                    published_by="collection_subscription",
                )
            except CollectionJobError:
                # Acquisition completion is durable even if publication is
                # blocked; the job payload exposes the exact confirmation gap.
                pass

    def _publish_if_complete(
        self,
        *,
        symbol: str,
        market: str,
        exchange_timezone: str,
        as_of_date: date,
        published_by: str,
    ) -> str | None:
        now = datetime.now(timezone.utc)
        local_today = now.astimezone(ZoneInfo(exchange_timezone)).date()
        cutoff_at = now if as_of_date == local_today else None
        try:
            with self.database.session() as session:
                publisher = BundlePublisher(
                    MarketDataRepository(session), registry=self.registry
                )
                bundle = publisher.publish(
                    symbol=symbol,
                    market=market,
                    as_of_date=as_of_date,
                    cutoff_at=cutoff_at,
                    exchange_timezone=exchange_timezone,
                    published_by=published_by,
                    now=now,
                    allow_degraded=as_of_date != local_today,
                )
                return bundle.id
        except IncompleteBundleError:
            return None

    def _cancel_task(
        self,
        task_id: str,
        *,
        job_id: str | None = None,
        worker_id: str | None = None,
    ) -> None:
        with self.database.session() as session:
            if worker_id is not None and (
                job_id is None
                or not self._fence_collection_worker(
                    session,
                    job_id,
                    worker_id,
                    allow_cancel_requested=True,
                )
            ):
                return
            task = session.get(MarketDataCollectionTask, task_id)
            if task is None or (
                worker_id is not None
                and (
                    task.status != CollectionStatus.RUNNING.value
                    or task.lease_owner != worker_id
                )
            ):
                return
            task.status = CollectionStatus.CANCELLED.value
            task.retryable = False
            task.next_retry_at = None
            task.last_error_code = "collection_cancelled"
            task.last_error_message = "采集任务已取消"
            task.lease_owner = None
            task.lease_until = None
            task.updated_at = datetime.now(timezone.utc)
            task.completed_at = datetime.now(timezone.utc)

    def _fail_task(
        self,
        task_id: str,
        exc: Exception,
        *,
        stage: str,
        job_id: str | None = None,
        worker_id: str | None = None,
    ) -> None:
        error_code, error_message, retryable = self._exception_details(exc, stage=stage)
        with self.database.session() as session:
            if worker_id is not None and (
                job_id is None
                or not self._fence_collection_worker(session, job_id, worker_id)
            ):
                return
            task = session.get(MarketDataCollectionTask, task_id)
            if task is None or (
                worker_id is not None
                and (
                    task.status != CollectionStatus.RUNNING.value
                    or task.lease_owner != worker_id
                )
            ):
                return
            now = datetime.now(timezone.utc)
            attempt = task.attempt_count
            existing_dates = set(
                session.scalars(
                    select(MarketDataCollectionTaskResult.as_of_date).where(
                        MarketDataCollectionTaskResult.task_id == task.id,
                        MarketDataCollectionTaskResult.attempt == attempt,
                    )
                )
            )
            finished_dates: list[str] = []
            for as_of in date_range(task.start_date, task.end_date):
                finished_dates.append(as_of.isoformat())
                if as_of in existing_dates:
                    continue
                session.add(
                    MarketDataCollectionTaskResult(
                        task_id=task.id,
                        as_of_date=as_of,
                        attempt=attempt,
                        status="failed",
                        provider_status=f"{stage}_error",
                        error_code=error_code,
                        error_message=error_message,
                        retryable=retryable,
                        started_at=now,
                    )
                )
            task.status = CollectionStatus.FAILED.value
            task.retryable = retryable
            task.last_error_code = error_code
            task.last_error_message = error_message
            task.next_retry_at = (
                now + timedelta(seconds=min(60, 2 ** max(1, attempt)))
                if retryable and attempt < task.max_attempts
                else None
            )
            task.checkpoint_json = {
                "attempt": attempt,
                "finished_dates": finished_dates,
            }
            task.lease_owner = None
            task.lease_until = None
            task.updated_at = now
            task.completed_at = now

    def _fail_job(
        self,
        job_id: str,
        exc: Exception,
        *,
        worker_id: str | None = None,
    ) -> None:
        error_code, error_message, retryable = self._exception_details(
            exc, stage="worker"
        )
        with self.database.session() as session:
            if worker_id is not None and not self._fence_collection_worker(
                session,
                job_id,
                worker_id,
                allow_cancel_requested=True,
            ):
                return
            job = session.get(MarketDataCollectionJob, job_id)
            if job is None:
                return
            now = datetime.now(timezone.utc)
            cancelled = job.cancel_requested
            tasks = list(
                session.scalars(
                    select(MarketDataCollectionTask).where(
                        MarketDataCollectionTask.job_id == job.id
                    )
                )
            )
            for task in tasks:
                if task.status not in {
                    CollectionStatus.QUEUED.value,
                    CollectionStatus.RUNNING.value,
                }:
                    continue
                task.status = (
                    CollectionStatus.CANCELLED.value
                    if cancelled
                    else CollectionStatus.FAILED.value
                )
                task.retryable = False if cancelled else retryable
                task.last_error_code = (
                    "collection_cancelled" if cancelled else error_code
                )
                task.last_error_message = (
                    "采集任务已取消" if cancelled else error_message
                )
                task.next_retry_at = (
                    now + timedelta(seconds=min(60, 2 ** max(1, task.attempt_count)))
                    if not cancelled
                    and retryable
                    and task.attempt_count < task.max_attempts
                    else None
                )
                task.lease_owner = None
                task.lease_until = None
                task.updated_at = now
                task.completed_at = now
            job.status = (
                CollectionStatus.CANCELLED.value
                if cancelled
                else CollectionStatus.FAILED.value
            )
            job.error_summary = (
                "collection_cancelled: collection job was cancelled"
                if cancelled
                else f"{error_code}: {error_message}"
            )
            self._set_job_progress(job, tasks)
            job.lease_owner = None
            job.lease_until = None
            job.updated_at = now
            job.completed_at = now

    @staticmethod
    def _exception_details(exc: Exception, *, stage: str) -> tuple[str, str, bool]:
        """Return stable, non-sensitive diagnostics suitable for persistence."""

        if isinstance(exc, ValidationError):
            issues: list[str] = []
            for item in exc.errors(include_url=False, include_input=False)[:5]:
                location = ".".join(str(part) for part in item.get("loc") or ()) or "$"
                issue_type = str(item.get("type") or "validation_error")
                issues.append(f"{location}[{issue_type}]")
            suffix = f": {', '.join(issues)}" if issues else ""
            return (
                f"{stage}_validation_error",
                f"{stage} validation failed{suffix}"[:4000],
                False,
            )
        if isinstance(exc, SnapshotIngestionError):
            return (
                f"{stage}_rejected",
                f"{stage} rejected the snapshot ({exc.__class__.__name__})",
                False,
            )
        return (
            f"{stage}_unexpected_error",
            f"{stage} failed with {exc.__class__.__name__}",
            True,
        )

    @staticmethod
    def _set_job_progress(
        job: MarketDataCollectionJob,
        tasks: list[MarketDataCollectionTask],
    ) -> None:
        counts = defaultdict(int)
        for task in tasks:
            counts[task.status] += 1
        finished = [
            task.id for task in tasks if task.status in TERMINAL_COLLECTION_STATUSES
        ]
        job.progress_json = {
            "completed": len(finished),
            "total": len(tasks),
            "by_status": dict(counts),
        }
        checkpoint = dict(job.checkpoint_json or {})
        checkpoint["finished_task_ids"] = finished
        job.checkpoint_json = checkpoint

    def _job_concurrency(self, job_id: str) -> int:
        with self.database.session() as session:
            job = session.get(MarketDataCollectionJob, job_id)
            return max(
                MIN_COLLECTION_CONCURRENCY,
                min(
                    int(job.max_concurrency if job else DEFAULT_COLLECTION_CONCURRENCY),
                    MAX_COLLECTION_CONCURRENCY,
                ),
            )

    @staticmethod
    def _lock_equity_job(session, job_id: str) -> MarketDataCollectionJob | None:
        """Acquire a portable write/row lock and return freshly loaded state."""

        locked = session.execute(
            update(MarketDataCollectionJob)
            .where(
                MarketDataCollectionJob.id == job_id,
                MarketDataCollectionJob.job_type.in_(EQUITY_COLLECTION_JOB_TYPES),
            )
            .values(updated_at=MarketDataCollectionJob.updated_at)
        )
        if locked.rowcount != 1:
            return None
        job = session.get(MarketDataCollectionJob, job_id)
        if job is not None:
            session.refresh(job)
        return job

    @staticmethod
    def _owned_job(
        session,
        job_id: str,
        *,
        tenant_id: str,
        user_id: str,
        lock: bool = False,
    ) -> MarketDataCollectionJob:
        job = (
            MarketDataCollectionCoordinator._lock_equity_job(session, job_id)
            if lock
            else session.get(MarketDataCollectionJob, job_id)
        )
        if (
            job is None
            or job.tenant_id != tenant_id
            or job.user_id != user_id
            or job.job_type not in EQUITY_COLLECTION_JOB_TYPES
        ):
            raise CollectionJobError(
                "collection_job_not_found", "未找到采集任务", status_code=404
            )
        return job

    def _refresh_publication_candidate(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._refresh_publication_candidate_locked(job_id)

    def _refresh_publication_candidate_locked(
        self, job_id: str
    ) -> dict[str, Any] | None:
        with self.database.session() as session:
            job = self._lock_equity_job(session, job_id)
            if job is None or job.job_type not in EQUITY_COLLECTION_JOB_TYPES:
                return None
            checkpoint = dict(job.checkpoint_json or {})
            if dict(checkpoint.get("publication") or {}).get("status") == "published":
                candidate = checkpoint.get("publication_candidate")
                return dict(candidate) if isinstance(candidate, Mapping) else None
            try:
                candidate = self._publication_candidate(
                    session,
                    job,
                    baseline=checkpoint.get("publication_candidate"),
                )
            except (
                CollectionJobError,
                IncompleteBundleError,
                BundleIntegrityError,
                ValueError,
            ) as exc:
                checkpoint.pop("publication_candidate", None)
                checkpoint["publication"] = {
                    "status": "blocked",
                    "error_code": "collection_freeze_candidate_incomplete",
                    "error_message": str(exc),
                    "updated_at": iso(datetime.now(timezone.utc)),
                }
                job.checkpoint_json = checkpoint
                return None
            checkpoint["publication_candidate"] = candidate
            publication = dict(checkpoint.get("publication") or {})
            if publication.get("status") != "published":
                checkpoint["publication"] = {
                    "status": "pending_confirmation",
                    "proposal_hash": candidate["proposal_hash"],
                    "updated_at": iso(datetime.now(timezone.utc)),
                }
            job.checkpoint_json = checkpoint
            return candidate

    def _publication_candidate(
        self,
        session,
        job: MarketDataCollectionJob,
        *,
        baseline: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if job.status not in {
            CollectionStatus.SUCCEEDED.value,
            CollectionStatus.PARTIAL.value,
            CollectionStatus.FAILED.value,
        }:
            raise CollectionJobError(
                "collection_freeze_not_ready",
                "采集任务尚未完成，不能确认冻结",
                status_code=409,
            )
        baseline = dict(baseline or {})
        evaluated_at = self._aware_datetime(
            baseline.get("evaluated_at"), fallback=job.completed_at
        )
        baseline_cutoffs = {
            (str(item.get("symbol")), str(item.get("as_of_date"))): item.get(
                "cutoff_at"
            )
            for item in list(baseline.get("bundles") or [])
            if isinstance(item, Mapping)
        }
        publisher = BundlePublisher(
            MarketDataRepository(session), registry=self.registry
        )
        bundles: list[dict[str, Any]] = []
        for symbol in sorted(
            str(item).strip().upper() for item in (job.symbols_json or [])
        ):
            market, exchange_timezone = market_scope(symbol)
            for as_of in date_range(job.start_date, job.end_date):
                saved_cutoff = baseline_cutoffs.get((symbol, as_of.isoformat()))
                cutoff_at = (
                    self._aware_datetime(saved_cutoff, fallback=evaluated_at)
                    if saved_cutoff
                    else evaluated_at
                    if as_of
                    == evaluated_at.astimezone(ZoneInfo(exchange_timezone)).date()
                    else None
                )
                bundles.append(
                    publisher.propose(
                        symbol=symbol,
                        market=market,
                        as_of_date=as_of,
                        cutoff_at=cutoff_at,
                        exchange_timezone=exchange_timezone,
                        now=evaluated_at,
                        allow_degraded=as_of
                        != evaluated_at.astimezone(ZoneInfo(exchange_timezone)).date(),
                    )
                )
        proposal_payload = {
            "version": "market-data-freeze-proposal-v1",
            "job_id": job.id,
            "evaluated_at": evaluated_at.isoformat(),
            "bundles": bundles,
        }
        missing_dimensions = sorted(
            {
                f"{bundle['symbol']}:{dimension}"
                for bundle in bundles
                for dimension in list(bundle.get("missing_dimensions") or [])
            }
        )
        return {
            **proposal_payload,
            "proposal_hash": canonical_hash(proposal_payload),
            "bundle_count": len(bundles),
            "missing_dimensions": missing_dimensions,
            "degraded": any(bool(item["degraded"]) for item in bundles),
            "quality": (
                "degraded"
                if any(bool(item["degraded"]) for item in bundles)
                else "strict"
            ),
        }

    def _confirm_freeze_job(
        self,
        job_id: str,
        *,
        proposal_hash: str,
        acknowledge_degraded: bool,
        published_by: str,
    ) -> dict[str, Any]:
        with self._lock:
            return self._confirm_freeze_job_locked(
                job_id,
                proposal_hash=proposal_hash,
                acknowledge_degraded=acknowledge_degraded,
                published_by=published_by,
            )

    def _confirm_freeze_job_locked(
        self,
        job_id: str,
        *,
        proposal_hash: str,
        acknowledge_degraded: bool,
        published_by: str,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            job = self._lock_equity_job(session, job_id)
            if job is None or job.job_type not in EQUITY_COLLECTION_JOB_TYPES:
                raise CollectionJobError(
                    "collection_job_not_found", "未找到采集任务", status_code=404
                )
            checkpoint = dict(job.checkpoint_json or {})
            publication = dict(checkpoint.get("publication") or {})
            if publication.get("status") == "published":
                if str(proposal_hash) != str(publication.get("proposal_hash") or ""):
                    raise CollectionJobError(
                        "collection_freeze_proposal_changed",
                        "该任务已按另一冻结候选发布，请刷新任务查看已冻结版本",
                        status_code=409,
                    )
                return publication
            if dict(checkpoint.get("agent_recovery") or {}).get("status") in {
                "queued",
                "running",
                "cancelling",
            }:
                raise CollectionJobError(
                    "collection_freeze_recovery_running",
                    "Agent 补采尚未结束，不能确认冻结",
                    status_code=409,
                )
            baseline = checkpoint.get("publication_candidate")
            candidate = self._publication_candidate(session, job, baseline=baseline)
            expected_hash = str(proposal_hash)
            if expected_hash != candidate["proposal_hash"]:
                raise CollectionJobError(
                    "collection_freeze_proposal_changed",
                    "冻结候选已变化，请刷新任务并重新确认",
                    status_code=409,
                )
            if candidate["degraded"] and not acknowledge_degraded:
                raise CollectionJobError(
                    "collection_freeze_degraded_ack_required",
                    "候选包含降级数据，必须明确确认降级披露后才能冻结",
                    status_code=409,
                )
            evaluated_at = self._aware_datetime(
                candidate["evaluated_at"], fallback=job.completed_at
            )
            publisher = BundlePublisher(
                MarketDataRepository(session), registry=self.registry
            )
            published: list[dict[str, Any]] = []
            try:
                for proposal in candidate["bundles"]:
                    bundle = publisher.publish(
                        symbol=proposal["symbol"],
                        market=proposal["market"],
                        as_of_date=date.fromisoformat(proposal["as_of_date"]),
                        cutoff_at=self._aware_datetime(
                            proposal["cutoff_at"], fallback=evaluated_at
                        ),
                        exchange_timezone=proposal["exchange_timezone"],
                        published_by=published_by,
                        now=evaluated_at,
                        allow_degraded=bool(proposal["degraded"]),
                        expected_manifest_hash=proposal["manifest_hash"],
                    )
                    published.append(
                        {
                            "symbol": proposal["symbol"],
                            "as_of_date": proposal["as_of_date"],
                            "bundle_id": bundle.id,
                            "manifest_hash": bundle.manifest_hash,
                        }
                    )
            except (IncompleteBundleError, BundleIntegrityError, ValueError) as exc:
                raise CollectionJobError(
                    "collection_freeze_proposal_changed",
                    "冻结候选在发布前发生变化，请刷新后重试",
                    status_code=409,
                ) from exc
            confirmed_at = datetime.now(timezone.utc)
            publication = {
                "status": "published",
                "proposal_hash": candidate["proposal_hash"],
                "confirmed_at": confirmed_at.isoformat(),
                "confirmed_by": published_by,
                "bundles": published,
            }
            checkpoint["publication_candidate"] = candidate
            checkpoint["publication"] = publication
            job.checkpoint_json = checkpoint
            job.updated_at = confirmed_at
            return publication

    @staticmethod
    def _aware_datetime(value: Any, *, fallback: datetime | None) -> datetime:
        resolved = value
        if isinstance(resolved, str):
            resolved = datetime.fromisoformat(resolved.replace("Z", "+00:00"))
        if not isinstance(resolved, datetime):
            resolved = fallback or datetime.now(timezone.utc)
        if resolved.tzinfo is None:
            resolved = resolved.replace(tzinfo=timezone.utc)
        return resolved.astimezone(timezone.utc)

    @staticmethod
    def _lease_expired(value: Any, *, now: datetime) -> bool:
        if value is None:
            return True
        resolved = value
        if isinstance(resolved, str):
            try:
                resolved = datetime.fromisoformat(resolved.replace("Z", "+00:00"))
            except ValueError:
                return True
        if not isinstance(resolved, datetime):
            return True
        if resolved.tzinfo is None:
            resolved = resolved.replace(tzinfo=timezone.utc)
        return resolved.astimezone(timezone.utc) <= now.astimezone(timezone.utc)

    @staticmethod
    def _publication_fields(
        job: MarketDataCollectionJob,
        checkpoint: Mapping[str, Any],
    ) -> dict[str, Any]:
        publication = dict(checkpoint.get("publication") or {})
        candidate = checkpoint.get("publication_candidate")
        recovery = dict(checkpoint.get("agent_recovery") or {})
        if publication.get("status") == "published":
            return {
                "publication_status": "published",
                "freeze_status": "frozen",
                "can_confirm_freeze": False,
                "publication_candidate": candidate,
            }
        if recovery.get("status") in {"queued", "running", "cancelling"}:
            return {
                "publication_status": "recovering",
                "freeze_status": "awaiting_recovery",
                "can_confirm_freeze": False,
                "publication_candidate": None,
            }
        if job.status not in TERMINAL_COLLECTION_STATUSES:
            return {
                "publication_status": "collecting",
                "freeze_status": "collecting",
                "can_confirm_freeze": False,
                "publication_candidate": candidate,
            }
        if candidate:
            degraded = bool(candidate.get("degraded"))
            return {
                "publication_status": "pending_confirmation",
                "freeze_status": "ready_degraded" if degraded else "ready",
                "can_confirm_freeze": True,
                "publication_candidate": candidate,
            }
        return {
            "publication_status": "blocked",
            "freeze_status": "blocked",
            "can_confirm_freeze": False,
            "publication_candidate": None,
        }

    def _job_payload(
        self,
        session,
        job: MarketDataCollectionJob,
        *,
        include_tasks: bool = True,
    ) -> dict[str, Any]:
        tasks = (
            list(
                session.scalars(
                    select(MarketDataCollectionTask)
                    .where(MarketDataCollectionTask.job_id == job.id)
                    .order_by(
                        MarketDataCollectionTask.symbol,
                        MarketDataCollectionTask.dimension,
                    )
                )
            )
            if include_tasks
            else []
        )
        result_rows = (
            list(
                session.scalars(
                    select(MarketDataCollectionTaskResult)
                    .where(
                        MarketDataCollectionTaskResult.task_id.in_(
                            [task.id for task in tasks]
                        )
                    )
                    .order_by(
                        MarketDataCollectionTaskResult.task_id,
                        MarketDataCollectionTaskResult.as_of_date,
                        MarketDataCollectionTaskResult.attempt.desc(),
                    )
                )
            )
            if tasks
            else []
        )
        latest_results: dict[tuple[str, date], MarketDataCollectionTaskResult] = {}
        for result in result_rows:
            latest_results.setdefault((result.task_id, result.as_of_date), result)
        snapshot_ids = {
            result.snapshot_id
            for result in latest_results.values()
            if result.snapshot_id
        }
        snapshots_by_id = {
            snapshot.id: snapshot
            for snapshot in (
                list(
                    session.scalars(
                        select(MarketDataSnapshot).where(
                            MarketDataSnapshot.id.in_(snapshot_ids)
                        )
                    )
                )
                if snapshot_ids
                else []
            )
        }
        results_by_task: dict[str, list[MarketDataCollectionTaskResult]] = defaultdict(
            list
        )
        latest_result_by_task: dict[str, MarketDataCollectionTaskResult] = {}
        resolution_counts: dict[str, int] = defaultdict(int)
        for result in latest_results.values():
            results_by_task[result.task_id].append(result)
            resolution_counts[result.status] += 1
            current = latest_result_by_task.get(result.task_id)
            if current is None or (result.as_of_date, result.attempt) > (
                current.as_of_date,
                current.attempt,
            ):
                latest_result_by_task[result.task_id] = result

        def task_failure(task: MarketDataCollectionTask) -> dict[str, Any]:
            if not task.last_error_code and not task.last_error_message:
                return {
                    "failure_kind": None,
                    "failure_stage": None,
                    "failure_label": None,
                    "raw_data_present": False,
                }
            result = latest_result_by_task.get(task.id)
            metrics = dict(result.metrics_json or {}) if result is not None else {}
            diagnostic_message = task.last_error_message
            snapshot = (
                snapshots_by_id.get(result.snapshot_id)
                if result is not None and result.snapshot_id
                else None
            )
            if snapshot is not None:
                raw_payload = dict(snapshot.payload_json or {}).get("raw")
                metrics["raw_data_present"] = bool(raw_payload)
                validation = dict(snapshot.validation_json or {})
                detailed_errors = list(validation.get("semantic_errors") or [])
                if detailed_errors and diagnostic_message in {
                    None,
                    "",
                    "collector reported data_gap",
                }:
                    diagnostic_message = "; ".join(
                        str(item) for item in detailed_errors if str(item).strip()
                    )[:4000]
            return {
                "error_message": diagnostic_message,
                **classify_collection_failure(
                    error_code=task.last_error_code,
                    error_message=diagnostic_message,
                    provider_status=(
                        result.provider_status if result is not None else None
                    ),
                    metrics=metrics,
                ),
            }

        checkpoint = dict(job.checkpoint_json or {})
        return {
            "id": job.id,
            "job_id": job.id,
            "job_type": job.job_type,
            "origin": job.origin,
            "subscription_id": job.subscription_id,
            "parent_job_id": job.parent_job_id,
            "mode": job.mode,
            "status": job.status,
            "symbols": list(job.symbols_json or []),
            "dimensions": list(job.dimensions_json or []),
            "start_date": job.start_date.isoformat(),
            "end_date": job.end_date.isoformat(),
            "max_concurrency": max(
                MIN_COLLECTION_CONCURRENCY,
                min(int(job.max_concurrency), MAX_COLLECTION_CONCURRENCY),
            ),
            "publish_when_complete": job.publish_when_complete,
            "progress": dict(job.progress_json or {}),
            "checkpoint": checkpoint,
            "agent_recovery": checkpoint.get("agent_recovery"),
            **self._publication_fields(job, checkpoint),
            "cancel_requested": job.cancel_requested,
            "error_summary": job.error_summary,
            "created_at": iso(job.created_at),
            "started_at": iso(job.started_at),
            "completed_at": iso(job.completed_at),
            "resolution_summary": {
                "total": sum(resolution_counts.values()),
                "strict": resolution_counts.get("succeeded", 0),
                "degraded": resolution_counts.get("degraded", 0),
                "skipped": resolution_counts.get("skipped", 0),
                "failed": resolution_counts.get("failed", 0)
                + resolution_counts.get("data_gap", 0),
                "by_status": dict(resolution_counts),
            },
            "tasks": [
                {
                    "id": task.id,
                    "symbol": task.symbol,
                    "market": task.market,
                    "dimension_id": task.dimension,
                    "start_date": task.start_date.isoformat(),
                    "end_date": task.end_date.isoformat(),
                    "status": task.status,
                    "attempt_count": task.attempt_count,
                    "max_attempts": task.max_attempts,
                    "retryable": task.retryable,
                    "next_retry_at": iso(task.next_retry_at),
                    "error_code": task.last_error_code,
                    "error_message": task.last_error_message,
                    **task_failure(task),
                    "resolution_status": (
                        "audit_only"
                        if task.last_error_code == "strict_contract_gap"
                        else "degraded"
                        if any(
                            result.status == "degraded"
                            for result in results_by_task.get(task.id, [])
                        )
                        else "strict"
                        if results_by_task.get(task.id)
                        and all(
                            result.status in {"succeeded", "skipped"}
                            for result in results_by_task[task.id]
                        )
                        else task.status
                    ),
                    "degraded_count": sum(
                        result.status == "degraded"
                        for result in results_by_task.get(task.id, [])
                    ),
                    "snapshot_id": next(
                        (
                            result.snapshot_id
                            for result in reversed(results_by_task.get(task.id, []))
                            if result.snapshot_id
                        ),
                        None,
                    ),
                }
                for task in tasks
            ],
        }

    @staticmethod
    def _local_today(exchange_timezone: str) -> date:
        return datetime.now(timezone.utc).astimezone(ZoneInfo(exchange_timezone)).date()

    @staticmethod
    def _request_fingerprint(request: Mapping[str, Any]) -> str:
        normalized = dict(request)
        normalized["symbols"] = sorted(
            {
                str(symbol or "").strip().upper()
                for symbol in list(request.get("symbols") or [])
                if str(symbol or "").strip()
            }
        )
        normalized["dimensions"] = sorted(
            {
                str(dimension or "").strip()
                for dimension in list(request.get("dimensions") or [])
                if str(dimension or "").strip()
            }
        )
        return canonical_hash(normalized)

    @staticmethod
    def _find_idempotent_job(
        session,
        *,
        tenant_id: str,
        user_id: str,
        idempotency_key: str,
    ) -> MarketDataCollectionJob | None:
        return session.scalar(
            select(MarketDataCollectionJob).where(
                MarketDataCollectionJob.tenant_id == tenant_id,
                MarketDataCollectionJob.user_id == user_id,
                MarketDataCollectionJob.idempotency_key == idempotency_key,
            )
        )

    def _validate_idempotent_job(
        self,
        job: MarketDataCollectionJob,
        *,
        request: Mapping[str, Any],
    ) -> None:
        if job.job_type not in EQUITY_COLLECTION_JOB_TYPES:
            raise CollectionJobError(
                "collection_idempotency_conflict",
                "idempotency_key 已被其他类型采集任务使用",
                status_code=409,
            )
        if self._request_fingerprint(
            dict(job.request_json or {})
        ) != self._request_fingerprint(request):
            raise CollectionJobError(
                "collection_idempotency_conflict",
                "idempotency_key 已绑定其他股票采集请求",
                status_code=409,
            )

    def _idempotent_replay_job_id(
        self,
        *,
        tenant_id: str,
        user_id: str,
        idempotency_key: str,
        request: Mapping[str, Any],
    ) -> str | None:
        with self.database.session() as session:
            job = self._find_idempotent_job(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                idempotency_key=idempotency_key,
            )
            if job is None:
                return None
            self._validate_idempotent_job(job, request=request)
            return job.id

    def _dimensions(self, values: Iterable[str] | str) -> list[str]:
        if isinstance(values, str):
            if values.strip().lower() == "all":
                return list(self.registry.dimension_ids)
            values = [values]
        normalized = list(dict.fromkeys(str(item or "").strip() for item in values))
        unknown = [
            item for item in normalized if item not in self.registry.dimension_ids
        ]
        if unknown:
            raise CollectionJobError(
                "collection_dimensions_invalid",
                f"未知采集维度：{', '.join(unknown)}",
            )
        return normalized

    @staticmethod
    def _symbols(values: Iterable[str]) -> list[str]:
        return list(
            dict.fromkeys(
                str(item or "").strip().upper()
                for item in values
                if str(item or "").strip()
            )
        )


def market_scope(symbol: str) -> tuple[str, str]:
    upper = str(symbol or "").strip().upper()
    if upper.endswith(".HK") or upper.startswith("HK"):
        return "HK_EQUITY", "Asia/Hong_Kong"
    if upper.endswith((".US", ".NYSE", ".NASDAQ")):
        return "US_EQUITY", "America/New_York"
    return "CN_EQUITY", "Asia/Shanghai"


def symbol_key(symbol: str) -> str:
    upper = str(symbol or "").strip().upper()
    digits = "".join(character for character in upper if character.isdigit())
    return digits[-6:] if len(digits) >= 6 else upper


def date_range(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


__all__ = [
    "EQUITY_COLLECTION_JOB_TYPES",
    "CollectionJobError",
    "MarketDataCollectionCoordinator",
    "date_range",
    "market_scope",
    "symbol_key",
]
