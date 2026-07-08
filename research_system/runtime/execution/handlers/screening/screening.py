from __future__ import annotations

from typing import Any

from research_system.runtime.execution.core.support import OrchestratorExecutionSupport
from research_system.runtime.execution.handlers.screening.screening_context import (
    collect_screening_enrichment_context,
    collect_screening_inputs,
)
from research_system.runtime.execution.handlers.screening.screening_enrichment import (
    run_screening_enrichment,
)
from research_system.runtime.execution.handlers.screening.screening_entries import (
    add_screening_candidates,
    add_screening_enrichment_plan,
    add_screening_scorecard,
)
from research_system.runtime.orchestration.orchestrator_context import RuntimeContext
from research_system.schemas.planning import ActionPlan, ActionSpec
from research_system.schemas.protocol import RequestEnvelope


def run_screener(
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
    if action.node_id == "screen_enrich":
        _run_screen_enrich_action(
            host,
            support,
            context=context,
            request=request,
            intent=intent,
            action=action,
            phase=phase,
        )
        return

    _run_screen_action(
        host,
        support,
        context=context,
        request=request,
        intent=intent,
        action=action,
        phase=phase,
    )


def _run_screen_action(
    host: Any,
    support: OrchestratorExecutionSupport,
    *,
    context: RuntimeContext,
    request: RequestEnvelope,
    intent: str,
    action: ActionSpec,
    phase: str,
) -> None:
    screening_inputs = collect_screening_inputs(context)
    source_payloads = list(screening_inputs["source_payloads"])
    snapshot_payloads = list(screening_inputs["snapshot_payloads"])
    screener_constraints = dict(request.constraints)
    screener_constraints.update(action.inputs or {})
    payload = host.screener.run(
        query=request.user_query,
        source_payloads=source_payloads,
        snapshot_payloads=snapshot_payloads,
        constraints=screener_constraints,
    )
    _extend_screening_trace(
        support,
        context=context,
        phase=phase,
        objective="Rank candidates and decide enrichment targets.",
        payload=payload,
    )
    enrichment_targets = [item for item in list(payload.get("enrichment_targets") or []) if isinstance(item, dict)]
    scorecard = dict(payload.get("scorecard") or {})
    enrichment_pending = bool(intent == "stock_screen" and enrichment_targets)
    scorecard["enrichment_pending"] = enrichment_pending
    payload["scorecard"] = scorecard
    if enrichment_pending:
        add_screening_enrichment_plan(
            support,
            context=context,
            request=request,
            intent=intent,
            phase=phase,
            agent="screener",
            enrichment_plan={
                "enrichment_targets": enrichment_targets,
                "enrichment_target_count": len(enrichment_targets),
                "screening_notes": list(payload.get("screening_notes") or []),
                "scorecard": scorecard,
            },
        )
    include_methodology_entries = not enrichment_pending
    add_screening_candidates(
        support,
        context=context,
        request=request,
        intent=intent,
        phase=phase,
        agent="screener",
        candidates=list(payload.get("screened_candidates", [])),
        source_type="screen_candidate",
        source_ref_type="screening",
        include_methodology_entries=include_methodology_entries,
    )
    add_screening_candidates(
        support,
        context=context,
        request=request,
        intent=intent,
        phase=phase,
        agent="screener",
        candidates=list(payload.get("watchlist_candidates", [])),
        source_type="watchlist_candidate",
        source_ref_type="screening_watchlist",
        include_methodology_entries=include_methodology_entries,
    )
    add_screening_scorecard(
        support,
        context=context,
        request=request,
        intent=intent,
        phase=phase,
        agent="screener",
        scorecard=scorecard,
        source_ref_type="screening",
    )


def _run_screen_enrich_action(
    host: Any,
    support: OrchestratorExecutionSupport,
    *,
    context: RuntimeContext,
    request: RequestEnvelope,
    intent: str,
    action: ActionSpec,
    phase: str,
) -> None:
    enrichment_context = collect_screening_enrichment_context(context)
    source_payloads = list(enrichment_context["source_payloads"])
    screener_constraints = dict(request.constraints)
    screener_constraints.update(action.inputs or {})
    enrichment_plan = dict(enrichment_context.get("enrichment_plan") or {})
    scorecard = dict(enrichment_context.get("screening_scorecard") or {})
    enrichment_targets = [
        item for item in list(enrichment_plan.get("enrichment_targets") or [])
        if isinstance(item, dict)
    ]
    if not enrichment_targets:
        screen_candidates = [
            item for item in list(enrichment_context.get("screen_candidates") or [])
            if isinstance(item, dict)
        ]
        target_count = int(scorecard.get("enrichment_target_count") or 0)
        fallback_targets = [item for item in screen_candidates if not bool(item.get("deep_checked"))]
        if target_count > 0:
            fallback_targets = fallback_targets[:target_count]
        enrichment_targets = fallback_targets
    if not enrichment_targets:
        raise RuntimeError("missing_screening_enrichment_targets")
    payload = run_screening_enrichment(
        host,
        support,
        context=context,
        request=request,
        intent=intent,
        phase=phase,
        source_payloads=source_payloads,
        screener_constraints=screener_constraints,
        payload={"enrichment_targets": enrichment_targets},
    )
    final_scorecard = dict(payload.get("scorecard") or {})
    final_scorecard["enrichment_pending"] = False
    payload["scorecard"] = final_scorecard
    add_screening_candidates(
        support,
        context=context,
        request=request,
        intent=intent,
        phase=phase,
        agent="screener",
        candidates=list(payload.get("screened_candidates", [])),
        source_type="screen_candidate",
        source_ref_type="screening_enrichment",
        include_methodology_entries=True,
    )
    add_screening_candidates(
        support,
        context=context,
        request=request,
        intent=intent,
        phase=phase,
        agent="screener",
        candidates=list(payload.get("watchlist_candidates", [])),
        source_type="watchlist_candidate",
        source_ref_type="screening_enrichment",
        include_methodology_entries=True,
    )
    add_screening_scorecard(
        support,
        context=context,
        request=request,
        intent=intent,
        phase=phase,
        agent="screener",
        scorecard=final_scorecard,
        source_ref_type="screening_enrichment",
    )


def _extend_screening_trace(
    support: OrchestratorExecutionSupport,
    *,
    context: RuntimeContext,
    phase: str,
    objective: str,
    payload: dict,
) -> None:
    if any((payload or {}).get(key) for key in ("subagent_trace", "subagent_plans", "subagent_plan_patches")):
        support.extend_subagent_trace(context, agent="screener", phase=phase, payload=payload)
        return
    support.extend_subagent_trace(
        context,
        agent="screener",
        phase=phase,
        payload={
            "subagent_trace": [
                {
                    "subagent_type": "screening_decider",
                    "status": "completed",
                    "objective": objective,
                    "notes": list(payload.get("screening_notes") or []),
                }
            ]
        },
    )
