from __future__ import annotations

from datetime import date
from typing import Any

from research_system.adapters.vendors.akshare_sentiment_adapter import AkShareSentimentAdapter
from research_system.adapters.vendors.market_router import MarketRouter
from research_system.adapters.vendors.provider_registry import VendorProviderRegistry
from research_system.adapters.vendors.provider_execution import failure_facts
from research_system.adapters.vendors.uzi_hottrend_adapter import UziHotTrendAdapter
from research_system.adapters.vendors.uzi_news_adapter import UziNewsAdapter
from research_system.adapters.vendors.uzi_web_search_adapter import UziWebSearchAdapter
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
        provider_chain = self.provider_registry.get_provider_chain("sentiment", "cn_equity") if self.provider_registry else []
        provider_trace = [
            {"provider_id": item.provider_id, "source_id": item.source_id, "status": "configured"}
            for item in provider_chain
        ] or [{"provider_id": "akshare_sentiment", "source_id": "akshare", "status": "configured"}]
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
            facts.update(self.adapter.get_sentiment_snapshot(symbol=symbol))
            trace.append({"provider_id": "akshare_sentiment", "source_id": "em_data", "status": "available"})
        except Exception as exc:
            trace.append({"provider_id": "akshare_sentiment", "source_id": "em_data", "status": "provider_unavailable", "error_type": exc.__class__.__name__})

        try:
            web = UziWebSearchAdapter().search(query=f"{query} 股票 舆情 雪球 股吧 大V", dimension="sentiment", max_results=10)
            facts["platform_snippets"] = web.get("results") or []
            trace.extend(list(web.get("source_trace") or [{"provider_id": "ddgs_sentiment", "source_id": "ddgs", "status": "available", "count": web.get("result_count", 0)}]))
        except Exception as exc:
            trace.append({"provider_id": "ddgs_sentiment", "source_id": "ddgs", "status": "provider_unavailable", "error_type": exc.__class__.__name__})

        try:
            hot = UziHotTrendAdapter().get_mentions(name=query, extra_keywords=[symbol])
            facts["hot_trend_mentions"] = hot
            trace.extend(list(hot.get("source_trace") or []))
        except Exception as exc:
            trace.append({"provider_id": "hottrend_sentiment", "source_id": "hottrend", "status": "provider_unavailable", "error_type": exc.__class__.__name__})

        try:
            news = UziNewsAdapter().get_news(symbol=symbol, name=query, limit=15)
            facts["news_multi_source"] = news.get("sources") or {}
            trace.extend(list(news.get("source_trace") or []))
        except Exception as exc:
            trace.append({"provider_id": "uzi_news_sentiment", "source_id": "uzi_news_http", "status": "provider_unavailable", "error_type": exc.__class__.__name__})

        available = bool(
            facts.get("hot_rank_latest")
            or facts.get("hot_rank_detail")
            or facts.get("hot_keywords")
            or facts.get("news_samples")
            or facts.get("platform_snippets")
            or (facts.get("hot_trend_mentions") or {}).get("total_hits")
            or any((facts.get("news_multi_source") or {}).values())
        )
        primary_source = SnapshotSource(
            skill=self.skill_name,
            vendor="uzi_sentiment",
            from_cache=False,
            provider_id="uzi_sentiment_multi",
            source_id=next((str(row.get("source_id")) for row in trace if row.get("status") == "available"), "ddgs"),
            chain_position=0,
            dim="sentiment",
        )
        return VerifiedSnapshot(
            snapshot_type="sentiment",
            symbol=symbol,
            query=query,
            snapshot_date=snapshot_date,
            facts={**facts, "availability": "available" if available else "data_gap", "data_gap": [] if available else ["sentiment_sources_empty"]},
            source=primary_source,
            primary_source=primary_source,
            source_confidence="medium" if available else "low",
            source_trace=trace,
            degraded=not available,
            degrade_reason=None if available else "no_data",
        ).model_dump(mode="json")
