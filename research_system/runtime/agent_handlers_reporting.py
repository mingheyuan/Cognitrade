from __future__ import annotations

from typing import Any

from research_system.runtime.jsonl import write_json
from research_system.runtime.orchestrator_context import RuntimeContext
from research_system.runtime.orchestrator_execution_support import OrchestratorExecutionSupport
from research_system.schemas.planning import ActionPlan, ActionSpec
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.runtime import BlackboardEntry


def run_reporter(
    host: Any,
    support: OrchestratorExecutionSupport,
    *,
    context: RuntimeContext,
    request: RequestEnvelope,
    plan: ActionPlan,
    intent: str,
    action: ActionSpec,
    phase: str,
) -> None:
    evidences = []
    retrieval_summary = None
    for entry in context.blackboard.list_entries():
        if entry.kind == "retrieved_evidence":
            evidences.extend(entry.payload.get("evidences", []))
            retrieval_summary = entry.payload.get("retrieval_summary")
    payload = host.reporter.run(
        intent=intent,
        query=request.user_query,
        evidences=evidences,
        retrieval_summary=retrieval_summary,
        planning_context_hints=dict(action.inputs.get("planning_context_hints") or {}),
    )
    support.extend_subagent_trace(context, agent="reporter", phase=phase, payload=payload)
    payload["debug"] = support.build_debug_payload(
        context=context,
        request=request,
        plan=plan,
        plan_intent=intent,
        retrieval_summary=retrieval_summary,
    )
    entry = BlackboardEntry(
        run_id=context.run_id,
        request_id=request.request_id,
        kind="report_fragment",
        agent="reporter",
        phase=phase,
        payload=payload,
        source_refs=[{"type": "retrieval", "evidence_count": len(evidences), "run_id": context.run_id}],
        confidence=0.7,
        risk_flags=[],
    )
    support.add_entry(context, request, intent, entry)
    write_json(context.paths.result, payload)
