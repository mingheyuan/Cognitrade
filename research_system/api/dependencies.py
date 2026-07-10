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


@dataclass
class ApiRuntimeState:
    repo_root: Path
    settings: Settings
    config: ApiConfig
    orchestrator: Any
    chat_service: Any
    event_source: Any
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


def get_orchestrator(request: Request):
    return get_api_runtime(request).orchestrator


def get_chat_service(request: Request):
    return get_api_runtime(request).chat_service


def get_mutation_lock(request: Request) -> asyncio.Lock:
    return get_api_runtime(request).mutation_lock


def get_event_source(request: Request):
    return get_api_runtime(request).event_source


def get_repo_root(request: Request) -> Path:
    return get_api_runtime(request).repo_root


def require_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_BEARER_AUTH)] = None,
) -> ApiPrincipal:
    config = get_api_runtime(request).config
    if not config.authentication_enabled:
        return ApiPrincipal(actor_id="local_development", authenticated=False)

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
    if not secrets.compare_digest(credentials.credentials.strip(), config.api_token or ""):
        raise ApiError(
            status_code=401,
            code="invalid_token",
            message="The Bearer token is invalid",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return ApiPrincipal(actor_id="api_token", authenticated=True)


__all__ = [
    "ApiPrincipal",
    "ApiRuntimeState",
    "get_api_runtime",
    "get_chat_service",
    "get_event_source",
    "get_mutation_lock",
    "get_orchestrator",
    "get_repo_root",
    "get_settings",
    "require_principal",
]
