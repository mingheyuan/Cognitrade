from __future__ import annotations

from datetime import datetime, timedelta, timezone


class OrchestratorProposalMemoryGateSupport:
    def stale_memory_support_reason(
        self,
        *,
        proposal,
        max_age_days: int,
    ) -> str | None:
        before_metrics = dict(getattr(proposal, "before_metrics", {}) or {})
        memory_support = dict(before_metrics.get("memory_support") or {})
        latest_recorded_at = str(memory_support.get("latest_recorded_at") or "").strip()
        context_count = int(memory_support.get("context_count") or 0)
        entity_scope_count = int(memory_support.get("entity_scope_count") or 0)
        if not latest_recorded_at or context_count <= 0 or entity_scope_count <= 0:
            return None
        try:
            recorded_at = datetime.fromisoformat(latest_recorded_at)
        except ValueError:
            return None
        if recorded_at.tzinfo is None:
            recorded_at = recorded_at.replace(tzinfo=timezone.utc)
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        if recorded_at < cutoff:
            return f"pending_manual_review_memory_support_stale>{max_age_days}d"
        return None

    def insufficient_memory_support_reason(
        self,
        *,
        proposal,
        min_entity_scope_count: int,
    ) -> str | None:
        if min_entity_scope_count <= 1:
            return None
        before_metrics = dict(getattr(proposal, "before_metrics", {}) or {})
        memory_support = dict(before_metrics.get("memory_support") or {})
        context_count = int(memory_support.get("context_count") or 0)
        entity_scope_count = int(memory_support.get("entity_scope_count") or 0)
        snapshot_count = int(memory_support.get("snapshot_count") or 0)
        if context_count <= 0:
            return None
        if entity_scope_count < min_entity_scope_count:
            return (
                "pending_manual_review_memory_support_entity_scope_count"
                f"<{min_entity_scope_count}"
            )
        if snapshot_count < min_entity_scope_count:
            return (
                "pending_manual_review_memory_support_snapshot_count"
                f"<{min_entity_scope_count}"
            )
        return None
