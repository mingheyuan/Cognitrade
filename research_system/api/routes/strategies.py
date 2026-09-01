from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from research_system.api.dependencies import (
    ApiPrincipal,
    get_strategies,
    require_principal,
)
from research_system.api.errors import ApiError
from research_system.api.route_support import call_strategy_service as _call
from research_system.strategies.contracts import BacktestMode
from research_system.strategies.service import StrategyService

router = APIRouter(prefix="/finance", tags=["strategy workspace"])


class CreateStrategyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    document: dict[str, Any] | None = None
    from_strategy_id: str | None = Field(default=None, min_length=1, max_length=64)


class UpdateDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
    document: dict[str, Any]
    name: str | None = Field(default=None, min_length=1, max_length=200)


class CloneStrategyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)


class CreateAssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_id: str = Field(min_length=1, max_length=64)
    fund_code: str = Field(min_length=1, max_length=32)
    fund_name: str | None = Field(default=None, max_length=200)
    mode: BacktestMode
    enabled: bool = True
    schedule: dict[str, Any] | None = None


class UpdateAssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    paused: bool | None = None
    schedule: dict[str, Any] | None = None


class PromoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(default="paper-default", min_length=1, max_length=128)
    fund_code: str = Field(min_length=1, max_length=32)
    mode: BacktestMode


@router.get("/strategies")
async def list_strategies(
    include_archived: bool = False,
    principal: ApiPrincipal = Depends(require_principal),
    service: StrategyService = Depends(get_strategies),
) -> dict[str, Any]:
    return await _call(service.list_strategies, principal, include_archived=include_archived)


@router.get("/strategy-catalog")
async def strategy_catalog(
    principal: ApiPrincipal = Depends(require_principal),
    service: StrategyService = Depends(get_strategies),
) -> dict[str, Any]:
    return await _call(service.list_catalog, principal)


@router.post("/strategies", status_code=status.HTTP_201_CREATED)
async def create_strategy(
    payload: CreateStrategyRequest,
    principal: ApiPrincipal = Depends(require_principal),
    service: StrategyService = Depends(get_strategies),
) -> dict[str, Any]:
    return await _call(
        service.create_strategy,
        principal,
        name=payload.name,
        description=payload.description,
        document=payload.document,
        from_strategy_id=payload.from_strategy_id,
    )


@router.get("/strategies/{strategy_id}")
async def get_strategy(
    strategy_id: str,
    response: Response,
    principal: ApiPrincipal = Depends(require_principal),
    service: StrategyService = Depends(get_strategies),
) -> dict[str, Any]:
    payload = await _call(service.get_strategy, principal, strategy_id)
    if payload.get("draft") is not None:
        response.headers["ETag"] = f'W/"{payload["draft_revision"]}"'
    return payload


@router.patch("/strategies/{strategy_id}/draft")
async def update_strategy_draft(
    strategy_id: str,
    payload: UpdateDraftRequest,
    response: Response,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    principal: ApiPrincipal = Depends(require_principal),
    service: StrategyService = Depends(get_strategies),
) -> dict[str, Any]:
    if if_match is not None:
        normalized = if_match.strip()
        if normalized.startswith("W/"):
            normalized = normalized[2:].strip()
        normalized = normalized.strip('"')
        if not normalized.isdigit() or int(normalized) != payload.expected_revision:
            raise ApiError(400, "invalid_if_match", "If-Match 必须与 expected_revision 一致")
    result = await _call(
        service.update_draft,
        principal,
        strategy_id,
        expected_revision=payload.expected_revision,
        document=payload.document,
        name=payload.name,
    )
    response.headers["ETag"] = f'W/"{result["draft_revision"]}"'
    return result


@router.delete("/strategies/{strategy_id}")
async def delete_strategy(
    strategy_id: str,
    principal: ApiPrincipal = Depends(require_principal),
    service: StrategyService = Depends(get_strategies),
) -> dict[str, Any]:
    return await _call(service.hard_delete, principal, strategy_id)


@router.post("/strategies/{strategy_id}/clone", status_code=status.HTTP_201_CREATED)
async def clone_strategy(
    strategy_id: str,
    payload: CloneStrategyRequest,
    principal: ApiPrincipal = Depends(require_principal),
    service: StrategyService = Depends(get_strategies),
) -> dict[str, Any]:
    return await _call(service.clone_strategy, principal, strategy_id, name=payload.name)


@router.post("/strategies/{strategy_id}/publish")
async def publish_strategy(
    strategy_id: str,
    principal: ApiPrincipal = Depends(require_principal),
    service: StrategyService = Depends(get_strategies),
) -> dict[str, Any]:
    return await _call(service.publish, principal, strategy_id)


@router.post("/strategies/{strategy_id}/archive")
async def archive_strategy(
    strategy_id: str,
    principal: ApiPrincipal = Depends(require_principal),
    service: StrategyService = Depends(get_strategies),
) -> dict[str, Any]:
    return await _call(service.archive, principal, strategy_id)


