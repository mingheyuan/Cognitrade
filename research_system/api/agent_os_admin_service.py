from __future__ import annotations

from typing import Any, Literal

from research_system.adapters.storage.qdrant_adapter import QdrantAdapter, QdrantUnavailableError
from research_system.runtime.agent_os import AgentOS
from research_system.runtime.core.approvals import (
    HumanApprovalStore,
    approval_policy_fingerprint,
)
from research_system.runtime.core.artifact_board import ArtifactBoard
from research_system.runtime.storage.runs.retention import RunRetentionService
from research_system.runtime.storage.jsonl import read_json
from research_system.schemas.execution_runtime import ExecutionRunSnapshot
from research_system.schemas.memory import MemoryEvaluation


ApprovalStatus = Literal["pending", "approved", "rejected", "revoked", "expired"]


class AgentOSAdminService:
    """Administrative resources backed by canonical Agent OS state only."""

    identity_scoped = True

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
            "tools": [
                tool.model_dump(mode="json")
                for tool in self.agent_os.registry.tool_definitions(domain_id=domain_id)
            ],
        }

    def list_approvals(
        self,
        run_id: str,
        *,
        status: ApprovalStatus | None = None,
        limit: int = 50,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        self._assert_run_scope(run_id, user_id=user_id, tenant_id=tenant_id)
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
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        self._assert_run_scope(run_id, user_id=user_id, tenant_id=tenant_id)
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
        tool_requires_approval = any(
            self._tool_requires_approval(tool_name)
            for tool_name in node.tools
        )
        if (
            node.side_effect_level not in snapshot.graph.governance.high_risk_side_effect_levels
            and not tool_requires_approval
        ):
            raise ValueError(f"node does not require human approval: {node_id}")
        if node.approval_id != approval_id:
            raise ValueError(f"approval_id does not match node: {node_id}")

        store = self._approval_store(run_id)
        return store.approve(
            run_id=run_id,
            node_id=node_id,
            capability=capability,
            approved_by=approved_by,
            reason=reason,
            approval_id=approval_id,
            graph_revision=snapshot.graph.revision,
            domain_id=snapshot.graph.domain_id,
            domain_version=str(snapshot.graph.metadata.get("domain_version") or "") or None,
            user_id=user_id,
            tenant_id=tenant_id,
            policy_fingerprint=approval_policy_fingerprint(snapshot.graph, node),
        ).model_dump(mode="json")

    def reject(
        self,
        run_id: str,
        *,
        approval_id: str,
        rejected_by: str,
        reason: str,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        self._assert_run_scope(run_id, user_id=user_id, tenant_id=tenant_id)
        snapshot = ExecutionRunSnapshot.model_validate(
            self.agent_os.inspect_run(run_id)["snapshot"]
        )
        store = self._approval_store(run_id)
        approval = store.get(approval_id)
        if approval is None or approval.run_id != run_id:
            raise KeyError(f"approval not found: {approval_id}")
        node = next(
            (item for item in snapshot.graph.nodes if item.node_id == approval.node_id),
            None,
        )
        if node is None or node.capability != approval.capability:
            raise ValueError("approval is no longer bound to the execution graph")
        store_binding_valid = store.validate(
            approval_id=approval_id,
            run_id=run_id,
            node_id=node.node_id,
            capability=str(node.capability),
            graph_revision=snapshot.graph.revision,
            domain_id=snapshot.graph.domain_id,
            domain_version=str(snapshot.graph.metadata.get("domain_version") or "") or None,
            user_id=user_id,
            tenant_id=tenant_id,
            policy_fingerprint=approval_policy_fingerprint(snapshot.graph, node),
            require_binding=True,
        )
        if not store_binding_valid and approval.status == "approved":
            raise ValueError("approval is no longer valid for the current execution context")
        return store.reject(
            approval_id,
            rejected_by=rejected_by,
            reason=reason,
        ).model_dump(mode="json")

    def list_memory(
        self,
        *,
        layer: Literal["episodic", "domain", "policy"] | None = None,
        domain_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        scoped = user_id is not None or tenant_id is not None
        records = self.agent_os.memory.list(
            layer=layer,
            domain_id=domain_id,
            status=status,
            user_id=user_id,
            tenant_id=tenant_id,
            include_scoped=not scoped,
        )
        ordered = sorted(records, key=lambda record: record.created_at, reverse=True)
        return [record.model_dump(mode="json") for record in ordered[:limit]]

    def promote_memory(
        self,
        memory_id: str,
        *,
        target_layer: Literal["domain", "policy"],
        evaluation_id: str,
        evaluator: str,
        verdict: Literal["pass", "fail", "needs_review"],
        score: float | None,
        evidence_refs: list[str],
        notes: list[str],
        content: dict[str, Any] | None = None,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        record = self.agent_os.memory.get(memory_id)
        if record is None or not self._memory_matches_scope(
            record,
            user_id=user_id,
            tenant_id=tenant_id,
        ):
            raise KeyError(f"memory record not found: {memory_id}")
        evaluation = MemoryEvaluation(
            evaluation_id=evaluation_id,
            evaluator=evaluator,
            verdict=verdict,
            score=score,
            evidence_refs=list(evidence_refs),
            notes=list(notes),
        )
        return self.agent_os.memory.promote(
            memory_id=memory_id,
            target_layer=target_layer,
            evaluation=evaluation,
            content=content,
        ).model_dump(mode="json")

    def revoke_memory(
        self,
        memory_id: str,
        *,
        revoked_by: str,
        reason: str,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        record = self.agent_os.memory.get(memory_id)
        if record is None or not self._memory_matches_scope(
            record,
            user_id=user_id,
            tenant_id=tenant_id,
        ):
            raise KeyError(f"memory record not found: {memory_id}")
        return self.agent_os.memory.revoke(
            memory_id=memory_id,
            revoked_by=revoked_by,
            reason=reason,
        ).model_dump(mode="json")

    def expire_memory(self) -> dict[str, Any]:
        expired = self.agent_os.memory.expire_records()
        return {
            "status": "completed",
            "expired_count": len(expired),
            "memory_ids": [record.memory_id for record in expired],
        }

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

    @staticmethod
    def _memory_matches_scope(record, *, user_id: str | None, tenant_id: str | None) -> bool:
        if user_id is not None and record.user_id not in {None, user_id}:
            return False
        if tenant_id is not None and record.tenant_id not in {None, tenant_id}:
            return False
        return True

    def _assert_run_scope(
        self,
        run_id: str,
        *,
        user_id: str | None,
        tenant_id: str | None,
    ) -> None:
        if user_id is None and tenant_id is None:
            return
        try:
            root = self.agent_os.run_store.load_run_paths(run_id).root
        except FileNotFoundError:
            raise
        request = read_json(root / "request.json")
        if not isinstance(request, dict):
            if user_id == "api_token" and tenant_id == "api_token":
                return
            raise FileNotFoundError(f"run not found: {run_id}")
        if (
            request.get("user_id") in (None, "")
            and request.get("tenant_id") in (None, "")
            and user_id == "api_token"
            and tenant_id == "api_token"
        ):
            return
        if user_id is not None and request.get("user_id") != user_id:
            raise FileNotFoundError(f"run not found: {run_id}")
        if tenant_id is not None and request.get("tenant_id") != tenant_id:
            raise FileNotFoundError(f"run not found: {run_id}")

    def _tool_requires_approval(self, tool_name: str) -> bool:
        try:
            return self.agent_os.tool_registry.definition(tool_name).requires_approval
        except Exception:
            return False


__all__ = ["AgentOSAdminService", "ApprovalStatus"]
