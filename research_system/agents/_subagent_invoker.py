from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from pathlib import Path

from research_system.runtime.local_runtime import start_local_runtime
from research_system.runtime.storage.jsonl import write_json
from research_system.schemas.runtime import RuntimeItemRecord
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.subagents import SubagentExecutionRecord, SubagentInvocation
from research_system.subagents.registry import SubagentRegistry


class RuntimeBoundSubagentInvoker:
    registry: object
    _runtime_binding: dict[str, Any] | None = None
    _local_runtime_binding: dict[str, Any] | None = None
    _local_agent_item_stack: list[RuntimeItemRecord] | None = None

    def _invoke_subagent(self, invocation: SubagentInvocation) -> tuple[dict, SubagentExecutionRecord]:
        registry = getattr(self, "registry")
        local_binding = self._runtime_binding_for_invocation(invocation, prefer_local=True)
        local_agent_control = local_binding.get("agent_control")
        if local_agent_control is not None and local_binding and isinstance(registry, SubagentRegistry):
            return local_agent_control.run_bound(
                registry=registry,
                invocation=invocation,
                binding=local_binding,
            )
        binding = self._runtime_binding_for_invocation(invocation, prefer_local=False)
        agent_control = binding.get("agent_control")
        if agent_control is not None and binding and isinstance(registry, SubagentRegistry):
            return agent_control.run_bound(
                registry=registry,
                invocation=invocation,
                binding=binding,
            )
        if isinstance(registry, SubagentRegistry):
            local_runtime = self._ensure_local_runtime(invocation)
            try:
                outputs, record = local_runtime.context.agent_control.invoke(
                    registry=registry,
                    invocation=invocation,
                    thread_id=str(
                        (invocation.context or {}).get("child_thread_id")
                        or local_runtime.context.thread.thread_id
                    ),
                    turn_id=str(
                        (invocation.context or {}).get("child_turn_id")
                        or local_runtime.context.turn.turn_id
                    ),
                    request_id=local_runtime.request.request_id,
                    agent=getattr(self, "agent_name", None),
                    phase=self._local_runtime_phase(invocation),
                    execution_policy=local_runtime.context.execution_policy,
                )
                return outputs, record
            finally:
                self._release_local_runtime(local_runtime)
        raise AttributeError("runtime-bound subagent execution requires SubagentRegistry or AgentControl binding")

    def _invoke_subagents_concurrently(
        self,
        invocations: list[SubagentInvocation],
        *,
        max_workers: int | None = None,
    ) -> list[tuple[dict, SubagentExecutionRecord]]:
        if not invocations:
            return []
        registry = getattr(self, "registry")
        local_binding = dict(getattr(self, "_local_runtime_binding", {}) or {})
        local_agent_control = local_binding.get("agent_control")
        if local_agent_control is not None and local_binding and isinstance(registry, SubagentRegistry):
            return local_agent_control.run_many_bound(
                registry=registry,
                invocations=invocations,
                binding=local_binding,
                max_workers=max_workers,
            )
        binding = dict(getattr(self, "_runtime_binding", {}) or {})
        agent_control = binding.get("agent_control")
        if agent_control is not None and binding and isinstance(registry, SubagentRegistry):
            return agent_control.run_many_bound(
                registry=registry,
                invocations=invocations,
                binding=binding,
                max_workers=max_workers,
            )
        if isinstance(registry, SubagentRegistry):
            local_runtime = self._ensure_local_runtime(invocations[0])
            try:
                self._bind_local_runtime(local_runtime)
                return local_runtime.context.agent_control.run_many_bound(
                    registry=registry,
                    invocations=invocations,
                    binding=dict(getattr(self, "_local_runtime_binding", {}) or {}),
                    max_workers=max_workers,
                )
            finally:
                self._clear_local_runtime()
                self._release_local_runtime(local_runtime)
        raise AttributeError("runtime-bound concurrent subagent execution requires SubagentRegistry or AgentControl binding")

    def bind_runtime(
        self,
        *,
        agent_control,
        execution_policy=None,
        context=None,
        support=None,
        intent: str | None = None,
        thread_id: str,
        turn_id: str,
        request_id: str,
        agent: str | None,
        phase: str | None,
    ) -> None:
        self._runtime_binding = {
            "agent_control": agent_control,
            "execution_policy": execution_policy,
            "context": context,
            "support": support,
            "intent": intent,
            "thread_id": thread_id,
            "turn_id": turn_id,
            "request_id": request_id,
            "agent": agent,
            "phase": phase,
        }

    def clear_runtime(self) -> None:
        self._runtime_binding = None

    def spawn_child_thread(
        self,
        *,
        intent: str,
        objective: str | None,
        agent: str | None = None,
        phase: str | None = None,
    ):
        binding = dict(getattr(self, "_runtime_binding", {}) or getattr(self, "_local_runtime_binding", {}) or {})
        agent_control = binding.get("agent_control")
        if agent_control is None:
            return None, None
        thread_id = str(binding.get("thread_id") or "").strip()
        request_id = str(binding.get("request_id") or "").strip()
        turn_id = str(binding.get("turn_id") or "").strip()
        if not thread_id or not request_id:
            return None, None
        return agent_control.spawn_child_thread(
            parent_thread_id=thread_id,
            parent_turn_id=turn_id,
            request_id=request_id,
            intent=intent,
            objective=objective,
            agent=agent or binding.get("agent"),
            phase=phase or binding.get("phase"),
            execution_policy=binding.get("execution_policy"),
        )

    def _runtime_binding_for_invocation(
        self,
        invocation: SubagentInvocation,
        *,
        prefer_local: bool,
    ) -> dict[str, Any]:
        if prefer_local:
            binding = dict(getattr(self, "_local_runtime_binding", {}) or {})
        else:
            binding = dict(getattr(self, "_runtime_binding", {}) or {})
        if not binding:
            return binding
        context = dict(invocation.context or {})
        child_thread_id = str(context.get("child_thread_id") or "").strip()
        child_turn_id = str(context.get("child_turn_id") or "").strip()
        if child_thread_id and child_turn_id:
            binding["thread_id"] = child_thread_id
            binding["turn_id"] = child_turn_id
        child_phase = str(context.get("phase") or "").strip()
        if child_phase:
            binding["phase"] = child_phase
        return binding

    def _runtime_binding_for_events(self) -> dict[str, Any]:
        local_binding = dict(getattr(self, "_local_runtime_binding", {}) or {})
        if local_binding:
            return local_binding
        return dict(getattr(self, "_runtime_binding", {}) or {})

    def _runtime_execution_policy(self):
        binding = self._runtime_binding_for_events()
        return binding.get("execution_policy")

    def _has_runtime_degradation(self, reason: object) -> bool:
        policy = self._runtime_execution_policy()
        has_degradation = getattr(policy, "has_degradation", None)
        if not callable(has_degradation):
            return False
        return bool(has_degradation(reason))

    def _emit_runtime_event(
        self,
        *,
        event_type: str,
        message: str,
        payload: dict[str, Any] | None = None,
        phase: str | None = None,
        agent: str | None = None,
    ) -> None:
        binding = self._runtime_binding_for_events()
        context = binding.get("context")
        support = binding.get("support")
        request_id = str(binding.get("request_id") or "").strip()
        context_intent = getattr(getattr(context, "state", None), "intent", "") if context is not None else ""
        intent = str(binding.get("intent") or context_intent or "").strip()
        if context is None or support is None or not request_id or not intent:
            return
        resolved_payload = dict(payload or {})
        resolved_payload.setdefault("thread_id", binding.get("thread_id"))
        resolved_payload.setdefault("turn_id", binding.get("turn_id"))
        current_action_id = getattr(context, "current_action_id", None)
        current_node_id = getattr(context, "current_node_id", None)
        current_action_objective = getattr(context, "current_action_objective", None)
        if current_action_id is not None:
            resolved_payload.setdefault("action_id", current_action_id)
        if current_node_id is not None:
            resolved_payload.setdefault("node_id", current_node_id)
        if current_action_objective is not None:
            resolved_payload.setdefault("action_objective", current_action_objective)
        support.emit_event(
            context,
            request_id=request_id,
            intent=intent,
            event_type=event_type,
            phase=phase or binding.get("phase"),
            agent=agent or binding.get("agent") or getattr(self, "agent_name", None),
            message=message,
            payload=resolved_payload,
        )

    def _local_runtime_phase(self, invocation: SubagentInvocation) -> str:
        phase = str((invocation.context or {}).get("phase") or "").strip()
        if phase:
            return phase
        return str(getattr(self, "agent_name", None) or "subagent")

    def _local_run_timeout_seconds(self) -> int:
        settings = getattr(self, "settings", None)
        return int(getattr(settings, "run_timeout_seconds", 120) or 120)

    def _local_max_llm_calls_per_turn(self) -> int:
        settings = getattr(self, "settings", None)
        return int(getattr(settings, "max_llm_calls_per_turn", 1) or 1)

    def _local_max_subagent_calls_per_turn(self) -> int:
        settings = getattr(self, "settings", None)
        return int(getattr(settings, "max_subagent_calls_per_turn", 32) or 32)

    def _local_max_child_threads_per_turn(self) -> int:
        settings = getattr(self, "settings", None)
        return int(getattr(settings, "max_child_threads_per_turn", 8) or 8)

    def _local_runs_root(self) -> Path | None:
        runs_root = getattr(self, "runs_root", None)
        if runs_root is not None:
            return Path(runs_root)
        settings = getattr(self, "settings", None)
        settings_runs_root = getattr(settings, "runs_root", None)
        if settings_runs_root is not None:
            return Path(settings_runs_root)
        return None

    def _ensure_local_runtime(self, invocation: SubagentInvocation):
        runtime = getattr(self, "_local_runtime", None)
        if runtime is not None:
            depth = int(getattr(self, "_local_runtime_depth", 0) or 0)
            self._local_runtime_depth = depth + 1
            return runtime
        runtime = start_local_runtime(
            request=RequestEnvelope(
                intent=str(getattr(self, "agent_name", None) or "subagent"),
                user_query=str(invocation.objective or invocation.subagent_type),
                slots={"subagent_type": invocation.subagent_type},
                constraints={"subagent_context": dict(invocation.context or {})},
            ),
            intent=str(getattr(self, "agent_name", None) or "subagent"),
            phase=self._local_runtime_phase(invocation),
            run_timeout_seconds=self._local_run_timeout_seconds(),
            max_llm_calls_per_turn=self._local_max_llm_calls_per_turn(),
            max_subagent_calls_per_turn=self._local_max_subagent_calls_per_turn(),
            max_child_threads_per_turn=self._local_max_child_threads_per_turn(),
            runs_root=self._local_runs_root(),
        )
        self._local_runtime = runtime
        self._local_runtime_depth = 1
        return runtime

    def _release_local_runtime(self, runtime) -> None:
        current_runtime = getattr(self, "_local_runtime", None)
        if current_runtime is not runtime:
            close = getattr(runtime, "close", None)
            if callable(close):
                close()
            return
        depth = int(getattr(self, "_local_runtime_depth", 0) or 0)
        remaining = max(depth - 1, 0)
        self._local_runtime_depth = remaining
        if remaining > 0:
            return
        self._local_runtime = None
        close = getattr(runtime, "close", None)
        if callable(close):
            close()

    def _bind_local_runtime(self, runtime) -> None:
        self._local_runtime_binding = {
            "agent_control": runtime.context.agent_control,
            "execution_policy": runtime.context.execution_policy,
            "context": runtime.context,
            "support": runtime.support,
            "intent": runtime.intent,
            "thread_id": runtime.context.thread.thread_id,
            "turn_id": runtime.context.turn.turn_id,
            "request_id": runtime.request.request_id,
            "agent": getattr(self, "agent_name", None),
            "phase": getattr(runtime, "phase", None),
        }

    def _clear_local_runtime(self) -> None:
        self._local_runtime_binding = None

    @contextmanager
    def local_agent_scope(
        self,
        *,
        title: str,
        objective: str | None,
    ):
        binding = dict(getattr(self, "_local_runtime_binding", {}) or getattr(self, "_runtime_binding", {}) or {})
        agent_control = binding.get("agent_control")
        thread_id = str(binding.get("thread_id") or "").strip()
        turn_id = str(binding.get("turn_id") or "").strip()
        request_id = str(binding.get("request_id") or "").strip()
        phase = binding.get("phase")
        agent = binding.get("agent") or getattr(self, "agent_name", None)
        if agent_control is None or not thread_id or not turn_id or not request_id:
            yield
            return
        outermost_scope = not bool(getattr(self, "_local_agent_item_stack", None))
        if outermost_scope:
            self._set_local_runtime_execution_state(
                binding=binding,
                thread_status="running",
                turn_status="running",
                run_status="running",
                message=f"{title} running",
            )
        item = agent_control.spawn(
            thread_id=thread_id,
            turn_id=turn_id,
            request_id=request_id,
            agent=agent,
            phase=phase,
            title=title,
            objective=objective,
            payload={"scope": "service_run"},
            item_type="agent",
        )
        agent_control.send(item, payload={"scope": "service_run"})
        stack = list(getattr(self, "_local_agent_item_stack", []) or [])
        stack.append(item)
        self._local_agent_item_stack = stack
        try:
            yield
        except Exception as exc:
            agent_control.wait(
                item,
                status="failed",
                payload={"error": f"{exc.__class__.__name__}: {exc}", "scope": "service_run"},
            )
            if outermost_scope:
                self._set_local_runtime_execution_state(
                    binding=binding,
                    thread_status="failed",
                    turn_status="failed",
                    run_status="failed",
                    message=f"{title} failed",
                    error_summary=f"{exc.__class__.__name__}: {exc}",
                )
            raise
        else:
            agent_control.wait(
                item,
                status="completed",
                payload={"scope": "service_run"},
            )
            if outermost_scope:
                self._set_local_runtime_execution_state(
                    binding=binding,
                    thread_status="completed",
                    turn_status="completed",
                    run_status="completed",
                    message=f"{title} completed",
                )
        finally:
            stack = list(getattr(self, "_local_agent_item_stack", []) or [])
            if stack:
                stack.pop()
            self._local_agent_item_stack = stack or None

    @contextmanager
    def local_run_scope(
        self,
        *,
        intent: str | None,
        query: str,
        phase: str | None,
        title: str,
        objective: str | None,
    ):
        with self.local_runtime_scope(intent=intent, query=query, phase=phase):
            with self.local_agent_scope(title=title, objective=objective):
                yield

    @contextmanager
    def local_runtime_scope(
        self,
        *,
        intent: str | None,
        query: str,
        phase: str | None = None,
    ):
        if getattr(self, "_runtime_binding", None) or getattr(self, "_local_runtime_binding", None):
            yield
            return
        runtime = start_local_runtime(
            request=RequestEnvelope(
                intent=intent or str(getattr(self, "agent_name", None) or "subagent"),
                user_query=query,
            ),
            intent=intent or str(getattr(self, "agent_name", None) or "subagent"),
            phase=phase or str(getattr(self, "agent_name", None) or "subagent"),
            run_timeout_seconds=self._local_run_timeout_seconds(),
            max_llm_calls_per_turn=self._local_max_llm_calls_per_turn(),
            max_subagent_calls_per_turn=self._local_max_subagent_calls_per_turn(),
            max_child_threads_per_turn=self._local_max_child_threads_per_turn(),
            runs_root=self._local_runs_root(),
        )
        self._bind_local_runtime(runtime)
        try:
            yield
        finally:
            self._clear_local_runtime()
            runtime.close()

    def _set_local_runtime_execution_state(
        self,
        *,
        binding: dict[str, Any],
        thread_status: str,
        turn_status: str,
        run_status: str,
        message: str,
        error_summary: str | None = None,
    ) -> None:
        context = binding.get("context")
        support = binding.get("support")
        request_id = str(binding.get("request_id") or "").strip()
        context_intent = getattr(getattr(context, "state", None), "intent", "") if context is not None else ""
        intent = str(binding.get("intent") or context_intent or "").strip()
        phase = binding.get("phase")
        agent = binding.get("agent") or getattr(self, "agent_name", None)
        if context is None or support is None or not request_id or not intent:
            return
        context.thread.status = thread_status  # type: ignore[assignment]
        context.turn.status = turn_status  # type: ignore[assignment]
        context.state.thread_id = context.thread.thread_id
        context.state.active_turn_id = context.turn.turn_id
        support.update_state(
            context,
            current_phase=phase,
            active_agent=agent,
        )
        support.transition(
            context,
            run_status,
            request_id=request_id,
            intent=intent,
            message=message,
            error_summary=error_summary,
        )
        write_json(context.paths.thread_state, context.thread.model_dump(mode="json"))
        write_json(context.paths.turn_state, context.turn.model_dump(mode="json"))
