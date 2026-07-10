from __future__ import annotations

from dataclasses import dataclass

from research_system.adapters.vendors.http_json_source_registry import HttpJsonSourceRegistry
from research_system.adapters.vendors.mcp_source_registry import McpSourceRegistry
from research_system.adapters.vendors.openapi_source_registry import OpenApiSourceRegistry
from research_system.adapters.vendors.skill_source_registry import SkillSourceRegistry
from research_system.runtime.proposals.management.admin_support import (
    OrchestratorProposalAdminSupport,
)
from research_system.runtime.orchestration.subagent_discovery_admin_support import (
    OrchestratorSubagentDiscoveryAdminSupport,
)
from research_system.runtime.orchestration.orchestrator_qdrant_admin_support import (
    OrchestratorQdrantAdminSupport,
)
from research_system.runtime.orchestration.orchestrator_runtime_io_support import (
    OrchestratorRuntimeIoSupport,
)
from research_system.runtime.orchestration.orchestrator_run_admin_support import (
    OrchestratorRunAdminSupport,
)
from research_system.runtime.platform.settings import Settings
from research_system.runtime.storage.runs.retention import RunRetentionService
from research_system.runtime.storage.runs.run_store import RunStore


@dataclass
class OrchestratorAdminService:
    settings: Settings
    run_store: RunStore
    retention: RunRetentionService
    llm_provider: object | None = None
    qdrant: object | None = None
    qdrant_error: str | None = None
    skill_runtime: object | None = None
    proposal_store: object | None = None
    subagent_discovery_store: object | None = None
    research_support: object | None = None
    http_json_source_registry: object | None = None
    mcp_source_registry: object | None = None
    openapi_source_registry: object | None = None
    skill_source_registry: object | None = None
    orchestrator_error_cls: type[Exception] = Exception

    def __post_init__(self) -> None:
        self.runtime_io = OrchestratorRuntimeIoSupport(self)
        self.run_admin = OrchestratorRunAdminSupport(self)
        self.proposal_admin = OrchestratorProposalAdminSupport(self)
        self.subagent_discovery_admin = OrchestratorSubagentDiscoveryAdminSupport(self)
        self.qdrant_admin = OrchestratorQdrantAdminSupport(self)

    def replay(self, run_id: str) -> dict:
        return self.run_admin.replay(run_id)

    def trace(self, run_id: str) -> dict:
        return self.run_admin.trace(run_id)

    def events(self, run_id: str) -> dict:
        return self.run_admin.events(run_id)

    def metrics(self, run_id: str) -> dict:
        return self.run_admin.metrics(run_id)

    def thread(self, run_id: str, thread_id: str) -> dict:
        return self.run_admin.thread(run_id, thread_id)

    def thread_replay(self, run_id: str, thread_id: str) -> dict:
        return self.run_admin.thread_replay(run_id, thread_id)

    def thread_metrics(self, run_id: str, thread_id: str) -> dict:
        return self.run_admin.thread_metrics(run_id, thread_id)

    def subagent(self, run_id: str, invocation_id: str) -> dict:
        return self.run_admin.subagent(run_id, invocation_id)

    def steps(self, run_id: str) -> dict:
        return self.run_admin.steps(run_id)

    def history(self) -> list[dict]:
        return self.run_admin.history()

    def list_proposals(self, *, status: str | None = None, limit: int = 50) -> list[dict]:
        return self.proposal_admin.list_proposals(status=status, limit=limit)

    def show_proposal(self, proposal_id: str) -> dict:
        return self.proposal_admin.show_proposal(proposal_id)

    def list_subagent_discovery_requests(self, *, status: str | None = None, limit: int = 50) -> list[dict]:
        return self.subagent_discovery_admin.list_requests(status=status, limit=limit)

    def show_subagent_discovery_request(self, ticket_id: str) -> dict:
        return self.subagent_discovery_admin.show_request(ticket_id)

    def review_subagent_discovery_request(
        self,
        ticket_id: str,
        *,
        decision: str,
        reviewer: str = "manual_cli_review",
        reason: str | None = None,
        candidate_catalog_entries: list[dict] | None = None,
    ) -> dict:
        return self.subagent_discovery_admin.review_request(
            ticket_id,
            decision=decision,
            reviewer=reviewer,
            reason=reason,
            candidate_catalog_entries=candidate_catalog_entries,
        )

    def activate_subagent_discovery_request(
        self,
        ticket_id: str,
        *,
        delegate_subagent_type: str | None = None,
        candidate_subagent_type: str | None = None,
        registration_template_id: str | None = None,
        activated_by: str = "manual_cli_activation",
        reason: str | None = None,
        registration_type: str | None = None,
        skill_name: str | None = None,
        snapshot_type: str | None = None,
        source_id: str | None = None,
        tool_name: str | None = None,
        endpoint_path: str | None = None,
        http_method: str | None = None,
        openapi_spec_uri: str | None = None,
        openapi_operation_id: str | None = None,
        timeout_seconds: float | None = None,
    ) -> dict:
        return self.subagent_discovery_admin.activate_request(
            ticket_id,
            delegate_subagent_type=delegate_subagent_type,
            candidate_subagent_type=candidate_subagent_type,
            registration_template_id=registration_template_id,
            activated_by=activated_by,
            reason=reason,
            registration_type=registration_type,
            skill_name=skill_name,
            snapshot_type=snapshot_type,
            source_id=source_id,
            tool_name=tool_name,
            endpoint_path=endpoint_path,
            http_method=http_method,
            openapi_spec_uri=openapi_spec_uri,
            openapi_operation_id=openapi_operation_id,
            timeout_seconds=timeout_seconds,
        )

    def review_proposal(
        self,
        proposal_id: str,
        *,
        decision: str,
        reviewer: str = "manual_cli_review",
        reason: str | None = None,
    ) -> dict:
        return self.proposal_admin.review_proposal(
            proposal_id,
            decision=decision,
            reviewer=reviewer,
            reason=reason,
        )

    def resume(self, run_id: str) -> dict:
        return self.run_admin.resume(run_id)

    def cancel(self, run_id: str) -> dict:
        return self.run_admin.cancel(run_id)

    def cleanup(self) -> dict:
        return self.retention.cleanup_artifacts(self.settings.runs_root)

    def rebuild_qdrant(self) -> dict:
        return self.qdrant_admin.rebuild_qdrant()

    @staticmethod
    def _load_json_payload(path) -> dict | None:
        return OrchestratorRuntimeIoSupport.read_json_payload(path)

    def load_json_payload(self, path) -> dict | None:
        return self.runtime_io.load_json_payload(path)

    def require_proposal_store(self):
        if self.proposal_store is None:
            raise self.orchestrator_error_cls("proposal_store is not configured")
        return self.proposal_store

    def require_subagent_discovery_store(self):
        if self.subagent_discovery_store is None:
            raise self.orchestrator_error_cls("subagent_discovery_store is not configured")
        return self.subagent_discovery_store

    def require_research_support(self):
        if self.research_support is None:
            raise self.orchestrator_error_cls("research_support is not configured")
        return self.research_support

    def require_skill_runtime(self):
        if self.skill_runtime is None:
            raise self.orchestrator_error_cls("skill_runtime is not configured")
        return self.skill_runtime

    def require_mcp_source_registry(self):
        registry = self.mcp_source_registry
        if isinstance(registry, McpSourceRegistry):
            return registry
        registry = McpSourceRegistry(settings=self.settings)
        self.mcp_source_registry = registry
        return registry

    def require_http_json_source_registry(self):
        registry = self.http_json_source_registry
        if isinstance(registry, HttpJsonSourceRegistry):
            return registry
        registry = HttpJsonSourceRegistry(settings=self.settings)
        self.http_json_source_registry = registry
        return registry

    def require_openapi_source_registry(self):
        registry = self.openapi_source_registry
        if isinstance(registry, OpenApiSourceRegistry):
            return registry
        registry = OpenApiSourceRegistry(settings=self.settings)
        self.openapi_source_registry = registry
        return registry

    def require_skill_source_registry(self):
        registry = self.skill_source_registry
        if isinstance(registry, SkillSourceRegistry):
            return registry
        registry = SkillSourceRegistry(settings=self.settings)
        self.skill_source_registry = registry
        return registry
