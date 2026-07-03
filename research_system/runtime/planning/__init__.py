from __future__ import annotations

from importlib import import_module

__all__ = [
    "EntryPlanDecision",
    "EntryPlanPayload",
    "PlannerActionFactory",
    "PlannerContextHints",
    "PlannerDynamicPlanSupport",
    "PlannerEntryDecisionSupport",
    "PlannerStaticPlanSupport",
    "RuntimePlanner",
]

_EXPORTS = {
    "EntryPlanDecision": ("research_system.runtime.planning.models", "EntryPlanDecision"),
    "EntryPlanPayload": ("research_system.runtime.planning.models", "EntryPlanPayload"),
    "PlannerActionFactory": ("research_system.runtime.planning.action_factory", "PlannerActionFactory"),
    "PlannerContextHints": ("research_system.runtime.planning.context_hints", "PlannerContextHints"),
    "PlannerDynamicPlanSupport": (
        "research_system.runtime.planning.dynamic_plan_support",
        "PlannerDynamicPlanSupport",
    ),
    "PlannerEntryDecisionSupport": (
        "research_system.runtime.planning.entry_decision_support",
        "PlannerEntryDecisionSupport",
    ),
    "PlannerStaticPlanSupport": (
        "research_system.runtime.planning.static_plan_support",
        "PlannerStaticPlanSupport",
    ),
    "RuntimePlanner": ("research_system.runtime.planning.planner", "RuntimePlanner"),
}


def __getattr__(name: str):
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
