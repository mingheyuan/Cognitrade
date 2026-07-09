from __future__ import annotations

from typing import Any

from research_system.runtime.execution.core.runtime_control_controller import (
    OrchestratorRuntimeControlController,
)
from research_system.runtime.execution.core.runtime_control_protocol_controller import (
    OrchestratorRuntimeControlProtocolController,
    RuntimeControlProtocolResolution,
)
from research_system.runtime.execution.core.runtime_subagent_control_controller import (
    OrchestratorRuntimeSubagentControlController,
)
from research_system.runtime.execution.core.runtime_subagent_control_protocol_controller import (
    OrchestratorRuntimeSubagentControlProtocolController,
)
from research_system.runtime.execution.core.runtime_subagent_transition_controller import (
    OrchestratorRuntimeSubagentTransitionController,
)
from research_system.schemas.planning import ActionPlan, ActionSpec


class OrchestratorRuntimeControlPlaneController:
    def __init__(
        self,
        host: Any,
        *,
        supervisor,
    ) -> None:
        self.host = host
        self.supervisor = supervisor
        self.transition_controller: OrchestratorRuntimeControlController | None = None
        self.protocol_controller: OrchestratorRuntimeControlProtocolController | None = None
        self.subagent_controller = OrchestratorRuntimeSubagentControlController(host)
        self.subagent_transition_controller = OrchestratorRuntimeSubagentTransitionController(host)
        self.subagent_protocol_controller = OrchestratorRuntimeSubagentControlProtocolController(
            host,
            subagent_controller=self.subagent_controller,
            transition_controller=self.subagent_transition_controller,
        )

    def register_execution_callbacks(
        self,
        *,
        apply_plan_patch,
        request_action_plan_replan,
    ) -> None:
        self.transition_controller = OrchestratorRuntimeControlController(
            self.host,
            apply_plan_patch=apply_plan_patch,
            request_action_plan_replan=request_action_plan_replan,
        )
        self.protocol_controller = OrchestratorRuntimeControlProtocolController(
            self.host,
            supervisor=self.supervisor,
            transition_controller=self.transition_controller,
        )

    def bind_runtime(
        self,
        *,
        context,
        support,
        request_id: str,
        intent: str,
    ) -> None:
        bind_runtime = getattr(self.supervisor, "bind_runtime", None)
        if callable(bind_runtime):
            bind_runtime(
                context=context,
                support=support,
                request_id=request_id,
                intent=intent,
            )

    def clear_runtime(self) -> None:
        clear_runtime = getattr(self.supervisor, "clear_runtime", None)
        if callable(clear_runtime):
            clear_runtime()

    def bind_agent_service_runtime(
        self,
        *,
        service,
        context,
        support,
        request_id: str,
        intent: str,
        agent: str,
        phase: str,
    ) -> None:
        bind_runtime = getattr(service, "bind_runtime", None)
        if not callable(bind_runtime):
            return
        bind_runtime(
            agent_control=context.agent_control,
            execution_policy=context.execution_policy,
            context=context,
            support=support,
            runtime_control=self,
            subagent_discovery_store=getattr(self.host, "subagent_discovery_store", None),
            skill_runtime=getattr(self.host, "skill_runtime", None),
            mcp_source_registry=getattr(self.host, "mcp_source_registry", None),
            http_json_source_registry=getattr(self.host, "http_json_source_registry", None),
            openapi_source_registry=getattr(self.host, "openapi_source_registry", None),
            skill_source_registry=getattr(self.host, "skill_source_registry", None),
            require_subagent_discovery_store=getattr(self.host, "require_subagent_discovery_store", None),
            require_skill_runtime=getattr(self.host, "require_skill_runtime", None),
            require_mcp_source_registry=getattr(self.host, "require_mcp_source_registry", None),
            require_http_json_source_registry=getattr(self.host, "require_http_json_source_registry", None),
            require_openapi_source_registry=getattr(self.host, "require_openapi_source_registry", None),
            require_skill_source_registry=getattr(self.host, "require_skill_source_registry", None),
            intent=intent,
            thread_id=context.thread.thread_id,
            turn_id=context.turn.turn_id,
            request_id=request_id,
            agent=agent,
            phase=phase,
        )

    @staticmethod
    def clear_agent_service_runtime(*, service) -> None:
        clear_runtime = getattr(service, "clear_runtime", None)
        if callable(clear_runtime):
            clear_runtime()

    def resolve_supervisor_cycle(
        self,
        *,
        context,
        request,
        plan: ActionPlan,
        ready_actions: list[ActionSpec],
        pending_actions: list[ActionSpec],
        completed_node_ids: list[str],
        skipped_node_ids: list[str],
    ) -> RuntimeControlProtocolResolution:
        if self.protocol_controller is None:
            raise RuntimeError("runtime control execution callbacks not registered")
        return self.protocol_controller.resolve_supervisor_cycle(
            context=context,
            request=request,
            plan=plan,
            ready_actions=ready_actions,
            pending_actions=pending_actions,
            completed_node_ids=completed_node_ids,
            skipped_node_ids=skipped_node_ids,
        )

    def handle_subagent_escalation(self, **kwargs) -> list[dict[str, Any]]:
        if self.protocol_controller is None:
            raise RuntimeError("runtime control execution callbacks not registered")
        return self.protocol_controller.handle_subagent_escalation(**kwargs)

    def resolve_subagent_capability_gap_escalation(self, **kwargs):
        return self.subagent_protocol_controller.resolve_capability_gap_escalation(**kwargs)

    def resolve_subagent_catalog_entry(self, **kwargs):
        return self.subagent_protocol_controller.resolve_catalog_entry(**kwargs)

    def select_subagent_catalog_entry(self, **kwargs):
        return self.subagent_protocol_controller.select_catalog_entry(**kwargs)

    def execute_subagent_phase_patch_cycle(self, **kwargs):
        return self.subagent_protocol_controller.execute_patch_cycle(**kwargs)


__all__ = ["OrchestratorRuntimeControlPlaneController"]
