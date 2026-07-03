from __future__ import annotations

from importlib import import_module

__all__ = [
    "RouteMatchResult",
    "RouteMatcher",
    "RouteMatcherResultSupport",
    "RouteMatcherSemanticSupport",
    "RouteMatcherTextSupport",
]

_EXPORTS = {
    "RouteMatchResult": (
        "research_system.services.routing.matching.route_matcher_result_support",
        "RouteMatchResult",
    ),
    "RouteMatcher": ("research_system.services.routing.matching.matcher", "RouteMatcher"),
    "RouteMatcherResultSupport": (
        "research_system.services.routing.matching.route_matcher_result_support",
        "RouteMatcherResultSupport",
    ),
    "RouteMatcherSemanticSupport": (
        "research_system.services.routing.matching.route_matcher_semantic_support",
        "RouteMatcherSemanticSupport",
    ),
    "RouteMatcherTextSupport": (
        "research_system.services.routing.matching.route_matcher_text_support",
        "RouteMatcherTextSupport",
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
