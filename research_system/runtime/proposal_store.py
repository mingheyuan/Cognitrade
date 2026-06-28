from __future__ import annotations

from pathlib import Path

from research_system.runtime.jsonl import read_json, write_json
from research_system.schemas.proposals import ProposalDraft


class ProposalStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, status: str, proposal_id: str) -> Path:
        return self.root / status / f"{proposal_id}.json"

    def create(self, proposal: ProposalDraft) -> ProposalDraft:
        path = self._path(proposal.status, proposal.proposal_id)
        write_json(path, proposal.model_dump(mode="json"))
        return proposal

    def load(self, status: str, proposal_id: str) -> ProposalDraft | None:
        payload = read_json(self._path(status, proposal_id))
        if payload is None:
            return None
        return ProposalDraft.model_validate(payload)
