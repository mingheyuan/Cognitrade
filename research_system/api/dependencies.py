from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from research_system.api.config import ApiConfig
from research_system.api.errors import ApiError
from research_system.runtime.platform.settings import Settings


_BEARER_AUTH = HTTPBearer(
    auto_error=False,
    scheme_name="BearerAuth",
    description="Static Cognitrade API Bearer token",
)


@dataclass(frozen=True)
class ApiPrincipal:
    actor_id: str
    authenticated: bool
    user_id: str | None = None
    tenant_id: str | None = None
    roles: tuple[str, ...] = ()


@dataclass
class ApiRuntimeState:
    repo_root: Path
    settings: Settings
    config: ApiConfig
    agent_os: Any
    run_service: Any
    chat_service: Any
    event_source: Any
    finance_market_data: Any
    paper_trading: Any
    mutation_lock: asyncio.Lock


def get_api_runtime(request: Request) -> ApiRuntimeState:
    runtime = getattr(request.app.state, "api_runtime", None)
    if not isinstance(runtime, ApiRuntimeState):
        raise ApiError(
            status_code=503,
            code="api_not_ready",
            message="API runtime has not finished starting",
        )
    return runtime


def get_settings(request: Request) -> Settings:
    return get_api_runtime(request).settings


def get_agent_os(request: Request):
    return get_api_runtime(request).agent_os


def get_run_service(request: Request):
    return get_api_runtime(request).run_service


def get_chat_service(request: Request):
    return get_api_runtime(request).chat_service


def get_mutation_lock(request: Request) -> asyncio.Lock:
    return get_api_runtime(request).mutation_lock


def get_event_source(request: Request):
    return get_api_runtime(request).event_source


def get_finance_market_data(request: Request):
    return get_api_runtime(request).finance_market_data


def get_paper_trading(request: Request):
    return get_api_runtime(request).paper_trading


def get_repo_root(request: Request) -> Path:
    return get_api_runtime(request).repo_root


def require_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_BEARER_AUTH)] = None,
) -> ApiPrincipal:
    config = get_api_runtime(request).config
    return _authenticate_principal(request, config, credentials)


def _authenticate_principal(
    request: Request,
    config: ApiConfig,
    credentials: HTTPAuthorizationCredentials | None,
) -> ApiPrincipal:
    if not config.authentication_enabled:
        return ApiPrincipal(
            actor_id="local_development",
            authenticated=False,
            user_id=request.headers.get("X-User-ID") or None,
            tenant_id=request.headers.get("X-Tenant-ID") or None,
            roles=("admin", "user"),
        )

    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or not credentials.credentials.strip()
    ):
        raise ApiError(
            status_code=401,
            code="authentication_required",
            message="A Bearer token is required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials.strip()
    binding = config.principal_for_token(token)
    valid_primary_token = secrets.compare_digest(token, config.api_token or "")
    valid_admin_token = any(
        secrets.compare_digest(token, admin_token)
        for admin_token in config.admin_tokens
    )
    valid_bound_token = binding is not None
    if not (valid_primary_token or valid_admin_token or valid_bound_token):
        raise ApiError(
            status_code=401,
            code="invalid_token",
            message="The Bearer token is invalid",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if binding is not None:
        header_user_id = request.headers.get("X-User-ID") or ""
        header_tenant_id = request.headers.get("X-Tenant-ID") or ""
        if header_user_id and header_user_id != binding.user_id:
            raise ApiError(
                status_code=403,
                code="identity_header_conflict",
                message="The request identity does not match the authenticated principal",
            )
        if header_tenant_id and header_tenant_id != binding.tenant_id:
            raise ApiError(
                status_code=403,
                code="identity_header_conflict",
                message="The request identity does not match the authenticated principal",
            )
        return ApiPrincipal(
            actor_id=binding.actor_id,
            authenticated=True,
            user_id=binding.user_id,
            tenant_id=binding.tenant_id,
            roles=binding.roles,
        )

    user_id = request.headers.get("X-User-ID") or "api_token"
    tenant_id = request.headers.get("X-Tenant-ID") or "api_token"
    if config.has_explicit_admin_tokens():
        roles = (
            ("admin", "user")
            if any(secrets.compare_digest(token, admin_token) for admin_token in config.admin_tokens)
            else ("user",)
        )
    else:
        roles = ("admin", "user")
    return ApiPrincipal(
        actor_id="api_token",
        authenticated=True,
        user_id=user_id,
        tenant_id=tenant_id,
        roles=roles,
    )


def require_role(role: str):
    required_role = str(role or "").strip()
    if not required_role:
        raise ValueError("required API role must not be empty")

    def dependency(principal: Annotated[ApiPrincipal, Depends(require_principal)]) -> ApiPrincipal:
        if required_role not in principal.roles:
            raise ApiError(
                status_code=403,
                code="insufficient_role",
                message="The authenticated principal lacks the required role",
                details={"required_role": required_role},
            )
        return principal

    return dependency


__all__ = [
    "ApiPrincipal",
    "ApiRuntimeState",
    "get_api_runtime",
    "get_agent_os",
    "get_chat_service",
    "get_event_source",
    "get_mutation_lock",
    "get_repo_root",
    "get_settings",
    "require_principal",
    "require_role",
]
