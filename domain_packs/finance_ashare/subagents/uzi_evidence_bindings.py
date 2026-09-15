"""Bind report citations to immutable archive metadata, never to wall time."""
from __future__ import annotations

from datetime import datetime
from typing import Any


def citation_validation_context(evidence_bundle: dict, stock_documents=()) -> dict:
    records = [record for group in evidence_bundle.values()
               for record in group.get("evidence_records", [])]
    bindings = {}
    for record in [*records, *stock_documents]:
        for binding in record.get("archive_bindings", []):
            # Only explicit document bindings qualify; a nearby snapshot date,
            # publication date, or a bare URL cannot supply retrieval metadata.
            row = {key: binding.get(key) for key in
                   ("evidence_id", "url", "observed_at", "content_hash")}
            if row["evidence_id"] and row["url"] and row["content_hash"]:
                bindings[tuple(row.values())] = row
    return {"frozen_citation_bindings": list(bindings.values())}


def bind_citation(value: Any, context: Any) -> Any:
    if not isinstance(value, dict):
        return value
    bindings = context.get("frozen_citation_bindings", []) if isinstance(context, dict) else []
    evidence_id, url = value.get("evidence_id"), value.get("url")
    if evidence_id:
        candidates = [row for row in bindings if row["evidence_id"] == evidence_id]
        if not candidates:
            # evidence_id is a host-only hint, not a native required field.
            # A mistyped hint must not reject an otherwise unique frozen URL.
            # Still never accept an unknown URL or pick a different version.
            candidates = [row for row in bindings if url and row["url"] == url]
            if not candidates:
                raise ValueError("qualitative_archive_binding_unknown")
            evidence_id = None
    else:
        candidates = [row for row in bindings if url and row["url"] == url]
        if not candidates:
            return value  # Existing unknown/non-archive and URL checks remain.
    # ID conflicts are never resolved by choosing a date or the latest row.
    if evidence_id and len(candidates) != 1:
        raise ValueError("qualitative_archive_binding_conflict")
    supplied = value.get("retrieved_at")
    if len(candidates) != 1 and supplied is not None:
        # Legacy full citations can disambiguate by their actual archive time,
        # but never by publication time or the host's current time.
        candidates = [row for row in candidates if _same_observation(supplied, row["observed_at"])]
    identities = {(row["url"], row["observed_at"], row["content_hash"]) for row in candidates}
    if len(identities) != 1:
        raise ValueError("qualitative_archive_binding_ambiguous")
    binding = candidates[0]
    observed = binding["observed_at"]
    # Once the document/version is unambiguous, model-copied metadata has no
    # authority. Join it from the archive instead of spending a model repair
    # on URL/date transcription. Never change finding or the frozen record.
    result = {**value, "evidence_id": binding["evidence_id"] if len(candidates) == 1 else None,
              "url": binding["url"], "retrieved_at": observed}
    if isinstance(context.get("citation_binding_audit"), list):
        context["citation_binding_audit"].append({
            "evidence_id": result["evidence_id"], "content_hash": binding["content_hash"],
            "filled_fields": [key for key in ("evidence_id", "url", "retrieved_at")
                              if value.get(key) is None and result.get(key) is not None],
            "corrected_fields": [key for key in ("evidence_id", "url", "retrieved_at")
                                 if value.get(key) is not None and value[key] != result[key]],
        })
    return result


def _same_observation(supplied: Any, observed: Any) -> bool:
    if not isinstance(supplied, str) or not isinstance(observed, str):
        return False
    if supplied in (observed, observed[:10]):
        return True
    try:
        left, right = (datetime.fromisoformat(value.replace("Z", "+00:00")) for value in (supplied, observed))
        return left.tzinfo is not None and right.tzinfo is not None and left == right
    except ValueError:
        return False
