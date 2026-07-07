from __future__ import annotations

from research_system.runtime.execution.core.support import OrchestratorExecutionSupport
from research_system.runtime.orchestration.orchestrator_context import RuntimeContext
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.runtime import BlackboardEntry


def add_retrieved_evidence(
    support: OrchestratorExecutionSupport,
    *,
    context: RuntimeContext,
    request: RequestEnvelope,
    intent: str,
    phase: str,
    payload: dict,
    blackboard_entries: list[dict],
) -> None:
    entry = BlackboardEntry(
        run_id=context.run_id,
        thread_id=context.thread.thread_id,
        request_id=request.request_id,
        kind="retrieved_evidence",
        agent="retriever",
        phase=phase,
        payload=payload,
        source_refs=[{"type": "blackboard", "count": len(blackboard_entries), "run_id": context.run_id}],
        confidence=1.0,
    )
    support.add_entry(context, request, intent, entry)
