from __future__ import annotations

from typing import Any

from research_system.runtime.execution.core.support import OrchestratorExecutionSupport
from research_system.runtime.execution.handlers.distillation.distillation_context import (
    collect_critic_inputs,
    collect_distiller_source_records,
)
from research_system.runtime.execution.handlers.distillation.distillation_entries import (
    add_critic_outputs,
    add_distillation_outputs,
)
from research_system.runtime.orchestration.orchestrator_context import RuntimeContext
from research_system.schemas.planning import ActionPlan, ActionSpec
from research_system.schemas.protocol import RequestEnvelope


def run_distiller(
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
    _ = plan
    _ = action
    source_records = collect_distiller_source_records(context)
    payload = host.distiller.run(query=request.user_query, source_records=source_records)
    support.extend_subagent_trace(
        context,
        agent="distiller",
        phase=phase,
        payload={
            "subagent_trace": [
                {
                    "subagent_type": "distillation_pipeline",
                    "status": "completed",
                    "objective": "Distill source records into logic blocks and dimensions.",
                    "notes": list(((payload.get("summary") or {}).get("processing_notes") or [])),
                }
            ]
        },
    )
    add_distillation_outputs(
        support,
        context=context,
        request=request,
        intent=intent,
        phase=phase,
        payload=payload,
    )


def run_critic(
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
    _ = plan
    _ = action
    critic_inputs = collect_critic_inputs(context)
    logic_blocks = list(critic_inputs["logic_blocks"])
    distilled_dimensions = list(critic_inputs["distilled_dimensions"])
    distillation_summary = critic_inputs["distillation_summary"]
    critic_payload = host.critic.run(
        distillation_summary=distillation_summary,
        logic_blocks=logic_blocks,
        distilled_dimensions=distilled_dimensions,
    )
    support.extend_subagent_trace(
        context,
        agent="critic",
        phase=phase,
        payload={
            "subagent_trace": [
                {
                    "subagent_type": "critic_review_pipeline",
                    "status": "completed",
                    "objective": "Review distillation risks and build scorecard.",
                    "notes": list((critic_payload.get("summary") or {}).get("notes") or []),
                }
            ]
        },
    )
    summary = critic_payload.get("summary") or {}
    scorecard = critic_payload.get("scorecard") or {}
    support.emit_event(
        context,
        request_id=request.request_id,
        intent=intent,
        event_type="review_requested",
        phase=phase,
        agent="critic",
        message="Critic review requested",
        payload={
            "review_required": summary.get("review_required", False),
            "finding_count": summary.get("finding_count", 0),
        },
    )
    add_critic_outputs(
        support,
        context=context,
        request=request,
        intent=intent,
        phase=phase,
        critic_payload=critic_payload,
    )
    support.emit_event(
        context,
        request_id=request.request_id,
        intent=intent,
        event_type="review_completed",
        phase=phase,
        agent="critic",
        message="Critic review completed",
        payload={**summary, "scorecard": scorecard},
    )
