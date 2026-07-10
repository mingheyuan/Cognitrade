from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response
from starlette.concurrency import run_in_threadpool

from research_system.api.dependencies import (
    ApiPrincipal,
    get_mutation_lock,
    get_orchestrator,
    require_principal,
)
from research_system.api.errors import ApiError
from research_system.api.models import (
    DiscoveryActivationRequest,
    DiscoveryReviewRequest,
    ProposalReviewRequest,
)
from research_system.api.validation import safe_identifier


ReviewStatus = Literal["pending", "approved", "rejected"]

router = APIRouter(tags=["administration"])


@router.get("/admin/proposals")
async def list_proposals(
    status: ReviewStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    orchestrator=Depends(get_orchestrator),
) -> list[dict]:
    return await run_in_threadpool(orchestrator.list_proposals, status=status, limit=limit)


@router.get("/admin/proposals/{proposal_id}")
async def show_proposal(proposal_id: str, orchestrator=Depends(get_orchestrator)) -> dict:
    resolved = safe_identifier(proposal_id, field_name="proposal_id")
    return await run_in_threadpool(orchestrator.show_proposal, resolved)


@router.post("/admin/proposals/{proposal_id}/reviews")
async def review_proposal(
    proposal_id: str,
    payload: ProposalReviewRequest,
    principal: ApiPrincipal = Depends(require_principal),
    orchestrator=Depends(get_orchestrator),
    mutation_lock=Depends(get_mutation_lock),
) -> dict:
    resolved = safe_identifier(proposal_id, field_name="proposal_id")
    requested_status = "approved" if payload.decision == "approve" else "rejected"
    async with mutation_lock:
        current = await run_in_threadpool(orchestrator.show_proposal, resolved)
        current_status = str(current.get("status") or "")
        if current_status in {"approved", "rejected"} and current_status != requested_status:
            raise ApiError(
                status_code=409,
                code="proposal_already_finalized",
                message=f"Proposal {resolved} is already {current_status}",
            )
        return await run_in_threadpool(
            orchestrator.review_proposal,
            resolved,
            decision=payload.decision,
            reviewer=f"api_v1:{principal.actor_id}",
            reason=payload.reason,
        )


@router.get("/admin/subagent-discoveries")
async def list_subagent_discoveries(
    status: ReviewStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    orchestrator=Depends(get_orchestrator),
) -> list[dict]:
    return await run_in_threadpool(
        orchestrator.list_subagent_discovery_requests,
        status=status,
        limit=limit,
    )


@router.get("/admin/subagent-discoveries/{ticket_id}")
async def show_subagent_discovery(ticket_id: str, orchestrator=Depends(get_orchestrator)) -> dict:
    resolved = safe_identifier(ticket_id, field_name="ticket_id")
    return await run_in_threadpool(orchestrator.show_subagent_discovery_request, resolved)


@router.post("/admin/subagent-discoveries/{ticket_id}/reviews")
async def review_subagent_discovery(
    ticket_id: str,
    payload: DiscoveryReviewRequest,
    principal: ApiPrincipal = Depends(require_principal),
    orchestrator=Depends(get_orchestrator),
    mutation_lock=Depends(get_mutation_lock),
) -> dict:
    resolved = safe_identifier(ticket_id, field_name="ticket_id")
    requested_status = "approved" if payload.decision == "approve" else "rejected"
    async with mutation_lock:
        current = await run_in_threadpool(orchestrator.show_subagent_discovery_request, resolved)
        current_status = str(current.get("status") or "")
        if current_status in {"approved", "rejected"} and current_status != requested_status:
            raise ApiError(
                status_code=409,
                code="discovery_ticket_already_finalized",
                message=f"Discovery ticket {resolved} is already {current_status}",
            )
        return await run_in_threadpool(
            orchestrator.review_subagent_discovery_request,
            resolved,
            decision=payload.decision,
            reviewer=f"api_v1:{principal.actor_id}",
            reason=payload.reason,
            candidate_catalog_entries=None,
        )


@router.post("/admin/subagent-discoveries/{ticket_id}/activations")
async def activate_subagent_discovery(
    ticket_id: str,
    payload: DiscoveryActivationRequest,
    response: Response,
    principal: ApiPrincipal = Depends(require_principal),
    orchestrator=Depends(get_orchestrator),
    mutation_lock=Depends(get_mutation_lock),
) -> dict:
    resolved = safe_identifier(ticket_id, field_name="ticket_id")
    kwargs = payload.model_dump(exclude_none=True)
    kwargs["activated_by"] = f"api_v1:{principal.actor_id}"
    async with mutation_lock:
        result = await run_in_threadpool(
            orchestrator.activate_subagent_discovery_request,
            resolved,
            **kwargs,
        )
    response.status_code = 200 if "already exists" in str(result.get("message") or "") else 201
    return result


@router.post("/admin/artifacts/cleanup")
async def cleanup_artifacts(
    orchestrator=Depends(get_orchestrator),
    mutation_lock=Depends(get_mutation_lock),
) -> dict:
    async with mutation_lock:
        return await run_in_threadpool(orchestrator.cleanup)


@router.post("/admin/qdrant/reindex")
async def reindex_qdrant(
    orchestrator=Depends(get_orchestrator),
    mutation_lock=Depends(get_mutation_lock),
) -> dict:
    async with mutation_lock:
        return await run_in_threadpool(orchestrator.rebuild_qdrant)


__all__ = ["router"]
