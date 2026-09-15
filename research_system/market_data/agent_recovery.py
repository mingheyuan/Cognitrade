from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from time import monotonic
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from domain_packs.finance_ashare.tools.uzi_data_recovery import (
    recover_uzi_draft_snapshots,
)
from research_system.market_data.bundles import native_transport_facts
from research_system.market_data.contracts import (
    EvidenceInput,
    canonical_hash,
    cutoff_at_for_date,
)
from research_system.market_data.dimension_contracts import (
    DEFAULT_DIMENSION_REGISTRY,
    DimensionRegistry,
)
from research_system.market_data.historical import capability_for_dimension
from research_system.market_data.ingestion import SnapshotIngestionService
from research_system.market_data.models import (
    MarketDataCollectionJob,
    MarketDataCollectionTask,
    MarketDataSnapshot,
)
from research_system.market_data.native_projection import project_native_result
from research_system.market_data.repository import MarketDataRepository
from research_system.market_data.research_collection import recover_research_documents
from research_system.market_data.resolver import SnapshotResolver, _database_utc
from research_system.market_data.uzi_adapter import UziSnapshotAdapter
from research_system.runtime.platform.settings import Settings
from research_system.strategies.database import StrategyDatabase

SnapshotCollector = Callable[..., list[dict[str, Any]]]
ResearchPipeline = Callable[[], Mapping[str, dict[str, Any]]]
_RESEARCH_STAGES = ("research_archive", "research_enrichment", "native_quant_auxiliary")
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
    archive_collector: Any | None = None,
    research_pipeline: ResearchPipeline | None = None,
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
        research_stages, research_summary = _recover_research_pipeline(
            database, job_id=job_id, settings=settings, registry=registry,
            cancellation=cancellation, archive_collector=archive_collector,
            research_pipeline=research_pipeline,
        )
        historical_attempted = int(provider_recovery.get("attempted_dimensions") or 0)
        historical_persisted = int(provider_recovery.get("persisted_snapshot_count") or 0)
        return {
            "schema_version": "uzi-3.9.4-data-center-agent-recovery-v1",
            "attempted": historical_attempted + research_summary["attempted"],
            "succeeded": historical_persisted + research_summary["succeeded"],
            "failed": max(0, historical_attempted - historical_persisted) + research_summary["failed"],
            "persisted_snapshot_count": historical_persisted + research_summary["persisted_snapshot_count"],
            "persisted_snapshots": [*list(provider_recovery.get("persisted_snapshots") or []), *research_summary["persisted_snapshots"]],
            "skipped_historical_scopes": prepared["skipped_historical_scopes"],
            "historical_only": True,
            "disclosure": (
                "历史截面已重试可按截止日查询的结构化 provider；"
                "研究原文按来源发现、核验版本后归档；无法证明目标日版本的当前网页未回填历史"
            ),
            "coverage": [],
            "provider_recovery": provider_recovery,
            "integrity_before": None,
            "integrity_after": None,
            "stages": {
                **research_stages,
                **{
                stage: {
                    "enabled": False,
                    "attempted": 0,
                    "succeeded": 0,
                    "failed": 0,
                    "disabled_reason": "live_source_not_pit_safe_use_research_archive",
                }
                for stage in (
                    "strict_recovery",
                    "qualitative_recovery",
                    "playwright_recovery",
                    "qualitative_agent_evidence",
                )
                },
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
        cancellation=cancellation,
        observation_store=_postfetch_observation_store(settings),
    )
    research_stages, research_summary = _recover_research_pipeline(
        database, job_id=job_id, settings=settings, registry=registry,
        cancellation=cancellation, archive_collector=archive_collector,
        research_pipeline=research_pipeline,
    )
    coverage = _coverage_after_recovery(
        database,
        registry=registry,
        scopes=prepared["scopes"],
    )
    return {
        "schema_version": "uzi-3.9.4-data-center-agent-recovery-v1",
        "attempted": int(recovery.get("attempted") or 0) + research_summary["attempted"],
        "succeeded": int(recovery.get("succeeded") or 0) + research_summary["succeeded"],
        "failed": int(recovery.get("failed") or 0) + research_summary["failed"],
        "persisted_snapshot_count": len(persisted) + research_summary["persisted_snapshot_count"],
        "persisted_snapshots": [*persisted, *research_summary["persisted_snapshots"]],
        "skipped_historical_scopes": prepared["skipped_historical_scopes"],
        "coverage": coverage,
        "provider_recovery": provider_recovery,
        "integrity_before": recovery.get("integrity_before"),
        "integrity_after": recovery.get("integrity_after"),
        "stages": {
            **research_stages,
            **{
            key: _compact_stage(recovery.get(key))
            for key in (
                "strict_recovery",
                "qualitative_recovery",
                "playwright_recovery",
                "qualitative_agent_evidence",
            )
            },
        },
    }


def _recover_research_pipeline(
    database, *, job_id, settings, registry, cancellation, archive_collector,
    research_pipeline: ResearchPipeline | None,
):
    """Use the explicit worker's deadline/fence; never construct another one.

    The compatibility path still performs its original archive operation, but
    does not silently create a model/quant source or claim those stages ran.
    """
    def not_run(reason):
        return {"status": "not_run", "reason_code": reason}

    if _cancelled(cancellation):
        stages = {key: not_run("collection_cancelled") for key in _RESEARCH_STAGES}
    elif research_pipeline is not None:
        output = research_pipeline()
        if not isinstance(output, Mapping):
            raise DraftAgentRecoveryError(
                "research_pipeline_result_invalid", "研究补证流程没有返回可核验的分阶段结果"
            )
        stages = {
            key: deepcopy(dict(output[key]))
            if isinstance(output.get(key), Mapping)
            else not_run("research_pipeline_stage_missing")
            for key in _RESEARCH_STAGES
        }
    else:
        stages = {key: not_run("research_pipeline_not_configured") for key in _RESEARCH_STAGES}
        stages["research_archive"] = recover_research_documents(
            database, job_id=job_id, settings=settings, registry=registry,
            cancellation=cancellation, archive_collector=archive_collector,
        )

    archived = stages["research_archive"]
    enriched = stages["research_enrichment"]
    revisions = [dict(row) for row in enriched.get("revisions", []) if isinstance(row, Mapping)]
    refused = len(enriched.get("rejected") or [])
    # Auxiliary inputs are not snapshots or completed research findings. Their
    # own result/status stays in stages, not in these snapshot recovery counts.
    summary = {
        "attempted": int(archived.get("attempted") or 0) + len(revisions) + refused,
        "succeeded": int(archived.get("succeeded") or 0) + len(revisions),
        "failed": int(archived.get("failed") or 0) + refused,
        "persisted_snapshot_count": int(archived.get("persisted_snapshot_count") or 0) + len(revisions),
        "persisted_snapshots": [*list(archived.get("persisted_snapshots") or []), *revisions],
    }
    return stages, summary


def _postfetch_observation_store(settings):
    root = getattr(settings, "workspace_root", None)
    if root is None:
        return None
    from pathlib import Path

    from research_system.market_data.research_archive import ResearchDocumentStore
    return ResearchDocumentStore(Path(root) / "cache" / "market_data_documents")


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
        repository = MarketDataRepository(session)
        resolver = SnapshotResolver(repository, registry=registry)
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
    requested_task_ids: Iterable[str] | None = None,
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
        query = select(MarketDataCollectionTask).where(MarketDataCollectionTask.job_id == job.id)
        if requested_task_ids is not None:
            if isinstance(requested_task_ids, (str, bytes)):
                raise DraftAgentRecoveryError("postfetch_task_scope_invalid", "补搜任务范围无效")
            requested_task_ids = frozenset(requested_task_ids)
            query = query.where(MarketDataCollectionTask.id.in_(requested_task_ids))
        tasks = list(session.scalars(query))
        if requested_task_ids is not None and {task.id for task in tasks} != requested_task_ids:
            raise DraftAgentRecoveryError("postfetch_task_scope_invalid", "补搜任务范围未获授权")
        repository = MarketDataRepository(session)
        resolver = SnapshotResolver(repository, registry=registry)
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
                    if requested_task_ids is not None and contract.dimension != "0_basic" and not any(
                        selected.symbol == task.symbol and selected.dimension == contract.dimension
                        and selected.start_date <= as_of_date <= selected.end_date for selected in tasks
                    ):
                        continue
                    row = rows_by_dimension.get(contract.dimension)
                    snapshots.append(
                        _native_snapshot(
                            row=row,
                            snapshot_type=contract.snapshot_type,
                            symbol=task.symbol,
                            as_of_date=as_of_date,
                            evidence=repository.list_snapshot_evidence(row.id) if row else [],
                            cutoff_at=cutoff_at_for_date(as_of_date, task.exchange_timezone),
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
    evidence=(),
    cutoff_at: datetime | None = None,
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
    native_result = project_native_result(row)
    facts = deepcopy(native_result.get("data") or {})
    facts["_uzi_raw_result"] = native_result
    facts["_market_data_input_snapshot"] = {"snapshot_id": row.id, "content_hash": row.content_hash}
    if isinstance(payload.get("_uzi_execution"), Mapping):
        facts["_uzi_execution"] = deepcopy(payload["_uzi_execution"])
    original_result = payload.get("_uzi_collector_original_result", payload.get("_uzi_raw_result"))
    if isinstance(original_result, dict):
        facts["_uzi_collector_original_result"] = deepcopy(original_result)
    facts.update(native_transport_facts(
        row, evidence=evidence,
        cutoff_at=cutoff_at or cutoff_at_for_date(as_of_date, row.exchange_timezone),
    ))
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
    write_fence=None,
    cancellation=None,
    deadline_at: float | None = None,
    observation_store=None,
    on_persist=None,
) -> list[dict[str, Any]]:
    scope_by_key = {
        (scope["symbol"], scope["as_of_date"]): scope for scope in allowed_scopes
    }
    now = datetime.now(timezone.utc)
    persisted: list[dict[str, Any]] = []
    with database.session() as session:
        repository = MarketDataRepository(session)
        ingestion = SnapshotIngestionService(
            repository, registry=registry
        )
        adapter = UziSnapshotAdapter(registry=registry)
        for snapshot in snapshots:
            if (cancellation is not None and cancellation.is_set()) or (
                deadline_at is not None and monotonic() >= deadline_at
            ) or (write_fence is not None and not write_fence(session)):
                raise DraftAgentRecoveryError("postfetch_worker_stopped", "补搜写入已取消、超时或租约失效")
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
            _preserve_recovery_input_provenance(repository, recovered, ingest)
            postfetch = (recovered.get("facts") or {}).get("_uzi_postfetch")
            if isinstance(postfetch, Mapping) and observation_store is not None:
                from research_system.market_data.contracts import (
                    EvidenceGrade,
                    canonical_json,
                )

                receipts = []
                for observation in postfetch.get("observations", []):
                    receipt = {key: deepcopy(value) for key, value in observation.items() if key != "response"}
                    if "response" in observation:
                        content = canonical_json(observation["response"]).encode("utf-8")
                        digest, reference = observation_store.put(content, suffix="json")
                        observed = datetime.fromisoformat(observation["observed_at"])
                        receipt.update(content_hash=digest, content_ref=reference)
                        ingest.evidence.append(EvidenceInput(
                            provider=observation["source_id"], source_kind="native_postfetch_observation",
                            source_url=(observation.get("request") or {}).get("url"),
                            content_hash=digest, content_ref=reference, mime_type="application/json",
                            observed_at=observed, published_at=observed, grade=EvidenceGrade.C,
                            immutable_proof=True, metadata={"purpose": "native_postfetch_input",
                                "dimension": dimension, "request": observation.get("request"),
                                "version": postfetch["version"], "historical_reconstruction": False,
                                "research_document": False, "publication_time_basis": "observed_upper_bound",
                                "source_published_at": None},
                        ))
                    receipts.append(receipt)
                ingest.query_manifest["native_postfetch"] = {"version": postfetch["version"], "observations": receipts}
                if isinstance(postfetch.get("search_budget"), Mapping):
                    ingest.query_manifest["native_postfetch"]["search_budget"] = deepcopy(postfetch["search_budget"])
            row = ingestion.ingest(ingest)
            if on_persist is not None:
                on_persist(session, task_id, row)
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
        if write_fence is not None and not write_fence(session):
            raise DraftAgentRecoveryError("postfetch_worker_stopped", "补搜写入租约失效")
    return persisted


def _preserve_recovery_input_provenance(repository, recovered, ingest):
    """Keep source observations separate from the projected working result."""
    facts = recovered.get("facts") or {}
    binding = facts.get("_market_data_input_snapshot")
    if not isinstance(binding, Mapping):
        return
    original = repository.get_snapshot(str(binding.get("snapshot_id") or ""))
    if original is None or original.content_hash != binding.get("content_hash") or (
        original.symbol, original.market, original.dimension, original.as_of_date
    ) != (ingest.symbol, ingest.market, ingest.dimension, ingest.as_of_date):
        raise DraftAgentRecoveryError("agent_recovery_input_mismatch", "Agent 补采输入的快照版本或范围已不一致")
    payload = original.payload_json or {}
    envelope = payload.get("_uzi_collector_original_result", payload.get("_uzi_raw_result"))
    if isinstance(envelope, Mapping):
        ingest.payload["_uzi_collector_original_result"] = deepcopy(dict(envelope))
    ingest.query_manifest["agent_recovery"].update(
        input_snapshot_id=original.id, input_snapshot_hash=original.content_hash,
        working_result="project_native_result", original_collector_result="payload._uzi_collector_original_result",
    )
    ingest.field_sources = {**deepcopy(original.field_sources_json or {}), **ingest.field_sources}
    evidence = repository.list_snapshot_evidence(original.id)
    transported = native_transport_facts(original, evidence=evidence, cutoff_at=cutoff_at_for_date(original.as_of_date, original.exchange_timezone))
    transport_keys = ("_uzi_root_fields", "_uzi_top_level_fields")
    unchanged_transport = all(
        (key in facts) == (key in transported) and facts.get(key) == transported.get(key)
        for key in transport_keys
    )
    if unchanged_transport and (original.query_manifest_json or {}).get("native_symbol_transport"):
        for key in transport_keys:
            if key in payload:
                ingest.payload[key] = deepcopy(payload[key])
        ingest.query_manifest["native_symbol_transport"] = deepcopy(original.query_manifest_json["native_symbol_transport"])
        # A copy of a prior observation is not a newly fetched root bonus.
        ingest.evidence = [row for row in ingest.evidence if row.source_kind != "native_symbol_transport"]
    known = {row.evidence_key() for row in ingest.evidence}
    for row in evidence:
        if row.source_kind == "native_symbol_transport" and not unchanged_transport:
            continue
        item = EvidenceInput(
            provider=row.provider, source_url=row.source_url, source_kind=row.source_kind,
            title=row.title, mime_type=row.mime_type, content_hash=row.content_hash,
            content_ref=row.content_ref, inline_content=row.inline_content,
            published_at=_database_utc(row.published_at), observed_at=_database_utc(row.observed_at),
            grade=row.grade, immutable_proof=row.immutable_proof, is_manual=row.is_manual,
            metadata=deepcopy(row.metadata_json or {}),
        )
        key = item.evidence_key()
        if key not in known:
            known.add(key)
            ingest.evidence.append(item)


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
