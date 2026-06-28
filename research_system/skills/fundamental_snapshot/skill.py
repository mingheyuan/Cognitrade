from __future__ import annotations

from datetime import date

from research_system.adapters.vendors.akshare_fundamental_adapter import AkShareFundamentalAdapter
from research_system.schemas.snapshots import SnapshotSource, VerifiedSnapshot


class FundamentalSnapshotSkill:
    skill_name = "fundamental_snapshot_skill"

    def __init__(self) -> None:
        self.adapter = AkShareFundamentalAdapter()

    def run(self, *, query: str, symbol: str | None = None) -> dict[str, Any]:
        snapshot_date = date.today().isoformat()
        if not symbol:
            return VerifiedSnapshot(
                snapshot_type="fundamental",
                symbol=None,
                query=query,
                snapshot_date=snapshot_date,
                facts={"availability": "degraded", "note": "symbol_missing"},
                source=SnapshotSource(skill=self.skill_name, vendor=self.adapter.vendor_name, from_cache=False),
                degraded=True,
                degrade_reason="symbol_missing",
            ).model_dump(mode="json")
        try:
            facts = self.adapter.get_fundamental_snapshot(symbol=symbol)
            return VerifiedSnapshot(
                snapshot_type="fundamental",
                symbol=symbol,
                query=query,
                snapshot_date=snapshot_date,
                facts=facts,
                source=SnapshotSource(skill=self.skill_name, vendor=self.adapter.vendor_name, from_cache=False),
            ).model_dump(mode="json")
        except Exception as exc:
            return VerifiedSnapshot(
                snapshot_type="fundamental",
                symbol=symbol,
                query=query,
                snapshot_date=snapshot_date,
                facts={"availability": "degraded", "note": str(exc)},
                source=SnapshotSource(skill=self.skill_name, vendor=self.adapter.vendor_name, from_cache=False),
                degraded=True,
                degrade_reason="vendor_failure",
            ).model_dump(mode="json")
