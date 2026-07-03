from __future__ import annotations

import time

from research_system.services.route_table_models import IntentRouteTable


class RouteTableCacheSupport:
    def __init__(self, *, cache_ttl_seconds: float) -> None:
        self.cache_ttl_seconds = max(0.0, cache_ttl_seconds)
        self.cached_table: IntentRouteTable | None = None
        self.cached_at = 0.0
        self.cached_version: str | None = None
        self.last_refresh_source: str = "default"
        self.last_refresh_timestamp: str | None = None

    def load_cached_table(self) -> IntentRouteTable | None:
        now = time.monotonic()
        if self.cached_table is None:
            return None
        if self.cache_ttl_seconds <= 0 or now - self.cached_at < self.cache_ttl_seconds:
            return self.cached_table
        return None

    def store_table(
        self,
        table: IntentRouteTable,
        *,
        version: str | None,
        refresh_source: str,
        refresh_timestamp: str | None,
    ) -> IntentRouteTable:
        self.cached_table = table
        self.cached_at = time.monotonic()
        self.cached_version = version
        self.last_refresh_source = refresh_source
        self.last_refresh_timestamp = refresh_timestamp
        return table

    def invalidate(self) -> None:
        self.cached_table = None
        self.cached_at = 0.0
        self.cached_version = None
        self.last_refresh_source = "default"
        self.last_refresh_timestamp = None

    def describe_runtime_state(self, *, has_backend: bool) -> dict[str, object]:
        return {
            "cached_version": self.cached_version,
            "cache_ttl_seconds": self.cache_ttl_seconds,
            "refresh_source": self.last_refresh_source,
            "last_refresh_timestamp": self.last_refresh_timestamp,
            "has_backend": has_backend,
        }
