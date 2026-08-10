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
from research_system.api.agent_os_run_service import AgentOSRunService
from research_system.api.config import ApiConfig
from research_system.api.finance_market_data_service import FinanceMarketDataService
from research_system.api.fund_holding_report_service import FundHoldingReportService
from research_system.api.hikyuu_backtest_service import HikyuuBacktestService
from research_system.api.paper_trading_service import PaperTradingService
from research_system.api.dependencies import (
    ApiRuntimeState,
    require_principal,
)
from research_system.api.errors import install_exception_handlers
from research_system.api.routes import admin, chat, dashboard, finance, fund_holding_reports, runs, system
from research_system.api.sse import RunEventSource
from research_system.runtime.core.id_utils import generate_ulid
from research_system.runtime.agent_os import AgentOS
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout
from research_system.services.memory.maintenance import MemoryExpirationScheduler


LOGGER = logging.getLogger(__name__)
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]


class LocalizedCORSMiddleware(CORSMiddleware):
    """Keep Starlette's CORS failures consistent with the Chinese API surface."""

    def preflight_response(self, request_headers):
        response = super().preflight_response(request_headers)
        if response.status_code >= 400:
            response.body = "CORS 预检请求不符合允许策略".encode("utf-8")
            response.headers["content-length"] = str(len(response.body))
        return response


def create_app(
    *,
    settings: Settings | None = None,
    api_config: ApiConfig | None = None,
    agent_os_factory: Callable[[Settings], AgentOS] | None = None,
    run_service_factory: Callable[[AgentOS], Any] | None = None,
    fund_holding_report_factory: Callable[[Settings, AgentOS, Any], Any] | None = None,
    chat_service_factory: Callable[[Settings], Any] | None = None,
    event_source_factory: Callable[[Settings, ApiConfig], Any] | None = None,
    finance_market_data_factory: Callable[[Settings], Any] | None = None,
    paper_trading_factory: Callable[[Settings, Any], Any] | None = None,
    backtesting_factory: Callable[[Settings, Any], Any] | None = None,
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
    build_backtesting = backtesting_factory or (
        lambda current_settings, market_data: HikyuuBacktestService(
            result_root=current_settings.workspace_root / "backtests",
            market_data=market_data,
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
        run_service = (
            run_service_factory(agent_os)
            if run_service_factory is not None
            else AgentOSRunService(agent_os)
        )
        fund_holding_report_service = (
            fund_holding_report_factory(effective_settings, agent_os, run_service)
            if fund_holding_report_factory is not None
            else FundHoldingReportService(
                settings=effective_settings,
                agent_os=agent_os,
                run_service=run_service,
            )
            if isinstance(run_service, AgentOSRunService)
            else None
        )
        memory_scheduler.start()
        try:
            chat_service = build_chat_service(effective_settings)
            bind_chat_completion = getattr(run_service, "bind_chat_completion", None)
            if callable(bind_chat_completion):
                bind_chat_completion(chat_service.record_run_completion)
            finance_market_data = build_finance_market_data(effective_settings)
            paper_trading = build_paper_trading(effective_settings, finance_market_data)
            backtesting = build_backtesting(effective_settings, finance_market_data)
            runtime = ApiRuntimeState(
                repo_root=resolved_repo_root,
                settings=effective_settings,
                config=resolved_api_config,
                agent_os=agent_os,
                run_service=run_service,
                fund_holding_reports=fund_holding_report_service,
                chat_service=chat_service,
                event_source=build_event_source(effective_settings, resolved_api_config),
                finance_market_data=finance_market_data,
                paper_trading=paper_trading,
                backtesting=backtesting,
                mutation_lock=asyncio.Lock(),
            )
            application.state.api_runtime = runtime
            if not resolved_api_config.authentication_enabled:
                LOGGER.warning("Cognitrade API authentication is disabled for local development")
            yield
        finally:
            application.state.api_runtime = None
            memory_scheduler.stop()
            close_holding_reports = getattr(fund_holding_report_service, "close", None)
            if callable(close_holding_reports):
                close_holding_reports()
            close = getattr(run_service, "close", None)
            if callable(close):
                close()

    application = FastAPI(
        title="Cognitrade API",
        description="A-share multi-agent research runtime HTTP adapter",
        version="1.0.0",
        lifespan=lifespan,
    )
    application.add_middleware(
        LocalizedCORSMiddleware,
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
        expose_headers=[
            "X-Request-ID",
            "Content-Disposition",
            "X-Cognitrade-Source-Artifact-Count",
            "X-Cognitrade-Report-Count",
        ],
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
    versioned.include_router(fund_holding_reports.router)
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

    return application


app = create_app()


__all__ = ["app", "create_app"]
