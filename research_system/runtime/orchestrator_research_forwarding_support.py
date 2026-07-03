from __future__ import annotations

from typing import Any


class OrchestratorResearchForwardingSupport:
    def __init__(self, host: Any) -> None:
        self.host = host

    def _research_support(self):
        support = getattr(self.host, "research_support", None)
        if support is None and hasattr(self.host, "_build_research_support"):
            support = self.host._build_research_support()
            self.host.research_support = support
        return support

    def run_methodology_evolution_collect(self, **kwargs):
        return self._research_support().run_methodology_evolution_collect(**kwargs)

    def build_candidate_methodology_entries(self, **kwargs):
        return self._research_support().build_candidate_methodology_entries(**kwargs)

    def build_stock_methodology_entries(self, **kwargs):
        return self._research_support().build_stock_methodology_entries(**kwargs)

    def build_review_methodology_entries(self, **kwargs):
        return self._research_support().build_review_methodology_entries(**kwargs)

    def build_debug_payload(self, **kwargs) -> dict:
        return self._research_support().build_debug_payload(**kwargs)

    def persist_proposal_transition(self, proposal, *, previous_status: str, apply_approved_side_effects: bool):
        return self._research_support().persist_proposal_transition(
            proposal,
            previous_status=previous_status,
            apply_approved_side_effects=apply_approved_side_effects,
        )

    def evaluate_methodology_auto_approval(self, *, proposal, constraints: dict) -> dict[str, object]:
        return self._research_support().evaluate_methodology_auto_approval(
            proposal=proposal,
            constraints=constraints,
        )

    def stale_memory_support_reason(self, *, proposal, max_age_days: int) -> str | None:
        return self._research_support().stale_memory_support_reason(
            proposal=proposal,
            max_age_days=max_age_days,
        )

    def insufficient_memory_support_reason(self, *, proposal, min_entity_scope_count: int) -> str | None:
        return self._research_support().insufficient_memory_support_reason(
            proposal=proposal,
            min_entity_scope_count=min_entity_scope_count,
        )

    def append_proposal_review_event(
        self,
        proposal,
        *,
        decision: str,
        reviewer: str,
        reason: str,
        previous_status: str | None,
        reviewed_at,
        source: str,
    ) -> None:
        self._research_support().append_proposal_review_event(
            proposal,
            decision=decision,
            reviewer=reviewer,
            reason=reason,
            previous_status=previous_status,
            reviewed_at=reviewed_at,
            source=source,
        )

    def resolve_methodology_target_run_ids(self, request) -> list[str]:
        return self._research_support().resolve_methodology_target_run_ids(request)
