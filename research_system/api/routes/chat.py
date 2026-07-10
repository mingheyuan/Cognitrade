from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status
from starlette.concurrency import run_in_threadpool

from research_system.api.chat_service import ChatService
from research_system.api.dependencies import get_chat_service
from research_system.api.models import ChatThreadRenameRequest
from research_system.api.validation import safe_identifier


router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/threads", status_code=status.HTTP_201_CREATED)
async def create_chat_thread(
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    return await run_in_threadpool(service.create_thread)


@router.get("/threads")
async def list_chat_thread_records(
    service: Annotated[ChatService, Depends(get_chat_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
    scope: Literal["active", "all", "archived"] = "active",
) -> list[dict]:
    return await run_in_threadpool(
        service.list_threads,
        limit=limit,
        scope=scope,
    )


@router.get("/threads/{thread_id}")
async def get_chat_thread(
    thread_id: str,
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    resolved_thread_id = safe_identifier(thread_id, field_name="thread_id")
    return await run_in_threadpool(service.get_thread, resolved_thread_id)


@router.get("/threads/{thread_id}/preview")
async def preview_chat_thread(
    thread_id: str,
    service: Annotated[ChatService, Depends(get_chat_service)],
    turn_limit: Annotated[int, Query(ge=1, le=20)] = 3,
) -> dict:
    resolved_thread_id = safe_identifier(thread_id, field_name="thread_id")
    return await run_in_threadpool(
        service.preview_thread,
        resolved_thread_id,
        turn_limit=turn_limit,
    )


@router.patch("/threads/{thread_id}")
async def rename_chat_thread(
    thread_id: str,
    request: ChatThreadRenameRequest,
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    resolved_thread_id = safe_identifier(thread_id, field_name="thread_id")
    return await run_in_threadpool(
        service.rename_thread,
        resolved_thread_id,
        thread_label=request.thread_label,
    )


@router.post("/threads/{thread_id}/forks", status_code=status.HTTP_201_CREATED)
async def fork_chat_thread(
    thread_id: str,
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    resolved_thread_id = safe_identifier(thread_id, field_name="thread_id")
    return await run_in_threadpool(service.fork_thread, resolved_thread_id)


@router.post("/threads/{thread_id}/archive")
async def archive_chat_thread(
    thread_id: str,
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    resolved_thread_id = safe_identifier(thread_id, field_name="thread_id")
    return await run_in_threadpool(service.archive_thread, resolved_thread_id)


@router.post("/threads/{thread_id}/unarchive")
async def unarchive_chat_thread(
    thread_id: str,
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    resolved_thread_id = safe_identifier(thread_id, field_name="thread_id")
    return await run_in_threadpool(service.unarchive_thread, resolved_thread_id)


@router.post("/threads/{thread_id}/compact")
async def compact_chat_thread(
    thread_id: str,
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    resolved_thread_id = safe_identifier(thread_id, field_name="thread_id")
    return await run_in_threadpool(service.compact_thread, resolved_thread_id)


__all__ = ["router"]
