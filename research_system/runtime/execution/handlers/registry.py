from __future__ import annotations

from typing import Any

from research_system.runtime.execution.handlers.dataflow.data import run_collector, run_retriever
from research_system.runtime.execution.handlers.distillation.distillation import run_critic, run_distiller
from research_system.runtime.execution.handlers.reporting import run_reporter
from research_system.runtime.execution.handlers.reviewing.review import run_reviewer, run_screen_reviewer
from research_system.runtime.execution.handlers.screening.screening import run_screener
from research_system.runtime.execution.core.support import OrchestratorExecutionSupport
from research_system.runtime.orchestration.orchestrator_context import RuntimeContext
from research_system.schemas.planning import ActionPlan, ActionSpec
from research_system.schemas.protocol import RequestEnvelope


class OrchestratorAgentHandlers:
    def __init__(self, host: Any, support: OrchestratorExecutionSupport | None = None) -> None:
        self.host = host
        self.support = support or OrchestratorExecutionSupport(host)
        self._handlers = {
            "collector": lambda **kwargs: run_collector(self.host, self.support, **kwargs),
            "retriever": lambda **kwargs: run_retriever(self.host, self.support, **kwargs),
            "screener": lambda **kwargs: run_screener(self.host, self.support, **kwargs),
            "distiller": lambda **kwargs: run_distiller(self.host, self.support, **kwargs),
            "critic": lambda **kwargs: run_critic(self.host, self.support, **kwargs),
            "reviewer": lambda **kwargs: run_reviewer(self.host, self.support, **kwargs),
            "screen_reviewer": lambda **kwargs: run_screen_reviewer(self.host, self.support, **kwargs),
            "reporter": lambda **kwargs: run_reporter(self.host, self.support, **kwargs),
        }

    def dispatch(
        self,
        *,
        agent: str,
        context: RuntimeContext,
        request: RequestEnvelope,
        plan: ActionPlan,
        intent: str,
        action: ActionSpec,
        phase: str,
    ) -> bool:
        handler = self._handlers.get(agent)
        if handler is None:
            return False
        handler(
            context=context,
            request=request,
            plan=plan,
            intent=intent,
            action=action,
            phase=phase,
        )
        return True
