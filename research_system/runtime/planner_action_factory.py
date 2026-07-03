from __future__ import annotations

from research_system.schemas.planning import ActionPlan, ActionSpec


class PlannerActionFactory:
    def default_initial_actions(self, intent: str) -> list[str]:
        if intent == "stock_screen":
            return ["collect", "screen", "retrieve", "report"]
        return ["collect", "retrieve", "report"]

    def normalize_initial_actions(self, *, intent: str, actions: list[str]) -> list[str]:
        allowed = {"collect", "screen", "retrieve", "report"}
        normalized = [action for action in actions if action in allowed]
        if not normalized:
            return self.default_initial_actions(intent)
        if intent != "stock_screen":
            normalized = [action for action in normalized if action != "screen"]
        if "report" not in normalized:
            normalized.append("report")
        return normalized

    def build_entry_actions(
        self,
        *,
        intent: str,
        action_ids: list[str],
        planning_context_hints: dict[str, str] | None = None,
    ) -> list[ActionSpec]:
        action_specs: list[ActionSpec] = []
        inputs = {"planning_context_hints": planning_context_hints} if planning_context_hints else {}
        for action_id in action_ids:
            if action_id == "collect":
                objective = {
                    "stock_check": "Collect stock snapshots.",
                    "stock_compare": "Collect comparison stock snapshots.",
                    "stock_screen": "Build candidate universe and snapshots.",
                    "sector_research": "Collect sector snapshots.",
                }[intent]
                action_specs.append(
                    ActionSpec(
                        node_id="collect",
                        phase="collect",
                        agent="collector",
                        objective=objective,
                        inputs=inputs,
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
                    )
                )
            elif action_id == "retrieve":
                objective = {
                    "stock_check": "Retrieve relevant evidence.",
                    "stock_compare": "Retrieve comparison evidence.",
                    "stock_screen": "Retrieve screening evidence.",
                    "sector_research": "Retrieve relevant evidence.",
                }[intent]
                action_specs.append(
                    ActionSpec(
                        node_id="retrieve",
                        phase="retrieve",
                        agent="retriever",
                        objective=objective,
                        inputs=inputs,
                    )
                )
            elif action_id == "report":
                objective = {
                    "stock_check": "Compose final report.",
                    "stock_compare": "Compose comparison report.",
                    "stock_screen": "Compose watchlist-style screening report.",
                    "sector_research": "Compose final report.",
                }[intent]
                action_specs.append(
                    ActionSpec(
                        node_id="report",
                        phase="report",
                        agent="reporter",
                        objective=objective,
                        inputs=inputs,
                    )
                )
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
