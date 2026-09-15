"""Versioned, pure projection of persisted snapshots into UZI DimResult.

Both collection preflight and report materialization consume this boundary.
The legacy projection is retained for manifests frozen before it was versioned.
No projection mutates the persisted snapshot or its upstream envelope.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from research_system.market_data.contracts import canonical_hash

LEGACY_PROJECTION_VERSION = "legacy-v1"
V2_PROJECTION_VERSION = "uzi-dim-result-v2"
V3_PROJECTION_VERSION = "uzi-dim-result-v3"
V4_PROJECTION_VERSION = "uzi-dim-result-v4"
V5_PROJECTION_VERSION = "uzi-dim-result-v5"
V6_PROJECTION_VERSION = "uzi-dim-result-v6"
V7_PROJECTION_VERSION = "uzi-dim-result-v7"
PROJECTION_VERSION = "uzi-dim-result-v8"
EMPTY_INPUT_VERSION = "native-empty-input-v1"
PROJECTION_VERSIONS = frozenset(
    {LEGACY_PROJECTION_VERSION, V2_PROJECTION_VERSION, V3_PROJECTION_VERSION, V4_PROJECTION_VERSION, V5_PROJECTION_VERSION, V6_PROJECTION_VERSION, V7_PROJECTION_VERSION, PROJECTION_VERSION}
)
UNAVAILABLE_QUALITIES = frozenset(
    {
        "assumption_only",
        "data_gap",
        "error",
        "failed",
        "missing",
        "missing_result",
        "no_data",
        "parse_failed",
        "provider_unavailable",
        "rate_limited",
        "timeout",
    }
)


def native_quality(snapshot: Any) -> str:
    manifest = dict(getattr(snapshot, "query_manifest_json", None) or {})
    validation = dict(getattr(snapshot, "validation_json", None) or {})
    return (
        str(
            manifest.get("native_quality_status")
            or validation.get("native_quality_status")
            or validation.get("requested_status")
            or getattr(snapshot, "status", "")
            or "data_gap"
        )
        .strip()
        .lower()
        or "data_gap"
    )


def native_payload(
    snapshot: Any, *, version: str = PROJECTION_VERSION
) -> dict[str, Any]:
    if version not in PROJECTION_VERSIONS:
        raise ValueError(f"unsupported snapshot projection version: {version}")
    if version == V3_PROJECTION_VERSION:
        return _native_payload_v3(snapshot)
    if version == PROJECTION_VERSION:
        disclosure = _historical_availability_payload(snapshot)
        if disclosure:
            return disclosure
    if version in {V7_PROJECTION_VERSION, PROJECTION_VERSION}:
        return _native_payload_v4(snapshot, payload=stored_native_payload(snapshot, version=version),
                                  promote_verified_empty=False)
    if version in {V4_PROJECTION_VERSION, V5_PROJECTION_VERSION, V6_PROJECTION_VERSION}:
        return _native_payload_v4(snapshot)
    # Frozen v1/v2 manifests must retain their exact old projection, including
    # its strict-outcome clearing and original raw/envelope precedence.
    payload = dict(getattr(snapshot, "payload_json", None) or {})
    manifest = dict(getattr(snapshot, "query_manifest_json", None) or {})
    outcome = str(manifest.get("collection_outcome") or "").strip().lower()
    if outcome == "verified_empty":
        observation = manifest.get("native_empty_observation")
        return (
            deepcopy(dict(observation))
            if isinstance(observation, Mapping)
            else {"observation_status": "verified_empty"}
        )
    if outcome in {"retryable_failure", "unavailable"}:
        return {}
    if isinstance(payload.get("raw"), Mapping):
        return deepcopy(dict(payload["raw"]))
    envelope = payload.get("_uzi_raw_result")
    if isinstance(envelope, Mapping) and isinstance(envelope.get("data"), Mapping):
        return deepcopy(dict(envelope["data"]))
    if str(payload.get("availability") or "").lower() in {
        "historically_unavailable",
        "provider_unavailable",
    }:
        return {}
    return deepcopy(
        {str(k): v for k, v in payload.items() if not str(k).startswith("_")}
    )


def _pit_rejected(manifest: Mapping[str, Any]) -> bool:
    if manifest.get("pit_safe") is False:
        return True
    requested = manifest.get("requested_as_of_date")
    observed = manifest.get("observed_local_date")
    return bool(
        requested
        and observed
        and requested != observed
        and manifest.get("pit_mode") != "historical_reconstruction"
    )


def _disclosure_only(data: Mapping[str, Any]) -> bool:
    """Host gap explanations are not substitutes for upstream observations."""
    return bool(data) and set(data).issubset(
        {
            "availability",
            "reason_code",
            "message",
            "excluded_from_scoring",
            "data_gap",
            "degrade_reason",
            "degrade_reasons",
            "missing_fields",
        }
    )


def _historical_availability_payload(snapshot: Any) -> dict[str, Any]:
    """Expose an owned, recorded availability decision as native-readable notes.

    Like UZI's no-login fetch result, a known limitation is not an unexplained
    empty response. This is explicitly host-authored metadata, not an upstream
    response, zero-result query, or historical observation. No market fields,
    error or timeout are invented. Unknown/failed/unsafe inputs stay unchanged.
    """
    payload = dict(getattr(snapshot, "payload_json", None) or {})
    manifest = dict(getattr(snapshot, "query_manifest_json", None) or {})
    degradation = manifest.get("degradation")
    if (
        getattr(snapshot, "source_version", None) != "cognitrade-historical-disclosure-v1"
        or native_quality(snapshot) != "data_gap"
        or _pit_rejected(manifest)
        or manifest.get("pit_safe") is not True
        or manifest.get("pit_mode") != "historical_unavailable_disclosure"
        or not _disclosure_only(payload)
        or payload.get("availability") != "historically_unavailable"
        or payload.get("excluded_from_scoring") is not True
        or not isinstance(degradation, Mapping)
        or degradation.get("safe_for_report") is not True
        or degradation.get("excluded_from_scoring") is not True
        or type(degradation.get("attempted_reconstruction")) is not bool
    ):
        return {}
    reason, message = payload.get("reason_code"), payload.get("message")
    requested = manifest.get("requested_as_of_date")
    if (not isinstance(reason, str) or not reason.strip()
            or not isinstance(message, str) or not message.strip()
            or not requested
            or reason != degradation.get("reason_code")
            or message != degradation.get("message")):
        return {}
    return {
        "_note": f"历史不可用披露（非历史观测）：{message}",
        "_historical_availability": {
            "availability": "historically_unavailable",
            "reason_code": reason,
            "requested_as_of_date": str(requested),
            "attempted_reconstruction": degradation["attempted_reconstruction"],
            "excluded_from_scoring": True,
            "is_research_fact": False,
        },
    }


def stored_native_payload(snapshot: Any, *, version: str = PROJECTION_VERSION) -> dict[str, Any]:
    """Read the hash-bound source of a bounded-zero query, never its status.

    Verified-empty snapshots keep their strict empty payload contract. New
    collectors retain source bytes in the query manifest (also part of the
    snapshot hash). Old manifests still use their original v1–v6 projection;
    missing historical bytes cannot be recovered from a zero-count marker.
    """
    stored = dict(getattr(snapshot, "payload_json", None) or {})
    manifest = dict(getattr(snapshot, "query_manifest_json", None) or {})
    marker = manifest.get("native_empty_input")
    if version not in {V7_PROJECTION_VERSION, PROJECTION_VERSION} or stored or marker is None:
        return stored
    if (manifest.get("collection_outcome") != "verified_empty"
            or not isinstance(marker, Mapping) or marker.get("version") != EMPTY_INPUT_VERSION
            or not isinstance(marker.get("payload"), Mapping)
            or canonical_hash(marker["payload"]) != marker.get("content_hash")):
        raise ValueError("verified-empty native input binding mismatch")
    return dict(marker["payload"])


def _native_payload_v3(snapshot: Any, *, payload=None, promote_verified_empty=True) -> dict[str, Any]:
    payload = dict(getattr(snapshot, "payload_json", None) or {}) if payload is None else payload
    manifest = dict(getattr(snapshot, "query_manifest_json", None) or {})
    if _pit_rejected(manifest):
        return {}
    envelope = payload.get("_uzi_raw_result")
    envelope_data = envelope.get("data") if isinstance(envelope, Mapping) else None
    # raw contains the retained upstream data plus actual source recovery facts,
    # not the adapter's normalized audit fields. Keep those successful recoveries
    # while restoring a nonempty native envelope if an old raw copy is empty.
    for data in (payload.get("raw"), envelope_data):
        if isinstance(data, Mapping) and data and not _disclosure_only(data):
            return deepcopy(dict(data))
    if promote_verified_empty and manifest.get("collection_outcome") == "verified_empty":
        observation = manifest.get("native_empty_observation")
        return (
            deepcopy(dict(observation))
            if isinstance(observation, Mapping)
            else {"observation_status": "verified_empty"}
        )
    if isinstance(payload.get("raw"), Mapping) or isinstance(envelope_data, Mapping):
        return {}
    if _disclosure_only(payload):
        return {}
    if str(payload.get("availability") or "").lower() in {
        "historically_unavailable",
        "provider_unavailable",
    }:
        return {}
    return deepcopy(
        {str(k): v for k, v in payload.items() if not str(k).startswith("_")}
    )


def _gap_disclosure(snapshot: Any) -> dict[str, Any]:
    manifest = dict(getattr(snapshot, "query_manifest_json", None) or {})
    payload = dict(getattr(snapshot, "payload_json", None) or {})
    if _pit_rejected(manifest):
        return {
            "reason_code": "point_in_time_input_rejected",
            "message": "该快照观测不满足目标日期可见性，未作为原生分析事实输入",
            "is_research_fact": False,
        }
    disclosure = (
        manifest.get("partial_disclosure")
        or manifest.get("provider_failure")
        or manifest.get("degradation")
        or {}
    )
    if not isinstance(disclosure, Mapping):
        disclosure = {}
    outcome = manifest.get("collection_outcome")
    if not disclosure and outcome not in {
        "partial",
        "retryable_failure",
        "unavailable",
    }:
        return {}
    return {
        "collection_outcome": outcome,
        "reason_code": disclosure.get("reason_code") or payload.get("reason_code"),
        "message": disclosure.get("message") or payload.get("message"),
        "missing_fields": list(
            disclosure.get("missing_fields")
            or (manifest.get("field_coverage") or {}).get("missing_fields")
            or []
        ),
        "is_research_fact": False,
    }


def _native_payload_v4(snapshot: Any, *, payload=None, promote_verified_empty=True) -> dict[str, Any]:
    """Separate collector display/replay mirrors from native report facts.

    The collector emits these exact aliases beside ``_uzi_raw_result`` for
    host UI consumers. The ingest adapter also merged them into ``raw``. A
    later explicit fact revision lives in that same raw mapping, so simply
    preferring the original envelope would discard legitimate new evidence.
    Restrict filtering to the alias's own dimension and a retained native
    envelope. If upstream itself returned that key, it remains native data.
    Persisted payloads and v1/v2/v3 manifests are never rewritten.
    """
    payload = dict(getattr(snapshot, "payload_json", None) or {}) if payload is None else payload
    data = _native_payload_v3(snapshot, payload=payload, promote_verified_empty=promote_verified_empty)
    envelope = payload.get("_uzi_raw_result")
    original = envelope.get("data") if isinstance(envelope, Mapping) else None
    alias = {
        "0_basic": "market_overview",
        "1_financials": "financial_abstract",
        "2_kline": "history",
    }.get(str(getattr(snapshot, "dimension", "")))
    if alias and isinstance(original, Mapping) and alias not in original:
        data.pop(alias, None)
    if not isinstance(original, Mapping):
        return data
    dimension = str(getattr(snapshot, "dimension", ""))
    if dimension == "2_kline":
        _exclude_replay_price_sidecar(data, original)
    if dimension == "6_fund_holders" and "fund_managers" not in original:
        root = payload.get("_uzi_top_level_fields")
        spill = root.get("fund_managers") if isinstance(root, Mapping) else None
        if (isinstance(spill, Mapping) and spill.get("present") is True
                and "fund_managers" in data and data["fund_managers"] == spill.get("value")):
            # The separately proven root is materialized by native_transport_facts.
            # It must not also be silently reintroduced as a dimension field.
            data.pop("fund_managers")
    return data


def _exclude_replay_price_sidecar(data: dict[str, Any], original: Mapping[str, Any]) -> None:
    sidecar = data.get("price_evidence")
    if (not isinstance(sidecar, Mapping)
            or sidecar.get("provider_version") != "baostock-price-evidence-v1"):
        return
    # UziSnapshotAdapter mirrors this identified replay-only source into raw;
    # retain it in the stored payload/series for execution, not model evidence.
    for field in ("price_evidence", "corporate_actions", "adjustment_factors"):
        if field != "price_evidence" and (field not in sidecar or data.get(field) != sidecar[field]):
            continue
        if field in original:
            data[field] = deepcopy(original[field])
        else:
            data.pop(field, None)
    queries, source_queries = data.get("field_queries"), sidecar.get("field_queries")
    if isinstance(queries, Mapping) and isinstance(source_queries, Mapping):
        queries = deepcopy(dict(queries))
        native_queries = original.get("field_queries")
        for field, value in source_queries.items():
            if field not in queries or queries[field] != value:
                continue
            if isinstance(native_queries, Mapping) and field in native_queries:
                queries[field] = deepcopy(native_queries[field])
            else:
                queries.pop(field)
        if queries:
            data["field_queries"] = queries
        elif "field_queries" in original:
            data["field_queries"] = deepcopy(original["field_queries"])
        else:
            data.pop("field_queries", None)


def project_native_result(
    snapshot: Any, *, version: str = PROJECTION_VERSION
) -> dict[str, Any]:
    if version not in PROJECTION_VERSIONS:
        raise ValueError(f"unsupported snapshot projection version: {version}")
    stored = stored_native_payload(snapshot, version=version)
    envelope = stored.get("_uzi_raw_result")
    result = deepcopy(dict(envelope)) if isinstance(envelope, Mapping) else {}
    quality = native_quality(snapshot)
    manifest = dict(getattr(snapshot, "query_manifest_json", None) or {})
    result["data"] = native_payload(snapshot, version=version)
    result.setdefault(
        "source", str(getattr(snapshot, "source_version", None) or "database")
    )
    result.setdefault("fallback", quality in UNAVAILABLE_QUALITIES)
    result.setdefault(
        "_pipeline",
        {
            "dim_key": str(getattr(snapshot, "dimension", "") or ""),
            "quality": (
                "partial"
                if quality in {"degraded", "partial"}
                else "missing"
                if quality
                in {
                    "assumption_only",
                    "data_gap",
                    "missing",
                    "missing_result",
                    "no_data",
                }
                else "error"
                if quality in UNAVAILABLE_QUALITIES
                else "full"
            ),
            "data_gaps": list(manifest.get("missing_fields") or []),
            "error": None,
        },
    )
    if (
        version == V2_PROJECTION_VERSION
        and manifest.get("collection_outcome") == "retryable_failure"
    ):
        failure = manifest.get("provider_failure")
        message = failure.get("message") if isinstance(failure, Mapping) else None
        if not result.get("error"):
            result["error"] = str(message or "historical provider failure")
    if version in {V3_PROJECTION_VERSION, V4_PROJECTION_VERSION, V5_PROJECTION_VERSION, V6_PROJECTION_VERSION, V7_PROJECTION_VERSION, PROJECTION_VERSION}:
        # Keep only the upstream error/_timeout already present in its envelope.
        # A strict collection gap must not manufacture an upstream exception or
        # downgrade the native final empty-dimension check from critical.
        disclosure = _gap_disclosure(snapshot)
        if disclosure:
            result["_market_data_gap"] = disclosure
    if version in {V7_PROJECTION_VERSION, PROJECTION_VERSION} and manifest.get("collection_outcome") == "verified_empty":
        result["_market_data_observation"] = {
            "collection_outcome": "verified_empty", "is_research_fact": False,
            "native_input_retained": bool(stored),
            "reason_code": "bounded_zero_source_retained" if stored else "native_empty_input_not_retained",
        }
    return result


def pin_native_workflow(pins: Mapping[str, Any]) -> dict[str, Any]:
    resolved = dict(pins)
    version = str(
        resolved.setdefault("snapshot_projection_version", PROJECTION_VERSION)
    )
    if version not in PROJECTION_VERSIONS:
        raise ValueError(f"unsupported snapshot projection version: {version}")
    mode = str(resolved.setdefault("agent_workflow_mode", "full"))
    if mode not in {"full", "profile"}:
        raise ValueError("agent_workflow_mode must be full or profile")
    profile = str(resolved.get("research_profile") or "medium")
    if profile not in {"lite", "medium", "deep"}:
        raise ValueError("research_profile must be lite, medium or deep")
    resolved["research_profile"] = profile
    return resolved
