"""Historical macro revision from persisted PIT originals, without acquisition.

Preparation freezes dependencies and runs native main outside a transaction.
Ingest construction belongs inside the collection worker's existing fence and
savepoint. It neither writes independently nor publishes Bundles or Manifests.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from time import monotonic

from research_system.market_data import native_macro
from research_system.market_data.contracts import (
    EvidenceGrade,
    EvidenceInput,
    canonical_hash,
    cutoff_at_for_date,
)
from research_system.market_data.moat_derivation import (
    _identity,
    _input_ok,
    _limit_representation_inputs,
    _scope,
)
from research_system.market_data.models import MarketDataCollectionJob
from research_system.market_data.native_projection import project_native_result
from research_system.market_data.repository import MarketDataRepository
from research_system.market_data.research_archive_text import (
    ResearchTextError,
    verified_text_projection,
)
from research_system.market_data.research_enrichment import _document_error
from research_system.market_data.resolver import (
    SnapshotResolver,
    _database_utc,
    _snapshot_pit_visibility_errors,
)
from research_system.market_data.uzi_adapter import UziSnapshotAdapter

VERSION = "pit-macro-collection-v1"
MAX_DOCUMENTS = 8
WINDOW_DAYS = 365


class MacroDependencyError(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def _document_ok(evidence, cutoff):
    return not _document_error(evidence, cutoff) and _database_utc(evidence.published_at) >= cutoff - timedelta(days=WINDOW_DAYS)


def _bound_text(snapshot, evidence):
    """New explicit full-text binding or the unchanged legacy inline excerpt."""
    entries = (snapshot.query_manifest_json or {}).get("research_archive", {}).get("text_extractions", [])
    if not isinstance(entries, list) or any(not isinstance(entry, dict)
            or not isinstance(entry.get("evidence_key"), str) or not isinstance(entry.get("binding"), dict)
            for entry in entries):
        raise ResearchTextError("archive_text_binding_invalid")
    selected = [entry["binding"] for entry in entries if entry.get("evidence_key") == evidence.evidence_key]
    if not selected:
        return evidence.inline_content, None
    if len(selected) != 1:
        raise ResearchTextError("archive_text_binding_duplicate")
    projected = verified_text_projection(evidence, selected[0])
    return projected["content"], projected["text_extraction"]


def prepare_macro_derivation(database, *, spec, as_of_date, registry, deadline_at, cancellation):
    """Read only existing same-stock PIT snapshot bindings; never fetch sources."""
    scope = _scope(spec, as_of_date)
    packet = {"version": VERSION, "scope": scope, "inputs": [], "documents": [],
              "status": "missing_dependency", "reason_code": "macro_basic_dependency_missing",
              "deadline_at": deadline_at, "_cancellation": cancellation, "document_rejections": []}
    if cancellation.is_set() or monotonic() >= deadline_at:
        return {**packet, "status": "cancelled" if cancellation.is_set() else "timed_out"}
    cutoff = cutoff_at_for_date(as_of_date, scope["exchange_timezone"])
    with database.session() as session:
        repo = MarketDataRepository(session)
        coverage = SnapshotResolver(repo, registry=registry).coverage(
            scope["symbol"], scope["market"], as_of_date, exchange_timezone=scope["exchange_timezone"])
        chosen = {row.dimension: repo.require_snapshot(row.snapshot_id)
                  for row in coverage.dimensions if row.snapshot_id}
        original = chosen.get("3_macro")
        if _input_ok(repo, original, scope, cutoff):
            packet["original"] = _identity(original)
        basic = chosen.get("0_basic")
        if not _input_ok(repo, basic, scope, cutoff):
            return packet
        packet["basic"] = deepcopy(project_native_result(basic).get("data") or {})
        packet["inputs"].append(_identity(basic))
        seen = set()
        # Public macro publications need not contain the company's name. Their
        # existing snapshot binding, not a newly widened URL set, fixes scope.
        priority = {"3_macro": 0, "13_policy": 1, "7_industry": 2}
        candidates = [(dimension, snapshot) for dimension, snapshot in
                      sorted(chosen.items(), key=lambda pair: (priority.get(pair[0], 3), pair[0]))
                      if _input_ok(repo, snapshot, scope, cutoff)]
        evidence_by_snapshot = {snapshot.id: sorted(repo.list_snapshot_evidence(snapshot.id), key=lambda row: row.id)
                                for _, snapshot in candidates}
        explicit_owners = {}
        for _, snapshot in candidates:
            bound_keys = {row.evidence_key for row in evidence_by_snapshot[snapshot.id]}
            bindings = (snapshot.query_manifest_json or {}).get("research_archive", {}).get("text_extractions", [])
            for binding in bindings if isinstance(bindings, list) else []:
                if isinstance(binding, dict) and binding.get("evidence_key") in bound_keys:
                    explicit_owners.setdefault(binding["evidence_key"], set()).add(snapshot.id)
        for dimension, snapshot in candidates:
            for evidence in evidence_by_snapshot[snapshot.id]:
                # The same immutable proof may be bound by a legacy snapshot
                # and a newer explicit full-text revision. Prefer that explicit
                # owner for this NEW derivation; never upgrade the legacy row.
                if evidence.evidence_key in explicit_owners and snapshot.id not in explicit_owners[evidence.evidence_key]:
                    continue
                if evidence.id in seen or not _document_ok(evidence, cutoff):
                    continue
                try:
                    text, text_binding = _bound_text(snapshot, evidence)
                except ResearchTextError as exc:
                    # A declared full version cannot silently fall back to an
                    # older inline proof of the same original document.
                    raise MacroDependencyError(exc.code) from None
                seen.add(evidence.id)
                packet["documents"].append({
                    "snapshot_id": snapshot.id, "evidence_id": evidence.id,
                    "document_hash": evidence.content_hash, "text_hash": sha256(text.encode()).hexdigest(),
                    "title": evidence.title or "", "url": evidence.source_url, "inline_content": text,
                    "inline_content_hash": sha256(evidence.inline_content.encode()).hexdigest(), "text_binding": text_binding,
                    "published_at": _database_utc(evidence.published_at).isoformat(),
                })
                if not explicit_owners and len(packet["documents"]) >= MAX_DOCUMENTS:
                    break
            if not explicit_owners and len(packet["documents"]) >= MAX_DOCUMENTS:
                break
        _limit_representation_inputs(packet, {row.id: row for row in chosen.values()},
            binding_key="text_binding", error_type=MacroDependencyError, maximum=MAX_DOCUMENTS)
        packet["document_selection"] = {
            "scope": "research_documents_of_same_symbol_selected_pit_snapshots",
            "publication_window": {"start": (cutoff - timedelta(days=WINDOW_DAYS)).isoformat(), "end": cutoff.isoformat()},
            "maximum_documents": MAX_DOCUMENTS, "selected_document_count": len(packet["documents"]),
            "selection_limit_reached": len(packet["documents"]) >= MAX_DOCUMENTS,
            "may_have_unexamined_documents": len(packet["documents"]) >= MAX_DOCUMENTS,
            "complete_archive_coverage_claimed": False, "historical_web_search_coverage_claimed": False,
        }
    if not packet["documents"]:
        return {**packet, "reason_code": "macro_archive_dependency_missing"}
    recording = native_macro.archive_recording(
        symbol=scope["symbol"], basic=packet["basic"], documents=packet["documents"], as_of_date=as_of_date)
    replay = native_macro.replay_macro(recording, deadline_at=deadline_at, cancellation=cancellation)
    return {**packet, "recording": recording, "replay": replay, "status": replay["status"], "reason_code": None}


def macro_derivation_ingest(repository, *, packet, task, as_of_date, registry):
    """Construct a new revision within the caller's fenced transaction."""
    expected = _scope({"id": task.id, "job_id": task.job_id, "symbol": task.symbol,
                       "market": task.market, "exchange_timezone": task.exchange_timezone}, as_of_date)
    if task.dimension != "3_macro" or packet["scope"] != expected or packet["version"] != VERSION:
        raise MacroDependencyError("macro_task_scope_mismatch")
    cancel = packet.get("_cancellation")
    job = repository.session.get(MarketDataCollectionJob, task.job_id)
    if (cancel is not None and cancel.is_set()) or (job is not None and job.cancel_requested):
        raise MacroDependencyError("macro_cancelled_before_write")
    if monotonic() >= packet["deadline_at"]:
        raise MacroDependencyError("macro_deadline_exceeded_before_write")
    adapter = UziSnapshotAdapter(registry=registry)
    common = {"symbol": task.symbol, "market": task.market, "exchange_timezone": task.exchange_timezone,
              "dimension": "3_macro", "as_of_date": as_of_date, "collection_task_id": task.id,
              "provider_attempt_id": f"{VERSION}:{task.id}:{task.attempt_count}"}
    identities = {row["snapshot_id"]: row for row in packet["inputs"]}
    if packet.get("original"):
        identities[packet["original"]["snapshot_id"]] = packet["original"]
    locked = {row.id: row for row in repository.lock_snapshots_for_update(identities)}
    cutoff = cutoff_at_for_date(as_of_date, task.exchange_timezone)
    for identity in identities.values():
        row = locked.get(identity["snapshot_id"])
        if not _input_ok(repository, row, expected, cutoff) or row.content_hash != identity["content_hash"]:
            raise MacroDependencyError("macro_input_changed_or_pit_invalid")
        if row.dimension != identity["dimension"] or row.as_of_date.isoformat() != identity["as_of_date"]:
            raise MacroDependencyError("macro_input_scope_mismatch")
    original = locked.get((packet.get("original") or {}).get("snapshot_id"))
    if packet["status"] != "completed":
        reason = packet.get("reason_code") or f"macro_native_{packet['status']}"
        ingest = adapter.disclosed_gap(**common, reason_code=reason,
            message="历史宏观缺少可验证 PIT 原文依赖或原生派生未完成；未发起旧素材查询，基准行情不替代宏观证据。")
        ingest.query_manifest.update(collection_outcome="unavailable", macro_derivation={
            "version": VERSION, "status": packet["status"], "reason_code": reason,
            "missing_dependency": packet["status"] == "missing_dependency", "refresh_on_dependency_change": True,
            "inputs": packet["inputs"], "document_selection": packet.get("document_selection", {}),
            "document_rejections": packet.get("document_rejections", []),
        })
        ingest.query_manifest["degradation"].update(retryable=False, refresh_on_dependency_change=True)
    else:
        basic_identity = next(row for row in packet["inputs"] if row["dimension"] == "0_basic")
        basic = locked[basic_identity["snapshot_id"]]
        if deepcopy(project_native_result(basic).get("data") or {}) != packet["basic"]:
            raise MacroDependencyError("macro_basic_recording_mismatch")
        evidence_inputs = []
        for document in packet["documents"]:
            if document["snapshot_id"] not in locked:
                raise MacroDependencyError("macro_document_snapshot_not_bound")
            evidence = next((row for row in repository.list_snapshot_evidence(document["snapshot_id"])
                             if row.id == document["evidence_id"]), None)
            if evidence is None or not _document_ok(evidence, cutoff):
                raise MacroDependencyError("macro_document_invalid_before_write")
            try:
                text, text_binding = _bound_text(locked[document["snapshot_id"]], evidence)
            except ResearchTextError as exc:
                raise MacroDependencyError(exc.code) from None
            if (evidence.content_hash != document["document_hash"]
                    or sha256(text.encode()).hexdigest() != document["text_hash"]
                    or sha256(evidence.inline_content.encode()).hexdigest() != document["inline_content_hash"]):
                raise MacroDependencyError("macro_document_hash_mismatch")
            if (document["inline_content"] != text or document["text_binding"] != text_binding or document["url"] != evidence.source_url
                    or document["title"] != (evidence.title or "")
                    or document["published_at"] != _database_utc(evidence.published_at).isoformat()):
                raise MacroDependencyError("macro_document_recording_mismatch")
            evidence_inputs.append(EvidenceInput(
                provider=evidence.provider, source_url=evidence.source_url, source_kind=evidence.source_kind,
                title=evidence.title, mime_type=evidence.mime_type, content_hash=evidence.content_hash,
                content_ref=evidence.content_ref, inline_content=evidence.inline_content,
                published_at=_database_utc(evidence.published_at), observed_at=_database_utc(evidence.observed_at),
                grade=evidence.grade, immutable_proof=evidence.immutable_proof,
                metadata={**deepcopy(evidence.metadata_json or {}), "purpose": "macro_archive_input",
                          "field_paths": ["raw.web_search_snippets"]}))
        replay, recording = packet["replay"], packet["recording"]
        if (replay.get("status") != "completed" or replay.get("isolation_events") or replay.get("contract_failures")
                or replay.get("recording_hash") != canonical_hash(recording)):
            raise MacroDependencyError("macro_replay_binding_invalid")
        if replay.get("source_sha256") != native_macro.NATIVE_SOURCE_SHA256:
            raise MacroDependencyError("macro_native_source_mismatch")
        rebuilt = native_macro.archive_recording(symbol=task.symbol, basic=packet["basic"],
                                                 documents=packet["documents"], as_of_date=as_of_date)
        if canonical_hash(rebuilt) != canonical_hash(recording):
            raise MacroDependencyError("macro_recording_changed")
        native = deepcopy(replay["native_result"])
        groups = replay["coverage"]
        if replay.get("native_output_hash") != canonical_hash(native):
            raise MacroDependencyError("macro_native_output_hash_mismatch")
        snippets = native.get("data", {}).get("web_search_snippets", {})
        expected_groups = {key: {
            "status": "archive_evidence_present" if snippets.get(key) else "unassessed",
            "evidence_count": len(snippets.get(key, [])), "verified_neutral_fact": False,
            "factual_sentiment_verified": False} for key in native_macro.QUERIES}
        if groups != expected_groups:
            raise MacroDependencyError("macro_group_coverage_mismatch")
        expected_calls = [{"leaf": "trusted" if key in native_macro.TRUSTED_KEYS else "untrusted", "key": key}
                          for key in native_macro.QUERIES]
        if replay.get("calls") != expected_calls:
            raise MacroDependencyError("macro_replay_calls_mismatch")
        missing = [key for key in native_macro.QUERIES if not groups[key]["evidence_count"]]
        count = sum(groups[key]["evidence_count"] for key in native_macro.QUERIES)
        outcome = "unavailable" if not count else "partial" if missing else "available"
        now = datetime.now(timezone.utc)
        published = max(_database_utc(basic.published_at), *(item.published_at for item in evidence_inputs))
        # Corpus dependencies are not automatically supporting evidence. Only
        # documents represented in the rows native actually consumed belong
        # to the new macro support set; keep the full corpus in the receipt.
        supporting_ids = {row["evidence_id"] for query in recording["queries"]
            for row in query["trusted" if query["key"] in native_macro.TRUSTED_KEYS else "untrusted"][:3]}
        supported_pairs = [(doc, item) for doc, item in zip(packet["documents"], evidence_inputs)
                           if doc["evidence_id"] in supporting_ids]
        evidence_inputs = [item for _, item in supported_pairs]
        text_extractions = [{"evidence_key": item.evidence_key(), "binding": deepcopy(doc["text_binding"])}
                            for doc, item in supported_pairs if doc["text_binding"] is not None]
        evidence_inputs.append(EvidenceInput(
            provider=VERSION, source_kind="historical_derivation",
            content_hash=canonical_hash({"inputs": packet["inputs"], "recording_hash": replay["recording_hash"]}),
            published_at=published, observed_at=now, grade=EvidenceGrade.B, immutable_proof=True,
            metadata={"proof_basis": "validated_existing_pit_inputs", "inputs": packet["inputs"],
                      "recording_hash": replay["recording_hash"], "purpose": "derivation_input_receipt"}))
        snapshot = {"snapshot_type": "macro", "symbol": task.symbol, "retrieved_at": now.isoformat(),
            "quality_status": "available" if count else "data_gap", "facts": {"_uzi_raw_result": native},
            "source": {"provider_id": VERSION, "source_id": VERSION},
            "source_trace": [{"source_id": VERSION, "status": "available", "count": count,
                              "actual_source": replay["actual_source"], "request_executed": False,
                              "bounded_query_completed": True}],
            "historical_reconstruction": {"safe": True, "requested_as_of_date": as_of_date.isoformat(),
                "recoverable_fields": ["observations"], "current_only_fields": [],
                "proof_basis": "persisted_original_documents",
                "window_start": (as_of_date - timedelta(days=WINDOW_DAYS)).isoformat(),
                "complete_archive_coverage_claimed": False}}
        ingest = adapter.adapt(snapshot, **{key: value for key, value in common.items() if key != "dimension"})
        ingest.evidence = evidence_inputs
        ingest.origin, ingest.source_version = "derived", native_macro.VERSION
        ingest.source_priority = max(0, original.source_priority - 1) if original else 9
        ingest.published_at = published
        ingest.query_manifest["collection_outcome"] = outcome
        if text_extractions:
            ingest.query_manifest["research_archive"] = {"text_extractions": text_extractions}
        ingest.query_manifest["macro_derivation"] = {
            "version": VERSION, "status": "completed", "inputs": packet["inputs"], "original": packet.get("original"),
            "recording": recording, "document_selection": packet.get("document_selection", {}),
            "document_rejections": packet.get("document_rejections", []),
            "recording_hash": replay["recording_hash"], "source_sha256": replay["source_sha256"],
            "native_output_hash": canonical_hash(native), "actual_source": replay["actual_source"],
            "calls": replay.get("calls", []), "isolation_events": replay.get("isolation_events", []),
            "group_coverage": deepcopy(groups), "unassessed_groups": missing,
            "supporting_document_count": len(supporting_ids), "supporting_evidence_ids": sorted(supporting_ids),
            "refresh_on_dependency_change": bool(missing),
            "coverage_note": "Native heuristic outputs are unchanged. Missing groups are unassessed; native neutral defaults are not observed neutral facts. Bounded archive matches do not prove complete historical search coverage."}
        if missing:
            disclosure = {"reason_code": "macro_archive_groups_unassessed", "missing_groups": missing,
                          "retryable": False, "refresh_on_dependency_change": True,
                          "message": "有限归档未覆盖宏观组：" + "、".join(missing) + "。原生中性默认值不是已观测中性事实；有正文的情绪也只是原生启发式输出。"}
            ingest.query_manifest["partial_disclosure" if count else "degradation"] = disclosure
        ingest.field_sources.update({"raw.web_search_snippets": {
            "recording_hash": replay["recording_hash"], "evidence_ids": sorted(supporting_ids)}})
    if original:
        payload = original.payload_json or {}
        envelope = payload.get("_uzi_collector_original_result", payload.get("_uzi_raw_result"))
        if envelope is not None:
            ingest.payload["_uzi_collector_original_result"] = deepcopy(envelope)
        ingest.query_manifest["macro_derivation"]["original"] = packet["original"]
    if packet.get("document_representation_selection"):
        ingest.query_manifest["macro_derivation"]["document_representation_selection"] = deepcopy(packet["document_representation_selection"])
    # The collection fence also checks cancellation/lease immediately around
    # ingestion. This local check protects a cancellation during validation.
    if cancel is not None and cancel.is_set():
        raise MacroDependencyError("macro_cancelled_before_write")
    if monotonic() >= packet["deadline_at"]:
        raise MacroDependencyError("macro_deadline_exceeded_before_write")
    return ingest


def validate_persisted_macro(repository, snapshot, packet):
    cutoff = cutoff_at_for_date(snapshot.as_of_date, snapshot.exchange_timezone)
    if _snapshot_pit_visibility_errors(repository, snapshot, cutoff):
        raise MacroDependencyError("macro_output_pit_invalid")
    if packet["status"] == "completed":
        expected = packet["replay"]["native_result"]
        if snapshot.payload_json.get("_uzi_raw_result") != expected:
            raise MacroDependencyError("macro_native_envelope_mismatch")
        projected = project_native_result(snapshot)
        if any(key not in projected or projected[key] != value for key, value in expected.items()) or (
                set(projected) - set(expected) - {"_pipeline", "_market_data_gap"}):
            raise MacroDependencyError("macro_native_projection_mismatch")
