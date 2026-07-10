from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from research_system.api.dependencies import (
    get_event_source,
    get_mutation_lock,
    get_orchestrator,
)
from research_system.api.errors import ApiError
from research_system.api.models import RerunRequest, SubagentRerunRequest
from research_system.api.validation import safe_identifier, validate_research_request
from research_system.schemas.protocol import RequestEnvelope, ResponseEnvelope


RunStatusFilter = Literal[
    "received",
    "identifying_intent",
    "needs_clarification",
    "ready",
    "running",
    "waiting_external",
    "gated",
    "completed",
    "cancelled",
    "failed",
    "timed_out",
]

router = APIRouter(tags=["research-runs"])


async def _call_run_resource(operation, *args, nested: bool = False, **kwargs):
    try:
        return await run_in_threadpool(operation, *args, **kwargs)
    except FileNotFoundError as exc:
        raise ApiError(
            status_code=404,
            code="resource_not_found",
            message="The requested run was not found",
        ) from exc
    except KeyError as exc:
        if not nested:
            raise
        raise ApiError(
            status_code=404,
            code="nested_resource_not_found",
            message="The requested nested run resource was not found",
        ) from exc


@router.post("/runs", response_model=ResponseEnvelope)
async def create_run(
    payload: RequestEnvelope,
    orchestrator=Depends(get_orchestrator),
    mutation_lock=Depends(get_mutation_lock),
) -> ResponseEnvelope:
    validated = validate_research_request(payload)
    async with mutation_lock:
        return await run_in_threadpool(orchestrator.run, validated)


@router.get("/runs")
async def list_runs(
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    status: RunStatusFilter | None = None,
    orchestrator=Depends(get_orchestrator),
) -> list[dict]:
    rows = await run_in_threadpool(orchestrator.history)
    if status is not None:
        rows = [row for row in rows if str(row.get("status") or "") == status]
    return rows[:limit]


@router.get("/runs/{run_id}/replay")
async def replay_run(run_id: str, orchestrator=Depends(get_orchestrator)) -> dict:
    resolved = safe_identifier(run_id, field_name="run_id")
    return await _call_run_resource(orchestrator.replay, resolved)


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, orchestrator=Depends(get_orchestrator)) -> dict:
    resolved = safe_identifier(run_id, field_name="run_id")
    return await _call_run_resource(orchestrator.events, resolved)


@router.get("/runs/{run_id}/events/stream")
async def stream_run_events(
    run_id: str,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    event_source=Depends(get_event_source),
) -> StreamingResponse:
    resolved = safe_identifier(run_id, field_name="run_id")
    cursor = await run_in_threadpool(event_source.prepare, resolved, last_event_id=last_event_id)
    return StreamingResponse(
        event_source.stream(resolved, cursor=cursor),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/runs/{run_id}/trace")
async def run_trace(run_id: str, orchestrator=Depends(get_orchestrator)) -> dict:
    resolved = safe_identifier(run_id, field_name="run_id")
    return await _call_run_resource(orchestrator.trace, resolved)


@router.get("/runs/{run_id}/steps")
async def run_steps(run_id: str, orchestrator=Depends(get_orchestrator)) -> dict:
    resolved = safe_identifier(run_id, field_name="run_id")
    return await _call_run_resource(orchestrator.steps, resolved)


@router.get("/runs/{run_id}/metrics")
async def run_metrics(run_id: str, orchestrator=Depends(get_orchestrator)) -> dict:
    resolved = safe_identifier(run_id, field_name="run_id")
    return await _call_run_resource(orchestrator.metrics, resolved)


@router.get("/runs/{run_id}/threads/{thread_id}")
async def run_thread(run_id: str, thread_id: str, orchestrator=Depends(get_orchestrator)) -> dict:
    resolved_run = safe_identifier(run_id, field_name="run_id")
    resolved_thread = safe_identifier(thread_id, field_name="thread_id")
    return await _call_run_resource(orchestrator.thread, resolved_run, resolved_thread, nested=True)


@router.get("/runs/{run_id}/threads/{thread_id}/replay")
async def replay_run_thread(run_id: str, thread_id: str, orchestrator=Depends(get_orchestrator)) -> dict:
    resolved_run = safe_identifier(run_id, field_name="run_id")
    resolved_thread = safe_identifier(thread_id, field_name="thread_id")
    return await _call_run_resource(
        orchestrator.thread_replay,
        resolved_run,
        resolved_thread,
        nested=True,
    )


@router.get("/runs/{run_id}/threads/{thread_id}/metrics")
async def run_thread_metrics(run_id: str, thread_id: str, orchestrator=Depends(get_orchestrator)) -> dict:
    resolved_run = safe_identifier(run_id, field_name="run_id")
    resolved_thread = safe_identifier(thread_id, field_name="thread_id")
    return await _call_run_resource(
        orchestrator.thread_metrics,
        resolved_run,
        resolved_thread,
        nested=True,
    )


@router.get("/runs/{run_id}/subagents/{invocation_id}")
async def run_subagent(run_id: str, invocation_id: str, orchestrator=Depends(get_orchestrator)) -> dict:
    resolved_run = safe_identifier(run_id, field_name="run_id")
    resolved_invocation = safe_identifier(invocation_id, field_name="invocation_id")
    return await _call_run_resource(orchestrator.subagent, resolved_run, resolved_invocation, nested=True)


@router.get("/runs/{run_id}/recovery")
async def run_recovery(run_id: str, orchestrator=Depends(get_orchestrator)) -> dict:
    resolved = safe_identifier(run_id, field_name="run_id")
    return await _call_run_resource(orchestrator.resume, resolved)


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, orchestrator=Depends(get_orchestrator)) -> dict:
    resolved = safe_identifier(run_id, field_name="run_id")
    return await _call_run_resource(orchestrator.cancel, resolved)


@router.post("/runs/{run_id}/reruns", response_model=ResponseEnvelope)
async def rerun_from_node(
    run_id: str,
    payload: RerunRequest,
    orchestrator=Depends(get_orchestrator),
    mutation_lock=Depends(get_mutation_lock),
) -> ResponseEnvelope:
    resolved = safe_identifier(run_id, field_name="run_id")
    async with mutation_lock:
        return await _call_run_resource(orchestrator.rerun_from, resolved, from_node=payload.from_node)


@router.post("/runs/{run_id}/subagent-reruns", response_model=ResponseEnvelope)
async def rerun_subagent_from_node(
    run_id: str,
    payload: SubagentRerunRequest,
    orchestrator=Depends(get_orchestrator),
    mutation_lock=Depends(get_mutation_lock),
) -> ResponseEnvelope:
    resolved = safe_identifier(run_id, field_name="run_id")
    async with mutation_lock:
        return await _call_run_resource(
            orchestrator.rerun_subagent_from,
            resolved,
            action_node=payload.action_node,
            dag_id=payload.dag_id,
            from_node=payload.from_node,
        )


__all__ = ["router"]
