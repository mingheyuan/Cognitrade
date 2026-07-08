from __future__ import annotations

from research_system.schemas.planning import ActionPlan, ActionSpec


class PlannerActionFactory:
    def default_initial_actions(self, intent: str) -> list[str]:
        if intent == "stock_screen":
            return ["collect", "screen", "retrieve", "report"]
        return ["collect", "retrieve", "report"]

    def normalize_initial_actions(self, *, intent: str, actions: list[str]) -> list[str]:
        allowed_order = {
            "stock_screen": ["collect", "screen", "screen_enrich", "screen_review", "retrieve", "report"],
            "review_thesis": ["collect", "review", "retrieve", "report"],
        }.get(intent, ["collect", "retrieve", "report"])
        allowed = set(allowed_order)
        normalized = [action for action in actions if action in allowed]
        if not normalized:
            return self.default_initial_actions(intent)
        ordered = [action for action in allowed_order if action in normalized]
        if "collect" not in ordered:
            ordered.insert(0, "collect")
        if "report" not in ordered:
            ordered.append("report")
        return ordered

    def build_entry_actions(
        self,
        *,
        intent: str,
        action_ids: list[str],
        planning_context_hints: dict[str, str] | None = None,
    ) -> list[ActionSpec]:
        action_specs: list[ActionSpec] = []
        action_ids = self.normalize_initial_actions(intent=intent, actions=action_ids)
        inputs = {"planning_context_hints": planning_context_hints} if planning_context_hints else {}
        dependency_chain: list[str] = []
        for action_id in action_ids:
            if action_id == "collect":
                objective = {
                    "stock_check": "Collect stock snapshots.",
                    "stock_compare": "Collect comparison stock snapshots.",
                    "stock_screen": "Build candidate universe and snapshots.",
                    "sector_research": "Collect sector snapshots.",
                    "review_thesis": "Collect posterior review inputs.",
                }[intent]
                action_specs.append(
                    ActionSpec(
                        node_id="collect",
                        phase="collect",
                        agent="collector",
                        objective=objective,
                        inputs=inputs,
                        depends_on=dependency_chain[:],
                        max_retries=1,
                        cache_scope="run",
                        cache_key=f"{intent}:collect",
                    )
                )
            elif action_id == "screen" and intent == "stock_screen":
                action_specs.append(
                    ActionSpec(
                        node_id="screen",
                        phase="screen",
                        agent="screener",
                        objective="Screen and rank stock candidates.",
                        inputs=inputs,
                        depends_on=dependency_chain[:],
                        max_retries=1,
                        cache_scope="run",
                        cache_key=f"{intent}:screen",
                    )
                )
            elif action_id == "screen_enrich" and intent == "stock_screen":
                action_specs.append(
                    ActionSpec(
                        node_id="screen_enrich",
                        phase="screen",
                        agent="screener",
                        objective="Enrich top screening candidates and rerank screening outputs.",
                        inputs=inputs,
                        tags=["dynamic_inserted", "enrichment"],
                        depends_on=dependency_chain[:],
                        max_retries=1,
                        failure_mode="isolate",
                        cache_scope="run",
                        cache_key="stock_screen:screen_enrich",
                    )
                )
            elif action_id == "screen_review" and intent == "stock_screen":
                action_specs.append(
                    ActionSpec(
                        node_id="screen_review",
                        phase="review",
                        agent="screen_reviewer",
                        objective="Review screened candidates before retrieval/reporting.",
                        inputs=inputs,
                        tags=["dynamic_inserted"],
                        depends_on=dependency_chain[:],
                        max_retries=1,
                        cache_scope="run",
                        cache_key="stock_screen:screen_review",
                    )
                )
            elif action_id == "review" and intent == "review_thesis":
                action_specs.append(
                    ActionSpec(
                        node_id="review",
                        phase="review",
                        agent="reviewer",
                        objective="Run posterior review after verifying source context and snapshots.",
                        inputs=inputs,
                        tags=["dynamic_inserted"],
                        depends_on=dependency_chain[:],
                        max_retries=1,
                        cache_scope="run",
                        cache_key="review_thesis:review",
                    )
                )
            elif action_id == "retrieve":
                objective = {
                    "stock_check": "Retrieve relevant evidence.",
                    "stock_compare": "Retrieve comparison evidence.",
                    "stock_screen": "Retrieve screening evidence.",
                    "sector_research": "Retrieve relevant evidence.",
                    "review_thesis": "Retrieve review evidence.",
                }[intent]
                action_specs.append(
                    ActionSpec(
                        node_id="retrieve",
                        phase="retrieve",
                        agent="retriever",
                        objective=objective,
                        inputs=inputs,
                        depends_on=dependency_chain[:],
                        max_retries=1,
                        cache_scope="run",
                        cache_key=f"{intent}:retrieve",
                    )
                )
            elif action_id == "report":
                objective = {
                    "stock_check": "Compose final report.",
                    "stock_compare": "Compose comparison report.",
                    "stock_screen": "Compose watchlist-style screening report.",
                    "sector_research": "Compose final report.",
                    "review_thesis": "Compose final review report.",
                }[intent]
                action_specs.append(
                    ActionSpec(
                        node_id="report",
                        phase="report",
                        agent="reporter",
                        objective=objective,
                        inputs=inputs,
                        depends_on=dependency_chain[:],
                        max_retries=1,
                        cache_scope="run",
                        cache_key=f"{intent}:report",
                    )
                )
            if action_specs:
                dependency_chain = [action_specs[-1].node_id]
        return action_specs

    def build_research_plan(
        self,
        *,
        intent: str,
        actions: list[ActionSpec],
        entry_strategy: str,
        entry_reason: str | None,
        subagent_required: bool,
        context_hints: dict[str, str],
        initial_actions: list[str],
        reason: str | None,
        strategy: str = "dynamic",
        notes: list[str] | None = None,
    ) -> ActionPlan:
        return ActionPlan(
            intent=intent,
            strategy=strategy,
            actions=actions,
            notes=list(notes or []),
            entry_strategy=entry_strategy,
            entry_reason=entry_reason,
            subagent_required=subagent_required,
            dispatch_trace={
                "mode": "research",
                "intent": intent,
                "source": entry_strategy,
                "reason": reason,
                "context_hints": context_hints,
                "subagent_required": subagent_required,
                "initial_actions": initial_actions,
            },
        )
