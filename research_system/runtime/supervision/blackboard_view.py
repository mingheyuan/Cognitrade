from __future__ import annotations

from research_system.runtime.supervision.blackboard_models import (
    ReviewThesisView,
    SectorResearchView,
    StockCheckView,
    StockScreenView,
)
from research_system.runtime.supervision.blackboard_review_support import SupervisorBlackboardReviewSupport
from research_system.runtime.supervision.blackboard_screen_support import SupervisorBlackboardScreenSupport
from research_system.runtime.supervision.blackboard_sector_support import SupervisorBlackboardSectorSupport
from research_system.runtime.supervision.blackboard_snapshot_support import SupervisorBlackboardSnapshotSupport
from research_system.runtime.supervision.blackboard_source_support import SupervisorBlackboardSourceSupport
from research_system.runtime.supervision.blackboard_stock_check_support import SupervisorBlackboardStockCheckSupport
from research_system.schemas.runtime import BlackboardEntry


class SupervisorBlackboardView:
    def __init__(self) -> None:
        self.source_support = SupervisorBlackboardSourceSupport()
        self.snapshot_support = SupervisorBlackboardSnapshotSupport()
        self.screen_support = SupervisorBlackboardScreenSupport(source_support=self.source_support)
        self.stock_check_support = SupervisorBlackboardStockCheckSupport(
            source_support=self.source_support,
            snapshot_support=self.snapshot_support,
        )
        self.sector_support = SupervisorBlackboardSectorSupport(
            source_support=self.source_support,
            snapshot_support=self.snapshot_support,
        )
        self.review_support = SupervisorBlackboardReviewSupport(
            source_support=self.source_support,
            snapshot_support=self.snapshot_support,
        )

    def source_records(self, blackboard_entries: list[BlackboardEntry]) -> list[BlackboardEntry]:
        return self.source_support.source_records(blackboard_entries)

    def stock_screen_view(self, blackboard_entries: list[BlackboardEntry]) -> StockScreenView:
        return self.screen_support.stock_screen_view(blackboard_entries)

    def stock_check_view(self, blackboard_entries: list[BlackboardEntry]) -> StockCheckView:
        return self.stock_check_support.stock_check_view(blackboard_entries)

    def sector_research_view(self, blackboard_entries: list[BlackboardEntry]) -> SectorResearchView:
        return self.sector_support.sector_research_view(blackboard_entries)

    def review_thesis_view(self, blackboard_entries: list[BlackboardEntry]) -> ReviewThesisView:
        return self.review_support.review_thesis_view(blackboard_entries)
