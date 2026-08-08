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
    require_role,
)
from research_system.api.errors import ApiError, public_error_message
from research_system.api.models import (
    MemoryPromotionRequest,
    MemoryRevokeRequest,
    RunApprovalRejectRequest,
    RunApprovalRequest,
)
from research_system.api.validation import safe_identifier
from research_system.runtime.presentation.display import localize_public_error_fields


ApprovalStatus = Literal["pending", "approved", "rejected", "revoked", "expired"]
MemoryLayer = Literal["episodic", "domain", "policy"]

router = APIRouter(tags=["administration"])


async def _public_call(operation, *args, **kwargs):
    result = await run_in_threadpool(operation, *args, **kwargs)
    return localize_public_error_fields(result)


def _scope_kwargs(service: AgentOSAdminService, principal: ApiPrincipal | None) -> dict[str, str]:
    if principal is None or not principal.authenticated:
        return {}
    if not getattr(service, "identity_scoped", False):
        return {}
    return {
        "user_id": str(principal.user_id or ""),
        "tenant_id": str(principal.tenant_id or ""),
    }


def _admin_service(agent_os=Depends(get_agent_os)) -> AgentOSAdminService:
    return AgentOSAdminService(agent_os)


@router.get("/admin/domain-packs")
async def list_domain_packs(service: AgentOSAdminService = Depends(_admin_service)) -> list[dict]:
    return await _public_call(service.list_domain_packs)


@router.get("/admin/domain-packs/{domain_id}")
async def show_domain_pack(
    domain_id: str,
    service: AgentOSAdminService = Depends(_admin_service),
) -> dict:
    resolved = safe_identifier(domain_id, field_name="domain_id")
    try:
        return await _public_call(service.domain_pack, resolved)
    except KeyError as exc:
        raise ApiError(
            status_code=404,
            code="resource_not_found",
            message="未找到请求的 DomainPack",
        ) from exc


@router.get("/admin/runs/{run_id}/approvals")
async def list_run_approvals(
    run_id: str,
    status: ApprovalStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    principal: ApiPrincipal = Depends(require_principal),
    service: AgentOSAdminService = Depends(_admin_service),
) -> list[dict]:
    resolved = safe_identifier(run_id, field_name="run_id")
    try:
        return await _public_call(
            service.list_approvals,
            resolved,
            status=status,
            limit=limit,
            **_scope_kwargs(service, principal),
        )
    except FileNotFoundError as exc:
        raise ApiError(
            status_code=404,
            code="resource_not_found",
            message="未找到请求的运行记录",
        ) from exc


@router.post("/admin/runs/{run_id}/approvals")
async def approve_run_node(
    run_id: str,
    payload: RunApprovalRequest,
    principal: ApiPrincipal = Depends(require_role("admin")),
    service: AgentOSAdminService = Depends(_admin_service),
    mutation_lock=Depends(get_mutation_lock),
) -> dict:
    resolved = safe_identifier(run_id, field_name="run_id")
    async with mutation_lock:
        try:
            return await _public_call(
                service.approve,
                resolved,
                node_id=payload.node_id,
                capability=payload.capability,
                approval_id=payload.approval_id,
                approved_by=f"api_v1:{principal.actor_id}",
                reason=payload.reason,
                **_scope_kwargs(service, principal),
            )
        except FileNotFoundError as exc:
            raise ApiError(
                status_code=404,
                code="resource_not_found",
                message="未找到请求的运行记录",
            ) from exc
        except KeyError as exc:
            raise ApiError(
                status_code=404,
                code="nested_resource_not_found",
                message="未找到请求的 ExecutionGraph 节点",
            ) from exc
        except ValueError as exc:
            raise ApiError(
                status_code=409,
                code="approval_conflict",
                message=public_error_message(exc, fallback="审批状态与当前执行上下文冲突"),
            ) from exc


