from __future__ import annotations

from research_system.adapters.storage.redis_route_store import RouteTableBackendError


class RouteTableHealthSupport:
    def probe_backend_health(self, backend) -> dict[str, object]:
        if backend is None:
            return {
                "enabled": False,
                "reachable": False,
                "detail": "not_configured",
            }
        ping = getattr(backend, "ping", None)
        if not callable(ping):
            return {
                "enabled": True,
                "reachable": False,
                "detail": "ping_unsupported",
            }
        try:
            reachable = bool(ping())
        except RouteTableBackendError as exc:
            return {
                "enabled": True,
                "reachable": False,
                "detail": str(exc),
            }
        return {
            "enabled": True,
            "reachable": reachable,
            "detail": "ok" if reachable else "ping_failed",
        }
