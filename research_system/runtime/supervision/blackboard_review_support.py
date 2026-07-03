from __future__ import annotations

from research_system.runtime.supervision.blackboard_models import ReviewThesisView
from research_system.schemas.runtime import BlackboardEntry


class SupervisorBlackboardReviewSupport:
    def __init__(self, *, source_support, snapshot_support) -> None:
        self.source_support = source_support
        self.snapshot_support = snapshot_support

    def review_thesis_view(self, blackboard_entries: list[BlackboardEntry]) -> ReviewThesisView:
        review_request_entry = self.source_support.latest_source_record(
            blackboard_entries,
            source_types={"review_request"},
        )
        review_request = (review_request_entry.payload if review_request_entry else None) or {}
        review_target_run_id = str(review_request.get("review_target_run_id") or "")
        review_source_entries = list(review_request.get("review_source_entries") or [])
        has_symbol_snapshot = self.snapshot_support.has_any_snapshot(
            blackboard_entries,
            snapshot_types={"market", "fundamental", "notice"},
        )
        has_sector_snapshot = self.snapshot_support.has_any_snapshot(
            blackboard_entries,
            snapshot_types={"sector"},
        )
        return ReviewThesisView(
            review_summary_present=self.source_support.review_summary_present(blackboard_entries),
            needs_review=bool(review_target_run_id and review_source_entries and (has_symbol_snapshot or has_sector_snapshot)),
        )


__all__ = ["SupervisorBlackboardReviewSupport"]
