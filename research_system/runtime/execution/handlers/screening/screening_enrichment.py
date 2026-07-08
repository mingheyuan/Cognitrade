from __future__ import annotations

from typing import Any

from research_system.runtime.execution.core.support import OrchestratorExecutionSupport
from research_system.runtime.orchestration.orchestrator_context import RuntimeContext
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.runtime import BlackboardEntry


def run_screening_enrichment(
    host: Any,
    support: OrchestratorExecutionSupport,
    *,
    context: RuntimeContext,
    request: RequestEnvelope,
    intent: str,
    phase: str,
    source_payloads: list[dict],
    screener_constraints: dict,
    payload: dict,
) -> dict:
    enrichment_targets = payload.get("enrichment_targets") or []
    if intent != "stock_screen" or not enrichment_targets:
        return payload

    bind_runtime = getattr(host.collector, "bind_runtime", None)
    clear_runtime = getattr(host.collector, "clear_runtime", None)
    if callable(bind_runtime):
        bind_runtime(
            agent_control=context.agent_control,
            execution_policy=context.execution_policy,
            context=context,
            support=support,
            intent=intent,
            thread_id=context.thread.thread_id,
            turn_id=context.turn.turn_id,
            request_id=request.request_id,
            agent="collector",
            phase=phase,
        )
    try:
        enrichment_payload = host.collector.enrich_stock_screen_candidates(
            candidates=enrichment_targets,
            snapshot_types=["market", "fundamental", "notice", "lhb", "capital_flow", "sentiment", "events"],
        )
    finally:
        if callable(clear_runtime):
            clear_runtime()
    support.extend_subagent_trace(context, agent="collector", phase=phase, payload=enrichment_payload)
    support.emit_event(
        context,
        request_id=request.request_id,
        intent=intent,
        event_type="agent_progress",
        phase=phase,
        agent="screener",
        message=f"Enriching top {len(enrichment_targets)} stock_screen candidates",
        payload={"candidate_symbols": [item.get("symbol") for item in enrichment_targets]},
    )
    child_thread_ids = list(enrichment_payload.get("child_thread_ids") or [])
    if child_thread_ids:
        support.emit_event(
            context,
            request_id=request.request_id,
            intent=intent,
            event_type="subagent_trace",
            phase=phase,
            agent="collector",
            message="Collector screening enrichment spawned child threads",
            payload={
                "subagent_type": "collector_screening_enrichment_threads",
                "status": "completed",
                "objective": "Track child-thread fan-out for screening enrichment.",
                "notes": [f"child_threads={','.join(child_thread_ids)}"],
            },
        )
    for snapshot in enrichment_payload.get("snapshots", []):
        snapshot_type = snapshot.get("snapshot_type", "snapshot")
        entry = BlackboardEntry(
            run_id=context.run_id,
            thread_id=context.thread.thread_id,
            request_id=request.request_id,
            kind="verified_snapshot",
            agent="screener",
            phase=phase,
            payload=snapshot,
            source_refs=[{"type": "stock_screen_enrichment", "run_id": context.run_id}],
            confidence=1.0,
            risk_flags=[snapshot.get("degrade_reason")] if snapshot.get("degraded") else [],
        )
        support.add_entry(context, request, intent, entry)
        support.emit_event(
            context,
            request_id=request.request_id,
            intent=intent,
            event_type="subagent_completed",
            phase=phase,
            agent="screener",
            message=f"Enriched {snapshot_type} snapshot for {snapshot.get('symbol') or 'unknown'}",
        )
    refreshed_snapshot_payloads = [
        entry.payload
        for entry in context.blackboard.list_entries(thread_id=context.thread.thread_id)
        if entry.kind == "verified_snapshot"
    ]
    enriched_constraints = dict(screener_constraints)
    enriched_constraints["skip_enrichment"] = True
    payload = host.screener.run(
        query=request.user_query,
        source_payloads=source_payloads,
        snapshot_payloads=refreshed_snapshot_payloads,
        constraints=enriched_constraints,
    )
    rerank_payload = dict(payload)
    rerank_trace = [dict(item) for item in list(rerank_payload.get("subagent_trace") or []) if isinstance(item, dict)]
    if rerank_trace:
        for item in rerank_trace:
            notes = [str(note) for note in list(item.get("notes") or []) if str(note).strip()]
            if "post_enrichment=true" not in notes:
                notes.append("post_enrichment=true")
            item["notes"] = notes
        rerank_payload["subagent_trace"] = rerank_trace
    support.extend_subagent_trace(
        context,
        agent="screener",
        phase=phase,
        payload=rerank_payload,
    )
    return payload
