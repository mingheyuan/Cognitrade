from __future__ import annotations

from research_system.schemas.core_runtime import PlanDecision


class MainAgentPlanRole:
    """Planning facet of the Main Agent, backed by the capability planner."""

    def __init__(self, planner) -> None:
        self.planner = planner

    def build_initial_graph(self, request):
        return self.planner.planner.build_lazy_graph(request)

    def expand_compound(self, **kwargs):
        patch = self.planner.expand_compound(**kwargs)
        return patch.model_copy(update={"actor": "main_agent.plan_agent"})

    def decide_expand_compound(self, **kwargs) -> PlanDecision:
        return self._decision_for_patch(self.expand_compound(**kwargs))

    def evaluate_frontier(self, **kwargs):
        evaluator = getattr(self.planner, "evaluate_frontier", None)
        if evaluator is None:
            return None
        patch = evaluator(**kwargs)
        if patch is None:
            return None
        return patch.model_copy(update={"actor": "main_agent.plan_agent"})

    def decide_frontier(self, **kwargs) -> PlanDecision:
        patch = self.evaluate_frontier(**kwargs)
        if patch is None:
            return PlanDecision(
                action="continue",
                reason="The current frontier satisfies the active capability requirements.",
            )
        return self._decision_for_patch(patch)

    @staticmethod
    def _decision_for_patch(patch) -> PlanDecision:
        action = "continue"
        if patch.decision_type == "replan":
            action = "replan"
        elif patch.decision_type == "stop":
            action = "stop"
        elif any(operation.op_type == "rerun_from" for operation in patch.operations):
            action = "retry"
        return PlanDecision(
            action=action,
            reason=(
                patch.notes[-1]
                if patch.notes
                else "Main Agent planning role updated the execution frontier."
            ),
            graph_patch=patch,
            confidence=patch.confidence,
        )


__all__ = ["MainAgentPlanRole"]
