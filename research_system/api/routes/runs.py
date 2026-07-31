from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from research_system.api.dependencies import (
    ApiPrincipal,
    get_event_source,
    get_mutation_lock,
    get_run_service,
    require_principal,
)
from research_system.api.errors import ApiError
from research_system.api.models import RerunRequest
from research_system.api.validation import safe_identifier, validate_research_request
from research_system.schemas.protocol import RequestEnvelope, ResponseEnvelope


RunStatusFilter = Literal[
    "received",
    "identifying_intent",
    "needs_clarification",
    "ready",
    "running",
    "waiting_approval",
    "waiting_external",
    "gated",
    "completed",
    "completed_partial",
    "cancelled",
    "failed",
    "timed_out",
]

router = APIRouter(tags=["research-runs"])


def _scope_kwargs(target, principal: ApiPrincipal | None) -> dict[str, str]:
    service = getattr(target, "__self__", target)
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


async def _call_run_resource(
    operation,
    *args,
    nested: bool = False,
    principal: ApiPrincipal | None = None,
    **kwargs,
):
    try:
        return await run_in_threadpool(
            operation,
            *args,
            **_scope_kwargs(operation, principal),
            **kwargs,
        )
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
    except ValueError as exc:
        message = str(exc).lower()
        if "unknown_rerun_node" in message:
            raise ApiError(
                status_code=422,
                code="unknown_rerun_node",
                message="The requested rerun node is not part of the source plan",
            ) from exc
        if (
            "missing_request_payload" in message
            or "missing_execution_graph_payload" in message
        ):
            raise ApiError(
                status_code=409,
                code="source_run_incomplete",
                message="The source run does not contain the data required for rerun",
            ) from exc
        raise


@router.post("/runs", response_model=ResponseEnvelope)
async def create_run(
    payload: RequestEnvelope,
    run_service=Depends(get_run_service),
    mutation_lock=Depends(get_mutation_lock),
    principal: ApiPrincipal = Depends(require_principal),
) -> ResponseEnvelope:
    identity = (
        {"user_id": principal.user_id, "tenant_id": principal.tenant_id}
        if principal.authenticated
        else {}
    )
    validated = validate_research_request(
        payload.model_copy(update=identity),
        supported_intents=_supported_intents(run_service),
        supported_methods=_supported_methods(run_service),
        supported_snapshot_types=_supported_snapshot_types(run_service),
    )
    async with mutation_lock:
        return await run_in_threadpool(run_service.run, validated)


@router.get("/runs")
async def list_runs(
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    status: RunStatusFilter | None = None,
    run_service=Depends(get_run_service),
    principal: ApiPrincipal = Depends(require_principal),
) -> list[dict]:
    rows = await run_in_threadpool(
        run_service.history,
        **_scope_kwargs(run_service, principal),
    )
    if status is not None:
        rows = [row for row in rows if str(row.get("status") or "") == status]
    return rows[:limit]


@router.get("/runs/{run_id}")
async def run_detail(
    run_id: str,
    run_service=Depends(get_run_service),
    principal: ApiPrincipal = Depends(require_principal),
) -> dict:
    resolved = safe_identifier(run_id, field_name="run_id")
    return await _call_run_resource(run_service.detail, resolved, principal=principal)


@router.get("/runs/{run_id}/artifacts")
async def run_artifacts(
    run_id: str,
    artifact_type: str | None = None,
    status: str | None = "active",
    run_service=Depends(get_run_service),
    principal: ApiPrincipal = Depends(require_principal),
) -> dict:
    resolved = safe_identifier(run_id, field_name="run_id")
    return await _call_run_resource(
        run_service.artifacts,
        resolved,
        artifact_type=artifact_type,
        status=status,
        principal=principal,
    )


@router.get("/runs/{run_id}/replay")
async def replay_run(
    run_id: str,
    run_service=Depends(get_run_service),
    principal: ApiPrincipal = Depends(require_principal),
) -> dict:
    resolved = safe_identifier(run_id, field_name="run_id")
    return await _call_run_resource(run_service.replay, resolved, principal=principal)


@router.get("/runs/{run_id}/events")
async def run_events(
    run_id: str,
    run_service=Depends(get_run_service),
    principal: ApiPrincipal = Depends(require_principal),
) -> dict:
    resolved = safe_identifier(run_id, field_name="run_id")
    return await _call_run_resource(run_service.events, resolved, principal=principal)


