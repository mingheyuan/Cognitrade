from __future__ import annotations

from research_system.adapters.storage.redis_route_store import RedisRouteTableBackend
from research_system.runtime.platform.settings import Settings
from research_system.services.routing.tables.route_table_defaults import build_default_intent_route_table_from_settings
from research_system.services.routing.tables.route_table_store import IntentRouteTableStore


def build_intent_route_table_store(settings: Settings | None) -> IntentRouteTableStore:
    default_table = build_default_intent_route_table_from_settings(settings)
    if settings is None or not settings.router_redis_url:
        return IntentRouteTableStore(default_table=default_table, cache_ttl_seconds=0.0)
    backend = RedisRouteTableBackend(
        settings.router_redis_url,
        timeout_seconds=settings.router_redis_timeout_seconds,
    )
    return IntentRouteTableStore(
        backend=backend,
        active_key=settings.router_redis_active_key,
        key_prefix=settings.router_redis_key_prefix,
        last_good_key=settings.router_redis_last_good_key,
        cache_ttl_seconds=settings.router_rules_cache_ttl_seconds,
        seed_defaults=settings.router_seed_defaults,
        default_table=default_table,
    )
