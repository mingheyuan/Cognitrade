from __future__ import annotations

from research_system.runtime.execution.core.support import OrchestratorExecutionSupport
from research_system.runtime.orchestration.orchestrator_context import RuntimeContext
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.runtime import BlackboardEntry


def add_distillation_outputs(
    support: OrchestratorExecutionSupport,
    *,
    context: RuntimeContext,
    request: RequestEnvelope,
    intent: str,
    phase: str,
    payload: dict,
) -> None:
    for logic_block in payload.get("logic_blocks", []):
        entry = BlackboardEntry(
            run_id=context.run_id,
            thread_id=context.thread.thread_id,
            request_id=request.request_id,
            kind="logic_block",
            agent="distiller",
            phase=phase,
            payload=logic_block,
            source_refs=[
                {
                    "type": "distillation",
                    "source_record_id": logic_block.get("source_record_id"),
                    "run_id": context.run_id,
                }
            ],
            confidence=logic_block.get("confidence"),
            risk_flags=logic_block.get("risk_flags") or [],
        )
        support.add_entry(context, request, intent, entry)
    for dimension in payload.get("distilled_dimensions", []):
        entry = BlackboardEntry(
            run_id=context.run_id,
            thread_id=context.thread.thread_id,
            request_id=request.request_id,
            kind="distilled_dimension",
            agent="distiller",
            phase=phase,
            payload=dimension,
            source_refs=[
                {
                    "type": "distillation",
                    "supported_claim_block_ids": dimension.get("supported_claim_block_ids", []),
                    "run_id": context.run_id,
                }
            ],
            confidence=dimension.get("confidence"),
            risk_flags=[],
        )
        support.add_entry(context, request, intent, entry)
    summary_entry = BlackboardEntry(
        run_id=context.run_id,
        thread_id=context.thread.thread_id,
        request_id=request.request_id,
        kind="source_record",
        agent="distiller",
        phase=phase,
        payload={
            "title": "distillation_summary",
            "content": str(payload.get("summary") or {}),
            "source_type": "distillation_summary",
            "summary": payload.get("summary") or {},
        },
        source_refs=[{"type": "distillation", "run_id": context.run_id}],
        confidence=payload.get("summary", {}).get("overall_confidence"),
        risk_flags=payload.get("summary", {}).get("major_risks") or [],
    )
    support.add_entry(context, request, intent, summary_entry)


def add_critic_outputs(
    support: OrchestratorExecutionSupport,
    *,
    context: RuntimeContext,
    request: RequestEnvelope,
    intent: str,
    phase: str,
    critic_payload: dict,
) -> dict[str, dict]:
    summary = critic_payload.get("summary") or {}
    scorecard = critic_payload.get("scorecard") or {}
    if scorecard:
        scorecard_entry = BlackboardEntry(
            run_id=context.run_id,
            thread_id=context.thread.thread_id,
            request_id=request.request_id,
            kind="source_record",
            agent="critic",
            phase=phase,
            payload={
                "title": "distillation_scorecard",
                "content": str(scorecard),
                "source_type": "distillation_scorecard",
                "scorecard": scorecard,
            },
            source_refs=[{"type": "critic_review", "run_id": context.run_id}],
            confidence=1.0,
            risk_flags=[],
        )
        support.add_entry(context, request, intent, scorecard_entry)
    for finding in critic_payload.get("findings", []):
        entry = BlackboardEntry(
            run_id=context.run_id,
            thread_id=context.thread.thread_id,
            request_id=request.request_id,
            kind="review_finding",
            agent="critic",
            phase=phase,
            status="needs_review" if finding.get("verdict") == "needs_review" else "active",
            payload=finding,
            source_refs=[{"type": "critic_review", "run_id": context.run_id}],
            confidence=1.0,
            risk_flags=[finding.get("finding_type")] if finding.get("finding_type") else [],
        )
        support.add_entry(context, request, intent, entry)
    return {"summary": summary, "scorecard": scorecard}
