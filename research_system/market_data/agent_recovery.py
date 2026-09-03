from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from domain_packs.finance_ashare.tools.uzi_data_recovery import (
    recover_uzi_draft_snapshots,
)
from research_system.market_data.contracts import canonical_hash
from research_system.market_data.dimension_contracts import (
    DEFAULT_DIMENSION_REGISTRY,
    DimensionRegistry,
)
from research_system.market_data.ingestion import SnapshotIngestionService
from research_system.market_data.historical import capability_for_dimension
from research_system.market_data.contracts import cutoff_at_for_date
from research_system.market_data.models import (
    MarketDataCollectionJob,
    MarketDataCollectionTask,
    MarketDataSnapshot,
)
from research_system.market_data.repository import MarketDataRepository
from research_system.market_data.resolver import SnapshotResolver, _database_utc
from research_system.market_data.uzi_adapter import UziSnapshotAdapter
from research_system.runtime.platform.settings import Settings
from research_system.strategies.database import StrategyDatabase

SnapshotCollector = Callable[..., list[dict[str, Any]]]
_BASIC_DEPENDENT_SNAPSHOT_TYPES = frozenset(
    {"macro", "industry", "futures", "policy"}
)


class DraftAgentRecoveryError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def recover_collection_job_draft(
    database: StrategyDatabase,
    *,
    job_id: str,
    settings: Settings,
    collector: SnapshotCollector | None = None,
    max_workers: int = 5,
    registry: DimensionRegistry = DEFAULT_DIMENSION_REGISTRY,
    cancellation: Any | None = None,
) -> dict[str, Any]:
    """Append Agent-recovered current-day snapshots for one collection job.

    The immutable rows selected by a previously published Bundle are never
    changed. Historical scopes retry only source-bounded PIT providers; live
    WebSearch/browser recovery remains disabled for those scopes.
    """

    provider_recovery = (
        _recover_with_scoped_providers(
            database,
            job_id=job_id,
            registry=registry,
            collector=collector,
            max_workers=max_workers,
            cancellation=cancellation,
        )
        if collector is not None
        else {
            "attempted_dimensions": 0,
            "persisted_snapshot_count": 0,
            "persisted_snapshots": [],
            "failures": [],
        }
    )
    prepared = _load_draft_inputs(database, job_id=job_id, registry=registry)
    if not prepared["scopes"]:
        historical_attempted = int(provider_recovery.get("attempted_dimensions") or 0)
        historical_persisted = int(provider_recovery.get("persisted_snapshot_count") or 0)
        return {
            "schema_version": "uzi-3.9.4-data-center-agent-recovery-v1",
            "attempted": historical_attempted,
            "succeeded": historical_persisted,
            "failed": max(0, historical_attempted - historical_persisted),
            "persisted_snapshot_count": historical_persisted,
            "persisted_snapshots": list(provider_recovery.get("persisted_snapshots") or []),
            "skipped_historical_scopes": prepared["skipped_historical_scopes"],
            "historical_only": True,
            "disclosure": (
                "历史截面已重试可按截止日查询的结构化 provider；"
                "当前 WebSearch/MX/浏览器证据未用于回填历史"
            ),
            "coverage": [],
            "provider_recovery": provider_recovery,
            "integrity_before": None,
            "integrity_after": None,
            "stages": {
                stage: {
                    "enabled": False,
                    "attempted": 0,
                    "succeeded": 0,
                    "failed": 0,
                    "disabled_reason": "historical_point_in_time_evidence_unrecoverable",
                }
                for stage in (
                    "strict_recovery",
                    "qualitative_recovery",
                    "playwright_recovery",
                    "qualitative_agent_evidence",
                )
            },
        }

    snapshots = prepared["snapshots"]
    before_hashes = {
        _snapshot_identity(snapshot): canonical_hash(snapshot)
        for snapshot in snapshots
    }
    recovery = recover_uzi_draft_snapshots(
        snapshots,
        query="数据中心显式 Agent 补采：按 UZI 自查问题补齐真实来源",
        symbols=prepared["symbols"],
        profile="deep",
        settings=settings,
        cancellation=cancellation,
    )
    persisted = _persist_recovered_snapshots(
        database,
        job_id=job_id,
        registry=registry,
        snapshots=snapshots,
        before_hashes=before_hashes,
        task_ids=prepared["task_ids"],
        allowed_scopes=prepared["scopes"],
    )
    coverage = _coverage_after_recovery(
        database,
        registry=registry,
        scopes=prepared["scopes"],
    )
    return {
        "schema_version": "uzi-3.9.4-data-center-agent-recovery-v1",
        "attempted": int(recovery.get("attempted") or 0),
        "succeeded": int(recovery.get("succeeded") or 0),
        "failed": int(recovery.get("failed") or 0),
        "persisted_snapshot_count": len(persisted),
        "persisted_snapshots": persisted,
        "skipped_historical_scopes": prepared["skipped_historical_scopes"],
        "coverage": coverage,
        "provider_recovery": provider_recovery,
        "integrity_before": recovery.get("integrity_before"),
        "integrity_after": recovery.get("integrity_after"),
        "stages": {
            key: _compact_stage(recovery.get(key))
            for key in (
                "strict_recovery",
                "qualitative_recovery",
                "playwright_recovery",
                "qualitative_agent_evidence",
            )
        },
    }


