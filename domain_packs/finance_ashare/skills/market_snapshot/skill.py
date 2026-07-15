from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from research_system.adapters.vendors.akshare_market_adapter import AkShareMarketAdapter
from research_system.adapters.vendors.baostock_market_adapter import BaoStockMarketAdapter
from research_system.adapters.vendors.market_router import MarketRouter
from research_system.adapters.vendors.provider_execution import execute_provider_chain, failure_facts
from research_system.adapters.vendors.provider_registry import ProviderSpec, VendorProviderRegistry
from research_system.schemas.snapshots import SnapshotSource, VerifiedSnapshot


class MarketSnapshotSkill:
    skill_name = "market_snapshot_skill"

    def __init__(
        self,
        provider_registry: VendorProviderRegistry | None = None,
        market_router: MarketRouter | None = None,
    ) -> None:
        self.provider_registry = provider_registry
        self.market_router = market_router or MarketRouter()
        self.adapters = {
            "akshare_market": AkShareMarketAdapter(),
            "baostock_market": BaoStockMarketAdapter(),
        }

    def run(self, *, query: str, symbol: str | None = None) -> dict[str, Any]:
        today = date.today()
        snapshot_date = today.isoformat()
        route = self.market_router.classify(symbol=symbol, query=query)
        providers = self._providers(route.market or "cn_equity")
        if not symbol:
            source = self._source(providers[0], 0)
            return VerifiedSnapshot(
                snapshot_type="market",
                symbol=None,
                query=query,
                snapshot_date=snapshot_date,
                facts={**failure_facts("no_data"), "missing_scope": "symbol"},
                source=source,
                primary_source=source,
                missing_fields=["symbol"],
                degraded=True,
                degrade_reason="symbol_missing",
            ).model_dump(mode="json")

        start_date = (today - timedelta(days=30)).strftime("%Y%m%d")
        end_date = today.strftime("%Y%m%d")
        execution = execute_provider_chain(
            providers=providers,
            loaders={
                provider_id: self._market_loader(adapter, symbol, start_date, end_date)
                for provider_id, adapter in self.adapters.items()
            },
        )
        selected = execution.provider or providers[0]
        position = execution.chain_position if execution.chain_position is not None else 0
        source = self._source(selected, position)
        field_sources = {
            key: selected.provider_id
            for key in execution.facts
            if not key.startswith("_") and key not in {"availability", "message", "data_gap"}
        }
        return VerifiedSnapshot(
            snapshot_type="market",
            symbol=symbol,
            query=query,
            snapshot_date=snapshot_date,
            facts=execution.facts,
            source=source,
            primary_source=source,
            fallback_sources=[source] if execution.fallback_applied else [],
            field_sources=field_sources,
            fallback_applied=execution.fallback_applied,
            missing_fields=list(execution.facts.get("data_gap") or []),
            source_confidence=self._confidence(selected),
            source_trace=execution.trace,
            degraded=execution.availability != "available",
            degrade_reason=None if execution.availability == "available" else execution.availability,
        ).model_dump(mode="json")

    @staticmethod
    def _market_loader(adapter, symbol: str, start_date: str, end_date: str):
        def load() -> dict[str, Any]:
            market_hist = adapter.get_market_snapshot(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
            )
            return {
                "market_hist": market_hist,
                "market_overview": adapter.get_market_overview(
                    symbol=symbol,
                    latest_bar=(market_hist or {}).get("latest_bar"),
                ),
            }

        return load

    def _providers(self, market: str) -> list[ProviderSpec]:
        if self.provider_registry:
            providers = self.provider_registry.get_provider_chain("market", market)
            if providers:
                return providers
        return [
            ProviderSpec(
                provider_id="akshare_market",
                source_id="akshare",
                vendor_name="akshare",
                markets=("cn_equity",),
                dims=("market",),
                access="sdk",
            )
        ]

    def _source(self, provider: ProviderSpec, position: int) -> SnapshotSource:
        return SnapshotSource(
            skill=self.skill_name,
            vendor=provider.vendor_name,
            from_cache=False,
            provider_id=provider.provider_id,
            source_id=provider.source_id,
            chain_position=position,
            dim="market",
        )

    def _confidence(self, provider: ProviderSpec) -> str:
        return self.provider_registry.source_confidence(provider) if self.provider_registry else "medium"
