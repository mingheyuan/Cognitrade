from __future__ import annotations

from research_system.services.routing.tables.route_table_cache_support import RouteTableCacheSupport
from research_system.services.routing.tables.route_table_defaults import build_default_intent_route_table
from research_system.services.routing.tables.route_table_health_support import RouteTableHealthSupport
from research_system.services.routing.tables.route_table_load_support import RouteTableLoadSupport
from research_system.services.routing.tables.route_table_models import IntentRouteTable


class IntentRouteTableStore:
    def __init__(
        self,
        *,
        backend=None,
        active_key: str = "cognitrade:router:intent_rules:active",
        key_prefix: str = "cognitrade:router:intent_rules:",
        last_good_key: str = "cognitrade:router:intent_rules:last_good",
        cache_ttl_seconds: float = 2.0,
        seed_defaults: bool = True,
        default_table: IntentRouteTable | None = None,
    ) -> None:
        self.backend = backend
        self.active_key = active_key
        self.key_prefix = key_prefix
        self.last_good_key = last_good_key
        self.seed_defaults = seed_defaults
        self._default_table = default_table or build_default_intent_route_table()
        self.cache = RouteTableCacheSupport(cache_ttl_seconds=cache_ttl_seconds)
        self.health = RouteTableHealthSupport()
        self.loader = RouteTableLoadSupport(
            backend=backend,
            active_key=active_key,
            key_prefix=key_prefix,
            last_good_key=last_good_key,
            seed_defaults=seed_defaults,
            default_table=self._default_table,
        )

    def load_table(self) -> IntentRouteTable:
        cached = self.cache.load_cached_table()
        if cached is not None:
            return cached
        table, refresh_source, refresh_timestamp = self.loader.load_uncached()
        return self.cache.store_table(
            table,
            version=table.version,
            refresh_source=refresh_source,
            refresh_timestamp=refresh_timestamp,
        )

    def replace_table(self, table: IntentRouteTable) -> None:
        if self.backend is None:
            self._default_table = table
            self.loader.default_table = table
            self.cache.store_table(
                table,
                version=table.version,
                refresh_source="default",
                refresh_timestamp=None,
            )
            return
        self.loader.replace_table(table)
        self.invalidate_cache()

    def invalidate_cache(self) -> None:
        self.cache.invalidate()

    def describe_runtime_state(self) -> dict[str, object]:
        return self.cache.describe_runtime_state(has_backend=self.backend is not None)

    def probe_backend_health(self) -> dict[str, object]:
        return self.health.probe_backend_health(self.backend)
