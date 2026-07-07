from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from research_system.runtime.orchestration.orchestrator_context import RuntimeContext


@dataclass(frozen=True)
class RuntimeLLMExecutionDecision:
    can_use_llm: bool
    degrade_reason: str | None = None


class OrchestratorExecutionControlSupport:
    def __init__(self, host: Any, *, lifecycle_support) -> None:
        self.host = host
        self.lifecycle_support = lifecycle_support

    def enforce_runtime_controls(self, context: RuntimeContext, *, request_id: str, intent: str) -> None:
        if context.control_plane.cancellation_requested():
            self.lifecycle_support.transition(
                context,
                "cancelled",
                request_id=request_id,
                intent=intent,
                message="Run cancelled by user",
            )
            self.lifecycle_support.emit_event(
                context,
                request_id=request_id,
                intent=intent,
                event_type="run_cancelled",
                message="Run cancelled",
            )
            raise self.host.run_cancelled_error_cls("run_cancelled")
        if context.control_plane.run_timed_out():
            self.lifecycle_support.transition(
                context,
                "timed_out",
                request_id=request_id,
                intent=intent,
                message="Run timed out",
            )
            self.lifecycle_support.emit_event(
                context,
                request_id=request_id,
                intent=intent,
                event_type="run_timed_out",
                message="Run timed out",
            )
            raise self.host.run_timed_out_error_cls("run_timed_out")

    def wait_for_external_dependency(self, context: RuntimeContext, *, request_id: str, intent: str) -> None:
        import time

        while True:
            if context.control_plane.cancellation_requested():
                self.lifecycle_support.transition(
                    context,
                    "cancelled",
                    request_id=request_id,
                    intent=intent,
                    message="Run cancelled while waiting",
                )
                self.lifecycle_support.emit_event(
                    context,
                    request_id=request_id,
                    intent=intent,
                    event_type="run_cancelled",
                    message="Run cancelled while waiting",
                )
                raise self.host.run_cancelled_error_cls("run_cancelled")
            if context.control_plane.external_wait_timed_out() or context.control_plane.run_timed_out():
                self.lifecycle_support.transition(
                    context,
                    "timed_out",
                    request_id=request_id,
                    intent=intent,
                    message="External wait timed out",
                )
                self.lifecycle_support.emit_event(
                    context,
                    request_id=request_id,
                    intent=intent,
                    event_type="run_timed_out",
                    message="External wait timed out",
                )
                raise self.host.run_timed_out_error_cls("run_timed_out")
            time.sleep(0.2)

    def reserve_llm_slot(self, context: RuntimeContext) -> bool:
        return context.execution_policy.reserve_llm_call(turn_id=context.turn.turn_id)

    def resolve_llm_execution(
        self,
        context: RuntimeContext,
        *,
        request_id: str,
        agent: str,
        phase: str | None,
        strategy: str,
        provider=None,
        reserve: bool = True,
    ) -> RuntimeLLMExecutionDecision:
        if provider is not None and not bool(getattr(provider, "available", True)):
            reason = "llm_unavailable"
            component = f"{agent}_llm_provider"
        elif reserve and self.reserve_llm_slot(context):
            return RuntimeLLMExecutionDecision(can_use_llm=True)
        elif not reserve and context.execution_policy.can_use_llm(turn_id=context.turn.turn_id):
            return RuntimeLLMExecutionDecision(can_use_llm=True)
        else:
            reason = "turn_llm_budget_exhausted"
            component = f"{agent}_llm_budget"
        self.record_degradation(
            context,
            request_id=request_id,
            component=component,
            reason=reason,
            phase=phase,
            agent=agent,
            payload={"strategy": strategy},
        )
        return RuntimeLLMExecutionDecision(can_use_llm=False, degrade_reason=reason)

    def record_degradation(
        self,
        context: RuntimeContext,
        *,
        request_id: str,
        component: str,
        reason: str,
        phase: str | None,
        agent: str | None,
        payload: dict | None = None,
    ) -> None:
        item = context.agent_control.spawn(
            thread_id=context.thread.thread_id,
            turn_id=context.turn.turn_id,
            request_id=request_id,
            agent=agent,
            phase=phase,
            title=component,
            objective=f"degradation:{component}",
            payload=dict(payload or {}),
            item_type="degradation",
        )
        context.agent_control.send(item, payload={"reason": reason})
        context.agent_control.wait(
            item,
            status="degraded",
            payload={"reason": reason, **dict(payload or {})},
            degrade_reason=reason,
        )

    def record_payload_degradation(
        self,
        context: RuntimeContext,
        *,
        request_id: str,
        component: str,
        phase: str | None,
        agent: str | None,
        payload: dict | None,
        intent: str | None = None,
    ) -> bool:
        payload_dict = dict(payload or {})
        reason = str(
            payload_dict.get("degrade_reason")
            or payload_dict.get("degraded_note")
            or ""
        ).strip()
        if not reason:
            return False
        self.record_degradation(
            context,
            request_id=request_id,
            component=component,
            reason=reason,
            phase=phase,
            agent=agent,
            payload={"intent": intent} if intent else None,
        )
        return True
