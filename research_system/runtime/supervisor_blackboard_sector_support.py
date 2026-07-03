from __future__ import annotations

from research_system.runtime.supervisor_blackboard_models import SectorResearchView
from research_system.schemas.runtime import BlackboardEntry


class SupervisorBlackboardSectorSupport:
    def __init__(self, *, source_support, snapshot_support) -> None:
        self.source_support = source_support
        self.snapshot_support = snapshot_support

    def sector_research_view(self, blackboard_entries: list[BlackboardEntry]) -> SectorResearchView:
        request_entry = self.source_support.latest_source_record(
            blackboard_entries,
            source_types={"user_query", "sector_research_replenish"},
        )
        request_payload = (request_entry.payload if request_entry else None) or {}
        sector_snapshot_entry = self.snapshot_support.latest_snapshot(
            blackboard_entries,
            snapshot_type="sector",
        )
        sector_snapshot = (sector_snapshot_entry.payload if sector_snapshot_entry else None) or {}
        facts = sector_snapshot.get("facts") or {}
        include_policy_snapshot = bool(request_payload.get("include_policy_snapshot"))
        required_sector_evidence = ["policy_signals", "regulatory_signals", "benchmark", "relative_strength"]
        if include_policy_snapshot:
            required_sector_evidence.append("policy_digest")
        missing_sector_evidence = [
            evidence_name
            for evidence_name in required_sector_evidence
            if not facts.get(evidence_name)
        ]
        request_query = str(request_payload.get("content") or sector_snapshot.get("query") or "")
        return SectorResearchView(
            request_query=request_query,
            missing_sector_evidence=missing_sector_evidence,
        )


__all__ = ["SupervisorBlackboardSectorSupport"]
