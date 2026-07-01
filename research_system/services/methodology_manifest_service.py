from __future__ import annotations

from datetime import datetime, timezone

import yaml

from research_system.runtime.jsonl import read_json, write_json
from research_system.runtime.settings import Settings
from research_system.schemas.proposals import ProposalDraft


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MethodologyManifestService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def load(self) -> dict:
        path = self.settings.methodology_manifest_path
        if not path.exists():
            return {
                "manifest_id": "methodology_manifest_v1",
                "version": 1,
                "active_methodology": "baseline",
                "approved_proposal_ids": [],
                "generated_at": utc_now_iso(),
            }
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}

    def approve(self, proposal: ProposalDraft) -> dict:
        manifest = self.load()
        approved_ids = list(manifest.get("approved_proposal_ids") or [])
        if proposal.proposal_id not in approved_ids:
            approved_ids.append(proposal.proposal_id)
        manifest["approved_proposal_ids"] = approved_ids
        manifest["version"] = int(manifest.get("version") or 1) + 1
        manifest["generated_at"] = utc_now_iso()
        if proposal.proposal_type == "MethodologyTemplatePatch":
            manifest["active_methodology"] = "evolving_dimensions"
        elif proposal.proposal_type == "ValuationHeuristicPatch":
            manifest["active_methodology"] = "valuation_augmented"
        self.settings.methodology_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with self.settings.methodology_manifest_path.open("w", encoding="utf-8") as handle:
            yaml.safe_dump(manifest, handle, sort_keys=False, allow_unicode=True)
        return manifest
