from __future__ import annotations

from datetime import date
from typing import Any

from research_system.adapters.vendors.akshare_lhb_adapter import AkShareLHBAdapter
from research_system.adapters.vendors.market_router import MarketRouter
from research_system.adapters.vendors.provider_registry import VendorProviderRegistry
from research_system.schemas.snapshots import SnapshotSource, VerifiedSnapshot


class LHBSnapshotSkill:
    skill_name = "lhb_snapshot_skill"

    def __init__(
        self,
        provider_registry: VendorProviderRegistry | None = None,
        market_router: MarketRouter | None = None,
    ) -> None:
        self.provider_registry = provider_registry
        self.market_router = market_router or MarketRouter()
        self.adapter = AkShareLHBAdapter()

    def run(self, *, query: str, symbol: str | None = None) -> dict[str, Any]:
        snapshot_date = date.today().isoformat()
        provider_trace = [{"provider_id": "akshare_lhb", "status": "configured"}]
        if not symbol:
            primary_source = SnapshotSource(
                skill=self.skill_name,
                vendor=self.adapter.vendor_name,
                from_cache=False,
                provider_id="akshare_lhb",
                source_id="akshare",
                chain_position=0,
                dim="lhb",
            )
            return VerifiedSnapshot(
                snapshot_type="lhb",
                symbol=None,
                query=query,
                snapshot_date=snapshot_date,
                facts={"availability": "degraded", "note": "symbol_missing"},
                source=primary_source,
                primary_source=primary_source,
                missing_fields=["symbol"],
                source_trace=provider_trace,
                degraded=True,
                degrade_reason="symbol_missing",
            ).model_dump(mode="json")
        try:
            facts = self.adapter.get_lhb_snapshot(symbol=symbol)
            primary_source = SnapshotSource(
                skill=self.skill_name,
                vendor=self.adapter.vendor_name,
                from_cache=False,
                provider_id="akshare_lhb",
                source_id="akshare",
                chain_position=0,
                dim="lhb",
            )
            return VerifiedSnapshot(
                snapshot_type="lhb",
                symbol=symbol,
                query=query,
                snapshot_date=snapshot_date,
                facts=facts,
                source=primary_source,
                primary_source=primary_source,
                source_confidence="medium",
                source_trace=provider_trace,
            ).model_dump(mode="json")
        except Exception as exc:
            primary_source = SnapshotSource(
                skill=self.skill_name,
                vendor=self.adapter.vendor_name,
                from_cache=False,
                provider_id="akshare_lhb",
                source_id="akshare",
                chain_position=0,
                dim="lhb",
            )
            return VerifiedSnapshot(
                snapshot_type="lhb",
                symbol=symbol,
                query=query,
                snapshot_date=snapshot_date,
                facts={"availability": "degraded", "note": str(exc)},
                source=primary_source,
                primary_source=primary_source,
                source_trace=provider_trace + [{"provider_id": "akshare_lhb", "status": f"error:{exc.__class__.__name__}"}],
                degraded=True,
                degrade_reason="vendor_failure",
            ).model_dump(mode="json")
