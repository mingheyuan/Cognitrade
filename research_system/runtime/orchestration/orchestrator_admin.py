from __future__ import annotations

from dataclasses import dataclass

from research_system.runtime.proposals.management.admin_support import (
    OrchestratorProposalAdminSupport,
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
    retriever: object
    proposal_store: object | None = None
    research_support: object | None = None
    orchestrator_error_cls: type[Exception] = Exception

    def __post_init__(self) -> None:
        self.runtime_io = OrchestratorRuntimeIoSupport(self)
        self.run_admin = OrchestratorRunAdminSupport(self)
        self.proposal_admin = OrchestratorProposalAdminSupport(self)
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

    def require_research_support(self):
        if self.research_support is None:
            raise self.orchestrator_error_cls("research_support is not configured")
        return self.research_support
