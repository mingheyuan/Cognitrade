from __future__ import annotations

from research_system.runtime.supervisor_blackboard_models import StockCheckView
from research_system.schemas.runtime import BlackboardEntry


class SupervisorBlackboardStockCheckSupport:
    def __init__(self, *, source_support, snapshot_support) -> None:
        self.source_support = source_support
        self.snapshot_support = snapshot_support

    def stock_check_view(self, blackboard_entries: list[BlackboardEntry]) -> StockCheckView:
        request_entry = self.source_support.latest_source_record(
            blackboard_entries,
            source_types={"user_query"},
        )
        request_payload = (request_entry.payload if request_entry else None) or {}
        symbol = str(request_payload.get("symbol") or "")
        request_source_type = str(request_payload.get("source_type") or "")
        required_snapshot_types = ["market", "fundamental", "notice"]
        if request_source_type == "stock_check_replenish":
            requested_snapshot_types = request_payload.get("snapshot_types") or []
            required_snapshot_types = [str(item) for item in requested_snapshot_types if str(item)]
        snapshot_types = self.snapshot_support.snapshot_types(blackboard_entries)
        missing_snapshot_types = [
            snapshot_type
            for snapshot_type in required_snapshot_types
            if snapshot_type not in snapshot_types
        ]
        request_query = str(request_payload.get("content") or symbol or "")
        return StockCheckView(
            symbol=symbol,
            request_source_type=request_source_type,
            request_query=request_query,
            missing_snapshot_types=missing_snapshot_types,
        )


__all__ = ["SupervisorBlackboardStockCheckSupport"]
