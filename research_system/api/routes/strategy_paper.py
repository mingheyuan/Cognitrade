from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, status
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from research_system.api.dependencies import (
    ApiPrincipal,
    get_strategy_paper_execution,
    require_principal,
)
from research_system.api.errors import ApiError
from research_system.api.finance_market_data_service import FinanceMarketDataError
from research_system.api.paper_trading_service import PaperTradingError
from research_system.strategies.errors import StrategyDomainError
from research_system.strategies.service import owner_scope


router = APIRouter(prefix="/finance", tags=["strategy paper execution"])


class StrategyPaperOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal_id: str = Field(min_length=1, max_length=64)
    side: Literal["buy", "sell"]
    quantity: int = Field(gt=0, le=100_000_000)
    technical_reason: str = Field(min_length=1, max_length=128)
    client_order_id: str | None = Field(default=None, min_length=1, max_length=128)
    trading_date: date | None = None
    order_type: Literal["market", "limit"] = "market"
    limit_price: float | None = Field(default=None, gt=0)


@router.get("/strategy-paper-orders")
async def list_strategy_paper_orders(
    binding_id: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    principal: ApiPrincipal = Depends(require_principal),
    service=Depends(get_strategy_paper_execution),
) -> dict[str, Any]:
    return await _call(service.list, principal, binding_id=binding_id)


@router.post(
    "/paper-strategy-bindings/{binding_id}/orders",
    status_code=status.HTTP_201_CREATED,
)
async def submit_strategy_paper_order(
    binding_id: str,
    payload: StrategyPaperOrderRequest,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    principal: ApiPrincipal = Depends(require_principal),
    service=Depends(get_strategy_paper_execution),
) -> dict[str, Any]:
    effective_key = str(idempotency_key or payload.client_order_id or "").strip()
    if not effective_key:
        raise ApiError(400, "idempotency_key_required", "必须提供 Idempotency-Key 或 client_order_id")
    if payload.client_order_id and idempotency_key and payload.client_order_id != idempotency_key:
        raise ApiError(
            400,
            "idempotency_key_mismatch",
            "Idempotency-Key 必须与 client_order_id 一致",
        )
    return await _call(
        service.submit,
        principal,
        binding_id=binding_id,
        client_order_id=effective_key,
        **payload.model_dump(exclude={"client_order_id"}),
    )


async def _call(operation, principal: ApiPrincipal, *args, **kwargs):
    scope = owner_scope(
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        actor_id=principal.actor_id,
    )
    try:
        return await run_in_threadpool(operation, scope, *args, **kwargs)
    except StrategyDomainError as exc:
        raise ApiError(exc.status_code, exc.code, exc.message, details=exc.details) from exc
    except PaperTradingError as exc:
        raise ApiError(exc.status_code, exc.code, exc.message, details=exc.details) from exc
    except FinanceMarketDataError as exc:
        status_by_code = {
            "security_required": 422,
            "security_not_found": 404,
            "security_ambiguous": 409,
            "quote_unavailable": 503,
        }
        raise ApiError(status_by_code.get(exc.code, 422), exc.code, exc.message) from exc


__all__ = ["router"]
