from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

from research_system.api.chat_service import ChatService
from research_system.api.agent_os_run_service import AgentOSRunService
from research_system.api.config import ApiConfig
from research_system.api.finance_market_data_service import FinanceMarketDataService
from research_system.api.fund_holding_report_service import FundHoldingReportService
from research_system.api.hikyuu_backtest_service import HikyuuBacktestService
from research_system.api.paper_trading_service import PaperTradingService
from research_system.api.dependencies import (
    ApiRuntimeState,
    _authenticate_principal,
    require_principal,
)
from research_system.api.errors import install_exception_handlers
from research_system.api.routes import (
    admin,
    chat,
    dashboard,
    finance,
    fund_holding_reports,
    report_signals,
    runs,
    strategy_backtests,
    strategy_paper,
    strategy_verifications,
    strategies,
    system,
)
from research_system.api.sse import RunEventSource
from research_system.runtime.core.id_utils import generate_ulid
from research_system.runtime.agent_os import AgentOS
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout
from research_system.services.memory.maintenance import MemoryExpirationScheduler
from research_system.strategies.database import StrategyDatabase, resolve_database_url
from research_system.strategies.backtesting.market_data_gateway import MarketDataGateway
from research_system.strategies.backtesting.service import StrategyBacktestService
from research_system.strategies.orchestration import ReportOrchestrationService
from research_system.strategies.idempotency import (
    ApiMutationIdempotencyService,
    MutationReplay,
    MutationReservation,
)
from research_system.strategies.paper_execution import StrategyPaperExecutionService
from research_system.strategies.errors import StrategyDomainError
from research_system.strategies.service import StrategyService, owner_scope
from research_system.strategies.signals import SignalEngineService
from research_system.strategies.verification import StrategyVerificationService


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


async def _call_with_idempotency(request: Request, call_next):
    key = str(request.headers.get("Idempotency-Key") or "").strip()
    if (
        not key
        or request.method.upper() not in {"POST", "PATCH", "DELETE"}
        or not request.url.path.startswith("/api/v2/finance")
    ):
        return await call_next(request)
    runtime = getattr(request.app.state, "api_runtime", None)
    service = getattr(runtime, "api_idempotency", None)
    principal = _idempotency_principal(request, runtime)
    if service is None or principal is None:
        return await call_next(request)

    body = await request.body()
    target = request.url.path
    if request.url.query:
        target = f"{target}?{request.url.query}"
    digest = hashlib.sha256()
    digest.update(request.method.upper().encode("ascii"))
    digest.update(b"\0")
    digest.update(target.encode("utf-8"))
    digest.update(b"\0")
    digest.update(body)
    scope = owner_scope(
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        actor_id=principal.actor_id,
    )
    try:
        reservation = await run_in_threadpool(
            service.reserve,
            scope,
            idempotency_key=key,
            method=request.method.upper(),
            request_target=target,
            request_hash=digest.hexdigest(),
        )
    except StrategyDomainError as exc:
        return _idempotency_error_response(request, exc)
    if isinstance(reservation, MutationReplay):
        headers = {**reservation.headers, "Idempotent-Replay": "true"}
        return JSONResponse(
            status_code=reservation.status_code,
            content=reservation.payload,
            headers=headers,
        )
    assert isinstance(reservation, MutationReservation)

    try:
        response = await call_next(request)
    except Exception:
        await _release_idempotency(service, scope, reservation)
        raise
    response_body = b"".join([chunk async for chunk in response.body_iterator])
    response_headers = dict(response.headers)
    rebuilt = Response(
        content=response_body,
        status_code=response.status_code,
        headers=response_headers,
        background=response.background,
    )
    if not 200 <= response.status_code < 300:
        await _release_idempotency(service, scope, reservation)
        return rebuilt
    if "application/json" not in str(response.headers.get("content-type") or ""):
        await _release_idempotency(service, scope, reservation)
        return rebuilt
    try:
        payload = json.loads(response_body.decode("utf-8")) if response_body else None
        retained_headers = {
            name: value
            for name, value in response.headers.items()
            if name.lower() in {"etag", "location"}
        }
        await run_in_threadpool(
            service.complete,
            scope,
            reservation,
            status_code=response.status_code,
            payload=payload,
            headers=retained_headers,
        )
    except Exception:
        LOGGER.exception("Failed to persist API idempotency response for %s", target)
    return rebuilt


def _idempotency_principal(request: Request, runtime: Any):
    if not isinstance(runtime, ApiRuntimeState):
        return None
    credentials = None
    authorization = str(request.headers.get("Authorization") or "").strip()
    if authorization:
        scheme, separator, token = authorization.partition(" ")
        if separator and token.strip():
            credentials = HTTPAuthorizationCredentials(
                scheme=scheme,
                credentials=token.strip(),
            )
    try:
        return _authenticate_principal(request, runtime.config, credentials)
    except Exception:
        return None


async def _release_idempotency(service, scope, reservation: MutationReservation) -> None:
    try:
        await run_in_threadpool(service.release, scope, reservation)
    except Exception:
        LOGGER.exception("Failed to release API idempotency reservation %s", reservation.record_id)


