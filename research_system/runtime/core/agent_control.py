from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_system.runtime.providers.provider_registry import LLMProviderError
from research_system.runtime.storage.jsonl import append_jsonl, read_json, write_json
from research_system.schemas.runtime import RuntimeItemRecord, RuntimeThreadState, RuntimeTurnState
from research_system.schemas.subagents import SubagentExecutionRecord, SubagentInvocation


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AgentControl:
    def __init__(self, *, paths, run_id: str) -> None:
        self.paths = paths
        self.run_id = run_id
        self._observers: list[Callable[..., None]] = []

    def add_observer(self, observer: Callable[..., None]) -> None:
        self._observers.append(observer)

    def spawn(
        self,
        *,
        thread_id: str,
        turn_id: str,
        request_id: str,
        agent: str | None,
        phase: str | None,
        title: str,
        objective: str | None,
        payload: dict[str, Any] | None = None,
        item_type: str = "subagent",
    ) -> RuntimeItemRecord:
        item = RuntimeItemRecord(
            thread_id=thread_id,
            turn_id=turn_id,
            run_id=self.run_id,
            request_id=request_id,
            item_type=item_type,  # type: ignore[arg-type]
            status="queued",
            agent=agent,
            phase=phase,
            title=title,
            objective=objective,
            payload=dict(payload or {}),
        )
        self._write_item(item)
        return item

    def spawn_child_thread(
        self,
        *,
        parent_thread_id: str,
        parent_turn_id: str,
        request_id: str,
        intent: str,
        objective: str | None,
        agent: str | None,
        phase: str | None,
        execution_policy=None,
    ) -> tuple[RuntimeThreadState | None, RuntimeTurnState | None]:
        child_thread_budget_exhausted = False
        if execution_policy is not None:
            reserve = getattr(execution_policy, "reserve_child_thread", None)
            if callable(reserve):
                child_thread_budget_exhausted = not bool(reserve(turn_id=parent_turn_id))
        if child_thread_budget_exhausted:
            self._record_child_thread_budget_degradation(
                thread_id=parent_thread_id,
                turn_id=parent_turn_id,
                request_id=request_id,
                agent=agent,
                phase=phase,
                intent=intent,
                objective=objective,
                execution_policy=execution_policy,
            )
            return None, None
        thread = RuntimeThreadState(
            run_id=self.run_id,
            request_id=request_id,
            intent=intent,
            status="ready",
            parent_thread_id=parent_thread_id,
            total_turns=1,
        )
        turn = RuntimeTurnState(
            thread_id=thread.thread_id,
            run_id=self.run_id,
            request_id=request_id,
            turn_index=1,
            status="ready",
            intent=intent,
            objective=objective,
        )
        thread.active_turn_id = turn.turn_id
        self._write_child_thread_state(thread)
        self._write_child_turn_state(thread, turn)
        lifecycle_item = self.spawn(
            thread_id=parent_thread_id,
            turn_id=parent_turn_id,
            request_id=request_id,
            agent=agent,
            phase=phase,
            title=f"child_thread:{thread.thread_id}",
            objective=objective,
            payload={
                "child_thread_id": thread.thread_id,
                "child_turn_id": turn.turn_id,
                "intent": intent,
            },
            item_type="thread",
        )
        self.send(lifecycle_item, payload={"child_thread_id": thread.thread_id})
        self.wait(
            lifecycle_item,
            status="completed",
            payload={"child_thread_id": thread.thread_id, "child_turn_id": turn.turn_id},
        )
        return thread, turn

    def send(self, item: RuntimeItemRecord, *, payload: dict[str, Any] | None = None) -> RuntimeItemRecord:
        item.status = "running"
        item.updated_at = _utc_now()
        if payload:
            item.payload.update(payload)
        self._write_item(item)
        self._sync_child_execution_state(thread_id=item.thread_id, turn_id=item.turn_id, status="running")
        self._notify(stage="started", item=item, payload=payload or {})
        return item

    def wait(
        self,
        item: RuntimeItemRecord,
        *,
        status: str,
        payload: dict[str, Any] | None = None,
        degrade_reason: str | None = None,
    ) -> RuntimeItemRecord:
        item.status = status  # type: ignore[assignment]
        item.degrade_reason = degrade_reason
        item.updated_at = _utc_now()
        if payload:
            item.payload.update(payload)
        self._write_item(item)
        self._sync_child_execution_state(
            thread_id=item.thread_id,
            turn_id=item.turn_id,
            status="completed" if status in {"completed", "degraded"} else "failed" if status == "failed" else None,
        )
        self._notify(
            stage="completed" if status == "completed" else "failed" if status == "failed" else "updated",
            item=item,
            payload=payload or {},
        )
        return item

    def invoke(
        self,
        *,
        registry=None,
        subagent=None,
        invocation: SubagentInvocation,
        thread_id: str,
        turn_id: str,
        request_id: str,
        agent: str | None,
        phase: str | None,
        execution_policy=None,
    ) -> tuple[dict[str, Any], SubagentExecutionRecord]:
        invocation, llm_degrade_reason = self._prepare_invocation_for_runtime(
            invocation=invocation,
            registry=registry,
            subagent=subagent,
            turn_id=turn_id,
            execution_policy=execution_policy,
        )
        budget_exhausted = False
        if execution_policy is not None:
            reserve = getattr(execution_policy, "reserve_subagent_call", None)
            if callable(reserve):
                budget_exhausted = not bool(reserve(turn_id=turn_id))
        if budget_exhausted:
            self._record_budget_degradation(
                thread_id=thread_id,
                turn_id=turn_id,
                request_id=request_id,
                agent=agent,
                phase=phase,
                invocation=invocation,
                execution_policy=execution_policy,
            )
        if subagent is None:
            if registry is None:
                raise ValueError("registry or subagent is required")
            subagent = registry.get(invocation.subagent_type)
        if not str(invocation.invocation_id or "").strip():
            invocation = invocation.model_copy(update={"invocation_id": self._build_invocation_id()})
        item = self.spawn(
            thread_id=thread_id,
            turn_id=turn_id,
            request_id=request_id,
            agent=agent,
            phase=phase,
            title=invocation.subagent_type,
            objective=invocation.objective,
            payload={
                "subagent_type": invocation.subagent_type,
                "invocation_id": invocation.invocation_id,
                "inputs": invocation.inputs,
                "context": invocation.context or {},
            },
        )
        self.send(item, payload={"invocation_id": invocation.invocation_id})
        notify_started = getattr(registry, "notify_started", None) if registry is not None else None
        if callable(notify_started):
            notify_started(invocation=invocation)
        try:
            outputs, record = self._run_subagent(subagent=subagent, invocation=invocation)
        except LLMProviderError as exc:
            fallback_bundle = self._prepare_error_fallback(
                invocation=invocation,
                registry=registry,
                turn_id=turn_id,
                error=exc,
            )
            if fallback_bundle is None:
                self._notify_registry_failed(
                    registry=registry,
                    invocation=invocation,
                    objective=invocation.objective,
                    inputs=invocation.inputs,
                    error=exc,
                )
                self.wait(
                    item,
                    status="failed",
                    payload={"error": f"{exc.__class__.__name__}: {exc}"},
                )
                raise
            fallback_invocation, fallback_subagent, error_degrade_reason = fallback_bundle
            try:
                outputs, record = self._run_subagent(subagent=fallback_subagent, invocation=fallback_invocation)
            except Exception as fallback_exc:
                self._notify_registry_failed(
                    registry=registry,
                    invocation=invocation,
                    objective=invocation.objective,
                    inputs=invocation.inputs,
                    error=fallback_exc,
                )
                self.wait(
                    item,
                    status="failed",
                    payload={"error": f"{fallback_exc.__class__.__name__}: {fallback_exc}"},
                )
                raise
            llm_degrade_reason = self._combine_degrade_reason(llm_degrade_reason, error_degrade_reason)
        except Exception as exc:
            self._notify_registry_failed(
                registry=registry,
                invocation=invocation,
                objective=invocation.objective,
                inputs=invocation.inputs,
                error=exc,
            )
            self.wait(
                item,
                status="failed",
                payload={"error": f"{exc.__class__.__name__}: {exc}"},
            )
            raise
        degrade_reason = self._combine_degrade_reason(llm_degrade_reason, self._extract_degrade_reason(outputs))
        if budget_exhausted:
            degrade_reason = self._combine_degrade_reason("turn_subagent_budget_exhausted", degrade_reason)
        if degrade_reason and isinstance(outputs, dict):
            outputs["degraded"] = True
            outputs["degrade_reason"] = degrade_reason
            record.output_keys = sorted(outputs.keys())
        final_status = "degraded" if degrade_reason else "completed"
        self.wait(
            item,
            status=final_status,
            payload={
                "output_keys": sorted(outputs.keys()),
                "notes": list(record.notes or []),
            },
            degrade_reason=degrade_reason,
        )
        notify_completed = getattr(registry, "notify_completed", None) if registry is not None else None
        if callable(notify_completed):
            notify_completed(invocation=invocation, record=record, outputs=outputs)
        return outputs, record

    def run_bound(
        self,
        *,
        registry,
        invocation: SubagentInvocation,
        binding: dict[str, Any],
    ) -> tuple[dict[str, Any], SubagentExecutionRecord]:
        return self.invoke(
            registry=registry,
            invocation=invocation,
            thread_id=str(binding.get("thread_id") or ""),
            turn_id=str(binding.get("turn_id") or ""),
            request_id=str(binding.get("request_id") or ""),
            agent=binding.get("agent"),
            phase=binding.get("phase"),
            execution_policy=binding.get("execution_policy"),
        )

    def _extract_degrade_reason(self, outputs: dict[str, Any]) -> str | None:
        if not isinstance(outputs, dict):
            return None
        degraded = bool(outputs.get("degraded"))
        reason = str(outputs.get("degrade_reason") or "").strip()
        if degraded and reason:
            return reason
        return None

    def _prepare_invocation_for_runtime(
        self,
        *,
        invocation: SubagentInvocation,
        registry,
        subagent,
        turn_id: str,
        execution_policy,
    ) -> tuple[SubagentInvocation, str | None]:
        if not invocation.requires_llm:
            return invocation, None
        target_subagent = subagent
        if target_subagent is None and registry is not None:
            target_subagent = registry.get(invocation.subagent_type)
        provider = getattr(target_subagent, "llm_provider", None)
        if provider is not None and not bool(getattr(provider, "available", True)):
            fallback_type = str(invocation.fallback_subagent_type or "").strip()
            if fallback_type:
                fallback_inputs = dict(invocation.fallback_inputs or invocation.inputs)
                self._inject_runtime_fallback_inputs(
                    fallback_inputs,
                    degrade_reason="llm_unavailable",
                )
                fallback_context = dict(invocation.context or {})
                fallback_context.update(dict(invocation.fallback_context or {}))
                fallback_context.setdefault("runtime_fallback_reason", "llm_unavailable")
                return (
                    invocation.model_copy(
                        update={
                            "subagent_type": fallback_type,
                            "inputs": fallback_inputs,
                            "objective": invocation.fallback_objective or invocation.objective,
                            "context": fallback_context,
                            "requires_llm": False,
                            "fallback_subagent_type": None,
                            "fallback_inputs": {},
                            "fallback_objective": None,
                            "fallback_context": {},
                        }
                    ),
                    "llm_unavailable",
                )
            return invocation, "llm_unavailable"
        if execution_policy is None:
            return invocation, None
        can_use_llm = getattr(execution_policy, "reserve_llm_call", None)
        if not callable(can_use_llm):
            return invocation, None
        if bool(can_use_llm(turn_id=turn_id)):
            return invocation, None
        fallback_type = str(invocation.fallback_subagent_type or "").strip()
        if not fallback_type:
            return invocation, "turn_llm_budget_exhausted"
        target_registry = registry
        if target_registry is None and subagent is None:
            return invocation, "turn_llm_budget_exhausted"
        if target_registry is not None:
            target_registry.get(fallback_type)
        fallback_inputs = dict(invocation.fallback_inputs or invocation.inputs)
        self._inject_runtime_fallback_inputs(
            fallback_inputs,
            degrade_reason="turn_llm_budget_exhausted",
        )
        fallback_context = dict(invocation.context or {})
        fallback_context.update(dict(invocation.fallback_context or {}))
        fallback_context.setdefault("runtime_fallback_reason", "turn_llm_budget_exhausted")
        return (
            invocation.model_copy(
                update={
                    "subagent_type": fallback_type,
                    "inputs": fallback_inputs,
                    "objective": invocation.fallback_objective or invocation.objective,
                    "context": fallback_context,
                    "requires_llm": False,
                    "fallback_subagent_type": None,
                    "fallback_inputs": {},
                    "fallback_objective": None,
                    "fallback_context": {},
                }
            ),
            "turn_llm_budget_exhausted",
        )

    def _inject_runtime_fallback_inputs(
        self,
        inputs: dict[str, Any],
        *,
        degrade_reason: str,
    ) -> None:
        if "runtime_degraded_reason" in inputs and not inputs.get("runtime_degraded_reason"):
            inputs["runtime_degraded_reason"] = degrade_reason
        if "degraded_note" in inputs and not inputs.get("degraded_note"):
            inputs["degraded_note"] = degrade_reason

    def _prepare_error_fallback(
        self,
        *,
        invocation: SubagentInvocation,
        registry,
        turn_id: str,
        error: LLMProviderError,
    ) -> tuple[SubagentInvocation, Any, str] | None:
        fallback_type = str(invocation.error_fallback_subagent_type or "").strip()
        if not fallback_type or registry is None:
            return None
        fallback_subagent = registry.get(fallback_type)
        fallback_context = dict(invocation.context or {})
        fallback_context.update(dict(invocation.error_fallback_context or {}))
        error_reason = self._derive_llm_error_reason(error)
        fallback_context.setdefault("runtime_fallback_reason", error_reason)
        fallback_context.setdefault("fallback_from_subagent_type", invocation.subagent_type)
        fallback_objective = invocation.error_fallback_objective or invocation.objective
        fallback_inputs = dict(invocation.error_fallback_inputs or {})
        if "error_name" not in fallback_inputs:
            fallback_inputs["error_name"] = error.__class__.__name__
        fallback_invocation = invocation.model_copy(
            update={
                "subagent_type": fallback_type,
                "inputs": fallback_inputs,
                "objective": fallback_objective,
                "context": fallback_context,
                "requires_llm": False,
                "fallback_subagent_type": None,
                "error_fallback_subagent_type": None,
                "error_fallback_inputs": {},
                "error_fallback_objective": None,
                "error_fallback_context": {},
            }
        )
        return fallback_invocation, fallback_subagent, error_reason

    def _derive_llm_error_reason(self, error: Exception) -> str:
        error_text = str(error)
        if "llm_request_timeout" in error_text:
            return "llm_request_timeout"
        return f"llm_error:{error.__class__.__name__}"

    def _combine_degrade_reason(self, primary: str | None, secondary: str | None) -> str | None:
        parts: list[str] = []
        for value in (primary, secondary):
            token = str(value or "").strip()
            if token and token not in parts:
                parts.append(token)
        return ";".join(parts) or None

    def _record_budget_degradation(
        self,
        *,
        thread_id: str,
        turn_id: str,
        request_id: str,
        agent: str | None,
        phase: str | None,
        invocation: SubagentInvocation,
        execution_policy,
    ) -> None:
        payload = {
            "subagent_type": invocation.subagent_type,
            "objective": invocation.objective,
        }
        max_subagent_calls = getattr(execution_policy, "max_subagent_calls_per_turn", None)
        if max_subagent_calls is not None:
            payload["max_subagent_calls_per_turn"] = max_subagent_calls
        item = self.spawn(
            thread_id=thread_id,
            turn_id=turn_id,
            request_id=request_id,
            agent=agent,
            phase=phase,
            title=invocation.subagent_type,
            objective=f"degradation:{invocation.subagent_type}",
            payload=payload,
            item_type="degradation",
        )
        self.send(item, payload={"reason": "turn_subagent_budget_exhausted"})
        self.wait(
            item,
            status="degraded",
            payload={"reason": "turn_subagent_budget_exhausted", **payload},
            degrade_reason="turn_subagent_budget_exhausted",
        )

    def _record_child_thread_budget_degradation(
        self,
        *,
        thread_id: str,
        turn_id: str,
        request_id: str,
        agent: str | None,
        phase: str | None,
        intent: str,
        objective: str | None,
        execution_policy,
    ) -> None:
        payload: dict[str, Any] = {
            "intent": intent,
            "objective": objective,
        }
        max_child_threads = getattr(execution_policy, "max_child_threads_per_turn", None)
        if max_child_threads is not None:
            payload["max_child_threads_per_turn"] = max_child_threads
        item = self.spawn(
            thread_id=thread_id,
            turn_id=turn_id,
            request_id=request_id,
            agent=agent,
            phase=phase,
            title=f"child_thread_budget:{intent}",
            objective=f"degradation:child_thread:{intent}",
            payload=payload,
            item_type="degradation",
        )
        self.send(item, payload={"reason": "turn_child_thread_budget_exhausted"})
        self.wait(
            item,
            status="degraded",
            payload={"reason": "turn_child_thread_budget_exhausted", **payload},
            degrade_reason="turn_child_thread_budget_exhausted",
        )

    def _write_item(self, item: RuntimeItemRecord) -> None:
        items_path = Path(self.paths.items)
        items_path.parent.mkdir(parents=True, exist_ok=True)
        append_jsonl(items_path, item.model_dump(mode="json"))

    def _write_child_thread_state(self, thread: RuntimeThreadState) -> None:
        child_threads_root = Path(self.paths.root) / "threads"
        child_threads_root.mkdir(parents=True, exist_ok=True)
        write_json(child_threads_root / f"{thread.thread_id}.thread.json", thread.model_dump(mode="json"))

    def _write_child_turn_state(self, thread: RuntimeThreadState, turn: RuntimeTurnState) -> None:
        child_threads_root = Path(self.paths.root) / "threads"
        child_threads_root.mkdir(parents=True, exist_ok=True)
        write_json(
            child_threads_root / f"{thread.thread_id}.turn.{turn.turn_id}.json",
            turn.model_dump(mode="json"),
        )

    def _sync_child_execution_state(
        self,
        *,
        thread_id: str,
        turn_id: str,
        status: str | None,
    ) -> None:
        if not status:
            return
        child_threads_root = Path(self.paths.root) / "threads"
        thread_path = child_threads_root / f"{thread_id}.thread.json"
        turn_path = child_threads_root / f"{thread_id}.turn.{turn_id}.json"
        if not thread_path.exists() or not turn_path.exists():
            return

        now = _utc_now()
        thread_payload = read_json(thread_path) or {}
        turn_payload = read_json(turn_path) or {}
        if not thread_payload or not turn_payload:
            return

        if status == "running":
            thread_payload["status"] = "running"
            turn_payload["status"] = "running"
        elif status == "completed":
            thread_payload["status"] = "completed"
            turn_payload["status"] = "completed"
        elif status == "failed":
            thread_payload["status"] = "failed"
            turn_payload["status"] = "failed"
        else:
            return

        timestamp = now.isoformat()
        thread_payload["updated_at"] = timestamp
        turn_payload["updated_at"] = timestamp
        if status == "completed":
            thread_payload["active_turn_id"] = turn_id
            turn_payload["current_item_id"] = None
        write_json(thread_path, thread_payload)
        write_json(turn_path, turn_payload)

    def _run_subagent(self, *, subagent, invocation: SubagentInvocation) -> tuple[dict[str, Any], SubagentExecutionRecord]:
        outputs = subagent.run(**invocation.inputs)
        notes = []
        if "notes" in outputs and isinstance(outputs["notes"], list):
            notes = [str(item) for item in outputs["notes"]]
        record = SubagentExecutionRecord(
            subagent_type=invocation.subagent_type,
            objective=invocation.objective,
            inputs=invocation.inputs,
            output_keys=sorted(outputs.keys()),
            notes=notes,
        )
        return outputs, record

    def _build_invocation_id(self) -> str:
        from research_system.runtime.core.id_utils import generate_ulid

        return generate_ulid()

    def _notify(self, *, stage: str, item: RuntimeItemRecord, payload: dict[str, Any]) -> None:
        for observer in self._observers:
            try:
                observer(stage=stage, item=item, payload=payload)
            except Exception:
                continue

    def _notify_registry_failed(
        self,
        *,
        registry,
        invocation: SubagentInvocation,
        objective: str | None,
        inputs: dict[str, Any],
        error: Exception,
    ) -> None:
        notify_failed = getattr(registry, "notify_failed", None) if registry is not None else None
        if not callable(notify_failed):
            return
        record = SubagentExecutionRecord(
            subagent_type=invocation.subagent_type,
            status="failed",
            objective=objective,
            inputs=inputs,
            output_keys=[],
            notes=[f"{error.__class__.__name__}: {error}"],
        )
        notify_failed(invocation=invocation, record=record, error=error)


__all__ = ["AgentControl"]
