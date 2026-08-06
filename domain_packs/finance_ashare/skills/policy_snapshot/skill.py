from __future__ import annotations

from datetime import date
from typing import Any

from research_system.adapters.vendors.akshare_policy_adapter import AkSharePolicyAdapter
from research_system.adapters.vendors.provider_registry import VendorProviderRegistry
from research_system.adapters.vendors.provider_execution import failure_facts
from research_system.adapters.vendors.uzi_news_adapter import UziNewsAdapter
from research_system.adapters.vendors.uzi_web_search_adapter import UziWebSearchAdapter
from research_system.schemas.snapshots import SnapshotSource, VerifiedSnapshot


class PolicySnapshotSkill:
    skill_name = "policy_snapshot_skill"

    def __init__(self, provider_registry: VendorProviderRegistry | None = None) -> None:
        self.provider_registry = provider_registry
        self.adapter = AkSharePolicyAdapter()

    def run(self, *, query: str, sector: str | None = None, constituents: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        snapshot_date = date.today().isoformat()
        theme = str(sector or query or "").strip()
        provider_chain = self.provider_registry.get_provider_chain("policy", "cn_sector") if self.provider_registry else []
        provider_trace = [
            {"provider_id": item.provider_id, "source_id": item.source_id, "status": "configured"}
            for item in provider_chain
        ] or [{"provider_id": "akshare_policy", "source_id": "akshare", "status": "configured"}]
        if not theme:
            primary_source = SnapshotSource(
                skill=self.skill_name,
                vendor=self.adapter.vendor_name,
                from_cache=False,
                provider_id="akshare_policy",
                source_id="akshare",
                chain_position=0,
                dim="policy",
            )
            return VerifiedSnapshot(
                snapshot_type="policy",
                query=query,
                snapshot_date=snapshot_date,
                facts={**failure_facts("no_data"), "missing_scope": "sector"},
                source=primary_source,
                primary_source=primary_source,
                missing_fields=["sector"],
                source_trace=provider_trace,
                degraded=True,
                degrade_reason="sector_missing",
            ).model_dump(mode="json")
        facts: dict[str, Any] = {}
        trace: list[dict[str, Any]] = []
        try:
            facts.update(self.adapter.get_policy_snapshot(theme=theme, constituents=constituents))
            trace.append({"provider_id": "akshare_policy", "source_id": "em_data", "status": "available"})
        except Exception as exc:
            trace.append({"provider_id": "akshare_policy", "source_id": "em_data", "status": "provider_unavailable", "error_type": exc.__class__.__name__})
        try:
            web = UziWebSearchAdapter().search(query=f"{theme} 最新政策 监管 原文", dimension="policy", max_results=10)
            facts["official_policy_search"] = web.get("results") or []
            trace.extend(list(web.get("source_trace") or [{"provider_id": "ddgs_policy", "source_id": "ddgs", "status": "available", "count": web.get("result_count", 0)}]))
        except Exception as exc:
            trace.append({"provider_id": "ddgs_policy", "source_id": "ddgs", "status": "provider_unavailable", "error_type": exc.__class__.__name__})
        try:
            news = UziNewsAdapter().get_news(name=theme, limit=20)
            facts["news_multi_source"] = news.get("sources") or {}
            trace.extend(list(news.get("source_trace") or []))
        except Exception as exc:
            trace.append({"provider_id": "uzi_news_policy", "source_id": "uzi_news_http", "status": "provider_unavailable", "error_type": exc.__class__.__name__})
        available = any(bool(value) for value in facts.values())
        primary_source = SnapshotSource(
            skill=self.skill_name,
            vendor="uzi_policy",
            from_cache=False,
            provider_id="uzi_policy_multi",
            source_id=next((str(row.get("source_id")) for row in trace if row.get("status") == "available"), "ddgs"),
            chain_position=0,
            dim="policy",
        )
        return VerifiedSnapshot(
            snapshot_type="policy",
            query=query,
            snapshot_date=snapshot_date,
            sector=theme,
            theme=theme,
            facts={**facts, "availability": "available" if available else "data_gap", "data_gap": [] if available else ["policy_sources_empty"]},
            source=primary_source,
            primary_source=primary_source,
            source_confidence="high" if facts.get("official_policy_search") else "medium" if available else "low",
            source_trace=trace,
            degraded=not available,
            degrade_reason=None if available else "no_data",
        ).model_dump(mode="json")
