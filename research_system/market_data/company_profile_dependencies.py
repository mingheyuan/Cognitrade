"""Pure consistency checks for a fixed snapshot set after company-profile repair.

This is not native quality scoring or a rebuild executor. A dependency receipt
is an attestation written by a trusted rebuild/review producer into its new
immutable snapshot. Matching labels, model conclusions and critical-count zero
cannot substitute for it. Old snapshots without a profile marker are unchanged.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

from research_system.market_data.contracts import canonical_hash
from research_system.market_data.dimension_contracts import DIMENSION_IDS
from research_system.market_data.native_projection import project_native_result

DEPENDENCY_RECEIPT_VERSION = "company-profile-dependency-v1"
DEPENDENCY_CHECK_VERSION = "company-profile-dependency-check-v1"
_PROFILE_VERSION = "company-profile-revision-v1"
_DEPENDENCY_RULES_VERSION = "native-profile-fetch-dependencies-v1"
DEPENDENCY_LINEAGE_RULES_VERSION = "native-profile-fetch-dependencies-v2-lineage"
DEPENDENCY_LINEAGE_VERSION = "company-profile-lineage-v1"
_PROFILE_FIELDS = ("name", "industry", "main_business")
_RECEIPT_FIELDS = frozenset(
    {
        "version",
        "input_basic_snapshot_id",
        "input_basic_snapshot_hash",
        "dependency_fingerprint",
        "raw_result_hash",
    }
)
_DIMENSION_ORDER = {
    dimension: ordinal for ordinal, dimension in enumerate(DIMENSION_IDS)
}


def profile_consumer_input_hash(dimension, raw):
    """Only mutable identity inputs actually consumed by the pinned fetcher.

    Other raw/quote fields are preserved byte-for-byte by the revision producer.
    This is not a hash of all native inputs or a replacement for snapshot hashes.
    """
    values = {}
    if dimension in {"3_macro", "4_peers", "7_industry", "8_materials", "9_futures",
                     "10_valuation", "13_policy", "similar_stocks"}:
        values["industry"] = raw.get("industry")
    if dimension in {"4_peers", "14_moat", "15_events", "17_sentiment", "18_trap"}:
        values["name"] = raw.get("name")
    if dimension == "16_lhb":
        values["industry_present"] = bool(raw.get("industry"))
    return canonical_hash({"dimension": dimension, "profile_inputs": values})


def _lineage_error(basic, marker):
    rebuild = marker["rebuild_required"]
    if rebuild["version"] != DEPENDENCY_LINEAGE_RULES_VERSION:
        return None
    current = _dimensions(rebuild.get("current_required_dimensions"))
    required = _dimensions(rebuild.get("required_dimensions"))
    lineage = rebuild.get("dependency_lineage")
    if (current is None or required is None or not isinstance(lineage, dict)
            or lineage.get("version") != DEPENDENCY_LINEAGE_VERSION
            or lineage.get("parent_basic") != {
                "snapshot_id": marker["input_snapshot_id"], "snapshot_hash": marker["input_snapshot_hash"]}):
        return "profile_lineage_invalid"
    inputs, anchors = lineage.get("validated_inputs"), lineage.get("consumer_anchors")
    if (type(inputs) is not list or not inputs or not isinstance(anchors, dict)
            or _dimensions(list(anchors)) is None):
        return "profile_lineage_invalid"
    catalog = {}
    for item in inputs:
        if (not isinstance(item, dict) or set(item) != {"snapshot_id", "snapshot_hash"}
                or not _id(item["snapshot_id"]) or not _hash(item["snapshot_hash"])
                or item["snapshot_id"] in catalog):
            return "profile_lineage_inputs_invalid"
        catalog[item["snapshot_id"]] = item["snapshot_hash"]
    if catalog.get(marker["input_snapshot_id"]) != marker["input_snapshot_hash"]:
        return "profile_lineage_parent_invalid"
    if set(required) != set(current) | set(anchors):
        return "profile_lineage_range_invalid"
    raw = _get(basic, "payload_json")["raw"]
    for dimension, bindings in anchors.items():
        if type(bindings) is not list or not bindings:
            return "profile_lineage_anchor_invalid"
        seen = set()
        for binding in bindings:
            if (not isinstance(binding, dict) or set(binding) != {
                    "input_basic_snapshot_id", "input_basic_snapshot_hash", "dependency_fingerprint",
                    "profile_input_hash"}
                    or not _id(binding["input_basic_snapshot_id"])
                    or any(not _hash(binding[key]) for key in (
                        "input_basic_snapshot_hash", "dependency_fingerprint", "profile_input_hash"))
                    or catalog.get(binding["input_basic_snapshot_id"]) != binding["input_basic_snapshot_hash"]
                    or binding["input_basic_snapshot_id"] in seen):
                return "profile_lineage_anchor_invalid"
            seen.add(binding["input_basic_snapshot_id"])
            # Changed consumers deliberately carry no old receipt authority.
            if dimension not in current and binding["profile_input_hash"] != profile_consumer_input_hash(dimension, raw):
                return "profile_lineage_consumer_input_mismatch"
    return None


def _get(row: Any, key: str, default=None):
    return (
        row.get(key, default)
        if isinstance(row, Mapping)
        else getattr(row, key, default)
    )


def _hash(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _id(value):
    return type(value) is str and bool(value.strip())


def _native_result_hash(snapshot):
    # Receipt v1 permanently binds v3, even if the default projection changes.
    view = SimpleNamespace(
        **{
            field: _get(snapshot, field)
            for field in (
                "payload_json",
                "query_manifest_json",
                "source_version",
                "validation_json",
                "status",
                "dimension",
            )
        }
    )
    return canonical_hash(project_native_result(view, version="uzi-dim-result-v3"))


def _dimensions(value):
    if (
        type(value) is not list
        or any(
            type(key) is not str or key == "0_basic" or key not in _DIMENSION_ORDER
            for key in value
        )
        or len(set(value)) != len(value)
    ):
        return None
    return value


def _fingerprint_error(basic, marker):
    """Recompute the existing revision producer's fingerprint, without I/O."""
    if marker.get("version") != _PROFILE_VERSION:
        return "profile_version_unsupported"
    rebuild = marker.get("rebuild_required")
    if (
        not isinstance(rebuild, dict)
        or rebuild.get("version") not in {_DEPENDENCY_RULES_VERSION, DEPENDENCY_LINEAGE_RULES_VERSION}
    ):
        return "profile_dependency_version_unsupported"
    if not _id(marker.get("input_snapshot_id")) or not _hash(
        marker.get("input_snapshot_hash")
    ):
        return "profile_basic_input_invalid"
    if not _hash(marker.get("dependency_fingerprint")):
        return "profile_fingerprint_invalid"
    payload = _get(basic, "payload_json")
    raw = payload.get("raw") if isinstance(payload, dict) else None
    facts, sources = marker.get("facts"), marker.get("source_snapshot_inputs")
    if (
        not isinstance(raw, dict)
        or type(facts) is not list
        or not facts
        or type(sources) is not list
    ):
        return "profile_fingerprint_inputs_invalid"
    fields = []
    for fact in facts:
        if (
            not isinstance(fact, dict)
            or fact.get("field") not in _PROFILE_FIELDS
            or fact.get("snapshot_id") != marker["input_snapshot_id"]
            or fact.get("snapshot_hash") != marker["input_snapshot_hash"]
            or fact.get("new_value") != raw.get(fact["field"])
        ):
            return "profile_fingerprint_facts_invalid"
        fields.append(fact["field"])
    if len(set(fields)) != len(fields):
        return "profile_fingerprint_facts_invalid"
    inputs = []
    for source in sources:
        if (
            not isinstance(source, dict)
            or not _id(source.get("snapshot_id"))
            or not _hash(source.get("snapshot_hash"))
        ):
            return "profile_fingerprint_sources_invalid"
        inputs.append((source["snapshot_id"], source["snapshot_hash"]))
    if len(set(inputs)) != len(inputs):
        return "profile_fingerprint_sources_invalid"
    expected = canonical_hash(
        {
            "version": _PROFILE_VERSION,
            "basic_input": {
                "snapshot_id": marker["input_snapshot_id"],
                "snapshot_hash": marker["input_snapshot_hash"],
            },
            "profile": {field: raw.get(field) for field in _PROFILE_FIELDS},
            "source_inputs": sorted(inputs),
            "facts": facts,
            "rebuild_required": rebuild,
        }
    )
    if expected != marker["dependency_fingerprint"]:
        return "profile_fingerprint_mismatch"
    return _lineage_error(basic, marker)


