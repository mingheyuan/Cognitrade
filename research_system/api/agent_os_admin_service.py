from __future__ import annotations

from typing import Any, Literal

from research_system.adapters.storage.qdrant_adapter import QdrantAdapter, QdrantUnavailableError
from research_system.runtime.agent_os import AgentOS
from research_system.runtime.core.approvals import HumanApprovalStore
from research_system.runtime.core.artifact_board import ArtifactBoard
from research_system.runtime.storage.runs.retention import RunRetentionService
from research_system.schemas.execution_runtime import ExecutionRunSnapshot


ApprovalStatus = Literal["approved", "revoked"]


class AgentOSAdminService:
    """Administrative resources backed by canonical Agent OS state only."""

    def __init__(self, agent_os: AgentOS) -> None:
        self.agent_os = agent_os
        self.retention = RunRetentionService(agent_os.settings)

    def list_domain_packs(self) -> list[dict[str, Any]]:
        return self.agent_os.list_domain_packs()

    def domain_pack(self, domain_id: str) -> dict[str, Any]:
        manifest = self.agent_os.registry.get(domain_id)
        return {
            "domain_id": manifest.domain_id,
            "version": manifest.version,
            "pack_path": str(self.agent_os.registry.pack_path(domain_id)),
            "manifest": manifest.model_dump(mode="json"),
        }

    def list_approvals(
        self,
        run_id: str,
        *,
        status: ApprovalStatus | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        store = self._approval_store(run_id)
        return [
            approval.model_dump(mode="json")
            for approval in store.list(status=status)[:limit]
        ]

    def approve(
        self,
        run_id: str,
        *,
        node_id: str,
        capability: str,
        approval_id: str,
        approved_by: str,
        reason: str,
    ) -> dict[str, Any]:
        snapshot = ExecutionRunSnapshot.model_validate(
            self.agent_os.inspect_run(run_id)["snapshot"]
        )
        node = next(
            (item for item in snapshot.graph.nodes if item.node_id == node_id),
            None,
        )
        if node is None:
            raise KeyError(f"execution graph node not found: {node_id}")
        if node.node_kind != "subagent":
            raise ValueError(f"approval target must be a subagent node: {node_id}")
        if node.capability != capability:
            raise ValueError(f"approval capability does not match node: {node_id}")
        if node.side_effect_level not in snapshot.graph.governance.high_risk_side_effect_levels:
            raise ValueError(f"node does not require human approval: {node_id}")
        if node.approval_id != approval_id:
            raise ValueError(f"approval_id does not match node: {node_id}")

        store = self._approval_store(run_id)
        existing = store.get(approval_id)
        if existing is not None:
            expected = (run_id, node_id, capability, approved_by, reason)
            actual = (
                existing.run_id,
                existing.node_id,
                existing.capability,
                existing.approved_by,
                existing.reason,
            )
            if existing.status == "approved" and actual == expected:
                return existing.model_dump(mode="json")
            raise ValueError(f"approval_id is already bound: {approval_id}")
        return store.approve(
            run_id=run_id,
            node_id=node_id,
            capability=capability,
            approved_by=approved_by,
            reason=reason,
            approval_id=approval_id,
        ).model_dump(mode="json")

    def list_memory(
        self,
        *,
        layer: Literal["episodic", "domain", "policy"] | None = None,
        domain_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        records = self.agent_os.memory.list(
            layer=layer,
            domain_id=domain_id,
            status=status,
        )
        ordered = sorted(records, key=lambda record: record.created_at, reverse=True)
        return [record.model_dump(mode="json") for record in ordered[:limit]]

    def cleanup_artifacts(self) -> dict[str, Any]:
        return self.retention.cleanup_artifacts(self.agent_os.settings.runs_root)

    def rebuild_qdrant(self) -> dict[str, Any]:
        try:
            qdrant = QdrantAdapter(self.agent_os.settings)
        except QdrantUnavailableError as exc:
            return {
                "status": "skipped",
                "message": "Qdrant unavailable",
                "qdrant_error": str(exc),
            }
        try:
            if not qdrant.available:
                return {
                    "status": "skipped",
                    "message": "Qdrant unavailable",
                    "qdrant_error": None,
                }
            qdrant.reset_collection()
            indexed_entries = 0
            skipped_entries = 0
            indexed_runs = 0
            skipped_runs: list[str] = []
            for run_dir in sorted(self.agent_os.settings.runs_root.iterdir()):
                if not run_dir.is_dir():
                    continue
                board_path = run_dir / "artifact_board.jsonl"
                if not board_path.exists():
                    skipped_runs.append(run_dir.name)
                    continue
                indexed_this_run = 0
                for artifact in ArtifactBoard(board_path).list_artifacts(thread_id=None):
                    if artifact.artifact_type == "user_request":
                        skipped_entries += 1
                        continue
                    qdrant.upsert_artifact(artifact)
                    indexed_entries += 1
                    indexed_this_run += 1
                if indexed_this_run:
                    indexed_runs += 1
                else:
                    skipped_runs.append(run_dir.name)
            return {
                "status": "completed",
                "message": "Qdrant rebuilt from Agent OS artifact boards",
                "collection_name": self.agent_os.settings.qdrant_collection_name,
                "indexed_runs": indexed_runs,
                "indexed_entries": indexed_entries,
                "skipped_entries": skipped_entries,
                "skipped_runs": skipped_runs,
            }
        finally:
            qdrant.close()

    def _approval_store(self, run_id: str) -> HumanApprovalStore:
        root = self.agent_os.run_store.load_run_paths(run_id).root
        return HumanApprovalStore(root / "approvals.jsonl")


__all__ = ["AgentOSAdminService", "ApprovalStatus"]
