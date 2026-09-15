"""One historical moat dimension, derived from already persisted PIT inputs.

The collection worker owns authorization, deadline and transaction fencing.
This module never fetches documents, invokes a model, publishes a Bundle, or
changes an existing snapshot. Native computation lives in native_moat only.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
from hashlib import sha256
from time import monotonic

from research_system.market_data import native_moat
from research_system.market_data.contracts import (
    EvidenceGrade,
    EvidenceInput,
    canonical_hash,
    cutoff_at_for_date,
)
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

VERSION = "pit-moat-collection-v1"
MAX_DOCUMENTS = 8


class MoatDependencyError(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def _scope(spec, as_of):
    return {"job_id": spec["job_id"], "task_id": spec["id"],
            "symbol": spec["symbol"], "market": spec["market"],
            "exchange_timezone": spec["exchange_timezone"], "as_of_date": as_of.isoformat()}


def _identity(snapshot):
    return {"snapshot_id": snapshot.id, "content_hash": snapshot.content_hash,
            "dimension": snapshot.dimension, "as_of_date": snapshot.as_of_date.isoformat()}


def _input_ok(repository, snapshot, scope, cutoff):
    return bool(snapshot and snapshot.revoked_at is None and snapshot.status != "revoked"
                and snapshot.symbol == scope["symbol"] and snapshot.market == scope["market"]
                and snapshot.as_of_date <= date.fromisoformat(scope["as_of_date"])
                and not _snapshot_pit_visibility_errors(repository, snapshot, cutoff))


def _text_bindings(snapshot, evidence):
    """Authorize text by the selected snapshot, never by sidecar discovery."""
    archive = (snapshot.query_manifest_json or {}).get("research_archive", {})
    bindings = archive.get("text_extractions", []) if isinstance(archive, dict) else None
    if not isinstance(bindings, list) or any(
        not isinstance(row, dict) or not isinstance(row.get("evidence_key"), str)
        or not isinstance(row.get("binding"), dict) for row in bindings
    ):
        raise MoatDependencyError("archive_text_binding_set_invalid")
    expected = {row["evidence_key"]: row["binding"] for row in bindings}
    if len(expected) != len(bindings) or not set(expected).issubset({row.evidence_key for row in evidence}):
        raise MoatDependencyError("archive_text_binding_evidence_missing_or_duplicated")
    return expected


def _document_text(evidence, binding):
    if binding is None:
        return evidence.inline_content
    try:
        return verified_text_projection(evidence, binding)["content"]
    except ResearchTextError as exc:
        raise MoatDependencyError(exc.code) from None


def _select_document_representations(documents, *, binding_key, error_type):
    """One verified full text per original/URL; leave legacy inputs intact.

    Call only after source/PIT/text verification and before the corpus limit.
    Receipts distinguish candidate lineage from the one native-consumed body.
    """
    groups, complete_hashes = {}, {}
    for document in documents:
        digest = document["document_hash"]
        groups.setdefault((digest, document["url"]), []).append(document)
        if document.get(binding_key) is not None:
            complete_hashes.setdefault(digest, set()).add(document["text_hash"])
    if any(len(hashes) != 1 for hashes in complete_hashes.values()):
        raise error_type("archive_text_representation_conflict")
    replacements, receipts = {}, []

    def identity(row):
        return {key: row[key] for key in ("snapshot_id", "evidence_id", "text_hash")}

    for (digest, url), rows in groups.items():
        complete = sorted((row for row in rows if row.get(binding_key) is not None),
                          key=lambda row: (row["evidence_id"], row["snapshot_id"]))
        if not complete:
            continue
        # URL relationships are inputs to native source-policy filtering.
        # Never discard an official URL in favor of a same-body republication.
        replacements[(digest, url)] = complete[0]
        receipts.append({"document_hash": digest, "url": url, "selected": identity(complete[0]),
            "equivalent_full_text_inputs": [identity(row) for row in complete[1:]],
            "superseded_inline_inputs": [identity(row) for row in rows if row.get(binding_key) is None]})
    selected, emitted = [], set()
    for document in documents:
        key = (document["document_hash"], document["url"])
        if key not in replacements:
            selected.append(document)
        elif key not in emitted:
            selected.append(replacements[key])
            emitted.add(key)
    return selected, receipts


def _limit_representation_inputs(packet, snapshots, *, binding_key, error_type, maximum):
    documents, receipts = _select_document_representations(
        packet["documents"], binding_key=binding_key, error_type=error_type)
    packet["documents"] = documents[:maximum]
    selected_sources = {(row["document_hash"], row["url"]) for row in packet["documents"]}
    receipts = [row for row in receipts if (row["document_hash"], row["url"]) in selected_sources]
    if receipts:
        packet["document_representation_selection"] = receipts
    lineage = [row for receipt in receipts for row in
               [receipt["selected"], *receipt["equivalent_full_text_inputs"], *receipt["superseded_inline_inputs"]]]
    for row in [*packet["documents"], *lineage]:
        if not any(item["snapshot_id"] == row["snapshot_id"] for item in packet["inputs"]):
            packet["inputs"].append(_identity(snapshots[row["snapshot_id"]]))


def prepare_moat_derivation(database, *, spec, as_of_date, registry, deadline_at, cancellation):
    """Freeze existing dependencies and replay outside a database transaction."""
    scope = _scope(spec, as_of_date)
    packet = {"version": VERSION, "scope": scope, "inputs": [], "documents": [],
              "status": "missing_dependency", "reason_code": "moat_basic_dependency_missing",
              "deadline_at": deadline_at}
    if cancellation.is_set() or monotonic() >= deadline_at:
        return {**packet, "status": "cancelled" if cancellation.is_set() else "timed_out"}
    cutoff = cutoff_at_for_date(as_of_date, scope["exchange_timezone"])
    with database.session() as session:
        repo = MarketDataRepository(session)
        coverage = SnapshotResolver(repo, registry=registry).coverage(
            scope["symbol"], scope["market"], as_of_date,
            exchange_timezone=scope["exchange_timezone"],
        )
        chosen = {row.dimension: repo.require_snapshot(row.snapshot_id)
                  for row in coverage.dimensions if row.snapshot_id}
        original = chosen.get("14_moat")
        if _input_ok(repo, original, scope, cutoff):
            packet["original"] = _identity(original)
        basic = chosen.get("0_basic")
        if not _input_ok(repo, basic, scope, cutoff):
            return packet
        raw = project_native_result(basic).get("data") or {}
        name = str(raw.get("name") or raw.get("short_name") or "").strip()
        if not name:
            return packet
        packet["basic"] = deepcopy(raw)
        packet["basic"]["name"] = name
        packet["inputs"].append(_identity(basic))
        candidates = []
        for dimension, snapshot in sorted(chosen.items()):
            if not _input_ok(repo, snapshot, scope, cutoff):
                continue
            proofs = repo.list_snapshot_evidence(snapshot.id)
            bindings = _text_bindings(snapshot, proofs)
            for evidence in proofs:
                candidates.append((snapshot, evidence, bindings.get(evidence.evidence_key)))
        # The same evidence may also belong to an older, inline-only snapshot.
        # Prefer its explicitly bound selected revision without changing the
        # order or bytes of legacy-only inputs.
        bound_ids = {evidence.id for _, evidence, binding in candidates if binding is not None}
        seen = set()
        for snapshot, evidence, binding in candidates:
            if evidence.id in seen or (binding is None and evidence.id in bound_ids):
                continue
            if _document_error(evidence, cutoff):
                continue
            text = _document_text(evidence, binding)
            # Bind company-specific archived material, not an unrelated
            # macro/industry page that merely shares a snapshot scope.
            if name not in f"{evidence.title or ''} {text}" and scope["symbol"].split(".")[0] not in text:
                continue
            seen.add(evidence.id)
            document = {
                "snapshot_id": snapshot.id, "evidence_id": evidence.id,
                "document_hash": evidence.content_hash,
                "text_hash": sha256(text.encode()).hexdigest(),
                "title": evidence.title or "", "url": evidence.source_url,
                # Existing replay transport field; DB inline stays unchanged.
                "inline_content": text,
            }
            if binding is not None:
                document["text_extraction"] = deepcopy(binding)
            packet["documents"].append(document)
            if not bound_ids and len(packet["documents"]) >= MAX_DOCUMENTS:
                break
        _limit_representation_inputs(packet, {row.id: row for row in chosen.values()},
            binding_key="text_extraction", error_type=MoatDependencyError, maximum=MAX_DOCUMENTS)
        packet["document_selection"] = {
            "scope": "research_documents_of_same_symbol_selected_pit_snapshots",
            "maximum_documents": MAX_DOCUMENTS,
            "selected_document_count": len(packet["documents"]),
            "selection_limit_reached": len(packet["documents"]) >= MAX_DOCUMENTS,
            "may_have_unexamined_documents": len(packet["documents"]) >= MAX_DOCUMENTS,
            "complete_archive_coverage_claimed": False,
        }
    if not packet["documents"]:
        return {**packet, "reason_code": "moat_archive_dependency_missing"}
    recording = native_moat.archive_recording(
        symbol=scope["symbol"], basic=packet["basic"], documents=packet["documents"],
    )
    result = native_moat.replay_moat(recording, deadline_at=deadline_at, cancellation=cancellation)
    return {**packet, "recording": recording, "replay": result,
            "status": result["status"], "reason_code": None}


def moat_derivation_ingest(repository, *, packet, task, as_of_date, registry):
    """Build an ingest inside _process_task's already-fenced transaction.

    The caller ingests and links the result under the same fence/savepoint.
    This function has no standalone write authority.
    """
    expected = _scope({"id": task.id, "job_id": task.job_id, "symbol": task.symbol,
                       "market": task.market, "exchange_timezone": task.exchange_timezone}, as_of_date)
    if task.dimension != "14_moat" or packet["scope"] != expected:
        raise MoatDependencyError("moat_task_scope_mismatch")
    adapter = UziSnapshotAdapter(registry=registry)
    common = {"symbol": task.symbol, "market": task.market,
              "exchange_timezone": task.exchange_timezone, "dimension": "14_moat",
              "as_of_date": as_of_date, "collection_task_id": task.id,
              "provider_attempt_id": f"{VERSION}:{task.id}:{task.attempt_count}"}
    identities = {row["snapshot_id"]: row for row in packet["inputs"]}
    if packet.get("original"):
        identities[packet["original"]["snapshot_id"]] = packet["original"]
    locked = {row.id: row for row in repository.lock_snapshots_for_update(identities)}
    cutoff = cutoff_at_for_date(as_of_date, task.exchange_timezone)
    for identity in identities.values():
        row = locked.get(identity["snapshot_id"])
        if not _input_ok(repository, row, expected, cutoff) or row.content_hash != identity["content_hash"]:
            raise MoatDependencyError("moat_input_changed_or_pit_invalid")
        if row.dimension != identity["dimension"] or row.as_of_date.isoformat() != identity["as_of_date"]:
            raise MoatDependencyError("moat_input_scope_mismatch")
    original = locked.get((packet.get("original") or {}).get("snapshot_id"))
    if packet["status"] != "completed":
        reason = packet.get("reason_code") or f"moat_native_{packet['status']}"
        ingest = adapter.disclosed_gap(**common, reason_code=reason,
            message="历史护城河缺少可验证的公司/PIT归档依赖或派生未完成；补入依赖后可显式定向重试，当前未发起旧素材查询。")
        ingest.query_manifest.update(collection_outcome="unavailable", moat_derivation={
            "version": VERSION, "status": packet["status"], "reason_code": reason,
            "missing_dependency": packet["status"] == "missing_dependency",
            "refresh_on_dependency_change": True, "inputs": packet["inputs"],
            "document_selection": packet.get("document_selection", {}),
        })
        ingest.query_manifest["degradation"].update(retryable=False, refresh_on_dependency_change=True)
    else:
        if monotonic() >= packet["deadline_at"]:
            raise MoatDependencyError("moat_deadline_exceeded_before_write")
        basic_identity = next(row for row in packet["inputs"] if row["dimension"] == "0_basic")
        basic_snapshot = locked[basic_identity["snapshot_id"]]
        actual_basic = deepcopy(project_native_result(basic_snapshot).get("data") or {})
        actual_basic["name"] = str(actual_basic.get("name") or actual_basic.get("short_name") or "").strip()
        if actual_basic != packet["basic"]:
            raise MoatDependencyError("moat_basic_recording_mismatch")
        evidence_inputs, text_extractions = [], []
        for document in packet["documents"]:
            snapshot = locked.get(document["snapshot_id"])
            if snapshot is None:
                raise MoatDependencyError("moat_document_snapshot_not_bound")
            proofs = repository.list_snapshot_evidence(snapshot.id)
            evidence = next((row for row in proofs if row.id == document["evidence_id"]), None)
            if evidence is None or _document_error(evidence, cutoff):
                raise MoatDependencyError("moat_document_invalid_before_write")
            binding = _text_bindings(snapshot, proofs).get(evidence.evidence_key)
            if document.get("text_extraction") != binding:
                raise MoatDependencyError("archive_text_binding_mismatch")
            text = _document_text(evidence, binding)
            if evidence.content_hash != document["document_hash"] or sha256(text.encode()).hexdigest() != document["text_hash"]:
                raise MoatDependencyError("moat_document_hash_mismatch")
            if document["inline_content"] != text or document["url"] != evidence.source_url or document["title"] != (evidence.title or ""):
                raise MoatDependencyError("moat_document_recording_mismatch")
            evidence_inputs.append(EvidenceInput(
                provider=evidence.provider, source_url=evidence.source_url,
                source_kind=evidence.source_kind, title=evidence.title,
                mime_type=evidence.mime_type, content_hash=evidence.content_hash,
                content_ref=evidence.content_ref, inline_content=evidence.inline_content,
                published_at=_database_utc(evidence.published_at), observed_at=_database_utc(evidence.observed_at),
                grade=evidence.grade, immutable_proof=evidence.immutable_proof,
                metadata={**deepcopy(evidence.metadata_json or {}), "purpose": "moat_archive_input",
                          "field_paths": ["raw.web_search_snippets"]},
            ))
            if binding is not None:
                text_extractions.append({"evidence_key": evidence_inputs[-1].evidence_key(),
                                         "binding": deepcopy(binding)})
        replay, recording = packet["replay"], packet["recording"]
        if replay.get("isolation_events") or replay.get("recording_hash") != canonical_hash(recording):
            raise MoatDependencyError("moat_replay_binding_invalid")
        if replay.get("source_sha256") != native_moat.NATIVE_SOURCE_SHA256:
            raise MoatDependencyError("moat_native_source_mismatch")
        rebuilt = native_moat.archive_recording(symbol=task.symbol, basic=packet["basic"], documents=packet["documents"])
        if canonical_hash(rebuilt) != canonical_hash(recording):
            raise MoatDependencyError("moat_recording_changed")
        native = deepcopy(replay["native_result"])
        now = datetime.now(timezone.utc)
        published = max(_database_utc(basic_snapshot.published_at), *(item.published_at for item in evidence_inputs))
        evidence_inputs.append(EvidenceInput(
            provider=VERSION, source_kind="historical_derivation",
            content_hash=canonical_hash({"inputs": packet["inputs"], "recording_hash": replay["recording_hash"]}),
            published_at=published, observed_at=now, grade=EvidenceGrade.B, immutable_proof=True,
            metadata={"proof_basis": "validated_existing_pit_inputs", "inputs": packet["inputs"],
                      "recording_hash": replay["recording_hash"], "purpose": "derivation_input_receipt"},
        ))
        snapshot = {
            "snapshot_type": "moat", "symbol": task.symbol, "retrieved_at": now.isoformat(),
            "quality_status": "available", "facts": {"_uzi_raw_result": native},
            "source": {"provider_id": VERSION, "source_id": VERSION},
            "source_trace": [{"source_id": VERSION, "status": "available",
                              "actual_source": replay["actual_source"]}],
            "historical_reconstruction": {"safe": True, "requested_as_of_date": as_of_date.isoformat(),
                "recoverable_fields": ["factors", "research_and_development"], "current_only_fields": [],
                "proof_basis": "persisted_original_documents", "window_start": as_of_date.isoformat()},
        }
        ingest = adapter.adapt(snapshot, **{key: value for key, value in common.items() if key != "dimension"})
        # Use verified original documents, not the adapter's synthesized
        # source-bounded-query proof, for this no-query archive derivation.
        ingest.evidence = evidence_inputs
        ingest.origin = "derived"
        ingest.source_version = native_moat.VERSION
        ingest.source_priority = max(0, original.source_priority - 1) if original else 9
        ingest.published_at = published
        if text_extractions:
            ingest.query_manifest["research_archive"] = {"text_extractions": text_extractions}
        data = native.get("data") or {}
        ingest.query_manifest["moat_derivation"] = {
            "version": VERSION, "status": "completed", "inputs": packet["inputs"],
            "original": packet.get("original"), "recording": recording,
            "document_selection": packet.get("document_selection", {}),
            "recording_hash": replay["recording_hash"], "source_sha256": replay["source_sha256"],
            "native_output_hash": canonical_hash(native), "actual_source": replay["actual_source"],
            "calls": replay.get("calls", []), "isolation_events": replay.get("isolation_events", []),
            "factor_evidence": deepcopy(data.get("scores_evidence") or {}),
            "rd_evidence_present": bool(data.get("rd_summary") and data["rd_summary"] != "—"),
            "coverage_note": "Missing factor evidence is disclosed; native partial-evidence scores are unchanged.",
        }
        ingest.field_sources.update({"raw.web_search_snippets": {
            "recording_hash": replay["recording_hash"], "evidence_ids": [row["evidence_id"] for row in packet["documents"]],
        }})
    if original:
        payload = original.payload_json or {}
        envelope = payload.get("_uzi_collector_original_result", payload.get("_uzi_raw_result"))
        if envelope is not None:
            ingest.payload["_uzi_collector_original_result"] = deepcopy(envelope)
        ingest.query_manifest["moat_derivation"]["original"] = packet["original"]
    if packet.get("document_representation_selection"):
        ingest.query_manifest["moat_derivation"]["document_representation_selection"] = deepcopy(packet["document_representation_selection"])
    return ingest


def validate_persisted_moat(repository, snapshot, packet):
    cutoff = cutoff_at_for_date(snapshot.as_of_date, snapshot.exchange_timezone)
    if _snapshot_pit_visibility_errors(repository, snapshot, cutoff):
        raise MoatDependencyError("moat_output_pit_invalid")
    if packet["status"] == "completed":
        expected = packet["replay"]["native_result"]
        if snapshot.payload_json.get("_uzi_raw_result") != expected:
            raise MoatDependencyError("moat_native_envelope_mismatch")
        projected = project_native_result(snapshot)
        # Projection may add only the established host disclosure/quality
        # namespaces. Every original field, including absent error/_timeout,
        # must otherwise remain exact; actual_source stays in the receipt.
        if any(key not in projected or projected[key] != value for key, value in expected.items()) or (
            set(projected) - set(expected) - {"_pipeline", "_market_data_gap"}
        ):
            raise MoatDependencyError("moat_native_projection_mismatch")
