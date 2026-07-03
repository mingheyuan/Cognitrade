from __future__ import annotations

from importlib import import_module

__all__ = [
    "ReviewThesisView",
    "RuntimeSupervisor",
    "SectorResearchView",
    "StockCheckView",
    "StockScreenView",
    "SupervisorBlackboardReviewSupport",
    "SupervisorBlackboardScreenSupport",
    "SupervisorBlackboardSectorSupport",
    "SupervisorBlackboardSnapshotSupport",
    "SupervisorBlackboardSourceSupport",
    "SupervisorBlackboardStockCheckSupport",
    "SupervisorBlackboardView",
    "SupervisorDecisionFactory",
    "SupervisorReviewThesisSupport",
    "SupervisorSectorResearchSupport",
    "SupervisorStockCheckSupport",
    "SupervisorStockScreenSupport",
]

_EXPORTS = {
    "ReviewThesisView": ("research_system.runtime.supervision.blackboard_models", "ReviewThesisView"),
    "RuntimeSupervisor": ("research_system.runtime.supervision.supervisor", "RuntimeSupervisor"),
    "SectorResearchView": ("research_system.runtime.supervision.blackboard_models", "SectorResearchView"),
    "StockCheckView": ("research_system.runtime.supervision.blackboard_models", "StockCheckView"),
    "StockScreenView": ("research_system.runtime.supervision.blackboard_models", "StockScreenView"),
    "SupervisorBlackboardReviewSupport": (
        "research_system.runtime.supervision.blackboard_review_support",
        "SupervisorBlackboardReviewSupport",
    ),
    "SupervisorBlackboardScreenSupport": (
        "research_system.runtime.supervision.blackboard_screen_support",
        "SupervisorBlackboardScreenSupport",
    ),
    "SupervisorBlackboardSectorSupport": (
        "research_system.runtime.supervision.blackboard_sector_support",
        "SupervisorBlackboardSectorSupport",
    ),
    "SupervisorBlackboardSnapshotSupport": (
        "research_system.runtime.supervision.blackboard_snapshot_support",
        "SupervisorBlackboardSnapshotSupport",
    ),
    "SupervisorBlackboardSourceSupport": (
        "research_system.runtime.supervision.blackboard_source_support",
        "SupervisorBlackboardSourceSupport",
    ),
    "SupervisorBlackboardStockCheckSupport": (
        "research_system.runtime.supervision.blackboard_stock_check_support",
        "SupervisorBlackboardStockCheckSupport",
    ),
    "SupervisorBlackboardView": (
        "research_system.runtime.supervision.blackboard_view",
        "SupervisorBlackboardView",
    ),
    "SupervisorDecisionFactory": (
        "research_system.runtime.supervision.decision_factory",
        "SupervisorDecisionFactory",
    ),
    "SupervisorReviewThesisSupport": (
        "research_system.runtime.supervision.review_thesis_support",
        "SupervisorReviewThesisSupport",
    ),
    "SupervisorSectorResearchSupport": (
        "research_system.runtime.supervision.sector_research_support",
        "SupervisorSectorResearchSupport",
    ),
    "SupervisorStockCheckSupport": (
        "research_system.runtime.supervision.stock_check_support",
        "SupervisorStockCheckSupport",
    ),
    "SupervisorStockScreenSupport": (
        "research_system.runtime.supervision.stock_screen_support",
        "SupervisorStockScreenSupport",
    ),
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
