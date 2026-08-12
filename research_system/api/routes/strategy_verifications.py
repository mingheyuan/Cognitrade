from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from research_system.api.dependencies import (
    ApiPrincipal,
    get_strategy_verification,
    require_principal,
)
from research_system.api.errors import ApiError
from research_system.strategies.errors import StrategyDomainError
from research_system.strategies.service import owner_scope


router = APIRouter(prefix="/finance", tags=["strategy governance"])


class CreateVerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_version_id: str = Field(min_length=1, max_length=64)
    backtest_run_id: str = Field(min_length=1, max_length=64)
    thresholds: dict[str, Any] = Field(default_factory=dict)


@router.post("/strategy-verifications", status_code=status.HTTP_201_CREATED)
async def create_strategy_verification(
    payload: CreateVerificationRequest,
    principal: ApiPrincipal = Depends(require_principal),
    service=Depends(get_strategy_verification),
) -> dict[str, Any]:
    return await _call(service.create, principal, **payload.model_dump())


@router.get("/strategy-verifications")
async def list_strategy_verifications(
    strategy_version_id: str | None = None,
    principal: ApiPrincipal = Depends(require_principal),
    service=Depends(get_strategy_verification),
) -> dict[str, Any]:
    return await _call(
        service.list,
        principal,
        strategy_version_id=strategy_version_id,
    )


@router.get("/strategy-verifications/{verification_id}")
async def get_strategy_verification_record(
    verification_id: str,
    principal: ApiPrincipal = Depends(require_principal),
    service=Depends(get_strategy_verification),
) -> dict[str, Any]:
    return await _call(service.get, principal, verification_id)


@router.post("/strategy-verifications/{verification_id}/confirm")
async def confirm_strategy_verification(
    verification_id: str,
    principal: ApiPrincipal = Depends(require_principal),
    service=Depends(get_strategy_verification),
) -> dict[str, Any]:
    return await _call(service.confirm, principal, verification_id)


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
