from __future__ import annotations

from research_system.runtime.execution.core.support import OrchestratorExecutionSupport
from research_system.runtime.orchestration.orchestrator_context import RuntimeContext
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.runtime import BlackboardEntry


def add_collector_outputs(
    support: OrchestratorExecutionSupport,
    *,
    context: RuntimeContext,
    request: RequestEnvelope,
    intent: str,
    phase: str,
    payload: dict,
) -> None:
    for source in payload["sources"]:
        entry = BlackboardEntry(
            run_id=context.run_id,
            request_id=request.request_id,
            kind="source_record",
            agent="collector",
            phase=phase,
            payload=source,
            source_refs=[{"type": "request", "request_id": request.request_id, "run_id": context.run_id}],
        )
        support.add_entry(context, request, intent, entry)
    for snapshot in payload["snapshots"]:
        entry = BlackboardEntry(
            run_id=context.run_id,
            request_id=request.request_id,
            kind="verified_snapshot",
            agent="collector",
            phase=phase,
            payload=snapshot,
            source_refs=[{"type": "skill", "skill": snapshot["source"]["skill"], "run_id": context.run_id}],
            confidence=1.0,
        )
        support.add_entry(context, request, intent, entry)


def add_stock_methodology_entries(
    support: OrchestratorExecutionSupport,
    *,
    context: RuntimeContext,
    request: RequestEnvelope,
    intent: str,
    phase: str,
) -> None:
    methodology_entries = support.build_stock_methodology_entries(
        context=context,
        request=request,
        intent=intent,
        phase=phase,
        agent="collector",
    )
    for entry in methodology_entries:
        support.add_entry(context, request, intent, entry)
