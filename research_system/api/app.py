from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import ConfigDict, BaseModel, Field

from research_system.api.chat_service import ChatService
from research_system.api.config import ApiConfig
from research_system.api.dependencies import ApiRuntimeState, get_agent_os, require_principal
from research_system.api.errors import install_exception_handlers
from research_system.api.routes import admin, chat, runs, system
from research_system.api.sse import RunEventSource
from research_system.runtime.core.id_utils import generate_ulid
from research_system.runtime.agent_os import AgentOS
from research_system.runtime.orchestration.orchestrator import ResearchOrchestrator
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout
from research_system.schemas.artifacts import ArtifactDraft


LOGGER = logging.getLogger(__name__)
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]


class DomainRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain_id: str
    objective: str
    artifacts: list[ArtifactDraft] = Field(default_factory=list)
    required_artifact_types: list[str] = Field(default_factory=list)
    preferred_capabilities: list[str] = Field(default_factory=list)
    run_id: str | None = None


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    capability: str
    approved_by: str
    reason: str
    approval_id: str | None = None


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
        agent_os = getattr(application.state, "agent_os", None) or AgentOS(effective_settings)
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
            application.state.agent_os = agent_os
            if not resolved_api_config.authentication_enabled:
                LOGGER.warning("Cognitrade API authentication is disabled for local development")
            yield
        finally:
            application.state.api_runtime = None
            application.state.agent_os = None
            close = getattr(orchestrator, "close", None)
            if callable(close):
                close()

    application = FastAPI(
        title="Cognitrade API",
        description="A-share multi-agent research runtime HTTP adapter",
        version="1.0.0",
        lifespan=lifespan,
    )
    # Keep the canonical Agent OS workspace usable with both a plain TestClient
    # and a lifespan-managed production server.
    application.state.agent_os = AgentOS(settings or Settings.from_env(resolved_repo_root))
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

    @application.get("/", response_class=HTMLResponse)
    def workspace() -> str:
        return _WORKSPACE_HTML

    @application.get("/api/domain-packs")
    def domain_packs(agent_os=Depends(get_agent_os)) -> list[dict[str, Any]]:
        return agent_os.list_domain_packs()

    @application.get("/api/runs")
    def agent_os_runs(agent_os=Depends(get_agent_os)) -> list[dict[str, Any]]:
        rows = []
        for row in agent_os.run_store.list_runs():
            run_id = str(row.get("run_id") or "")
            if not run_id:
                continue
            try:
                snapshot = agent_os.inspect_run(run_id)["snapshot"]
            except (KeyError, ValueError):
                continue
            rows.append({
                "run_id": run_id,
                "domain_id": snapshot["graph"]["domain_id"],
                "objective": snapshot["graph"]["objective"],
                "status": snapshot["status"],
                "revision": snapshot["graph"]["revision"],
            })
        return rows

    @application.post("/api/runs")
    def agent_os_run(request: DomainRunRequest, agent_os=Depends(get_agent_os)) -> dict[str, Any]:
        try:
            result = agent_os.run_domain(
                domain_id=request.domain_id,
                objective=request.objective,
                input_artifacts=request.artifacts,
                required_artifact_types=request.required_artifact_types,
                preferred_capabilities=request.preferred_capabilities,
                run_id=request.run_id,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return result.model_dump(mode="json")

    @application.get("/api/runs/{run_id}")
    def agent_os_inspect(run_id: str, agent_os=Depends(get_agent_os)) -> dict[str, Any]:
        try:
            return agent_os.inspect_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @application.get("/api/runs/{run_id}/replay")
    def agent_os_replay(run_id: str, agent_os=Depends(get_agent_os)) -> dict[str, Any]:
        try:
            return agent_os.replay(run_id).model_dump(mode="json")
        except (KeyError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @application.post("/api/runs/{run_id}/pause")
    def agent_os_pause(run_id: str, agent_os=Depends(get_agent_os)) -> dict[str, Any]:
        return {"run_id": run_id, "pause_requested": agent_os.pause(run_id)}

    @application.post("/api/runs/{run_id}/cancel")
    def agent_os_cancel(run_id: str, agent_os=Depends(get_agent_os)) -> dict[str, Any]:
        return {"run_id": run_id, "cancel_requested": agent_os.cancel(run_id)}

    @application.post("/api/runs/{run_id}/resume")
    def agent_os_resume(run_id: str, agent_os=Depends(get_agent_os)) -> dict[str, Any]:
        try:
            return agent_os.resume(run_id).model_dump(mode="json")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @application.post("/api/runs/{run_id}/rerun/{node_id}")
    def agent_os_rerun(run_id: str, node_id: str, agent_os=Depends(get_agent_os)) -> dict[str, Any]:
        try:
            return agent_os.rerun_from(run_id, node_id).model_dump(mode="json")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @application.post("/api/runs/{run_id}/approve")
    def agent_os_approve(
        run_id: str,
        request: ApprovalRequest,
        agent_os=Depends(get_agent_os),
    ) -> dict[str, Any]:
        runner = agent_os._runner(run_id)
        try:
            approval = runner.approvals.approve(
                run_id=run_id,
                node_id=request.node_id,
                capability=request.capability,
                approved_by=request.approved_by,
                reason=request.reason,
                approval_id=request.approval_id,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return approval.model_dump(mode="json")

    return application


app = create_app()


__all__ = ["app", "create_app"]


_WORKSPACE_HTML = """<!doctype html><html><head><title>Cognitrade Agent OS</title></head>
<body><main><h1>Cognitrade Agent OS</h1><h2>Execution Graph</h2>
<p>Use the workspace API to inspect runs, artifacts, gates, and graph revisions.</p>
</main></body></html>"""
