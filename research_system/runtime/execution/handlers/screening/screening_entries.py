from __future__ import annotations

from research_system.runtime.execution.core.support import OrchestratorExecutionSupport
from research_system.runtime.orchestration.orchestrator_context import RuntimeContext
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.runtime import BlackboardEntry


def add_screening_candidates(
    support: OrchestratorExecutionSupport,
    *,
    context: RuntimeContext,
    request: RequestEnvelope,
    intent: str,
    phase: str,
    agent: str,
    candidates: list[dict],
    source_type: str,
    source_ref_type: str,
) -> None:
    for candidate in candidates:
        entry = BlackboardEntry(
            run_id=context.run_id,
            thread_id=context.thread.thread_id,
            request_id=request.request_id,
            kind="source_record",
            agent=agent,
            phase=phase,
            payload={
                "title": f"{source_type}:{candidate.get('symbol')}",
                "content": str(candidate),
                "source_type": source_type,
                "candidate": candidate,
            },
            source_refs=[{"type": source_ref_type, "run_id": context.run_id}],
            confidence=1.0,
            risk_flags=candidate.get("missing_evidence") or [],
        )
        support.add_entry(context, request, intent, entry)
        for methodology_entry in support.build_candidate_methodology_entries(
            context=context,
            request=request,
            phase=phase,
            agent=agent,
            candidate=candidate,
            source_type=source_type,
        ):
            support.add_entry(context, request, intent, methodology_entry)


def add_screening_scorecard(
    support: OrchestratorExecutionSupport,
    *,
    context: RuntimeContext,
    request: RequestEnvelope,
    intent: str,
    phase: str,
    agent: str,
    scorecard: dict,
) -> None:
    scorecard_entry = BlackboardEntry(
        run_id=context.run_id,
        thread_id=context.thread.thread_id,
        request_id=request.request_id,
        kind="source_record",
        agent=agent,
        phase=phase,
        payload={
            "title": "screening_scorecard",
            "content": str(scorecard),
            "source_type": "screening_scorecard",
            "scorecard": scorecard,
        },
        source_refs=[{"type": "screening", "run_id": context.run_id}],
        confidence=1.0,
        risk_flags=[],
    )
    support.add_entry(context, request, intent, scorecard_entry)
