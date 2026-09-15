from __future__ import annotations

from datetime import date, datetime
from typing import Any

from research_system.adapters.vendors.akshare_capital_flow_adapter import (
    AkShareCapitalFlowAdapter,
)
from research_system.adapters.vendors.efinance_capital_flow_adapter import (
    EFinanceCapitalFlowAdapter,
)
from research_system.adapters.vendors.market_router import MarketRouter
from research_system.adapters.vendors.provider_execution import (
    execute_provider_chain,
    failure_facts,
)
from research_system.adapters.vendors.provider_registry import (
    ProviderSpec,
    VendorProviderRegistry,
)
from research_system.adapters.vendors.tushare_signal_adapter import (
    TuShareCapitalFlowAdapter,
)
from research_system.schemas.snapshots import SnapshotSource, VerifiedSnapshot


class CapitalFlowSnapshotSkill:
    skill_name = "capital_flow_snapshot_skill"

    def __init__(
        self,
        provider_registry: VendorProviderRegistry | None = None,
        market_router: MarketRouter | None = None,
    ) -> None:
        self.provider_registry = provider_registry
        self.market_router = market_router or MarketRouter()
        self.adapter = AkShareCapitalFlowAdapter()
        self.adapters: dict[str, Any] = {"akshare_capital_flow": self.adapter}
        if provider_registry:
            provider_chain = provider_registry.get_provider_chain(
                "capital_flow", "cn_equity"
            )
            if provider_registry.get("efinance_capital_flow") in provider_chain:
                self.adapters["efinance_capital_flow"] = EFinanceCapitalFlowAdapter()
            if provider_registry.settings.tushare_token:
                self.adapters["tushare_capital_flow"] = TuShareCapitalFlowAdapter(
                    provider_registry.settings.tushare_token
                )

    def run(
        self,
        *,
        query: str,
        symbol: str | None = None,
        as_of_date: date | None = None,
        cutoff_at: datetime | None = None,
    ) -> dict[str, Any]:
        del cutoff_at
        snapshot_date = (as_of_date or date.today()).isoformat()
        provider_chain = (
            self.provider_registry.get_provider_chain("capital_flow", "cn_equity")
            if self.provider_registry
            else [
                ProviderSpec(
                    provider_id="akshare_capital_flow",
                    source_id="akshare",
                    vendor_name="akshare",
                    markets=("cn_equity",),
                    dims=("capital_flow",),
                    access="sdk",
                )
            ]
        )
        provider_trace = [
            {
                "provider_id": item.provider_id,
                "source_id": item.source_id,
                "status": "configured",
            }
            for item in provider_chain
        ] or [
            {
                "provider_id": "akshare_capital_flow",
                "source_id": "akshare",
                "status": "configured",
            }
        ]
        if not symbol:
            primary_source = SnapshotSource(
                skill=self.skill_name,
                vendor=self.adapter.vendor_name,
                from_cache=False,
                provider_id="akshare_capital_flow",
                source_id="akshare",
                chain_position=0,
                dim="capital_flow",
            )
            return VerifiedSnapshot(
                snapshot_type="capital_flow",
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
        execution = execute_provider_chain(
            providers=provider_chain,
            loaders={
                provider_id: (
                    lambda current=adapter: current.get_capital_flow_snapshot(
                        symbol=symbol,
                        as_of_date=as_of_date,
                    )
                )
                for provider_id, adapter in self.adapters.items()
            },
        )
        selected = execution.provider or provider_chain[0]
        position = (
            execution.chain_position if execution.chain_position is not None else 0
        )
        primary_source = SnapshotSource(
            skill=self.skill_name,
            vendor=selected.vendor_name,
            from_cache=False,
            provider_id=selected.provider_id,
            source_id=selected.source_id,
            chain_position=position,
            dim="capital_flow",
        )
        available = execution.availability == "available"
        return VerifiedSnapshot(
            snapshot_type="capital_flow",
            symbol=symbol,
            query=query,
            snapshot_date=snapshot_date,
            facts=execution.facts,
            source=primary_source,
            primary_source=primary_source,
            fallback_sources=[primary_source] if execution.fallback_applied else [],
            fallback_applied=execution.fallback_applied,
            source_confidence=self.provider_registry.source_confidence(selected)
            if self.provider_registry
            else "medium",
            source_trace=execution.trace,
            degraded=not available,
            degrade_reason=None if available else execution.availability,
        ).model_dump(mode="json")
