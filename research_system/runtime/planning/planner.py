from __future__ import annotations

from research_system.runtime.domain_packs.registry import DomainPackRegistry
from research_system.runtime.planning.capability_planner import CapabilityPlanner
from research_system.runtime.platform.settings import Settings
from research_system.schemas.domain_pack import CapabilityPlanningRequest
from research_system.schemas.execution_graph import ExecutionGraph
from research_system.schemas.protocol import RequestEnvelope


class RuntimePlanner:
    """Domain-neutral entry planner backed only by installed capability contracts."""

    def __init__(self, llm_provider=None, settings: Settings | None = None) -> None:
        self.llm_provider = llm_provider
        self.settings = settings
        self.domain_pack_registry = (
            DomainPackRegistry.load(settings.domain_packs_root)
            if settings is not None
            else None
        )
        self.capability_planner = (
            CapabilityPlanner(self.domain_pack_registry)
            if self.domain_pack_registry is not None
            else None
        )

    def build_initial_execution_graph(
        self,
        *,
        intent: str,
        request: RequestEnvelope,
    ) -> ExecutionGraph:
        domain_id = self._domain_id(intent=intent, request=request)
        if domain_id is None:
            raise ValueError(
                f"no DomainPack capability graph is configured for intent: {intent}"
            )
        if self.capability_planner is None or self.domain_pack_registry is None:
            raise ValueError("Agent OS domain-pack planner is not configured")
        manifest = self.domain_pack_registry.get(domain_id)
        required = list(request.slots.get("required_artifact_types") or [])
        if not required:
            required = [contract.artifact_type for contract in manifest.report_contracts]
        graph = self.capability_planner.build_graph(
            CapabilityPlanningRequest(
                domain_id=domain_id,
                objective=request.user_query,
                available_artifact_types=list(
                    request.slots.get("available_artifact_types") or ["user_request"]
                ),
                required_artifact_types=required,
                allowed_tools=list(manifest.governance.allowed_tools),
                allowed_permissions=list(manifest.governance.allowed_permissions),
                preferred_capabilities=list(
                    request.slots.get("preferred_capabilities") or []
                ),
                token_budget=manifest.governance.token_budget,
                cost_budget=manifest.governance.cost_budget,
                metadata={
                    "entry_protocol": "agent_os",
                    "intent": intent,
                    "intent_inputs": {
                        **dict(request.slots),
                        **dict(request.constraints),
                    },
                    "request_constraints": dict(request.constraints),
                    "conversation_context": list(request.conversation_context),
                    "chat_dispatch": dict(request.constraints.get("chat_dispatch") or {}),
                },
            )
        )
        if graph.governance.timeout_seconds is None and self.settings is not None:
            graph.governance.timeout_seconds = float(self.settings.run_timeout_seconds)
        return graph

    def _domain_id(self, *, intent: str, request: RequestEnvelope) -> str | None:
        configured = str(request.slots.get("domain_id") or "").strip()
        if configured:
            return configured
        if intent.startswith("domain:"):
            return intent.split(":", 1)[1].strip() or None
        if self.domain_pack_registry is None:
            return None
        matches = [
            manifest.domain_id
            for manifest in self.domain_pack_registry.list()
            if intent in set(manifest.planner_policies.get("supported_intents") or [])
        ]
        if len(matches) > 1:
            raise ValueError(
                f"multiple DomainPacks claim intent {intent}: {', '.join(sorted(matches))}"
            )
        return matches[0] if matches else None


__all__ = ["RuntimePlanner"]
