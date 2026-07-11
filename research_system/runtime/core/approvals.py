from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from research_system.runtime.storage.jsonl import append_jsonl, read_jsonl
from research_system.schemas.governance import HumanApproval


def approval_policy_fingerprint(graph: Any, node: Any) -> str:
    """Return the stable policy identity shared by Runner and Admin."""
    payload = {
        "domain_id": graph.domain_id,
        "domain_version": graph.metadata.get("domain_version"),
        "governance": graph.governance.model_dump(mode="json"),
        "node": {
            "node_id": node.node_id,
            "capability": node.capability,
            "tools": node.tools,
            "permissions": node.permissions,
            "side_effect_level": node.side_effect_level,
        },
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class HumanApprovalStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def request(
        self,
        *,
        run_id: str,
        node_id: str,
        capability: str,
        reason: str,
        approval_id: str | None = None,
        expires_at: datetime | None = None,
        graph_revision: int | None = None,
        domain_id: str | None = None,
        domain_version: str | None = None,
        user_id: str | None = None,
        tenant_id: str | None = None,
        policy_fingerprint: str | None = None,
    ) -> HumanApproval:
        payload = {
            "run_id": run_id,
            "node_id": node_id,
            "capability": capability,
            "reason": reason,
            "status": "pending",
            "expires_at": expires_at,
            "graph_revision": graph_revision,
            "domain_id": domain_id,
            "domain_version": domain_version,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "policy_fingerprint": policy_fingerprint,
        }
        if approval_id:
            payload["approval_id"] = approval_id
        approval = HumanApproval.model_validate(payload)
        append_jsonl(self.path, approval.model_dump(mode="json"))
        return approval

    def approve(
        self,
        *,
        run_id: str,
        node_id: str,
        capability: str,
        approved_by: str,
        reason: str,
        approval_id: str | None = None,
        graph_revision: int | None = None,
        domain_id: str | None = None,
        domain_version: str | None = None,
        user_id: str | None = None,
        tenant_id: str | None = None,
        policy_fingerprint: str | None = None,
    ) -> HumanApproval:
        existing = self.get(approval_id) if approval_id else None
        if existing is not None:
            if (existing.run_id, existing.node_id, existing.capability) != (
                run_id,
                node_id,
                capability,
            ):
                raise ValueError("approval identity does not match the requested node")
            graph_revision = graph_revision if graph_revision is not None else existing.graph_revision
            domain_id = domain_id if domain_id is not None else existing.domain_id
            domain_version = domain_version if domain_version is not None else existing.domain_version
            user_id = user_id if user_id is not None else existing.user_id
            tenant_id = tenant_id if tenant_id is not None else existing.tenant_id
            policy_fingerprint = (
                policy_fingerprint
                if policy_fingerprint is not None
                else existing.policy_fingerprint
            )
            if not self._binding_matches(
                existing,
                graph_revision=graph_revision,
                domain_id=domain_id,
                domain_version=domain_version,
                user_id=user_id,
                tenant_id=tenant_id,
                policy_fingerprint=policy_fingerprint,
            ):
                raise ValueError("approval binding does not match the governed execution context")
            if existing.status == "approved" and (
                existing.approved_by == approved_by and existing.reason == reason
            ):
                return existing
            if existing.status not in {"pending", "approved"}:
                raise ValueError(f"approval is not approvable: {existing.status}")
            approval = existing.model_copy(
                update={
                    "status": "approved",
                    "approved_by": approved_by,
                    "reason": reason,
                "decided_at": datetime.now(timezone.utc),
                    "graph_revision": graph_revision,
                    "domain_id": domain_id,
                    "domain_version": domain_version,
                    "user_id": user_id,
                    "tenant_id": tenant_id,
                    "policy_fingerprint": policy_fingerprint,
                }
            )
        else:
            payload = {
                "run_id": run_id,
                "node_id": node_id,
                "capability": capability,
                "approved_by": approved_by,
                "reason": reason,
                "status": "approved",
                "decided_at": datetime.now(timezone.utc),
                "graph_revision": graph_revision,
                "domain_id": domain_id,
                "domain_version": domain_version,
                "user_id": user_id,
                "tenant_id": tenant_id,
                "policy_fingerprint": policy_fingerprint,
            }
            if approval_id:
                payload["approval_id"] = approval_id
            approval = HumanApproval.model_validate(payload)
        append_jsonl(self.path, approval.model_dump(mode="json"))
        return approval

    def reject(
        self,
        approval_id: str,
        *,
        rejected_by: str,
        reason: str,
    ) -> HumanApproval:
        approval = self.get(approval_id)
        if approval is None:
            raise KeyError(f"unknown approval: {approval_id}")
        if approval.status not in {"pending", "approved"}:
            raise ValueError(f"approval is not rejectable: {approval.status}")
        rejected = approval.model_copy(
            update={
                "status": "rejected",
                "approved_by": rejected_by,
                    "reason": reason,
                    "decided_at": datetime.now(timezone.utc),
            }
        )
        append_jsonl(self.path, rejected.model_dump(mode="json"))
        return rejected

    def revoke(self, approval_id: str, *, revoked_by: str, reason: str) -> HumanApproval:
        approval = self.get(approval_id)
        if approval is None:
            raise KeyError(f"unknown approval: {approval_id}")
        revoked = approval.model_copy(
            update={
                "status": "revoked",
                "approved_by": revoked_by,
                "reason": reason,
                "decided_at": datetime.now(timezone.utc),
            }
        )
        append_jsonl(self.path, revoked.model_dump(mode="json"))
        return revoked

    def bind_legacy(
        self,
        approval_id: str,
        *,
        graph_revision: int,
        domain_id: str,
        domain_version: str | None,
        user_id: str | None,
        tenant_id: str | None,
        policy_fingerprint: str,
    ) -> HumanApproval:
        """Attach canonical context to a pre-existing approved record once.

        This is only for records created before context binding existed. It is
        allowed at the initial graph revision and preserves the original human
        decision; later graph revisions require a fresh approval.
        """
        approval = self.get(approval_id)
        if approval is None:
            raise KeyError(f"unknown approval: {approval_id}")
        if approval.status != "approved":
            raise ValueError("only an approved legacy record can be bound")
        if any(
            value is not None
            for value in (
                approval.graph_revision,
                approval.domain_id,
                approval.domain_version,
                approval.user_id,
                approval.tenant_id,
                approval.policy_fingerprint,
            )
        ):
            raise ValueError("approval already has a canonical binding")
        bound = approval.model_copy(
            update={
                "graph_revision": graph_revision,
                "domain_id": domain_id,
                "domain_version": domain_version,
                "user_id": user_id,
                "tenant_id": tenant_id,
                "policy_fingerprint": policy_fingerprint,
            }
        )
        append_jsonl(self.path, bound.model_dump(mode="json"))
        return bound

    def get(self, approval_id: str) -> HumanApproval | None:
        for payload in reversed(read_jsonl(self.path)):
            if payload.get("approval_id") == approval_id:
                approval = HumanApproval.model_validate(payload)
                if (
                    approval.status in {"pending", "approved"}
                    and approval.expires_at is not None
                    and approval.expires_at <= datetime.now(timezone.utc)
                ):
                    expired = approval.model_copy(
                        update={
                            "status": "expired",
                            "decided_at": datetime.now(timezone.utc),
                        }
                    )
                    append_jsonl(self.path, expired.model_dump(mode="json"))
                    return expired
                return approval
        return None

    def list(self, *, status: str | None = None) -> list[HumanApproval]:
        latest_by_id: dict[str, HumanApproval] = {}
        for payload in read_jsonl(self.path):
            approval = HumanApproval.model_validate(payload)
            latest_by_id[approval.approval_id] = approval
        rows = list(latest_by_id.values())
        if status is not None:
            rows = [row for row in rows if row.status == status]
        return sorted(rows, key=lambda row: row.created_at, reverse=True)

    def validate(
        self,
        *,
        approval_id: str,
        run_id: str,
        node_id: str,
        capability: str,
        graph_revision: int | None = None,
        domain_id: str | None = None,
        domain_version: str | None = None,
        user_id: str | None = None,
        tenant_id: str | None = None,
        policy_fingerprint: str | None = None,
        require_binding: bool = False,
    ) -> bool:
        approval = self.get(approval_id)
        if approval is not None and approval.expires_at is not None:
            if approval.expires_at <= datetime.now(timezone.utc):
                return False
        return bool(
            approval is not None
            and approval.status == "approved"
            and approval.run_id == run_id
            and approval.node_id == node_id
            and approval.capability == capability
            and self._binding_matches(
                approval,
                graph_revision=graph_revision,
                domain_id=domain_id,
                domain_version=domain_version,
                user_id=user_id,
                tenant_id=tenant_id,
                policy_fingerprint=policy_fingerprint,
                require_binding=require_binding,
            )
        )

    @staticmethod
    def _binding_matches(
        approval: HumanApproval,
        *,
        graph_revision: int | None,
        domain_id: str | None,
        domain_version: str | None,
        user_id: str | None,
        tenant_id: str | None,
        policy_fingerprint: str | None,
        require_binding: bool = False,
    ) -> bool:
        expected = {
            "graph_revision": graph_revision,
            "domain_id": domain_id,
            "domain_version": domain_version,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "policy_fingerprint": policy_fingerprint,
        }
        bound = {
            field: getattr(approval, field)
            for field in expected
        }
        if require_binding and any(value is not None for value in expected.values()):
            if any(
                expected[field] is not None and bound[field] is None
                for field in expected
            ):
                return False
        return all(
            expected_value is None or bound[field] == expected_value
            for field, expected_value in expected.items()
        )


__all__ = ["HumanApprovalStore", "approval_policy_fingerprint"]
