from __future__ import annotations

"""Application facade for the database-first market-data center.

Every read method is side-effect free. External acquisition is reachable only
through an explicit collection job or an explicitly enabled subscription tick.
"""

from collections.abc import Mapping
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from research_system.market_data.bundles import (
    BundleIntegrityError,
    BundleMaterializer,
    BundlePublisher,
    IncompleteBundleError,
)
from research_system.market_data.collection import (
    EQUITY_COLLECTION_JOB_TYPES,
    CollectionJobError,
    MarketDataCollectionCoordinator,
    market_scope,
)
from research_system.market_data.contracts import EvidenceInput, SnapshotIngestInput
from research_system.market_data.dimension_contracts import (
    DEFAULT_DIMENSION_REGISTRY,
    DimensionRegistry,
)
from research_system.market_data.fund_holding_collection import (
    FUND_HOLDING_JOB_TYPE,
    FUND_MARKET,
    FundHoldingCollectionCoordinator,
)
from research_system.market_data.fund_holdings_contracts import (
    FUND_TOP_HOLDINGS_REGISTRY,
    validate_fund_top_holdings_payload,
)
from research_system.market_data.ingestion import (
    SnapshotIngestionError,
    SnapshotIngestionService,
)
from research_system.market_data.instruments import (
    AssetType,
    SecurityIdentityError,
    SecurityIdentityResolver,
)
from research_system.market_data.job_diagnostics import (
    summarize_fund_portfolio,
    summarize_tasks,
)
from research_system.market_data.models import (
    MarketDataBundle,
    MarketDataBundleItem,
    MarketDataCollectionJob,
    MarketDataCollectionTask,
    MarketDataCollectionTaskResult,
    MarketDataConflict,
    MarketDataSnapshot,
    MarketDataSnapshotEvidence,
    ReportDataManifestDependency,
    ReportDataManifestItem,
)
from research_system.market_data.providers import MarketDataProviderCatalog
from research_system.market_data.report_inputs import (
    DatabaseReportInputGateway,
    ReportInputError,
)
from research_system.market_data.repository import MarketDataRepository
from research_system.market_data.resolver import SnapshotResolver
from research_system.market_data.subscriptions import (
    MarketDataSubscriptionScheduler,
    MarketDataSubscriptionService,
    SubscriptionError,
)
from research_system.runtime.platform.settings import Settings
from research_system.strategies.database import StrategyDatabase


class MarketDataServiceError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.status_code = int(status_code)
        self.details = dict(details or {})


