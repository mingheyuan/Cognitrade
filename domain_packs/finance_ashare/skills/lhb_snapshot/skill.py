from __future__ import annotations

from datetime import date
from typing import Any

from research_system.adapters.vendors.akshare_lhb_adapter import AkShareLHBAdapter
from research_system.adapters.vendors.market_router import MarketRouter
from research_system.adapters.vendors.provider_registry import ProviderSpec, VendorProviderRegistry
from research_system.adapters.vendors.provider_execution import execute_provider_chain, failure_facts
from research_system.adapters.vendors.tushare_signal_adapter import TuShareLHBAdapter
from research_system.adapters.vendors.uzi_browser_adapter import UziBrowserAdapter
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
        self.adapters: dict[str, Any] = {"akshare_lhb": self.adapter}
        if provider_registry and provider_registry.settings.tushare_token:
            self.adapters["tushare_lhb"] = TuShareLHBAdapter(provider_registry.settings.tushare_token)

    def run(self, *, query: str, symbol: str | None = None) -> dict[str, Any]:
        snapshot_date = date.today().isoformat()
        provider_chain = self.provider_registry.get_provider_chain("lhb", "cn_equity") if self.provider_registry else [
            ProviderSpec(provider_id="akshare_lhb", source_id="akshare_lhb", vendor_name="akshare", markets=("cn_equity",), dims=("lhb",), access="sdk")
        ]
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
        execution = execute_provider_chain(
            providers=provider_chain,
            loaders={provider_id: (lambda current=adapter: current.get_lhb_snapshot(symbol=symbol)) for provider_id, adapter in self.adapters.items()},
        )
        browser_result: dict[str, Any] = {"evidence": {}, "source_trace": []}
        if execution.availability != "available" and self.provider_registry is not None:
            browser_result = UziBrowserAdapter(self.provider_registry.settings).collect_many(
                source_ids=("yuncaijing",), symbol=symbol, query=query
            )
        browser_available = bool(browser_result.get("evidence"))
        selected = execution.provider or (
            ProviderSpec(provider_id="yuncaijing_lhb", source_id="yuncaijing", vendor_name="playwright", markets=("cn_equity",), dims=("lhb",), access="browser")
            if browser_available
            else provider_chain[0]
        )
        position = execution.chain_position if execution.chain_position is not None else len(provider_chain) if browser_available else 0
        primary_source = SnapshotSource(
            skill=self.skill_name,
            vendor=selected.vendor_name,
            from_cache=False,
            provider_id=selected.provider_id,
            source_id=selected.source_id,
            chain_position=position,
            dim="lhb",
        )
        available = execution.availability == "available" or browser_available
        facts = execution.facts if execution.availability == "available" else {
            **execution.facts,
            "browser_evidence": browser_result.get("evidence") or {},
            "availability": "available" if browser_available else execution.availability,
            "data_gap": [] if browser_available else list(execution.facts.get("data_gap") or []),
        }
        return VerifiedSnapshot(
            snapshot_type="lhb",
            symbol=symbol,
            query=query,
            snapshot_date=snapshot_date,
            facts=facts,
            source=primary_source,
            primary_source=primary_source,
            fallback_sources=[primary_source] if execution.fallback_applied else [],
            fallback_applied=execution.fallback_applied,
            source_confidence=self.provider_registry.source_confidence(selected) if self.provider_registry else "medium",
            source_trace=[*execution.trace, *list(browser_result.get("source_trace") or [])],
            degraded=not available,
            degrade_reason=None if available else execution.availability,
        ).model_dump(mode="json")