def check_company_profile_dependencies(selected_snapshots) -> dict[str, Any]:
    """Check only these fixed rows, never resolve a newer profile or mutate rows.

    Accepts a dimension-to-snapshot mapping or an iterable of snapshot objects.
    can_freeze denotes this independent consistency check only; existing hash,
    PIT, native input and final report checks remain the callers' responsibility.
    """
    rows = (
        list(selected_snapshots.values())
        if isinstance(selected_snapshots, Mapping)
        else list(selected_snapshots)
    )
    result = {
        "version": DEPENDENCY_CHECK_VERSION,
        "status": "not_required",
        "can_freeze": True,
        "input_basic_snapshot_id": None,
        "input_basic_snapshot_hash": None,
        "dependency_fingerprint": None,
        "direct_dimensions": [],
        "review_dimensions": [],
        "required_dimensions": [],
        "pending_dimensions": [],
        "issues": [],
    }
    basics = [row for row in rows if _get(row, "dimension") == "0_basic"]
    marked = [
        row
        for row in basics
        if isinstance(_get(row, "query_manifest_json"), dict)
        and "company_profile_revision" in _get(row, "query_manifest_json")
    ]
    if not marked:
        return result
    basic = marked[0]
    result.update(
        input_basic_snapshot_id=_get(basic, "id"),
        input_basic_snapshot_hash=_get(basic, "content_hash"),
    )
    marker = _get(basic, "query_manifest_json")["company_profile_revision"]
    if len(basics) != 1 or not isinstance(marker, dict):
        error = "profile_marker_invalid"
    else:
        error = _fingerprint_error(basic, marker)
    rebuild = marker.get("rebuild_required") if isinstance(marker, dict) else None
    direct = (
        _dimensions(rebuild.get("direct_dimensions"))
        if isinstance(rebuild, dict)
        else None
    )
    required = (
        _dimensions(rebuild.get("required_dimensions"))
        if isinstance(rebuild, dict)
        else None
    )
    review = (
        _dimensions(rebuild.get("review_dimensions"))
        if isinstance(rebuild, dict)
        else None
    )
    if (
        direct is None
        or review is None
        or required is None
        or set(direct) != set(required)
    ):
        error = error or "profile_dependency_range_invalid"
    else:
        # Review-only analysis never grants raw recollection authority or
        # blocks this input check. The producer alone defines true consumers.
        required = sorted(required, key=_DIMENSION_ORDER.__getitem__)
        result.update(
            direct_dimensions=list(direct),
            review_dimensions=list(review),
            required_dimensions=required,
        )
    if isinstance(marker, dict) and _hash(marker.get("dependency_fingerprint")):
        result["dependency_fingerprint"] = marker["dependency_fingerprint"]
    if error:
        result.update(
            status="invalid",
            can_freeze=False,
            pending_dimensions=list(result["required_dimensions"]),
            issues=[{"dimension": "0_basic", "reason": error}],
        )
        return result
    by_dimension = {}
    for row in rows:
        by_dimension.setdefault(_get(row, "dimension"), []).append(row)
    expected_receipt = {
        "version": DEPENDENCY_RECEIPT_VERSION,
        "input_basic_snapshot_id": _get(basic, "id"),
        "input_basic_snapshot_hash": _get(basic, "content_hash"),
        "dependency_fingerprint": marker["dependency_fingerprint"],
    }
    scope_fields = (
        "symbol",
        "market",
        "as_of_date",
        "exchange_timezone",
        "contract_version",
    )
    for dimension in result["required_dimensions"]:
        permitted = [expected_receipt]
        if (rebuild.get("version") == DEPENDENCY_LINEAGE_RULES_VERSION
                and dimension not in rebuild["current_required_dimensions"]):
            permitted.extend({"version": DEPENDENCY_RECEIPT_VERSION,
                              **{key: value for key, value in anchor.items() if key != "profile_input_hash"}}
                             for anchor in rebuild["dependency_lineage"]["consumer_anchors"].get(dimension, []))
        dependencies = by_dimension.get(dimension, [])
        if len(dependencies) != 1:
            reason = (
                "dependency_snapshot_missing"
                if not dependencies
                else "dependency_snapshot_ambiguous"
            )
        elif any(
            _get(dependencies[0], field) != _get(basic, field) for field in scope_fields
        ):
            reason = "dependency_scope_mismatch"
        else:
            manifest = _get(dependencies[0], "query_manifest_json")
            receipt = (
                manifest.get("company_profile_dependency_receipt")
                if isinstance(manifest, dict)
                else None
            )
            if not isinstance(receipt, dict):
                reason = "dependency_receipt_missing"
            elif set(receipt) != _RECEIPT_FIELDS or not any(all(
                receipt.get(key) == value for key, value in binding.items()
            ) for binding in permitted):
                reason = "dependency_receipt_mismatch"
            elif not _hash(receipt.get("raw_result_hash")) or receipt[
                "raw_result_hash"
            ] != _native_result_hash(dependencies[0]):
                reason = "dependency_result_hash_mismatch"
            else:
                if dimension != "9_futures":
                    continue
                materials = by_dimension.get("8_materials", [])
                if len(materials) != 1:
                    reason = "dependency_materials_snapshot_missing"
                elif any(_get(materials[0], field) != _get(basic, field) for field in scope_fields):
                    reason = "dependency_materials_scope_mismatch"
                elif manifest.get("company_profile_materials_input") != {
                    "snapshot_id": _get(materials[0], "id"),
                    "snapshot_hash": _get(materials[0], "content_hash"),
                    "raw_result_hash": _native_result_hash(materials[0]),
                }:
                    reason = "dependency_materials_binding_mismatch"
                else:
                    continue
        result["pending_dimensions"].append(dimension)
        result["issues"].append({"dimension": dimension, "reason": reason})
    result.update(
        status="pending" if result["pending_dimensions"] else "ready",
        can_freeze=not result["pending_dimensions"],
    )
    return result


__all__ = [
    "DEPENDENCY_CHECK_VERSION",
    "DEPENDENCY_RECEIPT_VERSION",
    "check_company_profile_dependencies",
]
