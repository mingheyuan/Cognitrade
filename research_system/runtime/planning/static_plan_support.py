from __future__ import annotations

from research_system.schemas.planning import ActionPlan, ActionSpec


class PlannerStaticPlanSupport:
    def __init__(self, host) -> None:
        self.host = host

    def build_static_plan(self, *, intent: str, request, context_hints: dict, planning_context_hints: dict):
        if intent == "distill_kol_logic":
            actions = [
                ActionSpec(
                    node_id="collect",
                    phase="collect",
                    agent="collector",
                    objective="Load source records.",
                    max_retries=1,
                    cache_scope="run",
                    cache_key="distill_kol_logic:collect",
                ),
                ActionSpec(
                    node_id="distill",
                    phase="distill",
                    agent="distiller",
                    objective="Distill source content.",
                    depends_on=["collect"],
                    max_retries=1,
                    cache_scope="run",
                    cache_key="distill_kol_logic:distill",
                ),
                ActionSpec(
                    node_id="critic",
                    phase="review",
                    agent="critic",
                    objective="Critique distilled output.",
                    depends_on=["distill"],
                    max_retries=1,
                    cache_scope="run",
                    cache_key="distill_kol_logic:critic",
                ),
                ActionSpec(
                    node_id="retrieve",
                    phase="retrieve",
                    agent="retriever",
                    objective="Retrieve distilled evidence.",
                    depends_on=["critic"],
                    max_retries=1,
                    cache_scope="run",
                    cache_key="distill_kol_logic:retrieve",
                ),
                ActionSpec(
                    node_id="report",
                    phase="report",
                    agent="reporter",
                    objective="Compose final distillation report.",
                    depends_on=["retrieve"],
                    max_retries=1,
                    cache_scope="run",
                    cache_key="distill_kol_logic:report",
                ),
            ]
            return ActionPlan(intent=intent, strategy="static", actions=actions, entry_strategy="explicit_intent")
        if intent == "review_thesis":
            actions = [
                ActionSpec(
                    node_id="collect",
                    phase="collect",
                    agent="collector",
                    objective="Collect posterior review inputs.",
                    inputs={"planning_context_hints": planning_context_hints} if planning_context_hints else {},
                    max_retries=1,
                    cache_scope="run",
                    cache_key="review_thesis:collect",
                ),
                ActionSpec(
                    node_id="retrieve",
                    phase="retrieve",
                    agent="retriever",
                    objective="Retrieve review evidence.",
                    inputs={"planning_context_hints": planning_context_hints} if planning_context_hints else {},
                    depends_on=["collect"],
                    max_retries=1,
                    cache_scope="run",
                    cache_key="review_thesis:retrieve",
                ),
                ActionSpec(
                    node_id="report",
                    phase="report",
                    agent="reporter",
                    objective="Compose final review report.",
                    inputs={"planning_context_hints": planning_context_hints} if planning_context_hints else {},
                    depends_on=["retrieve"],
                    max_retries=1,
                    cache_scope="run",
                    cache_key="review_thesis:report",
                ),
            ]
            return self.host.action_factory.build_research_plan(
                intent=intent,
                actions=actions,
                notes=["review_thesis uses dynamic supervisor"],
                entry_strategy="explicit_intent",
                entry_reason=self.host.context_hints.merge_entry_reason(
                    "review_thesis always uses research subagents",
                    context_hints,
                ),
                subagent_required=True,
                context_hints=context_hints,
                initial_actions=["collect", "retrieve", "report"],
                reason="review_thesis always uses research subagents",
                strategy="dynamic",
            )
        if intent == "methodology_evolution":
            actions = [
                ActionSpec(
                    node_id="collect",
                    phase="collect",
                    agent="collector",
                    objective="Load methodology evolution inputs.",
                    max_retries=1,
                    cache_scope="run",
                    cache_key="methodology_evolution:collect",
                ),
                ActionSpec(
                    node_id="retrieve",
                    phase="retrieve",
                    agent="retriever",
                    objective="Retrieve methodology evolution evidence.",
                    depends_on=["collect"],
                    max_retries=1,
                    cache_scope="run",
                    cache_key="methodology_evolution:retrieve",
                ),
                ActionSpec(
                    node_id="report",
                    phase="report",
                    agent="reporter",
                    objective="Compose methodology evolution report.",
                    depends_on=["retrieve"],
                    max_retries=1,
                    cache_scope="run",
                    cache_key="methodology_evolution:report",
                ),
            ]
            source = "explicit_intent" if request.intent else "rule"
            reason = "methodology_evolution uses internal evolution services"
            return self.host.action_factory.build_research_plan(
                intent=intent,
                actions=actions,
                entry_strategy=source,
                entry_reason=self.host.context_hints.merge_entry_reason(reason, context_hints),
                subagent_required=True,
                context_hints=context_hints,
                initial_actions=["collect", "retrieve", "report"],
                reason=reason,
                strategy="static",
            )
        return None
