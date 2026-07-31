from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from collections import deque
from threading import Event, RLock, Semaphore, Thread
from time import monotonic
from typing import Any, Callable

import jsonschema

from research_system.runtime.storage.jsonl import append_jsonl, read_json, read_jsonl, write_json
from research_system.runtime.core.id_utils import generate_ulid
from research_system.runtime.core.approvals import HumanApprovalStore
from research_system.runtime.event_store import append_canonical_event
from research_system.adapters.storage.resource_lease import (
    FileResourceLeaseStore,
    ResourceLease,
    ResourceLeaseStore,
    ResourceLeaseStoreError,
    build_resource_lease_store,
)
from research_system.schemas.core_runtime import (
    CoreToolCall,
    CoreToolResult,
)
from research_system.schemas.events import EventEnvelope
from research_system.schemas.tools import ToolDefinition
from research_system.schemas.turn_runtime import FollowupTaskRequest, FollowupTaskResult


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CoreToolError(RuntimeError):
    pass


@dataclass(frozen=True)
class CoreToolContext:
    run_id: str
    thread_id: str
    request_id: str
    run_root: Path
    actor: str
    turn_id: str | None = None
    cache_root: Path | None = None
    worker_id: str | None = None
    user_id: str | None = None
    tenant_id: str | None = None
    artifact_board: Any | None = None
    memory_store: Any | None = None
    skill_runtime: Any | None = None
    settings: Any | None = None
    graph_supplier: Callable[[], dict[str, Any] | None] | None = None
    request_intake_supplier: Callable[..., Any] | None = None
    cancellation: Event | None = None
    event_callback: Callable[[dict[str, Any]], None] | None = None
    allowed_tools: frozenset[str] | None = None
    allowed_permissions: frozenset[str] | None = None
    authorize_call: Callable[[], bool] | None = None
    authorize_approval: Callable[[str], bool] | None = None
    approval_id: str | None = None
    idempotency_key: str | None = None
    rate_limit_key: str | None = None
    tool_semaphore: Semaphore | None = None
    hook_runtime: Any | None = None
    followup_task_handler: Callable[[FollowupTaskRequest], FollowupTaskResult] | None = None
    approval_store: HumanApprovalStore | None = None


@dataclass(frozen=True)
class _RegisteredTool:
    definition: ToolDefinition
    handler: Callable[[dict[str, Any], CoreToolContext], dict[str, Any]]


@dataclass
class _ResourceLease:
    store: ResourceLeaseStore
    record: ResourceLease

    @property
    def lease_id(self) -> str:
        return self.record.lease_id

    @property
    def resource_key(self) -> str:
        return self.record.resource_key

    @property
    def expires_at(self) -> str:
        return self.record.expires_at


class _ResourceLeaseHeartbeat:
    def __init__(
        self,
        *,
        lease: _ResourceLease,
        lease_seconds: float,
        context: CoreToolContext,
        registry: "CoreToolRegistry",
        call: CoreToolCall,
        definition: ToolDefinition,
    ) -> None:
        self.lease = lease
        self.lease_seconds = lease_seconds
        self.context = context
        self.registry = registry
        self.call = call
        self.definition = definition
        self.stop_event = Event()
        self.lost_event = Event()
        self.thread: Thread | None = None

    def start(self) -> None:
        self.thread = Thread(target=self._run, name="agent-os-tool-lease-heartbeat", daemon=True)
        self.thread.start()

    def stop(self) -> bool:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=max(1.0, self.lease_seconds))
        return self.lost_event.is_set()

    def _run(self) -> None:
        interval = max(0.05, min(5.0, self.lease_seconds / 3.0))
        while not self.stop_event.wait(interval):
            try:
                renewed = self.lease.store.renew(
                    self.lease.record,
                    lease_seconds=self.lease_seconds,
                )
            except ResourceLeaseStoreError as exc:
                self._lost(f"resource lease renewal failed: {exc}")
                return
            if renewed is None:
                self._lost("resource lease is no longer owned by this Tool call")
                return
            self.lease.record = renewed
            self.registry._emit(
                self.context,
                "tool_resource_lease_renewed",
                call=self.call,
                definition=self.definition,
                extra={
                    "resource_key": self.lease.resource_key,
                    "lease_id": self.lease.lease_id,
                    "lease_expires_at": self.lease.expires_at,
                },
            )

    def _lost(self, reason: str) -> None:
        self.lost_event.set()
        if self.context.cancellation is not None:
            self.context.cancellation.set()
        self.registry._emit(
            self.context,
            "tool_resource_lease_lost",
            call=self.call,
            definition=self.definition,
            extra={
                "resource_key": self.lease.resource_key,
                "lease_id": self.lease.lease_id,
                "reason": reason,
            },
        )