@router.post("/admin/runs/{run_id}/approvals/{approval_id}/reject")
async def reject_run_approval(
    run_id: str,
    approval_id: str,
    payload: RunApprovalRejectRequest,
    principal: ApiPrincipal = Depends(require_role("admin")),
    service: AgentOSAdminService = Depends(_admin_service),
    mutation_lock=Depends(get_mutation_lock),
) -> dict:
    resolved = safe_identifier(run_id, field_name="run_id")
    resolved_approval = safe_identifier(approval_id, field_name="approval_id")
    async with mutation_lock:
        try:
            return await _public_call(
                service.reject,
                resolved,
                approval_id=resolved_approval,
                rejected_by=f"api_v1:{principal.actor_id}",
                reason=payload.reason,
                **_scope_kwargs(service, principal),
            )
        except FileNotFoundError as exc:
            raise ApiError(
                status_code=404,
                code="resource_not_found",
                message="未找到请求的运行记录",
            ) from exc
        except KeyError as exc:
            raise ApiError(
                status_code=404,
                code="nested_resource_not_found",
                message="未找到请求的审批记录",
            ) from exc
        except ValueError as exc:
            raise ApiError(
                status_code=409,
                code="approval_conflict",
                message=public_error_message(exc, fallback="审批状态与当前执行上下文冲突"),
            ) from exc


@router.get("/admin/memory")
async def list_memory(
    layer: MemoryLayer | None = None,
    domain_id: str | None = None,
    status: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    principal: ApiPrincipal = Depends(require_role("admin")),
    service: AgentOSAdminService = Depends(_admin_service),
) -> list[dict]:
    return await _public_call(
        service.list_memory,
        layer=layer,
        domain_id=domain_id,
        status=status,
        limit=limit,
        **_scope_kwargs(service, principal),
    )


@router.post("/admin/memory/expire")
async def expire_memory(
    principal: ApiPrincipal = Depends(require_role("admin")),
    service: AgentOSAdminService = Depends(_admin_service),
    mutation_lock=Depends(get_mutation_lock),
) -> dict:
    async with mutation_lock:
        return await _public_call(service.expire_memory)


@router.post("/admin/memory/{memory_id}/promote")
async def promote_memory(
    memory_id: str,
    payload: MemoryPromotionRequest,
    principal: ApiPrincipal = Depends(require_role("admin")),
    service: AgentOSAdminService = Depends(_admin_service),
    mutation_lock=Depends(get_mutation_lock),
) -> dict:
    resolved = safe_identifier(memory_id, field_name="memory_id")
    async with mutation_lock:
        try:
            return await _public_call(
                service.promote_memory,
                resolved,
                target_layer=payload.target_layer,
                evaluation_id=payload.evaluation_id,
                evaluator=payload.evaluator,
                verdict=payload.verdict,
                score=payload.score,
                evidence_refs=payload.evidence_refs,
                notes=payload.notes,
                content=payload.content,
                **_scope_kwargs(service, principal),
            )
        except KeyError as exc:
            raise ApiError(
                status_code=404,
                code="resource_not_found",
                message="未找到请求的记忆记录",
            ) from exc
        except ValueError as exc:
            raise ApiError(
                status_code=409,
                code="memory_promotion_conflict",
                message=public_error_message(exc, fallback="记忆提升请求与当前记录状态冲突"),
            ) from exc


@router.post("/admin/memory/{memory_id}/revoke")
async def revoke_memory(
    memory_id: str,
    payload: MemoryRevokeRequest,
    principal: ApiPrincipal = Depends(require_role("admin")),
    service: AgentOSAdminService = Depends(_admin_service),
    mutation_lock=Depends(get_mutation_lock),
) -> dict:
    resolved = safe_identifier(memory_id, field_name="memory_id")
    async with mutation_lock:
        try:
            return await _public_call(
                service.revoke_memory,
                resolved,
                revoked_by=f"api_v1:{principal.actor_id}",
                reason=payload.reason,
                **_scope_kwargs(service, principal),
            )
        except KeyError as exc:
            raise ApiError(
                status_code=404,
                code="resource_not_found",
                message="未找到请求的记忆记录",
            ) from exc


@router.post("/admin/artifacts/cleanup")
async def cleanup_artifacts(
    principal: ApiPrincipal = Depends(require_role("admin")),
    service: AgentOSAdminService = Depends(_admin_service),
    mutation_lock=Depends(get_mutation_lock),
) -> dict:
    async with mutation_lock:
        return await _public_call(service.cleanup_artifacts)


@router.post("/admin/qdrant/reindex")
async def reindex_qdrant(
    principal: ApiPrincipal = Depends(require_role("admin")),
    service: AgentOSAdminService = Depends(_admin_service),
    mutation_lock=Depends(get_mutation_lock),
) -> dict:
    async with mutation_lock:
        return await _public_call(service.rebuild_qdrant)


__all__ = ["router"]
