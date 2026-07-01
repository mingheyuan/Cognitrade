from __future__ import annotations

from datetime import date
from typing import Any

from research_system.adapters.vendors.akshare_sentiment_adapter import AkShareSentimentAdapter
from research_system.adapters.vendors.market_router import MarketRouter
from research_system.adapters.vendors.provider_registry import VendorProviderRegistry
from research_system.schemas.snapshots import SnapshotSource, VerifiedSnapshot


class SentimentSnapshotSkill:
    skill_name = "sentiment_snapshot_skill"

    def __init__(
        self,
        provider_registry: VendorProviderRegistry | None = None,
        market_router: MarketRouter | None = None,
    ) -> None:
        self.provider_registry = provider_registry
        self.market_router = market_router or MarketRouter()
        self.adapter = AkShareSentimentAdapter()

    def run(self, *, query: str, symbol: str | None = None) -> dict[str, Any]:
        snapshot_date = date.today().isoformat()
        provider_trace = [{"provider_id": "akshare_sentiment", "status": "configured"}]
        if not symbol:
            primary_source = SnapshotSource(
                skill=self.skill_name,
                vendor=self.adapter.vendor_name,
                from_cache=False,
                provider_id="akshare_sentiment",
                source_id="akshare",
                chain_position=0,
                dim="sentiment",
            )
            return VerifiedSnapshot(
                snapshot_type="sentiment",
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
            facts = self.adapter.get_sentiment_snapshot(symbol=symbol)
            primary_source = SnapshotSource(
                skill=self.skill_name,
                vendor=self.adapter.vendor_name,
                from_cache=False,
                provider_id="akshare_sentiment",
                source_id="akshare",
                chain_position=0,
                dim="sentiment",
            )
            return VerifiedSnapshot(
                snapshot_type="sentiment",
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
                provider_id="akshare_sentiment",
                source_id="akshare",
                chain_position=0,
                dim="sentiment",
            )
            return VerifiedSnapshot(
                snapshot_type="sentiment",
                symbol=symbol,
                query=query,
                snapshot_date=snapshot_date,
                facts={"availability": "degraded", "note": str(exc)},
                source=primary_source,
                primary_source=primary_source,
                source_trace=provider_trace + [{"provider_id": "akshare_sentiment", "status": f"error:{exc.__class__.__name__}"}],
                degraded=True,
                degrade_reason="vendor_failure",
            ).model_dump(mode="json")
