from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from inspect import Parameter, signature
from threading import Event, RLock, Thread
from time import monotonic
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from research_system.market_data.bundles import (
    BundleIntegrityError,
    BundleMaterializer,
    BundlePublisher,
    IncompleteBundleError,
)
from research_system.market_data.collection import (
    DEFAULT_COLLECTION_CONCURRENCY,
    EQUITY_COLLECTION_JOB_TYPES,
    MAX_COLLECTION_CONCURRENCY,
    MIN_COLLECTION_CONCURRENCY,
    CollectionJobError,
    MarketDataCollectionCoordinator,
    iso,
    market_scope,
)
from research_system.market_data.contracts import (
    CollectionStatus,
    SnapshotStatus,
    canonical_hash,
)
from research_system.market_data.dimension_contracts import DimensionRegistry
from research_system.market_data.ingestion import SnapshotIngestionService
from research_system.market_data.models import (
    MarketDataCollectionJob,
    MarketDataCollectionTask,
    MarketDataCollectionTaskResult,
    MarketDataSubscription,
)
from research_system.market_data.repository import MarketDataRepository
from research_system.market_data.resolver import SnapshotResolver
from research_system.runtime.core.id_utils import generate_ulid
from research_system.strategies.database import StrategyDatabase

FUND_HOLDING_JOB_TYPE = "fund_holding_expansion"
FUND_MARKET = "CN_FUND"
FUND_EXCHANGE_TIMEZONE = "Asia/Shanghai"
_FUND_CODE = re.compile(r"^(?:OF)?(?P<code>\d{6})(?:\.(?:OF|FUND))?$", re.IGNORECASE)
_A_SHARE_SYMBOL = re.compile(r"^(?P<code>\d{6})\.(?P<exchange>SH|SZ|BJ)$")
_FUND_LEASE_DURATION = timedelta(minutes=30)
_FUND_LEASE_RENEW_INTERVAL_SECONDS = 60.0
_FUND_DURABLE_SIGNAL_POLL_SECONDS = 0.25

FundSnapshotLoader = Callable[..., Mapping[str, Any]]
Clock = Callable[[], datetime]


