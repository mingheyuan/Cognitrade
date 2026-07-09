from __future__ import annotations


class OrchestratorRuntimeServiceBindingController:
    def __init__(
        self,
        host,
        *,
        subagent_runtime_control,
    ) -> None:
        self.host = host
        self.subagent_runtime_control = subagent_runtime_control

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
            runtime_control=self.subagent_runtime_control,
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


__all__ = ["OrchestratorRuntimeServiceBindingController"]