@router.get("/runs/{run_id}/events/stream")
async def stream_run_events(
    run_id: str,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    event_source=Depends(get_event_source),
    principal: ApiPrincipal = Depends(require_principal),
) -> StreamingResponse:
    resolved = safe_identifier(run_id, field_name="run_id")
    cursor = await run_in_threadpool(
        event_source.prepare,
        resolved,
        last_event_id=last_event_id,
        **_scope_kwargs(event_source, principal),
    )
    return StreamingResponse(
        event_source.stream(
            resolved,
            cursor=cursor,
            **_scope_kwargs(event_source, principal),
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/runs/{run_id}/trace")
async def run_trace(
    run_id: str,
    run_service=Depends(get_run_service),
    principal: ApiPrincipal = Depends(require_principal),
) -> dict:
    resolved = safe_identifier(run_id, field_name="run_id")
    return await _call_run_resource(run_service.trace, resolved, principal=principal)


@router.get("/runs/{run_id}/steps")
async def run_steps(
    run_id: str,
    run_service=Depends(get_run_service),
    principal: ApiPrincipal = Depends(require_principal),
) -> dict:
    resolved = safe_identifier(run_id, field_name="run_id")
    return await _call_run_resource(run_service.steps, resolved, principal=principal)


@router.get("/runs/{run_id}/metrics")
async def run_metrics(
    run_id: str,
    run_service=Depends(get_run_service),
    principal: ApiPrincipal = Depends(require_principal),
) -> dict:
    resolved = safe_identifier(run_id, field_name="run_id")
    return await _call_run_resource(run_service.metrics, resolved, principal=principal)


@router.get("/runs/{run_id}/threads/{thread_id}")
async def run_thread(
    run_id: str,
    thread_id: str,
    run_service=Depends(get_run_service),
    principal: ApiPrincipal = Depends(require_principal),
) -> dict:
    resolved_run = safe_identifier(run_id, field_name="run_id")
    resolved_thread = safe_identifier(thread_id, field_name="thread_id")
    return await _call_run_resource(
        run_service.thread,
        resolved_run,
        resolved_thread,
        nested=True,
        principal=principal,
    )


@router.get("/runs/{run_id}/threads/{thread_id}/replay")
async def replay_run_thread(
    run_id: str,
    thread_id: str,
    run_service=Depends(get_run_service),
    principal: ApiPrincipal = Depends(require_principal),
) -> dict:
    resolved_run = safe_identifier(run_id, field_name="run_id")
    resolved_thread = safe_identifier(thread_id, field_name="thread_id")
    return await _call_run_resource(
        run_service.thread_replay,
        resolved_run,
        resolved_thread,
        nested=True,
        principal=principal,
    )


@router.get("/runs/{run_id}/threads/{thread_id}/metrics")
async def run_thread_metrics(
    run_id: str,
    thread_id: str,
    run_service=Depends(get_run_service),
    principal: ApiPrincipal = Depends(require_principal),
) -> dict:
    resolved_run = safe_identifier(run_id, field_name="run_id")
    resolved_thread = safe_identifier(thread_id, field_name="thread_id")
    return await _call_run_resource(
        run_service.thread_metrics,
        resolved_run,
        resolved_thread,
        nested=True,
        principal=principal,
    )


@router.get("/runs/{run_id}/subagents/{invocation_id}")
async def run_subagent(
    run_id: str,
    invocation_id: str,
    run_service=Depends(get_run_service),
    principal: ApiPrincipal = Depends(require_principal),
) -> dict:
    resolved_run = safe_identifier(run_id, field_name="run_id")
    resolved_invocation = safe_identifier(invocation_id, field_name="invocation_id")
    return await _call_run_resource(
        run_service.subagent,
        resolved_run,
        resolved_invocation,
        nested=True,
        principal=principal,
    )


@router.get("/runs/{run_id}/recovery")
async def run_recovery(
    run_id: str,
    run_service=Depends(get_run_service),
    principal: ApiPrincipal = Depends(require_principal),
) -> dict:
    resolved = safe_identifier(run_id, field_name="run_id")
    return await _call_run_resource(run_service.resume, resolved, principal=principal)


@router.post("/runs/{run_id}/cancel")
async def cancel_run(
    run_id: str,
    run_service=Depends(get_run_service),
    principal: ApiPrincipal = Depends(require_principal),
) -> dict:
    resolved = safe_identifier(run_id, field_name="run_id")
    return await _call_run_resource(run_service.cancel, resolved, principal=principal)


@router.post("/runs/{run_id}/reruns", response_model=ResponseEnvelope)
async def rerun_from_node(
    run_id: str,
    payload: RerunRequest,
    run_service=Depends(get_run_service),
    mutation_lock=Depends(get_mutation_lock),
    principal: ApiPrincipal = Depends(require_principal),
) -> ResponseEnvelope:
    resolved = safe_identifier(run_id, field_name="run_id")
    async with mutation_lock:
        return await _call_run_resource(
            run_service.rerun_from,
            resolved,
            from_node=payload.from_node,
            principal=principal,
        )


__all__ = ["router"]
