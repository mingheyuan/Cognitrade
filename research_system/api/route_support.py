from __future__ import annotations

from typing import Any

from starlette.concurrency import run_in_threadpool

from research_system.api.dependencies import ApiPrincipal
from research_system.api.errors import ApiError
from research_system.runtime.presentation.display import localize_public_error_fields
from research_system.strategies.errors import StrategyDomainError
from research_system.strategies.service import owner_scope


async def call_public_service(operation: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a synchronous API service operation and localize its public payload."""
    result = await run_in_threadpool(operation, *args, **kwargs)
    return localize_public_error_fields(result)


async def call_strategy_service(
    operation: Any,
    principal: ApiPrincipal,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Run a tenant-scoped strategy operation and translate domain failures."""
    scope = owner_scope(
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        actor_id=principal.actor_id,
    )
    try:
        return await run_in_threadpool(operation, scope, *args, **kwargs)
    except StrategyDomainError as exc:
        raise ApiError(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        ) from exc


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


__all__ = [
    "call_public_service",
    "call_strategy_service",
    "identity_scope_kwargs",
    "research_validation_capabilities",
]