class _FundExpansionFailure(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        provider_status: str,
        retryable: bool,
        snapshot_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.provider_status = provider_status
        self.retryable = retryable
        self.snapshot_id = snapshot_id


class _FundExpansionCancelled(RuntimeError):
    pass


class _FundExpansionLeaseLost(RuntimeError):
    """Stop a reclaimed ordinary fund worker without stale writes."""


class _FundAgentRecoveryLeaseLost(RuntimeError):
    """Stop an obsolete recovery worker without mutating durable state."""


class _CancellationView:
    def __init__(self, *events: Event) -> None:
        self._events = events

    def is_set(self) -> bool:
        return any(event.is_set() for event in self._events)

    def wait(self, timeout: float | None = None) -> bool:
        if self.is_set():
            return True
        deadline = None if timeout is None else monotonic() + max(timeout, 0.0)
        while not self.is_set():
            remaining = (
                None if deadline is None else max(deadline - monotonic(), 0.0)
            )
            if remaining == 0.0:
                return False
            self._events[0].wait(
                0.05 if remaining is None else min(0.05, remaining)
            )
        return True


class FundHoldingCollectionCoordinator:
    """Expand one persisted fund holding slot into ordinary equity collection jobs.

    The parent owns exactly one ``fund_top_holdings`` task and one ``CN_FUND``
    bundle.  Its ten holdings are then grouped by their still-missing equity
    dimensions and passed to the existing equity coordinator.  The fund skill
    is only allowed on the current Shanghai date; historical executions must
    resolve an already-persisted PIT snapshot.
    """

    def __init__(
        self,
        database: StrategyDatabase,
        *,
        equity_collection: MarketDataCollectionCoordinator | None = None,
        fund_snapshot_loader: FundSnapshotLoader | Any | None = None,
        adapter: Any | None = None,
        registry: DimensionRegistry | None = None,
        max_background_jobs: int = 1,
        clock: Clock | None = None,
    ) -> None:
        if registry is None:
            from research_system.market_data.fund_holdings_contracts import (
                FUND_TOP_HOLDINGS_REGISTRY,
            )

            registry = FUND_TOP_HOLDINGS_REGISTRY
        if adapter is None:
            from research_system.market_data.fund_holdings_adapter import (
                FundTopHoldingsSnapshotAdapter,
            )

            adapter = FundTopHoldingsSnapshotAdapter(registry=registry)
        self.database = database
        self.registry = registry
        self.adapter = adapter
        self.fund_snapshot_loader = fund_snapshot_loader
        self._owns_equity_collection = equity_collection is None
        self.equity_collection = equity_collection or MarketDataCollectionCoordinator(
            database
        )
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, int(max_background_jobs)),
            thread_name_prefix="fund-holding-job",
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
        fund_code: str | None = None,
        as_of_date: date | None = None,
        symbols: Iterable[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        dimensions: Iterable[str] | str | None = None,
        equity_dimensions: Iterable[str] | str = "all",
        mode: str = "missing_or_stale",
        max_concurrency: int = DEFAULT_COLLECTION_CONCURRENCY,
        publish_when_complete: bool = False,
        idempotency_key: str | None = None,
        investment_cycle: str = "long_term",
        origin: str = "manual",
        subscription_id: str | None = None,
        subscription_claim_until: datetime | None = None,
        submit: bool = True,
    ) -> dict[str, Any]:
        normalized_code = self._resolve_fund_code(fund_code=fund_code, symbols=symbols)
        resolved_as_of = self._resolve_as_of_date(
            as_of_date=as_of_date,
            start_date=start_date,
            end_date=end_date,
        )
        self._validate_parent_dimensions(dimensions)
        current_date = self._local_today()
        if resolved_as_of > current_date:
            raise CollectionJobError(
                "collection_future_date",
                "基金持仓采集不能请求上海市场尚未发生的未来日期",
            )
        if mode not in {"missing_or_stale", "force_all"}:
            raise CollectionJobError("collection_mode_invalid", "不支持的采集模式")
        normalized_origin = str(origin or "manual").strip().lower()
        if normalized_origin not in {"manual", "subscription"}:
            raise CollectionJobError(
                "collection_origin_invalid", "不支持的采集任务来源"
            )
        normalized_subscription_id = str(subscription_id or "").strip() or None
        if normalized_origin == "subscription" and normalized_subscription_id is None:
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
        if investment_cycle not in {"short_term", "mid_term", "long_term"}:
            raise CollectionJobError(
                "fund_investment_cycle_invalid",
                "investment_cycle 必须是 short_term、mid_term 或 long_term",
            )
        normalized_dimensions = self._equity_dimensions(equity_dimensions)
        if normalized_dimensions != list(self.equity_collection.registry.dimension_ids):
            raise CollectionJobError(
                "fund_equity_contract_partial",
                "基金持仓扩展必须覆盖完整股票数据合同，不能选择部分维度",
            )
        concurrency = int(max_concurrency)
        if not MIN_COLLECTION_CONCURRENCY <= concurrency <= MAX_COLLECTION_CONCURRENCY:
            raise CollectionJobError(
                "collection_concurrency_invalid",
                "采集并发必须在 1 到 5 之间",
                status_code=422,
            )
        request = {
            "fund_code": normalized_code,
            "symbols": [normalized_code],
            "as_of_date": resolved_as_of.isoformat(),
            "start_date": resolved_as_of.isoformat(),
            "end_date": resolved_as_of.isoformat(),
            "dimensions": [self._fund_dimension],
            "equity_dimensions": normalized_dimensions,
            "mode": mode,
            "max_concurrency": concurrency,
            "publish_when_complete": bool(publish_when_complete),
            "origin": normalized_origin,
            "subscription_id": normalized_subscription_id,
            "asset_family": "fund",
            "top_n": 10,
            "investment_cycle": investment_cycle,
        }
        resolved_key = str(idempotency_key or generate_ulid())
        created = False
        try:
            with self.database.session() as session:
                existing = self._idempotent_job(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    idempotency_key=resolved_key,
                )
                if existing is not None:
                    job_id = self._validate_idempotent_job(existing, request=request)
                else:
                    if (
                        normalized_origin == "subscription"
                        and normalized_claim_until is not None
                    ):
                        launch = session.execute(
                            update(MarketDataSubscription)
                            .where(
                                MarketDataSubscription.id
                                == normalized_subscription_id,
                                MarketDataSubscription.tenant_id == tenant_id,
                                MarketDataSubscription.user_id == user_id,
                                MarketDataSubscription.enabled.is_(True),
                                MarketDataSubscription.next_run_at
                                == normalized_claim_until,
                                MarketDataSubscription.last_status == "authorized",
                            )
                            .values(last_status="launching", updated_at=self._now())
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
                        subscription_id=normalized_subscription_id,
                        parent_job_id=None,
                        job_type=FUND_HOLDING_JOB_TYPE,
                        mode=mode,
                        status=CollectionStatus.QUEUED.value,
                        symbols_json=[normalized_code],
                        dimensions_json=[self._fund_dimension],
                        start_date=resolved_as_of,
                        end_date=resolved_as_of,
                        max_concurrency=concurrency,
                        publish_when_complete=bool(publish_when_complete),
                        request_json=request,
                        progress_json={"completed": 0, "total": 1},
                        checkpoint_json={},
                        requested_by=actor_id,
                    )
                    session.add(job)
                    session.flush()
                    session.add(
                        MarketDataCollectionTask(
                            job_id=job.id,
                            symbol=normalized_code,
                            market=FUND_MARKET,
                            exchange_timezone=FUND_EXCHANGE_TIMEZONE,
                            dimension=self._fund_dimension,
                            start_date=resolved_as_of,
                            end_date=resolved_as_of,
                            status=CollectionStatus.QUEUED.value,
                        )
                    )
                    job_id = job.id
                    created = True
        except CollectionJobError as exc:
            if exc.code != "collection_subscription_claim_lost":
                raise
            with self.database.session() as session:
                existing = self._idempotent_job(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    idempotency_key=resolved_key,
                )
                if existing is None:
                    raise
                job_id = self._validate_idempotent_job(existing, request=request)
                created = False
        except IntegrityError:
            # A concurrent creator may win after our SELECT.  Re-read in a
            # clean transaction because the failed INSERT transaction is
            # unusable on both PostgreSQL and SQLite.
            with self.database.session() as session:
                existing = self._idempotent_job(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    idempotency_key=resolved_key,
                )
                if existing is None:
                    raise
                job_id = self._validate_idempotent_job(existing, request=request)
                created = False
        if submit and created:
            self.submit(job_id)
        return self.get_job(job_id, tenant_id=tenant_id, user_id=user_id)

    @staticmethod
    def _idempotent_job(
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

    @staticmethod
    def _validate_idempotent_job(
        existing: MarketDataCollectionJob,
        *,
        request: Mapping[str, Any],
    ) -> str:
        if existing.job_type != FUND_HOLDING_JOB_TYPE:
            raise CollectionJobError(
                "collection_idempotency_conflict",
                "idempotency_key 已被其他类型采集任务使用",
                status_code=409,
            )
        if canonical_hash(dict(existing.request_json or {})) != canonical_hash(
            dict(request)
        ):
            raise CollectionJobError(
                "collection_idempotency_conflict",
                "idempotency_key 已绑定其他基金采集请求",
                status_code=409,
            )
        return existing.id

    def submit(self, job_id: str) -> None:
        with self._lock:
            future = self._futures.get(job_id)
            if future is not None and not future.done():
                return
            with self.database.session() as session:
                job = session.get(MarketDataCollectionJob, job_id)
                if (
                    job is None
                    or job.job_type != FUND_HOLDING_JOB_TYPE
                    or job.status != CollectionStatus.QUEUED.value
                    or job.cancel_requested
                ):
                    return
            cancellation = self._cancellations.setdefault(job_id, Event())
            cancellation.clear()
            future = self._executor.submit(
                self._run_job,
                job_id,
                cancellation,
            )
            self._futures[job_id] = future
            future.add_done_callback(
                lambda completed, target=job_id: self._release_future(
                    target,
                    completed,
                    recovery=False,
                )
            )

    def run_job_now(self, job_id: str) -> None:
        with self._lock:
            cancellation = self._cancellations.setdefault(job_id, Event())
            cancellation.clear()
        try:
            self._run_job(job_id, cancellation)
        finally:
            self._release_idle_cancellation(job_id)

    def retry_failed(
        self,
        job_id: str,
        *,
        tenant_id: str,
        user_id: str,
        actor_id: str,
        symbols: list[str] | None = None,
        dimensions: list[str] | None = None,
        submit: bool = True,
    ) -> dict[str, Any]:
        with self._lock:
            return self._retry_failed_locked(
                job_id,
                tenant_id=tenant_id,
                user_id=user_id,
                actor_id=actor_id,
                symbols=symbols,
                dimensions=dimensions,
                submit=submit,
            )

    def _retry_failed_locked(
        self,
        job_id: str,
        *,
        tenant_id: str,
        user_id: str,
        actor_id: str,
        symbols: list[str] | None,
        dimensions: list[str] | None,
        submit: bool,
    ) -> dict[str, Any]:
        requested_symbols = list(
            dict.fromkeys(
                str(symbol or "").strip().upper()
                for symbol in (symbols or [])
                if str(symbol or "").strip()
            )
        )
        requested_dimensions = list(
            dict.fromkeys(
                str(dimension or "").strip()
                for dimension in (dimensions or [])
                if str(dimension or "").strip()
            )
        )
        scoped = bool(requested_symbols or requested_dimensions)
        unknown_dimensions = set(requested_dimensions).difference(
            self.equity_collection.registry.dimension_ids
        )
        if unknown_dimensions:
            raise CollectionJobError(
                "collection_retry_scope_invalid",
                "重试范围包含未知维度：" + "、".join(sorted(unknown_dimensions)),
                status_code=422,
            )
        with self.database.session() as session:
            job = self._owned_parent_job(
                session,
                job_id,
                tenant_id=tenant_id,
                user_id=user_id,
                lock=True,
            )
            checkpoint = dict(job.checkpoint_json or {})
            if dict(checkpoint.get("publication") or {}).get("status") == "published":
                raise CollectionJobError(
                    "collection_retry_bundle_frozen",
                    "组合 Bundle 已冻结；补采需要创建新的基金采集任务与修订版",
                    status_code=409,
                )
            if dict(checkpoint.get("agent_recovery") or {}).get("status") in {
                "queued",
                "running",
                "cancelling",
            }:
                raise CollectionJobError(
                    "collection_retry_recovery_running",
                    "组合 Agent 补采尚未结束，不能同时重试采集槽",
                    status_code=409,
                )
            if job.status not in {
                CollectionStatus.FAILED.value,
                CollectionStatus.PARTIAL.value,
            }:
                raise CollectionJobError(
                    "collection_retry_unavailable",
                    "基金持仓扩展任务当前没有可重试缺口",
                    status_code=409,
                )
            task = self._parent_task(session, job.id)
            if not scoped and (
                not task.retryable or task.attempt_count >= task.max_attempts
            ):
                raise CollectionJobError(
                    "collection_retry_unavailable",
                    "没有可重试的实时采集缺口；历史 PIT 不允许 live 回填",
                    status_code=409,
                )
            if scoped:
                holdings = {
                    str(holding.get("symbol") or "").upper()
                    for holding in list(checkpoint.get("holdings") or [])
                    if str(holding.get("symbol") or "").strip()
                }
                selected_symbols = (
                    set(requested_symbols) if requested_symbols else holdings
                )
                unknown_symbols = selected_symbols.difference(holdings)
                if unknown_symbols:
                    raise CollectionJobError(
                        "collection_retry_scope_invalid",
                        "重试范围包含非本基金持仓股票："
                        + "、".join(sorted(unknown_symbols)),
                        status_code=422,
                    )
                child_ids = [
                    str(child_id)
                    for child_id in list(checkpoint.get("child_job_ids") or [])
                    if str(child_id).strip()
                ]
                child_tasks = (
                    list(
                        session.scalars(
                            select(MarketDataCollectionTask).where(
                                MarketDataCollectionTask.job_id.in_(child_ids),
                                MarketDataCollectionTask.symbol.in_(selected_symbols),
                            )
                        )
                    )
                    if child_ids
                    else []
                )
                child_order = {
                    child_id: index for index, child_id in enumerate(child_ids)
                }
                latest_tasks: dict[tuple[str, str], MarketDataCollectionTask] = {}
                for child_task in child_tasks:
                    slot = (child_task.symbol, child_task.dimension)
                    current = latest_tasks.get(slot)
                    if current is None or child_order.get(
                        child_task.job_id, -1
                    ) > child_order.get(current.job_id, -1):
                        latest_tasks[slot] = child_task
                dimension_scope = set(requested_dimensions)
                retry_scope: dict[str, list[str]] = defaultdict(list)
                for child_task in latest_tasks.values():
                    if dimension_scope and child_task.dimension not in dimension_scope:
                        continue
                    if child_task.status not in {
                        CollectionStatus.FAILED.value,
                        CollectionStatus.PARTIAL.value,
                    }:
                        continue
                    if (
                        not child_task.retryable
                        or child_task.attempt_count >= child_task.max_attempts
                    ):
                        continue
                    retry_scope[child_task.symbol].append(child_task.dimension)
                if not retry_scope:
                    raise CollectionJobError(
                        "collection_retry_unavailable",
                        "所选股票当前没有可手动重试的失败维度",
                        status_code=409,
                    )
                checkpoint["retry_scope_by_symbol"] = {
                    symbol: sorted(set(retry_scope[symbol]))
                    for symbol in sorted(retry_scope)
                }
            else:
                checkpoint.pop("retry_scope_by_symbol", None)
            checkpoint.pop("publication_candidate", None)
            previous_recovery = dict(checkpoint.get("agent_recovery") or {})
            if previous_recovery:
                checkpoint["agent_recovery"] = {
                    **previous_recovery,
                    "status": "superseded_by_retry",
                    "message": "新一轮 provider 重试已启动，旧组合 Agent 结论不再作为冻结依据",
                }
            checkpoint["publication"] = {
                "status": "blocked",
                "error_code": "collection_retry_pending",
                "error_message": "重试完成并重新计算组合候选前不能冻结",
                "updated_at": iso(self._now()),
            }
            job.checkpoint_json = checkpoint
            now = self._now()
            task.status = CollectionStatus.QUEUED.value
            task.retryable = True
            task.next_retry_at = None
            task.lease_owner = None
            task.lease_until = None
            task.updated_at = now
            job.status = CollectionStatus.QUEUED.value
            job.cancel_requested = False
            job.error_summary = None
            job.completed_at = None
            job.requested_by = actor_id
            job.updated_at = now
        if submit:
            self.submit(job_id)
        return self.get_job(job_id, tenant_id=tenant_id, user_id=user_id)

    def get_job(self, job_id: str, *, tenant_id: str, user_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            job = self._owned_parent_job(
                session,
                job_id,
                tenant_id=tenant_id,
                user_id=user_id,
            )
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
                self._owned_parent_job(
                    session, job_id, tenant_id=tenant_id, user_id=user_id
                )
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
            job = self._owned_parent_job(
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
                    "基金持仓与子股票采集尚未结束，不能启动 Agent 补采",
                    status_code=409,
                )
            checkpoint = dict(job.checkpoint_json or {})
            if dict(checkpoint.get("publication") or {}).get("status") == "published":
                raise CollectionJobError(
                    "agent_recovery_bundle_frozen",
                    "组合 Bundle 已冻结；如需补采请创建新的采集任务",
                    status_code=409,
                )
            child_ids = [
                str(child_id)
                for child_id in list(checkpoint.get("child_job_ids") or [])
                if str(child_id).strip()
            ]
            if not child_ids:
                raise CollectionJobError(
                    "agent_recovery_children_missing",
                    "基金任务尚未形成前十大持仓股票采集任务",
                    status_code=409,
                )
            recovery = dict(checkpoint.get("agent_recovery") or {})
            if recovery.get("status") not in {"queued", "running", "cancelling"}:
                now = self._now()
                checkpoint.pop("publication_candidate", None)
                checkpoint["publication"] = {
                    "status": "blocked",
                    "error_code": "agent_recovery_pending",
                    "error_message": "前十大持仓 Agent 补采完成前不能冻结",
                    "updated_at": iso(now),
                }
                checkpoint["agent_recovery"] = {
                    "status": "queued",
                    "lease_owner": None,
                    "lease_until": None,
                    "requested_at": iso(now),
                    "requested_by": actor_id,
                    "attempted": 0,
                    "succeeded": 0,
                    "failed": 0,
                    "message": "等待逐只执行 UZI Agent 缺口补采",
                }
                job.checkpoint_json = checkpoint
                job.updated_at = now
        self._submit_agent_recovery(
            job_id,
            tenant_id=tenant_id,
            user_id=user_id,
            actor_id=actor_id,
            child_ids=child_ids,
        )
        return self.get_job(job_id, tenant_id=tenant_id, user_id=user_id)

    def _submit_agent_recovery(
        self,
        job_id: str,
        *,
        tenant_id: str,
        user_id: str,
        actor_id: str,
        child_ids: list[str],
    ) -> None:
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
                    or job.job_type != FUND_HOLDING_JOB_TYPE
                    or recovery.get("status") != "queued"
                ):
                    return
            cancellation = self._cancellations.setdefault(job_id, Event())
            cancellation.clear()
            future = self._executor.submit(
                self._run_agent_recovery,
                job_id,
                tenant_id,
                user_id,
                actor_id,
                child_ids,
                cancellation,
            )
            self._recovery_futures[job_id] = future
            future.add_done_callback(
                lambda completed, target=job_id: self._release_future(
                    target,
                    completed,
                    recovery=True,
                )
            )

    def _release_future(
        self,
        job_id: str,
        future: Future[None],
        *,
        recovery: bool,
    ) -> None:
        with self._lock:
            futures = self._recovery_futures if recovery else self._futures
            if futures.get(job_id) is future:
                futures.pop(job_id, None)
            self._release_idle_cancellation(job_id)

    def _release_idle_cancellation(self, job_id: str) -> None:
        with self._lock:
            normal = self._futures.get(job_id)
            recovery = self._recovery_futures.get(job_id)
            if (normal is None or normal.done()) and (
                recovery is None or recovery.done()
            ):
                self._cancellations.pop(job_id, None)

    def cancel(
        self,
        job_id: str,
        *,
        tenant_id: str,
        user_id: str,
        actor_id: str,
    ) -> dict[str, Any]:
        cancelling_recovery = False
        child_ids: list[str] = []
        with self._lock:
            with self.database.session() as session:
                job = self._owned_parent_job(
                    session,
                    job_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    lock=True,
                )
                checkpoint = dict(job.checkpoint_json or {})
                recovery = dict(checkpoint.get("agent_recovery") or {})
                if recovery.get("status") in {"queued", "running"}:
                    now = self._now()
                    recovery.update(
                        {
                            "status": "cancelling",
                            "cancel_requested_at": iso(now),
                            "message": "正在取消组合 Agent 缺口补采",
                        }
                    )
                    checkpoint["agent_recovery"] = recovery
                    job.checkpoint_json = checkpoint
                    job.updated_at = now
                    cancelling_recovery = True
                    child_ids = [
                        str(child_id)
                        for child_id in list(checkpoint.get("child_job_ids") or [])
                        if str(child_id).strip()
                    ]
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
                    child_ids = list(
                        session.scalars(
                            select(MarketDataCollectionJob.id).where(
                                MarketDataCollectionJob.parent_job_id == job.id,
                                MarketDataCollectionJob.job_type.in_(
                                    EQUITY_COLLECTION_JOB_TYPES
                                ),
                                MarketDataCollectionJob.status.in_(
                                    (
                                        CollectionStatus.QUEUED.value,
                                        CollectionStatus.RUNNING.value,
                                    )
                                ),
                            )
                        )
                    )
                    job.cancel_requested = True
                    job.updated_at = self._now()
            # Final success is persisted under the same lock, so setting this
            # event linearizes cancellation ahead of any later completion.
            self._cancellations.setdefault(job_id, Event()).set()

        cancel_child = getattr(self.equity_collection, "cancel", None)
        if callable(cancel_child):
            for child_id in child_ids:
                try:
                    cancel_child(
                        child_id,
                        tenant_id=tenant_id,
                        user_id=user_id,
                        actor_id=actor_id,
                    )
                except CollectionJobError:
                    # The child may have reached a terminal state between the
                    # ownership query and propagation; parent cancellation is
                    # still authoritative.
                    continue
        with self._lock, self.database.session() as session:
            self._cancel_linked_active_children(
                session,
                parent_job_id=job_id,
                now=self._now(),
                reason="parent fund holding collection was cancelled",
            )
        return self.get_job(job_id, tenant_id=tenant_id, user_id=user_id)

    def resume_pending(self, *, resume_subscription_jobs: bool = True) -> list[str]:
        with self._lock:
            with self.database.session() as session:
                now = self._now()
                cancelled_jobs = list(
                    session.scalars(
                        select(MarketDataCollectionJob).where(
                            MarketDataCollectionJob.job_type == FUND_HOLDING_JOB_TYPE,
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
                for listed_job in cancelled_jobs:
                    job = self._lock_fund_job(session, listed_job.id)
                    if (
                        job is None
                        or job.status
                        not in {
                            CollectionStatus.QUEUED.value,
                            CollectionStatus.RUNNING.value,
                        }
                        or not job.cancel_requested
                    ):
                        continue
                    self._finalize_cancelled_parent(
                        session,
                        job,
                        now=now,
                        reason="服务重启时完成已持久化的基金采集取消",
                    )

                jobs = list(
                    session.scalars(
                        select(MarketDataCollectionJob).where(
                            MarketDataCollectionJob.job_type == FUND_HOLDING_JOB_TYPE,
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
                resumable: list[MarketDataCollectionJob] = []
                for listed_job in jobs:
                    job = self._lock_fund_job(session, listed_job.id)
                    if (
                        job is None
                        or job.status
                        not in {
                            CollectionStatus.QUEUED.value,
                            CollectionStatus.RUNNING.value,
                        }
                        or job.cancel_requested
                    ):
                        continue
                    task = self._parent_task(session, job.id)
                    session.refresh(task)
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
                            self._finalize_cancelled_parent(
                                session,
                                job,
                                now=now,
                                reason=(
                                    "subscription job not resumed because global or "
                                    "per-symbol opt-in is disabled"
                                ),
                            )
                            continue
                    if (
                        job.status == CollectionStatus.RUNNING.value
                        and not self._lease_expired(job.lease_until, now=now)
                    ):
                        continue
                    if (
                        task.status == CollectionStatus.RUNNING.value
                        and not self._lease_expired(task.lease_until, now=now)
                    ):
                        continue
                    job.status = CollectionStatus.QUEUED.value
                    job.lease_owner = None
                    job.lease_until = None
                    job.updated_at = now
                    if task.status == CollectionStatus.RUNNING.value:
                        task.status = CollectionStatus.QUEUED.value
                        task.lease_owner = None
                        task.lease_until = None
                        task.updated_at = now
                    resumable.append(job)
                job_ids = [job.id for job in resumable]
                recovery_specs: list[dict[str, Any]] = []
                recovery_cancel_specs: list[dict[str, str]] = []
                recovery_child_ids: list[str] = []
                terminal_jobs = list(
                    session.scalars(
                        select(MarketDataCollectionJob).where(
                            MarketDataCollectionJob.job_type == FUND_HOLDING_JOB_TYPE,
                            MarketDataCollectionJob.status.in_(
                                (
                                    CollectionStatus.SUCCEEDED.value,
                                    CollectionStatus.PARTIAL.value,
                                    CollectionStatus.FAILED.value,
                                )
                            ),
                            MarketDataCollectionJob.cancel_requested.is_(False),
                        )
                    )
                )
                for listed_job in terminal_jobs:
                    job = self._lock_fund_job(session, listed_job.id)
                    if (
                        job is None
                        or job.status
                        not in {
                            CollectionStatus.SUCCEEDED.value,
                            CollectionStatus.PARTIAL.value,
                            CollectionStatus.FAILED.value,
                        }
                        or job.cancel_requested
                    ):
                        continue
                    checkpoint = dict(job.checkpoint_json or {})
                    recovery = dict(checkpoint.get("agent_recovery") or {})
                    if recovery.get("status") == "cancelling":
                        cancelling_child_ids = [
                            str(child_id)
                            for child_id in list(checkpoint.get("child_job_ids") or [])
                            if str(child_id).strip()
                        ]
                        recovery_child_ids.extend(cancelling_child_ids)
                        recovery_cancel_specs.extend(
                            {
                                "job_id": child_id,
                                "tenant_id": job.tenant_id,
                                "user_id": job.user_id,
                                "actor_id": str(
                                    recovery.get("requested_by")
                                    or job.requested_by
                                    or "system"
                                ),
                            }
                            for child_id in cancelling_child_ids
                        )
                        recovery.update(
                            {
                                "status": "cancelled",
                                "lease_owner": None,
                                "lease_until": None,
                                "completed_at": iso(now),
                                "message": "服务重启时完成组合 Agent 补采取消",
                            }
                        )
                        checkpoint["agent_recovery"] = recovery
                        job.checkpoint_json = checkpoint
                        job.updated_at = now
                        continue
                    if recovery.get("status") not in {"queued", "running"}:
                        continue
                    if (
                        dict(checkpoint.get("publication") or {}).get("status")
                        == "published"
                    ):
                        continue
                    if recovery.get("status") == "running" and not self._lease_expired(
                        self._optional_datetime(recovery.get("lease_until")),
                        now=now,
                    ):
                        # Another process still owns this durable recovery
                        # wave.  A rolling restart must not enqueue a second
                        # parent worker while that lease remains valid.
                        continue
                    child_ids = [
                        str(child_id)
                        for child_id in list(checkpoint.get("child_job_ids") or [])
                        if str(child_id).strip()
                    ]
                    if not child_ids:
                        recovery.update(
                            {
                                "status": "failed",
                                "completed_at": iso(now),
                                "error_code": "agent_recovery_children_missing",
                                "message": "服务重启后未找到持久化子任务，无法继续组合补采",
                            }
                        )
                        checkpoint["agent_recovery"] = recovery
                        job.checkpoint_json = checkpoint
                        continue
                    recovery.update(
                        {
                            "status": "queued",
                            "lease_owner": None,
                            "lease_until": None,
                            "resumed_at": iso(now),
                            "message": "服务重启后继续逐只执行 UZI Agent 缺口补采",
                        }
                    )
                    checkpoint["agent_recovery"] = recovery
                    job.checkpoint_json = checkpoint
                    job.updated_at = now
                    recovery_child_ids.extend(child_ids)
                    recovery_specs.append(
                        {
                            "job_id": job.id,
                            "tenant_id": job.tenant_id,
                            "user_id": job.user_id,
                            "actor_id": str(
                                recovery.get("requested_by")
                                or job.requested_by
                                or "system"
                            ),
                            "child_ids": child_ids,
                        }
                    )
            cancel_child = getattr(self.equity_collection, "cancel", None)
            if callable(cancel_child):
                for cancel_spec in recovery_cancel_specs:
                    try:
                        cancel_child(**cancel_spec)
                    except CollectionJobError:
                        continue
            if recovery_child_ids:
                self._resume_equity_agent_children(recovery_child_ids)
            for job_id in job_ids:
                self.submit(job_id)
            for spec in recovery_specs:
                self._submit_agent_recovery(**spec)
            return list(
                dict.fromkeys(
                    [*job_ids, *(str(spec["job_id"]) for spec in recovery_specs)]
                )
            )

    def _resume_equity_agent_children(self, child_ids: Iterable[str]) -> int:
        resume_children = getattr(
            self.equity_collection,
            "resume_agent_recovery_jobs",
            None,
        )
        if not callable(resume_children):
            resume_children = getattr(
                getattr(self.equity_collection, "delegate", None),
                "resume_agent_recovery_jobs",
                None,
            )
        if not callable(resume_children):
            return 0
        return int(resume_children(list(dict.fromkeys(child_ids))) or 0)

    def _finalize_cancelled_parent(
        self,
        session,
        job: MarketDataCollectionJob,
        *,
        now: datetime,
        reason: str,
    ) -> None:
        task = self._parent_task(session, job.id)
        session.refresh(task)
        checkpoint = dict(job.checkpoint_json or {})
        recovery = dict(checkpoint.get("agent_recovery") or {})
        if recovery.get("status") in {"queued", "running", "cancelling"}:
            recovery.update(
                {
                    "status": "cancelled",
                    "lease_owner": None,
                    "lease_until": None,
                    "completed_at": iso(now),
                    "message": "服务重启时完成已持久化的组合取消",
                }
            )
            checkpoint["agent_recovery"] = recovery
            checkpoint.pop("publication_candidate", None)
            checkpoint["publication"] = {
                "status": "blocked",
                "error_code": "agent_recovery_cancelled",
                "error_message": "组合补采已取消",
                "updated_at": iso(now),
            }
            job.checkpoint_json = checkpoint
        job.status = CollectionStatus.CANCELLED.value
        job.cancel_requested = True
        job.lease_owner = None
        job.lease_until = None
        job.error_summary = reason
        job.progress_json = {
            "completed": 1,
            "total": 1,
            "by_status": {CollectionStatus.CANCELLED.value: 1},
        }
        job.updated_at = now
        job.completed_at = now
        task.status = CollectionStatus.CANCELLED.value
        task.retryable = False
        task.next_retry_at = None
        task.lease_owner = None
        task.lease_until = None
        task.updated_at = now
        task.completed_at = now
        self._cancel_linked_active_children(
            session,
            parent_job_id=job.id,
            now=now,
            reason=reason,
        )

    @staticmethod
    def _lease_expired(value: datetime | None, *, now: datetime) -> bool:
        if value is None:
            return True
        if value.tzinfo is None or value.utcoffset() is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc) <= now.astimezone(timezone.utc)

    @classmethod
    def _owns_active_parent_lease(
        cls,
        job: MarketDataCollectionJob,
        task: MarketDataCollectionTask,
        *,
        lease_owner: str,
        now: datetime,
        allow_cancel_requested: bool = False,
    ) -> bool:
        return bool(
            job.status == CollectionStatus.RUNNING.value
            and task.status == CollectionStatus.RUNNING.value
            and job.lease_owner == lease_owner
            and task.lease_owner == lease_owner
            and not cls._lease_expired(job.lease_until, now=now)
            and not cls._lease_expired(task.lease_until, now=now)
            and (allow_cancel_requested or not job.cancel_requested)
        )

    @staticmethod
    def _lock_fund_job(session, job_id: str) -> MarketDataCollectionJob | None:
        """Take a write lock that also serializes correctly on SQLite."""

        locked = session.execute(
            update(MarketDataCollectionJob)
            .where(
                MarketDataCollectionJob.id == job_id,
                MarketDataCollectionJob.job_type == FUND_HOLDING_JOB_TYPE,
            )
            .values(updated_at=MarketDataCollectionJob.updated_at)
        )
        if locked.rowcount != 1:
            return None
        job = session.get(MarketDataCollectionJob, job_id)
        if job is not None:
            session.refresh(job)
        return job

    def _renew_fund_collection_lease(
        self,
        job_id: str,
        task_id: str,
        lease_owner: str,
    ) -> str:
        now = self._now()
        with self.database.session() as session:
            job = self._lock_fund_job(session, job_id)
            task = session.get(MarketDataCollectionTask, task_id)
            if (
                job is None
                or task is None
                or not self._owns_active_parent_lease(
                    job,
                    task,
                    lease_owner=lease_owner,
                    now=now,
                    allow_cancel_requested=True,
                )
            ):
                return "lost"
            if job.cancel_requested:
                return "cancelled"
            lease_until = now + _FUND_LEASE_DURATION
            job.lease_until = lease_until
            job.updated_at = now
            task.lease_until = lease_until
            task.updated_at = now
            return "renewed"

    def _inspect_fund_collection_lease(
        self,
        job_id: str,
        task_id: str,
        lease_owner: str,
    ) -> str:
        now = self._now()
        with self.database.session() as session:
            job = session.get(MarketDataCollectionJob, job_id)
            task = session.get(MarketDataCollectionTask, task_id)
            if (
                job is None
                or task is None
                or not self._owns_active_parent_lease(
                    job,
                    task,
                    lease_owner=lease_owner,
                    now=now,
                    allow_cancel_requested=True,
                )
            ):
                return "lost"
            return "cancelled" if job.cancel_requested else "active"

    def _heartbeat_fund_collection_lease(
        self,
        job_id: str,
        task_id: str,
        lease_owner: str,
        initial_lease_until: datetime,
        stop: Event,
        lost: Event,
        durable_cancel: Event,
    ) -> None:
        lease_deadline = self._aware_datetime(initial_lease_until, fallback=self._now())
        renew_at = monotonic() + _FUND_LEASE_RENEW_INTERVAL_SECONDS
        while True:
            remaining = (lease_deadline - self._now()).total_seconds()
            if remaining <= 0:
                lost.set()
                return
            until_renew = max(renew_at - monotonic(), 0.0)
            if stop.wait(
                min(_FUND_DURABLE_SIGNAL_POLL_SECONDS, remaining, until_renew)
            ):
                return
            try:
                state = self._inspect_fund_collection_lease(
                    job_id,
                    task_id,
                    lease_owner,
                )
                if state == "cancelled":
                    durable_cancel.set()
                    return
                if state == "lost":
                    lost.set()
                    return
                if monotonic() < renew_at:
                    continue
                renewed = self._renew_fund_collection_lease(
                    job_id,
                    task_id,
                    lease_owner,
                )
                if renewed == "cancelled":
                    durable_cancel.set()
                    return
                if renewed == "renewed":
                    lease_deadline = self._now() + _FUND_LEASE_DURATION
                    renew_at = monotonic() + _FUND_LEASE_RENEW_INTERVAL_SECONDS
                    continue
            except Exception:  # noqa: BLE001 - retry transient DB failures
                if self._now() >= lease_deadline:
                    lost.set()
                    return
                renew_at = monotonic() + min(
                    _FUND_DURABLE_SIGNAL_POLL_SECONDS,
                    max((lease_deadline - self._now()).total_seconds(), 0.0),
                )
                continue
            lost.set()
            return

    @staticmethod
    def _cancel_linked_active_children(
        session,
        *,
        parent_job_id: str,
        now: datetime,
        reason: str,
    ) -> None:
        children = list(
            session.scalars(
                select(MarketDataCollectionJob).where(
                    MarketDataCollectionJob.parent_job_id == parent_job_id,
                    MarketDataCollectionJob.job_type.in_(EQUITY_COLLECTION_JOB_TYPES),
                    MarketDataCollectionJob.status.in_(
                        (CollectionStatus.QUEUED.value, CollectionStatus.RUNNING.value)
                    ),
                )
            )
        )
        for child in children:
            child.status = CollectionStatus.CANCELLED.value
            child.cancel_requested = True
            child.lease_owner = None
            child.lease_until = None
            child.error_summary = reason
            child.updated_at = now
            child.completed_at = now
            session.query(MarketDataCollectionTask).filter(
                MarketDataCollectionTask.job_id == child.id,
                MarketDataCollectionTask.status.in_(
                    (CollectionStatus.QUEUED.value, CollectionStatus.RUNNING.value)
                ),
            ).update(
                {
                    "status": CollectionStatus.CANCELLED.value,
                    "retryable": False,
                    "next_retry_at": None,
                    "lease_owner": None,
                    "lease_until": None,
                    "updated_at": now,
                    "completed_at": now,
                },
                synchronize_session=False,
            )

    def close(self) -> None:
        with self._lock:
            for cancellation in self._cancellations.values():
                cancellation.set()
        self._executor.shutdown(wait=False, cancel_futures=False)
        if self._owns_equity_collection:
            self.equity_collection.close()

    def _run_agent_recovery(
        self,
        job_id: str,
        tenant_id: str,
        user_id: str,
        actor_id: str,
        child_ids: list[str],
        cancellation: Event,
    ) -> None:
        started_at = self._now()
        worker_id = f"fund-holding-agent:{generate_ulid()}"
        lease_state = {"until": started_at + _FUND_LEASE_DURATION}
        child_summaries: list[dict[str, Any]] = []
        if cancellation.is_set():
            return
        try:
            with self.database.session() as session:
                job = self._lock_fund_job(session, job_id)
                if job is None:
                    return
                checkpoint = dict(job.checkpoint_json or {})
                recovery = dict(checkpoint.get("agent_recovery") or {})
                if recovery.get("status") != "queued":
                    return
                child_summaries = [
                    dict(item)
                    for item in list(recovery.get("children") or [])
                    if isinstance(item, Mapping)
                    and str(item.get("job_id") or "").strip()
                ]
                resumed = bool(recovery.get("resumed_at"))
                recovery_started_at = self._aware_datetime(
                    recovery.get("started_at"),
                    fallback=started_at,
                )
                recovery.update(
                    {
                        "status": "running",
                        "started_at": iso(recovery_started_at),
                        "attempt": (
                            max(int(recovery.get("attempt") or 0), 1)
                            if resumed
                            else int(recovery.get("attempt") or 0) + 1
                        ),
                        "lease_owner": worker_id,
                        "lease_until": iso(
                            lease_state["until"]
                        ),
                        "children": child_summaries,
                        "message": "正在逐只补采前十大持仓的 UZI 数据缺口",
                    }
                )
                checkpoint["agent_recovery"] = recovery
                job.checkpoint_json = checkpoint
                job.updated_at = started_at
            completed_child_ids = {
                str(item.get("job_id") or "") for item in child_summaries
            }
            for child_id in child_ids:
                if child_id in completed_child_ids:
                    continue
                if self._agent_recovery_cancel_requested(
                    job_id,
                    cancellation,
                    lease_owner=worker_id,
                ):
                    self._cancel_agent_recovery(job_id, lease_owner=worker_id)
                    return
                if not self._maintain_agent_recovery_lease(
                    job_id,
                    worker_id,
                    cancellation=cancellation,
                    lease_state=lease_state,
                ):
                    raise _FundAgentRecoveryLeaseLost()
                try:
                    child = self.equity_collection.get_job(
                        child_id,
                        tenant_id=tenant_id,
                        user_id=user_id,
                    )
                    if not (
                        resumed
                        and self._child_recovery_completed_in_wave(
                            child,
                            started_at=recovery_started_at,
                        )
                    ):
                        self.equity_collection.start_agent_recovery(
                            child_id,
                            tenant_id=tenant_id,
                            user_id=user_id,
                            actor_id=actor_id,
                        )
                        child = self._wait_for_child_agent_recovery(
                            child_id,
                            tenant_id=tenant_id,
                            user_id=user_id,
                            cancellation=cancellation,
                            parent_job_id=job_id,
                            parent_lease_owner=worker_id,
                            parent_lease_state=lease_state,
                        )
                    child_summaries.append(
                        self._child_agent_recovery_summary(child_id, child)
                    )
                except _FundAgentRecoveryLeaseLost:
                    raise
                except Exception as exc:  # noqa: BLE001 - isolate one holding and redact details
                    child_summaries.append(
                        {
                            "job_id": child_id,
                            "status": "failed",
                            "attempted": 0,
                            "succeeded": 0,
                            "failed": 1,
                            "error_code": exc.__class__.__name__,
                        }
                    )
                completed_child_ids.add(child_id)
                if not self._persist_agent_recovery_children(
                    job_id,
                    child_summaries,
                    cancellation=cancellation,
                    lease_owner=worker_id,
                    lease_state=lease_state,
                ):
                    self._cancel_agent_recovery(job_id, lease_owner=worker_id)
                    return
            if self._agent_recovery_cancel_requested(
                job_id,
                cancellation,
                lease_owner=worker_id,
            ):
                self._cancel_agent_recovery(job_id, lease_owner=worker_id)
                return
            if not self._maintain_agent_recovery_lease(
                job_id,
                worker_id,
                cancellation=cancellation,
                lease_state=lease_state,
            ):
                raise _FundAgentRecoveryLeaseLost()
            completed_at = self._now()
            portfolio_state = self._portfolio_state_after_agent_recovery(job_id)
            failure_count = sum(
                max(
                    int(row.get("failed") or 0),
                    0 if row.get("status") == "succeeded" else 1,
                )
                for row in child_summaries
            )
            with self.database.session() as session:
                job = self._lock_fund_job(session, job_id)
                if job is None:
                    return
                checkpoint = dict(job.checkpoint_json or {})
                previous = dict(checkpoint.get("agent_recovery") or {})
                if (
                    previous.get("status") not in {"running", "cancelling"}
                    or previous.get("lease_owner") != worker_id
                    or self._lease_expired(
                        self._optional_datetime(previous.get("lease_until")),
                        now=completed_at,
                    )
                ):
                    # The lease was reclaimed after this process completed its
                    # child work.  The new owner is solely responsible for the
                    # durable terminal transition.
                    return
                if job.cancel_requested or previous.get("status") == "cancelling":
                    cancellation.set()
                    checkpoint.pop("publication_candidate", None)
                    checkpoint["agent_recovery"] = {
                        **previous,
                        "status": "cancelled",
                        "lease_owner": None,
                        "lease_until": None,
                        "completed_at": iso(completed_at),
                        "children": child_summaries,
                        "message": (
                            "组合 Agent 缺口补采已取消；已落库 Snapshot 保持不变"
                        ),
                    }
                    checkpoint["publication"] = {
                        "status": "blocked",
                        "error_code": "agent_recovery_cancelled",
                        "error_message": (
                            "组合补采已取消，可重新启动补采或重新采集后再冻结"
                        ),
                        "updated_at": iso(completed_at),
                    }
                    job.checkpoint_json = checkpoint
                    job.updated_at = completed_at
                    return
                checkpoint.pop("publication_candidate", None)
                checkpoint["agent_recovery"] = {
                    "status": (
                        "cancelled"
                        if cancellation.is_set()
                        else "partial"
                        if failure_count
                        else "succeeded"
                    ),
                    "requested_at": previous.get("requested_at"),
                    "requested_by": previous.get("requested_by"),
                    "attempt": int(previous.get("attempt") or 1),
                    "started_at": iso(started_at),
                    "completed_at": iso(completed_at),
                    "lease_owner": None,
                    "lease_until": None,
                    "attempted": sum(
                        int(row.get("attempted") or 0) for row in child_summaries
                    ),
                    "succeeded": sum(
                        int(row.get("succeeded") or 0) for row in child_summaries
                    ),
                    "failed": failure_count,
                    "child_count": len(child_summaries),
                    "children": child_summaries,
                    "message": (
                        "Agent 补采已取消"
                        if cancellation.is_set()
                        else "前十大持仓 Agent 补采完成，请核对组合冻结候选"
                    ),
                }
                holdings = list(portfolio_state.get("holdings") or [])
                remaining = dict(portfolio_state.get("remaining") or {})
                completed_count = int(portfolio_state.get("completed_count") or 0)
                portfolio_complete = bool(portfolio_state.get("complete"))
                if holdings:
                    checkpoint["holdings"] = [
                        {
                            **holding,
                            "missing_dimensions": list(
                                remaining.get(str(holding.get("symbol") or ""), [])
                            ),
                        }
                        for holding in holdings
                    ]
                    checkpoint["child_missing_by_symbol"] = {
                        symbol: dimensions
                        for symbol, dimensions in remaining.items()
                        if dimensions
                    }
                    checkpoint["child_complete_count"] = completed_count
                    checkpoint["child_total_count"] = len(holdings)
                checkpoint["publication"] = {
                    "status": "blocked",
                    "error_code": "publication_candidate_recomputing",
                    "error_message": "正在根据补采结果重新计算组合冻结候选",
                    "updated_at": iso(completed_at),
                }
                job.checkpoint_json = checkpoint
                # Child jobs may have appended newer recovered snapshots.  The
                # parent proposal must evaluate the whole portfolio at the end
                # of this recovery wave rather than at the original run cutoff.
                job.completed_at = completed_at
                job.updated_at = completed_at
                if holdings:
                    final_status = (
                        CollectionStatus.SUCCEEDED.value
                        if portfolio_complete
                        else CollectionStatus.PARTIAL.value
                    )
                    job.status = final_status
                    job.progress_json = {
                        "completed": 1,
                        "total": 1,
                        "by_status": {final_status: 1},
                        "child_complete": completed_count,
                        "child_total": len(holdings),
                    }
                    job.error_summary = (
                        None
                        if portfolio_complete
                        else "fund_child_collection_incomplete"
                    )
                    task = self._parent_task(session, job.id)
                    task.status = final_status
                    task.retryable = bool(
                        not portfolio_complete
                        and job.start_date == self._local_today()
                        and task.attempt_count < task.max_attempts
                    )
                    task.last_error_code = (
                        None
                        if portfolio_complete
                        else "fund_child_collection_incomplete"
                    )
                    task.last_error_message = (
                        None
                        if portfolio_complete
                        else "一个或多个持仓股票仍缺少报告所需行情维度"
                    )
                    task.next_retry_at = None
                    task.updated_at = completed_at
                    task.completed_at = completed_at
                    parent_snapshot_id = str(
                        checkpoint.get("parent_snapshot_id")
                        or dict(task.checkpoint_json or {}).get("parent_snapshot_id")
                        or ""
                    )
                    self._upsert_result(
                        session,
                        task=task,
                        as_of_date=job.start_date,
                        attempt=max(int(task.attempt_count), 1),
                        status="succeeded" if portfolio_complete else "partial",
                        provider_status=(
                            "fund_and_children_ready_after_agent"
                            if portfolio_complete
                            else "fund_ready_child_gaps_after_agent"
                        ),
                        snapshot_id=parent_snapshot_id or None,
                        metrics={
                            "child_complete_count": completed_count,
                            "child_total_count": len(holdings),
                            "agent_recovery": True,
                        },
                        error_code=task.last_error_code,
                        error_message=task.last_error_message,
                        retryable=task.retryable,
                        started_at=job.started_at,
                        completed_at=completed_at,
                    )
            if not cancellation.is_set():
                self._refresh_publication_candidate(job_id)
        except _FundAgentRecoveryLeaseLost:
            # A newer process reclaimed the expired lease.  This obsolete
            # worker must exit silently and, crucially, must not overwrite the
            # new owner's checkpoint or terminal status.
            return
        except Exception as exc:  # noqa: BLE001 - parent worker boundary
            if self._agent_recovery_cancel_requested(
                job_id,
                cancellation,
                lease_owner=worker_id,
            ):
                self._cancel_agent_recovery(job_id, lease_owner=worker_id)
                return
            now = self._now()
            with self.database.session() as session:
                job = self._lock_fund_job(session, job_id)
                if job is None:
                    return
                checkpoint = dict(job.checkpoint_json or {})
                previous = dict(checkpoint.get("agent_recovery") or {})
                if (
                    previous.get("status") != "running"
                    or previous.get("lease_owner") != worker_id
                    or self._lease_expired(
                        self._optional_datetime(previous.get("lease_until")),
                        now=now,
                    )
                ):
                    return
                checkpoint.pop("publication_candidate", None)
                checkpoint["agent_recovery"] = {
                    **previous,
                    "status": "failed",
                    "lease_owner": None,
                    "lease_until": None,
                    "completed_at": iso(now),
                    "failed": max(int(previous.get("failed") or 0), 1),
                    "error_code": exc.__class__.__name__,
                    "message": "组合 Agent 补采未完成，可稍后重试",
                }
                job.checkpoint_json = checkpoint
                job.updated_at = now

    def _persist_agent_recovery_children(
        self,
        job_id: str,
        children: list[dict[str, Any]],
        *,
        cancellation: Event,
        lease_owner: str,
        lease_state: dict[str, datetime],
    ) -> bool:
        now = self._now()
        with self.database.session() as session:
            job = self._lock_fund_job(session, job_id)
            if job is None:
                cancellation.set()
                return False
            checkpoint = dict(job.checkpoint_json or {})
            recovery = dict(checkpoint.get("agent_recovery") or {})
            if (
                recovery.get("lease_owner") != lease_owner
                or recovery.get("status") not in {"running", "cancelling"}
                or self._lease_expired(
                    self._optional_datetime(recovery.get("lease_until")),
                    now=now,
                )
            ):
                cancellation.set()
                return False
            if job.cancel_requested or recovery.get("status") == "cancelling":
                cancellation.set()
                return False
            lease_until = now + _FUND_LEASE_DURATION
            recovery.update(
                {
                    "children": children,
                    "child_count": len(children),
                    "attempted": sum(
                        int(item.get("attempted") or 0) for item in children
                    ),
                    "succeeded": sum(
                        int(item.get("succeeded") or 0) for item in children
                    ),
                    "failed": sum(int(item.get("failed") or 0) for item in children),
                    "lease_until": iso(lease_until),
                    "updated_at": iso(now),
                }
            )
            checkpoint["agent_recovery"] = recovery
            job.checkpoint_json = checkpoint
            job.updated_at = now
            lease_state["until"] = lease_until
            return True

    def _agent_recovery_cancel_requested(
        self,
        job_id: str,
        cancellation: Event,
        *,
        lease_owner: str | None = None,
    ) -> bool:
        if cancellation.is_set():
            return True
        with self.database.session() as session:
            job = session.get(MarketDataCollectionJob, job_id)
            if job is None:
                cancellation.set()
                return True
            recovery = dict(dict(job.checkpoint_json or {}).get("agent_recovery") or {})
            cancelled = bool(
                job.cancel_requested or recovery.get("status") == "cancelling"
            )
            lease_lost = bool(
                lease_owner is not None
                and (
                    recovery.get("status") not in {"running", "cancelling"}
                    or recovery.get("lease_owner") != lease_owner
                    or self._lease_expired(
                        self._optional_datetime(recovery.get("lease_until")),
                        now=self._now(),
                    )
                )
            )
        if cancelled or lease_lost:
            cancellation.set()
        return cancelled or lease_lost

    def _renew_agent_recovery_lease(
        self,
        job_id: str,
        lease_owner: str,
        *,
        cancellation: Event,
    ) -> bool:
        """Renew only the still-owned recovery wave (owner-token fencing)."""

        if cancellation.is_set():
            return False
        now = self._now()
        with self.database.session() as session:
            job = self._lock_fund_job(session, job_id)
            if job is None or job.job_type != FUND_HOLDING_JOB_TYPE:
                cancellation.set()
                return False
            checkpoint = dict(job.checkpoint_json or {})
            recovery = dict(checkpoint.get("agent_recovery") or {})
            if (
                job.cancel_requested
                or recovery.get("status") != "running"
                or recovery.get("lease_owner") != lease_owner
                or self._lease_expired(
                    self._optional_datetime(recovery.get("lease_until")),
                    now=now,
                )
            ):
                cancellation.set()
                return False
            recovery["lease_until"] = iso(now + _FUND_LEASE_DURATION)
            recovery["updated_at"] = iso(now)
            checkpoint["agent_recovery"] = recovery
            job.checkpoint_json = checkpoint
            job.updated_at = now
            return True

    def _maintain_agent_recovery_lease(
        self,
        job_id: str,
        lease_owner: str,
        *,
        cancellation: Event,
        lease_state: dict[str, datetime],
    ) -> bool:
        """Tolerate transient DB errors only until the last known deadline."""

        deadline = self._aware_datetime(
            lease_state.get("until"),
            fallback=self._now(),
        )
        try:
            renewed = self._renew_agent_recovery_lease(
                job_id,
                lease_owner,
                cancellation=cancellation,
            )
        except Exception:  # noqa: BLE001 - deadline fences transient DB failures
            if self._now() < deadline:
                return True
            cancellation.set()
            return False
        if not renewed:
            return False
        lease_state["until"] = self._now() + _FUND_LEASE_DURATION
        return True

    @staticmethod
    def _child_agent_recovery_summary(
        child_id: str,
        child: Mapping[str, Any],
    ) -> dict[str, Any]:
        recovery = dict(child.get("agent_recovery") or {})
        return {
            "job_id": child_id,
            "symbol": next(iter(child.get("symbols") or []), None),
            "status": recovery.get("status") or "unknown",
            "attempted": int(recovery.get("attempted") or 0),
            "succeeded": int(recovery.get("succeeded") or 0),
            "failed": int(recovery.get("failed") or 0),
            "completed_at": recovery.get("completed_at"),
        }

    def _child_recovery_completed_in_wave(
        self,
        child: Mapping[str, Any],
        *,
        started_at: datetime,
    ) -> bool:
        recovery = dict(child.get("agent_recovery") or {})
        if recovery.get("status") in {"queued", "running", "cancelling", None}:
            return False
        completed_at = recovery.get("completed_at")
        if not completed_at:
            return False
        return self._aware_datetime(completed_at, fallback=None) >= started_at

    def _cancel_agent_recovery(
        self,
        job_id: str,
        *,
        lease_owner: str | None = None,
    ) -> None:
        now = self._now()
        with self.database.session() as session:
            job = self._lock_fund_job(session, job_id)
            if job is None or job.job_type != FUND_HOLDING_JOB_TYPE:
                return
            checkpoint = dict(job.checkpoint_json or {})
            previous = dict(checkpoint.get("agent_recovery") or {})
            if lease_owner is not None and (
                previous.get("lease_owner") != lease_owner
                or previous.get("status") not in {"running", "cancelling"}
                or self._lease_expired(
                    self._optional_datetime(previous.get("lease_until")),
                    now=now,
                )
            ):
                return
            checkpoint.pop("publication_candidate", None)
            checkpoint["agent_recovery"] = {
                **previous,
                "status": "cancelled",
                "lease_owner": None,
                "lease_until": None,
                "completed_at": iso(now),
                "message": "组合 Agent 缺口补采已取消；已落库 Snapshot 保持不变",
            }
            checkpoint["publication"] = {
                "status": "blocked",
                "error_code": "agent_recovery_cancelled",
                "error_message": "组合补采已取消，可重新启动补采或重新采集后再冻结",
                "updated_at": iso(now),
            }
            job.checkpoint_json = checkpoint
            job.updated_at = now

    def _wait_for_child_agent_recovery(
        self,
        job_id: str,
        *,
        tenant_id: str,
        user_id: str,
        cancellation: Event,
        parent_job_id: str,
        parent_lease_owner: str,
        parent_lease_state: dict[str, datetime],
        timeout_seconds: float = 7_200,
    ) -> dict[str, Any]:
        """Wait on durable child state instead of a process-local Future."""

        deadline = monotonic() + max(float(timeout_seconds), 1.0)
        renew_at = monotonic() + min(
            _FUND_LEASE_RENEW_INTERVAL_SECONDS,
            max(
                (
                    parent_lease_state["until"] - self._now()
                ).total_seconds(),
                0.0,
            ),
        )
        while True:
            if monotonic() >= renew_at:
                if not self._maintain_agent_recovery_lease(
                    parent_job_id,
                    parent_lease_owner,
                    cancellation=cancellation,
                    lease_state=parent_lease_state,
                ):
                    raise _FundAgentRecoveryLeaseLost()
                remaining = max(
                    (parent_lease_state["until"] - self._now()).total_seconds(),
                    0.0,
                )
                renew_at = monotonic() + min(
                    _FUND_LEASE_RENEW_INTERVAL_SECONDS,
                    remaining,
                )
            child = self.equity_collection.get_job(
                job_id, tenant_id=tenant_id, user_id=user_id
            )
            status = str(
                dict(child.get("agent_recovery") or {}).get("status") or ""
            ).lower()
            if status not in {"queued", "running", "cancelling"}:
                return child
            recovery = dict(child.get("agent_recovery") or {})
            if status == "cancelling" or (
                status == "running"
                and self._lease_expired(
                    self._optional_datetime(recovery.get("lease_until")),
                    now=self._now(),
                )
            ):
                self._resume_equity_agent_children([job_id])
            if cancellation.wait(0.25):
                return child
            if monotonic() >= deadline:
                raise TimeoutError("child agent recovery durable wait timed out")

    @staticmethod
    def _optional_datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str) and value.strip():
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        return None

    def _portfolio_state_after_agent_recovery(self, job_id: str) -> dict[str, Any]:
        """Recompute the parent fund gate from current persisted child snapshots."""

        with self.database.session() as session:
            job = session.get(MarketDataCollectionJob, job_id)
            if job is None or job.job_type != FUND_HOLDING_JOB_TYPE:
                return {}
            checkpoint = dict(job.checkpoint_json or {})
            holdings = list(checkpoint.get("holdings") or [])
            request = dict(job.request_json or {})
            dimensions = list(
                request.get("equity_dimensions")
                or self.equity_collection.registry.dimension_ids
            )
            as_of_date = job.start_date
        remaining = {
            str(holding.get("symbol") or ""): self._missing_equity_dimensions(
                str(holding.get("symbol") or ""),
                as_of_date,
                dimensions,
            )
            for holding in holdings
            if str(holding.get("symbol") or "")
        }
        completed_count = sum(not dimensions for dimensions in remaining.values())
        return {
            "holdings": holdings,
            "remaining": remaining,
            "completed_count": completed_count,
            "complete": len(holdings) == 10 and completed_count == 10,
        }

    def _run_job(self, job_id: str, cancellation: Event) -> None:
        claimed: dict[str, Any] | None = None
        heartbeat_stop = Event()
        lease_lost = Event()
        durable_cancel = Event()
        heartbeat: Thread | None = None
        try:
            claimed = self._claim(job_id)
            if claimed is None:
                return
            heartbeat = Thread(
                target=self._heartbeat_fund_collection_lease,
                args=(
                    job_id,
                    claimed["task_id"],
                    claimed["lease_owner"],
                    claimed["lease_until"],
                    heartbeat_stop,
                    lease_lost,
                    durable_cancel,
                ),
                name=f"fund-holding-lease-{job_id}",
                daemon=True,
            )
            heartbeat.start()
            worker_cancellation = _CancellationView(
                cancellation,
                durable_cancel,
                lease_lost,
            )
            if worker_cancellation.is_set():
                raise _FundExpansionCancelled()
            snapshot = self._resolve_or_collect_parent(
                claimed, cancellation=worker_cancellation
            )
            if not self._checkpoint_parent_snapshot(
                job_id,
                claimed["task_id"],
                snapshot,
                lease_owner=claimed["lease_owner"],
            ):
                raise _FundExpansionLeaseLost()
            if snapshot.status != SnapshotStatus.AVAILABLE.value:
                errors = list((snapshot.validation_json or {}).get("errors") or [])
                raise _FundExpansionFailure(
                    "fund_top_holdings_contract_gap",
                    "; ".join(str(item) for item in errors)[:4000]
                    or "基金快照未形成精确十只 A 股持仓",
                    provider_status=snapshot.status,
                    retryable=claimed["as_of_date"] == self._local_today(),
                    snapshot_id=snapshot.id,
                )
            bundle = (
                self._ensure_parent_bundle(claimed, snapshot)
                if claimed["publish_when_complete"]
                else None
            )
            holdings = self._holdings(snapshot)
            if not self._checkpoint_parent_bundle_and_holdings(
                job_id,
                claimed["task_id"],
                bundle=bundle,
                holdings=holdings,
                lease_owner=claimed["lease_owner"],
            ):
                raise _FundExpansionLeaseLost()
            if worker_cancellation.is_set():
                raise _FundExpansionCancelled()
            child_runs = self._run_equity_children(
                claimed,
                holdings=holdings,
                cancellation=worker_cancellation,
            )
            self._raise_if_parent_cancelled(
                claimed["job_id"],
                worker_cancellation,
                lease_owner=claimed["lease_owner"],
            )
            remaining = {
                holding["symbol"]: self._missing_equity_dimensions(
                    holding["symbol"],
                    claimed["as_of_date"],
                    claimed["equity_dimensions"],
                )
                for holding in holdings
            }
            stock_bundles: dict[str, dict[str, str]] = {}
            for holding in holdings:
                self._raise_if_parent_cancelled(
                    claimed["job_id"],
                    worker_cancellation,
                    lease_owner=claimed["lease_owner"],
                )
                symbol = str(holding["symbol"])
                if remaining[symbol]:
                    continue
                if claimed["publish_when_complete"]:
                    stock_bundle = self._ensure_equity_bundle(
                        symbol,
                        claimed["as_of_date"],
                    )
                    if stock_bundle is None:
                        remaining[symbol] = ["published_bundle"]
                        continue
                    stock_bundles[symbol] = {
                        "bundle_id": stock_bundle.id,
                        "manifest_hash": stock_bundle.manifest_hash,
                    }
            completed_count = sum(
                not dimensions
                and (not claimed["publish_when_complete"] or symbol in stock_bundles)
                for symbol, dimensions in remaining.items()
            )
            with self._lock:
                self._raise_if_parent_cancelled(
                    claimed["job_id"],
                    worker_cancellation,
                    lease_owner=claimed["lease_owner"],
                )
                completed = self._complete_parent(
                    claimed,
                    snapshot=snapshot,
                    bundle=bundle,
                    holdings=holdings,
                    child_runs=child_runs,
                    remaining=remaining,
                    completed_count=completed_count,
                    stock_bundles=stock_bundles,
                )
                if not completed:
                    raise _FundExpansionLeaseLost()
            candidate = self._refresh_publication_candidate(claimed["job_id"])
            if claimed["publish_when_complete"] and candidate is not None:
                try:
                    self._confirm_freeze_job(
                        claimed["job_id"],
                        proposal_hash=candidate["proposal_hash"],
                        acknowledge_degraded=True,
                        published_by="fund_holding_subscription",
                    )
                except CollectionJobError:
                    pass
        except _FundExpansionLeaseLost:
            return
        except _FundExpansionCancelled:
            if not lease_lost.is_set():
                self._cancel_parent(
                    job_id,
                    lease_owner=(claimed or {}).get("lease_owner"),
                )
        except _FundExpansionFailure as exc:
            if not lease_lost.is_set():
                self._fail_parent(
                    job_id,
                    exc,
                    lease_owner=(claimed or {}).get("lease_owner"),
                )
        except Exception as exc:  # noqa: BLE001 - worker boundary persists a safe summary
            if not lease_lost.is_set():
                self._fail_parent(
                    job_id,
                    _FundExpansionFailure(
                        "fund_holding_worker_failed",
                        f"{exc.__class__.__name__}: 基金持仓扩展执行失败",
                        provider_status="worker_error",
                        retryable=True,
                    ),
                    lease_owner=(claimed or {}).get("lease_owner"),
                )
        finally:
            heartbeat_stop.set()
            if heartbeat is not None:
                heartbeat.join(timeout=1.0)

    def _claim(self, job_id: str) -> dict[str, Any] | None:
        with self._lock, self.database.session() as session:
            job = self._lock_fund_job(session, job_id)
            if (
                job is None
                or job.job_type != FUND_HOLDING_JOB_TYPE
                or job.cancel_requested
                or job.status != CollectionStatus.QUEUED.value
            ):
                return None
            task = self._parent_task(session, job.id)
            if task.status != CollectionStatus.QUEUED.value:
                return None
            now = self._now()
            worker_id = f"fund-holding:{generate_ulid()}"
            lease_until = now + _FUND_LEASE_DURATION
            job.status = CollectionStatus.RUNNING.value
            job.started_at = job.started_at or now
            job.updated_at = now
            job.lease_owner = worker_id
            job.lease_until = lease_until
            task.status = CollectionStatus.RUNNING.value
            task.attempt_count += 1
            task.lease_owner = worker_id
            task.lease_until = lease_until
            task.updated_at = now
            request = dict(job.request_json or {})
            return {
                "job_id": job.id,
                "task_id": task.id,
                "lease_owner": worker_id,
                "lease_until": lease_until,
                "tenant_id": job.tenant_id,
                "user_id": job.user_id,
                "actor_id": job.requested_by,
                "origin": job.origin,
                "subscription_id": job.subscription_id,
                "fund_code": str(request.get("fund_code") or task.symbol),
                "as_of_date": task.start_date,
                "mode": job.mode,
                "max_concurrency": job.max_concurrency,
                "publish_when_complete": bool(job.publish_when_complete),
                "investment_cycle": str(request.get("investment_cycle") or "long_term"),
                "equity_dimensions": list(
                    request.get("equity_dimensions")
                    or self.equity_collection.registry.dimension_ids
                ),
                "retry_scope_by_symbol": {
                    str(symbol): [str(dimension) for dimension in dimensions]
                    for symbol, dimensions in dict(
                        dict(job.checkpoint_json or {}).get("retry_scope_by_symbol")
                        or {}
                    ).items()
                },
                "attempt": task.attempt_count,
                "checkpoint": dict(job.checkpoint_json or {}),
            }

    def _resolve_or_collect_parent(
        self,
        claimed: dict[str, Any],
        *,
        cancellation: Event | _CancellationView,
    ):
        snapshot = self._checkpoint_snapshot(claimed)
        if snapshot is not None:
            return snapshot
        current_date = self._local_today()
        allow_existing = (
            claimed["mode"] == "missing_or_stale"
            or claimed["as_of_date"] != current_date
        )
        if allow_existing:
            with self.database.session() as session:
                now = self._now()
                snapshot = SnapshotResolver(
                    MarketDataRepository(session), registry=self.registry
                ).resolve_snapshot(
                    symbol=claimed["fund_code"],
                    market=FUND_MARKET,
                    dimension=self._fund_dimension,
                    as_of_date=claimed["as_of_date"],
                    cutoff_at=now if claimed["as_of_date"] == current_date else None,
                    exchange_timezone=FUND_EXCHANGE_TIMEZONE,
                    now=now,
                )
                if snapshot is not None:
                    return snapshot
        if claimed["as_of_date"] != current_date:
            raise _FundExpansionFailure(
                "fund_historical_pit_unavailable",
                "历史日没有当时已落库的基金持仓快照，禁止用当前披露回填",
                provider_status="pit_not_supported",
                retryable=False,
            )
        if cancellation.is_set():
            raise _FundExpansionCancelled()
        try:
            collection_started_at = self._now()
            raw = self._collect_current_fund_snapshot(
                claimed,
                cancellation=cancellation,
            )
            self._raise_if_parent_cancelled(
                claimed["job_id"],
                cancellation,
                lease_owner=claimed["lease_owner"],
            )
            observed_at = self._now()
            if observed_at < collection_started_at:
                raise _FundExpansionFailure(
                    "fund_collection_clock_invalid",
                    "基金采集完成时间早于开始时间，无法形成可信观测窗口",
                    provider_status="clock_invalid",
                    retryable=False,
                )
            observed_as_of_date = observed_at.astimezone(
                ZoneInfo(FUND_EXCHANGE_TIMEZONE)
            ).date()
            ingest = self.adapter.adapt(
                raw,
                fund_code=claimed["fund_code"],
                # A live response belongs to the Shanghai date on which the
                # provider call completed.  Never backdate data observed after
                # midnight into the previous day's PIT snapshot.
                as_of_date=observed_as_of_date,
                exchange_timezone=FUND_EXCHANGE_TIMEZONE,
                top_n=10,
                observed_at=observed_at,
                collection_task_id=claimed["task_id"],
                provider_attempt_id=f"{claimed['task_id']}:{claimed['attempt']}",
            )
            with self.database.session() as session:
                job = self._lock_fund_job(session, claimed["job_id"])
                task = session.get(MarketDataCollectionTask, claimed["task_id"])
                if (
                    job is None
                    or task is None
                    or not self._owns_active_parent_lease(
                        job,
                        task,
                        lease_owner=claimed["lease_owner"],
                        now=self._now(),
                    )
                ):
                    if job is not None and job.cancel_requested:
                        raise _FundExpansionCancelled()
                    raise _FundExpansionLeaseLost()
                persisted = SnapshotIngestionService(
                    MarketDataRepository(session), registry=self.registry
                ).ingest(ingest)
            if observed_as_of_date != claimed["as_of_date"]:
                raise _FundExpansionFailure(
                    "fund_collection_crossed_market_day",
                    "基金实时采集跨越上海午夜；结果已按次日落库，禁止回填到前一日",
                    provider_status="market_day_advanced",
                    retryable=False,
                    snapshot_id=persisted.id,
                )
            return persisted
        except (
            _FundExpansionFailure,
            _FundExpansionCancelled,
            _FundExpansionLeaseLost,
        ):
            raise
        except Exception as exc:
            raise _FundExpansionFailure(
                "fund_snapshot_collection_failed",
                f"{exc.__class__.__name__}: 当前基金持仓采集失败",
                provider_status="provider_error",
                retryable=True,
            ) from exc

    def _collect_current_fund_snapshot(
        self,
        claimed: dict[str, Any],
        *,
        cancellation: Event | _CancellationView,
    ) -> Mapping[str, Any]:
        loader = self.fund_snapshot_loader
        if loader is None:
            from domain_packs.finance_ashare.skills.fund_snapshot.skill import (
                FundSnapshotSkill,
            )

            loader = FundSnapshotSkill()
        run = getattr(loader, "run", None)
        call = run if callable(run) else loader
        if not callable(call):
            raise TypeError("fund_snapshot_loader must be callable or expose run()")
        call_kwargs: dict[str, Any] = {
            "query": f"显式采集基金 {claimed['fund_code']} 前十大持仓",
            "fund_codes": [claimed["fund_code"]],
            "fund_category": None,
            "candidate_pool_size": 1,
            "investment_cycle": claimed["investment_cycle"],
        }
        try:
            parameters = tuple(signature(call).parameters.values())
        except (TypeError, ValueError):
            parameters = ()
        if any(
            parameter.name == "cancellation"
            or parameter.kind == Parameter.VAR_KEYWORD
            for parameter in parameters
        ):
            call_kwargs["cancellation"] = cancellation
        payload = call(**call_kwargs)
        if not isinstance(payload, Mapping):
            raise TypeError("FundSnapshotSkill must return a mapping")
        return payload

    def _checkpoint_snapshot(self, claimed: dict[str, Any]):
        snapshot_id = str(claimed["checkpoint"].get("parent_snapshot_id") or "").strip()
        if not snapshot_id:
            return None
        with self.database.session() as session:
            snapshot = MarketDataRepository(session).get_snapshot(snapshot_id)
            if snapshot is None:
                return None
            if (
                snapshot.symbol != claimed["fund_code"]
                or snapshot.market != FUND_MARKET
                or snapshot.dimension != self._fund_dimension
                or snapshot.as_of_date != claimed["as_of_date"]
                or snapshot.contract_version != self.registry.contract_version
                or snapshot.status != SnapshotStatus.AVAILABLE.value
                or snapshot.revoked_at is not None
            ):
                return None
            return snapshot

    def _ensure_parent_bundle(self, claimed: dict[str, Any], snapshot):
        bundle_id = str(claimed["checkpoint"].get("parent_bundle_id") or "").strip()
        with self.database.session() as session:
            repository = MarketDataRepository(session)
            candidates = []
            if bundle_id:
                candidates.append(repository.get_bundle(bundle_id))
            candidates.extend(
                repository.list_bundles(
                    symbol=claimed["fund_code"],
                    market=FUND_MARKET,
                    as_of_date=claimed["as_of_date"],
                    include_invalidated=False,
                    limit=20,
                )
            )
            seen: set[str] = set()
            for bundle in candidates:
                if bundle is None or bundle.id in seen:
                    continue
                seen.add(bundle.id)
                items = repository.list_bundle_items(bundle.id)
                if (
                    bundle.invalidated_at is None
                    and bundle.symbol == claimed["fund_code"]
                    and bundle.market == FUND_MARKET
                    and bundle.as_of_date == claimed["as_of_date"]
                    and bundle.contract_version == self.registry.contract_version
                    and len(items) == 1
                    and items[0].snapshot_id == snapshot.id
                ):
                    return bundle
        now = self._now()
        try:
            with self.database.session() as session:
                return BundlePublisher(
                    MarketDataRepository(session), registry=self.registry
                ).publish(
                    symbol=claimed["fund_code"],
                    market=FUND_MARKET,
                    as_of_date=claimed["as_of_date"],
                    cutoff_at=now
                    if claimed["as_of_date"] == self._local_today()
                    else None,
                    exchange_timezone=FUND_EXCHANGE_TIMEZONE,
                    published_by="fund_holding_collection",
                    now=now,
                )
        except IncompleteBundleError as exc:
            raise _FundExpansionFailure(
                "fund_top_holdings_bundle_incomplete",
                "基金前十大持仓未满足单槽 PIT Bundle 合同："
                + ", ".join(exc.missing_dimensions + exc.blocked_dimensions),
                provider_status="bundle_incomplete",
                retryable=claimed["as_of_date"] == self._local_today(),
                snapshot_id=snapshot.id,
            ) from exc

    def _holdings(self, snapshot) -> list[dict[str, Any]]:
        from research_system.market_data.fund_holdings_contracts import (
            validate_fund_top_holdings_payload,
        )

        validated = validate_fund_top_holdings_payload(
            dict(snapshot.payload_json or {})
        )
        holdings = [
            item.model_dump(mode="json", exclude_none=False)
            for item in validated.holdings
        ]
        if len(holdings) != 10:
            raise _FundExpansionFailure(
                "fund_top_holdings_not_exact",
                "基金持仓扩展必须冻结精确十只 A 股",
                provider_status="strict_contract_gap",
                retryable=False,
                snapshot_id=snapshot.id,
            )
        seen: set[str] = set()
        for holding in holdings:
            symbol = str(holding.get("symbol") or "").upper()
            match = _A_SHARE_SYMBOL.fullmatch(symbol)
            if (
                match is None
                or str(holding.get("market") or "") != "CN_EQUITY"
                or str(holding.get("status") or "") != "available"
                or symbol in seen
            ):
                raise _FundExpansionFailure(
                    "fund_top_holdings_identity_invalid",
                    "基金持仓包含非 A 股、重复或不可用标的",
                    provider_status="strict_contract_gap",
                    retryable=False,
                    snapshot_id=snapshot.id,
                )
            seen.add(symbol)
        return holdings

    def _run_equity_children(
        self,
        claimed: dict[str, Any],
        *,
        holdings: list[dict[str, Any]],
        cancellation: Event | _CancellationView,
    ) -> list[dict[str, Any]]:
        recovered_runs = self._resume_pending_equity_children(
            claimed,
            cancellation=cancellation,
        )
        if recovered_runs:
            # One parent attempt owns one provider wave.  Re-evaluate coverage
            # in _run_job after the recovered children finish; any remaining
            # gap is retried explicitly instead of being called a second time
            # during this restart pass.
            return recovered_runs

        groups: dict[tuple[str, ...], list[str]] = defaultdict(list)
        retry_scope = dict(claimed.get("retry_scope_by_symbol") or {})
        for holding in holdings:
            symbol = str(holding["symbol"])
            if retry_scope and symbol not in retry_scope:
                continue
            requested_dimensions = (
                [
                    dimension
                    for dimension in claimed["equity_dimensions"]
                    if dimension in set(retry_scope[symbol])
                ]
                if retry_scope
                else claimed["equity_dimensions"]
            )
            missing = tuple(
                self._missing_equity_dimensions(
                    symbol,
                    claimed["as_of_date"],
                    requested_dimensions,
                )
            )
            if missing:
                groups[missing].append(symbol)

        child_runs: list[dict[str, Any]] = []
        for dimensions, symbols in sorted(groups.items(), key=lambda item: item[1]):
            self._raise_if_parent_cancelled(
                claimed["job_id"],
                cancellation,
                lease_owner=claimed["lease_owner"],
            )
            idempotency_key = self._child_idempotency_key(
                claimed["job_id"],
                symbols=symbols,
                dimensions=dimensions,
            )
            child = self.equity_collection.create_job(
                tenant_id=claimed["tenant_id"],
                user_id=claimed["user_id"],
                actor_id=claimed["actor_id"],
                symbols=symbols,
                start_date=claimed["as_of_date"],
                end_date=claimed["as_of_date"],
                dimensions=list(dimensions),
                mode="missing_or_stale",
                max_concurrency=claimed["max_concurrency"],
                publish_when_complete=bool(claimed["publish_when_complete"]),
                idempotency_key=idempotency_key,
                origin=claimed["origin"],
                subscription_id=claimed["subscription_id"],
                parent_job_id=claimed["job_id"],
                submit=False,
            )
            run = {
                "job_id": child["id"],
                "symbols": list(symbols),
                "dimensions": list(dimensions),
                "status": child["status"],
                "attempt": claimed["attempt"],
            }
            if not self._append_child_checkpoint(
                claimed["job_id"],
                claimed["task_id"],
                run,
                lease_owner=claimed["lease_owner"],
            ):
                raise _FundExpansionLeaseLost()
            self.equity_collection.run_job_now(child["id"])
            self._raise_if_parent_cancelled(
                claimed["job_id"],
                cancellation,
                lease_owner=claimed["lease_owner"],
            )
            completed = self.equity_collection.get_job(
                child["id"],
                tenant_id=claimed["tenant_id"],
                user_id=claimed["user_id"],
            )
            run["status"] = completed["status"]
            child_runs.append(run)
            if not self._replace_child_checkpoint(
                claimed["job_id"],
                claimed["task_id"],
                run,
                lease_owner=claimed["lease_owner"],
            ):
                raise _FundExpansionLeaseLost()
        return child_runs

    def _child_idempotency_key(
        self,
        parent_job_id: str,
        *,
        symbols: list[str],
        dimensions: tuple[str, ...],
    ) -> str:
        """Return a restart-stable key and advance only after terminal work.

        The persisted generation solves two distinct cases: an active child is
        always reclaimed after a crash, while a later explicit retry of the
        exact same unresolved scope still receives a fresh executable job.
        """

        scope_hash = canonical_hash(
            {
                "parent_job_id": parent_job_id,
                "symbols": symbols,
                "dimensions": dimensions,
            }
        )[:20]
        base_key = f"fund-child:{parent_job_id}:{scope_hash}"
        with self.database.session() as session:
            children = list(
                session.scalars(
                    select(MarketDataCollectionJob).where(
                        MarketDataCollectionJob.parent_job_id == parent_job_id,
                        MarketDataCollectionJob.job_type.in_(
                            EQUITY_COLLECTION_JOB_TYPES
                        ),
                    )
                )
            )
            same_scope_count = sum(
                list(child.symbols_json or []) == list(symbols)
                and list(child.dimensions_json or []) == list(dimensions)
                for child in children
            )
        return base_key if same_scope_count == 0 else f"{base_key}:{same_scope_count}"

    def _resume_pending_equity_children(
        self,
        claimed: dict[str, Any],
        *,
        cancellation: Event | _CancellationView,
    ) -> list[dict[str, Any]]:
        """Reclaim queued/running children exclusively owned by this parent.

        The database parent link is also consulted because a process can stop
        after the child transaction commits but just before its id is appended
        to the parent's checkpoint.
        """

        with self.database.session() as session:
            parent = self._lock_fund_job(session, claimed["job_id"])
            parent_task = session.get(
                MarketDataCollectionTask,
                claimed["task_id"],
            )
            now = self._now()
            if (
                parent is None
                or parent_task is None
                or not self._owns_active_parent_lease(
                    parent,
                    parent_task,
                    lease_owner=claimed["lease_owner"],
                    now=now,
                )
            ):
                raise _FundExpansionLeaseLost()
            checkpoint_ids = (
                {
                    str(job_id)
                    for job_id in dict(parent.checkpoint_json or {}).get(
                        "child_job_ids", []
                    )
                    if str(job_id).strip()
                }
                if parent is not None
                else set()
            )
            linked_children = list(
                session.scalars(
                    select(MarketDataCollectionJob)
                    .where(
                        MarketDataCollectionJob.parent_job_id == claimed["job_id"],
                        MarketDataCollectionJob.job_type.in_(
                            EQUITY_COLLECTION_JOB_TYPES
                        ),
                        MarketDataCollectionJob.status.in_(
                            (
                                CollectionStatus.QUEUED.value,
                                CollectionStatus.RUNNING.value,
                            )
                        ),
                        MarketDataCollectionJob.cancel_requested.is_(False),
                    )
                    .order_by(
                        MarketDataCollectionJob.created_at,
                        MarketDataCollectionJob.id,
                    )
                )
            )
            pending: list[dict[str, Any]] = []
            for child in linked_children:
                if (
                    child.tenant_id != claimed["tenant_id"]
                    or child.user_id != claimed["user_id"]
                ):
                    continue
                # Include checkpointed jobs and database-linked orphans.  The
                # latter cover the narrow crash window before checkpointing.
                checkpoint_ids.add(child.id)
                child_tasks = list(
                    session.scalars(
                        select(MarketDataCollectionTask).where(
                            MarketDataCollectionTask.job_id == child.id
                        )
                    )
                )
                running_tasks = [
                    task
                    for task in child_tasks
                    if task.status == CollectionStatus.RUNNING.value
                ]
                reclaimable = bool(
                    child.status == CollectionStatus.RUNNING.value
                    and self._lease_expired(child.lease_until, now=now)
                    and all(
                        self._lease_expired(task.lease_until, now=now)
                        for task in running_tasks
                    )
                )
                live = bool(
                    child.status == CollectionStatus.RUNNING.value
                    and not reclaimable
                )
                if not live:
                    child.status = CollectionStatus.QUEUED.value
                    child.lease_owner = None
                    child.lease_until = None
                    child.completed_at = None
                    child.updated_at = now
                    for child_task in running_tasks:
                        child_task.status = CollectionStatus.QUEUED.value
                        child_task.lease_owner = None
                        child_task.lease_until = None
                        child_task.updated_at = now
                        child_task.completed_at = None
                pending.append(
                    {
                        "job_id": child.id,
                        "symbols": list(child.symbols_json or []),
                        "dimensions": list(child.dimensions_json or []),
                        "status": child.status,
                        "attempt": claimed["attempt"],
                        "wait_for_existing": live,
                    }
                )
            if checkpoint_ids:
                checkpoint = dict(parent.checkpoint_json or {})
                checkpoint["child_job_ids"] = sorted(checkpoint_ids)
                parent.checkpoint_json = checkpoint
                parent.updated_at = now

        recovered: list[dict[str, Any]] = []
        for run in pending:
            self._raise_if_parent_cancelled(
                claimed["job_id"],
                cancellation,
                lease_owner=claimed["lease_owner"],
            )
            if not self._append_child_checkpoint(
                claimed["job_id"],
                claimed["task_id"],
                run,
                lease_owner=claimed["lease_owner"],
            ):
                raise _FundExpansionLeaseLost()
            if run.pop("wait_for_existing", False):
                completed = self._wait_for_equity_child_or_reclaim(
                    claimed,
                    run["job_id"],
                    cancellation=cancellation,
                )
            else:
                self.equity_collection.run_job_now(run["job_id"])
                self._raise_if_parent_cancelled(
                    claimed["job_id"],
                    cancellation,
                    lease_owner=claimed["lease_owner"],
                )
                completed = self.equity_collection.get_job(
                    run["job_id"],
                    tenant_id=claimed["tenant_id"],
                    user_id=claimed["user_id"],
                )
            run["status"] = completed["status"]
            recovered.append(run)
            if not self._replace_child_checkpoint(
                claimed["job_id"],
                claimed["task_id"],
                run,
                lease_owner=claimed["lease_owner"],
            ):
                raise _FundExpansionLeaseLost()
        return recovered

    def _wait_for_equity_child_or_reclaim(
        self,
        claimed: Mapping[str, Any],
        child_job_id: str,
        *,
        cancellation: Event | _CancellationView,
        timeout_seconds: float = 7_200,
    ) -> dict[str, Any]:
        """Reuse one live child; reclaim it only after every lease expires."""

        deadline = monotonic() + max(float(timeout_seconds), 1.0)
        terminal = {
            CollectionStatus.SUCCEEDED.value,
            CollectionStatus.PARTIAL.value,
            CollectionStatus.FAILED.value,
            CollectionStatus.CANCELLED.value,
        }
        while True:
            self._raise_if_parent_cancelled(
                str(claimed["job_id"]),
                cancellation,
                lease_owner=str(claimed["lease_owner"]),
            )
            should_run = False
            with self.database.session() as session:
                # Serialize reclaim on SQLite as well as PostgreSQL.
                locked = session.execute(
                    update(MarketDataCollectionJob)
                    .where(MarketDataCollectionJob.id == child_job_id)
                    .values(updated_at=MarketDataCollectionJob.updated_at)
                )
                child = session.get(MarketDataCollectionJob, child_job_id)
                if locked.rowcount != 1 or child is None:
                    raise _FundExpansionFailure(
                        "fund_child_collection_missing",
                        "基金持仓子采集任务已丢失",
                        provider_status="child_job_missing",
                        retryable=True,
                    )
                if child.status in terminal:
                    should_run = False
                elif child.status == CollectionStatus.QUEUED.value:
                    should_run = True
                else:
                    now = self._now()
                    child_tasks = list(
                        session.scalars(
                            select(MarketDataCollectionTask).where(
                                MarketDataCollectionTask.job_id == child_job_id
                            )
                        )
                    )
                    running_tasks = [
                        task
                        for task in child_tasks
                        if task.status == CollectionStatus.RUNNING.value
                    ]
                    reclaimable = bool(
                        child.status == CollectionStatus.RUNNING.value
                        and self._lease_expired(child.lease_until, now=now)
                        and all(
                            self._lease_expired(task.lease_until, now=now)
                            for task in running_tasks
                        )
                    )
                    if reclaimable:
                        child.status = CollectionStatus.QUEUED.value
                        child.lease_owner = None
                        child.lease_until = None
                        child.completed_at = None
                        child.updated_at = now
                        for child_task in running_tasks:
                            child_task.status = CollectionStatus.QUEUED.value
                            child_task.lease_owner = None
                            child_task.lease_until = None
                            child_task.completed_at = None
                            child_task.updated_at = now
                        should_run = True
            if child.status in terminal:
                return self.equity_collection.get_job(
                    child_job_id,
                    tenant_id=str(claimed["tenant_id"]),
                    user_id=str(claimed["user_id"]),
                )
            if should_run:
                self.equity_collection.run_job_now(child_job_id)
                return self.equity_collection.get_job(
                    child_job_id,
                    tenant_id=str(claimed["tenant_id"]),
                    user_id=str(claimed["user_id"]),
                )
            if monotonic() >= deadline:
                raise TimeoutError("fund child durable wait timed out")
            Event().wait(0.25)

    def _raise_if_parent_cancelled(
        self,
        parent_job_id: str,
        cancellation: Event | _CancellationView,
        *,
        lease_owner: str,
    ) -> None:
        if cancellation.is_set():
            if isinstance(cancellation, _CancellationView):
                # Distinguish a durable lease loss from an explicit cancel;
                # only the current owner may write the cancelled state.
                with self.database.session() as session:
                    parent = session.get(MarketDataCollectionJob, parent_job_id)
                    if parent is None:
                        raise _FundExpansionLeaseLost()
                    task = self._parent_task(session, parent_job_id)
                    if not self._owns_active_parent_lease(
                        parent,
                        task,
                        lease_owner=lease_owner,
                        now=self._now(),
                        allow_cancel_requested=True,
                    ):
                        raise _FundExpansionLeaseLost()
            raise _FundExpansionCancelled()
        with self.database.session() as session:
            parent = session.get(MarketDataCollectionJob, parent_job_id)
            if parent is None:
                raise _FundExpansionLeaseLost()
            task = self._parent_task(session, parent_job_id)
            if not self._owns_active_parent_lease(
                parent,
                task,
                lease_owner=lease_owner,
                now=self._now(),
                allow_cancel_requested=True,
            ):
                raise _FundExpansionLeaseLost()
            cancelled = bool(parent.cancel_requested)
        if cancelled:
            raise _FundExpansionCancelled()

    def _missing_equity_dimensions(
        self,
        symbol: str,
        as_of_date: date,
        requested_dimensions: list[str],
    ) -> list[str]:
        now = self._now()
        with self.database.session() as session:
            coverage = SnapshotResolver(
                MarketDataRepository(session),
                registry=self.equity_collection.registry,
            ).coverage(
                symbol,
                "CN_EQUITY",
                as_of_date,
                now if as_of_date == self._local_today() else None,
                exchange_timezone=FUND_EXCHANGE_TIMEZONE,
                now=now,
            )
        by_dimension = {item.dimension: item for item in coverage.dimensions}
        return [
            dimension
            for dimension in requested_dimensions
            if dimension not in by_dimension
            or not (
                by_dimension[dimension].eligible or by_dimension[dimension].degraded
            )
        ]

    def _ensure_equity_bundle(self, symbol: str, as_of_date: date):
        """Return a verified stock Bundle, publishing from complete DB data if needed."""

        now = self._now()
        registry = self.equity_collection.registry
        with self.database.session() as session:
            repository = MarketDataRepository(session)
            for candidate in repository.list_bundles(
                symbol=symbol,
                market="CN_EQUITY",
                as_of_date=as_of_date,
                include_invalidated=False,
                limit=20,
            ):
                if (
                    candidate.status != "published"
                    or candidate.invalidated_at is not None
                ):
                    continue
                try:
                    BundleMaterializer(
                        repository,
                        registry=registry,
                    ).materialize(candidate.id)
                except BundleIntegrityError:
                    continue
                return candidate
        try:
            with self.database.session() as session:
                return BundlePublisher(
                    MarketDataRepository(session),
                    registry=registry,
                ).publish(
                    symbol=symbol,
                    market="CN_EQUITY",
                    as_of_date=as_of_date,
                    cutoff_at=now if as_of_date == self._local_today() else None,
                    exchange_timezone=FUND_EXCHANGE_TIMEZONE,
                    published_by="fund_holding_collection",
                    now=now,
                    allow_degraded=as_of_date != self._local_today(),
                )
        except (BundleIntegrityError, IncompleteBundleError, ValueError):
            return None

    def _complete_parent(
        self,
        claimed: dict[str, Any],
        *,
        snapshot,
        bundle,
        holdings: list[dict[str, Any]],
        child_runs: list[dict[str, Any]],
        remaining: dict[str, list[str]],
        completed_count: int,
        stock_bundles: dict[str, dict[str, str]],
    ) -> bool:
        complete = completed_count == len(holdings) == 10
        now = self._now()
        with self.database.session() as session:
            job = self._lock_fund_job(session, claimed["job_id"])
            task = session.get(MarketDataCollectionTask, claimed["task_id"])
            if (
                job is None
                or task is None
                or not self._owns_active_parent_lease(
                    job,
                    task,
                    lease_owner=claimed["lease_owner"],
                    now=now,
                )
            ):
                return False
            checkpoint = dict(job.checkpoint_json or {})
            checkpoint.pop("retry_scope_by_symbol", None)
            checkpoint.update(
                {
                    "holdings": [
                        {**holding, "missing_dimensions": remaining[holding["symbol"]]}
                        for holding in holdings
                    ],
                    "child_total_count": len(holdings),
                    "child_complete_count": completed_count,
                    "child_missing_by_symbol": {
                        symbol: dimensions
                        for symbol, dimensions in remaining.items()
                        if dimensions
                    },
                    "stock_bundles_by_symbol": stock_bundles,
                    "last_child_runs": child_runs,
                    "completed_at": now.isoformat(),
                }
            )
            job.checkpoint_json = checkpoint
            task_checkpoint = {
                **dict(task.checkpoint_json or {}),
                "parent_snapshot_id": snapshot.id,
                "child_complete_count": completed_count,
                "child_total_count": len(holdings),
            }
            if bundle is not None:
                task_checkpoint["parent_bundle_id"] = bundle.id
            task.checkpoint_json = task_checkpoint
            final_status = (
                CollectionStatus.SUCCEEDED.value
                if complete
                else CollectionStatus.PARTIAL.value
            )
            task.status = final_status
            task.retryable = bool(
                not complete
                and claimed["as_of_date"] == self._local_today()
                and claimed["attempt"] < task.max_attempts
            )
            task.last_error_code = (
                None if complete else "fund_child_collection_incomplete"
            )
            task.last_error_message = (
                None if complete else "一个或多个持仓股票仍缺少报告所需行情维度"
            )
            task.next_retry_at = (
                now + timedelta(seconds=min(60, 2 ** claimed["attempt"]))
                if task.retryable and claimed["attempt"] < task.max_attempts
                else None
            )
            task.lease_owner = None
            task.lease_until = None
            task.updated_at = now
            task.completed_at = now
            job.status = final_status
            job.progress_json = {
                "completed": 1,
                "total": 1,
                "by_status": {final_status: 1},
                "child_complete": completed_count,
                "child_total": len(holdings),
            }
            job.error_summary = None if complete else "fund_child_collection_incomplete"
            job.lease_owner = None
            job.lease_until = None
            job.updated_at = now
            job.completed_at = now
            self._upsert_result(
                session,
                task=task,
                as_of_date=claimed["as_of_date"],
                attempt=claimed["attempt"],
                status="succeeded" if complete else "partial",
                provider_status=(
                    "fund_and_children_ready" if complete else "fund_ready_child_gaps"
                ),
                snapshot_id=snapshot.id,
                metrics={
                    "bundle_id": bundle.id if bundle is not None else None,
                    "manifest_hash": bundle.manifest_hash
                    if bundle is not None
                    else None,
                    "holdings_count": len(holdings),
                    "child_complete_count": completed_count,
                    "child_job_ids": list(checkpoint.get("child_job_ids") or []),
                },
                error_code=None if complete else "fund_child_collection_incomplete",
                error_message=None if complete else "持仓股票行情维度尚未完整",
                retryable=task.retryable,
                started_at=job.started_at,
                completed_at=now,
            )
            return True

    def _fail_parent(
        self,
        job_id: str,
        error: _FundExpansionFailure,
        *,
        lease_owner: str | None = None,
    ) -> bool:
        now = self._now()
        with self.database.session() as session:
            job = self._lock_fund_job(session, job_id)
            if job is None:
                return False
            task = self._parent_task(session, job.id)
            if lease_owner is not None and job.cancel_requested:
                if not self._owns_active_parent_lease(
                    job,
                    task,
                    lease_owner=lease_owner,
                    now=now,
                    allow_cancel_requested=True,
                ):
                    return False
                self._mark_parent_cancelled(job, task, now=now)
                return True
            if lease_owner is not None and (
                not self._owns_active_parent_lease(
                    job,
                    task,
                    lease_owner=lease_owner,
                    now=now,
                )
            ):
                return False
            retryable = bool(error.retryable and task.attempt_count < task.max_attempts)
            task.status = CollectionStatus.FAILED.value
            task.retryable = retryable
            task.last_error_code = error.code
            task.last_error_message = error.message
            task.next_retry_at = (
                now + timedelta(seconds=min(60, 2**task.attempt_count))
                if retryable
                else None
            )
            task.lease_owner = None
            task.lease_until = None
            task.updated_at = now
            task.completed_at = now
            checkpoint = dict(job.checkpoint_json or {})
            checkpoint["last_error"] = {
                "code": error.code,
                "message": error.message,
                "retryable": retryable,
                "at": now.isoformat(),
            }
            job.checkpoint_json = checkpoint
            job.status = CollectionStatus.FAILED.value
            job.progress_json = {
                "completed": 1,
                "total": 1,
                "by_status": {CollectionStatus.FAILED.value: 1},
            }
            job.error_summary = f"{error.code}: {error.message}"
            job.lease_owner = None
            job.lease_until = None
            job.updated_at = now
            job.completed_at = now
            self._upsert_result(
                session,
                task=task,
                as_of_date=task.start_date,
                attempt=task.attempt_count,
                status="data_gap"
                if error.provider_status == "pit_not_supported"
                else "failed",
                provider_status=error.provider_status,
                snapshot_id=error.snapshot_id,
                metrics={},
                error_code=error.code,
                error_message=error.message,
                retryable=retryable,
                started_at=job.started_at,
                completed_at=now,
            )
            return True

    def _cancel_parent(
        self,
        job_id: str,
        *,
        lease_owner: str | None = None,
    ) -> bool:
        now = self._now()
        with self._lock, self.database.session() as session:
            job = self._lock_fund_job(session, job_id)
            if job is None:
                return False
            task = self._parent_task(session, job.id)
            if lease_owner is not None and (
                not self._owns_active_parent_lease(
                    job,
                    task,
                    lease_owner=lease_owner,
                    now=now,
                    allow_cancel_requested=True,
                )
            ):
                return False
            self._mark_parent_cancelled(job, task, now=now)
            return True

    @staticmethod
    def _mark_parent_cancelled(
        job: MarketDataCollectionJob,
        task: MarketDataCollectionTask,
        *,
        now: datetime,
    ) -> None:
        task.status = CollectionStatus.CANCELLED.value
        task.retryable = False
        task.lease_owner = None
        task.lease_until = None
        task.updated_at = now
        task.completed_at = now
        job.status = CollectionStatus.CANCELLED.value
        job.cancel_requested = True
        job.progress_json = {
            "completed": 1,
            "total": 1,
            "by_status": {CollectionStatus.CANCELLED.value: 1},
        }
        job.lease_owner = None
        job.lease_until = None
        job.updated_at = now
        job.completed_at = now

    def _checkpoint_parent_snapshot(
        self,
        job_id: str,
        task_id: str,
        snapshot,
        *,
        lease_owner: str,
    ) -> bool:
        return self._update_checkpoint(
            job_id,
            task_id,
            {
                "parent_snapshot_id": snapshot.id,
                "parent_snapshot_hash": snapshot.content_hash,
                "parent_snapshot_status": snapshot.status,
            },
            lease_owner=lease_owner,
        )

    def _checkpoint_parent_bundle_and_holdings(
        self,
        job_id: str,
        task_id: str,
        *,
        bundle,
        holdings: list[dict[str, Any]],
        lease_owner: str,
    ) -> bool:
        values = {
            "holdings": holdings,
            "child_total_count": len(holdings),
            "child_complete_count": 0,
        }
        if bundle is not None:
            values.update(
                {
                    "parent_bundle_id": bundle.id,
                    "parent_bundle_hash": bundle.manifest_hash,
                }
            )
        return self._update_checkpoint(
            job_id,
            task_id,
            values,
            lease_owner=lease_owner,
        )

    def _append_child_checkpoint(
        self,
        job_id: str,
        task_id: str,
        run: dict[str, Any],
        *,
        lease_owner: str,
    ) -> bool:
        now = self._now()
        with self.database.session() as session:
            job = self._lock_fund_job(session, job_id)
            task = session.get(MarketDataCollectionTask, task_id)
            if (
                job is None
                or task is None
                or not self._owns_active_parent_lease(
                    job,
                    task,
                    lease_owner=lease_owner,
                    now=now,
                )
            ):
                return False
            checkpoint = dict(job.checkpoint_json or {})
            ids = list(checkpoint.get("child_job_ids") or [])
            if run["job_id"] not in ids:
                ids.append(run["job_id"])
            runs = list(checkpoint.get("child_runs") or [])
            for index in range(len(runs) - 1, -1, -1):
                if str(runs[index].get("job_id") or "") == run["job_id"]:
                    runs[index] = dict(run)
                    break
            else:
                runs.append(dict(run))
            checkpoint.update({"child_job_ids": ids, "child_runs": runs})
            job.checkpoint_json = checkpoint
            task.checkpoint_json = {
                **dict(task.checkpoint_json or {}),
                "child_job_ids": ids,
            }
            job.updated_at = now
            return True

    def _replace_child_checkpoint(
        self,
        job_id: str,
        task_id: str,
        run: dict[str, Any],
        *,
        lease_owner: str,
    ) -> bool:
        now = self._now()
        with self.database.session() as session:
            job = self._lock_fund_job(session, job_id)
            task = session.get(MarketDataCollectionTask, task_id)
            if (
                job is None
                or task is None
                or not self._owns_active_parent_lease(
                    job,
                    task,
                    lease_owner=lease_owner,
                    now=now,
                )
            ):
                return False
            checkpoint = dict(job.checkpoint_json or {})
            runs = list(checkpoint.get("child_runs") or [])
            for index in range(len(runs) - 1, -1, -1):
                if str(runs[index].get("job_id") or "") == run["job_id"]:
                    runs[index] = dict(run)
                    break
            checkpoint["child_runs"] = runs
            job.checkpoint_json = checkpoint
            job.updated_at = now
            return True

    def _update_checkpoint(
        self,
        job_id: str,
        task_id: str,
        values: dict[str, Any],
        *,
        lease_owner: str,
    ) -> bool:
        now = self._now()
        with self.database.session() as session:
            job = self._lock_fund_job(session, job_id)
            task = session.get(MarketDataCollectionTask, task_id)
            if (
                job is None
                or task is None
                or not self._owns_active_parent_lease(
                    job,
                    task,
                    lease_owner=lease_owner,
                    now=now,
                )
            ):
                return False
            checkpoint = dict(job.checkpoint_json or {})
            checkpoint.update(values)
            job.checkpoint_json = checkpoint
            task.checkpoint_json = {
                **dict(task.checkpoint_json or {}),
                **{
                    key: value
                    for key, value in values.items()
                    if key.startswith("parent_")
                },
            }
            job.updated_at = now
            task.updated_at = now
            return True

    @staticmethod
    def _upsert_result(
        session,
        *,
        task: MarketDataCollectionTask,
        as_of_date: date,
        attempt: int,
        status: str,
        provider_status: str,
        snapshot_id: str | None,
        metrics: dict[str, Any],
        error_code: str | None,
        error_message: str | None,
        retryable: bool,
        started_at: datetime | None,
        completed_at: datetime,
    ) -> None:
        result = session.scalar(
            select(MarketDataCollectionTaskResult).where(
                MarketDataCollectionTaskResult.task_id == task.id,
                MarketDataCollectionTaskResult.as_of_date == as_of_date,
                MarketDataCollectionTaskResult.attempt == attempt,
            )
        )
        if result is None:
            result = MarketDataCollectionTaskResult(
                task_id=task.id,
                as_of_date=as_of_date,
                attempt=attempt,
                status=status,
            )
            session.add(result)
        result.status = status
        result.provider_status = provider_status
        result.snapshot_id = snapshot_id
        result.metrics_json = metrics
        result.error_code = error_code
        result.error_message = error_message
        result.retryable = retryable
        result.started_at = started_at
        result.completed_at = completed_at

    def _refresh_publication_candidate(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._refresh_publication_candidate_locked(job_id)

    def _refresh_publication_candidate_locked(
        self,
        job_id: str,
    ) -> dict[str, Any] | None:
        with self.database.session() as session:
            job = self._lock_fund_job(session, job_id)
            if job is None:
                return None
            checkpoint = dict(job.checkpoint_json or {})
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
                    "updated_at": iso(self._now()),
                }
                job.checkpoint_json = checkpoint
                return None
            checkpoint["publication_candidate"] = candidate
            publication = dict(checkpoint.get("publication") or {})
            if publication.get("status") != "published":
                checkpoint["publication"] = {
                    "status": "pending_confirmation",
                    "proposal_hash": candidate["proposal_hash"],
                    "updated_at": iso(self._now()),
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
                "基金采集任务尚未完成，不能确认冻结",
                status_code=409,
            )
        checkpoint = dict(job.checkpoint_json or {})
        holdings = list(checkpoint.get("holdings") or [])
        symbols = [str(item.get("symbol") or "").strip().upper() for item in holdings]
        if (
            len(holdings) != 10
            or len(set(symbols)) != 10
            or any(not item for item in symbols)
        ):
            raise CollectionJobError(
                "fund_top_holdings_contract_gap",
                "基金冻结候选必须精确包含十只不重复持仓股票",
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
        as_of = job.start_date

        def cutoff_for(symbol: str, exchange_timezone: str) -> datetime | None:
            saved = baseline_cutoffs.get((symbol, as_of.isoformat()))
            if saved:
                return self._aware_datetime(saved, fallback=evaluated_at)
            if as_of == evaluated_at.astimezone(ZoneInfo(exchange_timezone)).date():
                return evaluated_at
            return None

        fund_publisher = BundlePublisher(
            MarketDataRepository(session), registry=self.registry
        )
        fund_proposal = fund_publisher.propose(
            symbol=str((job.symbols_json or [""])[0]),
            market=FUND_MARKET,
            as_of_date=as_of,
            cutoff_at=cutoff_for(
                str((job.symbols_json or [""])[0]), FUND_EXCHANGE_TIMEZONE
            ),
            exchange_timezone=FUND_EXCHANGE_TIMEZONE,
            now=evaluated_at,
        )
        fund_proposal["asset_role"] = "fund"
        expected_parent_snapshot = str(checkpoint.get("parent_snapshot_id") or "")
        if (
            len(fund_proposal["items"]) != 1
            or fund_proposal["items"][0]["snapshot_id"] != expected_parent_snapshot
        ):
            raise CollectionJobError(
                "collection_freeze_proposal_changed",
                "基金持仓快照已变化，请刷新采集任务后重新确认",
                status_code=409,
            )

        equity_publisher = BundlePublisher(
            MarketDataRepository(session), registry=self.equity_collection.registry
        )
        bundles = [fund_proposal]
        for symbol in symbols:
            market, exchange_timezone = market_scope(symbol)
            proposal = equity_publisher.propose(
                symbol=symbol,
                market=market,
                as_of_date=as_of,
                cutoff_at=cutoff_for(symbol, exchange_timezone),
                exchange_timezone=exchange_timezone,
                now=evaluated_at,
                allow_degraded=as_of
                != evaluated_at.astimezone(ZoneInfo(exchange_timezone)).date(),
            )
            proposal["asset_role"] = "holding"
            bundles.append(proposal)
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
        degraded = any(bool(item["degraded"]) for item in bundles)
        return {
            **proposal_payload,
            "proposal_hash": canonical_hash(proposal_payload),
            "bundle_count": len(bundles),
            "missing_dimensions": missing_dimensions,
            "degraded": degraded,
            "quality": "degraded" if degraded else "strict",
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
            job = self._lock_fund_job(session, job_id)
            if job is None:
                raise CollectionJobError(
                    "collection_job_not_found",
                    "未找到基金持仓扩展任务",
                    status_code=404,
                )
            checkpoint = dict(job.checkpoint_json or {})
            publication = dict(checkpoint.get("publication") or {})
            if publication.get("status") == "published":
                if str(proposal_hash) != str(publication.get("proposal_hash") or ""):
                    raise CollectionJobError(
                        "collection_freeze_proposal_changed",
                        "该任务已按另一冻结候选发布，提交的 proposal_hash 不匹配",
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
                    "前十大持仓 Agent 补采尚未结束，不能确认冻结",
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
            repository = MarketDataRepository(session)
            # A fund freezes eleven Bundle scopes in one transaction. Two
            # portfolios can overlap while listing their holdings in opposite
            # order, so acquiring locks in proposal order can deadlock on
            # PostgreSQL. Pre-lock every parent/holding scope by one global key;
            # the individual publishers then only re-enter locks already held.
            for (
                contract_version,
                symbol,
                market,
                bundle_as_of,
            ) in self._publication_lock_scopes(candidate):
                repository.lock_snapshot_scope_for_publication(
                    symbol=symbol,
                    market=market,
                    as_of_date=bundle_as_of,
                    contract_version=contract_version,
                )
            fund_publisher = BundlePublisher(
                repository, registry=self.registry
            )
            equity_publisher = BundlePublisher(
                repository, registry=self.equity_collection.registry
            )
            published: list[dict[str, Any]] = []
            try:
                for proposal in candidate["bundles"]:
                    publisher = (
                        fund_publisher
                        if proposal["asset_role"] == "fund"
                        else equity_publisher
                    )
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
                            "asset_role": proposal["asset_role"],
                            "symbol": proposal["symbol"],
                            "as_of_date": proposal["as_of_date"],
                            "bundle_id": bundle.id,
                            "manifest_hash": bundle.manifest_hash,
                        }
                    )
            except (IncompleteBundleError, BundleIntegrityError, ValueError) as exc:
                raise CollectionJobError(
                    "collection_freeze_proposal_changed",
                    "冻结候选在发布前发生变化，父基金及持仓 Bundle 均未发布",
                    status_code=409,
                ) from exc
            parent_bundle = next(
                item for item in published if item["asset_role"] == "fund"
            )
            stock_bundles = {
                item["symbol"]: {
                    "bundle_id": item["bundle_id"],
                    "manifest_hash": item["manifest_hash"],
                }
                for item in published
                if item["asset_role"] == "holding"
            }
            confirmed_at = self._now()
            publication = {
                "status": "published",
                "proposal_hash": candidate["proposal_hash"],
                "confirmed_at": confirmed_at.isoformat(),
                "confirmed_by": published_by,
                "bundles": published,
            }
            checkpoint.update(
                {
                    "parent_bundle_id": parent_bundle["bundle_id"],
                    "parent_bundle_hash": parent_bundle["manifest_hash"],
                    "stock_bundles_by_symbol": stock_bundles,
                    "publication_candidate": candidate,
                    "publication": publication,
                }
            )
            job.checkpoint_json = checkpoint
            task = self._parent_task(session, job.id)
            task.checkpoint_json = {
                **dict(task.checkpoint_json or {}),
                "parent_bundle_id": parent_bundle["bundle_id"],
            }
            job.updated_at = confirmed_at
            return publication

    @staticmethod
    def _publication_lock_scopes(
        candidate: Mapping[str, Any],
    ) -> list[tuple[str, str, str, date]]:
        """Return all candidate Bundle scopes in a process-independent order."""

        scopes = {
            (
                str(proposal["contract_version"]),
                str(proposal["symbol"]).strip().upper(),
                str(proposal["market"]).strip().upper(),
                date.fromisoformat(str(proposal["as_of_date"])),
            )
            for proposal in list(candidate.get("bundles") or [])
        }
        return sorted(scopes)

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
        if job.status not in {
            CollectionStatus.SUCCEEDED.value,
            CollectionStatus.PARTIAL.value,
            CollectionStatus.FAILED.value,
            CollectionStatus.CANCELLED.value,
        }:
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

    def _job_payload(self, session, job: MarketDataCollectionJob) -> dict[str, Any]:
        task = self._parent_task(session, job.id)
        checkpoint = dict(job.checkpoint_json or {})
        return {
            "id": job.id,
            "job_id": job.id,
            "job_type": job.job_type,
            "parent_job_id": job.parent_job_id,
            "origin": job.origin,
            "subscription_id": job.subscription_id,
            "mode": job.mode,
            "status": job.status,
            "asset_family": "fund",
            "top_n": 10,
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
            "expanded_holdings": list(checkpoint.get("holdings") or []),
            "child_collection_job_ids": list(checkpoint.get("child_job_ids") or []),
            "cancel_requested": job.cancel_requested,
            "error_summary": job.error_summary,
            "created_at": iso(job.created_at),
            "started_at": iso(job.started_at),
            "completed_at": iso(job.completed_at),
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
                }
            ],
        }

    def _owned_parent_job(
        self,
        session,
        job_id: str,
        *,
        tenant_id: str,
        user_id: str,
        lock: bool = False,
    ) -> MarketDataCollectionJob:
        job = (
            self._lock_fund_job(session, job_id)
            if lock
            else session.get(MarketDataCollectionJob, job_id)
        )
        if (
            job is None
            or job.job_type != FUND_HOLDING_JOB_TYPE
            or job.tenant_id != tenant_id
            or job.user_id != user_id
        ):
            raise CollectionJobError(
                "collection_job_not_found",
                "未找到基金持仓扩展任务",
                status_code=404,
            )
        return job

    @staticmethod
    def _parent_task(session, job_id: str) -> MarketDataCollectionTask:
        tasks = list(
            session.scalars(
                select(MarketDataCollectionTask).where(
                    MarketDataCollectionTask.job_id == job_id
                )
            )
        )
        if len(tasks) != 1:
            raise RuntimeError(
                "fund holding expansion requires exactly one parent task"
            )
        return tasks[0]

    @property
    def _fund_dimension(self) -> str:
        if len(self.registry.dimension_ids) != 1:
            raise ValueError("fund holding coordinator requires a one-slot registry")
        dimension = self.registry.dimension_ids[0]
        if dimension != "fund_top_holdings":
            raise ValueError("fund holding registry must expose fund_top_holdings")
        return dimension

    def _equity_dimensions(self, values: Iterable[str] | str) -> list[str]:
        registry_ids = list(self.equity_collection.registry.dimension_ids)
        if isinstance(values, str):
            if values.strip().lower() == "all":
                return registry_ids
            values = [values]
        normalized = list(dict.fromkeys(str(item or "").strip() for item in values))
        unknown = [item for item in normalized if item not in registry_ids]
        if unknown:
            raise CollectionJobError(
                "collection_dimensions_invalid",
                f"未知股票采集维度：{', '.join(unknown)}",
            )
        if not normalized:
            raise CollectionJobError(
                "collection_dimensions_invalid",
                "基金持仓扩展至少需要一个股票采集维度",
            )
        return normalized

    @staticmethod
    def _fund_code(value: str) -> str:
        match = _FUND_CODE.fullmatch(str(value or "").strip())
        if match is None:
            raise CollectionJobError(
                "fund_code_invalid",
                "基金代码必须是六位代码",
            )
        return match.group("code")

    def _resolve_fund_code(
        self,
        *,
        fund_code: str | None,
        symbols: Iterable[str] | None,
    ) -> str:
        symbol_rows: list[str] = []
        if symbols is not None:
            symbol_rows = (
                [symbols]
                if isinstance(symbols, str)
                else [str(item) for item in symbols]
            )
            if len(symbol_rows) != 1:
                raise CollectionJobError(
                    "fund_collection_symbol_count_invalid",
                    "基金持仓父任务必须且只能包含一个基金代码",
                )
        candidates = [
            self._fund_code(value)
            for value in [fund_code, *symbol_rows]
            if value is not None and str(value).strip()
        ]
        if not candidates:
            raise CollectionJobError("fund_code_missing", "基金持仓采集需要 fund_code")
        if len(set(candidates)) != 1:
            raise CollectionJobError(
                "fund_collection_symbol_conflict",
                "fund_code 与 symbols 指向不同基金",
            )
        return candidates[0]

    @staticmethod
    def _resolve_as_of_date(
        *,
        as_of_date: date | None,
        start_date: date | None,
        end_date: date | None,
    ) -> date:
        if (start_date is None) != (end_date is None):
            raise CollectionJobError(
                "fund_collection_date_range_invalid",
                "订阅兼容调用必须同时提供 start_date 与 end_date",
            )
        if start_date is not None and start_date != end_date:
            raise CollectionJobError(
                "fund_collection_date_range_invalid",
                "基金持仓父任务仅支持单日采集",
            )
        candidates = [value for value in (as_of_date, start_date) if value is not None]
        if not candidates:
            raise CollectionJobError(
                "fund_collection_date_missing",
                "基金持仓采集需要 as_of_date 或单日 start_date/end_date",
            )
        if len(set(candidates)) != 1:
            raise CollectionJobError(
                "fund_collection_date_conflict",
                "as_of_date 与 start_date/end_date 不一致",
            )
        return candidates[0]

    def _validate_parent_dimensions(self, values: Iterable[str] | str | None) -> None:
        if values is None:
            return
        normalized = (
            [values]
            if isinstance(values, str)
            else [str(item or "").strip() for item in values]
        )
        if normalized != [self._fund_dimension]:
            raise CollectionJobError(
                "fund_collection_dimensions_invalid",
                "基金持仓父任务只允许 fund_top_holdings 单槽",
            )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("fund holding collection clock must be timezone-aware")
        return value.astimezone(timezone.utc)

    def _local_today(self) -> date:
        return self._now().astimezone(ZoneInfo(FUND_EXCHANGE_TIMEZONE)).date()


__all__ = [
    "FUND_EXCHANGE_TIMEZONE",
    "FUND_HOLDING_JOB_TYPE",
    "FUND_MARKET",
    "FundHoldingCollectionCoordinator",
]
