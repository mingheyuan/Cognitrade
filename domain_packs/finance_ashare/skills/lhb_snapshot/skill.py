from __future__ import annotations

from datetime import date
from typing import Any

from research_system.adapters.vendors.akshare_lhb_adapter import AkShareLHBAdapter
from research_system.adapters.vendors.market_router import MarketRouter
from research_system.adapters.vendors.provider_registry import VendorProviderRegistry
from research_system.adapters.vendors.provider_execution import classify_provider_exception, failure_facts
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
        provider_chain = self.provider_registry.get_provider_chain("lhb", "cn_equity") if self.provider_registry else []
        provider_trace = [
            {"provider_id": item.provider_id, "source_id": item.source_id, "status": "configured"}
            for item in provider_chain
        ] or [{"provider_id": "akshare_lhb", "source_id": "akshare", "status": "configured"}]
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
                facts={**failure_facts("no_data"), "missing_scope": "symbol"},
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
                source_confidence=(
                    self.provider_registry.source_confidence(provider_chain[0])
                    if self.provider_registry and provider_chain
                    else "medium"
                ),
                source_trace=provider_trace,
            ).model_dump(mode="json")
        except Exception as exc:
            failure = classify_provider_exception(exc)
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
                facts=failure_facts(failure),
                source=primary_source,
                primary_source=primary_source,
                source_trace=provider_trace + [{"provider_id": "akshare_lhb", "status": f"error:{exc.__class__.__name__}"}],
                degraded=True,
                degrade_reason=failure,
            ).model_dump(mode="json")
