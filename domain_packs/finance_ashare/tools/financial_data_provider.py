from __future__ import annotations

from typing import Any

from domain_packs.finance_ashare.tools.snapshot_collector import collect_snapshot_bundle


def invoke(arguments: dict[str, Any], context) -> dict[str, Any]:
    """Execute the Finance Pack data provider through the shared Tool Registry."""
    payload = collect_snapshot_bundle(
        query=str(arguments.get("query") or ""),
        symbols=list(arguments.get("symbols") or []),
        snapshot_types=list(arguments.get("snapshot_types") or []),
        intent=str(arguments.get("intent") or "stock_check"),
        sector=str(arguments.get("sector") or "").strip() or None,
        candidate_pool_size=int(arguments.get("candidate_pool_size") or 8),
        enrichment_top_n=int(arguments.get("enrichment_top_n") or 2),
        cancellation=context.cancellation,
        cache_root=getattr(context, "cache_root", None),
        research_profile=str(arguments.get("research_profile") or "").strip() or None,
    )
    source_refs: list[dict[str, Any]] = []
    for row in list(payload.get("snapshots") or []):
        if not isinstance(row, dict):
            continue
        source = row.get("source") if isinstance(row.get("source"), dict) else {}
        provider = str(
            source.get("provider_id")
            or source.get("source_id")
            or row.get("snapshot_type")
            or "financial_data_provider"
        )
        identifier = ":".join(
            item
            for item in (
                str(row.get("snapshot_type") or "snapshot"),
                str(row.get("symbol") or row.get("sector") or "unscoped"),
            )
            if item
        )
        source_refs.append(
            {
                "type": "financial_data_provider",
                "provider_id": provider,
                "id": identifier,
            }
        )
    payload["source_refs"] = source_refs or [
        {"type": "financial_data_provider", "id": "empty_result"}
    ]
    return payload


__all__ = ["invoke"]
