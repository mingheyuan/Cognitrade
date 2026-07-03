from __future__ import annotations

from importlib import import_module

__all__ = [
    "ChatDispatchDecision",
    "IntentRouteRule",
    "IntentRouteTable",
    "IntentRouteTableStore",
    "RouteMatchResult",
    "RouteMatcher",
    "RouteTarget",
    "RoutingDecision",
    "TaskRouter",
    "build_default_intent_route_table",
    "build_default_intent_route_table_from_settings",
    "build_intent_route_table_store",
    "extract_stock_symbols",
    "load_route_table_from_file",
    "primary_stock_symbol",
]

_EXPORTS = {
    "ChatDispatchDecision": ("research_system.services.routing.router.task_router_models", "ChatDispatchDecision"),
    "IntentRouteRule": ("research_system.services.routing.tables.route_table_models", "IntentRouteRule"),
    "IntentRouteTable": ("research_system.services.routing.tables.route_table_models", "IntentRouteTable"),
    "IntentRouteTableStore": ("research_system.services.routing.tables.route_table_store", "IntentRouteTableStore"),
    "RouteMatchResult": ("research_system.services.routing.matching.route_matcher_result_support", "RouteMatchResult"),
    "RouteMatcher": ("research_system.services.routing.matching.matcher", "RouteMatcher"),
    "RouteTarget": ("research_system.services.routing.tables.route_table_models", "RouteTarget"),
    "RoutingDecision": ("research_system.services.routing.router.task_router_models", "RoutingDecision"),
    "TaskRouter": ("research_system.services.routing.router.task_router", "TaskRouter"),
    "build_default_intent_route_table": (
        "research_system.services.routing.tables.route_table_defaults",
        "build_default_intent_route_table",
    ),
    "build_default_intent_route_table_from_settings": (
        "research_system.services.routing.tables.route_table_defaults",
        "build_default_intent_route_table_from_settings",
    ),
    "build_intent_route_table_store": (
        "research_system.services.routing.tables.route_table_runtime",
        "build_intent_route_table_store",
    ),
    "extract_stock_symbols": ("research_system.services.routing.stock_target_resolution", "extract_stock_symbols"),
    "load_route_table_from_file": (
        "research_system.services.routing.tables.route_table_defaults",
        "load_route_table_from_file",
    ),
    "primary_stock_symbol": ("research_system.services.routing.stock_target_resolution", "primary_stock_symbol"),
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
