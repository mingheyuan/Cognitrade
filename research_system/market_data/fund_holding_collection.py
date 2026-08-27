from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from threading import Event, RLock
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, update

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
        self.equity_collection = equity_collection or MarketDataCollectionCoordinator(database)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, int(max_background_jobs)),
            thread_name_prefix="fund-holding-job",
        )
        self._futures: dict[str, Future[None]] = {}
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
        publish_when_complete: bool = True,
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
        if not publish_when_complete:
            raise CollectionJobError(
                "fund_bundle_publication_required",
                "基金持仓扩展必须发布父基金及十只股票的完整 Bundle",
            )
        normalized_origin = str(origin or "manual").strip().lower()
        if normalized_origin not in {"manual", "subscription"}:
            raise CollectionJobError("collection_origin_invalid", "不支持的采集任务来源")
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
            "publish_when_complete": True,
            "origin": normalized_origin,
            "subscription_id": normalized_subscription_id,
            "asset_family": "fund",
            "top_n": 10,
            "investment_cycle": investment_cycle,
        }
        resolved_key = str(idempotency_key or generate_ulid())
        with self.database.session() as session:
            if normalized_origin == "subscription" and normalized_claim_until is not None:
                launch = session.execute(
                    update(MarketDataSubscription)
                    .where(
                        MarketDataSubscription.id == normalized_subscription_id,
                        MarketDataSubscription.tenant_id == tenant_id,
                        MarketDataSubscription.user_id == user_id,
                        MarketDataSubscription.enabled.is_(True),
                        MarketDataSubscription.next_run_at == normalized_claim_until,
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
            existing = session.scalar(
                select(MarketDataCollectionJob).where(
                    MarketDataCollectionJob.tenant_id == tenant_id,
                    MarketDataCollectionJob.user_id == user_id,
                    MarketDataCollectionJob.idempotency_key == resolved_key,
                )
            )
            if existing is not None:
                if existing.job_type != FUND_HOLDING_JOB_TYPE:
                    raise CollectionJobError(
                        "collection_idempotency_conflict",
                        "idempotency_key 已被其他类型采集任务使用",
                        status_code=409,
                    )
                job_id = existing.id
            else:
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
                    publish_when_complete=True,
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
        if submit:
            self.submit(job_id)
        return self.get_job(job_id, tenant_id=tenant_id, user_id=user_id)

    def submit(self, job_id: str) -> None:
        with self._lock:
            future = self._futures.get(job_id)
            if future is not None and not future.done():
                return
            cancellation = self._cancellations.setdefault(job_id, Event())
            cancellation.clear()
            self._futures[job_id] = self._executor.submit(
                self._run_job,
                job_id,
                cancellation,
            )

    def run_job_now(self, job_id: str) -> None:
        cancellation = self._cancellations.setdefault(job_id, Event())
        cancellation.clear()
        self._run_job(job_id, cancellation)

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
            checkpoint = dict(job.checkpoint_json or {})
            if scoped:
                holdings = {
                    str(holding.get("symbol") or "").upper()
                    for holding in list(checkpoint.get("holdings") or [])
                    if str(holding.get("symbol") or "").strip()
                }
                selected_symbols = set(requested_symbols) if requested_symbols else holdings
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
                child_order = {child_id: index for index, child_id in enumerate(child_ids)}
                latest_tasks: dict[tuple[str, str], MarketDataCollectionTask] = {}
                for child_task in child_tasks:
                    slot = (child_task.symbol, child_task.dimension)
                    current = latest_tasks.get(slot)
                    if current is None or child_order.get(child_task.job_id, -1) > child_order.get(
                        current.job_id, -1
                    ):
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
                    if not child_task.retryable or child_task.attempt_count >= child_task.max_attempts:
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

    def cancel(
        self,
        job_id: str,
        *,
        tenant_id: str,
        user_id: str,
        actor_id: str,
    ) -> dict[str, Any]:
        with self._lock:
            with self.database.session() as session:
                job = self._owned_parent_job(
                    session,
                    job_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                )
                if job.status in {
                    CollectionStatus.SUCCEEDED.value,
                    CollectionStatus.FAILED.value,
                    CollectionStatus.PARTIAL.value,
                    CollectionStatus.CANCELLED.value,
                }:
                    return self._job_payload(session, job)
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
        with self.database.session() as session:
            self._cancel_linked_active_children(
                session,
                parent_job_id=job_id,
                now=self._now(),
                reason="parent fund holding collection was cancelled",
            )
        return self.get_job(job_id, tenant_id=tenant_id, user_id=user_id)

    def resume_pending(self, *, resume_subscription_jobs: bool = True) -> list[str]:
        with self.database.session() as session:
            jobs = list(
                session.scalars(
                    select(MarketDataCollectionJob).where(
                        MarketDataCollectionJob.job_type == FUND_HOLDING_JOB_TYPE,
                        MarketDataCollectionJob.status.in_(
                            (CollectionStatus.QUEUED.value, CollectionStatus.RUNNING.value)
                        ),
                        MarketDataCollectionJob.cancel_requested.is_(False),
                    )
                )
            )
            now = self._now()
            resumable: list[MarketDataCollectionJob] = []
            for job in jobs:
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
                        task = self._parent_task(session, job.id)
                        cancellation_reason = (
                            "subscription job not resumed because global or per-symbol "
                            "opt-in is disabled"
                        )
                        job.status = CollectionStatus.CANCELLED.value
                        job.cancel_requested = True
                        job.lease_owner = None
                        job.lease_until = None
                        job.error_summary = cancellation_reason
                        job.updated_at = now
                        job.completed_at = now
                        task.status = CollectionStatus.CANCELLED.value
                        task.retryable = False
                        task.lease_owner = None
                        task.lease_until = None
                        task.updated_at = now
                        task.completed_at = now
                        self._cancel_linked_active_children(
                            session,
                            parent_job_id=job.id,
                            now=now,
                            reason=cancellation_reason,
                        )
                        continue
                job.status = CollectionStatus.QUEUED.value
                job.lease_owner = None
                job.lease_until = None
                job.updated_at = now
                task = self._parent_task(session, job.id)
                if task.status == CollectionStatus.RUNNING.value:
                    task.status = CollectionStatus.QUEUED.value
                    task.lease_owner = None
                    task.lease_until = None
                    task.updated_at = now
                resumable.append(job)
            job_ids = [job.id for job in resumable]
        for job_id in job_ids:
            self.submit(job_id)
        return job_ids

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

    def _run_job(self, job_id: str, cancellation: Event) -> None:
        try:
            claimed = self._claim(job_id)
            if claimed is None:
                return
            if cancellation.is_set():
                raise _FundExpansionCancelled()
            snapshot = self._resolve_or_collect_parent(claimed, cancellation=cancellation)
            self._checkpoint_parent_snapshot(job_id, claimed["task_id"], snapshot)
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
            bundle = self._ensure_parent_bundle(claimed, snapshot)
            holdings = self._holdings(snapshot)
            self._checkpoint_parent_bundle_and_holdings(
                job_id,
                claimed["task_id"],
                bundle=bundle,
                holdings=holdings,
            )
            if cancellation.is_set():
                raise _FundExpansionCancelled()
            child_runs = self._run_equity_children(
                claimed,
                holdings=holdings,
                cancellation=cancellation,
            )
            self._raise_if_parent_cancelled(claimed["job_id"], cancellation)
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
                self._raise_if_parent_cancelled(claimed["job_id"], cancellation)
                symbol = str(holding["symbol"])
                if remaining[symbol]:
                    continue
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
                not dimensions and symbol in stock_bundles
                for symbol, dimensions in remaining.items()
            )
            with self._lock:
                self._raise_if_parent_cancelled(claimed["job_id"], cancellation)
                self._complete_parent(
                    claimed,
                    snapshot=snapshot,
                    bundle=bundle,
                    holdings=holdings,
                    child_runs=child_runs,
                    remaining=remaining,
                    completed_count=completed_count,
                    stock_bundles=stock_bundles,
                )
        except _FundExpansionCancelled:
            self._cancel_parent(job_id)
        except _FundExpansionFailure as exc:
            self._fail_parent(job_id, exc)
        except Exception as exc:  # noqa: BLE001 - worker boundary persists a safe summary
            self._fail_parent(
                job_id,
                _FundExpansionFailure(
                    "fund_holding_worker_failed",
                    f"{exc.__class__.__name__}: 基金持仓扩展执行失败",
                    provider_status="worker_error",
                    retryable=True,
                ),
            )

    def _claim(self, job_id: str) -> dict[str, Any] | None:
        with self.database.session() as session:
            job = session.get(MarketDataCollectionJob, job_id)
            if job is None or job.job_type != FUND_HOLDING_JOB_TYPE or job.cancel_requested:
                return None
            task = self._parent_task(session, job.id)
            if task.status != CollectionStatus.QUEUED.value:
                return None
            now = self._now()
            worker_id = f"fund-holding:{generate_ulid()}"
            job.status = CollectionStatus.RUNNING.value
            job.started_at = job.started_at or now
            job.updated_at = now
            job.lease_owner = worker_id
            job.lease_until = now + timedelta(minutes=30)
            task.status = CollectionStatus.RUNNING.value
            task.attempt_count += 1
            task.lease_owner = worker_id
            task.lease_until = now + timedelta(minutes=30)
            task.updated_at = now
            request = dict(job.request_json or {})
            return {
                "job_id": job.id,
                "task_id": task.id,
                "tenant_id": job.tenant_id,
                "user_id": job.user_id,
                "actor_id": job.requested_by,
                "origin": job.origin,
                "subscription_id": job.subscription_id,
                "fund_code": str(request.get("fund_code") or task.symbol),
                "as_of_date": task.start_date,
                "mode": job.mode,
                "max_concurrency": job.max_concurrency,
                "investment_cycle": str(request.get("investment_cycle") or "long_term"),
                "equity_dimensions": list(
                    request.get("equity_dimensions") or self.equity_collection.registry.dimension_ids
                ),
                "retry_scope_by_symbol": {
                    str(symbol): [str(dimension) for dimension in dimensions]
                    for symbol, dimensions in dict(
                        dict(job.checkpoint_json or {}).get("retry_scope_by_symbol") or {}
                    ).items()
                },
                "attempt": task.attempt_count,
                "checkpoint": dict(job.checkpoint_json or {}),
            }

    def _resolve_or_collect_parent(
        self,
        claimed: dict[str, Any],
        *,
        cancellation: Event,
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
            raw = self._collect_current_fund_snapshot(claimed)
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
        except _FundExpansionFailure:
            raise
        except Exception as exc:
            raise _FundExpansionFailure(
                "fund_snapshot_collection_failed",
                f"{exc.__class__.__name__}: 当前基金持仓采集失败",
                provider_status="provider_error",
                retryable=True,
            ) from exc

    def _collect_current_fund_snapshot(self, claimed: dict[str, Any]) -> Mapping[str, Any]:
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
        payload = call(
            query=f"显式采集基金 {claimed['fund_code']} 前十大持仓",
            fund_codes=[claimed["fund_code"]],
            fund_category=None,
            candidate_pool_size=1,
            investment_cycle=claimed["investment_cycle"],
        )
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
                    cutoff_at=now if claimed["as_of_date"] == self._local_today() else None,
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

        validated = validate_fund_top_holdings_payload(dict(snapshot.payload_json or {}))
        holdings = [item.model_dump(mode="json", exclude_none=False) for item in validated.holdings]
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
        cancellation: Event,
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
            if cancellation.is_set():
                raise _FundExpansionCancelled()
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
                publish_when_complete=True,
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
            self._append_child_checkpoint(claimed["job_id"], claimed["task_id"], run)
            self.equity_collection.run_job_now(child["id"])
            self._raise_if_parent_cancelled(claimed["job_id"], cancellation)
            completed = self.equity_collection.get_job(
                child["id"],
                tenant_id=claimed["tenant_id"],
                user_id=claimed["user_id"],
            )
            run["status"] = completed["status"]
            child_runs.append(run)
            self._replace_child_checkpoint(claimed["job_id"], claimed["task_id"], run)
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
                        MarketDataCollectionJob.job_type.in_(EQUITY_COLLECTION_JOB_TYPES),
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
        cancellation: Event,
    ) -> list[dict[str, Any]]:
        """Reclaim queued/running children exclusively owned by this parent.

        The database parent link is also consulted because a process can stop
        after the child transaction commits but just before its id is appended
        to the parent's checkpoint.
        """

        with self.database.session() as session:
            parent = session.get(MarketDataCollectionJob, claimed["job_id"])
            checkpoint_ids = {
                str(job_id)
                for job_id in dict(parent.checkpoint_json or {}).get("child_job_ids", [])
                if str(job_id).strip()
            } if parent is not None else set()
            linked_children = list(
                session.scalars(
                    select(MarketDataCollectionJob)
                    .where(
                        MarketDataCollectionJob.parent_job_id == claimed["job_id"],
                        MarketDataCollectionJob.job_type.in_(EQUITY_COLLECTION_JOB_TYPES),
                        MarketDataCollectionJob.status.in_(
                            (CollectionStatus.QUEUED.value, CollectionStatus.RUNNING.value)
                        ),
                        MarketDataCollectionJob.cancel_requested.is_(False),
                    )
                    .order_by(
                        MarketDataCollectionJob.created_at,
                        MarketDataCollectionJob.id,
                    )
                )
            )
            now = self._now()
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
                child.status = CollectionStatus.QUEUED.value
                child.lease_owner = None
                child.lease_until = None
                child.completed_at = None
                child.updated_at = now
                session.query(MarketDataCollectionTask).filter(
                    MarketDataCollectionTask.job_id == child.id,
                    MarketDataCollectionTask.status == CollectionStatus.RUNNING.value,
                ).update(
                    {
                        "status": CollectionStatus.QUEUED.value,
                        "lease_owner": None,
                        "lease_until": None,
                        "updated_at": now,
                        "completed_at": None,
                    },
                    synchronize_session=False,
                )
                pending.append(
                    {
                        "job_id": child.id,
                        "symbols": list(child.symbols_json or []),
                        "dimensions": list(child.dimensions_json or []),
                        "status": CollectionStatus.QUEUED.value,
                        "attempt": claimed["attempt"],
                    }
                )
            if parent is not None and checkpoint_ids:
                checkpoint = dict(parent.checkpoint_json or {})
                checkpoint["child_job_ids"] = sorted(checkpoint_ids)
                parent.checkpoint_json = checkpoint
                parent.updated_at = now

        recovered: list[dict[str, Any]] = []
        for run in pending:
            if cancellation.is_set():
                raise _FundExpansionCancelled()
            self._append_child_checkpoint(claimed["job_id"], claimed["task_id"], run)
            self.equity_collection.run_job_now(run["job_id"])
            self._raise_if_parent_cancelled(claimed["job_id"], cancellation)
            completed = self.equity_collection.get_job(
                run["job_id"],
                tenant_id=claimed["tenant_id"],
                user_id=claimed["user_id"],
            )
            run["status"] = completed["status"]
            recovered.append(run)
            self._replace_child_checkpoint(claimed["job_id"], claimed["task_id"], run)
        return recovered

    def _raise_if_parent_cancelled(
        self,
        parent_job_id: str,
        cancellation: Event,
    ) -> None:
        if cancellation.is_set():
            raise _FundExpansionCancelled()
        with self.database.session() as session:
            parent = session.get(MarketDataCollectionJob, parent_job_id)
            cancelled = bool(
                parent is not None
                and (
                    parent.cancel_requested
                    or parent.status == CollectionStatus.CANCELLED.value
                )
            )
        if cancelled:
            cancellation.set()
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
                by_dimension[dimension].eligible
                or by_dimension[dimension].degraded
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
                if candidate.status != "published" or candidate.invalidated_at is not None:
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
    ) -> None:
        complete = completed_count == len(holdings) == 10
        now = self._now()
        with self.database.session() as session:
            job = session.get(MarketDataCollectionJob, claimed["job_id"])
            task = session.get(MarketDataCollectionTask, claimed["task_id"])
            if job is None or task is None:
                return
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
            task.checkpoint_json = {
                "parent_snapshot_id": snapshot.id,
                "parent_bundle_id": bundle.id,
                "child_complete_count": completed_count,
                "child_total_count": len(holdings),
            }
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
            task.last_error_code = None if complete else "fund_child_collection_incomplete"
            task.last_error_message = (
                None if complete else "一个或多个持仓股票仍缺少报告所需行情维度或已发布 Bundle"
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
                    "fund_and_children_ready"
                    if complete
                    else "fund_ready_child_gaps"
                ),
                snapshot_id=snapshot.id,
                metrics={
                    "bundle_id": bundle.id,
                    "manifest_hash": bundle.manifest_hash,
                    "holdings_count": len(holdings),
                    "child_complete_count": completed_count,
                    "child_job_ids": list(checkpoint.get("child_job_ids") or []),
                },
                error_code=None if complete else "fund_child_collection_incomplete",
                error_message=None if complete else "持仓股票行情维度或已发布 Bundle 尚未完整",
                retryable=task.retryable,
                started_at=job.started_at,
                completed_at=now,
            )

    def _fail_parent(self, job_id: str, error: _FundExpansionFailure) -> None:
        now = self._now()
        with self.database.session() as session:
            job = session.get(MarketDataCollectionJob, job_id)
            if job is None:
                return
            task = self._parent_task(session, job.id)
            retryable = bool(error.retryable and task.attempt_count < task.max_attempts)
            task.status = CollectionStatus.FAILED.value
            task.retryable = retryable
            task.last_error_code = error.code
            task.last_error_message = error.message
            task.next_retry_at = (
                now + timedelta(seconds=min(60, 2 ** task.attempt_count))
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
                status="data_gap" if error.provider_status == "pit_not_supported" else "failed",
                provider_status=error.provider_status,
                snapshot_id=error.snapshot_id,
                metrics={},
                error_code=error.code,
                error_message=error.message,
                retryable=retryable,
                started_at=job.started_at,
                completed_at=now,
            )

    def _cancel_parent(self, job_id: str) -> None:
        now = self._now()
        with self.database.session() as session:
            job = session.get(MarketDataCollectionJob, job_id)
            if job is None:
                return
            task = self._parent_task(session, job.id)
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

    def _checkpoint_parent_snapshot(self, job_id: str, task_id: str, snapshot) -> None:
        self._update_checkpoint(
            job_id,
            task_id,
            {
                "parent_snapshot_id": snapshot.id,
                "parent_snapshot_hash": snapshot.content_hash,
                "parent_snapshot_status": snapshot.status,
            },
        )

    def _checkpoint_parent_bundle_and_holdings(
        self,
        job_id: str,
        task_id: str,
        *,
        bundle,
        holdings: list[dict[str, Any]],
    ) -> None:
        self._update_checkpoint(
            job_id,
            task_id,
            {
                "parent_bundle_id": bundle.id,
                "parent_bundle_hash": bundle.manifest_hash,
                "holdings": holdings,
                "child_total_count": len(holdings),
                "child_complete_count": 0,
            },
        )

    def _append_child_checkpoint(
        self,
        job_id: str,
        task_id: str,
        run: dict[str, Any],
    ) -> None:
        with self.database.session() as session:
            job = session.get(MarketDataCollectionJob, job_id)
            task = session.get(MarketDataCollectionTask, task_id)
            if job is None or task is None:
                return
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
            job.updated_at = self._now()

    def _replace_child_checkpoint(
        self,
        job_id: str,
        task_id: str,
        run: dict[str, Any],
    ) -> None:
        with self.database.session() as session:
            job = session.get(MarketDataCollectionJob, job_id)
            task = session.get(MarketDataCollectionTask, task_id)
            if job is None or task is None:
                return
            checkpoint = dict(job.checkpoint_json or {})
            runs = list(checkpoint.get("child_runs") or [])
            for index in range(len(runs) - 1, -1, -1):
                if str(runs[index].get("job_id") or "") == run["job_id"]:
                    runs[index] = dict(run)
                    break
            checkpoint["child_runs"] = runs
            job.checkpoint_json = checkpoint
            job.updated_at = self._now()

    def _update_checkpoint(
        self,
        job_id: str,
        task_id: str,
        values: dict[str, Any],
    ) -> None:
        with self.database.session() as session:
            job = session.get(MarketDataCollectionJob, job_id)
            task = session.get(MarketDataCollectionTask, task_id)
            if job is None or task is None:
                return
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
            job.updated_at = self._now()
            task.updated_at = self._now()

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

    @staticmethod
    def _owned_parent_job(
        session,
        job_id: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> MarketDataCollectionJob:
        job = session.get(MarketDataCollectionJob, job_id)
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
            raise RuntimeError("fund holding expansion requires exactly one parent task")
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
