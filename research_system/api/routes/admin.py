from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from starlette.concurrency import run_in_threadpool

from research_system.api.agent_os_admin_service import AgentOSAdminService
from research_system.api.dependencies import (
    ApiPrincipal,
    get_agent_os,
    get_mutation_lock,
    require_principal,
)
from research_system.api.errors import ApiError
from research_system.api.models import RunApprovalRequest
from research_system.api.validation import safe_identifier


ApprovalStatus = Literal["approved", "revoked"]
MemoryLayer = Literal["episodic", "domain", "policy"]

router = APIRouter(tags=["administration"])


def _admin_service(agent_os=Depends(get_agent_os)) -> AgentOSAdminService:
    return AgentOSAdminService(agent_os)


@router.get("/admin/domain-packs")
async def list_domain_packs(service: AgentOSAdminService = Depends(_admin_service)) -> list[dict]:
    return await run_in_threadpool(service.list_domain_packs)


@router.get("/admin/domain-packs/{domain_id}")
async def show_domain_pack(
    domain_id: str,
    service: AgentOSAdminService = Depends(_admin_service),
) -> dict:
    resolved = safe_identifier(domain_id, field_name="domain_id")
    try:
        return await run_in_threadpool(service.domain_pack, resolved)
    except KeyError as exc:
        raise ApiError(
            status_code=404,
            code="resource_not_found",
            message="The requested domain pack was not found",
        ) from exc


@router.get("/admin/runs/{run_id}/approvals")
async def list_run_approvals(
    run_id: str,
    status: ApprovalStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    service: AgentOSAdminService = Depends(_admin_service),
) -> list[dict]:
    resolved = safe_identifier(run_id, field_name="run_id")
    try:
        return await run_in_threadpool(service.list_approvals, resolved, status=status, limit=limit)
    except FileNotFoundError as exc:
        raise ApiError(
            status_code=404,
            code="resource_not_found",
            message="The requested run was not found",
        ) from exc


@router.post("/admin/runs/{run_id}/approvals")
async def approve_run_node(
    run_id: str,
    payload: RunApprovalRequest,
    principal: ApiPrincipal = Depends(require_principal),
    service: AgentOSAdminService = Depends(_admin_service),
    mutation_lock=Depends(get_mutation_lock),
) -> dict:
    resolved = safe_identifier(run_id, field_name="run_id")
    async with mutation_lock:
        try:
            return await run_in_threadpool(
                service.approve,
                resolved,
                node_id=payload.node_id,
                capability=payload.capability,
                approval_id=payload.approval_id,
                approved_by=f"api_v1:{principal.actor_id}",
                reason=payload.reason,
            )
        except FileNotFoundError as exc:
            raise ApiError(
                status_code=404,
                code="resource_not_found",
                message="The requested run was not found",
            ) from exc
        except KeyError as exc:
            raise ApiError(
                status_code=404,
                code="nested_resource_not_found",
                message="The requested execution graph node was not found",
            ) from exc
        except ValueError as exc:
            raise ApiError(
                status_code=409,
                code="approval_conflict",
                message=str(exc),
            ) from exc


@router.get("/admin/memory")
async def list_memory(
    layer: MemoryLayer | None = None,
    domain_id: str | None = None,
    status: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    service: AgentOSAdminService = Depends(_admin_service),
) -> list[dict]:
    return await run_in_threadpool(
        service.list_memory,
        layer=layer,
        domain_id=domain_id,
        status=status,
        limit=limit,
    )


@router.post("/admin/artifacts/cleanup")
async def cleanup_artifacts(
    service: AgentOSAdminService = Depends(_admin_service),
    mutation_lock=Depends(get_mutation_lock),
) -> dict:
    async with mutation_lock:
        return await run_in_threadpool(service.cleanup_artifacts)


@router.post("/admin/qdrant/reindex")
async def reindex_qdrant(
    service: AgentOSAdminService = Depends(_admin_service),
    mutation_lock=Depends(get_mutation_lock),
) -> dict:
    async with mutation_lock:
        return await run_in_threadpool(service.rebuild_qdrant)


__all__ = ["router"]