class CoreToolRegistry:
    """Single registry for Agent OS Core tools and Pack-provided adapters."""

    def __init__(
        self,
        *,
        resource_lease_root: Path | None = None,
        resource_lease_store: ResourceLeaseStore | None = None,
        settings: Any | None = None,
    ) -> None:
        self._tools: dict[str, _RegisteredTool] = {}
        self._semaphores: dict[str, Semaphore] = {}
        self._rate_windows: dict[tuple[str, str], deque[float]] = {}
        self._lock = RLock()
        self.resource_lease_root = Path(resource_lease_root) if resource_lease_root else None
        self.resource_lease_store = resource_lease_store or build_resource_lease_store(
            settings=settings,
            resource_lease_root=self.resource_lease_root,
        )

    def register(
        self,
        definition: ToolDefinition,
        handler: Callable[[dict[str, Any], CoreToolContext], dict[str, Any]],
    ) -> None:
        name = definition.name.strip()
        if not name:
            raise ValueError("tool name is required")
        normalized = definition.model_copy(
            update={
                "name": name,
                "namespace": definition.namespace or "core",
            }
        )
        with self._lock:
            if name in self._tools:
                raise ValueError(f"duplicate core tool: {name}")
            self._tools[name] = _RegisteredTool(
                definition=normalized,
                handler=handler,
            )
            if normalized.max_concurrency is not None:
                self._semaphores[name] = Semaphore(normalized.max_concurrency)

    def definitions(self) -> list[ToolDefinition]:
        with self._lock:
            return [item.definition for item in self._tools.values()]

    def definition(self, name: str) -> ToolDefinition:
        try:
            return self._tools[name].definition
        except KeyError as exc:
            raise CoreToolError(f"unknown Core Tool: {name}") from exc

    def invoke(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any] | None,
        context: CoreToolContext,
        graph_revision: int | None = None,
        node_id: str | None = None,
    ) -> CoreToolResult:
        try:
            definition = self.definition(tool_name)
        except CoreToolError as exc:
            return self._rejected(
                context=context,
                tool_name=tool_name,
                arguments=arguments or {},
                graph_revision=graph_revision,
                node_id=node_id,
                error=str(exc),
            )
        if context.allowed_tools is not None and tool_name not in context.allowed_tools:
            return self._rejected(
                context=context,
                tool_name=tool_name,
                arguments=arguments or {},
                graph_revision=graph_revision,
                node_id=node_id,
                error="tool is outside the current caller policy",
            )
        caller_kind = "subagent" if context.actor.startswith("subagent:") else context.actor
        if caller_kind not in definition.allowed_callers:
            return self._rejected(
                context=context,
                tool_name=tool_name,
                arguments=arguments or {},
                graph_revision=graph_revision,
                node_id=node_id,
                error=f"tool is not available to caller kind: {caller_kind}",
            )
        if (
            context.allowed_permissions is not None
            and not set(definition.permissions).issubset(context.allowed_permissions)
        ):
            return self._rejected(
                context=context,
                tool_name=tool_name,
                arguments=arguments or {},
                graph_revision=graph_revision,
                node_id=node_id,
                error="tool permissions are outside the current caller policy",
            )
        if context.authorize_call is not None and not context.authorize_call():
            return self._rejected(
                context=context,
                tool_name=tool_name,
                arguments=arguments or {},
                graph_revision=graph_revision,
                node_id=node_id,
                error="run Tool call budget exceeded",
            )
        if definition.requires_approval:
            if context.hook_runtime is not None:
                allowed, _ = context.hook_runtime.run_event(
                    "PermissionRequest",
                    {
                        "run_id": context.run_id,
                        "thread_id": context.thread_id,
                        "request_id": context.request_id,
                        "turn_id": context.turn_id,
                        "tool_name": tool_name,
                        "arguments": dict(arguments or {}),
                        "approval_id": context.approval_id,
                    },
                )
                if not allowed:
                    return self._rejected(
                        context=context,
                        tool_name=tool_name,
                        arguments=arguments or {},
                        graph_revision=graph_revision,
                        node_id=node_id,
                        error="PermissionRequest hook blocked the Tool call",
                    )
            if not context.approval_id:
                if context.approval_store is None:
                    return self._rejected(
                        context=context,
                        tool_name=tool_name,
                        arguments=arguments or {},
                        graph_revision=graph_revision,
                        node_id=node_id,
                        error="Tool requires human approval before execution",
                    )
                approval_id = self._request_tool_approval(
                    context=context,
                    tool_name=tool_name,
                    arguments=arguments or {},
                    graph_revision=graph_revision,
                    node_id=node_id,
                )
                return self._approval_required(
                    context=context,
                    tool_name=tool_name,
                    arguments=arguments or {},
                    graph_revision=graph_revision,
                    node_id=node_id,
                    approval_id=approval_id,
                )
            if context.authorize_approval is None:
                return self._rejected(
                    context=context,
                    tool_name=tool_name,
                    arguments=arguments or {},
                    graph_revision=graph_revision,
                    node_id=node_id,
                    error="Tool approval authorizer is not configured",
                )
            if not context.authorize_approval(context.approval_id):
                return self._rejected(
                    context=context,
                    tool_name=tool_name,
                    arguments=arguments or {},
                    graph_revision=graph_revision,
                    node_id=node_id,
                    error="Tool approval is invalid or revoked",
                )
        if definition.idempotency_required and not context.idempotency_key:
            return self._rejected(
                context=context,
                tool_name=tool_name,
                arguments=arguments or {},
                graph_revision=graph_revision,
                node_id=node_id,
                error="Tool requires an idempotency key",
            )
        if context.cancellation is not None and context.cancellation.is_set():
            return self._rejected(
                context=context,
                tool_name=tool_name,
                arguments=arguments or {},
                graph_revision=graph_revision,
                node_id=node_id,
                error="tool call cancelled before execution",
                status="cancelled",
            )

        if context.hook_runtime is not None:
            allowed, rewritten = context.hook_runtime.run_event(
                "PreToolUse",
                {
                    "run_id": context.run_id,
                    "thread_id": context.thread_id,
                    "request_id": context.request_id,
                    "tool_name": tool_name,
                    "arguments": dict(arguments or {}),
                    "node_id": node_id,
                },
            )
            if not allowed:
                return self._rejected(
                    context=context,
                    tool_name=tool_name,
                    arguments=arguments or {},
                    graph_revision=graph_revision,
                    node_id=node_id,
                    error="PreToolUse hook blocked the Tool call",
                )
            if isinstance(rewritten.get("arguments"), dict):
                arguments = dict(rewritten["arguments"])
        payload = dict(arguments or {})
        try:
            jsonschema.validate(instance=payload, schema=definition.input_schema or {"type": "object"})
        except jsonschema.ValidationError as exc:
            return self._rejected(
                context=context,
                tool_name=tool_name,
                arguments=payload,
                graph_revision=graph_revision,
                node_id=node_id,
                error=f"tool input schema validation failed: {exc.message}",
            )

        if not self._acquire_rate_slot(tool_name, definition, context):
            return self._rejected(
                context=context,
                tool_name=tool_name,
                arguments=payload,
                graph_revision=graph_revision,
                node_id=node_id,
                error="Tool rate limit exceeded",
            )

        receipt = self._load_idempotency_receipt(context, tool_name)
        if receipt is not None:
            call = CoreToolCall(
                tool_name=tool_name,
                arguments=payload,
                actor=context.actor,
                run_id=context.run_id,
                thread_id=context.thread_id,
                request_id=context.request_id,
                graph_revision=graph_revision,
                node_id=node_id,
                idempotency_key=context.idempotency_key,
            )
            result = CoreToolResult(
                call_id=call.call_id,
                tool_name=tool_name,
                status="completed",
                output=dict(receipt),
                source_refs=list(receipt.get("source_refs") or []),
                metadata={"idempotency_hit": True},
            )
            self._emit(context, "tool_call_idempotency_hit", call=call, definition=definition, result=result)
            return result

        global_semaphore = context.tool_semaphore
        if global_semaphore is not None and not self._acquire_slot(
            global_semaphore, context.cancellation
        ):
            return self._rejected(
                context=context,
                tool_name=tool_name,
                arguments=payload,
                graph_revision=graph_revision,
                node_id=node_id,
                error="tool concurrency limit reached or call cancelled",
                status="cancelled" if context.cancellation and context.cancellation.is_set() else "rejected",
            )
        semaphore = self._semaphores.get(tool_name)
        if semaphore is not None and not self._acquire_slot(semaphore, context.cancellation):
            if global_semaphore is not None:
                global_semaphore.release()
            return self._rejected(
                context=context,
                tool_name=tool_name,
                arguments=payload,
                graph_revision=graph_revision,
                node_id=node_id,
                error="tool concurrency limit reached or call cancelled",
                status="cancelled" if context.cancellation and context.cancellation.is_set() else "rejected",
            )

        call = CoreToolCall(
            tool_name=tool_name,
            arguments=payload,
            actor=context.actor,
            run_id=context.run_id,
            thread_id=context.thread_id,
            request_id=context.request_id,
            graph_revision=graph_revision,
            node_id=node_id,
            idempotency_key=context.idempotency_key,
        )
        resource_lease: _ResourceLease | None = None
        try:
            resource_lease = self._acquire_resource_lease(
                tool_name=tool_name,
                definition=definition,
                payload=payload,
                context=context,
            )
        except CoreToolError as exc:
            if semaphore is not None:
                semaphore.release()
            if global_semaphore is not None:
                global_semaphore.release()
            return self._rejected(
                context=context,
                tool_name=tool_name,
                arguments=payload,
                graph_revision=graph_revision,
                node_id=node_id,
                error=str(exc),
            )
        if definition.resource_key_fields and resource_lease is None:
            if semaphore is not None:
                semaphore.release()
            if global_semaphore is not None:
                global_semaphore.release()
            return self._rejected(
                context=context,
                tool_name=tool_name,
                arguments=payload,
                graph_revision=graph_revision,
                node_id=node_id,
                error="Tool resource lease is currently held by another Worker",
                retryable=True,
                metadata={"resource_key_fields": list(definition.resource_key_fields)},
            )
        if resource_lease is not None:
            self._emit(
                context,
                "tool_resource_lease_acquired",
                call=call,
                definition=definition,
                extra={
                    "resource_key": resource_lease.resource_key,
                    "lease_id": resource_lease.lease_id,
                    "lease_expires_at": resource_lease.expires_at,
                },
            )
        self._emit(context, "tool_call_started", call=call, definition=definition)
        started_at = utc_now()
        heartbeat = (
            _ResourceLeaseHeartbeat(
                lease=resource_lease,
                lease_seconds=definition.resource_lease_seconds,
                context=context,
                registry=self,
                call=call,
                definition=definition,
            )
            if resource_lease is not None
            else None
        )
        if heartbeat is not None:
            heartbeat.start()
        try:
            try:
                raw_output = self._execute_handler(
                    tool_name=tool_name,
                    definition=definition,
                    payload=payload,
                    context=context,
                    call=call,
                )
                if heartbeat is not None and heartbeat.lost_event.is_set():
                    raise CoreToolError("Tool resource lease was lost during execution")
                output = dict(raw_output or {})
                jsonschema.validate(
                    instance=output,
                    schema=definition.output_schema or {"type": "object"},
                )
                if definition.source_refs_required and not output.get("source_refs"):
                    raise CoreToolError("Tool output is missing required source_refs")
                result = CoreToolResult(
                    call_id=call.call_id,
                    tool_name=tool_name,
                    status="completed",
                    output=output,
                    started_at=started_at,
                    completed_at=utc_now(),
                    source_refs=list(output.get("source_refs") or []),
                )
                self._save_idempotency_receipt(context, result)
                self._emit(context, "tool_call_completed", call=call, result=result)
                if context.hook_runtime is not None:
                    context.hook_runtime.run_event(
                        "PostToolUse",
                        {
                            "run_id": context.run_id,
                            "thread_id": context.thread_id,
                            "request_id": context.request_id,
                            "tool_name": tool_name,
                            "call_id": call.call_id,
                            "status": result.status,
                            "output": dict(result.output),
                        },
                    )
                return result
            except Exception as exc:
                result = CoreToolResult(
                    call_id=call.call_id,
                    tool_name=tool_name,
                    status="failed",
                    error=f"{exc.__class__.__name__}: {exc}",
                    started_at=started_at,
                    completed_at=utc_now(),
                )
                self._emit(context, "tool_call_failed", call=call, result=result)
                if context.hook_runtime is not None:
                    context.hook_runtime.run_event(
                        "PostToolUse",
                        {
                            "run_id": context.run_id,
                            "thread_id": context.thread_id,
                            "request_id": context.request_id,
                            "tool_name": tool_name,
                            "call_id": call.call_id,
                            "status": result.status,
                            "error": result.error,
                        },
                    )
                return result
        finally:
            if heartbeat is not None:
                heartbeat.stop()
            if semaphore is not None:
                semaphore.release()
            if global_semaphore is not None:
                global_semaphore.release()
            if resource_lease is not None:
                self._release_resource_lease(resource_lease, context=context, call=call, definition=definition)

    @staticmethod
    def _acquire_slot(semaphore: Semaphore, cancellation: Event | None) -> bool:
        deadline = monotonic() + 30.0
        while monotonic() < deadline:
            if cancellation is not None and cancellation.is_set():
                return False
            if semaphore.acquire(timeout=0.1):
                return True
        return False

    def _execute_handler(
        self,
        *,
        tool_name: str,
        definition: ToolDefinition,
        payload: dict[str, Any],
        context: CoreToolContext,
        call: CoreToolCall,
    ) -> dict[str, Any]:
        registered = self._tools[tool_name]
        for attempt in range(definition.max_retries + 1):
            try:
                if definition.timeout_seconds is None:
                    return registered.handler(payload, context)
                executor = ThreadPoolExecutor(
                    max_workers=1,
                    thread_name_prefix="cognitrade-tool",
                )
                future = executor.submit(registered.handler, payload, context)
                try:
                    return future.result(timeout=definition.timeout_seconds)
                except FutureTimeoutError as exc:
                    future.cancel()
                    if context.cancellation is not None:
                        context.cancellation.set()
                    raise TimeoutError(
                        f"Tool timed out after {definition.timeout_seconds}s"
                    ) from exc
                finally:
                    executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                if attempt >= definition.max_retries:
                    raise
                if context.cancellation is not None and context.cancellation.is_set():
                    raise
                self._emit(context, "tool_call_retry", call=call, definition=definition)
        raise CoreToolError("Tool execution exhausted retry policy")

    def _rejected(
        self,
        *,
        context: CoreToolContext,
        tool_name: str,
        arguments: dict[str, Any],
        graph_revision: int | None,
        node_id: str | None,
        error: str,
        status: str = "rejected",
        retryable: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> CoreToolResult:
        call = CoreToolCall(
            tool_name=tool_name,
            arguments=dict(arguments),
            actor=context.actor,
            run_id=context.run_id,
            thread_id=context.thread_id,
            request_id=context.request_id,
            graph_revision=graph_revision,
            node_id=node_id,
            idempotency_key=context.idempotency_key,
        )
        result = CoreToolResult(
            call_id=call.call_id,
            tool_name=tool_name,
            status=status,
            error=error,
            retryable=retryable,
            metadata=dict(metadata or {}),
        )
        self._emit(context, "tool_call_rejected", call=call, result=result)
        return result

    @staticmethod
    def _approval_id(
        *,
        context: CoreToolContext,
        tool_name: str,
        node_id: str | None,
    ) -> str:
        import hashlib

        identity = f"{context.run_id}:{node_id or f'core_tool:{tool_name}'}:{tool_name}:{context.idempotency_key or ''}"
        return f"approval_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]}"

    def _request_tool_approval(
        self,
        *,
        context: CoreToolContext,
        tool_name: str,
        arguments: dict[str, Any],
        graph_revision: int | None,
        node_id: str | None,
    ) -> str:
        if context.approval_store is None:
            raise CoreToolError("approval store is not configured")
        approval_node_id = node_id or f"core_tool:{tool_name}"
        approval_id = self._approval_id(
            context=context,
            tool_name=tool_name,
            node_id=approval_node_id,
        )
        existing = context.approval_store.get(approval_id)
        if existing is None:
            context.approval_store.request(
                run_id=context.run_id,
                node_id=approval_node_id,
                capability=tool_name,
                reason=f"Core Tool {tool_name} requires human approval.",
                approval_id=approval_id,
                graph_revision=graph_revision,
                user_id=context.user_id,
                tenant_id=context.tenant_id,
            )
        return approval_id

    def _approval_required(
        self,
        *,
        context: CoreToolContext,
        tool_name: str,
        arguments: dict[str, Any],
        graph_revision: int | None,
        node_id: str | None,
        approval_id: str,
    ) -> CoreToolResult:
        call = CoreToolCall(
            tool_name=tool_name,
            arguments=dict(arguments),
            actor=context.actor,
            run_id=context.run_id,
            thread_id=context.thread_id,
            request_id=context.request_id,
            graph_revision=graph_revision,
            node_id=node_id or f"core_tool:{tool_name}",
            idempotency_key=context.idempotency_key,
        )
        result = CoreToolResult(
            call_id=call.call_id,
            tool_name=tool_name,
            status="waiting_approval",
            error="Tool is waiting for human approval",
            retryable=True,
            metadata={"approval_id": approval_id},
        )
        self._emit(context, "tool_call_waiting_approval", call=call, result=result)
        return result

    def _emit(
        self,
        context: CoreToolContext,
        event_type: str,
        *,
        call: CoreToolCall,
        definition: ToolDefinition | None = None,
        result: CoreToolResult | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        payload = {
            "event_type": event_type,
            "tool_name": call.tool_name,
            "call_id": call.call_id,
            "actor": call.actor,
            "run_id": call.run_id,
            "thread_id": call.thread_id,
            "request_id": call.request_id,
            "graph_revision": call.graph_revision,
            "node_id": call.node_id,
            "arguments": _redact_arguments(call.arguments),
            "idempotency_key": call.idempotency_key,
        }
        if definition is not None:
            payload["side_effect_level"] = definition.side_effect_level
            payload["tool_version"] = definition.version
            payload["permissions"] = list(definition.permissions)
        if result is not None:
            payload["status"] = result.status
            payload["error"] = result.error
            payload["source_refs"] = result.source_refs
            payload["metadata"] = result.metadata
        if extra:
            payload.update(extra)
        with self._lock:
            payload["turn_id"] = context.turn_id
            payload["sequence"] = 0
            payload = EventEnvelope.model_validate(payload).model_dump(mode="json")
            payload = append_canonical_event(
                context.run_root / "execution_events.jsonl",
                payload,
            )
        if context.event_callback is not None:
            context.event_callback(payload)

    def _acquire_rate_slot(
        self,
        tool_name: str,
        definition: ToolDefinition,
        context: CoreToolContext,
    ) -> bool:
        limit = definition.rate_limit_per_minute
        if limit is None:
            return True
        key = (tool_name, context.rate_limit_key or context.run_id)
        now = monotonic()
        with self._lock:
            window = self._rate_windows.setdefault(key, deque())
            while window and now - window[0] >= 60.0:
                window.popleft()
            if len(window) >= limit:
                return False
            window.append(now)
            return True

    def _acquire_resource_lease(
        self,
        *,
        tool_name: str,
        definition: ToolDefinition,
        payload: dict[str, Any],
        context: CoreToolContext,
    ) -> _ResourceLease | None:
        fields = [str(field).strip() for field in definition.resource_key_fields if str(field).strip()]
        if not fields:
            return None
        values: dict[str, Any] = {}
        for field in fields:
            if field in payload:
                values[field] = payload[field]
            elif field == "run_id":
                values[field] = context.run_id
            elif field == "thread_id":
                values[field] = context.thread_id
            elif field == "tenant_id":
                values[field] = context.tenant_id
            else:
                raise CoreToolError(f"Tool resource key field is missing: {field}")
        resource_key = json.dumps(
            {"tool": tool_name, "namespace": definition.namespace, "values": values},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        store = self.resource_lease_store
        if store is None:
            store = FileResourceLeaseStore(
                self.resource_lease_root or (Path(context.run_root).parent / ".tool_resource_leases")
            )
        metadata = {
            "tool_name": tool_name,
            "run_id": context.run_id,
            "thread_id": context.thread_id,
            "worker_id": str(getattr(context, "worker_id", None) or context.run_id),
        }
        try:
            record = store.acquire(
                resource_key=resource_key,
                metadata=metadata,
                lease_seconds=definition.resource_lease_seconds,
            )
        except ResourceLeaseStoreError as exc:
            raise CoreToolError(str(exc)) from exc
        return _ResourceLease(store=store, record=record) if record is not None else None

    def _release_resource_lease(
        self,
        lease: _ResourceLease,
        *,
        context: CoreToolContext,
        call: CoreToolCall,
        definition: ToolDefinition,
    ) -> None:
        released = False
        try:
            released = lease.store.release(lease.record)
        except ResourceLeaseStoreError:
            released = False
        self._emit(
            context,
            "tool_resource_lease_released",
            call=call,
            definition=definition,
            extra={
                "resource_key": lease.resource_key,
                "lease_id": lease.lease_id,
                "lease_released": released,
            },
        )

    @staticmethod
    def _idempotency_path(context: CoreToolContext, tool_name: str) -> Path | None:
        if not context.idempotency_key:
            return None
        digest = re.sub(
            r"[^A-Za-z0-9_.-]+",
            "_",
            f"{tool_name}:{context.idempotency_key}",
        )
        return context.run_root / "tool_idempotency" / f"{digest[:240]}.json"

    def _load_idempotency_receipt(
        self,
        context: CoreToolContext,
        tool_name: str,
    ) -> dict[str, Any] | None:
        path = self._idempotency_path(context, tool_name)
        if path is None:
            return None
        payload = read_json(path)
        return dict(payload.get("output") or {}) if isinstance(payload, dict) else None

    def _save_idempotency_receipt(
        self,
        context: CoreToolContext,
        result: CoreToolResult,
    ) -> None:
        path = self._idempotency_path(context, result.tool_name)
        if path is None or result.status != "completed":
            return
        write_json(
            path,
            {
                "tool_name": result.tool_name,
                "idempotency_key": context.idempotency_key,
                "output": result.output,
            },
        )


def build_core_tool_registry(
    *,
    settings: Any | None = None,
    resource_lease_store: ResourceLeaseStore | None = None,
    resource_lease_root: Path | None = None,
) -> CoreToolRegistry:
    from research_system.runtime.core.builtin_tools import register_builtin_tools

    registry = CoreToolRegistry(
        settings=settings,
        resource_lease_store=resource_lease_store,
        resource_lease_root=resource_lease_root,
    )
    register_builtin_tools(registry)
    return registry


def register_domain_pack_tools(registry: CoreToolRegistry, pack_registry) -> CoreToolRegistry:
    """Adapt installed Pack Tool contracts into the shared Registry."""
    for manifest in pack_registry.list():
        for pack_tool in pack_registry.tool_definitions(domain_id=manifest.domain_id):
            name = str(pack_tool.name).strip()
            if not name:
                continue
            try:
                registry.definition(name)
            except CoreToolError:
                pass
            else:
                raise ValueError(f"duplicate Tool Registry name: {name}")
            implementation = str(pack_tool.implementation or "").strip()
            if not implementation:
                raise ValueError(f"Pack tool has no implementation: {manifest.domain_id}:{name}")
            handler = pack_registry.resolve_callable(
                domain_id=manifest.domain_id,
                implementation=implementation,
            )
            registry.register(
                ToolDefinition(
                    name=name,
                    namespace="pack",
                    version=pack_tool.version,
                    description=pack_tool.description,
                    input_schema=pack_tool.input_schema,
                    output_schema=pack_tool.output_schema,
                    permissions=pack_tool.permissions,
                    side_effect_level=pack_tool.side_effect_level,
                    parallel_safe=pack_tool.parallel_safe,
                    max_concurrency=pack_tool.max_concurrency,
                    timeout_seconds=pack_tool.timeout_seconds,
                    max_retries=pack_tool.max_retries,
                    requires_approval=pack_tool.requires_approval,
                    idempotency_required=pack_tool.idempotency_required,
                    rate_limit_per_minute=pack_tool.rate_limit_per_minute,
                    source_refs_required=pack_tool.source_refs_required,
                    allowed_callers=list(pack_tool.allowed_callers),
                ),
                handler,
            )
    return registry


def register_mcp_tools(
    registry: CoreToolRegistry,
    *,
    settings=None,
    source_registry=None,
) -> CoreToolRegistry:
    """Register explicitly configured MCP tools in the canonical Tool Registry."""
    from research_system.adapters.vendors.mcp_source_registry import McpSourceRegistry
    from research_system.runtime.core.mcp_tool_adapter import invoke_mcp_tool

    sources = source_registry or McpSourceRegistry(settings=settings)
    for server in sources.list_all():
        for mcp_tool in server.tools:
            tool_name = f"mcp.{server.id}.{mcp_tool.name}"
            try:
                registry.definition(tool_name)
            except CoreToolError:
                pass
            else:
                raise ValueError(f"duplicate MCP Tool Registry name: {tool_name}")
            side_effect = "read_only" if mcp_tool.read_only else "write_external"
            permission = f"mcp:server:{server.id}"

            def handler(arguments, context, *, source=server, name=mcp_tool.name):
                output = invoke_mcp_tool(
                    server_source=source,
                    tool_name=name,
                    arguments=arguments,
                    timeout_seconds=source.timeout_seconds,
                    cancellation=context.cancellation,
                )
                payload = dict(output or {})
                refs = list(payload.get("source_refs") or [])
                refs.append(
                    {
                        "type": "mcp_tool",
                        "server_id": source.id,
                        "tool_name": name,
                    }
                )
                payload["source_refs"] = refs
                return payload

            registry.register(
                ToolDefinition(
                    name=tool_name,
                    namespace="external",
                    description=mcp_tool.summary or f"MCP tool {server.id}/{mcp_tool.name}",
                    input_schema=mcp_tool.input_json_schema or {"type": "object"},
                    output_schema=mcp_tool.output_json_schema or {"type": "object"},
                    permissions=[permission],
                    side_effect_level=side_effect,
                    timeout_seconds=server.timeout_seconds,
                    requires_approval=not mcp_tool.read_only,
                    idempotency_required=not mcp_tool.read_only,
                    source_refs_required=True,
                    allowed_callers=["subagent"],
                ),
                handler,
            )
    return registry


def _inspect_artifacts(arguments: dict[str, Any], context: CoreToolContext) -> dict[str, Any]:
    board = context.artifact_board
    if board is None:
        return {"artifacts": []}
    artifact_id = str(arguments.get("artifact_id") or "").strip()
    if artifact_id:
        artifact = board.get(artifact_id)
        return {"artifacts": [artifact.model_dump(mode="json")] if artifact else []}
    artifacts = board.list_artifacts(
        run_id=context.run_id,
        artifact_type=str(arguments.get("artifact_type") or "").strip() or None,
        status="active",
    )
    limit = min(max(int(arguments.get("limit") or 20), 1), 100)
    return {"artifacts": [item.model_dump(mode="json") for item in artifacts[-limit:]]}


def _query_memory(arguments: dict[str, Any], context: CoreToolContext) -> dict[str, Any]:
    if context.memory_store is None:
        return {"memories": []}
    records = context.memory_store.list(
        layer=str(arguments.get("layer") or "").strip() or None,
        domain_id=str(arguments.get("domain_id") or "").strip() or None,
        status="verified",
        tenant_id=context.tenant_id,
        user_id=context.user_id,
        thread_id=context.thread_id,
        query=str(arguments.get("query") or "").strip() or None,
    )
    limit = min(max(int(arguments.get("limit") or 20), 1), 100)
    return {
        "retrieval": {
            "mode": "lexical_ranked",
            "query": str(arguments.get("query") or "").strip() or None,
        },
        "memories": [item.model_dump(mode="json") for item in records[:limit]],
    }


def _inspect_graph(arguments: dict[str, Any], context: CoreToolContext) -> dict[str, Any]:
    graph = context.graph_supplier() if context.graph_supplier is not None else None
    return graph or {"mode": "core", "graph": None}


def _request_intake(arguments: dict[str, Any], context: CoreToolContext) -> dict[str, Any]:
    resolver = context.request_intake_supplier
    if resolver is None:
        raise CoreToolError("no RequestIntake resolver is registered")
    from research_system.schemas.protocol import RequestEnvelope

    request_payload = arguments.get("request")
    if not isinstance(request_payload, dict):
        raise CoreToolError("request intake requires a serialized RequestEnvelope")
    request = RequestEnvelope.model_validate(request_payload)
    decision = resolver(
        request,
        runtime_context=dict(arguments.get("runtime_context") or {}),
    )
    return {"decision": decision.model_dump(mode="json")}


def _followup_task(arguments: dict[str, Any], context: CoreToolContext) -> dict[str, Any]:
    if context.followup_task_handler is None:
        raise CoreToolError("followup_task is unavailable outside a TurnRunner context")
    request = FollowupTaskRequest.model_validate(arguments)
    return context.followup_task_handler(request).model_dump(mode="json")


def _run_status(arguments: dict[str, Any], context: CoreToolContext) -> dict[str, Any]:
    state = read_json(context.run_root / "main_agent_state.json")
    if state is None:
        state = read_json(context.run_root / "run_state.json") or {
            "run_id": context.run_id,
            "status": "running",
        }
    return dict(state)


def _read_file(arguments: dict[str, Any], context: CoreToolContext) -> dict[str, Any]:
    path = _resolve_workspace_path(str(arguments.get("path") or ""), context)
    max_chars = min(max(int(arguments.get("max_chars") or 200_000), 1), 1_000_000)
    content = path.read_text(encoding="utf-8")[:max_chars]
    return {
        "path": str(path),
        "content": content,
        "truncated": len(content) >= max_chars,
        "source_refs": [{"type": "file", "id": str(path)}],
    }


def _search_files(arguments: dict[str, Any], context: CoreToolContext) -> dict[str, Any]:
    query = str(arguments.get("query") or "")
    root = _resolve_workspace_path(str(arguments.get("path") or "."), context)
    limit = min(max(int(arguments.get("limit") or 50), 1), 100)
    matches: list[dict[str, Any]] = []
    candidates = [root] if root.is_file() else root.rglob("*")
    for path in candidates:
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for line_number, line in enumerate(lines, start=1):
            if query.lower() in line.lower():
                matches.append({"path": str(path), "line": line_number, "text": line[:2_000]})
                if len(matches) >= limit:
                    return {"matches": matches}
    return {"matches": matches}


def _invoke_skill(arguments: dict[str, Any], context: CoreToolContext) -> dict[str, Any]:
    skill_name = str(arguments.get("skill_name") or "").strip()
    if not skill_name:
        raise CoreToolError("skill_name is required")
    runtime = context.skill_runtime
    if runtime is None:
        raise CoreToolError(
            "no Core Skill runtime is registered; install or explicitly inject a governed Skill"
        )
    result = runtime.run(skill_name, **dict(arguments.get("arguments") or {}))
    return {
        "result": dict(result or {}),
        "source_refs": [{"type": "skill", "id": skill_name}],
    }


def _resolve_workspace_path(raw_path: str, context: CoreToolContext) -> Path:
    candidate = Path(raw_path or ".")
    if not candidate.is_absolute():
        base = Path(getattr(context.settings, "workspace_root", context.run_root))
        candidate = base / candidate
    resolved = candidate.resolve()
    allowed_roots = [Path(context.run_root).resolve()]
    if context.settings is not None:
        allowed_roots.append(Path(context.settings.workspace_root).resolve())
    if not any(root == resolved or root in resolved.parents for root in allowed_roots):
        raise CoreToolError(f"file path is outside Agent OS workspace roots: {resolved}")
    if not resolved.exists():
        raise CoreToolError(f"file path does not exist: {resolved}")
    return resolved


def _redact_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in arguments.items():
        if re.search(r"token|secret|password|api[_-]?key", str(key), re.IGNORECASE):
            redacted[key] = "<redacted>"
        elif isinstance(value, str) and len(value) > 2_000:
            redacted[key] = f"{value[:2_000]}…"
        else:
            redacted[key] = value
    return redacted


__all__ = [
    "CoreToolContext",
    "CoreToolError",
    "CoreToolRegistry",
    "ToolRegistry",
    "build_core_tool_registry",
    "register_domain_pack_tools",
    "register_mcp_tools",
]

ToolRegistry = CoreToolRegistry