def _recover_with_scoped_providers(
    database: StrategyDatabase,
    *,
    job_id: str,
    registry: DimensionRegistry,
    collector: SnapshotCollector,
    max_workers: int,
    cancellation: Any | None,
) -> dict[str, Any]:
    scopes = _provider_gap_scopes(database, job_id=job_id, registry=registry)
    persisted: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for scope in scopes:
        if _cancelled(cancellation):
            break
        requested_types = list(scope["snapshot_types"])
        collector_types = list(requested_types)
        if (
            "market" not in collector_types
            and _BASIC_DEPENDENT_SNAPSHOT_TYPES.intersection(collector_types)
        ):
            collector_types.insert(0, "market")
        try:
            local_today = datetime.now(timezone.utc).astimezone(
                ZoneInfo(scope["exchange_timezone"])
            ).date()
            historical = scope["as_of_date"] != local_today
            rows = collector(
                query="数据中心 Agent 补采前置：按缺口重试 UZI 真实 provider",
                symbols=[scope["symbol"]],
                snapshot_types=collector_types,
                cache_root=None,
                research_profile="deep",
                max_workers=max(1, min(int(max_workers), 5)),
                uzi_root_route=False,
                no_resume=False,
                cancellation=cancellation,
                **(
                    {
                        "as_of_date": scope["as_of_date"],
                        "cutoff_at": cutoff_at_for_date(
                            scope["as_of_date"], scope["exchange_timezone"]
                        ),
                    }
                    if historical
                    else {}
                ),
            )
        except Exception as exc:  # noqa: BLE001 - one provider wave must not hide Agent fallback
            failures.append(
                {
                    "symbol": scope["symbol"],
                    "dimensions": list(scope["dimensions"]),
                    "error_type": exc.__class__.__name__,
                }
            )
            continue
        by_type = {
            str(row.get("snapshot_type") or ""): dict(row)
            for row in rows
            if isinstance(row, Mapping)
        }
        now = datetime.now(timezone.utc)
        with database.session() as session:
            ingestion = SnapshotIngestionService(
                MarketDataRepository(session), registry=registry
            )
            adapter = UziSnapshotAdapter(registry=registry)
            for dimension, snapshot_type in zip(
                scope["dimensions"], requested_types, strict=True
            ):
                native = by_type.get(snapshot_type)
                if native is None:
                    failures.append(
                        {
                            "symbol": scope["symbol"],
                            "dimension": dimension,
                            "error_type": "collector_result_missing",
                        }
                    )
                    continue
                ingest = adapter.adapt(
                    native,
                    symbol=scope["symbol"],
                    market=scope["market"],
                    exchange_timezone=scope["exchange_timezone"],
                    as_of_date=scope["as_of_date"],
                    collection_task_id=scope["task_ids"].get(dimension),
                    provider_attempt_id=(
                        f"agent-provider-recovery:{job_id}:{now.isoformat()}"
                    ),
                )
                ingest.query_manifest["agent_recovery"] = {
                    "schema_version": "uzi-3.9.4-data-center-agent-recovery-v1",
                    "stage": "scoped_provider_retry",
                    "job_id": job_id,
                    "performed_at": now.isoformat(),
                }
                row = ingestion.ingest(ingest)
                persisted.append(
                    {
                        "symbol": scope["symbol"],
                        "dimension_id": dimension,
                        "snapshot_id": row.id,
                        "content_hash": row.content_hash,
                        "status": row.status,
                    }
                )
    return {
        "attempted_dimensions": sum(len(scope["dimensions"]) for scope in scopes),
        "persisted_snapshot_count": len(persisted),
        "persisted_snapshots": persisted,
        "failures": failures,
    }


