from __future__ import annotations

from research_system.runtime.settings import Settings


def build_router_checks(settings: Settings, route_store, route_version: str, route_runtime: dict[str, object]) -> list[dict]:
    checks = [
        {
            "name": "router_defaults",
            "status": "passed" if settings.router_defaults_path.exists() else "failed",
            "path": str(settings.router_defaults_path),
        },
        {
            "name": "router_cache_ttl_seconds",
            "status": "passed" if settings.router_rules_cache_ttl_seconds >= 0 else "failed",
            "detail": str(settings.router_rules_cache_ttl_seconds),
        },
        {
            "name": "router_runtime",
            "status": "passed",
            "detail": (
                f"version={route_version}, "
                f"refresh_source={route_runtime.get('refresh_source')}, "
                f"cached_version={route_runtime.get('cached_version')}, "
                f"last_refresh={route_runtime.get('last_refresh_timestamp')}"
            ),
        },
    ]
    redis_health = route_store.probe_backend_health()
    checks.append(
        {
            "name": "router_redis",
            "status": (
                "passed"
                if settings.router_redis_url and redis_health.get("reachable")
                else "failed"
                if settings.router_redis_url
                else "skipped"
            ),
            "detail": (
                f"{settings.router_redis_url}; health={redis_health.get('detail')}"
                if settings.router_redis_url
                else "not_configured"
            ),
        }
    )
    return checks
