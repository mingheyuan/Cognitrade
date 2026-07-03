from __future__ import annotations

from importlib import import_module

__all__ = [
    "IntentRouteRule",
    "IntentRouteTable",
    "IntentRouteTableStore",
    "RouteIntent",
    "RouteMatchType",
    "RouteTarget",
    "build_default_intent_route_table",
    "build_default_intent_route_table_from_settings",
    "build_intent_route_table_store",
    "load_route_table_from_file",
]

_EXPORTS = {
    "IntentRouteRule": ("research_system.services.routing.tables.route_table_models", "IntentRouteRule"),
    "IntentRouteTable": ("research_system.services.routing.tables.route_table_models", "IntentRouteTable"),
    "IntentRouteTableStore": ("research_system.services.routing.tables.route_table_store", "IntentRouteTableStore"),
    "RouteIntent": ("research_system.services.routing.tables.route_table_models", "RouteIntent"),
    "RouteMatchType": ("research_system.services.routing.tables.route_table_models", "RouteMatchType"),
    "RouteTarget": ("research_system.services.routing.tables.route_table_models", "RouteTarget"),
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
    "load_route_table_from_file": (
        "research_system.services.routing.tables.route_table_defaults",
        "load_route_table_from_file",
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