def _provider_gap_scopes(
    database: StrategyDatabase,
    *,
    job_id: str,
    registry: DimensionRegistry,
) -> list[dict[str, Any]]:
    scopes: list[dict[str, Any]] = []
    with database.session() as session:
        job = session.get(MarketDataCollectionJob, job_id)
        if job is None:
            raise DraftAgentRecoveryError(
                "collection_job_not_found", "未找到采集任务"
            )
        tasks = list(
            session.scalars(
                select(MarketDataCollectionTask).where(
                    MarketDataCollectionTask.job_id == job.id
                )
            )
        )
        tasks_by_symbol: dict[str, list[MarketDataCollectionTask]] = {}
        for task in tasks:
            tasks_by_symbol.setdefault(task.symbol, []).append(task)
        resolver = SnapshotResolver(MarketDataRepository(session), registry=registry)
        for symbol, symbol_tasks in tasks_by_symbol.items():
            exemplar = symbol_tasks[0]
            current_local_date = datetime.now(timezone.utc).astimezone(
                ZoneInfo(exemplar.exchange_timezone)
            ).date()
            dates = sorted(
                {
                    task.start_date + timedelta(days=offset)
                    for task in symbol_tasks
                    for offset in range((task.end_date - task.start_date).days + 1)
                }
            )
            for as_of_date in dates:
                scoped_tasks = [
                    task
                    for task in symbol_tasks
                    if task.start_date <= as_of_date <= task.end_date
                    and (
                        as_of_date == current_local_date
                        or capability_for_dimension(task.dimension).can_reconstruct
                    )
                ]
                if not scoped_tasks:
                    continue
                coverage = resolver.coverage(
                    symbol=symbol,
                    market=exemplar.market,
                    as_of_date=as_of_date,
                    exchange_timezone=exemplar.exchange_timezone,
                )
                uzi_issue_dimensions = {
                    str(issue.get("dimension") or "")
                    for issue in list(coverage.uzi_integrity.get("issues") or [])
                    if isinstance(issue, dict)
                    and str(issue.get("dimension") or "") != "overall"
                }
                requested_dimensions = {task.dimension for task in scoped_tasks}
                gap_dimensions = [
                    item.dimension
                    for item in coverage.dimensions
                    if item.dimension in requested_dimensions
                    and (
                        not item.eligible
                        or item.dimension in uzi_issue_dimensions
                        or any(
                            marker.endswith("_")
                            and item.dimension.startswith(marker)
                            for marker in uzi_issue_dimensions
                        )
                    )
                ]
                if not gap_dimensions:
                    continue
                task_ids = {task.dimension: task.id for task in scoped_tasks}
                scopes.append(
                    {
                        "symbol": symbol,
                        "market": exemplar.market,
                        "exchange_timezone": exemplar.exchange_timezone,
                        "as_of_date": as_of_date,
                        "dimensions": gap_dimensions,
                        "snapshot_types": [
                            registry.get(dimension).snapshot_type
                            for dimension in gap_dimensions
                        ],
                        "task_ids": task_ids,
                    }
                )
    return scopes