@router.post("/strategies/{strategy_id}/restore")
async def restore_strategy(
    strategy_id: str,
    principal: ApiPrincipal = Depends(require_principal),
    service: StrategyService = Depends(get_strategies),
) -> dict[str, Any]:
    return await _call(service.restore, principal, strategy_id)


@router.get("/strategies/{strategy_id}/versions")
async def list_strategy_versions(
    strategy_id: str,
    principal: ApiPrincipal = Depends(require_principal),
    service: StrategyService = Depends(get_strategies),
) -> dict[str, Any]:
    return await _call(service.list_versions, principal, strategy_id)


@router.get("/strategies/{strategy_id}/events")
async def list_strategy_events(
    strategy_id: str,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    principal: ApiPrincipal = Depends(require_principal),
    service: StrategyService = Depends(get_strategies),
) -> dict[str, Any]:
    return await _call(service.list_events, principal, strategy_id, limit=limit)


@router.get("/strategies/{strategy_id}/versions/{version_id}")
async def get_strategy_version(
    strategy_id: str,
    version_id: str,
    principal: ApiPrincipal = Depends(require_principal),
    service: StrategyService = Depends(get_strategies),
) -> dict[str, Any]:
    return await _call(service.get_version, principal, strategy_id, version_id)


@router.get("/strategies/{strategy_id}/version-diff")
async def strategy_version_diff(
    strategy_id: str,
    from_version_id: Annotated[str, Query(min_length=1)],
    to_version_id: Annotated[str, Query(min_length=1)],
    principal: ApiPrincipal = Depends(require_principal),
    service: StrategyService = Depends(get_strategies),
) -> dict[str, Any]:
    return await _call(
        service.version_diff,
        principal,
        strategy_id,
        from_version_id=from_version_id,
        to_version_id=to_version_id,
    )


@router.get("/strategy-assignments")
async def list_assignments(
    strategy_id: str | None = None,
    fund_code: str | None = None,
    principal: ApiPrincipal = Depends(require_principal),
    service: StrategyService = Depends(get_strategies),
) -> dict[str, Any]:
    return await _call(
        service.list_assignments,
        principal,
        strategy_id=strategy_id,
        fund_code=fund_code,
    )


@router.post("/strategy-assignments", status_code=status.HTTP_201_CREATED)
async def create_assignment(
    payload: CreateAssignmentRequest,
    principal: ApiPrincipal = Depends(require_principal),
    service: StrategyService = Depends(get_strategies),
) -> dict[str, Any]:
    return await _call(service.create_assignment, principal, **payload.model_dump())


@router.patch("/strategy-assignments/{assignment_id}")
async def update_assignment(
    assignment_id: str,
    payload: UpdateAssignmentRequest,
    principal: ApiPrincipal = Depends(require_principal),
    service: StrategyService = Depends(get_strategies),
) -> dict[str, Any]:
    return await _call(
        service.update_assignment,
        principal,
        assignment_id,
        **payload.model_dump(),
    )


@router.delete("/strategy-assignments/{assignment_id}")
async def delete_assignment(
    assignment_id: str,
    principal: ApiPrincipal = Depends(require_principal),
    service: StrategyService = Depends(get_strategies),
) -> dict[str, Any]:
    return await _call(service.delete_assignment, principal, assignment_id)


@router.post("/strategy-assignments/{assignment_id}/pause")
async def pause_assignment(
    assignment_id: str,
    principal: ApiPrincipal = Depends(require_principal),
    service: StrategyService = Depends(get_strategies),
) -> dict[str, Any]:
    return await _call(service.pause_assignment, principal, assignment_id)


@router.post("/strategy-assignments/{assignment_id}/resume")
async def resume_assignment(
    assignment_id: str,
    principal: ApiPrincipal = Depends(require_principal),
    service: StrategyService = Depends(get_strategies),
) -> dict[str, Any]:
    return await _call(service.resume_assignment, principal, assignment_id)


@router.get("/paper-strategy-bindings")
async def list_paper_strategy_bindings(
    principal: ApiPrincipal = Depends(require_principal),
    service: StrategyService = Depends(get_strategies),
) -> dict[str, Any]:
    return await _call(service.list_paper_bindings, principal)


@router.post("/strategies/{strategy_id}/versions/{version_id}/promote")
async def promote_strategy_version(
    strategy_id: str,
    version_id: str,
    payload: PromoteRequest,
    principal: ApiPrincipal = Depends(require_principal),
    service: StrategyService = Depends(get_strategies),
) -> dict[str, Any]:
    return await _call(
        service.promote,
        principal,
        strategy_id=strategy_id,
        version_id=version_id,
        **payload.model_dump(),
    )

__all__ = ["router"]
