from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status
from starlette.concurrency import run_in_threadpool

from research_system.api.chat_service import ChatService
from research_system.api.dependencies import ApiPrincipal, get_chat_service, get_run_service, require_principal
from research_system.api.models import ChatResearchRequest, ChatThreadRenameRequest
from research_system.api.validation import safe_identifier, validate_research_request
from research_system.schemas.protocol import RequestEnvelope


router = APIRouter(prefix="/chat", tags=["chat"])


def _scope_kwargs(service, principal: ApiPrincipal | None) -> dict[str, str]:
    if principal is None or not principal.authenticated:
        return {}
    if not getattr(service, "identity_scoped", False):
        return {}
    return {
        "user_id": str(principal.user_id or ""),
        "tenant_id": str(principal.tenant_id or ""),
    }


def _supported_intents(run_service) -> list[str] | None:
    agent_os = getattr(run_service, "agent_os", None)
    resolver = getattr(agent_os, "supported_intents", None)
    if callable(resolver):
        return list(resolver())
    return None


def _supported_methods(run_service) -> list[str] | None:
    agent_os = getattr(run_service, "agent_os", None)
    resolver = getattr(agent_os, "supported_methods", None)
    if callable(resolver):
        return list(resolver())
    return None


def _supported_snapshot_types(run_service) -> list[str] | None:
    agent_os = getattr(run_service, "agent_os", None)
    resolver = getattr(agent_os, "supported_snapshot_types", None)
    if callable(resolver):
        return list(resolver())
    return None


@router.post("/threads", status_code=status.HTTP_201_CREATED)
async def create_chat_thread(
    service: Annotated[ChatService, Depends(get_chat_service)],
    principal: ApiPrincipal = Depends(require_principal),
) -> dict:
    return await run_in_threadpool(
        service.create_thread,
        **_scope_kwargs(service, principal),
    )


@router.get("/threads")
async def list_chat_thread_records(
    service: Annotated[ChatService, Depends(get_chat_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
    scope: Literal["active", "all", "archived"] = "active",
    principal: ApiPrincipal = Depends(require_principal),
) -> list[dict]:
    return await run_in_threadpool(
        service.list_threads,
        limit=limit,
        scope=scope,
        **_scope_kwargs(service, principal),
    )


@router.get("/threads/{thread_id}")
async def get_chat_thread(
    thread_id: str,
    service: Annotated[ChatService, Depends(get_chat_service)],
    principal: ApiPrincipal = Depends(require_principal),
) -> dict:
    resolved_thread_id = safe_identifier(thread_id, field_name="thread_id")
    return await run_in_threadpool(
        service.get_thread,
        resolved_thread_id,
        **_scope_kwargs(service, principal),
    )


@router.get("/threads/{thread_id}/preview")
async def preview_chat_thread(
    thread_id: str,
    service: Annotated[ChatService, Depends(get_chat_service)],
    turn_limit: Annotated[int, Query(ge=1, le=20)] = 3,
    principal: ApiPrincipal = Depends(require_principal),
) -> dict:
    resolved_thread_id = safe_identifier(thread_id, field_name="thread_id")
    return await run_in_threadpool(
        service.preview_thread,
        resolved_thread_id,
        turn_limit=turn_limit,
        **_scope_kwargs(service, principal),
    )


@router.patch("/threads/{thread_id}")
async def rename_chat_thread(
    thread_id: str,
    request: ChatThreadRenameRequest,
    service: Annotated[ChatService, Depends(get_chat_service)],
    principal: ApiPrincipal = Depends(require_principal),
) -> dict:
    resolved_thread_id = safe_identifier(thread_id, field_name="thread_id")
    return await run_in_threadpool(
        service.rename_thread,
        resolved_thread_id,
        thread_label=request.thread_label,
        **_scope_kwargs(service, principal),
    )


@router.post("/threads/{thread_id}/forks", status_code=status.HTTP_201_CREATED)
async def fork_chat_thread(
    thread_id: str,
    service: Annotated[ChatService, Depends(get_chat_service)],
    principal: ApiPrincipal = Depends(require_principal),
) -> dict:
    resolved_thread_id = safe_identifier(thread_id, field_name="thread_id")
    return await run_in_threadpool(
        service.fork_thread,
        resolved_thread_id,
        **_scope_kwargs(service, principal),
    )


@router.post("/threads/{thread_id}/archive")
async def archive_chat_thread(
    thread_id: str,
    service: Annotated[ChatService, Depends(get_chat_service)],
    principal: ApiPrincipal = Depends(require_principal),
) -> dict:
    resolved_thread_id = safe_identifier(thread_id, field_name="thread_id")
    return await run_in_threadpool(
        service.archive_thread,
        resolved_thread_id,
        **_scope_kwargs(service, principal),
    )


@router.post("/threads/{thread_id}/unarchive")
async def unarchive_chat_thread(
    thread_id: str,
    service: Annotated[ChatService, Depends(get_chat_service)],
    principal: ApiPrincipal = Depends(require_principal),
) -> dict:
    resolved_thread_id = safe_identifier(thread_id, field_name="thread_id")
    return await run_in_threadpool(
        service.unarchive_thread,
        resolved_thread_id,
        **_scope_kwargs(service, principal),
    )


@router.post("/threads/{thread_id}/compact")
async def compact_chat_thread(
    thread_id: str,
    service: Annotated[ChatService, Depends(get_chat_service)],
    principal: ApiPrincipal = Depends(require_principal),
) -> dict:
    resolved_thread_id = safe_identifier(thread_id, field_name="thread_id")
    return await run_in_threadpool(
        service.compact_thread,
        resolved_thread_id,
        **_scope_kwargs(service, principal),
    )


@router.post("/threads/{thread_id}/runs")
async def submit_chat_research(
    thread_id: str,
    payload: ChatResearchRequest,
    service: Annotated[ChatService, Depends(get_chat_service)],
    run_service=Depends(get_run_service),
    principal: ApiPrincipal = Depends(require_principal),
) -> dict:
    resolved_thread_id = safe_identifier(thread_id, field_name="thread_id")
    request = RequestEnvelope(
            intent=payload.intent,
            user_query=payload.user_query,
            conversation_context=payload.conversation_context,
            slots=payload.slots,
            constraints=payload.constraints,
            user_id=(principal.user_id if principal.authenticated else None),
            tenant_id=(principal.tenant_id if principal.authenticated else None),
        )
    request = validate_research_request(
        request,
        supported_intents=_supported_intents(run_service),
        supported_methods=_supported_methods(run_service),
        supported_snapshot_types=_supported_snapshot_types(run_service),
    )
    response = await run_in_threadpool(
        service.submit_research,
        resolved_thread_id,
        request,
        run_service=run_service,
        **_scope_kwargs(service, principal),
    )
    return response.model_dump(mode="json")


__all__ = ["router"]
