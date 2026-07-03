from __future__ import annotations

from importlib import import_module

__all__ = [
    "ChatDispatchDecision",
    "ChatDispatchPayload",
    "IntentRoutePayload",
    "RESEARCH_INTENTS",
    "RoutingDecision",
    "TaskRouter",
    "TaskRouterChatSupport",
    "TaskRouterIdentifySupport",
    "TaskRouterLlmSupport",
    "TaskRouterRuleSupport",
]

_EXPORTS = {
    "ChatDispatchDecision": (
        "research_system.services.routing.router.task_router_models",
        "ChatDispatchDecision",
    ),
    "ChatDispatchPayload": (
        "research_system.services.routing.router.task_router_models",
        "ChatDispatchPayload",
    ),
    "IntentRoutePayload": (
        "research_system.services.routing.router.task_router_models",
        "IntentRoutePayload",
    ),
    "RESEARCH_INTENTS": (
        "research_system.services.routing.router.task_router_models",
        "RESEARCH_INTENTS",
    ),
    "RoutingDecision": (
        "research_system.services.routing.router.task_router_models",
        "RoutingDecision",
    ),
    "TaskRouter": ("research_system.services.routing.router.task_router", "TaskRouter"),
    "TaskRouterChatSupport": (
        "research_system.services.routing.router.task_router_chat_support",
        "TaskRouterChatSupport",
    ),
    "TaskRouterIdentifySupport": (
        "research_system.services.routing.router.task_router_identify_support",
        "TaskRouterIdentifySupport",
    ),
    "TaskRouterLlmSupport": (
        "research_system.services.routing.router.task_router_llm_support",
        "TaskRouterLlmSupport",
    ),
    "TaskRouterRuleSupport": (
        "research_system.services.routing.router.task_router_rule_support",
        "TaskRouterRuleSupport",
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
