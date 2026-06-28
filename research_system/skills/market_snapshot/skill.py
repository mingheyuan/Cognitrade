from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from research_system.adapters.vendors.akshare_market_adapter import AkShareMarketAdapter
from research_system.schemas.snapshots import SnapshotSource, VerifiedSnapshot


class MarketSnapshotSkill:
    skill_name = "market_snapshot_skill"

    def __init__(self) -> None:
        self.adapter = AkShareMarketAdapter()

    def run(self, *, query: str, symbol: str | None = None) -> dict[str, Any]:
        today = date.today()
        snapshot_date = today.isoformat()
        if not symbol:
            return VerifiedSnapshot(
                snapshot_type="market",
                symbol=None,
                query=query,
                snapshot_date=snapshot_date,
                facts={"availability": "degraded", "note": "symbol_missing"},
                source=SnapshotSource(skill=self.skill_name, vendor=self.adapter.vendor_name, from_cache=False),
                degraded=True,
                degrade_reason="symbol_missing",
            ).model_dump(mode="json")
        try:
            facts = {
                "market_hist": self.adapter.get_market_snapshot(
                    symbol=symbol,
                    start_date=(today - timedelta(days=30)).strftime("%Y%m%d"),
                    end_date=today.strftime("%Y%m%d"),
                ),
                "market_overview": self.adapter.get_market_overview(symbol=symbol),
            }
            return VerifiedSnapshot(
                snapshot_type="market",
                symbol=symbol,
                query=query,
                snapshot_date=snapshot_date,
                facts=facts,
                source=SnapshotSource(skill=self.skill_name, vendor=self.adapter.vendor_name, from_cache=False),
            ).model_dump(mode="json")
        except Exception as exc:
            return VerifiedSnapshot(
                snapshot_type="market",
                symbol=symbol,
                query=query,
                snapshot_date=snapshot_date,
                facts={"availability": "degraded", "note": str(exc)},
                source=SnapshotSource(skill=self.skill_name, vendor=self.adapter.vendor_name, from_cache=False),
                degraded=True,
                degrade_reason="vendor_failure",
            ).model_dump(mode="json")