def _load_draft_inputs(
    database: StrategyDatabase,
    *,
    job_id: str,
    registry: DimensionRegistry,
) -> dict[str, Any]:
    snapshots: list[dict[str, Any]] = []
    scopes: list[dict[str, Any]] = []
    skipped_historical_scopes: list[dict[str, Any]] = []
    task_ids: dict[tuple[str, str], str] = {}
    symbols: list[str] = []
    with database.session() as session:
        job = session.get(MarketDataCollectionJob, job_id)
        if job is None:
            raise DraftAgentRecoveryError(
                "collection_job_not_found", "未找到采集任务"
            )
        tasks = list(
            session.scalars(
                select(MarketDataCollectionTask).where(
                    MarketDataCollectionTask.job_id == job.id
                )
            )
        )
        resolver = SnapshotResolver(MarketDataRepository(session), registry=registry)
        seen_scopes: set[tuple[str, str, date]] = set()
        for task in tasks:
            task_ids[(task.symbol, task.dimension)] = task.id
            current_local_date = datetime.now(timezone.utc).astimezone(
                ZoneInfo(task.exchange_timezone)
            ).date()
            for offset in range((task.end_date - task.start_date).days + 1):
                as_of_date = task.start_date + timedelta(days=offset)
                scope_key = (task.symbol, task.market, as_of_date)
                if scope_key in seen_scopes:
                    continue
                seen_scopes.add(scope_key)
                scope = {
                    "symbol": task.symbol,
                    "market": task.market,
                    "exchange_timezone": task.exchange_timezone,
                    "as_of_date": as_of_date,
                }
                if as_of_date != current_local_date:
                    skipped_historical_scopes.append(
                        {**scope, "as_of_date": as_of_date.isoformat()}
                    )
                    continue
                if task.symbol not in symbols:
                    symbols.append(task.symbol)
                scopes.append(scope)
                coverage = resolver.coverage(
                    symbol=task.symbol,
                    market=task.market,
                    as_of_date=as_of_date,
                    exchange_timezone=task.exchange_timezone,
                )
                rows_by_dimension: dict[str, MarketDataSnapshot] = {}
                for item in coverage.dimensions:
                    row = (
                        session.get(MarketDataSnapshot, item.snapshot_id)
                        if item.snapshot_id
                        else None
                    )
                    if row is not None:
                        rows_by_dimension[item.dimension] = row
                for contract in registry:
                    snapshots.append(
                        _native_snapshot(
                            row=rows_by_dimension.get(contract.dimension),
                            snapshot_type=contract.snapshot_type,
                            symbol=task.symbol,
                            as_of_date=as_of_date,
                        )
                    )
    return {
        "snapshots": snapshots,
        "scopes": scopes,
        "skipped_historical_scopes": skipped_historical_scopes,
        "task_ids": task_ids,
        "symbols": symbols,
    }


def _native_snapshot(
    *,
    row: MarketDataSnapshot | None,
    snapshot_type: str,
    symbol: str,
    as_of_date: date,
) -> dict[str, Any]:
    if row is None:
        return {
            "snapshot_type": snapshot_type,
            "symbol": symbol,
            "snapshot_date": as_of_date.isoformat(),
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "quality_status": "data_gap",
            "facts": {
                "availability": "data_gap",
                "_uzi_raw_result": {
                    "ticker": symbol,
                    "data": {},
                    "source": "data_center_missing_snapshot",
                },
            },
            "source_trace": [],
        }
    payload = deepcopy(dict(row.payload_json or {}))
    raw = deepcopy(payload.get("raw") or {})
    facts = raw if isinstance(raw, dict) else {}
    native_result = payload.get("_uzi_raw_result")
    if isinstance(native_result, dict):
        facts["_uzi_raw_result"] = deepcopy(native_result)
    facts["availability"] = (
        "available" if row.status == "available" else "data_gap"
    )
    query_manifest = dict(row.query_manifest_json or {})
    return {
        "snapshot_type": snapshot_type,
        "symbol": symbol,
        "snapshot_date": as_of_date.isoformat(),
        "retrieved_at": _database_utc(row.observed_at).isoformat(),
        "quality_status": query_manifest.get("native_quality_status")
        or ("available" if row.status == "available" else "data_gap"),
        "facts": facts,
        "source": {
            "provider_id": query_manifest.get("provider") or "uzi:draft",
            "chain_position": max(int(row.source_priority) - 10, 0),
        },
        "source_trace": list(query_manifest.get("source_trace") or []),
        "degrade_reasons": list(query_manifest.get("degrade_reasons") or []),
        "missing_fields": list(query_manifest.get("missing_fields") or []),
    }


