"""Non-authoritative DC model transport; immutable inputs stay with validators."""

from copy import deepcopy
from hashlib import sha256

from domain_packs.finance_ashare.subagents.uzi_input_views import compact_evidence_view
from domain_packs.finance_ashare.subagents.uzi_qualitative_deep_dive import _COMPANY_CONTEXT_TYPES
from research_system.market_data.contracts import canonical_hash, canonical_json
from research_system.market_data.historical import capability_for_dimension


RESEARCH_PROMPT_TRANSPORT_VERSION = "uzi-dc-research-transport-v1"


def _keyed_tables(value, *, path=""):
    """Lossless row/column transport for repeated scalar field dictionaries."""
    if not isinstance(value, (dict, list)) or path == "snapshot_inputs":
        return value
    rows = list(value.values()) if isinstance(value, dict) else value
    if (len(rows) >= 2 and all(isinstance(row, dict) and row and
            all(not isinstance(item, (dict, list)) for item in row.values()) for row in rows)
            and all(set(row) == set(rows[0]) for row in rows)):
        common = {key: rows[0][key] for key in rows[0]
                  if all(canonical_json(row[key]) == canonical_json(rows[0][key]) for row in rows)}
        columns = [key for key in rows[0] if key not in common]
        encoded_rows = ({key: [row[column] for column in columns] for key, row in value.items()}
                        if isinstance(value, dict) else [[row[column] for column in columns] for row in rows])
        marker = "__uzi_keyed_table__" if isinstance(value, dict) else "__uzi_row_table__"
        candidate = {marker: {"columns": columns, "common_values": common, "rows": encoded_rows}}
        if len(canonical_json(candidate)) < len(canonical_json(value)):
            return candidate
    if isinstance(value, list):
        return [_keyed_tables(item, path=f"{path}/{index}") for index, item in enumerate(value)]
    return {key: _keyed_tables(item, path=f"{path}/{key}" if path else key) for key, item in value.items()}


def _same_value_references(value):
    """Losslessly share large identical subtrees using backward JSON pointers.

    Canonical JSON comparison distinguishes false/0 and null/empty values.
    References concern values only, not evidence or permission equivalence.
    """
    seen = {}
    def visit(item, pointer):
        if isinstance(item, (dict, list)):
            encoded = canonical_json(item)
            if len(encoded) >= 512:
                if encoded in seen:
                    return {"__uzi_same_value_reference__": seen[encoded]}
                seen[encoded] = pointer
        if isinstance(item, dict):
            return {key: visit(child, pointer + "/" + key.replace("~", "~0").replace("/", "~1"))
                    for key, child in item.items()}
        if isinstance(item, list):
            return [visit(child, pointer + "/" + str(index)) for index, child in enumerate(item)]
        return item
    return visit(value, "")


def group_document_view(context):
    """Share only byte-identical extraction strings from the same original.

    Keep each occurrence's evidence ID, URL, observation and extraction binding.
    A legacy prefix and a full extraction of one PDF are not interchangeable.
    """
    view = deepcopy(context)
    bodies = {}
    for dimension, row in view.items():
        for index, document in enumerate(row.get("documents", [])):
            text, original = document.get("inline_content"), document.get("document_hash")
            if not isinstance(text, str) or not text or not original:
                continue
            key = original, sha256(text.encode()).hexdigest()
            previous = bodies.get(key)
            if previous is not None and previous[0] == text:
                document.pop("inline_content")
                document["text_reference"] = deepcopy(previous[1])
            else:
                bodies[key] = text, {"dimension": dimension, "document_index": index,
                                     "evidence_id": document["evidence_id"],
                                     "text_sha256": key[1]}
    return view


def background_view(context, *, enrichment_version):
    """Use the report's company-background scope, with sparse index samples.

    All snapshot identities and native error/source envelopes survive. Only
    non-company-background facts are not displayed; they remain bound by hash.
    This grants no new citation or revision authority.
    """
    view = deepcopy(context)
    for dimension, row in view.get("dimensions", {}).items():
        if capability_for_dimension(dimension).snapshot_type not in _COMPANY_CONTEXT_TYPES:
            row.pop("data", None)
            row["facts_view_status"] = "outside_company_background_view_not_missing"
    truncations = []
    view = compact_evidence_view(view, max_sequence_items=20, max_text_chars=800,
        truncations=truncations, preserve_sequences_at=frozenset({"snapshot_inputs"}),
        indexed_sequences=True)
    view = _same_value_references(_keyed_tables(view))
    view["prompt_view"] = {
        "version": RESEARCH_PROMPT_TRANSPORT_VERSION, "enrichment_version": enrichment_version,
        "read_only": True, "fixed_input_hash": canonical_hash(context), "truncations": truncations,
        "fact_snapshot_types": sorted(_COMPANY_CONTEXT_TYPES),
        "omission_semantics": "__uzi_indexed_sequence__ retains original length and index-keyed samples; absent indexes are outside the view, not missing source facts",
        "lossless_transport": "__uzi_keyed_table__ preserves dictionary keys; __uzi_row_table__ preserves list order. Restore each row from common_values and columns zipped with row values. __uzi_same_value_reference__ is an RFC6901 pointer from this context root to the identical represented value, not shared provenance",
    }
    return view


def reference_matching_raw(group, context):
    """A group may reference only its own identical, identity-bound background.

    Compilation and persistence still consume the original full raw value.
    A stale, differently hashed or absent context must not replace that value.
    """
    selected = {row["dimension"]: row for row in context.get("snapshot_inputs", [])}
    for dimension, row in group.items():
        identity = selected.get(dimension, {})
        background = context.get("dimensions", {}).get(dimension, {})
        if (row.get("snapshot_id") and row.get("snapshot_hash")
                and identity.get("snapshot_id") == row["snapshot_id"]
                and identity.get("content_hash") == row["snapshot_hash"]
                and "raw" in row and "data" in background
                and canonical_hash(row["raw"]) == canonical_hash(background["data"])):
            row.pop("raw")
            row["raw_reference"] = {"container": "READ_ONLY_RESEARCH_CONTEXT",
                                     "dimension": dimension, "field": "data",
                                     "snapshot_id": identity["snapshot_id"],
                                     "snapshot_hash": identity["content_hash"]}
    return group