def _idempotency_error_response(request: Request, exc: StrategyDomainError) -> JSONResponse:
    request_id = str(getattr(request.state, "request_id", "") or "").strip()
    error: dict[str, Any] = {"code": exc.code, "message": exc.message}
    if exc.details:
        error["details"] = exc.details
    headers = {"Cache-Control": "no-store"}
    if request_id:
        error["request_id"] = request_id
        headers["X-Request-ID"] = request_id
    return JSONResponse(status_code=exc.status_code, content={"error": error}, headers=headers)


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
    strategy_service_factory: Callable[[Settings], Any] | None = None,
    signal_engine_factory: Callable[[Any], Any] | None = None,
    report_orchestration_factory: Callable[[Settings, Any, Any, Any, Any], Any] | None = None,
    strategy_backtesting_factory: Callable[[Settings, Any, Any], Any] | None = None,
    strategy_verification_factory: Callable[[Any], Any] | None = None,
    strategy_paper_execution_factory: Callable[[Any, Any], Any] | None = None,
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
    build_strategy_service = strategy_service_factory or (
        lambda current_settings: StrategyService(
            StrategyDatabase(resolve_database_url(current_settings)),
            workspace_root=current_settings.workspace_root,
            auto_bootstrap=current_settings.cognitrade_database_auto_bootstrap,
        )
    )
    build_signal_engine = signal_engine_factory or SignalEngineService
    build_report_orchestration = report_orchestration_factory or (
        lambda current_settings, database, current_run_service, holding_reports, signal_engine: (
            ReportOrchestrationService(
                database,
                run_service=current_run_service,
                holding_report_service=holding_reports,
                workspace_root=current_settings.workspace_root,
                max_workers=1 if database.is_sqlite else 4,
                signal_engine=signal_engine,
            )
        )
    )
    build_strategy_backtesting = strategy_backtesting_factory or (
        lambda current_settings, database, market_data: StrategyBacktestService(
            database,
            market_data_gateway=MarketDataGateway(market_data),
            object_store_root=(
                current_settings.strategy_object_store_root
                or current_settings.workspace_root / "strategy_objects"
            ),
            max_workers=1 if database.is_sqlite else 4,
        )
    )
    build_strategy_verification = strategy_verification_factory or StrategyVerificationService
    build_strategy_paper_execution = (
        strategy_paper_execution_factory
        or (lambda database, paper_trading: StrategyPaperExecutionService(
            database,
            paper_trading=paper_trading,
        ))
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        strategy_service = None
        signal_engine = None
        report_orchestration = None
        strategy_backtesting = None
        strategy_verification = None
        strategy_paper_execution = None
        api_idempotency = None
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
            strategy_service = build_strategy_service(effective_settings)
            strategy_database = getattr(strategy_service, "database", None)
            if strategy_database is not None:
                signal_engine = build_signal_engine(strategy_database)
                strategy_verification = build_strategy_verification(strategy_database)
                strategy_paper_execution = build_strategy_paper_execution(
                    strategy_database,
                    paper_trading,
                )
                api_idempotency = ApiMutationIdempotencyService(strategy_database)
                strategy_backtesting = build_strategy_backtesting(
                    effective_settings,
                    strategy_database,
                    finance_market_data,
                )
                if fund_holding_report_service is not None:
                    report_orchestration = build_report_orchestration(
                        effective_settings,
                        strategy_database,
                        run_service,
                        fund_holding_report_service,
                        signal_engine,
                    )
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
                strategies=strategy_service,
                report_orchestration=report_orchestration,
                signal_engine=signal_engine,
                strategy_backtesting=strategy_backtesting,
                strategy_verification=strategy_verification,
                strategy_paper_execution=strategy_paper_execution,
                api_idempotency=api_idempotency,
                mutation_lock=asyncio.Lock(),
            )
            application.state.api_runtime = runtime
            if not resolved_api_config.authentication_enabled:
                LOGGER.warning("Cognitrade API authentication is disabled for local development")
            yield
        finally:
            application.state.api_runtime = None
            memory_scheduler.stop()
            close_strategy_backtesting = getattr(strategy_backtesting, "close", None)
            if callable(close_strategy_backtesting):
                close_strategy_backtesting()
            close_report_orchestration = getattr(report_orchestration, "close", None)
            if callable(close_report_orchestration):
                close_report_orchestration()
            close_holding_reports = getattr(fund_holding_report_service, "close", None)
            if callable(close_holding_reports):
                close_holding_reports()
            close = getattr(run_service, "close", None)
            if callable(close):
                close()
            close_strategies = getattr(strategy_service, "close", None)
            if callable(close_strategies):
                close_strategies()

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
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Last-Event-ID",
            "X-Request-ID",
            "X-User-ID",
            "X-Tenant-ID",
            "If-Match",
            "Idempotency-Key",
        ],
        expose_headers=[
            "X-Request-ID",
            "Content-Disposition",
            "X-Cognitrade-Source-Artifact-Count",
            "X-Cognitrade-Report-Count",
            "Idempotent-Replay",
        ],
    )

    @application.middleware("http")
    async def attach_request_id(request: Request, call_next):
        supplied = str(request.headers.get("X-Request-ID") or "").strip()
        request_id = supplied if _REQUEST_ID_PATTERN.fullmatch(supplied) else generate_ulid()
        request.state.request_id = request_id
        response = await _call_with_idempotency(request, call_next)
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
    finance_v2.include_router(strategies.router)
    finance_v2.include_router(report_signals.router)
    finance_v2.include_router(strategy_backtests.router)
    finance_v2.include_router(strategy_verifications.router)
    finance_v2.include_router(strategy_paper.router)
    application.include_router(finance_v2)

    return application


app = create_app()


__all__ = ["app", "create_app"]
