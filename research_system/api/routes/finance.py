from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from research_system.api.dependencies import (
    ApiPrincipal,
    get_finance_market_data,
    get_paper_trading,
    require_principal,
)
from research_system.api.errors import ApiError
from research_system.api.finance_market_data_service import (
    FinanceMarketDataError,
    FinanceMarketDataService,
)
from research_system.api.paper_trading_service import PaperTradingError, PaperTradingService


router = APIRouter(prefix="/finance", tags=["finance workspace"])


class PaperOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=64)
    side: Literal["buy", "sell"]
    quantity: int = Field(gt=0, le=100_000_000)
    client_order_id: str = Field(min_length=1, max_length=128)


class PaperAccountResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    initial_cash: float = Field(default=1_000_000, ge=10_000, le=1_000_000_000)


@router.get("/securities")
async def search_securities(
    query: Annotated[str, Query(min_length=1, max_length=80)],
    limit: Annotated[int, Query(ge=1, le=20)] = 8,
    service: FinanceMarketDataService = Depends(get_finance_market_data),
) -> dict:
    try:
        results = await run_in_threadpool(service.search_securities, query, limit=limit)
        return {"query": query, "results": results}
    except FinanceMarketDataError as exc:
        raise _market_error(exc) from exc


@router.get("/market-data/{symbol}")
async def market_data(
    symbol: str,
    refresh: bool = False,
    service: FinanceMarketDataService = Depends(get_finance_market_data),
) -> dict:
    try:
        return await run_in_threadpool(service.get_market_data, symbol, refresh=refresh)
    except FinanceMarketDataError as exc:
        raise _market_error(exc) from exc


@router.get("/quotes/{symbol}")
async def market_quote(
    symbol: str,
    refresh: bool = False,
    service: FinanceMarketDataService = Depends(get_finance_market_data),
) -> dict:
    try:
        return await run_in_threadpool(service.get_quote, symbol, refresh=refresh)
    except FinanceMarketDataError as exc:
        raise _market_error(exc) from exc


@router.get("/paper/account")
async def paper_account(
    principal: ApiPrincipal = Depends(require_principal),
    service: PaperTradingService = Depends(get_paper_trading),
) -> dict:
    tenant_id, user_id = _owner(principal)
    return await run_in_threadpool(service.get_account, tenant_id=tenant_id, user_id=user_id)


@router.post("/paper/account/reset")
async def reset_paper_account(
    request: PaperAccountResetRequest,
    principal: ApiPrincipal = Depends(require_principal),
    service: PaperTradingService = Depends(get_paper_trading),
) -> dict:
    tenant_id, user_id = _owner(principal)
    try:
        return await run_in_threadpool(
            service.reset_account,
            tenant_id=tenant_id,
            user_id=user_id,
            initial_cash=request.initial_cash,
        )
    except PaperTradingError as exc:
        raise _paper_error(exc) from exc


@router.get("/paper/portfolio")
async def paper_portfolio(
    principal: ApiPrincipal = Depends(require_principal),
    service: PaperTradingService = Depends(get_paper_trading),
) -> dict:
    tenant_id, user_id = _owner(principal)
    return await run_in_threadpool(service.get_portfolio, tenant_id=tenant_id, user_id=user_id)


@router.get("/paper/orders")
async def paper_orders(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    principal: ApiPrincipal = Depends(require_principal),
    service: PaperTradingService = Depends(get_paper_trading),
) -> dict:
    tenant_id, user_id = _owner(principal)
    orders = await run_in_threadpool(
        service.list_orders,
        tenant_id=tenant_id,
        user_id=user_id,
        limit=limit,
    )
    return {"orders": orders}


@router.post("/paper/orders", status_code=status.HTTP_201_CREATED)
async def place_paper_order(
    request: PaperOrderRequest,
    principal: ApiPrincipal = Depends(require_principal),
    service: PaperTradingService = Depends(get_paper_trading),
) -> dict:
    tenant_id, user_id = _owner(principal)
    try:
        return await run_in_threadpool(
            service.place_order,
            tenant_id=tenant_id,
            user_id=user_id,
            symbol=request.symbol,
            side=request.side,
            quantity=request.quantity,
            client_order_id=request.client_order_id,
        )
    except FinanceMarketDataError as exc:
        raise _market_error(exc) from exc
    except PaperTradingError as exc:
        raise _paper_error(exc) from exc


def _owner(principal: ApiPrincipal) -> tuple[str, str]:
    return (
        str(principal.tenant_id or "local"),
        str(principal.user_id or principal.actor_id or "local_user"),
    )


def _market_error(exc: FinanceMarketDataError) -> ApiError:
    status_by_code = {
        "security_required": 422,
        "security_not_found": 404,
        "security_ambiguous": 409,
        "quote_unavailable": 503,
    }
    return ApiError(
        status_code=status_by_code.get(exc.code, 422),
        code=exc.code,
        message=exc.message,
    )


def _paper_error(exc: PaperTradingError) -> ApiError:
    return ApiError(
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        details=exc.details,
    )


__all__ = ["router"]
