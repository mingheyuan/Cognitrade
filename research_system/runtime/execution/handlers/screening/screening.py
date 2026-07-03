from __future__ import annotations

from typing import Any

from research_system.runtime.execution.core.support import OrchestratorExecutionSupport
from research_system.runtime.execution.handlers.screening.screening_context import (
    collect_screening_inputs,
)
from research_system.runtime.execution.handlers.screening.screening_enrichment import (
    run_screening_enrichment,
)
from research_system.runtime.execution.handlers.screening.screening_entries import (
    add_screening_candidates,
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
    support.extend_subagent_trace(
        context,
        agent="screener",
        phase=phase,
        payload={
            "subagent_trace": [
                {
                    "subagent_type": "screening_decider",
                    "status": "completed",
                    "objective": "Rank candidates and decide enrichment targets.",
                    "notes": list(payload.get("screening_notes") or []),
                }
            ]
        },
    )
    payload = run_screening_enrichment(
        host,
        support,
        context=context,
        request=request,
        intent=intent,
        phase=phase,
        source_payloads=source_payloads,
        screener_constraints=screener_constraints,
        payload=payload,
    )
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
    )
    scorecard = payload.get("scorecard") or {}
    add_screening_scorecard(
        support,
        context=context,
        request=request,
        intent=intent,
        phase=phase,
        agent="screener",
        scorecard=scorecard,
    )
