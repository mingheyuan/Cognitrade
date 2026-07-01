from __future__ import annotations

from pathlib import Path

from research_system.runtime.jsonl import read_json, write_json
from research_system.schemas.proposals import ProposalDraft


class ProposalStore:
    STATUSES = ("pending", "approved", "rejected")

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, status: str, proposal_id: str) -> Path:
        return self.root / status / f"{proposal_id}.json"

    def create(self, proposal: ProposalDraft) -> ProposalDraft:
        path = self._path(proposal.status, proposal.proposal_id)
        write_json(path, proposal.model_dump(mode="json"))
        return proposal

    def list(self, status: str) -> list[ProposalDraft]:
        root = self.root / status
        if not root.exists():
            return []
        proposals: list[ProposalDraft] = []
        for path in sorted(root.glob("*.json")):
            payload = read_json(path)
            if payload is None:
                continue
            proposals.append(ProposalDraft.model_validate(payload))
        return proposals

    def list_all(self) -> list[ProposalDraft]:
        proposals: list[ProposalDraft] = []
        for status in self.STATUSES:
            proposals.extend(self.list(status))
        proposals.sort(key=lambda item: item.updated_at, reverse=True)
        return proposals

    def load(self, status: str, proposal_id: str) -> ProposalDraft | None:
        payload = read_json(self._path(status, proposal_id))
        if payload is None:
            return None
        return ProposalDraft.model_validate(payload)

    def find(self, proposal_id: str) -> tuple[ProposalDraft, str] | None:
        for status in self.STATUSES:
            proposal = self.load(status, proposal_id)
            if proposal is not None:
                return proposal, status
        return None

    def save(self, proposal: ProposalDraft, *, previous_status: str | None = None) -> ProposalDraft:
        old_status = previous_status or proposal.status
        if old_status != proposal.status:
            old_path = self._path(old_status, proposal.proposal_id)
            if old_path.exists():
                old_path.unlink()
        path = self._path(proposal.status, proposal.proposal_id)
        write_json(path, proposal.model_dump(mode="json"))
        return proposal
