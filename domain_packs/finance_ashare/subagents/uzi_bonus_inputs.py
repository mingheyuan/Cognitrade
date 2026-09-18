"""Pure selection of frozen bonus inputs, shared with quant input binding.

Older projections used the first symbol-root mirror on any dimension. V5
prefers the selected bonus producer, including a real empty or null value.
This never manufactures observations, resolves newer snapshots, or mutates
the source/error envelope. Decisions remain inspectable outside native raw.
"""
from __future__ import annotations

from copy import deepcopy

BONUS_PROJECTION_VERSION = "uzi-dim-result-v5"
SKILL_BONUS_PROJECTION_VERSION = "uzi-dim-result-v6"
SKILL_BONUS_PROJECTION_VERSIONS = frozenset({SKILL_BONUS_PROJECTION_VERSION, "uzi-dim-result-v7", "uzi-dim-result-v8", "uzi-dim-result-v9", "uzi-dim-result-v10"})
BONUS_OWNERS = {"fund_managers": "fund_holders", "similar_stocks": "similar_stocks"}


def uses_owned_bonus_inputs(rows):
    return any((row.get("facts") or {}).get("_uzi_bonus_projection") in {BONUS_PROJECTION_VERSION, *SKILL_BONUS_PROJECTION_VERSIONS}
               for row in rows)


def uses_skill_bonus_inputs(rows):
    return any((row.get("facts") or {}).get("_uzi_bonus_projection") in SKILL_BONUS_PROJECTION_VERSIONS for row in rows)


def _skill_bonus_value(field, decision):
    """Unwrap only identifiable retained pipeline envelopes, not arbitrary data.

    A list extracted from a failed source is not a verified-empty observation:
    status/source/error and the original envelope remain on the frozen row.
    No observation or a failed PIT proof remains absent, not a fabricated [].
    """
    value = decision.get("value")
    if not decision["present"] or not isinstance(value, dict) or not isinstance(value.get("data"), dict):
        return decision
    pipeline = value.get("_pipeline") or {}
    source = str(value.get("source") or "")
    if not (source in {"pipeline:timeout", "pipeline:worker"}
            or isinstance(pipeline, dict) and pipeline.get("dim_key") in {field, "6_fund_holders" if field == "fund_managers" else field}):
        return decision
    return {**decision, "value": deepcopy(value["data"].get(field, [])), "reason": "native_bonus_from_retained_pipeline_envelope"}


def _blocked(facts):
    limits = facts.get("_uzi_transport_limitations") or {}
    return limits.get("bonus_status") == "not_fixed_historical"


def _decision(present, value=None, *, reason, rows):
    return {"present": present, **({"value": deepcopy(value)} if present else {}),
            "reason": reason, "snapshot_ids": [row.get("snapshot_id") for row in rows]}


def _owner_observation(row, key):
    facts = row.get("facts") or {}
    if _blocked(facts):
        return _decision(False, reason="owner_not_point_in_time_available", rows=[row])
    raw = facts.get("_uzi_raw_result") or {}
    data = raw.get("data") or {}
    if isinstance(data, dict) and key in data:
        return _decision(True, data[key], reason="selected_owner_data", rows=[row])
    spill = (facts.get("_uzi_top_level_fields") or {}).get(key)
    if isinstance(spill, dict) and isinstance(spill.get("present"), bool):
        return _decision(spill["present"], spill.get("value"), reason="selected_owner_transport", rows=[row])
    if raw.get("error") or raw.get("_timeout") or (raw.get("_pipeline") or {}).get("error"):
        # Failure is not an empty observation or permission to reuse an older
        # success. The exact failure remains in the frozen owner's envelope.
        return _decision(False, reason="owner_failed_without_bonus_observation", rows=[row])
    return None


def select_bonus_inputs(rows):
    """Select from already symbol-scoped, immutable materialized snapshots."""
    selected = {}
    for key, kind in BONUS_OWNERS.items():
        owners = [row for row in rows if row.get("snapshot_type") == kind]
        observations = [value for row in owners if (value := _owner_observation(row, key)) is not None]
        if observations:
            first = observations[0]
            selected[key] = (first if all((value["present"], value.get("value")) == (first["present"], first.get("value")) for value in observations)
                             else _decision(False, reason="conflicting_owner_observations", rows=owners))
            continue
        # Old databases may have the root observation only on another snapshot.
        # A proven, unambiguous mirror is usable; row ordering is not evidence.
        mirrors = []
        for row in rows:
            facts = row.get("facts") or {}
            spill = (facts.get("_uzi_top_level_fields") or {}).get(key)
            if not _blocked(facts) and isinstance(spill, dict) and spill.get("present") is True:
                mirrors.append((row, spill.get("value")))
        if mirrors and all(value == mirrors[0][1] for _, value in mirrors):
            selected[key] = _decision(True, mirrors[0][1], reason="consistent_proven_mirrors", rows=[row for row, _ in mirrors])
        else:
            selected[key] = _decision(False, reason="conflicting_mirrors" if mirrors else "not_observed", rows=[row for row, _ in mirrors])
    return {key: _skill_bonus_value(key, decision) for key, decision in selected.items()} if uses_skill_bonus_inputs(rows) else selected
