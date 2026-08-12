from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from research_system.api.dependencies import (
    ApiPrincipal,
    get_strategy_backtesting,
    require_principal,
)
from research_system.api.errors import ApiError
from research_system.strategies.contracts import BacktestMode
from research_system.strategies.errors import StrategyDomainError
from research_system.strategies.service import owner_scope


router = APIRouter(prefix="/finance", tags=["strategy backtests"])


class StrategyBacktestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_version_id: str = Field(min_length=1, max_length=64)
    fund_code: str = Field(min_length=1, max_length=32)
    mode: BacktestMode
    start_date: date
    end_date: date
    initial_cash: float = Field(default=1_000_000, ge=10_000, le=1_000_000_000)
    benchmark: dict[str, Any] = Field(default_factory=dict)
    report_policy: Literal["existing_only", "require_complete"] = "existing_only"


class StrategyBacktestPreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_version_id: str = Field(min_length=1, max_length=64)
    fund_code: str = Field(min_length=1, max_length=32)
    mode: BacktestMode
    start_date: date
    end_date: date


@router.post("/strategy-backtests/preflight")
async def strategy_backtest_preflight(
    payload: StrategyBacktestPreflightRequest,
    principal: ApiPrincipal = Depends(require_principal),
    service=Depends(get_strategy_backtesting),
) -> dict[str, Any]:
    return await _call(service.preflight, principal, **payload.model_dump())


@router.post("/strategy-backtests", status_code=status.HTTP_202_ACCEPTED)
async def create_strategy_backtest(
    payload: StrategyBacktestRequest,
    principal: ApiPrincipal = Depends(require_principal),
    service=Depends(get_strategy_backtesting),
) -> dict[str, Any]:
    return await _call(service.create, principal, **payload.model_dump())


@router.get("/strategy-backtests")
async def list_strategy_backtests(
    principal: ApiPrincipal = Depends(require_principal),
    service=Depends(get_strategy_backtesting),
) -> dict[str, Any]:
    return await _call(service.list, principal)


@router.get("/strategy-backtests/{backtest_id}")
async def get_strategy_backtest(
    backtest_id: str,
    principal: ApiPrincipal = Depends(require_principal),
    service=Depends(get_strategy_backtesting),
) -> dict[str, Any]:
    return await _call(service.get, principal, backtest_id)


@router.post("/strategy-backtests/{backtest_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_strategy_backtest(
    backtest_id: str,
    principal: ApiPrincipal = Depends(require_principal),
    service=Depends(get_strategy_backtesting),
) -> dict[str, Any]:
    return await _call(service.retry, principal, backtest_id)


@router.post("/strategy-backtests/{backtest_id}/cancel")
async def cancel_strategy_backtest(
    backtest_id: str,
    principal: ApiPrincipal = Depends(require_principal),
    service=Depends(get_strategy_backtesting),
) -> dict[str, Any]:
    return await _call(service.cancel, principal, backtest_id)


async def _call(operation, principal: ApiPrincipal, *args, **kwargs):
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


__all__ = ["router"]
