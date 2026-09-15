"""Explicit A-share rebuild from an immutable, point-in-time profile revision.

This producer attests dependency transport, not native/research quality. It does
not revise basic, publish or perform Agent research. Historical consumers use
bounded adapters or the existing original-document native derivations. All
source calls run synchronously outside database sessions.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from time import monotonic
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from research_system.market_data.company_profile_dependencies import (
    DEPENDENCY_RECEIPT_VERSION,
    check_company_profile_dependencies,
)
from research_system.market_data.company_profile_revision import (
    CompanyProfileRefusal,
    _check_snapshot,
    _document,
    _evidence_input,
)
from research_system.market_data.contracts import (
    CollectionStatus,
    canonical_hash,
    cutoff_at_for_date,
)
from research_system.market_data.ingestion import SnapshotIngestionService
from research_system.market_data.models import (
    MarketDataCollectionJob,
    MarketDataCollectionTask,
    MarketDataCollectionTaskResult,
)
from research_system.market_data.native_projection import (
    PROJECTION_VERSION,
    project_native_result,
)
from research_system.market_data.repository import MarketDataRepository
from research_system.market_data.research_archive_text import (
    ResearchTextError,
    snapshot_text_bindings,
)
from research_system.market_data.resolver import (
    SnapshotResolver,
    _snapshot_pit_visibility_errors,
)
from research_system.market_data.uzi_adapter import UziSnapshotAdapter
from research_system.market_data.uzi_gate import native_snapshot_usable

VERSION = "company-profile-dependency-rebuild-v1"
SUPPORTED = {"3_macro": "macro", "7_industry": "industry", "4_peers": "peers",
    "8_materials": "materials", "9_futures": "futures", "10_valuation": "valuation",
    "13_policy": "policy", "14_moat": "moat", "15_events": "events", "16_lhb": "lhb",
    "17_sentiment": "sentiment", "18_trap": "trap", "similar_stocks": "similar_stocks"}


def _today(zone):
    return datetime.now(timezone.utc).astimezone(ZoneInfo(zone)).date()


class _DeadlineCancellation:
    def __init__(self, parent, deadline):
        self.parent, self.deadline = parent, deadline

    def is_set(self):
        return bool(self.parent is not None and self.parent.is_set()) or monotonic() >= self.deadline

    def reason(self):
        if self.parent is not None and self.parent.is_set():
            return "cancelled"
        return "deadline_exceeded" if monotonic() >= self.deadline else None


def _stop(cancellation):
    if reason := cancellation.reason():
        raise CompanyProfileRefusal(reason)


def _job(session, job_id, write_fence, cancellation):
    _stop(cancellation)
    if write_fence is not None and not write_fence(session):
        raise CompanyProfileRefusal("worker_lease_lost")
    job = session.get(MarketDataCollectionJob, job_id)
    if job is None or job.origin != "manual":
        raise CompanyProfileRefusal("explicit_collection_required")
    if job.cancel_requested:
        raise CompanyProfileRefusal("cancelled")
    return job


def _scopes(session, job_id, task_ids=()):
    # A retry wave is narrower than its original job. Authority must come
    # from the coordinator's claimed tasks, never from all old job rows.
    if isinstance(task_ids, (str, bytes)) or task_ids is None:
        raise CompanyProfileRefusal("profile_rebuild_task_scope_invalid")
    task_ids = tuple(task_ids)
    if any(not isinstance(key, str) or not key for key in task_ids):
        raise CompanyProfileRefusal("profile_rebuild_task_scope_invalid")
    scopes = defaultdict(dict)
    if not task_ids:
        return scopes
    tasks = list(session.scalars(select(MarketDataCollectionTask).where(
        MarketDataCollectionTask.job_id == job_id, MarketDataCollectionTask.id.in_(task_ids))))
    if {task.id for task in tasks} != set(task_ids):
        raise CompanyProfileRefusal("profile_rebuild_task_scope_invalid")
    for task in tasks:
        for offset in range((task.end_date - task.start_date).days + 1):
            scopes[(task.symbol, task.market, task.exchange_timezone,
                    task.start_date + timedelta(days=offset))][task.dimension] = task.id
    return scopes


def _selected(repository, registry, scope):
    symbol, market, zone, day = scope
    coverage = SnapshotResolver(repository, registry=registry).coverage(symbol, market, day, exchange_timezone=zone)
    return {row.dimension: repository.require_snapshot(row.snapshot_id)
            for row in coverage.dimensions if row.snapshot_id}


def has_company_profile_rebuild(database, *, job_id, registry, task_ids=()):
    """Cheap read-only routing: no marker means no extra stage/timer/provider."""
    with database.session() as session:
        job = session.get(MarketDataCollectionJob, job_id)
        if job is None or job.origin != "manual":
            return False
        for scope in _scopes(session, job_id, task_ids):
            basic = _selected(MarketDataRepository(session), registry, scope).get("0_basic")
            if basic is not None and "company_profile_revision" in (basic.query_manifest_json or {}):
                return True
    return False


def _identity(snapshot):
    return {"snapshot_id": snapshot.id, "snapshot_hash": snapshot.content_hash}


def _documents(repository, snapshot, cutoff):
    if snapshot is None:
        return [], []
    proofs = repository.list_snapshot_evidence(snapshot.id)
    bindings = snapshot_text_bindings(snapshot, proofs)
    evidence, text_bindings = [], []
    for proof in proofs:
        if _document(snapshot, proof, bindings, cutoff) is None:
            continue
        evidence.append(_evidence_input(proof))
        if proof.evidence_key in bindings:
            text_bindings.append({"evidence_key": proof.evidence_key, "binding": deepcopy(bindings[proof.evidence_key])})
    return evidence, text_bindings


def _packet(repository, registry, scope, requested):
    chosen = _selected(repository, registry, scope)
    basic = chosen.get("0_basic")
    if basic is None or "company_profile_revision" not in (basic.query_manifest_json or {}):
        return None
    cutoff = cutoff_at_for_date(scope[3], scope[2])
    if (basic.symbol, basic.market, basic.exchange_timezone, basic.as_of_date) != scope:
        raise CompanyProfileRefusal("basic_scope_mismatch")
    _check_snapshot(repository, basic, basic.content_hash, cutoff)
    _documents(repository, basic, cutoff)
    check = check_company_profile_dependencies(chosen)
    if check["status"] == "invalid":
        raise CompanyProfileRefusal(check["issues"][0]["reason"])
    # Validate the original fact provenance, without resolving other sources.
    marker = basic.query_manifest_json["company_profile_revision"]
    for identity in marker["source_snapshot_inputs"]:
        source = repository.require_snapshot(identity["snapshot_id"])
        if (source.symbol, source.market, source.exchange_timezone, source.as_of_date) != scope:
            raise CompanyProfileRefusal("source_scope_mismatch")
        _check_snapshot(repository, source, identity["snapshot_hash"], cutoff)
    targets = [key for key in SUPPORTED if key in requested and key in check["required_dimensions"]
               and (key in check["pending_dimensions"] or not native_snapshot_usable(chosen[key]))]
    if "8_materials" in targets and "9_futures" in requested and "9_futures" in check["required_dimensions"]:
        # An authorized new 8 response changes 9's real native input even if
        # the company identity itself is unchanged. Never expand task scope.
        targets = [key for key in SUPPORTED if key in {*targets, "9_futures"}]
    blocked, materials_input, fixed_materials = {}, None, None
    if "9_futures" in targets and "8_materials" not in targets:
        material = chosen.get("8_materials")
        if material is None or "8_materials" in check["pending_dimensions"]:
            blocked["9_futures"] = "materials_dependency_unbound"
        else:
            _check_snapshot(repository, material, material.content_hash, cutoff)
            fixed_materials = project_native_result(material, version="uzi-dim-result-v3")
            materials_input = _identity(material)
        if blocked:
            targets.remove("9_futures")
    originals, archives = {}, {}
    for key in SUPPORTED:
        if key not in requested or key not in check["required_dimensions"]:
            continue
        old = chosen.get(key)
        if old is not None:
            if (old.symbol, old.market, old.exchange_timezone, old.as_of_date) != scope:
                raise CompanyProfileRefusal("dependency_scope_mismatch")
            _check_snapshot(repository, old, old.content_hash, cutoff)
            originals[key] = _identity(old)
        archives[key] = _documents(repository, old, cutoff)
    native_basic = project_native_result(basic, version="uzi-dim-result-v3")
    reused = [{"dimension": key, **_identity(chosen[key]),
               "document_status": "inherited_original_documents" if archives[key][0] else "missing_original_documents",
               "known_upstream_issues": _upstream_issues(project_native_result(chosen[key], version="uzi-dim-result-v3"), native_basic),
               "research_completion": "not_assessed"}
              for key in SUPPORTED if key in requested and key in check["required_dimensions"] and key not in targets
              and key not in check["pending_dimensions"] and native_snapshot_usable(chosen[key])]
    return {"basic": _identity(basic), "native_basic": native_basic,
            "profile_basis": next(({key: fact.get(key) for key in ("industry_basis", "classification_system")}
                                    for fact in reversed(marker["facts"]) if fact.get("field") == "industry"), {}),
            "fingerprint": check["dependency_fingerprint"], "check": check, "targets": targets,
            "source_inputs": deepcopy(marker["source_snapshot_inputs"]),
            "blocked_dimensions": blocked, "fixed_materials": fixed_materials, "materials_input": materials_input,
            "originals": originals, "archives": archives,
            "reused_inputs": reused, "reused_snapshot_ids": [row["snapshot_id"] for row in reused]}


def _view(ingest):
    return SimpleNamespace(payload_json=ingest.payload, query_manifest_json=ingest.query_manifest,
        source_version=ingest.source_version, validation_json=ingest.validation,
        status=ingest.status, dimension=ingest.dimension)


def _upstream_issues(native, basic):
    comparison = native.get("data", {}).get("global_peer_comparison")
    target = comparison.get("target") if isinstance(comparison, dict) else None
    if not isinstance(target, dict):
        return []
    return [{"reason": "native_global_peer_cache_profile_mismatch", "field": f"global_peer_comparison.target.{field}"}
            for field, source in (("name", "name"), ("local_industry", "industry"))
            if field in target and source in basic.get("data", {}) and target[field] != basic["data"][source]]


def _recompute_fixed(database, *, registry, scope, job_id, expected_basic, cancellation,
                     write_fence, native_recompute=None):
    from domain_packs.finance_ashare.subagents.uzi_392_native_methods import (
        build_uzi_raw,
    )
    from research_system.market_data.research_enrichment import (
        _native_input_row,
        recompute_native_facts,
    )
    cutoff = cutoff_at_for_date(scope[3], scope[2])
    with database.session() as session:
        _job(session, job_id, write_fence, cancellation)
        repository = MarketDataRepository(session)
        chosen = _selected(repository, registry, scope)
        if _identity(chosen["0_basic"]) != expected_basic:
            raise CompanyProfileRefusal("selected_basic_changed")
        check = check_company_profile_dependencies(chosen)
        if check["status"] != "ready":
            return {"status": "missing_dependency", "pending_dimensions": check["pending_dimensions"]}
        identities, inputs = {}, []
        for key, snapshot in sorted(chosen.items()):
            _check_snapshot(repository, snapshot, snapshot.content_hash, cutoff)
            identities[key] = _identity(snapshot)
            inputs.append(_native_input_row(snapshot, evidence=repository.list_snapshot_evidence(snapshot.id), cutoff_at=cutoff))
        raw = build_uzi_raw(inputs, scope[0])
    started = monotonic()
    computed = recompute_native_facts(raw, profile="medium", deadline_at=cancellation.deadline,
                                     cancellation=cancellation, native_recompute=native_recompute)
    computed = {key: value for key, value in computed.items() if key != "result"}
    elapsed = max(0, monotonic() - started)
    with database.session() as session:
        _job(session, job_id, write_fence, cancellation)
        repository = MarketDataRepository(session)
        repository.lock_snapshots_for_update(row["snapshot_id"] for row in identities.values())
        current = _selected(repository, registry, scope)
        if {key: _identity(row) for key, row in current.items()} != identities:
            raise CompanyProfileRefusal("recompute_inputs_changed")
        for key, snapshot in current.items():
            _check_snapshot(repository, snapshot, identities[key]["snapshot_hash"], cutoff)
    return {**computed, "elapsed_seconds": elapsed, "snapshot_inputs": identities,
            "projection_version": PROJECTION_VERSION, "model_executions": 0, "published": False}


def _historical_derivation_tools(dimension):
    if dimension == "3_macro":
        from research_system.market_data.macro_derivation import (
            macro_derivation_ingest,
            prepare_macro_derivation,
            validate_persisted_macro,
        )
        return prepare_macro_derivation, macro_derivation_ingest, validate_persisted_macro
    if dimension == "14_moat":
        from research_system.market_data.moat_derivation import (
            moat_derivation_ingest,
            prepare_moat_derivation,
            validate_persisted_moat,
        )
        return prepare_moat_derivation, moat_derivation_ingest, validate_persisted_moat
    raise CompanyProfileRefusal("historical_derivation_dimension_invalid")


def _prepare_historical_derivations(database, *, registry, job_id, scope, requested, packet, cancellation):
    """Reuse real native archive consumers, not a benchmark-only substitute."""
    prepared = {}
    for dimension in packet["targets"]:
        if dimension not in {"3_macro", "14_moat"}:
            continue
        _stop(cancellation)
        prepare, _, _ = _historical_derivation_tools(dimension)
        spec = {"id": requested[dimension], "job_id": job_id, "symbol": scope[0],
                "market": scope[1], "exchange_timezone": scope[2]}
        result = prepare(database, spec=spec, as_of_date=scope[3], registry=registry,
                         deadline_at=cancellation.deadline, cancellation=cancellation)
        if result["status"] == "completed":
            basic = next((row for row in result["inputs"] if row["dimension"] == "0_basic"), None)
            if basic is None or {"snapshot_id": basic["snapshot_id"], "snapshot_hash": basic["content_hash"]} != packet["basic"]:
                raise CompanyProfileRefusal("historical_derivation_basic_changed")
        prepared[dimension] = result
    return prepared


def _persist(repository, *, registry, job, scope, requested, packet, rows, cancellation, write_fence,
             derivations=None):
    # This transaction owns the fence, re-selection, append and task linkage.
    repository.lock_snapshots_for_update(row["snapshot_id"] for row in
        [packet["basic"], *packet["originals"].values(), *packet["source_inputs"],
         *([packet["materials_input"]] if packet["materials_input"] else [])])
    current = _packet(repository, registry, scope, requested)
    if current is None or any(current[key] != packet[key] for key in ("basic", "native_basic", "fingerprint", "originals", "archives", "source_inputs", "fixed_materials", "materials_input")):
        raise CompanyProfileRefusal("selected_inputs_changed")
    symbol, market, zone, day = scope
    cutoff = cutoff_at_for_date(day, zone)
    by_type = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("symbol") != symbol or row.get("snapshot_type") not in {SUPPORTED[key] for key in packet["targets"]}:
            raise CompanyProfileRefusal("collector_result_scope_mismatch")
        if row["snapshot_type"] in by_type:
            raise CompanyProfileRefusal("collector_result_duplicate")
        facts = row.get("facts")
        raw = facts.get("_uzi_raw_result") if isinstance(facts, dict) else None
        if not isinstance(raw, dict) or not isinstance(raw.get("data"), dict):
            raise CompanyProfileRefusal("collector_dimresult_invalid")
        if day != _today(zone):
            proof = row.get("historical_reconstruction") or {}
            try:
                safe = (proof.get("safe") is True and proof.get("requested_as_of_date") == day.isoformat()
                        and datetime.fromisoformat(proof["cutoff_at"]) == cutoff
                        and proof.get("window_end") == day.isoformat())
            except (KeyError, TypeError, ValueError):
                safe = False
            if not safe:
                raise CompanyProfileRefusal("historical_rebuild_response_unbound")
        by_type[row["snapshot_type"]] = row
    revisions = []
    derivations = derivations or {}
    material_snapshot = None
    for dimension in packet["targets"]:
        _job(repository.session, job.id, write_fence, cancellation)
        task = repository.session.get(MarketDataCollectionTask, requested[dimension])
        if (task is None or task.job_id != job.id or task.dimension != dimension
                or (task.symbol, task.market, task.exchange_timezone) != scope[:3]
                or not task.start_date <= day <= task.end_date):
            raise CompanyProfileRefusal("task_scope_changed")
        native = by_type.get(SUPPORTED[dimension])
        derived = derivations.get(dimension)
        if derived is not None and derived["status"] != "completed":
            continue  # No execution receipt for a missing original or stopped native worker.
        if native is None and derived is None:
            continue
        if native is not None and (native.get("dependency_execution") or {}).get("status") == "not_run":
            continue
        previous_attempt = repository.session.scalar(select(func.max(MarketDataCollectionTaskResult.attempt)).where(
            MarketDataCollectionTaskResult.task_id == task.id)) or 0
        attempt = max(task.attempt_count, previous_attempt) + 1
        if derived is not None:
            _, derive_ingest, _ = _historical_derivation_tools(dimension)
            ingest = derive_ingest(repository, packet=derived, task=task, as_of_date=day, registry=registry)
            ingest.provider_attempt_id = f"{VERSION}:{job.id}:{task.id}:{attempt}"
        else:
            ingest = UziSnapshotAdapter(registry=registry).adapt(native, symbol=symbol, market=market,
                exchange_timezone=zone, as_of_date=day, collection_task_id=task.id,
                provider_attempt_id=f"{VERSION}:{job.id}:{task.id}:{attempt}")
        evidence, bindings = current["archives"][dimension] if derived is None else ([], [])
        # Retain the exact existing proof/body identity, but do not assert an
        # old archived document supports the newly fetched statistical values.
        for proof in deepcopy(evidence):
            proof.metadata = {**proof.metadata, "purpose": "company_profile_inherited_archive_context"}
            ingest.evidence.append(proof)
        document_status = ("validated_derivation_original_documents" if derived is not None
                           else "inherited_original_documents" if evidence else "missing_original_documents")
        if evidence:
            ingest.query_manifest["research_archive"] = {"version": "company-profile-inherited-archive-v1",
                "text_extractions": deepcopy(bindings), "document_inventory": "available",
                "research_completion": "not_assessed", "raw_enrichment": "not_run",
                "inherited_from": current["originals"][dimension],
                "documents": sorted({proof.content_hash for proof in evidence})}
        native_result = project_native_result(_view(ingest), version="uzi-dim-result-v3")
        if dimension == "9_futures":
            material = material_snapshot or (
                repository.require_snapshot(packet["materials_input"]["snapshot_id"]) if packet["materials_input"] else None)
            if material is None:
                raise CompanyProfileRefusal("materials_dependency_unbound")
            material_raw = project_native_result(material, version="uzi-dim-result-v3")
            ingest.query_manifest["company_profile_materials_input"] = {
                **_identity(material), "raw_result_hash": canonical_hash(material_raw)}
        issues = _upstream_issues(native_result, packet["native_basic"])
        ingest.query_manifest["company_profile_rebuild"] = {"version": VERSION,
            "job_id": job.id, "original_snapshot": packet["originals"].get(dimension),
            "input_mode": "historical_pit" if day != _today(zone) else "current_day",
            "consumer_mode": "native_archive_derivation" if derived is not None else "source_adapter",
            "document_status": document_status, "known_upstream_issues": issues,
            "research_completion": "not_assessed", "dependency_attestation_only": True}
        usable = native_snapshot_usable(_view(ingest))
        # An executed native failure is still an exact dependency execution.
        # Receipt integrity and native availability are independent decisions.
        ingest.query_manifest["company_profile_dependency_receipt"] = {
            "version": DEPENDENCY_RECEIPT_VERSION,
            "input_basic_snapshot_id": packet["basic"]["snapshot_id"],
            "input_basic_snapshot_hash": packet["basic"]["snapshot_hash"],
            "dependency_fingerprint": packet["fingerprint"],
            "raw_result_hash": canonical_hash(native_result)}
        snapshot = SnapshotIngestionService(repository, registry=registry).ingest(ingest)
        if derived is not None:
            _, _, validate_derived = _historical_derivation_tools(dimension)
            validate_derived(repository, snapshot, derived)
        if _snapshot_pit_visibility_errors(repository, snapshot, cutoff):
            raise CompanyProfileRefusal("rebuild_pit_invalid")
        if project_native_result(snapshot, version="uzi-dim-result-v3") != native_result:
            raise CompanyProfileRefusal("persisted_native_projection_changed")
        if usable and not native_snapshot_usable(snapshot):
            raise CompanyProfileRefusal("persisted_native_availability_changed")
        status = "succeeded" if usable else "degraded"
        now = datetime.now(timezone.utc)
        retryable = not usable
        error_code = None if usable else "native_rebuild_data_gap"
        error_message = None if usable else "原生重建响应不可用；已保留原始错误及依赖执行绑定。"
        repository.session.add(MarketDataCollectionTaskResult(task_id=task.id, as_of_date=day,
            attempt=attempt, status=status, provider_status=snapshot.status, snapshot_id=snapshot.id,
            retryable=retryable, error_code=error_code, error_message=error_message,
            metrics_json={"stage": VERSION, "document_status": document_status, "known_upstream_issues": issues,
                "dependency_receipt_written": True}, completed_at=now))
        task.attempt_count = attempt
        # Result quality has a degraded state; the collection task lifecycle
        # does not. Match the coordinator's terminal mapping and retry policy,
        # without carrying stale flags from an earlier successful attempt.
        task.status = CollectionStatus.SUCCEEDED.value if usable else CollectionStatus.PARTIAL.value
        task.updated_at, task.completed_at = now, now
        task.retryable = retryable
        task.next_retry_at = (now + timedelta(seconds=min(60, 2**attempt))
                              if retryable and attempt < task.max_attempts else None)
        task.lease_owner, task.lease_until = None, None
        task.last_error_code, task.last_error_message = error_code, error_message
        task.checkpoint_json = {**dict(task.checkpoint_json or {}), "company_profile_dependency_rebuild": {
            "snapshot_id": snapshot.id, "snapshot_hash": snapshot.content_hash, "attempt": attempt}}
        revisions.append({"dimension": dimension, "snapshot_id": snapshot.id, "snapshot_hash": snapshot.content_hash,
            "task_id": task.id, "attempt": attempt, "status": status, "document_status": document_status,
            "known_upstream_issues": issues})
        if dimension == "8_materials":
            material_snapshot = snapshot
    repository.session.flush()
    _job(repository.session, job.id, write_fence, cancellation)
    return revisions


def run_company_profile_dependency_rebuild(database, *, job_id, registry, collector, deadline,
        settings=None, provider_registry=None, cancellation=None, write_fence=None, progress_callback=None,
        native_recompute=None, task_ids=()):
    """Use only explicit task authority and the existing shared stage deadline."""
    cancellation = _DeadlineCancellation(cancellation, deadline)
    result = {"version": VERSION, "status": "not_required", "scopes": [], "revisions": [], "recomputations": [],
              "report_delivery_assessed": False}
    try:
        with database.session() as session:
            job = _job(session, job_id, write_fence, cancellation)
            scopes = _scopes(session, job_id, task_ids)
            max_workers = max(1, min(5, job.max_concurrency))
        for scope, requested in scopes.items():
            _stop(cancellation)
            detail = {"symbol": scope[0], "as_of_date": scope[3].isoformat(), "status": "pending"}
            try:
                with database.session() as session:
                    _job(session, job_id, write_fence, cancellation)
                    packet = _packet(MarketDataRepository(session), registry, scope, requested)
                if packet is None:
                    continue
                result["scopes"].append(detail)
                result["status"] = "running"
                detail.update(input_basic_snapshot_id=packet["basic"]["snapshot_id"],
                    input_basic_snapshot_hash=packet["basic"]["snapshot_hash"],
                    dependency_fingerprint=packet["fingerprint"], required_dimensions=packet["check"]["required_dimensions"],
                    pending_dimensions=packet["check"]["pending_dimensions"], reused_snapshot_ids=packet["reused_snapshot_ids"],
                    reused_inputs=deepcopy(packet["reused_inputs"]),
                    implemented_dimensions=list(SUPPORTED), requested_dimensions=sorted(requested),
                    unimplemented_dimensions=[key for key in packet["check"]["required_dimensions"] if key not in SUPPORTED],
                    not_requested_dimensions=[key for key in packet["check"]["required_dimensions"] if key not in requested])
                detail["blocked_dimensions"] = deepcopy(packet["blocked_dimensions"])
                if scope[1] != "CN_EQUITY":
                    detail.update(status="missing_dependency", reason_code="profile_dependency_market_unsupported")
                elif packet["targets"]:
                    if provider_registry is None and settings is not None:
                        from research_system.adapters.vendors.provider_registry import (
                            VendorProviderRegistry,
                        )
                        provider_registry = VendorProviderRegistry(settings)
                    _stop(cancellation)
                    basic_input = deepcopy(packet["native_basic"])
                    materials_input = deepcopy(packet["fixed_materials"])
                    historical_options = {}
                    derivations = {}
                    if scope[3] != _today(scope[2]):
                        from research_system.market_data.fixed_pit_inputs import (
                            bind_pit_input,
                        )
                        cutoff = cutoff_at_for_date(scope[3], scope[2])
                        historical_options = {"as_of_date": scope[3], "cutoff_at": cutoff}
                        basic_input = bind_pit_input(basic_input, symbol=scope[0], dimension="0_basic",
                            day=scope[3], cutoff=cutoff, identity=packet["basic"], **packet["profile_basis"])
                        if materials_input is not None:
                            materials_input = bind_pit_input(materials_input, symbol=scope[0], dimension="8_materials",
                                day=scope[3], cutoff=cutoff, identity=packet["materials_input"])
                        derivations = _prepare_historical_derivations(database, registry=registry, job_id=job_id,
                            scope=scope, requested=requested, packet=packet, cancellation=cancellation)
                        detail["historical_derivations"] = {key: {"status": value["status"],
                            "reason_code": value.get("reason_code")} for key, value in derivations.items()}
                        detail["blocked_dimensions"].update({key: value.get("reason_code") or value["status"]
                            for key, value in derivations.items() if value["status"] != "completed"})
                    detail["input_mode"] = "historical_pit" if historical_options else "current_day"
                    source_targets = [key for key in packet["targets"] if key not in derivations]
                    started = monotonic()
                    try:
                        rows = collector(query=f"{scope[0]} 显式公司身份依赖重建", symbols=[scope[0]],
                            snapshot_types=[SUPPORTED[key] for key in source_targets],
                            research_profile="medium", max_workers=max_workers, provider_registry=provider_registry,
                            fixed_basic_by_symbol={scope[0]: basic_input},
                            uzi_root_route=False, cancellation=cancellation,
                            **historical_options,
                            **({"fixed_materials_by_symbol": {scope[0]: materials_input}}
                               if materials_input is not None else {})) if source_targets else []
                    finally:
                        detail["collector_elapsed_seconds"] = max(0, monotonic() - started)
                    _stop(cancellation)
                    for row in rows:
                        if isinstance(row, dict) and (row.get("dependency_execution") or {}).get("status") == "not_run":
                            detail["blocked_dimensions"].update({key: row["dependency_execution"]["reason_code"]
                                for key in packet["targets"] if SUPPORTED[key] == row.get("snapshot_type")})
                    with database.session() as session:
                        job = _job(session, job_id, write_fence, cancellation)
                        revisions = _persist(MarketDataRepository(session), registry=registry, job=job, scope=scope,
                            requested=requested, packet=packet, rows=rows, cancellation=cancellation, write_fence=write_fence,
                            derivations=derivations)
                    result["revisions"].extend(revisions)
                    detail["new_snapshot_ids"] = [row["snapshot_id"] for row in revisions]
                with database.session() as session:
                    check = check_company_profile_dependencies(_selected(MarketDataRepository(session), registry, scope))
                detail["pending_dimensions"] = check["pending_dimensions"]
                if "reason_code" not in detail:
                    detail["status"] = "pending" if check["pending_dimensions"] else "dependency_bound"
                    if check["status"] == "ready":
                        recomputed = _recompute_fixed(database, registry=registry, scope=scope, job_id=job_id,
                            expected_basic=packet["basic"], cancellation=cancellation,
                            write_fence=write_fence, native_recompute=native_recompute)
                        result["recomputations"].append({"symbol": scope[0], "as_of_date": scope[3].isoformat(), **recomputed})
            except Exception as exc:  # noqa: BLE001 - no provider text/URLs/settings in diagnostics
                if detail not in result["scopes"]:
                    result["scopes"].append(detail)
                reason = exc.code if isinstance(exc, ResearchTextError) else getattr(exc, "reason", "dependency_rebuild_failed")
                detail.update(status="missing_dependency", reason_code=reason, error_type=type(exc).__name__)
                if reason in {"cancelled", "deadline_exceeded", "worker_lease_lost"}:
                    raise CompanyProfileRefusal(reason) from None
            if progress_callback is not None:
                progress_callback(deepcopy(result))
        if result["scopes"]:
            disclosed_inputs = [*result["revisions"],
                *(row for scope in result["scopes"] for row in scope.get("reused_inputs", []))]
            result["status"] = "partial" if any(row["status"] != "dependency_bound" for row in result["scopes"]) or any(
                row["document_status"] == "missing_original_documents" or row["known_upstream_issues"] for row in disclosed_inputs) or any(
                row["status"] != "completed" for row in result["recomputations"]) else "completed"
    except CompanyProfileRefusal as exc:
        result["status"] = exc.reason
    if progress_callback is not None and result["scopes"]:
        progress_callback(deepcopy(result))
    return result
