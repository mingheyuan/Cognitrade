from __future__ import annotations

from datetime import date
from typing import Any

from research_system.adapters.vendors.cninfo_notice_adapter import CninfoNoticeAdapter
from research_system.adapters.vendors.fallback_merge import merge_provider_facts
from research_system.adapters.vendors.market_router import MarketRouter
from research_system.adapters.vendors.provider_execution import classify_provider_exception, failure_facts
from research_system.adapters.vendors.provider_registry import VendorProviderRegistry
from research_system.schemas.snapshots import SnapshotSource, VerifiedSnapshot


class NoticeSnapshotSkill:
    skill_name = "notice_snapshot_skill"

    def __init__(
        self,
        provider_registry: VendorProviderRegistry | None = None,
        market_router: MarketRouter | None = None,
    ) -> None:
        self.provider_registry = provider_registry
        self.market_router = market_router or MarketRouter()
        self.adapter = CninfoNoticeAdapter()

    def run(self, *, query: str, symbol: str | None = None) -> dict[str, Any]:
        snapshot_date = date.today().isoformat()
        route = self.market_router.classify(symbol=symbol, query=query)
        provider_chain = (
            self.provider_registry.get_provider_chain("notice", route.market or "cn_equity")
            if self.provider_registry
            else []
        )
        provider_trace = [self._provider_trace_entry(item.provider_id, "configured") for item in provider_chain]
        if not symbol:
            return VerifiedSnapshot(
                snapshot_type="notice",
                symbol=None,
                query=query,
                snapshot_date=snapshot_date,
                facts={**failure_facts("no_data"), "missing_scope": "symbol"},
                source=SnapshotSource(
                    skill=self.skill_name,
                    vendor=self.adapter.vendor_name,
                    from_cache=False,
                    provider_id="cninfo_notice",
                    source_id="cninfo",
                    chain_position=0,
                    dim="notice",
                ),
                primary_source=SnapshotSource(
                    skill=self.skill_name,
                    vendor=self.adapter.vendor_name,
                    from_cache=False,
                    provider_id="cninfo_notice",
                    source_id="cninfo",
                    chain_position=0,
                    dim="notice",
                ),
                missing_fields=["symbol"],
                source_trace=provider_trace,
                degraded=True,
                degrade_reason="symbol_missing",
            ).model_dump(mode="json")
        try:
            primary_facts = self.adapter.get_notice_snapshot(symbol=symbol)
            facts = merge_provider_facts(primary_facts=primary_facts, fallback_fact_sets=[])
            primary_source = SnapshotSource(
                skill=self.skill_name,
                vendor=self.adapter.vendor_name,
                from_cache=False,
                provider_id="cninfo_notice",
                source_id="cninfo",
                chain_position=0,
                dim="notice",
            )
            return VerifiedSnapshot(
                snapshot_type="notice",
                symbol=symbol,
                query=query,
                snapshot_date=snapshot_date,
                facts=facts,
                source=primary_source,
                primary_source=primary_source,
                fallback_applied=bool(facts.get("_fallback_applied")),
                field_sources={str(key): str(value) for key, value in (facts.get("_field_sources") or {}).items()},
                missing_fields=[str(item) for item in (facts.get("_missing_fields") or [])],
                source_confidence=(
                    self.provider_registry.source_confidence(provider_chain[0])
                    if self.provider_registry and provider_chain
                    else "high"
                ),
                source_trace=provider_trace,
            ).model_dump(mode="json")
        except Exception as exc:
            failure = classify_provider_exception(exc)
            return VerifiedSnapshot(
                snapshot_type="notice",
                symbol=symbol,
                query=query,
                snapshot_date=snapshot_date,
                facts=failure_facts(failure),
                source=SnapshotSource(
                    skill=self.skill_name,
                    vendor=self.adapter.vendor_name,
                    from_cache=False,
                    provider_id="cninfo_notice",
                    source_id="cninfo",
                    chain_position=0,
                    dim="notice",
                ),
                primary_source=SnapshotSource(
                    skill=self.skill_name,
                    vendor=self.adapter.vendor_name,
                    from_cache=False,
                    provider_id="cninfo_notice",
                    source_id="cninfo",
                    chain_position=0,
                    dim="notice",
                ),
                source_trace=provider_trace + [self._provider_trace_entry("cninfo_notice", f"error:{exc.__class__.__name__}")],
                degraded=True,
                degrade_reason=failure,
            ).model_dump(mode="json")

    def _provider_trace_entry(self, provider_id: str, status: str) -> dict[str, str]:
        return {"provider_id": provider_id, "status": status}
