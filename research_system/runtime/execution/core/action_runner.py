from __future__ import annotations

from typing import Any

from research_system.runtime.execution.core.support import OrchestratorExecutionSupport
from research_system.runtime.execution.handlers.registry import OrchestratorAgentHandlers
from research_system.runtime.orchestration.orchestrator_context import RuntimeContext
from research_system.schemas.planning import ActionPlan, ActionSpec
from research_system.schemas.protocol import RequestEnvelope


class OrchestratorActionRunner:
    def __init__(self, host: Any, support: OrchestratorExecutionSupport | None = None) -> None:
        self.host = host
        self.support = support or OrchestratorExecutionSupport(host)
        self.handlers = OrchestratorAgentHandlers(host, self.support)

    def run_action(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        plan: ActionPlan,
        intent: str,
        action: ActionSpec,
        completed_node_ids: list[str],
        pending_actions_after_current: list[ActionSpec],
    ) -> None:
        phase = action.phase
        agent = action.agent
        self._bind_runtime_registry(
            context=context,
            request=request,
            agent=agent,
            phase=phase,
        )
        self.support.begin_action(context=context, request=request, intent=intent, action=action)

        try:
            if self.handlers.dispatch(
                agent=agent,
                context=context,
                request=request,
                plan=plan,
                intent=intent,
                action=action,
                phase=phase,
            ):
                self._complete_action(
                    context=context,
                    request=request,
                    intent=intent,
                    phase=phase,
                    agent=agent,
                    completed_node_ids=completed_node_ids,
                    pending_actions_after_current=pending_actions_after_current,
                )
                return
            raise self.host.orchestrator_error_cls(f"unsupported agent: {agent}")
        except Exception as exc:
            self.support.fail_action(
                context=context,
                request=request,
                intent=intent,
                phase=phase,
                agent=agent,
                error_summary=f"{exc.__class__.__name__}: {exc}",
                pending_actions_after_current=pending_actions_after_current,
            )
            raise
        finally:
            self._clear_runtime_registry(agent=agent)

    def _complete_action(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        intent: str,
        phase: str,
        agent: str,
        completed_node_ids: list[str],
        pending_actions_after_current: list[ActionSpec],
    ) -> None:
        self.support.complete_action(
            context=context,
            request=request,
            intent=intent,
            phase=phase,
            agent=agent,
            completed_node_ids=completed_node_ids,
            pending_actions_after_current=pending_actions_after_current,
        )

    def _bind_runtime_registry(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        agent: str,
        phase: str,
    ) -> None:
        service = getattr(self.host, agent, None)
        if service is None or not hasattr(service, "bind_runtime"):
            return
        service.bind_runtime(
            agent_control=context.agent_control,
            execution_policy=context.execution_policy,
            context=context,
            support=self.support,
            intent=context.state.intent,
            thread_id=context.thread.thread_id,
            turn_id=context.turn.turn_id,
            request_id=request.request_id,
            agent=agent,
            phase=phase,
        )

    def _clear_runtime_registry(self, *, agent: str) -> None:
        service = getattr(self.host, agent, None)
        if service is None or not hasattr(service, "clear_runtime"):
            return
        service.clear_runtime()
