from __future__ import annotations

from typing import Any

from research_system.runtime.agent_handlers_review_context import (
    collect_review_request_context,
    collect_screen_review_context,
)
from research_system.runtime.agent_handlers_review_entries import (
    add_review_findings,
    add_review_summary,
)
from research_system.runtime.agent_handlers_review_events import (
    emit_reviewer_completed,
    emit_reviewer_requested,
    emit_screen_reviewer_completed,
    emit_screen_reviewer_requested,
)
from research_system.runtime.agent_handlers_review_flow import (
    run_reviewer_flow,
    run_screen_reviewer_flow,
)
from research_system.runtime.agent_handlers_review_trace import (
    extend_reviewer_trace,
    extend_screen_reviewer_trace,
)
from research_system.runtime.orchestrator_context import RuntimeContext
from research_system.runtime.orchestrator_execution_support import OrchestratorExecutionSupport
from research_system.schemas.planning import ActionPlan, ActionSpec
from research_system.schemas.protocol import RequestEnvelope


def run_reviewer(
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
    review_context = collect_review_request_context(context)
    review_request = review_context["review_request"]
    latest_sector_snapshot = review_context["latest_sector_snapshot"]
    latest_market_snapshot = review_context["latest_market_snapshot"]
    latest_fundamental_snapshot = review_context["latest_fundamental_snapshot"]
    latest_notice_snapshot = review_context["latest_notice_snapshot"]
    source_entries = list(review_context["source_entries"])
    reviewer_payload = run_reviewer_flow(
        host,
        request=request,
        action=action,
        review_request=review_request,
        source_entries=source_entries,
        latest_sector_snapshot=latest_sector_snapshot,
        latest_market_snapshot=latest_market_snapshot,
        latest_fundamental_snapshot=latest_fundamental_snapshot,
        latest_notice_snapshot=latest_notice_snapshot,
    )
    summary = reviewer_payload.get("summary") or {}
    extend_reviewer_trace(
        support,
        context=context,
        phase=phase,
        source_entry_count=len(source_entries),
        summary=summary,
    )
    emit_reviewer_requested(
        support,
        context=context,
        request=request,
        intent=intent,
        phase=phase,
        review_target_run_id=(review_request or {}).get("review_target_run_id"),
    )
    source_refs = [
        {
            "type": "posterior_review",
            "review_target_run_id": (review_request or {}).get("review_target_run_id"),
            "run_id": context.run_id,
        }
    ]
    add_review_findings(
        support,
        context=context,
        request=request,
        intent=intent,
        phase=phase,
        agent="reviewer",
        findings=list(reviewer_payload.get("findings", [])),
        source_refs=source_refs,
    )
    add_review_summary(
        support,
        context=context,
        request=request,
        intent=intent,
        phase=phase,
        agent="reviewer",
        summary=summary,
        source_refs=source_refs,
    )
    for entry in support.build_review_methodology_entries(
        context=context,
        request=request,
        intent=intent,
        phase=phase,
        agent="reviewer",
        review_request=review_request or {},
        latest_market_snapshot=latest_market_snapshot,
            latest_fundamental_snapshot=latest_fundamental_snapshot,
    ):
        support.add_entry(context, request, intent, entry)
    emit_reviewer_completed(
        support,
        context=context,
        request=request,
        intent=intent,
        phase=phase,
        summary=summary,
    )


def run_screen_reviewer(
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
    screen_review_context = collect_screen_review_context(context)
    screen_candidates = list(screen_review_context["screen_candidates"])
    screening_scorecard = screen_review_context["screening_scorecard"]
    emit_screen_reviewer_requested(
        support,
        context=context,
        request=request,
        intent=intent,
        phase=phase,
        candidate_count=len(screen_candidates),
    )
    reviewer_payload = run_screen_reviewer_flow(
        host,
        context=context,
        action=action,
        screen_candidates=screen_candidates,
        screening_scorecard=screening_scorecard,
    )
    summary = reviewer_payload.get("summary") or {}
    extend_screen_reviewer_trace(
        support,
        context=context,
        phase=phase,
        summary=summary,
    )
    source_refs = [{"type": "screen_review", "run_id": context.run_id}]
    add_review_findings(
        support,
        context=context,
        request=request,
        intent=intent,
        phase=phase,
        agent="screen_reviewer",
        findings=list(reviewer_payload.get("findings", [])),
        source_refs=source_refs,
    )
    add_review_summary(
        support,
        context=context,
        request=request,
        intent=intent,
        phase=phase,
        agent="screen_reviewer",
        summary=summary,
        source_refs=source_refs,
    )
    emit_screen_reviewer_completed(
        support,
        context=context,
        request=request,
        intent=intent,
        phase=phase,
        summary=summary,
    )