class MarketDataCenterService:
    """Stable API over PIT storage, acquisition, subscriptions and reports."""

    def __init__(
        self,
        database: StrategyDatabase,
        settings: Settings,
        *,
        registry: DimensionRegistry = DEFAULT_DIMENSION_REGISTRY,
        collection: MarketDataCollectionCoordinator | None = None,
        fund_collection: FundHoldingCollectionCoordinator | None = None,
        identity_resolver: SecurityIdentityResolver | None = None,
        providers: MarketDataProviderCatalog | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.registry = registry
        self.collection = collection or MarketDataCollectionCoordinator(database, registry=registry)
        self.fund_registry = FUND_TOP_HOLDINGS_REGISTRY
        self.fund_collection = fund_collection or FundHoldingCollectionCoordinator(
            database,
            equity_collection=self.collection,
            registry=self.fund_registry,
        )
        if identity_resolver is None:
            from domain_packs.finance_ashare.tools.fund_master import FundSecurityMaster

            identity_resolver = SecurityIdentityResolver(FundSecurityMaster())
        self.identity_resolver = identity_resolver
        self.providers = providers or MarketDataProviderCatalog(settings)
        self.subscriptions = MarketDataSubscriptionService(
            database,
            self.collection,
            automatic_collection_enabled=bool(
                getattr(settings, "market_data_auto_collection_enabled", False)
            ),
            registry=registry,
            markets=("CN_EQUITY", "HK_EQUITY", "US_EQUITY"),
        )
        self.subscription_scheduler = MarketDataSubscriptionScheduler(self.subscriptions)
        self.fund_subscriptions = MarketDataSubscriptionService(
            database,
            self.fund_collection,
            automatic_collection_enabled=bool(
                getattr(settings, "market_data_auto_collection_enabled", False)
            ),
            registry=self.fund_registry,
            markets=(FUND_MARKET,),
        )
        self.fund_subscription_scheduler = MarketDataSubscriptionScheduler(
            self.fund_subscriptions
        )
        self.report_gateway = DatabaseReportInputGateway(database)

    # Read-only contract and coverage -------------------------------------------------
    def get_contract(self, *, market: str = "CN_EQUITY") -> dict[str, Any]:
        registry = self._registry_for_market(market)
        payload = registry.contract_payload()
        payload["dimensions"] = [
            {**item, "id": item["dimension"], "order": item["ordinal"]}
            for item in payload["dimensions"]
        ]
        payload["server_time"] = _iso(datetime.now(timezone.utc))
        payload["collector_count"] = registry.expected_dimension_count
        payload["asset_family"] = "fund" if registry is self.fund_registry else "equity"
        payload["market"] = str(market or "CN_EQUITY").strip().upper()
        payload["derived_report_dimensions"] = (
            [] if registry is self.fund_registry else ["20", "21", "22"]
        )
        return payload

    def get_coverage(
        self,
        *,
        symbol: str,
        market: str,
        as_of_date: date,
        cutoff_at: datetime | None = None,
    ) -> dict[str, Any]:
        symbol = _symbol(symbol)
        resolved_market, exchange_timezone = _market_and_timezone(symbol, market)
        registry = self._registry_for_market(resolved_market)
        with self.database.session() as session:
            repository = MarketDataRepository(session)
            coverage = SnapshotResolver(repository, registry=registry).coverage(
                symbol,
                resolved_market,
                as_of_date,
                cutoff_at,
                exchange_timezone=exchange_timezone,
            )
            latest = repository.latest_bundle(
                symbol=symbol,
                market=resolved_market,
                as_of_date=as_of_date,
                contract_version=registry.contract_version,
            )
            result = coverage.model_dump(mode="json")
            result["counts"] = {
                "available": coverage.available_count,
                "verified_empty": coverage.verified_empty_count,
                "data_gap": coverage.gap_count,
                "conflict": coverage.conflict_count,
                "stale": coverage.stale_count,
                "degraded": coverage.degraded_dimension_count,
            }
            result["dimensions"] = [
                {
                    **row,
                    "dimension_id": row["dimension"],
                    "freshness_status": (
                        "stale" if row["stale"] else "fresh" if row["snapshot_id"] else "unknown"
                    ),
                }
                for row in result["dimensions"]
            ]
            result["latest_bundle"] = (
                self._bundle_payload(session, latest, include_items=False)
                if latest is not None
                else None
            )
            return result

    def list_snapshots(
        self,
        *,
        symbol: str | None = None,
        dimension_id: str | None = None,
        as_of_date: date | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        page_limit = max(1, min(int(limit), 200))
        page_offset = max(0, int(offset))
        with self.database.session() as session:
            filters = []
            if symbol:
                filters.append(MarketDataSnapshot.symbol == _symbol(symbol))
            if dimension_id:
                filters.append(MarketDataSnapshot.dimension == dimension_id)
            if as_of_date:
                filters.append(MarketDataSnapshot.as_of_date == as_of_date)
            total = int(
                session.scalar(select(func.count()).select_from(MarketDataSnapshot).where(*filters))
                or 0
            )
            rows = list(
                session.scalars(
                    select(MarketDataSnapshot)
                    .where(*filters)
                    .order_by(
                        MarketDataSnapshot.published_at.desc(),
                        MarketDataSnapshot.created_at.desc(),
                    )
                    .offset(page_offset)
                    .limit(page_limit)
                )
            )
            return {
                "items": [self._snapshot_payload(session, row, detailed=False) for row in rows],
                "total_count": total,
                "limit": page_limit,
                "offset": page_offset,
            }

    def get_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            row = session.get(MarketDataSnapshot, snapshot_id)
            if row is None:
                raise MarketDataServiceError(
                    "market_data_snapshot_not_found", "未找到行情快照", status_code=404
                )
            return self._snapshot_payload(session, row, detailed=True)

    def get_workspace_market_data(
        self,
        *,
        symbol: str,
        as_of_date: date | None = None,
        market: str = "",
    ) -> dict[str, Any]:
        """Read the latest immutable Bundle for the legacy finance workspace.

        This compatibility projection is intentionally DB-only. ``GET`` callers
        cannot request a refresh; acquisition belongs to collection jobs.
        """

        symbol = _symbol(symbol)
        resolved_market, exchange_timezone = _market_and_timezone(symbol, market)
        target_date = as_of_date or datetime.now(ZoneInfo(exchange_timezone)).date()
        with self.database.session() as session:
            repository = MarketDataRepository(session)
            bundle = repository.latest_bundle(
                symbol=symbol,
                market=resolved_market,
                as_of_date=target_date,
                contract_version=self.registry.contract_version,
            )
            if bundle is None:
                raise MarketDataServiceError(
                    "published_bundle_missing",
                    f"{symbol} 在 {target_date.isoformat()} 没有已发布 Bundle；请先显式采集并发布",
                    status_code=404,
                )
            try:
                materialized = BundleMaterializer(
                    repository, registry=self.registry
                ).materialize(bundle.id)
            except BundleIntegrityError as exc:
                raise MarketDataServiceError(
                    "published_bundle_invalid", "已发布 Bundle 未通过完整性校验", status_code=409
                ) from exc
            by_type = {
                str(item.get("snapshot_type") or ""): item
                for item in materialized.snapshots
            }
            by_dimension = {
                str(item.get("dimension_id") or ""): item
                for item in materialized.snapshots
            }
            def facts(key: str) -> dict[str, Any]:
                return dict((by_type.get(key) or {}).get("facts") or {})

            market_facts = facts("market")
            kline_facts = facts("kline")
            return {
                "symbol": bundle.symbol,
                "name": str(market_facts.get("name") or bundle.symbol),
                "retrieved_at": _iso(datetime.now(timezone.utc)),
                "as_of_date": bundle.as_of_date.isoformat(),
                "cutoff_at": _iso(bundle.cutoff_at),
                "quality_status": "available",
                "data_source_mode": "bundle_only",
                "bundle_id": bundle.id,
                "bundle_manifest_hash": bundle.manifest_hash,
                "contract_version": bundle.contract_version,
                "expected_dimension_count": bundle.expected_dimension_count,
                "sections": {
                    "market": market_facts,
                    "kline": kline_facts,
                    "fundamental": facts("fundamental"),
                    "capital_flow": facts("capital_flow"),
                    "sentiment": facts("sentiment"),
                    "events": facts("events"),
                    "lhb": facts("lhb"),
                    "dimensions": {
                        key: dict(value.get("facts") or {}) for key, value in by_dimension.items()
                    },
                },
                "snapshots": materialized.snapshots,
                "source_refs": materialized.source_refs,
            }

    # Explicit collection jobs --------------------------------------------------------
    def resolve_instrument(
        self,
        *,
        query: str,
        asset_family: str = "auto",
        allow_network: bool = False,
    ) -> dict[str, Any]:
        declared = _asset_type_for_family(asset_family)
        resolver_kwargs = {
            "asset_type": declared,
            "fund_type": "fund" if asset_family == "fund" else None,
            "query": query,
        }
        try:
            identities = self.identity_resolver.resolve_candidates(
                query,
                allow_network=allow_network,
                **resolver_kwargs,
            )
        except SecurityIdentityError as exc:
            raise MarketDataServiceError(exc.code, exc.message, status_code=422) from exc
        rows = [identity.model_dump(mode="json") for identity in identities]
        ambiguous = len(rows) != 1
        return {
            "query": str(query or "").strip(),
            "candidates": rows,
            "resolved": None if ambiguous else rows[0],
            "ambiguous": ambiguous,
        }

    def create_collection_job(
        self,
        *,
        tenant_id: str,
        user_id: str,
        actor_id: str,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        try:
            asset_family = str(request.get("asset_family") or "auto").strip().lower()
            identities = [
                self._resolve_collection_identity(symbol, asset_family=asset_family)
                for symbol in request["symbols"]
            ]
            resolved_families = {
                "fund" if identity.is_fund else "equity" for identity in identities
            }
            if len(resolved_families) != 1:
                raise MarketDataServiceError(
                    "mixed_asset_collection_unsupported",
                    "一次采集任务不能混合基金与股票，请分别创建任务",
                    status_code=422,
                )
            resolved_family = next(iter(resolved_families))
            if asset_family not in {"auto", resolved_family}:
                raise MarketDataServiceError(
                    "asset_type_mismatch",
                    f"请求声明为 {asset_family}，但标的解析为 {resolved_family}",
                    status_code=422,
                )
            start_date = _date(request["start_date"])
            end_date = _date(request["end_date"])
            requested_concurrency = request.get("max_concurrency")
            concurrency = int(
                getattr(self.settings, "market_data_collection_concurrency", 5)
                if requested_concurrency is None
                else requested_concurrency
            )
            if not 1 <= concurrency <= 5:
                raise MarketDataServiceError(
                    "collection_concurrency_invalid",
                    "采集并发必须在 1 到 5 之间",
                    status_code=422,
                )
            if resolved_family == "fund":
                if len(identities) != 1:
                    raise MarketDataServiceError(
                        "fund_collection_single_parent_required",
                        "每个基金持仓任务只能指定一只父基金",
                        status_code=422,
                    )
                if start_date != end_date:
                    raise MarketDataServiceError(
                        "fund_collection_single_date_required",
                        "基金持仓任务按日冻结，每次只能指定同一天",
                        status_code=422,
                    )
                if int(request.get("top_n") or 10) != 10:
                    raise MarketDataServiceError(
                        "fund_top_n_must_be_ten",
                        "基金入口固定分析前十大持仓",
                        status_code=422,
                    )
                requested_dimensions = request.get("dimensions", "all")
                if requested_dimensions != "all" and list(requested_dimensions or []) != [
                    "fund_top_holdings"
                ]:
                    raise MarketDataServiceError(
                        "fund_equity_contract_partial",
                        "基金十大持仓分析固定采集每只股票的完整 22 槽合同",
                        status_code=422,
                    )
                return self.fund_collection.create_job(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    actor_id=actor_id,
                    fund_code=identities[0].code,
                    as_of_date=start_date,
                    equity_dimensions="all",
                    mode=str(request.get("mode") or "missing_or_stale"),
                    max_concurrency=concurrency,
                )
            return self.collection.create_job(
                tenant_id=tenant_id,
                user_id=user_id,
                actor_id=actor_id,
                symbols=request["symbols"],
                start_date=start_date,
                end_date=end_date,
                dimensions=request.get("dimensions", "all"),
                mode=str(request.get("mode") or "missing_or_stale"),
                max_concurrency=concurrency,
                publish_when_complete=bool(request.get("publish_when_complete", True)),
            )
        except CollectionJobError as exc:
            raise MarketDataServiceError(exc.code, exc.message, status_code=exc.status_code) from exc

    def list_collection_jobs(self, **kwargs: Any) -> dict[str, Any]:
        tenant_id = str(kwargs["tenant_id"])
        user_id = str(kwargs["user_id"])
        page_limit = max(1, min(int(kwargs.get("limit", 50)), 200))
        page_offset = max(0, int(kwargs.get("offset", 0)))
        requested_symbol = str(kwargs.get("symbol") or "").strip().upper()
        asset_family = str(kwargs.get("asset_family") or "").strip().lower()
        as_of_date = kwargs.get("as_of_date")
        with self.database.session() as session:
            filters = [
                MarketDataCollectionJob.tenant_id == tenant_id,
                MarketDataCollectionJob.user_id == user_id,
            ]
            if kwargs.get("status"):
                filters.append(MarketDataCollectionJob.status == str(kwargs["status"]))
            if as_of_date is not None:
                resolved_date = _date(as_of_date)
                filters.extend(
                    (
                        MarketDataCollectionJob.start_date <= resolved_date,
                        MarketDataCollectionJob.end_date >= resolved_date,
                    )
                )
            if asset_family == "fund":
                filters.extend(
                    (
                        MarketDataCollectionJob.job_type == FUND_HOLDING_JOB_TYPE,
                        MarketDataCollectionJob.parent_job_id.is_(None),
                    )
                )
            elif asset_family == "equity":
                filters.append(
                    MarketDataCollectionJob.job_type.in_(EQUITY_COLLECTION_JOB_TYPES)
                )

            primary_rows = list(
                session.scalars(
                    select(MarketDataCollectionJob)
                    .where(*filters)
                    .order_by(MarketDataCollectionJob.created_at.desc())
                )
            )
            if requested_symbol:
                primary_rows = [
                    row for row in primary_rows if _job_contains_symbol(row, requested_symbol)
                ]
            total = len(primary_rows)
            primary_rows = primary_rows[page_offset : page_offset + page_limit]

            rows = list(primary_rows)
            if asset_family == "fund" and primary_rows:
                parent_ids = [row.id for row in primary_rows]
                rows.extend(
                    list(
                        session.scalars(
                            select(MarketDataCollectionJob)
                            .where(
                                MarketDataCollectionJob.tenant_id == tenant_id,
                                MarketDataCollectionJob.user_id == user_id,
                                MarketDataCollectionJob.parent_job_id.in_(parent_ids),
                                MarketDataCollectionJob.job_type.in_(
                                    EQUITY_COLLECTION_JOB_TYPES
                                ),
                            )
                            .order_by(MarketDataCollectionJob.created_at.desc())
                        )
                    )
                )

            row_ids = [row.id for row in rows]
            tasks = (
                list(
                    session.scalars(
                        select(MarketDataCollectionTask)
                        .where(MarketDataCollectionTask.job_id.in_(row_ids))
                        .order_by(
                            MarketDataCollectionTask.job_id,
                            MarketDataCollectionTask.symbol,
                            MarketDataCollectionTask.dimension,
                        )
                    )
                )
                if row_ids
                else []
            )
            tasks_by_job: dict[str, list[MarketDataCollectionTask]] = {}
            for task in tasks:
                tasks_by_job.setdefault(task.job_id, []).append(task)
            task_job = {task.id: task.job_id for task in tasks}
            result_rows = (
                list(
                    session.scalars(
                        select(MarketDataCollectionTaskResult)
                        .where(
                            MarketDataCollectionTaskResult.task_id.in_(
                                list(task_job)
                            )
                        )
                        .order_by(
                            MarketDataCollectionTaskResult.task_id,
                            MarketDataCollectionTaskResult.as_of_date,
                            MarketDataCollectionTaskResult.attempt.desc(),
                        )
                    )
                )
                if task_job
                else []
            )
            latest_results: dict[
                tuple[str, date], MarketDataCollectionTaskResult
            ] = {}
            for result in result_rows:
                latest_results.setdefault((result.task_id, result.as_of_date), result)
            resolution_by_job: dict[str, dict[str, int]] = {}
            for result in latest_results.values():
                job_id = task_job.get(result.task_id)
                if not job_id:
                    continue
                bucket = resolution_by_job.setdefault(job_id, {})
                bucket[result.status] = bucket.get(result.status, 0) + 1
            children_by_parent: dict[str, list[MarketDataCollectionJob]] = {}
            for row in rows:
                if row.parent_job_id:
                    children_by_parent.setdefault(row.parent_job_id, []).append(row)

            items = [
                self._collection_job_summary_payload(
                    row,
                    tasks=tasks_by_job.get(row.id, []),
                    resolution_counts=resolution_by_job.get(row.id, {}),
                    child_rows=children_by_parent.get(row.id, []),
                    tasks_by_job=tasks_by_job,
                )
                for row in rows
            ]
        return {
            "items": items,
            "total_count": total,
            "limit": page_limit,
            "offset": page_offset,
        }

    def _collection_job_summary_payload(
        self,
        job: MarketDataCollectionJob,
        *,
        tasks: list[MarketDataCollectionTask],
        resolution_counts: Mapping[str, int],
        child_rows: list[MarketDataCollectionJob],
        tasks_by_job: Mapping[str, list[MarketDataCollectionTask]],
    ) -> dict[str, Any]:
        checkpoint = dict(job.checkpoint_json or {})
        compact_checkpoint = (
            {
                "parent_bundle_id": checkpoint.get("parent_bundle_id"),
                "parent_bundle_hash": checkpoint.get("parent_bundle_hash"),
            }
            if job.job_type == FUND_HOLDING_JOB_TYPE
            else {}
        )
        task_summary = summarize_tasks(tasks)
        task_summary["by_error_code"] = []
        task_summary["by_dimension"] = []
        payload = {
            "id": job.id,
            "job_id": job.id,
            "job_type": job.job_type,
            "origin": job.origin,
            "subscription_id": job.subscription_id,
            "parent_job_id": job.parent_job_id,
            "mode": job.mode,
            "status": job.status,
            "asset_family": (
                "fund" if job.job_type == FUND_HOLDING_JOB_TYPE else "equity"
            ),
            "top_n": 10 if job.job_type == FUND_HOLDING_JOB_TYPE else None,
            "symbols": list(job.symbols_json or []),
            "dimensions": list(job.dimensions_json or []),
            "start_date": job.start_date.isoformat(),
            "end_date": job.end_date.isoformat(),
            "max_concurrency": int(job.max_concurrency),
            "publish_when_complete": bool(job.publish_when_complete),
            "scoped_retry_supported": True,
            "progress": dict(job.progress_json or {}),
            "checkpoint": compact_checkpoint,
            "cancel_requested": bool(job.cancel_requested),
            "error_summary": job.error_summary,
            "created_at": _iso(job.created_at),
            "started_at": _iso(job.started_at),
            "completed_at": _iso(job.completed_at),
            "expanded_holdings": (
                list(checkpoint.get("holdings") or [])
                if job.job_type == FUND_HOLDING_JOB_TYPE
                else []
            ),
            "child_collection_job_ids": (
                list(checkpoint.get("child_job_ids") or [])
                if job.job_type == FUND_HOLDING_JOB_TYPE
                else []
            ),
            "task_summary": task_summary,
            "resolution_summary": {
                "total": sum(resolution_counts.values()),
                "strict": int(resolution_counts.get("succeeded", 0)),
                "degraded": int(resolution_counts.get("degraded", 0)),
                "skipped": int(resolution_counts.get("skipped", 0)),
                "failed": int(resolution_counts.get("failed", 0))
                + int(resolution_counts.get("data_gap", 0)),
                "by_status": dict(resolution_counts),
            },
            # List polling stays compact; GET /collection-jobs/{id} returns tasks.
            "tasks": [],
        }
        if job.job_type == FUND_HOLDING_JOB_TYPE:
            child_tasks = [
                task
                for child in child_rows
                for task in tasks_by_job.get(child.id, [])
            ]
            payload["portfolio_summary"] = summarize_fund_portfolio(
                checkpoint=checkpoint,
                child_tasks=child_tasks,
                dimensions_per_holding=len(self.registry.dimension_ids),
                top_n=10,
            )
        return payload

    def get_collection_job(
        self, job_id: str, *, tenant_id: str, user_id: str
    ) -> dict[str, Any]:
        return self._collection_job_payload(
            job_id,
            job_type=self._owned_job_type(job_id, tenant_id=tenant_id, user_id=user_id),
            tenant_id=tenant_id,
            user_id=user_id,
        )

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
        try:
            coordinator = self._coordinator_for_job(
                job_id, tenant_id=tenant_id, user_id=user_id
            )
            return coordinator.retry_failed(
                job_id,
                tenant_id=tenant_id,
                user_id=user_id,
                actor_id=actor_id,
                symbols=symbols,
                dimensions=dimensions,
            )
        except CollectionJobError as exc:
            raise MarketDataServiceError(exc.code, exc.message, status_code=exc.status_code) from exc

    def cancel_collection_job(
        self, job_id: str, *, tenant_id: str, user_id: str, actor_id: str
    ) -> dict[str, Any]:
        try:
            coordinator = self._coordinator_for_job(
                job_id, tenant_id=tenant_id, user_id=user_id
            )
            return coordinator.cancel(
                job_id, tenant_id=tenant_id, user_id=user_id, actor_id=actor_id
            )
        except CollectionJobError as exc:
            raise MarketDataServiceError(exc.code, exc.message, status_code=exc.status_code) from exc

    # Manual evidence, revocation and conflict decisions ------------------------------
    def create_manual_snapshot(
        self,
        *,
        tenant_id: str,
        user_id: str,
        actor_id: str,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        del tenant_id, user_id
        now = datetime.now(timezone.utc)
        symbol = _symbol(request.get("symbol"))
        market, exchange_timezone = _market_and_timezone(symbol, str(request.get("market") or ""))
        registry = self._registry_for_market(market)
        cutoff = _datetime(request.get("cutoff_at"), "cutoff_at")
        published_at = _datetime(request.get("published_at"), "published_at")
        if published_at > cutoff:
            raise MarketDataServiceError(
                "manual_snapshot_future_leak",
                "证据发布时间晚于所选 cutoff，不能写入该历史时点",
                status_code=409,
            )
        raw_evidence = list(request.get("evidence") or [])
        evidence: list[dict[str, Any]] = []
        for item in raw_evidence:
            if not isinstance(item, Mapping):
                raise MarketDataServiceError("manual_evidence_invalid", "人工证据格式无效")
            row = dict(item)
            row.update({"grade": "B", "is_manual": True})
            row.setdefault("immutable_proof", False)
            row.setdefault("published_at", published_at)
            row.setdefault("observed_at", now)
            evidence_published_at = _datetime(
                row.get("published_at"), "evidence.published_at"
            )
            if evidence_published_at > cutoff:
                raise MarketDataServiceError(
                    "manual_snapshot_future_leak",
                    "存在首次公开时间晚于所选 cutoff 的人工证据，不能写入该历史时点",
                    status_code=409,
                )
            row["published_at"] = evidence_published_at
            metadata = dict(row.get("metadata") or {})
            metadata.update(
                {"manual_reason": str(request.get("reason") or ""), "entered_by": actor_id}
            )
            row["metadata"] = metadata
            evidence.append(row)
        field_sources = _field_sources(request.get("field_sources"), evidence)
        try:
            ingest = SnapshotIngestInput(
                symbol=symbol,
                market=market,
                exchange_timezone=exchange_timezone,
                dimension=str(request.get("dimension_id") or ""),
                contract_version=registry.contract_version,
                as_of_date=_date(request.get("as_of_date")),
                status=str(request.get("status") or ""),
                payload=dict(request.get("payload") or {}),
                effective_at=(
                    _datetime(request.get("effective_at"), "effective_at")
                    if request.get("effective_at")
                    else published_at
                ),
                published_at=published_at,
                observed_at=now,
                ingested_at=now,
                source_version="manual-v1",
                source_priority=50,
                origin="manual",
                query_manifest=dict(request.get("query_manifest") or {}),
                validation={
                    "manual_reason": str(request.get("reason") or ""),
                    "entered_by": actor_id,
                    "requested_cutoff_at": cutoff.isoformat(),
                    "empty_reason": request.get("empty_reason"),
                },
                field_sources=field_sources,
                evidence=[EvidenceInput.model_validate(row) for row in evidence],
                observation_metadata={"entered_by": actor_id, "manual": True},
            )
            with self.database.session() as session:
                row = SnapshotIngestionService(
                    MarketDataRepository(session), registry=registry
                ).ingest(ingest)
                if row.status != ingest.status.value:
                    errors = list((row.validation_json or {}).get("errors") or [])
                    raise SnapshotIngestionError(
                        "; ".join(str(item) for item in errors)
                        or "manual snapshot failed strict validation"
                    )
                return self._snapshot_payload(session, row, detailed=True)
        except (ValidationError, SnapshotIngestionError, ValueError, KeyError) as exc:
            raise MarketDataServiceError(
                "manual_snapshot_validation_failed",
                f"人工快照未通过严格契约校验：{exc}",
                status_code=422,
            ) from exc

    def revoke_snapshot(
        self,
        snapshot_id: str,
        *,
        actor_id: str,
        reason: str,
        superseded_by_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            with self.database.session() as session:
                repository = MarketDataRepository(session)
                row = repository.revoke_snapshot(
                    snapshot_id,
                    reason=reason,
                    revoked_by=actor_id,
                    superseded_by_id=superseded_by_id,
                )
                return self._snapshot_payload(session, row, detailed=True)
        except KeyError as exc:
            raise MarketDataServiceError(
                "market_data_snapshot_not_found", "未找到行情快照", status_code=404
            ) from exc
        except ValueError as exc:
            raise MarketDataServiceError("snapshot_revoke_invalid", str(exc), status_code=409) from exc

    def resolve_conflict(
        self,
        conflict_id: str,
        *,
        actor_id: str,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        resolution = str(request.get("resolution") or "")
        selected = request.get("selected_snapshot_id")
        if resolution == "select_candidate" and not selected:
            raise MarketDataServiceError(
                "conflict_selection_missing", "选择候选快照时必须提供 selected_snapshot_id"
            )
        if resolution == "manual_override":
            raise MarketDataServiceError(
                "conflict_manual_snapshot_required",
                "人工覆盖必须先录入带 B 级证据的新快照，再选择该快照解决冲突",
                status_code=422,
            )
        payload = {
            "action": resolution,
            "selected_snapshot_id": selected,
            "manual_value": request.get("manual_value"),
            "reason": str(request.get("reason") or ""),
        }
        try:
            with self.database.session() as session:
                repository = MarketDataRepository(session)
                current = session.get(MarketDataConflict, conflict_id)
                if current is None:
                    raise KeyError(conflict_id)
                locked_scope = _lock_conflict_scope(session, current)
                current = next(
                    (item for item in locked_scope if item.id == conflict_id),
                    None,
                )
                if current is None:
                    raise KeyError(conflict_id)
                if resolution == "select_candidate":
                    candidate_ids = _conflict_candidate_ids(current)
                    if str(selected) not in candidate_ids:
                        raise MarketDataServiceError(
                            "conflict_candidate_invalid",
                            "selected_snapshot_id 不属于该冲突的候选集合",
                            status_code=422,
                        )
                    previous_winner = str(
                        (current.resolution_json or {}).get("selected_snapshot_id") or ""
                    )
                    if current.status != "open":
                        if previous_winner and previous_winner != str(selected):
                            raise MarketDataServiceError(
                                "conflict_winner_immutable",
                                "该事实冲突已完成整快照决议，不能反向选择另一候选",
                                status_code=409,
                            )
                        return self._conflict_payload(current)

                    candidates = _conflict_candidate_snapshots(
                        session, current, candidate_ids
                    )
                    selected_snapshot = candidates[str(selected)]
                    if selected_snapshot.revoked_at is not None:
                        raise MarketDataServiceError(
                            "conflict_candidate_revoked",
                            "不能选择已经撤销的候选快照",
                            status_code=409,
                        )
                    # A whole-snapshot decision is one atomic fact decision in
                    # v1. Resolve every open field conflict generated for the
                    # same candidate set so a later request cannot select the
                    # already-revoked loser on another field.
                    related = _related_open_conflicts(
                        locked_scope, current, candidate_ids
                    )
                    related_ids = [item.id for item in related]
                    payload["resolved_conflict_ids"] = related_ids
                    resolved_rows = repository.resolve_conflicts_atomically(
                        related_ids,
                        resolution=payload,
                        resolved_by=actor_id,
                    )
                    for candidate_id in sorted(candidate_ids - {str(selected)}):
                        repository.revoke_snapshot(
                            candidate_id,
                            reason=f"字段冲突 {conflict_id} 已选择另一候选：{request.get('reason')}",
                            revoked_by=actor_id,
                            superseded_by_id=str(selected),
                        )
                    resolved_current = next(
                        (item for item in resolved_rows if item.id == current.id),
                        None,
                    )
                    return self._conflict_payload(resolved_current or current)
                elif resolution == "mark_invalid":
                    if current.status != "open":
                        return self._conflict_payload(current)
                    invalid_snapshot_id = str(current.snapshot_id or "")
                    candidate_ids = _conflict_candidate_ids(current)
                    if not invalid_snapshot_id or invalid_snapshot_id not in candidate_ids:
                        raise MarketDataServiceError(
                            "conflict_candidate_invalid",
                            "待淘汰快照不属于该冲突的候选集合",
                            status_code=422,
                        )
                    candidates = _conflict_candidate_snapshots(
                        session, current, candidate_ids
                    )
                    winner_ids = candidate_ids - {invalid_snapshot_id}
                    if len(winner_ids) != 1:
                        raise MarketDataServiceError(
                            "conflict_mark_invalid_ambiguous",
                            "整快照 mark_invalid 必须恰好保留一个候选赢家",
                            status_code=409,
                        )
                    winner_id = next(iter(winner_ids))
                    if candidates[winner_id].revoked_at is not None:
                        raise MarketDataServiceError(
                            "conflict_candidate_revoked",
                            "冲突对手快照已经撤销，不能作为 mark_invalid 后的赢家",
                            status_code=409,
                        )
                    related = _related_open_conflicts(
                        locked_scope, current, candidate_ids
                    )
                    related_ids = [item.id for item in related]
                    payload.update(
                        {
                            "selected_snapshot_id": winner_id,
                            "invalid_snapshot_id": invalid_snapshot_id,
                            "resolved_conflict_ids": related_ids,
                        }
                    )
                    resolved_rows = repository.resolve_conflicts_atomically(
                        related_ids,
                        resolution=payload,
                        resolved_by=actor_id,
                    )
                    repository.revoke_snapshot(
                        invalid_snapshot_id,
                        reason=f"字段冲突 {conflict_id} 被标记无效：{request.get('reason')}",
                        revoked_by=actor_id,
                        superseded_by_id=winner_id,
                    )
                    resolved_current = next(
                        (item for item in resolved_rows if item.id == current.id),
                        None,
                    )
                    return self._conflict_payload(resolved_current or current)
                row = repository.resolve_conflict(
                    conflict_id, resolution=payload, resolved_by=actor_id
                )
                return self._conflict_payload(row)
        except KeyError as exc:
            raise MarketDataServiceError(
                "market_data_conflict_not_found", "未找到数据冲突", status_code=404
            ) from exc
        except ValueError as exc:
            raise MarketDataServiceError(
                "conflict_resolution_invalid",
                "冲突决议与候选快照范围不一致",
                status_code=409,
            ) from exc
        except OperationalError as exc:
            raise MarketDataServiceError(
                "conflict_resolution_concurrent",
                "冲突正在被另一事务决议，请刷新后重试",
                status_code=409,
            ) from exc

    # Immutable Bundles ---------------------------------------------------------------
    def list_bundles(
        self,
        *,
        symbol: str | None = None,
        as_of_date: date | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        page_limit = max(1, min(int(limit), 200))
        page_offset = max(0, int(offset))
        with self.database.session() as session:
            filters = []
            if symbol:
                filters.append(MarketDataBundle.symbol == _symbol(symbol))
            if as_of_date:
                filters.append(MarketDataBundle.as_of_date == as_of_date)
            total = int(
                session.scalar(select(func.count()).select_from(MarketDataBundle).where(*filters))
                or 0
            )
            rows = list(
                session.scalars(
                    select(MarketDataBundle)
                    .where(*filters)
                    .order_by(MarketDataBundle.as_of_date.desc(), MarketDataBundle.revision.desc())
                    .offset(page_offset)
                    .limit(page_limit)
                )
            )
            return {
                "items": [self._bundle_payload(session, row, include_items=False) for row in rows],
                "total_count": total,
                "limit": page_limit,
                "offset": page_offset,
            }

    def get_bundle(self, bundle_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            row = session.get(MarketDataBundle, bundle_id)
            if row is None:
                raise MarketDataServiceError(
                    "market_data_bundle_not_found", "未找到数据 Bundle", status_code=404
                )
            payload = self._bundle_payload(session, row, include_items=True)
            if row.status == "published" and row.invalidated_at is None:
                try:
                    materialized = BundleMaterializer(
                        MarketDataRepository(session),
                        registry=self._registry_for_market(row.market),
                    ).materialize(row.id)
                    payload["materialized_snapshot_count"] = len(materialized.snapshots)
                    payload["integrity_verified"] = True
                except BundleIntegrityError as exc:
                    payload["integrity_verified"] = False
                    payload["integrity_error"] = str(exc)
            return payload

    def publish_bundle(self, *, actor_id: str, request: Mapping[str, Any]) -> dict[str, Any]:
        symbol = _symbol(request.get("symbol"))
        market, exchange_timezone = _market_and_timezone(symbol, str(request.get("market") or ""))
        registry = self._registry_for_market(market)
        try:
            with self.database.session() as session:
                row = BundlePublisher(
                    MarketDataRepository(session), registry=registry
                ).publish(
                    symbol=symbol,
                    market=market,
                    as_of_date=_date(request.get("as_of_date")),
                    cutoff_at=(
                        _datetime(request.get("cutoff_at"), "cutoff_at")
                        if request.get("cutoff_at")
                        else None
                    ),
                    exchange_timezone=exchange_timezone,
                    published_by=actor_id,
                )
                return self._bundle_payload(session, row, include_items=True)
        except IncompleteBundleError as exc:
            raise MarketDataServiceError(
                "market_data_bundle_incomplete",
                "22 个 UZI 采集槽尚未形成完整日级快照，不能发布 Bundle",
                status_code=409,
                details={
                    "missing_dimensions": exc.missing_dimensions,
                    "blocked_dimensions": exc.blocked_dimensions,
                    "coverage": exc.coverage.model_dump(mode="json"),
                },
            ) from exc
        except (BundleIntegrityError, ValueError) as exc:
            raise MarketDataServiceError(
                "market_data_bundle_invalid", str(exc), status_code=409
            ) from exc

    # Subscriptions and provider capability -------------------------------------------
    def list_subscriptions(self, **kwargs: Any) -> dict[str, Any]:
        return self.subscriptions.list(**kwargs)

    def upsert_subscription(
        self,
        *,
        symbol: str,
        tenant_id: str,
        user_id: str,
        actor_id: str,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        try:
            resolved_market, exchange_timezone = _market_and_timezone(
                symbol, str(request.get("market") or "")
            )
            requested_timezone = str(request.get("timezone") or "").strip()
            if requested_timezone and requested_timezone != exchange_timezone:
                raise MarketDataServiceError(
                    "subscription_timezone_market_mismatch",
                    f"{resolved_market} 必须使用交易所时区 {exchange_timezone}",
                    status_code=422,
                )
            subscription_service = (
                self.fund_subscriptions
                if resolved_market == FUND_MARKET
                else self.subscriptions
            )
            if resolved_market == FUND_MARKET:
                identity = self._resolve_collection_identity(symbol, asset_family="fund")
                symbol = identity.code
            return subscription_service.upsert(
                tenant_id=tenant_id,
                user_id=user_id,
                actor_id=actor_id,
                symbol=symbol,
                market=resolved_market,
                exchange_timezone=exchange_timezone,
                enabled=bool(request.get("enabled", False)),
                dimensions=request.get("dimensions", "all"),
                local_time=str(request.get("local_time") or "23:15"),
                max_concurrency=(
                    getattr(self.settings, "market_data_collection_concurrency", 5)
                    if request.get("max_concurrency") is None
                    else int(request["max_concurrency"])
                ),
            )
        except SubscriptionError as exc:
            raise MarketDataServiceError(exc.code, exc.message, status_code=exc.status_code) from exc

    def list_providers(self) -> dict[str, Any]:
        items = [self._provider_payload(row) for row in self.providers.list()]
        return {"items": items, "total_count": len(items), "limit": len(items), "offset": 0}

    def test_provider(self, provider_id: str) -> dict[str, Any]:
        payload = self.providers.test_configuration(provider_id)
        if payload.get("status") == "unknown_provider":
            raise MarketDataServiceError(
                "market_data_provider_not_found", "未登记该数据源", status_code=404
            )
        return self._provider_payload(payload)

    # Report manifest -----------------------------------------------------------------
    def create_report_manifest(
        self,
        *,
        tenant_id: str,
        user_id: str,
        actor_id: str,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        symbols = [_symbol(item) for item in request.get("symbols") or []]
        requested_report_kind = str(
            request.get("report_kind") or "single_stock"
        ).strip()
        context = {
            "report_kind": requested_report_kind,
            "markets": dict(request.get("market_by_symbol") or {}),
            "weights": dict(request.get("weights") or {}),
            "requested_by": actor_id,
            "requested_evaluation_at": (
                _iso(_datetime(request.get("evaluation_at"), "evaluation_at"))
                if request.get("evaluation_at")
                else None
            ),
        }
        asset_family = str(request.get("asset_family") or "equity").strip().lower()
        if asset_family == "fund":
            if requested_report_kind != "fund_top_holdings":
                raise MarketDataServiceError(
                    "fund_report_kind_mismatch",
                    "基金持仓入口必须使用 fund_top_holdings 报告类型",
                    status_code=422,
                )
            symbols, fund_context = self._fund_report_inputs(
                fund_symbols=symbols,
                fund_bundle_id=str(request.get("fund_bundle_id") or "").strip(),
                as_of_date=_date(request.get("as_of_date")),
            )
            context.update(fund_context)
        elif asset_family != "equity":
            raise MarketDataServiceError(
                "report_asset_family_invalid",
                "报告 asset_family 必须是 equity 或 fund",
                status_code=422,
            )
        elif requested_report_kind == "fund_top_holdings":
            raise MarketDataServiceError(
                "fund_report_dependency_required",
                "fund_top_holdings 必须通过基金入口并钉住父基金 Bundle",
                status_code=422,
            )
        try:
            return dict(
                self.report_gateway.freeze_report_manifest(
                    symbols=symbols,
                    as_of_date=_date(request.get("as_of_date")),
                    tenant_id=tenant_id,
                    user_id=user_id,
                    workflow_pins=dict(request.get("workflow_pins") or {}),
                    context=context,
                )
            )
        except ReportInputError as exc:
            raise MarketDataServiceError(exc.code, str(exc), status_code=409) from exc

    def _fund_report_inputs(
        self,
        *,
        fund_symbols: list[str],
        fund_bundle_id: str,
        as_of_date: date,
    ) -> tuple[list[str], dict[str, Any]]:
        if len(fund_symbols) != 1:
            raise MarketDataServiceError(
                "fund_report_single_parent_required",
                "基金十大持仓报告必须指定一只父基金",
                status_code=422,
            )
        if not fund_bundle_id:
            raise MarketDataServiceError(
                "fund_holding_bundle_required",
                "基金报告必须钉住已发布的基金持仓 Bundle",
                status_code=409,
            )
        with self.database.session() as session:
            repository = MarketDataRepository(session)
            bundle = repository.get_bundle(fund_bundle_id)
            if (
                bundle is None
                or bundle.status != "published"
                or bundle.invalidated_at is not None
            ):
                raise MarketDataServiceError(
                    "fund_holding_bundle_missing",
                    "基金持仓 Bundle 不存在或已经失效",
                    status_code=409,
                )
            if (
                bundle.market != FUND_MARKET
                or bundle.as_of_date != as_of_date
                or _symbol(bundle.symbol) != _symbol(fund_symbols[0]).split(".", 1)[0]
            ):
                raise MarketDataServiceError(
                    "fund_holding_bundle_scope_mismatch",
                    "基金持仓 Bundle 与基金代码或报告日期不一致",
                    status_code=409,
                )
            try:
                materialized = BundleMaterializer(
                    repository,
                    registry=self.fund_registry,
                ).materialize(bundle.id)
                payload = validate_fund_top_holdings_payload(
                    dict(materialized.snapshots[0].get("facts") or {})
                )
            except (BundleIntegrityError, ValueError, IndexError) as exc:
                raise MarketDataServiceError(
                    "fund_holding_bundle_invalid",
                    "基金持仓 Bundle 未通过 PIT 与十大持仓完整性校验",
                    status_code=409,
                ) from exc
            items = repository.list_bundle_items(bundle.id)
            if len(items) != 1:
                raise MarketDataServiceError(
                    "fund_holding_bundle_invalid",
                    "基金持仓 Bundle 槽位数不正确",
                    status_code=409,
                )
            dependency = {
                "dependency_kind": "fund_top_holdings",
                "bundle_id": bundle.id,
                "bundle_manifest_hash": bundle.manifest_hash,
                "snapshot_id": items[0].snapshot_id,
                "snapshot_content_hash": items[0].snapshot_content_hash,
            }
        holding_rows = [item.model_dump(mode="json") for item in payload.holdings]
        stock_symbols = [str(item["symbol"]) for item in holding_rows]
        return stock_symbols, {
            "report_kind": "fund_top_holdings",
            "markets": {symbol: "CN_EQUITY" for symbol in stock_symbols},
            "weights": {
                str(item["symbol"]): float(item["fund_weight_pct"])
                for item in holding_rows
            },
            "item_context": {
                str(item["symbol"]): {
                    "fund_code": payload.fund_code,
                    "fund_name": payload.fund_name,
                }
                for item in holding_rows
            },
            "bundle_dependencies": [dependency],
        }

    def start(self) -> None:
        # Resumes only previously persisted explicit work; it never creates a
        # new job unless both the global switch and a subscription are enabled.
        self.collection.resume_pending(
            resume_subscription_jobs=bool(
                getattr(self.settings, "market_data_auto_collection_enabled", False)
            )
        )
        self.fund_collection.resume_pending(
            resume_subscription_jobs=bool(
                getattr(self.settings, "market_data_auto_collection_enabled", False)
            )
        )
        self.subscription_scheduler.start()
        self.fund_subscription_scheduler.start()

    def close(self) -> None:
        self.subscription_scheduler.stop()
        self.fund_subscription_scheduler.stop()
        self.fund_collection.close()
        self.collection.close()

    def _registry_for_market(self, market: str) -> DimensionRegistry:
        normalized = str(market or "CN_EQUITY").strip().upper()
        return self.fund_registry if normalized == FUND_MARKET else self.registry

    def _resolve_collection_identity(self, symbol: str, *, asset_family: str):
        declared = _asset_type_for_family(asset_family)
        try:
            resolver_kwargs = {
                "asset_type": declared,
                "fund_type": "fund" if asset_family == "fund" else None,
                "query": symbol,
            }
            identities = self.identity_resolver.resolve_candidates(
                symbol,
                # Creating a job is the explicit acquisition boundary. Auto
                # mode must consult the fund catalog before choosing a contract;
                # declared equity/fund identities remain fully deterministic.
                allow_network=asset_family == "auto",
                **resolver_kwargs,
            )
        except SecurityIdentityError as exc:
            raise MarketDataServiceError(exc.code, exc.message, status_code=422) from exc
        if len(identities) != 1:
            raise MarketDataServiceError(
                "security_identity_ambiguous",
                f"证券代码 {symbol} 同时匹配股票与基金；请显式指定 asset_family",
                status_code=422,
                details={
                    "candidates": [
                        identity.model_dump(mode="json") for identity in identities
                    ]
                },
            )
        return identities[0]

    def _owned_job_type(self, job_id: str, *, tenant_id: str, user_id: str) -> str:
        with self.database.session() as session:
            row = session.get(MarketDataCollectionJob, job_id)
            if row is None or row.tenant_id != tenant_id or row.user_id != user_id:
                raise MarketDataServiceError(
                    "collection_job_not_found", "未找到采集任务", status_code=404
                )
            return row.job_type

    def _coordinator_for_job(self, job_id: str, *, tenant_id: str, user_id: str):
        job_type = self._owned_job_type(job_id, tenant_id=tenant_id, user_id=user_id)
        if job_type == FUND_HOLDING_JOB_TYPE:
            return self.fund_collection
        if job_type in EQUITY_COLLECTION_JOB_TYPES:
            return self.collection
        raise MarketDataServiceError(
            "collection_job_type_unsupported",
            f"不支持的采集任务类型：{job_type}",
            status_code=409,
        )

    def _collection_job_payload(
        self,
        job_id: str,
        *,
        job_type: str,
        tenant_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        try:
            coordinator = (
                self.fund_collection
                if job_type == FUND_HOLDING_JOB_TYPE
                else self.collection
            )
            payload = dict(
                coordinator.get_job(job_id, tenant_id=tenant_id, user_id=user_id)
            )
        except CollectionJobError as exc:
            raise MarketDataServiceError(exc.code, exc.message, status_code=exc.status_code) from exc
        if job_type in EQUITY_COLLECTION_JOB_TYPES:
            payload.setdefault("asset_family", "equity")
            payload.setdefault("top_n", None)
            payload.setdefault("expanded_holdings", [])
            payload.setdefault("child_collection_job_ids", [])
        payload.setdefault("scoped_retry_supported", True)
        return payload

    # Serializers ---------------------------------------------------------------------
    def _snapshot_payload(
        self, session, row: MarketDataSnapshot, *, detailed: bool
    ) -> dict[str, Any]:
        evidence_rows = []
        conflicts = []
        if detailed:
            associations = list(
                session.scalars(
                    select(MarketDataSnapshotEvidence).where(
                        MarketDataSnapshotEvidence.snapshot_id == row.id
                    )
                )
            )
            repository = MarketDataRepository(session)
            for association in associations:
                evidence = repository.get_evidence(association.evidence_id)
                if evidence is None:
                    continue
                evidence_rows.append(
                    {
                        "evidence_id": evidence.id,
                        "provider": evidence.provider,
                        "source_url": evidence.source_url,
                        "source_kind": evidence.source_kind,
                        "title": evidence.title,
                        "mime_type": evidence.mime_type,
                        "content_hash": evidence.content_hash,
                        "content_ref": evidence.content_ref,
                        "grade": evidence.grade,
                        "immutable_proof": evidence.immutable_proof,
                        "is_manual": evidence.is_manual,
                        "published_at": _iso(evidence.published_at),
                        "observed_at": _iso(evidence.observed_at),
                        "field_paths": [association.field_path],
                        "purpose": association.purpose,
                        "metadata": dict(evidence.metadata_json or {}),
                    }
                )
            conflict_rows = list(
                session.scalars(
                    select(MarketDataConflict)
                    .where(
                        MarketDataConflict.symbol == row.symbol,
                        MarketDataConflict.market == row.market,
                        MarketDataConflict.dimension == row.dimension,
                        MarketDataConflict.as_of_date == row.as_of_date,
                    )
                    .order_by(MarketDataConflict.created_at)
                )
            )
            conflicts = [self._conflict_payload(item) for item in conflict_rows]
        payload: dict[str, Any] = {
            "id": row.id,
            "snapshot_id": row.id,
            "symbol": row.symbol,
            "market": row.market,
            "dimension": row.dimension,
            "dimension_id": row.dimension,
            "as_of_date": row.as_of_date.isoformat(),
            "status": "revoked" if row.revoked_at is not None else row.status,
            "evidence_grade": row.evidence_grade,
            "content_hash": row.content_hash,
            "effective_at": _iso(row.effective_at),
            "published_at": _iso(row.published_at),
            "observed_at": _iso(row.observed_at),
            "ingested_at": _iso(row.ingested_at),
            "revoked_at": _iso(row.revoked_at),
            "revoke_reason": row.revoke_reason,
            "superseded_by_id": row.superseded_by_id,
            "origin": row.origin,
            "source_provider": evidence_rows[0]["provider"] if evidence_rows else None,
        }
        if detailed:
            payload.update(
                {
                    "contract_version": row.contract_version,
                    "payload": dict(row.payload_json or {}),
                    "validation": dict(row.validation_json or {}),
                    "query_manifest": dict(row.query_manifest_json or {}),
                    "field_provenance": dict(row.field_sources_json or {}),
                    "evidence": evidence_rows,
                    "conflicts": conflicts,
                }
            )
        return payload

    def _bundle_payload(self, session, row: MarketDataBundle, *, include_items: bool) -> dict[str, Any]:
        report_references = int(
            session.scalar(
                select(func.count())
                .select_from(ReportDataManifestItem)
                .where(ReportDataManifestItem.bundle_id == row.id)
            )
            or 0
        )
        report_references += int(
            session.scalar(
                select(func.count())
                .select_from(ReportDataManifestDependency)
                .where(ReportDataManifestDependency.bundle_id == row.id)
            )
            or 0
        )
        payload: dict[str, Any] = {
            "id": row.id,
            "bundle_id": row.id,
            "symbol": row.symbol,
            "market": row.market,
            "exchange_timezone": row.exchange_timezone,
            "as_of_date": row.as_of_date.isoformat(),
            "cutoff_at": _iso(row.cutoff_at),
            "contract_version": row.contract_version,
            "status": row.status,
            "revision": row.revision,
            "expected_dimension_count": row.expected_dimension_count,
            "ready_dimension_count": row.ready_dimension_count,
            "available_count": row.available_count,
            "verified_empty_count": row.verified_empty_count,
            "manifest_hash": row.manifest_hash,
            "supersedes_bundle_id": row.supersedes_bundle_id,
            "published_at": _iso(row.published_at),
            "invalidated_at": _iso(row.invalidated_at),
            "invalidation_reason": row.invalidation_reason,
            "report_reference_count": report_references,
            **dict(row.coverage_json or {}),
        }
        if include_items:
            items = list(
                session.scalars(
                    select(MarketDataBundleItem)
                    .where(MarketDataBundleItem.bundle_id == row.id)
                    .order_by(MarketDataBundleItem.ordinal)
                )
            )
            payload["items"] = [
                {
                    "dimension": item.dimension,
                    "dimension_id": item.dimension,
                    "ordinal": item.ordinal,
                    "snapshot_id": item.snapshot_id,
                    "content_hash": item.snapshot_content_hash,
                    "status": item.snapshot_status,
                    "evidence_grade": item.evidence_grade,
                }
                for item in items
            ]
        return payload

    @staticmethod
    def _provider_payload(row: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        configured = bool(payload.get("configured"))
        payload.setdefault(
            "label",
            payload.get("display_name")
            or payload.get("vendor_name")
            or payload.get("provider_id"),
        )
        payload["installed"] = bool(payload.get("dependency_installed", True))
        payload["healthy"] = payload.get("ok") if "ok" in payload else None
        payload.setdefault("capabilities", payload.get("dims") or payload.get("domains") or [])
        missing = []
        if not payload.get("dependency_installed", True):
            missing.append(str(payload.get("dependency") or "dependency"))
        if not payload.get("credential_configured", True):
            missing.append("credential")
        if not payload.get("policy_enabled", True):
            missing.append("explicit policy authorization")
        payload["configured"] = configured
        payload["missing_requirements"] = missing
        return payload

    @staticmethod
    def _conflict_payload(row: MarketDataConflict) -> dict[str, Any]:
        resolution = dict(row.resolution_json or {})
        return {
            "id": row.id,
            "conflict_id": row.id,
            "snapshot_id": row.snapshot_id,
            "symbol": row.symbol,
            "market": row.market,
            "dimension_id": row.dimension,
            "as_of_date": row.as_of_date.isoformat(),
            "cutoff_at": _iso(row.cutoff_at),
            "field_path": row.field_path,
            "status": row.status,
            "candidates": list(row.candidate_values_json or []),
            "candidate_evidence": list(row.candidate_evidence_json or []),
            "resolution": resolution,
            "resolved_candidate_id": resolution.get("selected_snapshot_id"),
            "resolution_reason": resolution.get("reason"),
            "resolved_by": row.resolved_by,
            "resolved_at": _iso(row.resolved_at),
        }


def _symbol(value: Any) -> str:
    symbol = str(value or "").strip().upper()
    if not symbol:
        raise MarketDataServiceError("market_data_symbol_missing", "缺少证券代码")
    return symbol


def _job_contains_symbol(job: MarketDataCollectionJob, requested_symbol: str) -> bool:
    requested = str(requested_symbol or "").strip().upper()
    requested_base = requested.split(".", 1)[0]
    for raw_symbol in list(job.symbols_json or []):
        candidate = str(raw_symbol or "").strip().upper()
        if candidate == requested or candidate.split(".", 1)[0] == requested_base:
            return True
    return False


def _normalize_market(value: str, symbol: str) -> str:
    normalized = str(value or "").strip().upper()
    aliases = {
        "CN": "CN_EQUITY",
        "A": "CN_EQUITY",
        "A_SHARE": "CN_EQUITY",
        "FUND": "CN_FUND",
        "CN_FUND": "CN_FUND",
        "HK": "HK_EQUITY",
        "US": "US_EQUITY",
    }
    if normalized in aliases:
        return aliases[normalized]
    if normalized:
        return normalized
    return market_scope(symbol)[0]


def _market_and_timezone(symbol: str, market: str) -> tuple[str, str]:
    _, inferred_timezone = market_scope(symbol)
    resolved_market = _normalize_market(market, symbol)
    timezone_by_market = {
        "CN_EQUITY": "Asia/Shanghai",
        "HK_EQUITY": "Asia/Hong_Kong",
        "US_EQUITY": "America/New_York",
        "CN_FUND": "Asia/Shanghai",
    }
    return resolved_market, timezone_by_market.get(resolved_market, inferred_timezone)


def _asset_type_for_family(value: str) -> AssetType | None:
    normalized = str(value or "auto").strip().lower()
    if normalized == "auto":
        return None
    if normalized == "equity":
        return AssetType.STOCK
    if normalized == "fund":
        # A family can contain both exchange and OTC funds; fund_type supplies
        # the namespace hint while the resolver determines the exact subtype.
        return None
    raise MarketDataServiceError(
        "asset_family_invalid",
        "asset_family 必须是 auto、equity 或 fund",
        status_code=422,
    )


def _date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value or ""))
    except ValueError as exc:
        raise MarketDataServiceError("market_data_date_invalid", "日期格式必须为 YYYY-MM-DD") from exc


def _datetime(value: Any, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except ValueError as exc:
            raise MarketDataServiceError(
                "market_data_datetime_invalid", f"{field_name} 必须为带时区的 ISO 时间"
            ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketDataServiceError(
            "market_data_datetime_naive", f"{field_name} 必须包含时区"
        )
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _database_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _conflict_candidate_ids(row: MarketDataConflict) -> set[str]:
    return {
        str(item.get("candidate_id") or "")
        for item in (row.candidate_values_json or [])
        if isinstance(item, Mapping) and str(item.get("candidate_id") or "")
    }


def _conflict_candidate_snapshots(
    session,
    conflict: MarketDataConflict,
    candidate_ids: set[str],
) -> dict[str, MarketDataSnapshot]:
    rows = list(
        session.scalars(
            select(MarketDataSnapshot)
            .where(MarketDataSnapshot.id.in_(sorted(candidate_ids)))
            .order_by(MarketDataSnapshot.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    candidates = {row.id: row for row in rows}
    if not candidate_ids or any(row is None for row in candidates.values()):
        raise MarketDataServiceError(
            "conflict_candidate_invalid",
            "冲突候选快照不存在或已损坏",
            status_code=422,
        )
    first = next(iter(candidates.values()))
    fact_scope = (
        conflict.symbol,
        conflict.market,
        conflict.dimension,
        conflict.as_of_date,
        first.contract_version,
        first.source_priority,
        _database_utc(first.effective_at),
    )
    for candidate in candidates.values():
        candidate_scope = (
            candidate.symbol,
            candidate.market,
            candidate.dimension,
            candidate.as_of_date,
            candidate.contract_version,
            candidate.source_priority,
            _database_utc(candidate.effective_at),
        )
        if candidate_scope != fact_scope:
            raise ValueError("conflict candidates do not describe the same effective fact")
    return candidates


def _lock_conflict_scope(
    session,
    current: MarketDataConflict,
) -> list[MarketDataConflict]:
    # Lock the entire logical fact scope in a stable order before re-reading
    # status/winner. Requests entering through different field-conflict IDs
    # therefore serialize on PostgreSQL instead of revoking opposite winners.
    return list(
        session.scalars(
            select(MarketDataConflict)
            .where(
                MarketDataConflict.symbol == current.symbol,
                MarketDataConflict.market == current.market,
                MarketDataConflict.dimension == current.dimension,
                MarketDataConflict.as_of_date == current.as_of_date,
            )
            .order_by(MarketDataConflict.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )


def _related_open_conflicts(
    locked_scope: list[MarketDataConflict],
    current: MarketDataConflict,
    candidate_ids: set[str],
) -> list[MarketDataConflict]:
    return [
        item
        for item in locked_scope
        if item.status == "open"
        and item.symbol == current.symbol
        and item.market == current.market
        and item.dimension == current.dimension
        and item.as_of_date == current.as_of_date
        and _conflict_candidate_ids(item) == candidate_ids
    ]


def _field_sources(raw: Any, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    if isinstance(raw, Mapping) and raw:
        return dict(raw)
    if evidence:
        return {"$": [item.get("provider") for item in evidence if item.get("provider")]}
    return {}


__all__ = ["MarketDataCenterService", "MarketDataServiceError"]
