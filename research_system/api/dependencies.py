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
    fund_holding_reports: Any | None
    chat_service: Any
    event_source: Any
    finance_market_data: Any
    market_data_center: Any | None
    paper_trading: Any
    backtesting: Any
    strategies: Any
    report_orchestration: Any | None
    signal_engine: Any | None
    strategy_backtesting: Any | None
    strategy_verification: Any | None
    strategy_paper_execution: Any | None
    api_idempotency: Any | None
    mutation_lock: asyncio.Lock


def get_api_runtime(request: Request) -> ApiRuntimeState:
    runtime = getattr(request.app.state, "api_runtime", None)
    if not isinstance(runtime, ApiRuntimeState):
        raise ApiError(
            status_code=503,
            code="api_not_ready",
            message="API 运行时尚未完成启动",
        )
    return runtime


def get_settings(request: Request) -> Settings:
    return get_api_runtime(request).settings


def get_agent_os(request: Request):
    return get_api_runtime(request).agent_os


def get_run_service(request: Request):
    return get_api_runtime(request).run_service


def get_fund_holding_reports(request: Request):
    service = get_api_runtime(request).fund_holding_reports
    if service is None:
        raise ApiError(
            status_code=503,
            code="fund_holding_reports_unavailable",
            message="基金持仓报告编排服务暂不可用",
        )
    return service


def get_chat_service(request: Request):
    return get_api_runtime(request).chat_service


def get_mutation_lock(request: Request) -> asyncio.Lock:
    return get_api_runtime(request).mutation_lock


def get_event_source(request: Request):
    return get_api_runtime(request).event_source


def get_finance_market_data(request: Request):
    return get_api_runtime(request).finance_market_data


def get_market_data_center(request: Request):
    service = get_api_runtime(request).market_data_center
    if service is None:
        raise ApiError(
            status_code=503,
            code="market_data_center_unavailable",
            message="行情数据中心暂不可用",
        )
    return service


def get_paper_trading(request: Request):
    return get_api_runtime(request).paper_trading


def get_backtesting(request: Request):
    return get_api_runtime(request).backtesting


def get_strategies(request: Request):
    return get_api_runtime(request).strategies


def get_report_orchestration(request: Request):
    service = get_api_runtime(request).report_orchestration
    if service is None:
        raise ApiError(503, "report_orchestration_unavailable", "日报编排服务暂不可用")
    return service


def get_signal_engine(request: Request):
    service = get_api_runtime(request).signal_engine
    if service is None:
        raise ApiError(503, "signal_engine_unavailable", "信号服务暂不可用")
    return service


def get_strategy_backtesting(request: Request):
    service = get_api_runtime(request).strategy_backtesting
    if service is None:
        raise ApiError(503, "strategy_backtesting_unavailable", "策略回测服务暂不可用")
    return service


def get_strategy_verification(request: Request):
    service = get_api_runtime(request).strategy_verification
    if service is None:
        raise ApiError(503, "strategy_verification_unavailable", "策略验证服务暂不可用")
    return service


def get_strategy_paper_execution(request: Request):
    service = get_api_runtime(request).strategy_paper_execution
    if service is None:
        raise ApiError(503, "strategy_paper_execution_unavailable", "策略模拟交易服务暂不可用")
    return service


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
            user_id="local_development",
            tenant_id="local",
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
            message="需要提供 Bearer token",
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
            message="Bearer token 无效",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if binding is not None:
        header_user_id = request.headers.get("X-User-ID") or ""
        header_tenant_id = request.headers.get("X-Tenant-ID") or ""
        if header_user_id and header_user_id != binding.user_id:
            raise ApiError(
                status_code=403,
                code="identity_header_conflict",
                message="请求身份与已认证主体不一致",
            )
        if header_tenant_id and header_tenant_id != binding.tenant_id:
            raise ApiError(
                status_code=403,
                code="identity_header_conflict",
                message="请求身份与已认证主体不一致",
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
                message="当前认证主体缺少所需角色",
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
    "get_backtesting",
    "get_strategies",
    "get_report_orchestration",
    "get_signal_engine",
    "get_strategy_backtesting",
    "get_strategy_verification",
    "get_strategy_paper_execution",
    "get_event_source",
    "get_fund_holding_reports",
    "get_mutation_lock",
    "get_repo_root",
    "get_settings",
    "require_principal",
    "require_role",
]
