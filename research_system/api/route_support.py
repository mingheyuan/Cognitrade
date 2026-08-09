from __future__ import annotations

from typing import Any

from research_system.api.dependencies import ApiPrincipal


def identity_scope_kwargs(
    target: Any, principal: ApiPrincipal | None
) -> dict[str, str]:
    service = getattr(target, "__self__", target)
    if principal is None or not principal.authenticated:
        return {}
    if not getattr(service, "identity_scoped", False):
        return {}
    return {
        "user_id": str(principal.user_id or ""),
        "tenant_id": str(principal.tenant_id or ""),
    }


def research_validation_capabilities(run_service: Any) -> dict[str, list[str] | None]:
    agent_os = getattr(run_service, "agent_os", None)
    capabilities: dict[str, list[str] | None] = {}
    for name in ("intents", "methods", "snapshot_types"):
        resolver = getattr(agent_os, f"supported_{name}", None)
        capabilities[f"supported_{name}"] = (
            list(resolver()) if callable(resolver) else None
        )
    return capabilities


__all__ = ["identity_scope_kwargs", "research_validation_capabilities"]
