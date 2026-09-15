"""Frozen-input diagnostics, not a substitute for UZI's final report review."""

from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

from research_system.market_data.native_projection import PROJECTION_VERSION
from research_system.market_data.report_execution_dependencies import (
    report_execution_dependencies,
)
from research_system.market_data.uzi_gate import evaluate_uzi_collection_gate

QUALITATIVE_DIMENSIONS = (
    "3_macro",
    "13_policy",
    "7_industry",
    "15_events",
    "8_materials",
    "9_futures",
)
PREFLIGHT_POLICY_VERSION = "uzi-report-start-v2"


def report_start_preflight(recorded: Mapping[str, Any]) -> dict[str, Any]:
    """Apply startup policy without rewriting an immutable Manifest assessment.

    Older manifests folded data-review criticals and distinct-URL counts into
    can_start_analysis. Those findings belong to the Agent/review workflow,
    not the frozen-input safety boundary. Hash, ownership, revocation and PIT
    validation still run in the report-input gateway/materializer.
    """
    result = dict(recorded)
    blockers = list(result.get("input_blockers") or [])
    version = result.get("policy_version")
    if version not in {None, PREFLIGHT_POLICY_VERSION}:
        blockers.append({"reason_code": "report_preflight_policy_unsupported"})
    can_start = not blockers
    if result.get("can_start_analysis") is False and can_start:
        result["recorded_can_start_analysis"] = False
        result["legacy_start_gate_reclassified"] = True
    result.update(
        policy_version=PREFLIGHT_POLICY_VERSION,
        input_blockers=blockers,
        can_start_analysis=can_start,
        research_findings_are_advisory=True,
        evidence_check_scope="frozen_input_source_inventory",
        final_report_gate="pending_agent_self_review",
    )
    return result


def report_input_readiness(
    items: list[dict[str, Any]], pins: Mapping[str, Any]
) -> dict[str, Any]:
    """Describe work for the Agent after frozen-input safety validation.

    UZI stage1 hands data gaps to the Agent. Its qualitative evidence contract
    applies to the resulting analysis, not to the number of URLs in a Bundle.
    In particular, two distinct findings may cite the same document.
    """
    profile = str(pins.get("research_profile") or "medium")
    full = pins.get("agent_workflow_mode", "full") == "full"
    required = QUALITATIVE_DIMENSIONS if full or profile != "lite" else ()
    stocks = []
    for item in items:
        snapshots = list(item.get("snapshots") or [])
        by_dim = {
            str(row.get("dimension_id") or row.get("dimension")): row
            for row in snapshots
        }
        native_snapshots = {}
        for dimension, row in by_dim.items():
            facts = dict(row.get("facts") or {})
            native_snapshots[dimension] = SimpleNamespace(
                dimension=dimension,
                market=row.get("market") or item.get("market"),
                payload_json=facts,
                query_manifest_json=row.get("query_manifest") or {},
                validation_json={},
                status=row.get("quality_status") or row.get("availability_status"),
                source_version="frozen_bundle",
            )
        data = evaluate_uzi_collection_gate(
            native_snapshots,
            profile=profile,
            market=item.get("market"),
            projection_version=str(
                pins.get("snapshot_projection_version") or PROJECTION_VERSION
            ),
        )
        gaps = []
        for dimension in required:
            urls = research_cited_urls(by_dim.get(dimension) or {})
            if not urls:
                gaps.append(
                    {
                        "dimension": dimension,
                        "reason_code": "research_source_unavailable",
                        "available_sources": len(urls),
                        "blocking": False,
                        "next_action": "analyze_frozen_evidence_or_disclose_gap",
                        "acquisition_policy": "explicit_data_center_task_only",
                    }
                )
        blockers = []
        if not str(item.get("symbol") or "").strip():
            blockers.append({"reason_code": "frozen_stock_identity_missing"})
        if not snapshots:
            blockers.append({"reason_code": "frozen_snapshots_missing"})
        stocks.append(
            report_start_preflight(
                {
                    "symbol": item.get("symbol"),
                    "data_check_passed": data["gate_passed"],
                    "data_issues": list(data.get("issues") or []),
                    "evidence_check_passed": not gaps,
                    "evidence_gaps": gaps,
                    "input_blockers": blockers,
                    # Kept separate: report startup is not a promise that
                    # frozen native synthesis dependencies are available.
                    "execution_dependencies": report_execution_dependencies(
                        item, profile=profile, school=pins.get("school"),
                    ),
                }
            )
        )
    return {
        "stage": "report_input",
        "policy_version": PREFLIGHT_POLICY_VERSION,
        "research_findings_are_advisory": True,
        "input_safety_boundary": "frozen_manifest_gateway_and_materializer",
        "profile": profile,
        "agent_workflow_mode": "full" if full else "profile",
        "snapshot_projection_version": pins.get("snapshot_projection_version")
        or PROJECTION_VERSION,
        "can_start_analysis": bool(stocks)
        and all(row["can_start_analysis"] for row in stocks),
        "final_report_gate": "pending_agent_self_review",
        "stocks": stocks,
    }


def research_cited_urls(snapshot: Mapping[str, Any]) -> set[str]:
    """Historical raw links are discovery hints, not frozen document evidence."""
    manifest = dict(snapshot.get("query_manifest") or {})
    if manifest.get("pit_mode") == "historical_reconstruction":
        return {
            str(ref["url"])
            for ref in snapshot.get("source_refs") or []
            if isinstance(ref, Mapping)
            and ref.get("source_kind") == "research_document"
            and ref.get("evidence_mode")
            in {"public_archive_reconstruction", "contemporaneous_archive"}
            and ref.get("content")
            and ref.get("content_hash")
            and ref.get("content_ref")
            and str(ref.get("url") or "").startswith(("http://", "https://"))
        }
    return cited_urls(snapshot)


def cited_urls(value: Any) -> set[str]:
    """Match native citation shape without treating unreferenced links as facts."""
    urls: set[str] = set()
    if isinstance(value, Mapping):
        url = value.get("url") or value.get("pdf") or value.get("link")
        body = (
            value.get("content")
            or value.get("finding")
            or value.get("body")
            or value.get("snippet")
            or value.get("text_excerpt")
            or value.get("title")
        )
        if isinstance(url, str) and url.startswith(("https://", "http://")) and body:
            urls.add(url)
        for child in value.values():
            urls.update(cited_urls(child))
    elif isinstance(value, list):
        for child in value:
            urls.update(cited_urls(child))
    return urls
