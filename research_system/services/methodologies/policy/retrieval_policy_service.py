from __future__ import annotations

from datetime import datetime, timezone

import yaml

from research_system.runtime.platform.settings import Settings
from research_system.schemas.proposals import ProposalDraft
from research_system.services.methodologies.planning.planning_context_methodology import (
    normalize_methodology_preference,
)
from research_system.services.methodologies.policy.retrieval_policy_view import normalize_retrieval_policy


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RetrievalPolicyService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def load(self) -> dict:
        path = self.settings.retrieval_policy_path
        if not path.exists():
            payload = normalize_retrieval_policy(None)
            payload["generated_at"] = utc_now_iso()
            return payload
        with path.open("r", encoding="utf-8") as handle:
            payload = normalize_retrieval_policy(yaml.safe_load(handle) or {})
        return payload

    def approve(self, proposal: ProposalDraft) -> dict:
        policy = self.load()
        approved_ids = list(policy.get("approved_proposal_ids") or [])
        if proposal.proposal_id not in approved_ids:
            approved_ids.append(proposal.proposal_id)
        policy["approved_proposal_ids"] = approved_ids
        policy["version"] = int(policy.get("version") or 1) + 1
        policy["generated_at"] = utc_now_iso()

        preferred_methodologies = list(policy.get("preferred_methodologies") or [])
        if proposal.proposal_type == "ValuationHeuristicPatch":
            preferred_methodology = normalize_methodology_preference(proposal.benchmark_scope)
            if preferred_methodology and preferred_methodology not in preferred_methodologies:
                preferred_methodologies.append(preferred_methodology)
            policy["preferred_methodologies"] = preferred_methodologies

        if proposal.proposal_type == "MethodologyTemplatePatch":
            score_weights = dict(policy.get("score_weights") or {})
            score_weights["metadata_match"] = max(float(score_weights.get("metadata_match") or 1.0), 1.1)
            policy["score_weights"] = score_weights

        self.settings.retrieval_policy_path.parent.mkdir(parents=True, exist_ok=True)
        with self.settings.retrieval_policy_path.open("w", encoding="utf-8") as handle:
            yaml.safe_dump(policy, handle, sort_keys=False, allow_unicode=True)
        return policy
