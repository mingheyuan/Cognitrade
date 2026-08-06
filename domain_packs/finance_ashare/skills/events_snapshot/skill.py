from __future__ import annotations

from datetime import date
from typing import Any

from research_system.adapters.vendors.akshare_events_adapter import AkShareEventsAdapter
from research_system.adapters.vendors.market_router import MarketRouter
from research_system.adapters.vendors.provider_registry import VendorProviderRegistry
from research_system.adapters.vendors.provider_execution import failure_facts
from research_system.adapters.vendors.uzi_news_adapter import UziNewsAdapter
from research_system.adapters.vendors.uzi_web_search_adapter import UziWebSearchAdapter
from research_system.schemas.snapshots import SnapshotSource, VerifiedSnapshot


class EventsSnapshotSkill:
    skill_name = "events_snapshot_skill"

    def __init__(
        self,
        provider_registry: VendorProviderRegistry | None = None,
        market_router: MarketRouter | None = None,
    ) -> None:
        self.provider_registry = provider_registry
        self.market_router = market_router or MarketRouter()
        self.adapter = AkShareEventsAdapter()

    def run(self, *, query: str, symbol: str | None = None) -> dict[str, Any]:
        snapshot_date = date.today().isoformat()
        provider_chain = self.provider_registry.get_provider_chain("events", "cn_equity") if self.provider_registry else []
        provider_trace = [
            {"provider_id": item.provider_id, "source_id": item.source_id, "status": "configured"}
            for item in provider_chain
        ] or [{"provider_id": "akshare_events", "source_id": "cninfo_events", "status": "configured"}]
        if not symbol:
            primary_source = SnapshotSource(
                skill=self.skill_name,
                vendor=self.adapter.vendor_name,
                from_cache=False,
                provider_id="akshare_events",
                source_id="akshare",
                chain_position=0,
                dim="events",
            )
            return VerifiedSnapshot(
                snapshot_type="events",
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
        facts: dict[str, Any] = {}
        trace: list[dict[str, Any]] = []
        try:
            facts.update(self.adapter.get_events_snapshot(symbol=symbol))
            trace.append({"provider_id": "cninfo_events", "source_id": "cninfo", "status": "available"})
        except Exception as exc:
            trace.append({"provider_id": "cninfo_events", "source_id": "cninfo", "status": "provider_unavailable", "error_type": exc.__class__.__name__})
        try:
            news = UziNewsAdapter().get_news(symbol=symbol, name=query, limit=20)
            facts["news_multi_source"] = news.get("sources") or {}
            trace.extend(list(news.get("source_trace") or []))
        except Exception as exc:
            trace.append({"provider_id": "uzi_news_events", "source_id": "uzi_news_http", "status": "provider_unavailable", "error_type": exc.__class__.__name__})
        try:
            web = UziWebSearchAdapter().search(query=f"{query} 最新动态 公告 合同 订单 业绩", dimension="events", max_results=8)
            facts["web_events"] = web.get("results") or []
            trace.extend(list(web.get("source_trace") or [{"provider_id": "ddgs_events", "source_id": "ddgs", "status": "available", "count": web.get("result_count", 0)}]))
        except Exception as exc:
            trace.append({"provider_id": "ddgs_events", "source_id": "ddgs", "status": "provider_unavailable", "error_type": exc.__class__.__name__})
        available = any(bool(value) for key, value in facts.items() if key not in {"symbol", "source_id"})
        primary_source = SnapshotSource(
            skill=self.skill_name,
            vendor="uzi_events",
            from_cache=False,
            provider_id="uzi_events_multi",
            source_id=next((str(row.get("source_id")) for row in trace if row.get("status") == "available"), "cninfo"),
            chain_position=0,
            dim="events",
        )
        return VerifiedSnapshot(
            snapshot_type="events",
            symbol=symbol,
            query=query,
            snapshot_date=snapshot_date,
            facts={**facts, "availability": "available" if available else "data_gap", "data_gap": [] if available else ["event_sources_empty"]},
            source=primary_source,
            primary_source=primary_source,
            source_confidence="high" if facts.get("event_notices") else "medium" if available else "low",
            source_trace=trace,
            degraded=not available,
            degrade_reason=None if available else "no_data",
        ).model_dump(mode="json")