def _persist_recovered_snapshots(
    database: StrategyDatabase,
    *,
    job_id: str,
    registry: DimensionRegistry,
    snapshots: list[dict[str, Any]],
    before_hashes: dict[tuple[str, str, str], str],
    task_ids: dict[tuple[str, str], str],
    allowed_scopes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    scope_by_key = {
        (scope["symbol"], scope["as_of_date"]): scope for scope in allowed_scopes
    }
    now = datetime.now(timezone.utc)
    persisted: list[dict[str, Any]] = []
    with database.session() as session:
        ingestion = SnapshotIngestionService(
            MarketDataRepository(session), registry=registry
        )
        adapter = UziSnapshotAdapter(registry=registry)
        for snapshot in snapshots:
            identity = _snapshot_identity(snapshot)
            if canonical_hash(snapshot) == before_hashes.get(identity):
                continue
            symbol, snapshot_type, as_of_text = identity
            as_of_date = date.fromisoformat(as_of_text)
            scope = scope_by_key.get((symbol, as_of_date))
            if scope is None:
                continue
            dimension = adapter.dimension_for_snapshot_type(snapshot_type)
            task_id = task_ids.get((symbol, dimension))
            if task_id is None:
                continue
            recovered = deepcopy(snapshot)
            recovered["retrieved_at"] = now.isoformat()
            ingest = adapter.adapt(
                recovered,
                symbol=symbol,
                market=scope["market"],
                exchange_timezone=scope["exchange_timezone"],
                as_of_date=as_of_date,
                collection_task_id=task_id,
                provider_attempt_id=f"agent-recovery:{job_id}:{now.isoformat()}",
            )
            ingest.query_manifest["agent_recovery"] = {
                "schema_version": "uzi-3.9.4-data-center-agent-recovery-v1",
                "job_id": job_id,
                "performed_at": now.isoformat(),
            }
            row = ingestion.ingest(ingest)
            persisted.append(
                {
                    "symbol": symbol,
                    "dimension_id": dimension,
                    "as_of_date": as_of_date.isoformat(),
                    "snapshot_id": row.id,
                    "content_hash": row.content_hash,
                    "status": row.status,
                }
            )
    return persisted


def _coverage_after_recovery(
    database: StrategyDatabase,
    *,
    registry: DimensionRegistry,
    scopes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    with database.session() as session:
        resolver = SnapshotResolver(MarketDataRepository(session), registry=registry)
        for scope in scopes:
            coverage = resolver.coverage(
                symbol=scope["symbol"],
                market=scope["market"],
                as_of_date=scope["as_of_date"],
                exchange_timezone=scope["exchange_timezone"],
            )
            rows.append(
                {
                    "symbol": scope["symbol"],
                    "as_of_date": scope["as_of_date"].isoformat(),
                    "report_ready": coverage.report_ready,
                    "report_generatable": coverage.report_generatable,
                    "quality": coverage.quality,
                    "gap_count": coverage.gap_count,
                    "missing_dimensions": [
                        item.dimension
                        for item in coverage.dimensions
                        if item.snapshot_id is None or not item.eligible
                    ],
                }
            )
    return rows


def _snapshot_identity(snapshot: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(snapshot.get("symbol") or "").strip().upper(),
        str(snapshot.get("snapshot_type") or "").strip(),
        str(snapshot.get("snapshot_date") or "").strip(),
    )


def _compact_stage(value: Any) -> dict[str, Any]:
    stage = dict(value or {}) if isinstance(value, dict) else {}
    return {
        key: stage.get(key)
        for key in (
            "schema_version",
            "enabled",
            "attempted",
            "succeeded",
            "failed",
            "critical_cleared",
            "exhausted",
            "exhausted_reason",
            "disabled_reason",
        )
        if key in stage
    }


def _cancelled(cancellation: Any | None) -> bool:
    return bool(
        cancellation is not None
        and callable(getattr(cancellation, "is_set", None))
        and cancellation.is_set()
    )


__all__ = [
    "DraftAgentRecoveryError",
    "recover_collection_job_draft",
]
