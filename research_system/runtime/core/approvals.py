from __future__ import annotations

from pathlib import Path

from research_system.runtime.storage.jsonl import append_jsonl, read_jsonl
from research_system.schemas.governance import HumanApproval


class HumanApprovalStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def approve(
        self,
        *,
        run_id: str,
        node_id: str,
        capability: str,
        approved_by: str,
        reason: str,
        approval_id: str | None = None,
    ) -> HumanApproval:
        payload = {
            "run_id": run_id,
            "node_id": node_id,
            "capability": capability,
            "approved_by": approved_by,
            "reason": reason,
        }
        if approval_id:
            payload["approval_id"] = approval_id
        approval = HumanApproval.model_validate(payload)
        append_jsonl(self.path, approval.model_dump(mode="json"))
        return approval

    def revoke(self, approval_id: str, *, revoked_by: str, reason: str) -> HumanApproval:
        approval = self.get(approval_id)
        if approval is None:
            raise KeyError(f"unknown approval: {approval_id}")
        revoked = approval.model_copy(
            update={
                "status": "revoked",
                "approved_by": revoked_by,
                "reason": reason,
            }
        )
        append_jsonl(self.path, revoked.model_dump(mode="json"))
        return revoked

    def get(self, approval_id: str) -> HumanApproval | None:
        for payload in reversed(read_jsonl(self.path)):
            if payload.get("approval_id") == approval_id:
                return HumanApproval.model_validate(payload)
        return None

    def validate(
        self,
        *,
        approval_id: str,
        run_id: str,
        node_id: str,
        capability: str,
    ) -> bool:
        approval = self.get(approval_id)
        return bool(
            approval is not None
            and approval.status == "approved"
            and approval.run_id == run_id
            and approval.node_id == node_id
            and approval.capability == capability
        )


__all__ = ["HumanApprovalStore"]
