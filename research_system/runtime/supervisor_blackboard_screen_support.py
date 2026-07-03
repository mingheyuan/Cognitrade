from __future__ import annotations

from research_system.runtime.supervisor_blackboard_models import StockScreenView
from research_system.schemas.runtime import BlackboardEntry


class SupervisorBlackboardScreenSupport:
    def __init__(self, *, source_support) -> None:
        self.source_support = source_support

    def stock_screen_view(self, blackboard_entries: list[BlackboardEntry]) -> StockScreenView:
        scorecard_entry = self.source_support.latest_source_record(
            blackboard_entries,
            source_types={"screening_scorecard"},
        )
        scorecard = ((scorecard_entry.payload or {}).get("scorecard") if scorecard_entry else None) or {}
        return StockScreenView(
            review_summary_present=self.source_support.review_summary_present(blackboard_entries),
            top_candidate_count=int(scorecard.get("top_candidate_count") or 0),
            watchlist_candidate_count=int(scorecard.get("watchlist_candidate_count") or 0),
        )


__all__ = ["SupervisorBlackboardScreenSupport"]
