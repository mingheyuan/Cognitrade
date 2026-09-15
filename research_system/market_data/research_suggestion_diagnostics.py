"""Non-authoritative, bounded diagnostics for rejected model suggestions.

No quotes, URLs, raw values, validation messages or arbitrary model field names
are persisted. Fingerprints permit identity comparison, not exact wire replay.
"""

import math
import re
from hashlib import sha256

from pydantic import ValidationError

VERSION = "uzi-suggestion-rejection-diagnostics-v1"
_MAX_DIAGNOSTICS = 32  # A group admits at most 24 facts and 3 profile proposals.
_PATH_PARTS = frozenset({
    "snapshot_id", "snapshot_hash", "dimension", "field", "old_exists", "old_value",
    "new_value", "unit", "period_text", "period_start", "period_end", "finding",
    "citations", "classification_citations", "evidence_id", "document_hash", "quote",
    "start", "end", "source_snapshot_id", "source_snapshot_hash", "text_content_hash",
    "industry_basis", "classification_system", "str", "int", "float", "bool", "none",
})
_VALIDATION_CODES = frozenset({
    "extra_forbidden", "missing", "string_type", "int_type", "float_type", "bool_type",
    "none_required", "literal_error", "value_error", "model_type", "dict_type", "list_type",
    "string_too_short", "string_too_long", "string_pattern_mismatch", "too_short", "too_long",
    "date_type", "date_parsing", "date_from_datetime_parsing", "date_from_datetime_inexact",
    "greater_than", "greater_than_equal", "less_than", "less_than_equal", "finite_number",
})


def _shape(value):
    if isinstance(value, str):
        return {"type": "string", "length": len(value), "sha256": sha256(value.encode()).hexdigest()}
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, (int, float)):
        return {"type": "integer" if isinstance(value, int) else "number",
                "finite": not isinstance(value, float) or math.isfinite(value)}
    if isinstance(value, (list, dict)):
        return {"type": "list" if isinstance(value, list) else "object", "length": len(value)}
    return {"type": "unsupported"}


def _identifier(value):
    return value if isinstance(value, str) and re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", value) else None


def _hash(value):
    return value if isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) else None


def _identity(row):
    return {"snapshot_id": _identifier(row.get("snapshot_id")),
            "snapshot_hash": _hash(row.get("snapshot_hash")),
            "snapshot_id_fingerprint": _shape(row.get("snapshot_id"))}


def _validation_details(error):
    if not isinstance(error, ValidationError):
        return [], False
    errors = error.errors(include_input=False, include_context=False, include_url=False)
    return [{"path": [part if (isinstance(part, int) and 0 <= part <= 1024)
                      or (isinstance(part, str) and part in _PATH_PARTS) else "<unknown>"
                      for part in row["loc"][:12]],
             "code": row["type"] if row["type"] in _VALIDATION_CODES else "schema_error"}
            for row in errors[:16]], len(errors) > 16


def _citation_details(context, suggestion):
    from research_system.market_data.research_suggestions import literal_text_present

    citation = context.get("citation")
    if citation is None:
        return None
    documents = [row for row in context.get("documents", []) if isinstance(row, dict)
                 and row.get("evidence_id") == citation.evidence_id
                 and (getattr(citation, "source_snapshot_id", None) is None
                      or row.get("source_snapshot_id") == citation.source_snapshot_id)]
    result = {"collection": context["citation_collection"], "index": context["citation_index"],
              "quote": _shape(citation.quote), "requested_evidence": _shape(citation.evidence_id),
              "bound_document_count": len(documents), "documents": [],
              "documents_truncated": len(documents) > 8}
    for row in documents[:8]:
        text = row.get("inline_content")
        value = getattr(suggestion, "new_value", None)
        result["documents"].append({
            "evidence_id": _identifier(row.get("evidence_id")), "document_hash": _hash(row.get("document_hash")),
            "source_snapshot_id": _identifier(row.get("source_snapshot_id")),
            "source_snapshot_hash": _hash(row.get("source_snapshot_hash")),
            "text": _shape(text),
            "literal_value_present": literal_text_present(value, text)
                if isinstance(value, str) and isinstance(text, str) else None,
        })
    return result


def append_rejection_diagnostic(sink, *, index, kind, reason, context, suggestion, validation=None):
    """Observe a real refusal; diagnostic failures must not change its decision."""
    if not isinstance(sink, list):
        return
    if len(sink) >= _MAX_DIAGNOSTICS:
        sink[-1]["diagnostics_omitted"] = sink[-1].get("diagnostics_omitted", 0) + 1
        return
    base = {"version": VERSION, "proposal_kind": kind, "suggestion_index": index,
            "stage": context["stage"], "reason": reason,
            "replayability": "fingerprints_only_not_original_wire"}
    try:
        field = getattr(suggestion, "field", None)
        allowed = context.get("allowed_fields", ())
        known = context.get("known_fields", ())
        raw = context.get("raw")
        validation_rows, truncated = _validation_details(validation)
        result = {**base, "dimension": context.get("dimension"),
                  "field": field if field in known else None,
                  "requested_field": _shape(field), "allowed_fields": sorted(set(allowed) & set(known)),
                  "input": _identity(context.get("input", {})),
                  "new_value": _shape(getattr(suggestion, "new_value", None)),
                  "old_value": _shape(raw.get(field)) if isinstance(raw, dict) and field in known else None,
                  "old_exists": field in raw if isinstance(raw, dict) and field in known else None,
                  "validation": validation_rows, "validation_truncated": truncated,
                  "citation": _citation_details(context, suggestion)}
        sink.append(result)
    except Exception:  # noqa: BLE001 - observability is not another adoption gate
        sink.append({**base, "diagnostic_status": "details_unavailable"})
