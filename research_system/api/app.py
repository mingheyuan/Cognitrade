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
from research_system.api.agent_os_run_service import AgentOSRunService
from research_system.api.config import ApiConfig
from research_system.api.finance_market_data_service import FinanceMarketDataService
from research_system.api.paper_trading_service import PaperTradingService
from research_system.api.dependencies import (
    ApiPrincipal,
    ApiRuntimeState,
    get_agent_os,
    get_workspace_principal,
    require_principal,
    require_role,
)
from research_system.api.errors import install_exception_handlers
from research_system.api.routes import admin, chat, dashboard, finance, runs, system
from research_system.api.sse import RunEventSource
from research_system.runtime.core.id_utils import generate_ulid
from research_system.runtime.agent_os import AgentOS
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout
from research_system.schemas.artifacts import ArtifactDraft
from research_system.services.memory.maintenance import MemoryExpirationScheduler


LOGGER = logging.getLogger(__name__)
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]


def _assert_workspace_scope(agent_os: AgentOS, run_id: str, principal: ApiPrincipal) -> None:
    try:
        agent_os.assert_run_scope(
            run_id,
            user_id=principal.user_id,
            tenant_id=principal.tenant_id,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc


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
    agent_os_factory: Callable[[Settings], AgentOS] | None = None,
    run_service_factory: Callable[[AgentOS], Any] | None = None,
    chat_service_factory: Callable[[Settings], Any] | None = None,
    event_source_factory: Callable[[Settings, ApiConfig], Any] | None = None,
    finance_market_data_factory: Callable[[Settings], Any] | None = None,
    paper_trading_factory: Callable[[Settings, Any], Any] | None = None,
    repo_root: Path | None = None,
) -> FastAPI:
    resolved_repo_root = (repo_root or _DEFAULT_REPO_ROOT).resolve()
    resolved_api_config = api_config or ApiConfig.from_env(resolved_repo_root)
    build_agent_os = agent_os_factory or AgentOS
    build_chat_service = chat_service_factory or ChatService
    build_event_source = event_source_factory or RunEventSource
    build_finance_market_data = finance_market_data_factory or (
        lambda current_settings: FinanceMarketDataService(
            cache_root=current_settings.workspace_root / "cache"
        )
    )
    build_paper_trading = paper_trading_factory or (
        lambda current_settings, market_data: PaperTradingService(
            database_path=current_settings.workspace_root / "paper_trading" / "paper_trading.sqlite3",
            market_data=market_data,
            enforce_market_hours=current_settings.paper_trading_enforce_market_hours,
        )
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        effective_settings = settings or Settings.from_env(resolved_repo_root)
        effective_settings.validate()
        resolved_api_config.validate()
        ensure_workspace_layout(effective_settings)

        agent_os = build_agent_os(effective_settings)
        memory_scheduler = MemoryExpirationScheduler(
            agent_os.memory,
            interval_seconds=effective_settings.memory_expiration_interval_seconds,
        )
        # This injection exists only for transport-layer test doubles while API
        # tests are migrated. Production always uses AgentOSRunService.
        injected_run_service = (
            orchestrator_factory(effective_settings)
            if orchestrator_factory is not None
            else None
        )
        run_service = (
            run_service_factory(agent_os)
            if run_service_factory is not None
            else injected_run_service or AgentOSRunService(agent_os)
        )
        memory_scheduler.start()
        try:
            chat_service = build_chat_service(effective_settings)
            bind_chat_completion = getattr(run_service, "bind_chat_completion", None)
            if callable(bind_chat_completion):
                bind_chat_completion(chat_service.record_run_completion)
            finance_market_data = build_finance_market_data(effective_settings)
            paper_trading = build_paper_trading(effective_settings, finance_market_data)
            runtime = ApiRuntimeState(
                repo_root=resolved_repo_root,
                settings=effective_settings,
                config=resolved_api_config,
                agent_os=agent_os,
                run_service=run_service,
                chat_service=chat_service,
                event_source=build_event_source(effective_settings, resolved_api_config),
                finance_market_data=finance_market_data,
                paper_trading=paper_trading,
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
            memory_scheduler.stop()
            for service in (run_service, injected_run_service):
                close = getattr(service, "close", None)
                if callable(close):
                    close()

    application = FastAPI(
        title="Cognitrade API",
        description="A-share multi-agent research runtime HTTP adapter",
        version="1.0.0",
        lifespan=lifespan,
    )
    # The unversioned workspace API is also useful in lightweight TestClient and
    # embedded use where a lifespan context is not explicitly entered.
    application.state.agent_os = build_agent_os(
        settings or Settings.from_env(resolved_repo_root)
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_api_config.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Last-Event-ID",
            "X-Request-ID",
            "X-User-ID",
            "X-Tenant-ID",
        ],
        expose_headers=["X-Request-ID"],
    )

    @application.middleware("http")
    async def attach_request_id(request: Request, call_next):
        supplied = str(request.headers.get("X-Request-ID") or "").strip()
        request_id = supplied if _REQUEST_ID_PATTERN.fullmatch(supplied) else generate_ulid()
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        if request.url.path.startswith(("/api/v1", "/api/v2")):
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
    versioned.include_router(dashboard.router)
    versioned.include_router(system.router)
    application.include_router(versioned)
    finance_v2 = APIRouter(
        prefix="/api/v2",
        dependencies=[Depends(require_principal)],
    )
    finance_v2.include_router(finance.router)
    application.include_router(finance_v2)

    @application.get("/", response_class=HTMLResponse)
    def workspace() -> str:
        return _WORKSPACE_HTML

    @application.get("/api/domain-packs")
    def domain_packs(
        agent_os=Depends(get_agent_os),
        principal: ApiPrincipal = Depends(get_workspace_principal),
    ) -> list[dict[str, Any]]:
        return agent_os.list_domain_packs()

    @application.get("/api/runs")
    def agent_os_runs(
        agent_os=Depends(get_agent_os),
        principal: ApiPrincipal = Depends(get_workspace_principal),
    ) -> list[dict[str, Any]]:
        rows = []
        for row in agent_os.run_store.list_runs():
            run_id = str(row.get("run_id") or "")
            if not run_id:
                continue
            try:
                agent_os.assert_run_scope(
                    run_id,
                    user_id=principal.user_id,
                    tenant_id=principal.tenant_id,
                )
                detail = agent_os.inspect_run(run_id)
            except (FileNotFoundError, KeyError, ValueError):
                continue
            snapshot = detail["snapshot"]
            if detail.get("mode") == "core":
                request = detail.get("request") or {}
                rows.append({
                    "run_id": run_id,
                    "domain_id": None,
                    "objective": request.get("user_query") or "",
                    "status": snapshot.get("status"),
                    "mode": "core",
                    "revision": None,
                })
                continue
            rows.append({
                "run_id": run_id,
                "domain_id": snapshot["graph"]["domain_id"],
                "objective": snapshot["graph"]["objective"],
                "status": snapshot["status"],
                "mode": "graph",
                "revision": snapshot["graph"]["revision"],
            })
        return rows

    @application.post("/api/runs")
    def agent_os_run(
        request: DomainRunRequest,
        agent_os=Depends(get_agent_os),
        principal: ApiPrincipal = Depends(get_workspace_principal),
    ) -> dict[str, Any]:
        try:
            result = agent_os.run_domain(
                domain_id=request.domain_id,
                objective=request.objective,
                input_artifacts=request.artifacts,
                required_artifact_types=request.required_artifact_types,
                preferred_capabilities=request.preferred_capabilities,
                run_id=request.run_id,
                user_id=principal.user_id,
                tenant_id=principal.tenant_id,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return result.model_dump(mode="json")

    @application.get("/api/runs/{run_id}")
    def agent_os_inspect(
        run_id: str,
        agent_os=Depends(get_agent_os),
        principal: ApiPrincipal = Depends(get_workspace_principal),
    ) -> dict[str, Any]:
        try:
            _assert_workspace_scope(agent_os, run_id, principal)
            return agent_os.inspect_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @application.get("/api/runs/{run_id}/replay")
    def agent_os_replay(
        run_id: str,
        agent_os=Depends(get_agent_os),
        principal: ApiPrincipal = Depends(get_workspace_principal),
    ) -> dict[str, Any]:
        try:
            _assert_workspace_scope(agent_os, run_id, principal)
            detail = agent_os.inspect_run(run_id)
            if detail.get("mode") == "core":
                return {
                    "run_id": run_id,
                    "mode": "core",
                    "request": detail.get("request"),
                    "snapshot": detail.get("snapshot"),
                    "artifacts": detail.get("artifacts") or [],
                }
            return agent_os.replay(run_id).model_dump(mode="json")
        except (KeyError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @application.post("/api/runs/{run_id}/pause")
    def agent_os_pause(
        run_id: str,
        agent_os=Depends(get_agent_os),
        principal: ApiPrincipal = Depends(get_workspace_principal),
    ) -> dict[str, Any]:
        _assert_workspace_scope(agent_os, run_id, principal)
        return {"run_id": run_id, "pause_requested": agent_os.pause(run_id)}

    @application.post("/api/runs/{run_id}/cancel")
    def agent_os_cancel(
        run_id: str,
        agent_os=Depends(get_agent_os),
        principal: ApiPrincipal = Depends(get_workspace_principal),
    ) -> dict[str, Any]:
        _assert_workspace_scope(agent_os, run_id, principal)
        return {"run_id": run_id, "cancel_requested": agent_os.cancel(run_id)}

    @application.post("/api/runs/{run_id}/resume")
    def agent_os_resume(
        run_id: str,
        agent_os=Depends(get_agent_os),
        principal: ApiPrincipal = Depends(get_workspace_principal),
    ) -> dict[str, Any]:
        try:
            _assert_workspace_scope(agent_os, run_id, principal)
            return agent_os.resume(run_id).model_dump(mode="json")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @application.post("/api/runs/{run_id}/rerun/{node_id}")
    def agent_os_rerun(
        run_id: str,
        node_id: str,
        agent_os=Depends(get_agent_os),
        principal: ApiPrincipal = Depends(get_workspace_principal),
    ) -> dict[str, Any]:
        try:
            _assert_workspace_scope(agent_os, run_id, principal)
            return agent_os.rerun_from(run_id, node_id).model_dump(mode="json")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @application.post("/api/runs/{run_id}/approve")
    def agent_os_approve(
        run_id: str,
        request: ApprovalRequest,
        agent_os=Depends(get_agent_os),
        principal: ApiPrincipal = Depends(require_role("admin")),
    ) -> dict[str, Any]:
        _assert_workspace_scope(agent_os, run_id, principal)
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
