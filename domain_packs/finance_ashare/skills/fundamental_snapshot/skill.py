from __future__ import annotations

from datetime import date
from typing import Any

from research_system.adapters.vendors.akshare_fundamental_adapter import AkShareFundamentalAdapter
from research_system.adapters.vendors.baostock_fundamental_adapter import BaoStockFundamentalAdapter
from research_system.adapters.vendors.market_router import MarketRouter
from research_system.adapters.vendors.mx_api_adapter import MxApiAdapter
from research_system.adapters.vendors.provider_execution import execute_provider_chain, failure_facts
from research_system.adapters.vendors.provider_registry import ProviderSpec, VendorProviderRegistry
from research_system.adapters.vendors.tushare_fundamental_adapter import TuShareFundamentalAdapter
from research_system.adapters.vendors.yfinance_fundamental_adapter import YFinanceFundamentalAdapter
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
        self.adapters: dict[str, Any] = {"akshare_fundamental": AkShareFundamentalAdapter()}
        if provider_registry and provider_registry.settings.mx_api_key:
            self.adapters["mx_api_fundamental"] = MxApiAdapter(provider_registry.settings.mx_api_key)
        token = provider_registry.settings.tushare_token if provider_registry else None
        if token:
            self.adapters["tushare_fundamental"] = TuShareFundamentalAdapter(token)
        self.adapters["baostock_fundamental"] = BaoStockFundamentalAdapter()
        if provider_registry and provider_registry.get_provider_chain("fundamental", "global_equity"):
            self.adapters["yfinance_fundamental"] = YFinanceFundamentalAdapter()

    def run(self, *, query: str, symbol: str | None = None) -> dict[str, Any]:
        snapshot_date = date.today().isoformat()
        route = self.market_router.classify(symbol=symbol, query=query)
        providers = self._providers(route.market or "cn_equity")
        if not symbol:
            source = self._source(providers[0], 0)
            return VerifiedSnapshot(
                snapshot_type="fundamental",
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

        resolved_symbol = route.normalized_symbol or symbol
        execution = execute_provider_chain(
            providers=providers,
            loaders={
                provider_id: (lambda current=adapter: current.get_fundamental_snapshot(symbol=resolved_symbol))
                for provider_id, adapter in self.adapters.items()
            },
        )
        execution.facts.update({
            "market": route.market_code or route.market,
            "provider_market": route.market,
            "exchange": route.exchange,
            "currency": route.currency or execution.facts.get("currency"),
            "country": route.country or None,
        })
        selected = execution.provider or providers[0]
        position = execution.chain_position if execution.chain_position is not None else 0
        source = self._source(selected, position)
        field_sources = {
            key: selected.provider_id
            for key in execution.facts
            if not key.startswith("_") and key not in {"availability", "message", "data_gap"}
        }
        return VerifiedSnapshot(
            snapshot_type="fundamental",
            symbol=resolved_symbol,
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

    def _providers(self, market: str) -> list[ProviderSpec]:
        if self.provider_registry:
            providers = self.provider_registry.get_provider_chain("fundamental", market)
            if providers:
                return providers
        return [
            ProviderSpec(
                provider_id="akshare_fundamental",
                source_id="akshare",
                vendor_name="akshare",
                markets=("cn_equity",),
                dims=("fundamental",),
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
            dim="fundamental",
        )

    def _confidence(self, provider: ProviderSpec) -> str:
        return self.provider_registry.source_confidence(provider) if self.provider_registry else "medium"
