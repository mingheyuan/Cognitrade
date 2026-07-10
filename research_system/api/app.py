from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from research_system.api.chat_service import ChatService
from research_system.api.config import ApiConfig
from research_system.api.dependencies import ApiRuntimeState, require_principal
from research_system.api.errors import install_exception_handlers
from research_system.api.routes import admin, chat, runs, system
from research_system.api.sse import RunEventSource
from research_system.runtime.core.id_utils import generate_ulid
from research_system.runtime.orchestration.orchestrator import ResearchOrchestrator
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout


LOGGER = logging.getLogger(__name__)
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]


def create_app(
    *,
    settings: Settings | None = None,
    api_config: ApiConfig | None = None,
    orchestrator_factory: Callable[[Settings], Any] | None = None,
    chat_service_factory: Callable[[Settings], Any] | None = None,
    event_source_factory: Callable[[Settings, ApiConfig], Any] | None = None,
    repo_root: Path | None = None,
) -> FastAPI:
    resolved_repo_root = (repo_root or _DEFAULT_REPO_ROOT).resolve()
    resolved_api_config = api_config or ApiConfig.from_env(resolved_repo_root)
    build_orchestrator = orchestrator_factory or ResearchOrchestrator
    build_chat_service = chat_service_factory or ChatService
    build_event_source = event_source_factory or RunEventSource

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        effective_settings = settings or Settings.from_env(resolved_repo_root)
        effective_settings.validate()
        resolved_api_config.validate()
        ensure_workspace_layout(effective_settings)

        orchestrator = build_orchestrator(effective_settings)
        try:
            runtime = ApiRuntimeState(
                repo_root=resolved_repo_root,
                settings=effective_settings,
                config=resolved_api_config,
                orchestrator=orchestrator,
                chat_service=build_chat_service(effective_settings),
                event_source=build_event_source(effective_settings, resolved_api_config),
                mutation_lock=asyncio.Lock(),
            )
            application.state.api_runtime = runtime
            if not resolved_api_config.authentication_enabled:
                LOGGER.warning("Cognitrade API authentication is disabled for local development")
            yield
        finally:
            application.state.api_runtime = None
            close = getattr(orchestrator, "close", None)
            if callable(close):
                close()

    application = FastAPI(
        title="Cognitrade API",
        description="A-share multi-agent research runtime HTTP adapter",
        version="1.0.0",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_api_config.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Last-Event-ID", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )

    @application.middleware("http")
    async def attach_request_id(request: Request, call_next):
        supplied = str(request.headers.get("X-Request-ID") or "").strip()
        request_id = supplied if _REQUEST_ID_PATTERN.fullmatch(supplied) else generate_ulid()
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        if request.url.path.startswith("/api/v1"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    install_exception_handlers(application)

    versioned = APIRouter(
        prefix="/api/v1",
        dependencies=[Depends(require_principal)],
    )
    versioned.include_router(runs.router)
    versioned.include_router(chat.router)
    versioned.include_router(admin.router)
    versioned.include_router(system.router)
    application.include_router(versioned)
    return application


app = create_app()


__all__ = ["app", "create_app"]
