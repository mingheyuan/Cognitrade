"""Persist research documents as a new data-centre snapshot revision."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from threading import Lock, local
from time import monotonic
from typing import Any

from sqlalchemy import select

from research_system.market_data.contracts import (
    EvidenceInput,
    SeriesPointInput,
    SnapshotIngestInput,
    cutoff_at_for_date,
)
from research_system.market_data.ingestion import SnapshotIngestionService
from research_system.market_data.models import (
    MarketDataCollectionJob,
    MarketDataCollectionTask,
)
from research_system.market_data.repository import MarketDataRepository
from research_system.market_data.research_archive import (
    ARCHIVE_VERSION,
    RESEARCH_SOURCES,
    ArchiveCandidate,
    ArchiveEvidenceError,
    ResearchArchiveCollector,
    ResearchDocumentStore,
    _error_details,
    _research_subject_term,
)
from research_system.market_data.resolver import (
    SnapshotResolver,
    _database_utc,
    _snapshot_pit_visibility_errors,
)


def recover_research_documents(
    database,
    *,
    job_id: str,
    settings,
    registry,
    cancellation=None,
    archive_collector=None,
    progress_callback=None,
    max_duration_seconds: float = 600,
    task_ids=None,
) -> dict[str, Any]:
    """Called only within an explicitly requested collection/recovery job.

    Scope is the caller's fixed task IDs. ``None`` retains the explicit
    whole-job direct-call contract; workers always pass their claimed IDs.
    No scheduler or report can implicitly opt in through a report boundary.
    """
    deadline = monotonic() + max(0.0, max_duration_seconds)
    parent_cancellation = cancellation

    class DeadlineCancellation:
        def is_set(self):
            return monotonic() >= deadline or (
                parent_cancellation is not None and parent_cancellation.is_set()
            )

    cancellation = DeadlineCancellation()
    transports = local()

    def get_collector():
        if archive_collector is not None:
            return archive_collector
        if not hasattr(transports, "collector"):
            from research_system.adapters.vendors.uzi_browser_adapter import (
                UziBrowserAdapter,
            )

            transports.collector = ResearchArchiveCollector(
                store=ResearchDocumentStore(
                    Path(settings.workspace_root) / "cache" / "market_data_documents"
                ),
                browser_adapter=UziBrowserAdapter(settings)
                if getattr(settings, "browser_sources_enabled", False)
                else None,
            )
        return transports.collector

    with database.session() as session:
        job = session.get(MarketDataCollectionJob, job_id)
        if job is None:
            raise ValueError("collection job not found")
        max_workers = min(5, max(1, int(job.max_concurrency)))
        requested_documents = list((job.request_json or {}).get("research_document_requests") or [])
        refresh_originals = job.mode == "force_all" and job.origin == "manual"
        query = select(MarketDataCollectionTask).where(MarketDataCollectionTask.job_id == job_id)
        if task_ids is not None:
            try:
                if isinstance(task_ids, (str, bytes)):
                    raise TypeError("invalid task IDs")
                task_ids = frozenset(task_ids)
                if any(not isinstance(key, str) or not key for key in task_ids):
                    raise ValueError("invalid task IDs")
            except (TypeError, ValueError):
                raise ArchiveEvidenceError("research_archive_task_scope_invalid", "归档任务范围未获授权") from None
            if not task_ids:
                return {"status": "not_requested", "reason_code": "research_archive_task_scope_empty",
                        "attempted": 0, "succeeded": 0, "failed": 0,
                        "persisted_snapshot_count": 0, "persisted_snapshots": [], "attempts": [],
                        "document_inventory_ready": False, "research_completion": "not_assessed",
                        "raw_enrichment": "not_run", "evidence_check_passed": None}
            query = query.where(MarketDataCollectionTask.id.in_(task_ids))
        tasks = list(session.scalars(query))
        if task_ids is not None and {task.id for task in tasks} != task_ids:
            raise ArchiveEvidenceError("research_archive_task_scope_invalid", "归档任务范围未获授权")
        scopes = [
            (
                task.id,
                task.symbol,
                task.market,
                task.exchange_timezone,
                task.dimension,
                task.start_date + timedelta(days=offset),
            )
            for task in tasks
            if task.dimension in RESEARCH_SOURCES
            and (not requested_documents or any(row["dimension"] == task.dimension for row in requested_documents))
            for offset in range((task.end_date - task.start_date).days + 1)
        ]
    attempts, persisted = [], []
    progress_lock = Lock()

    def progress(active=None):
        if progress_callback is not None:
            with progress_lock:
                progress_callback(
                    {
                        "total": len(scopes),
                        "completed": len(attempts),
                        "active": active,
                        "attempts": list(attempts),
                        "persisted_snapshot_count": len(persisted),
                        "max_concurrency": max_workers,
                    }
                )

    def process(scope):
        task_id, symbol, market, zone, dimension, as_of = scope
        source_urls = [row["url"] for row in requested_documents if row["dimension"] == dimension]
        if requested_documents and not source_urls:
            # A mixed plan may also collect structured gaps. Its unrelated
            # dimensions must not trigger broader document discovery here.
            return
        if cancellation.is_set():
            attempts.append(
                {
                    "symbol": symbol,
                    "dimension": dimension,
                    "as_of_date": as_of.isoformat(),
                    "status": "cancelled",
                    "reason": "research_archive_deadline"
                    if monotonic() >= deadline
                    else "collection_cancelled",
                }
            )
            return
        progress(
            {"symbol": symbol, "dimension": dimension, "as_of_date": as_of.isoformat()}
        )
        with database.session() as session:
            repository = MarketDataRepository(session)
            coverage = SnapshotResolver(repository, registry=registry).coverage(
                symbol, market, as_of, exchange_timezone=zone
            )
            chosen = next(
                (row for row in coverage.dimensions if row.dimension == dimension), None
            )
            snapshot = (
                repository.get_snapshot(chosen.snapshot_id)
                if chosen and chosen.snapshot_id
                else None
            )
            if snapshot is None:
                attempts.append(
                    {
                        "symbol": symbol,
                        "dimension": dimension,
                        "status": "input_missing",
                        "reason": "collect_dimension_before_research",
                    }
                )
                progress()
                return
            existing = [
                row
                for row in repository.list_snapshot_evidence(snapshot.id)
                if row.source_kind == "research_document"
            ]
            inventory = research_document_inventory(existing)
            if inventory["document_count"] and not refresh_originals and not source_urls:
                attempts.append(
                    {
                        "symbol": symbol,
                        "dimension": dimension,
                        "status": "ready",
                        "reused": True,
                        "document_inventory": inventory,
                        "research_completion": "not_assessed",
                        "raw_enrichment": "not_run",
                    }
                )
                progress()
                return
            snapshot_id = snapshot.id
            refresh_inputs = []
            originals = []
            if (refresh_originals or not inventory["document_count"]) and not source_urls:
                # A newer structured snapshot can lose the previous document
                # links even during missing-or-stale collection. Recover only
                # this exact stock/day/dimension's verified local originals;
                # keep the selected raw input and create a new bound revision.
                # Explicit URL requests still fetch precisely those URLs.
                archive_owner = snapshot
                if not inventory["document_count"]:
                    for prior in repository.list_snapshots(
                        symbol=symbol,
                        market=market,
                        dimension=dimension,
                        as_of_date=as_of,
                        cutoff_at=cutoff_at_for_date(as_of, zone),
                        limit=100,
                    ):
                        if prior.contract_version != registry.contract_version or prior.exchange_timezone != zone:
                            continue
                        if _snapshot_pit_visibility_errors(
                            repository, prior, cutoff_at_for_date(as_of, zone)
                        ):
                            continue
                        proofs = repository.list_snapshot_evidence(prior.id)
                        if research_document_inventory(proofs)["document_count"]:
                            archive_owner, existing = (
                                prior,
                                [
                                    row
                                    for row in proofs
                                    if row.source_kind == "research_document"
                                ],
                            )
                            break
                originals = [
                    (_evidence_input(row), row.id, archive_owner.id) for row in existing
                ]
                if originals:
                    refresh_inputs = [
                        {"snapshot_id": row.id, "content_hash": row.content_hash}
                        for row in {
                            snapshot.id: snapshot,
                            archive_owner.id: archive_owner,
                        }.values()
                    ]
                # This is a new text representation of already observed raw
                # and originals, not a new source observation. Never borrow
                # only an old archive timestamp to backdate newer raw inputs.
                refresh_observed_at = max(
                    [
                        _database_utc(snapshot.observed_at),
                        _database_utc(archive_owner.observed_at),
                        *(row.observed_at for row, _, _ in originals),
                    ]
                )
            raw = dict(snapshot.payload_json.get("raw") or {})
            basic_choice = next(
                (row for row in coverage.dimensions if row.dimension == "0_basic"), None
            )
            basic = (
                repository.get_snapshot(basic_choice.snapshot_id)
                if basic_choice and basic_choice.snapshot_id
                else None
            )
            basic_raw = dict(
                (basic.payload_json.get("raw") or basic.payload_json) if basic else {}
            )
            subject = next(
                (
                    term
                    for value in (
                        raw.get("industry"), basic_raw.get("industry"),
                        raw.get("name"), basic_raw.get("name"),
                    )
                    if (term := _research_subject_term(value)) is not None
                ),
                symbol,
            )
            # Context is read from snapshots chosen at the requested cutoff,
            # never from current company classification or an unbounded lookup.
            industry_choice = next(
                (row for row in coverage.dimensions if row.dimension == "7_industry"),
                None,
            )
            industry = (
                repository.get_snapshot(industry_choice.snapshot_id)
                if industry_choice and industry_choice.snapshot_id
                else None
            )
            industry_raw = dict(
                (industry.payload_json.get("raw") or industry.payload_json)
                if industry
                else {}
            )
            subject_terms = tuple(
                dict.fromkeys(
                    term
                    for context in (raw, basic_raw, industry_raw)
                    for key in (
                        "name",
                        "industry",
                        "industry_name",
                        "sw_level1",
                        "sw_level2",
                    )
                    if (term := _research_subject_term(context.get(key))) is not None
                )
            )
            subject_context = [
                {"snapshot_id": row.id, "content_hash": row.content_hash}
                for row in (snapshot, basic, industry)
                if row is not None
            ]
            if originals:
                refresh_inputs = list(
                    {
                        row["snapshot_id"]: row
                        for row in [*refresh_inputs, *subject_context]
                    }.values()
                )
                refresh_observed_at = max(
                    refresh_observed_at,
                    *(
                        _database_utc(row.observed_at)
                        for row in (snapshot, basic, industry)
                        if row is not None
                    ),
                )
        cutoff = cutoff_at_for_date(as_of, zone)
        try:
            if originals:
                result = _reextract_archived_originals(
                    originals,
                    store=ResearchDocumentStore(
                        Path(settings.workspace_root)
                        / "cache"
                        / "market_data_documents"
                    ),
                    dimension=dimension,
                    symbol=symbol,
                    subject=subject,
                    subject_terms=subject_terms,
                    cutoff_at=cutoff,
                    cancellation=cancellation,
                    deadline=deadline,
                )
            else:
                result = get_collector().collect(
                    dimension=dimension,
                    symbol=symbol,
                    subject=subject,
                    cutoff_at=cutoff,
                    subject_terms=subject_terms,
                    cancellation=cancellation,
                    **({"requested_urls": source_urls, "limit": len(source_urls)} if source_urls else {}),
                )
            attempt = {
                "symbol": symbol,
                "dimension": dimension,
                "as_of_date": as_of.isoformat(),
                "status": result["status"],
                "sources": result["attempts"],
                "document_inventory": result.get("document_inventory"),
                "research_completion": "not_assessed",
                "raw_enrichment": "not_run",
                "subject_context": subject_context,
                **(
                    {
                        "acquisition_mode": "archived_original_reextraction",
                        "inputs": refresh_inputs,
                    }
                    if originals
                    else {}
                ),
            }
            if result["evidence"]:
                for document in result["evidence"]:
                    document.metadata = {
                        **document.metadata,
                        "subject_context_snapshots": subject_context,
                    }
                with database.session() as session:
                    repository = MarketDataRepository(session)
                    if cancellation.is_set():
                        raise ArchiveEvidenceError(
                            "archive_cancelled_or_deadline", "原文提取已取消或达到时限"
                        )
                    if originals:
                        _validate_refresh_inputs(
                            repository, refresh_inputs, cutoff, originals
                        )
                    row = persist_research_documents(
                        repository,
                        registry=registry,
                        snapshot_id=snapshot_id,
                        documents=result["evidence"],
                        collection_task_id=task_id,
                        job_id=job_id,
                        observed_at=refresh_observed_at if originals else None,
                    )
                    attempt["reused"] = row.id == snapshot_id
                    if row.id != snapshot_id:
                        persisted.append(
                            {
                                "symbol": symbol,
                                "dimension_id": dimension,
                                "as_of_date": as_of.isoformat(),
                                "snapshot_id": row.id,
                                "content_hash": row.content_hash,
                                "status": row.status,
                            }
                        )
        except Exception as exc:  # noqa: BLE001 - other source scopes remain independently recoverable
            attempt = {
                "symbol": symbol,
                "dimension": dimension,
                "status": "failed",
                **_error_details(exc),
                "retryable": True,
            }
        attempts.append(attempt)
        progress()

    # No nested executor or abandoned background HTTP worker: on cancellation
    # queued scopes do not start I/O, and this context drains active bounded calls.
    with ThreadPoolExecutor(
        max_workers=max_workers, thread_name_prefix="research-archive"
    ) as pool:
        list(pool.map(process, scopes))
    progress()
    return {
        "enabled": True,
        "attempted": len(attempts),
        "succeeded": sum(row["status"] == "ready" for row in attempts),
        "failed": sum(row["status"] != "ready" for row in attempts),
        "persisted_snapshot_count": len(persisted),
        "persisted_snapshots": persisted,
        "attempts": attempts,
        "deadline_exceeded": monotonic() >= deadline,
        "max_concurrency": max_workers,
        "document_inventory_ready": bool(scopes)
        and all(row["status"] == "ready" for row in attempts),
        "research_completion": "not_assessed",
        "raw_enrichment": "not_run",
        # Retain the legacy key without misrepresenting document acquisition
        # as the native Agent's per-finding evidence/completion check.
        "evidence_check_passed": None,
        "evidence_check_scope": "document_inventory_only",
    }


def research_document_inventory(evidence) -> dict[str, Any]:
    """Count original documents, not URLs or Agent findings.

    One archived original can support multiple distinct findings. The future
    explicit enrichment stage binds those findings by document hash + excerpt;
    it must not manufacture another copy of the document to pass a URL count.
    """
    hashes = {
        item.content_hash
        for item in evidence
        if item.source_kind == "research_document"
        and item.inline_content
        and item.content_ref
        and item.source_url
        and item.content_hash
    }
    return {
        "status": "available" if hashes else "missing",
        "document_count": len(hashes),
        "content_hashes": sorted(hashes),
    }


def research_archive_gap_dimensions(
    repository, *, registry, symbol: str, market: str, as_of_date, dimensions
) -> list[str]:
    """Find unarchived research scopes without reading remote sources."""
    coverage = SnapshotResolver(repository, registry=registry).coverage(
        symbol, market, as_of_date
    )
    by_dim = {row.dimension: row for row in coverage.dimensions}
    missing = []
    for dimension in dimensions:
        if dimension not in RESEARCH_SOURCES:
            continue
        row = by_dim.get(dimension)
        evidence = (
            repository.list_snapshot_evidence(row.snapshot_id)
            if row and row.snapshot_id
            else []
        )
        if not research_document_inventory(evidence)["document_count"]:
            missing.append(dimension)
    return missing


def _evidence_input(row) -> EvidenceInput:
    return EvidenceInput(
        provider=row.provider,
        source_url=row.source_url,
        source_kind=row.source_kind,
        title=row.title,
        mime_type=row.mime_type,
        content_hash=row.content_hash,
        content_ref=row.content_ref,
        inline_content=row.inline_content,
        published_at=_database_utc(row.published_at),
        observed_at=_database_utc(row.observed_at),
        grade=row.grade,
        immutable_proof=row.immutable_proof,
        is_manual=row.is_manual,
        metadata=deepcopy(row.metadata_json or {}),
    )


class _ArchivedOriginalTransport:
    """Replay one already verified local response through the existing parser.

    This has no network client, discovery method, redirect, or URL fallback.
    Reusing the collector's parser avoids a second PDF/HTML extraction policy.
    """

    status_code = 200

    def __init__(self, url: str, content: bytes):
        self.url, self.content = url, content

    def get(self, url, **kwargs):
        if url != self.url:
            raise ArchiveEvidenceError(
                "archive_replay_url_mismatch", "原件引用地址不匹配"
            )
        return self

    def raise_for_status(self):
        pass

    def close(self):
        pass

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.content), chunk_size):
            yield self.content[offset : offset + chunk_size]


def _verified_original_content(original):
    path = Path(str(original.content_ref or ""))
    if (
        not path.is_absolute()
        or path.stem != original.content_hash
        or path.parent.name != original.content_hash[:2]
    ):
        raise ArchiveEvidenceError(
            "archive_original_path_invalid", "原件引用不是按内容 hash 归档的路径"
        )
    try:
        if path.stat().st_size > 12 * 1024 * 1024:
            raise ArchiveEvidenceError(
                "archive_document_too_large", "原文超过归档大小限制"
            )
        content = path.read_bytes()
    except OSError:
        raise ArchiveEvidenceError(
            "archive_original_unavailable", "本地归档原件不可读，未重新联网抓取"
        ) from None
    if sha256(content).hexdigest() != original.content_hash:
        raise ArchiveEvidenceError(
            "archive_content_corrupt", "已归档文档内容 hash 不匹配"
        )
    return content


def _reextract_archived_originals(
    originals,
    *,
    store,
    dimension,
    symbol,
    subject,
    subject_terms,
    cutoff_at,
    cancellation,
    deadline,
):
    documents, attempts, seen = [], [], set()
    for original, evidence_id, snapshot_id in originals:
        if cancellation.is_set() or monotonic() >= deadline:
            raise ArchiveEvidenceError(
                "archive_cancelled_or_deadline", "原文提取已取消或达到时限"
            )
        if original.content_hash in seen:
            continue
        seen.add(original.content_hash)
        if original.grade.value not in {"A", "B"} or not original.immutable_proof:
            raise ArchiveEvidenceError(
                "archive_original_proof_invalid", "原件缺少已验证的不可变来源依据"
            )
        content = _verified_original_content(original)
        metadata = original.metadata
        candidate = ArchiveCandidate(
            url=original.source_url,
            title=original.title or "",
            published_at=original.published_at,
            publisher_document_id=metadata.get("publisher_document_id"),
            listing_url=metadata.get("listing_url"),
            listing_hash=metadata.get("listing_hash"),
            immutable_attachment=bool(
                metadata.get("publisher_document_id") and metadata.get("listing_hash")
            ),
            attachment_proof_basis=metadata.get("proof_basis"),
            security_code=(metadata.get("relevance") or {}).get("security_code"),
        )
        parsed = ResearchArchiveCollector(
            store=store,
            http=_ArchivedOriginalTransport(original.source_url, content),
            # Replay its proven observation; the new extraction time below is
            # separate and must not be presented as a new source retrieval.
            clock=lambda observed=original.observed_at: observed,
        ).archive(
            candidate,
            dimension=dimension,
            symbol=symbol,
            subject=subject,
            subject_terms=subject_terms,
            cutoff_at=cutoff_at,
            cancellation=cancellation,
            deadline=deadline,
        )
        document = original.model_copy(deep=True)
        document.inline_content = parsed.inline_content
        document.metadata = {
            **document.metadata,
            "archive_version": ARCHIVE_VERSION,
            "text_extraction": parsed.metadata["text_extraction"],
            "text_reextraction": {
                "source_evidence_id": evidence_id,
                "source_snapshot_id": snapshot_id,
                "source_content_hash": original.content_hash,
                "source_observed_at": original.observed_at.isoformat(),
                "extracted_at": datetime.now(timezone.utc).isoformat(),
                "transport": "verified_local_original",
                "network_requests": 0,
            },
        }
        documents.append(document)
        attempts.append(
            {
                "stage": "local_original_reextraction",
                "status": "extracted",
                "content_hash": document.content_hash,
                "source_evidence_id": evidence_id,
                "text_content_hash": document.metadata["text_extraction"][
                    "text_content_hash"
                ],
            }
        )
    return {
        "status": "ready",
        "evidence": documents,
        "attempts": attempts,
        "document_inventory": research_document_inventory(documents),
    }


def _validate_refresh_inputs(repository, inputs, cutoff, originals):
    """Recheck current raw and original owners immediately before new writes."""
    locked = {
        row.id: row
        for row in repository.lock_snapshots_for_update(
            row["snapshot_id"] for row in inputs
        )
    }
    for identity in inputs:
        row = locked.get(identity["snapshot_id"])
        if (
            row is None
            or row.revoked_at is not None
            or row.content_hash != identity["content_hash"]
            or _snapshot_pit_visibility_errors(repository, row, cutoff)
        ):
            raise ArchiveEvidenceError(
                "archive_refresh_input_changed",
                "重提取依赖已变更、撤销或不满足历史时点",
            )
    for original, evidence_id, snapshot_id in originals:
        proof = next(
            (
                row
                for row in repository.list_snapshot_evidence(snapshot_id)
                if row.id == evidence_id
            ),
            None,
        )
        if (
            proof is None
            or _evidence_input(proof).model_dump() != original.model_dump()
        ):
            raise ArchiveEvidenceError(
                "archive_refresh_input_changed", "重提取原件证据已变更或不再绑定"
            )
        _verified_original_content(original)


def persist_research_documents(
    repository,
    *,
    registry,
    snapshot_id: str,
    documents: list[EvidenceInput],
    collection_task_id: str,
    job_id: str,
    now: datetime | None = None,
    observed_at: datetime | None = None,
):
    original = repository.require_snapshot(snapshot_id)
    cutoff = cutoff_at_for_date(original.as_of_date, original.exchange_timezone)
    for document in documents:
        if (
            document.source_kind != "research_document"
            or not document.inline_content
            or not document.content_ref
        ):
            raise ValueError(
                "research evidence requires persisted original content and excerpt"
            )
        if document.published_at > cutoff:
            raise ValueError("research evidence crosses snapshot cutoff")
        if document.observed_at > cutoff and (
            document.grade.value != "B" or not document.immutable_proof
        ):
            raise ValueError(
                "historical research evidence requires archive version proof"
            )
    existing = repository.list_snapshot_evidence(original.id)
    evidence = [_evidence_input(row) for row in existing]
    existing_hashes = {
        row.content_hash for row in existing if row.source_kind == "research_document"
    }
    # A re-extraction of the same immutable PDF is a NEW evidence version.
    # Legacy duplicates retain the old source-hash behavior; explicit text
    # versions include their extraction/content identity, not local paths.
    existing_keys = {
        row.evidence_key() for row in evidence if row.source_kind == "research_document"
    }
    fresh = list(
        {
            row.evidence_key(): row
            for row in documents
            if (
                row.evidence_key() not in existing_keys
                if row.metadata.get("text_extraction") is not None
                else row.content_hash not in existing_hashes
            )
        }.values()
    )
    if not fresh:
        return original
    manifest = deepcopy(original.query_manifest_json or {})
    manifest["research_archive"] = {
        "version": ARCHIVE_VERSION,
        "job_id": job_id,
        "documents": sorted({*existing_hashes, *(row.content_hash for row in fresh)}),
        "evidence_modes": sorted(
            {str(row.metadata.get("evidence_mode") or "unknown") for row in fresh}
        ),
        "document_inventory": "available",
        "research_completion": "not_assessed",
        "raw_enrichment": "not_run",
    }
    text_extractions = [
        {
            "evidence_key": row.evidence_key(),
            "binding": deepcopy(row.metadata["text_extraction"]),
        }
        for row in [*evidence, *fresh]
        if row.metadata.get("text_extraction") is not None
    ]
    if text_extractions:
        manifest["research_archive"]["text_extractions"] = sorted(
            text_extractions, key=lambda row: row["evidence_key"]
        )
    now = now or datetime.now(timezone.utc)
    if observed_at is not None:
        # Offline re-extraction can retain source observation time, never
        # precede the current raw snapshot or any newly bound proof.
        observed_at = max(
            observed_at,
            _database_utc(original.observed_at),
            *(row.observed_at for row in [*evidence, *fresh]),
        )
    if now > cutoff:
        manifest["pit_mode"] = "historical_reconstruction"
    ingest = SnapshotIngestInput(
        symbol=original.symbol,
        market=original.market,
        exchange_timezone=original.exchange_timezone,
        dimension=original.dimension,
        contract_version=original.contract_version,
        as_of_date=original.as_of_date,
        status=original.status,
        payload=deepcopy(original.payload_json),
        effective_at=_database_utc(original.effective_at),
        published_at=_database_utc(original.published_at),
        observed_at=observed_at or now,
        ingested_at=now,
        source_version=original.source_version,
        source_priority=original.source_priority,
        origin="derived",
        query_manifest=manifest,
        validation=deepcopy(original.validation_json),
        field_sources=deepcopy(original.field_sources_json),
        evidence=[*evidence, *fresh],
        collection_task_id=collection_task_id,
        provider_attempt_id=f"research-archive:{job_id}:{now.isoformat()}",
        series_points=[
            SeriesPointInput(
                series_key=row.series_key,
                point_at=_database_utc(row.point_at),
                effective_at=_database_utc(row.effective_at),
                published_at=_database_utc(row.published_at),
                observed_at=_database_utc(row.observed_at),
                value=row.value_json,
                source_version=row.source_version,
                metadata=row.metadata_json,
            )
            for row in repository.list_snapshot_series_points(original.id)
        ],
    )
    return SnapshotIngestionService(repository, registry=registry).ingest(ingest)
