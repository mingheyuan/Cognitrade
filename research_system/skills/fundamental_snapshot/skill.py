from __future__ import annotations

from datetime import date

from research_system.adapters.vendors.akshare_fundamental_adapter import AkShareFundamentalAdapter
from research_system.adapters.vendors.fallback_merge import merge_provider_facts
from research_system.adapters.vendors.market_router import MarketRouter
from research_system.adapters.vendors.provider_registry import VendorProviderRegistry
from research_system.schemas.snapshots import SnapshotSource, VerifiedSnapshot


class FundamentalSnapshotSkill:
    skill_name = "fundamental_snapshot_skill"

    def __init__(
        self,
        provider_registry: VendorProviderRegistry | None = None,
        market_router: MarketRouter | None = None,
    ) -> None:
        self.provider_registry = provider_registry
        self.market_router = market_router or MarketRouter()
        self.adapter = AkShareFundamentalAdapter()

    def run(self, *, query: str, symbol: str | None = None) -> dict[str, Any]:
        snapshot_date = date.today().isoformat()
        route = self.market_router.classify(symbol=symbol, query=query)
        provider_chain = (
            self.provider_registry.get_provider_chain("fundamental", route.market or "cn_equity")
            if self.provider_registry
            else []
        )
        provider_trace = [self._provider_trace_entry(item.provider_id, "configured") for item in provider_chain]
        if not symbol:
            return VerifiedSnapshot(
                snapshot_type="fundamental",
                symbol=None,
                query=query,
                snapshot_date=snapshot_date,
                facts={"availability": "degraded", "note": "symbol_missing"},
                source=SnapshotSource(
                    skill=self.skill_name,
                    vendor=self.adapter.vendor_name,
                    from_cache=False,
                    provider_id="akshare_fundamental",
                    source_id="akshare",
                    chain_position=0,
                    dim="fundamental",
                ),
                primary_source=SnapshotSource(
                    skill=self.skill_name,
                    vendor=self.adapter.vendor_name,
                    from_cache=False,
                    provider_id="akshare_fundamental",
                    source_id="akshare",
                    chain_position=0,
                    dim="fundamental",
                ),
                missing_fields=["symbol"],
                source_trace=provider_trace,
                degraded=True,
                degrade_reason="symbol_missing",
            ).model_dump(mode="json")
        try:
            primary_facts = self.adapter.get_fundamental_snapshot(symbol=symbol)
            facts = merge_provider_facts(primary_facts=primary_facts, fallback_fact_sets=[])
            primary_source = SnapshotSource(
                skill=self.skill_name,
                vendor=self.adapter.vendor_name,
                from_cache=False,
                provider_id="akshare_fundamental",
                source_id="akshare",
                chain_position=0,
                dim="fundamental",
            )
            return VerifiedSnapshot(
                snapshot_type="fundamental",
                symbol=symbol,
                query=query,
                snapshot_date=snapshot_date,
                facts=facts,
                source=primary_source,
                primary_source=primary_source,
                fallback_applied=bool(facts.get("_fallback_applied")),
                field_sources={str(key): str(value) for key, value in (facts.get("_field_sources") or {}).items()},
                missing_fields=[str(item) for item in (facts.get("_missing_fields") or [])],
                source_confidence="medium",
                source_trace=provider_trace,
            ).model_dump(mode="json")
        except Exception as exc:
            return VerifiedSnapshot(
                snapshot_type="fundamental",
                symbol=symbol,
                query=query,
                snapshot_date=snapshot_date,
                facts={"availability": "degraded", "note": str(exc)},
                source=SnapshotSource(
                    skill=self.skill_name,
                    vendor=self.adapter.vendor_name,
                    from_cache=False,
                    provider_id="akshare_fundamental",
                    source_id="akshare",
                    chain_position=0,
                    dim="fundamental",
                ),
                primary_source=SnapshotSource(
                    skill=self.skill_name,
                    vendor=self.adapter.vendor_name,
                    from_cache=False,
                    provider_id="akshare_fundamental",
                    source_id="akshare",
                    chain_position=0,
                    dim="fundamental",
                ),
                source_trace=provider_trace + [self._provider_trace_entry("akshare_fundamental", f"error:{exc.__class__.__name__}")],
                degraded=True,
                degrade_reason="vendor_failure",
            ).model_dump(mode="json")

    def _provider_trace_entry(self, provider_id: str, status: str) -> dict[str, str]:
        return {"provider_id": provider_id, "status": status}
