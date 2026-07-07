from __future__ import annotations

from typing import Any

from research_system.agents.retriever.service import RETRIEVAL_SEARCH_BLACKBOARD_ONLY, RETRIEVAL_SEARCH_QDRANT
from research_system.runtime.execution.core.support import OrchestratorExecutionSupport
from research_system.runtime.execution.handlers.dataflow.collector_entries import (
    add_collector_outputs,
    add_stock_methodology_entries,
)
from research_system.runtime.execution.handlers.dataflow.collector_wait import (
    handle_collector_wait,
)
from research_system.runtime.execution.handlers.dataflow.data_context import (
    collect_blackboard_entries_json,
)
from research_system.runtime.execution.handlers.dataflow.retriever_entries import (
    add_retrieved_evidence,
)
from research_system.runtime.orchestration.orchestrator_context import RuntimeContext
from research_system.schemas.planning import ActionPlan, ActionSpec
from research_system.schemas.protocol import RequestEnvelope


def run_collector(
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
    if intent == "methodology_evolution":
        evolution_entries = support.run_methodology_evolution_collect(
            context=context,
            request=request,
            phase=phase,
            agent="collector",
        )
        for entry in evolution_entries:
            support.add_entry(context, request, intent, entry)
        return

    collector_query = str(action.inputs.get("query") or request.user_query)
    collector_constraints = dict(request.constraints)
    collector_constraints.update(action.inputs or {})
    payload = host.collector.run(intent=intent, query=collector_query, constraints=collector_constraints)
    support.extend_subagent_trace(context, agent="collector", phase=phase, payload=payload)
    handle_collector_wait(
        host,
        support,
        context=context,
        request=request,
        intent=intent,
        action=action,
        phase=phase,
        payload=payload,
    )
    add_collector_outputs(
        support,
        context=context,
        request=request,
        intent=intent,
        phase=phase,
        payload=payload,
    )
    if intent in {"stock_check", "stock_compare"}:
        add_stock_methodology_entries(
            support,
            context=context,
            request=request,
            intent=intent,
            phase=phase,
        )


def run_retriever(
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
    blackboard_entries = collect_blackboard_entries_json(context)
    llm_decision = support.resolve_llm_execution(
        context,
        request_id=request.request_id,
        agent="retriever",
        phase=phase,
        strategy="fallback_blackboard_only",
        provider=host.retriever.llm_provider,
        reserve=False,
    )
    search_mode = RETRIEVAL_SEARCH_QDRANT
    if llm_decision.degrade_reason == "turn_llm_budget_exhausted":
        search_mode = RETRIEVAL_SEARCH_BLACKBOARD_ONLY
    payload = host.retriever.run(
        intent=intent,
        query=request.user_query,
        blackboard_entries=blackboard_entries,
        planning_context_hints=dict(action.inputs.get("planning_context_hints") or {}),
        search_mode=search_mode,
        allow_llm_decision=llm_decision.can_use_llm,
        runtime_degraded_reason=llm_decision.degrade_reason,
    )
    support.record_payload_degradation(
        context,
        request_id=request.request_id,
        component="retriever",
        phase=phase,
        agent="retriever",
        payload=payload,
        intent=intent,
    )
    support.extend_subagent_trace(context, agent="retriever", phase=phase, payload=payload)
    add_retrieved_evidence(
        support,
        context=context,
        request=request,
        intent=intent,
        phase=phase,
        payload=payload,
        blackboard_entries=blackboard_entries,
    )
