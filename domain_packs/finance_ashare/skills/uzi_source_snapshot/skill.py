from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import akshare as ak

from research_system.adapters.vendors.akshare_catalyst_adapter import AkShareCatalystAdapter
from research_system.adapters.vendors.akshare_sector_adapter import AkShareSectorAdapter
from research_system.adapters.vendors.akshare_utils import call_with_retry, coerce_numeric, normalize_value
from research_system.adapters.vendors.market_router import MarketRouter
from research_system.schemas.snapshots import SnapshotSource, VerifiedSnapshot
from research_system.adapters.vendors.provider_registry import VendorProviderRegistry
from domain_packs.finance_ashare.skills.fundamental_snapshot.skill import FundamentalSnapshotSkill
from domain_packs.finance_ashare.skills.market_snapshot.skill import MarketSnapshotSkill
from research_system.adapters.vendors.uzi_contest_adapter import UziContestAdapter
from research_system.adapters.vendors.uzi_browser_adapter import UziBrowserAdapter
from research_system.adapters.vendors.uzi_hottrend_adapter import UziHotTrendAdapter
from research_system.adapters.vendors.uzi_news_adapter import UziNewsAdapter
from research_system.adapters.vendors.uzi_web_search_adapter import UziWebSearchAdapter
from research_system.adapters.vendors.yfinance_fundamental_adapter import YFinanceFundamentalAdapter


class UziSourceSnapshotSkill:
    """Finance Pack source adapter for UZI's non-core dimensions.

    Each dimension is still an independent Snapshot Artifact. Unsupported
    sources return an explicit degraded record so the Planner and UI can show
    the gap instead of silently pretending that the dimension was collected.
    """

    skill_name = "uzi_source_snapshot_skill"
    snapshot_type = ""
    _SECTOR_ALIASES = (
        (("酒", "饮料"), "酿酒行业"),
        (("银行",), "银行"),
        (("证券", "资本市场服务"), "证券"),
        (("保险",), "保险"),
        (("房地产",), "房地产开发"),
        (("半导体",), "半导体"),
        (("电力",), "电力行业"),
        (("煤炭",), "煤炭行业"),
        (("钢铁", "黑色金属冶炼"), "钢铁行业"),
    )

    def __init__(self, provider_registry: VendorProviderRegistry | None = None) -> None:
        self.provider_registry = provider_registry

    def run(self, *, query: str, symbol: str | None = None) -> dict[str, Any]:
        snapshot_date = date.today().isoformat()
        provider = self._selected_provider()
        try:
            facts = self._collect(query=query, symbol=symbol)
            source_meta = dict(facts.pop("_source", {}) or {})
            source_trace = list(facts.pop("_source_trace", []) or [])
            source = SnapshotSource(
                skill=self.skill_name,
                vendor=str(source_meta.get("vendor") or (provider.vendor_name if provider is not None else "finance_pack")),
                from_cache=False,
                provider_id=str(source_meta.get("provider_id") or (provider.provider_id if provider is not None else f"finance_pack.{self.snapshot_type or 'unknown'}")),
                source_id=str(source_meta.get("source_id") or (provider.source_id if provider is not None else "finance_pack")),
                chain_position=int(source_meta.get("chain_position") or 0),
                dim=self.snapshot_type or "unknown",
            )
            availability = str((facts or {}).get("availability") or "").lower()
            if not facts or availability in {"data_gap", "degraded"}:
                return self._degraded(
                    query=query,
                    symbol=symbol,
                    snapshot_date=snapshot_date,
                    source=source,
                    reason=str((facts or {}).get("degrade_reason") or "source_data_gap"),
                    facts=facts or {},
                    source_trace=source_trace,
                )
            return VerifiedSnapshot(
                snapshot_type=self.snapshot_type,
                symbol=symbol,
                query=query,
                snapshot_date=snapshot_date,
                facts=facts,
                source=source,
                primary_source=source,
                source_confidence="medium",
                source_trace=source_trace,
            ).model_dump(mode="json")
        except Exception as exc:
            source = SnapshotSource(
                skill=self.skill_name,
                vendor=provider.vendor_name if provider is not None else "finance_pack",
                from_cache=False,
                provider_id=provider.provider_id if provider is not None else f"finance_pack.{self.snapshot_type or 'unknown'}",
                source_id=provider.source_id if provider is not None else "finance_pack",
                chain_position=0,
                dim=self.snapshot_type or "unknown",
            )
            return self._degraded(
                query=query,
                symbol=symbol,
                snapshot_date=snapshot_date,
                source=source,
                reason=f"provider_failure:{exc.__class__.__name__}",
                facts={
                    "availability": "data_gap",
                    "error_type": exc.__class__.__name__,
                },
                source_trace=[{"source_id": source.source_id, "status": "provider_unavailable", "error_type": exc.__class__.__name__}],
            )

    def _collect(self, *, query: str, symbol: str | None) -> dict[str, Any]:
        if not symbol:
            return {"availability": "data_gap", "degrade_reason": "symbol_missing"}
        if self.snapshot_type == "kline":
            snapshot = MarketSnapshotSkill(provider_registry=self.provider_registry).run(
                query=query,
                symbol=symbol,
                history_days=365,
            )
            facts = dict(snapshot.get("facts") or {})
            payload = dict(facts.get("market_hist") or {})
            return self._from_snapshot(
                snapshot,
                {
                    "history": payload,
                    "market_overview": facts.get("market_overview") or {},
                    "availability": "available" if payload else "data_gap",
                    "data_gap": [] if payload else ["kline_missing"],
                },
            )
        if self.snapshot_type == "valuation":
            market_snapshot = MarketSnapshotSkill(provider_registry=self.provider_registry).run(query=query, symbol=symbol)
            market_facts = dict(market_snapshot.get("facts") or {})
            market = dict(market_facts.get("market_overview") or {})
            valuation = dict(market.get("valuation") or {})
            industry_context = self._industry_context(symbol, query=query)
            industry = str(industry_context.get("industry") or "").strip()
            route = MarketRouter().classify(symbol=symbol, query=query)
            market_pe, pe_date, pe_column = self._cninfo_market_weighted_pe() if route.market == "cn_equity" else (None, None, None)
            browser = self._safe_browser(source_ids=("legulegu",), symbol=symbol, query=query)
            gaps: list[str] = []
            if market_pe is not None:
                valuation["industry_pe"] = market_pe
                valuation["industry_pe_fallback_reason"] = (
                    f"行业 {industry} 未匹配 · 使用 cninfo 市场加权均值"
                    if industry
                    else "basic.industry 缺失 · 使用 cninfo 市场加权均值"
                )
                valuation["industry_pe_source"] = {
                    "provider": "cninfo_via_akshare",
                    "date": pe_date,
                    "column": pe_column,
                    "scope": "market_weighted_average",
                }
            else:
                gaps.append("industry_pe_fallback_missing")
            availability = (
                "available"
                if valuation and not gaps
                else "degraded"
                if valuation
                else "data_gap"
            )
            return self._from_snapshot(market_snapshot, {
                "valuation": valuation,
                "industry": industry or None,
                "browser_evidence": browser.get("evidence") or {},
                "availability": availability,
                "data_gap": gaps if valuation else ["valuation_snapshot_missing", *gaps],
                "degrade_reason": gaps[0] if gaps else None,
                "_source_trace": [*list(market_snapshot.get("source_trace") or []), *list(browser.get("source_trace") or [])],
            })
        if self.snapshot_type == "governance":
            fundamental = FundamentalSnapshotSkill(provider_registry=self.provider_registry).run(query=query, symbol=symbol)
            facts = dict(fundamental.get("facts") or {})
            result = {
                "info": dict(facts.get("info") or {}),
                "banking_metrics": dict(facts.get("banking_metrics") or {}),
                "bank_peer_comparison": dict(facts.get("bank_peer_comparison") or {}),
            }
            search = self._safe_search(query=f"{query} 公司治理 股权质押 违规 减持", dimension="governance")
            browser = self._safe_browser(source_ids=("ths_f10", "xueqiu_f10"), symbol=symbol, query=query)
            result["governance_evidence"] = search.get("results") or []
            result["browser_evidence"] = browser.get("evidence") or {}
            return self._from_snapshot(fundamental, {
                **result,
                "availability": "available" if any(result.values()) else "data_gap",
                "data_gap": [] if any(result.values()) else ["governance_snapshot_missing"],
                "_source_trace": [*list(fundamental.get("source_trace") or []), *list(search.get("source_trace") or []), *list(browser.get("source_trace") or [])],
            })
        if self.snapshot_type == "research":
            try:
                reports = AkShareCatalystAdapter().get_symbol_research_reports(symbol=symbol, limit=10)
                trace = [{"source_id": "em_data", "status": "available" if reports else "no_data", "count": len(reports)}]
            except Exception as exc:
                reports = []
                trace = [{"source_id": "em_data", "status": "provider_unavailable", "error_type": exc.__class__.__name__}]
            search = self._safe_search(query=f"{query} 研报 评级 目标价", dimension="research")
            browser = self._safe_browser(source_ids=("stockstar",), symbol=symbol, query=query)
            return {
                "reports": reports,
                "web_research": search.get("results") or [],
                "browser_evidence": browser.get("evidence") or {},
                "availability": "available" if reports or search.get("results") or browser.get("evidence") else "data_gap",
                "data_gap": [] if reports or search.get("results") or browser.get("evidence") else ["research_reports_missing"],
                "_source": {"vendor": "akshare", "provider_id": "akshare_research", "source_id": "em_data"},
                "_source_trace": [*trace, *list(search.get("source_trace") or []), *list(browser.get("source_trace") or [])],
            }
        if self.snapshot_type == "macro":
            adapter = AkShareSectorAdapter()
            try:
                benchmark = adapter.get_benchmark_snapshot(
                    start_date=(date.today() - timedelta(days=30)).strftime("%Y%m%d"),
                    end_date=date.today().strftime("%Y%m%d"),
                )
                trace = [{"source_id": "em_push2", "status": "available" if benchmark else "no_data"}]
            except Exception as exc:
                benchmark = {}
                trace = [{"source_id": "em_push2", "status": "provider_unavailable", "error_type": exc.__class__.__name__}]
            search = self._safe_search(query=f"{query} 利率 汇率 宏观 政策 大宗商品", dimension="macro")
            news = self._safe_news(symbol=symbol, name=query)
            available = bool(benchmark or search.get("results") or news.get("sources"))
            return {
                "benchmark": benchmark,
                "web_search": search.get("results") or [],
                "news_multi_source": news.get("sources") or {},
                "availability": "available" if available else "data_gap",
                "data_gap": [] if available else ["macro_evidence_missing"],
                "_source": {"vendor": "uzi_macro", "provider_id": "uzi_macro_multi", "source_id": "ddgs" if search.get("results") else "em_push2"},
                "_source_trace": [*trace, *list(search.get("source_trace") or []), *list(news.get("source_trace") or [])],
            }
        if self.snapshot_type == "industry":
            return self._industry_context(symbol, query=query)
        if self.snapshot_type == "chain":
            context = self._industry_context(symbol, query=query)
            resolved = dict(context.get("resolved_sector") or {})
            sector = {}
            if context.get("industry"):
                try:
                    sector = AkShareSectorAdapter().get_sector_snapshot(
                        query=str(context.get("industry") or symbol),
                        sector_name=str(resolved.get("sector_name") or ""),
                        sector_type=str(resolved.get("sector_type") or "industry"),
                    )
                except Exception:
                    sector = {}
            search = self._safe_search(query=f"{query} 产业链 上游 下游 供应商 客户", dimension="chain")
            browser = self._safe_browser(source_ids=("iwencai", "ths_f10"), symbol=symbol, query=query)
            available = bool(sector or search.get("results") or browser.get("evidence"))
            return {
                "industry": context.get("industry"),
                "resolved_sector": resolved,
                "chain_snapshot": sector,
                "web_search": search.get("results") or [],
                "browser_evidence": browser.get("evidence") or {},
                "availability": "available" if available else "data_gap",
                "data_gap": [] if available else ["chain_snapshot_missing"],
                "_source": {"vendor": "uzi_chain", "provider_id": "uzi_chain_multi", "source_id": "em_data" if sector else "ddgs"},
                "_source_trace": [*list(context.get("_source_trace") or []), *list(search.get("source_trace") or []), *list(browser.get("source_trace") or [])],
            }
        if self.snapshot_type == "materials":
            rows = self._futures_rows(("RB0", "CU0", "AL0"))
            search = self._safe_search(query=f"{query} 原材料 价格 库存 基差", dimension="materials")
            available = bool(rows or search.get("results"))
            return {
                "contracts": rows,
                "web_search": search.get("results") or [],
                "availability": "available" if available else "data_gap",
                "data_gap": [] if available else ["materials_provider_missing"],
                "_source": {"vendor": "uzi_materials", "provider_id": "uzi_materials_multi", "source_id": "akshare" if rows else "ddgs"},
                "_source_trace": [{"source_id": "akshare", "status": "available" if rows else "no_data", "count": len(rows)}, *list(search.get("source_trace") or [])],
            }
        if self.snapshot_type == "futures":
            rows = self._futures_rows(("RB0", "CU0", "AL0", "SC0"))
            search = self._safe_search(query=f"{query} 期货 库存 仓单 现货 基差", dimension="futures")
            available = bool(rows or search.get("results"))
            return {
                "contracts": rows,
                "web_search": search.get("results") or [],
                "availability": "available" if available else "data_gap",
                "data_gap": [] if available else ["futures_provider_missing"],
                "_source": {"vendor": "uzi_futures", "provider_id": "uzi_futures_multi", "source_id": "akshare" if rows else "ddgs"},
                "_source_trace": [{"source_id": "akshare", "status": "available" if rows else "no_data", "count": len(rows)}, *list(search.get("source_trace") or [])],
            }
        if self.snapshot_type == "moat":
            fundamental = FundamentalSnapshotSkill(provider_registry=self.provider_registry).run(query=query, symbol=symbol)
            facts = dict(fundamental.get("facts") or {})
            moat_inputs = {
                "company_info": dict(facts.get("info") or {}),
                "financial_abstract": dict(facts.get("financial_abstract") or {}),
                "banking_metrics": dict(facts.get("banking_metrics") or {}),
            }
            search = self._safe_search(query=f"{query} 竞争壁垒 市占率 品牌 定价权 专利", dimension="moat")
            browser = self._safe_browser(source_ids=("ths_f10",), symbol=symbol, query=query)
            moat_inputs["web_search"] = search.get("results") or []
            moat_inputs["browser_evidence"] = browser.get("evidence") or {}
            return self._from_snapshot(fundamental, {
                **moat_inputs,
                "availability": "available" if any(moat_inputs.values()) else "data_gap",
                "data_gap": [] if any(moat_inputs.values()) else ["moat_inputs_missing"],
                "_source_trace": [*list(fundamental.get("source_trace") or []), *list(search.get("source_trace") or []), *list(browser.get("source_trace") or [])],
            })
        if self.snapshot_type == "trap":
            try:
                catalyst_news = AkShareCatalystAdapter().get_symbol_news(symbol=symbol, limit=10)
                trace = [{"source_id": "em_data", "status": "available" if catalyst_news else "no_data", "count": len(catalyst_news)}]
            except Exception as exc:
                catalyst_news = []
                trace = [{"source_id": "em_data", "status": "provider_unavailable", "error_type": exc.__class__.__name__}]
            direct_news = self._safe_news(symbol=symbol, name=query)
            search = self._safe_search(query=f"{query} 风险 骗局 荐股 庄家 股吧", dimension="trap")
            hot = self._safe_hottrend(name=query, symbol=symbol)
            available = bool(catalyst_news or direct_news.get("sources") or search.get("results") or hot.get("total_hits"))
            return {
                "news": catalyst_news,
                "news_multi_source": direct_news.get("sources") or {},
                "web_search": search.get("results") or [],
                "hot_trend_mentions": hot,
                "availability": "available" if available else "data_gap",
                "data_gap": [] if available else ["trap_news_missing"],
                "_source": {"vendor": "uzi_trap", "provider_id": "uzi_trap_multi", "source_id": "ddgs" if search.get("results") else "em_data"},
                "_source_trace": [*trace, *list(direct_news.get("source_trace") or []), *list(search.get("source_trace") or []), *list(hot.get("source_trace") or [])],
            }
        if self.snapshot_type == "peers":
            fundamental = FundamentalSnapshotSkill(provider_registry=self.provider_registry).run(query=query, symbol=symbol)
            facts = dict(fundamental.get("facts") or {})
            comparison = dict(facts.get("bank_peer_comparison") or {})
            if comparison:
                return {
                    "comparison": comparison,
                    "availability": "available",
                    "data_gap": [],
                }
            context = self._industry_context(symbol, query=query)
            resolved = dict(context.get("resolved_sector") or {})
            try:
                sector = AkShareSectorAdapter().get_sector_snapshot(
                    query=str(context.get("industry") or symbol),
                    sector_name=str(resolved.get("sector_name") or ""),
                    sector_type=str(resolved.get("sector_type") or "industry"),
                ) if context.get("industry") else {}
            except Exception:
                sector = {}
            sector_facts = dict(sector.get("facts") or {}) if isinstance(sector, dict) else {}
            peers = [
                dict(row)
                for row in list(sector_facts.get("top_constituents") or [])
                if isinstance(row, dict)
                and str(row.get("symbol") or row.get("代码") or "") != symbol
            ][:10]
            if peers:
                return self._from_snapshot(fundamental, {
                    "comparison": {
                        "industry": context.get("industry"),
                        "peers": peers,
                        "fallback_used": False,
                    },
                    "availability": "available",
                    "data_gap": [],
                })
            info = dict(facts.get("info") or {})
            search = self._safe_search(query=f"{query} 同行业 可比公司 竞争对手", dimension="peers")
            browser = self._safe_browser(source_ids=("iwencai", "ths_f10"), symbol=symbol, query=query)
            fallback_reason = (
                "basic.industry 缺失 · 仅返回公司自身"
                if not context.get("industry")
                else "行业成分数据缺失 · 仅返回公司自身"
            )
            return self._from_snapshot(fundamental, {
                "comparison": {
                    "industry": context.get("industry"),
                    "peers": [],
                    "self_only": [
                        {
                            "symbol": symbol,
                            "name": info.get("股票简称") or info.get("名称") or info.get("公司名称"),
                        }
                    ],
                    "fallback_used": True,
                    "fallback_reason": fallback_reason,
                    "web_search": search.get("results") or [],
                    "browser_evidence": browser.get("evidence") or {},
                },
                "availability": "available" if search.get("results") or browser.get("evidence") else "degraded",
                "data_gap": [] if search.get("results") or browser.get("evidence") else ["peer_group_missing"],
                "degrade_reason": None if search.get("results") or browser.get("evidence") else fallback_reason,
                "_source_trace": [*list(fundamental.get("source_trace") or []), *list(search.get("source_trace") or []), *list(browser.get("source_trace") or [])],
            })
        if self.snapshot_type == "fund_holders":
            fetcher = getattr(ak, "stock_fund_stock_holder", None)
            if not callable(fetcher):
                return {"availability": "data_gap", "data_gap": ["fund_holders_provider_missing"]}
            try:
                frame = call_with_retry(lambda: fetcher(symbol=symbol), retries=1, delay_seconds=0.2)
            except Exception as exc:
                return {
                    "availability": "data_gap",
                    "data_gap": ["fund_holders_provider_unavailable"],
                    "degrade_reason": "fund_holders_provider_unavailable",
                    "_source": {"vendor": "akshare", "provider_id": "akshare_fund_holders", "source_id": "em_data"},
                    "_source_trace": [{"source_id": "em_data", "status": "provider_unavailable", "error_type": exc.__class__.__name__}],
                }
            rows = [
                {str(key): normalize_value(value) for key, value in row.items()}
                for row in frame.to_dict("records")
            ] if frame is not None and not getattr(frame, "empty", True) else []
            return {
                "holders": rows,
                "active_funds_count": len(rows),
                "availability": "available" if rows else "data_gap",
                "data_gap": [] if rows else ["fund_holders_missing"],
                "_source": {"vendor": "akshare", "provider_id": "akshare_fund_holders", "source_id": "em_data"},
                "_source_trace": [{"source_id": "em_data", "status": "available" if rows else "no_data", "count": len(rows)}],
            }
        if self.snapshot_type == "similar_stocks":
            context = self._industry_context(symbol, query=query)
            resolved = dict(context.get("resolved_sector") or {})
            try:
                sector = AkShareSectorAdapter().get_sector_snapshot(
                    query=str(context.get("industry") or symbol),
                    sector_name=str(resolved.get("sector_name") or ""),
                    sector_type=str(resolved.get("sector_type") or "industry"),
                ) if context.get("industry") else {}
            except Exception:
                sector = {}
            facts = dict(sector.get("facts") or {}) if isinstance(sector, dict) else {}
            similar = [
                dict(row)
                for row in list(facts.get("top_constituents") or [])
                if isinstance(row, dict)
                and str(row.get("symbol") or row.get("代码") or "") != symbol
            ][:4]
            return {
                "industry": context.get("industry"),
                "similar_stocks": similar,
                "availability": "available" if similar else "data_gap",
                "data_gap": [] if similar else ["similar_stocks_missing"],
                "_source": {"vendor": "akshare", "provider_id": "akshare_similar_stocks", "source_id": "em_data"},
                "_source_trace": [{"source_id": "em_data", "status": "available" if similar else "no_data", "count": len(similar)}],
            }
        if self.snapshot_type == "contests":
            return self._collect_contests(symbol)
        return {
            "availability": "data_gap",
            "data_gap": [f"{self.snapshot_type}_provider_not_configured"],
            "degrade_reason": "provider_not_configured",
        }

    def _selected_provider(self):
        if self.provider_registry is None or not self.snapshot_type:
            return None
        chain = self.provider_registry.get_provider_chain(self.snapshot_type, "cn_equity")
        return chain[0] if chain else None

    def _collect_contests(self, symbol: str) -> dict[str, Any]:
        settings = self.provider_registry.settings if self.provider_registry is not None else None
        if settings is None:
            return {
                "availability": "data_gap",
                "data_gap": ["settings_missing"],
                "degrade_reason": "settings_missing",
            }
        facts = UziContestAdapter(settings).get_snapshot(symbol=symbol)
        trace = list(facts.pop("source_trace", []) or [])
        primary = next((row for row in trace if row.get("status") == "available"), None)
        facts["_source"] = {
            "vendor": "uzi_contests",
            "provider_id": "uzi_contests_multi",
            "source_id": str((primary or {}).get("source_id") or "xueqiu_f10"),
        }
        facts["_source_trace"] = trace
        return facts

    def _industry_context(self, symbol: str, *, query: str = "") -> dict[str, Any]:
        route = MarketRouter().classify(symbol=symbol, query=query)
        info: dict[str, Any] = {}
        trace: list[dict[str, Any]] = []
        if route.market == "cn_equity":
            try:
                info_df = call_with_retry(lambda: ak.stock_individual_info_em(symbol=symbol))
                info = {
                    str(row.get("item")): normalize_value(row.get("value"))
                    for row in info_df.to_dict("records")
                    if row.get("item")
                }
                trace.append({"source_id": "em_data", "status": "available" if info else "no_data"})
            except Exception as exc:
                trace.append({"source_id": "em_data", "status": "provider_unavailable", "error_type": exc.__class__.__name__})
            if not (info.get("行业") or info.get("所属行业")):
                try:
                    profile = call_with_retry(lambda: ak.stock_profile_cninfo(symbol=symbol))
                    profile_info = {
                        str(key): normalize_value(value)
                        for key, value in profile.iloc[0].to_dict().items()
                    } if profile is not None and not profile.empty else {}
                    info = {**profile_info, **info}
                    trace.append({"source_id": "cninfo", "status": "available" if profile_info else "no_data"})
                except Exception as exc:
                    trace.append({"source_id": "cninfo", "status": "provider_unavailable", "error_type": exc.__class__.__name__})
        else:
            try:
                foreign = YFinanceFundamentalAdapter().get_fundamental_snapshot(symbol=symbol)
                info = dict(foreign.get("info") or {})
                trace.append({"source_id": "yfinance", "status": "available" if info else "no_data"})
            except Exception as exc:
                trace.append({"source_id": "yfinance", "status": "provider_unavailable", "error_type": exc.__class__.__name__})
        industry = str(info.get("行业") or info.get("所属行业") or info.get("industry") or info.get("sector") or "").strip()
        search = self._safe_search(query=f"{query or symbol} 所属行业 行业分析", dimension="industry")
        browser = self._safe_browser(source_ids=("iwencai", "legulegu"), symbol=symbol, query=query or symbol)
        if not industry:
            return {
                "industry": None,
                "web_search": search.get("results") or [],
                "browser_evidence": browser.get("evidence") or {},
                "availability": "available" if search.get("results") or browser.get("evidence") else "data_gap",
                "data_gap": [] if search.get("results") or browser.get("evidence") else ["industry_missing"],
                "degrade_reason": None if search.get("results") or browser.get("evidence") else "industry_missing",
                "_source": {"vendor": "ddgs", "provider_id": "ddgs_industry", "source_id": "ddgs"},
                "_source_trace": [*trace, *list(search.get("source_trace") or []), *list(browser.get("source_trace") or [])],
            }
        resolved = self._known_sector(industry)
        if not resolved:
            try:
                resolved = AkShareSectorAdapter().resolve_sector(query=industry)
            except Exception:
                resolved = {}
        return {
            "industry": industry,
            "company_info": info,
            "resolved_sector": resolved,
            "web_search": search.get("results") or [],
            "browser_evidence": browser.get("evidence") or {},
            "availability": "available",
            "_source": {
                "vendor": "akshare" if route.market == "cn_equity" else "yfinance",
                "provider_id": "akshare_industry" if route.market == "cn_equity" else "yfinance_industry",
                "source_id": next(
                    (str(row.get("source_id")) for row in trace if row.get("status") == "available"),
                    "em_data" if route.market == "cn_equity" else "yfinance",
                ),
            },
            "_source_trace": [*trace, *list(search.get("source_trace") or []), *list(browser.get("source_trace") or [])],
        }

    @classmethod
    def _known_sector(cls, industry: str) -> dict[str, Any]:
        normalized = str(industry or "").strip()
        for keywords, sector_name in cls._SECTOR_ALIASES:
            if any(keyword in normalized for keyword in keywords):
                return {
                    "sector_name": sector_name,
                    "sector_type": "industry",
                    "catalog_source": "deterministic_industry_alias",
                    "matched_query": normalized,
                    "exact": normalized == sector_name,
                }
        return {}

    def _safe_search(self, *, query: str, dimension: str) -> dict[str, Any]:
        try:
            payload = UziWebSearchAdapter().search(query=query, dimension=dimension, max_results=8)
            return {
                **payload,
                "source_trace": list(payload.get("source_trace") or [{"source_id": "ddgs", "status": "available", "count": payload.get("result_count", 0)}]),
            }
        except Exception as exc:
            return {
                "results": [],
                "source_trace": [{"source_id": "ddgs", "status": "provider_unavailable", "error_type": exc.__class__.__name__}],
            }

    @staticmethod
    def _safe_news(*, symbol: str, name: str) -> dict[str, Any]:
        try:
            return UziNewsAdapter().get_news(symbol=symbol, name=name, limit=20)
        except Exception as exc:
            return {
                "sources": {},
                "source_trace": [{"source_id": "uzi_news_http", "status": "provider_unavailable", "error_type": exc.__class__.__name__}],
            }

    @staticmethod
    def _safe_hottrend(*, name: str, symbol: str) -> dict[str, Any]:
        try:
            return UziHotTrendAdapter().get_mentions(name=name, extra_keywords=[symbol])
        except Exception as exc:
            return {
                "mentions": {},
                "total_hits": 0,
                "source_trace": [{"source_id": "hottrend", "status": "provider_unavailable", "error_type": exc.__class__.__name__}],
            }

    def _safe_browser(self, *, source_ids: tuple[str, ...], symbol: str, query: str) -> dict[str, Any]:
        settings = self.provider_registry.settings if self.provider_registry is not None else None
        if settings is None:
            return {
                "evidence": {},
                "source_trace": [
                    {"source_id": source_id, "status": "disabled", "reason": "settings_missing"}
                    for source_id in source_ids
                ],
            }
        return UziBrowserAdapter(settings).collect_many(source_ids=source_ids, symbol=symbol, query=query)

    @staticmethod
    def _from_snapshot(snapshot: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any]:
        source = dict(snapshot.get("source") or {})
        explicit_trace = list(facts.pop("_source_trace", []) or [])
        facts["_source"] = {
            "vendor": source.get("vendor") or "finance_pack",
            "provider_id": source.get("provider_id") or "finance_pack",
            "source_id": source.get("source_id") or "finance_pack",
            "chain_position": source.get("chain_position") or 0,
        }
        facts["_source_trace"] = explicit_trace or list(snapshot.get("source_trace") or [])
        return facts

    @staticmethod
    def _cninfo_market_weighted_pe() -> tuple[float | None, str | None, str | None]:
        for offset in range(1, 8):
            snapshot_date = date.today() - timedelta(days=offset)
            try:
                frame = call_with_retry(
                    lambda snapshot_date=snapshot_date: ak.stock_industry_pe_ratio_cninfo(
                        symbol="证监会行业分类",
                        date=snapshot_date.strftime("%Y%m%d"),
                    ),
                    retries=1,
                    delay_seconds=0.2,
                )
            except Exception:
                continue
            if frame is None or getattr(frame, "empty", True):
                continue
            pe_column = next(
                (str(column) for column in frame.columns if "市盈率" in str(column) and "加权" in str(column)),
                None,
            )
            if not pe_column:
                continue
            values = [
                number
                for number in (coerce_numeric(value) for value in frame[pe_column].tolist())
                if number is not None and 0 < float(number) < 500
            ]
            if values:
                return (
                    round(sum(float(value) for value in values) / len(values), 2),
                    snapshot_date.isoformat(),
                    pe_column,
                )
        return None, None, None

    @staticmethod
    def _futures_rows(symbols: tuple[str, ...]) -> list[dict[str, Any]]:
        fetcher = getattr(ak, "futures_zh_daily_sina", None)
        if not callable(fetcher):
            return []
        rows: list[dict[str, Any]] = []
        for contract in symbols:
            try:
                frame = call_with_retry(lambda contract=contract: fetcher(symbol=contract), retries=1, delay_seconds=0.2)
            except Exception:
                continue
            if frame is None or getattr(frame, "empty", True):
                continue
            latest = frame.tail(1).to_dict("records")
            if latest:
                rows.append({"contract": contract, **{str(k): normalize_value(v) for k, v in latest[0].items()}})
        return rows

    def _degraded(
        self,
        *,
        query: str,
        symbol: str | None,
        snapshot_date: str,
        source: SnapshotSource,
        reason: str,
        facts: dict[str, Any],
        source_trace: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return VerifiedSnapshot(
            snapshot_type=self.snapshot_type,
            symbol=symbol,
            query=query,
            snapshot_date=snapshot_date,
            facts={"availability": "data_gap", **facts},
            source=source,
            primary_source=source,
            missing_fields=[self.snapshot_type],
            source_confidence="low",
            source_trace=source_trace or [],
            degraded=True,
            degrade_reason=reason,
        ).model_dump(mode="json")


def _skill_type(snapshot_type: str):
    return type(
        f"{snapshot_type.title().replace('_', '')}SnapshotSkill",
        (UziSourceSnapshotSkill,),
        {"snapshot_type": snapshot_type, "skill_name": f"uzi_{snapshot_type}_snapshot_skill"},
    )


KlineSnapshotSkill = _skill_type("kline")
MacroSnapshotSkill = _skill_type("macro")
PeersSnapshotSkill = _skill_type("peers")
ResearchSnapshotSkill = _skill_type("research")
IndustrySnapshotSkill = _skill_type("industry")
MaterialsSnapshotSkill = _skill_type("materials")
FuturesSnapshotSkill = _skill_type("futures")
ValuationSnapshotSkill = _skill_type("valuation")
GovernanceSnapshotSkill = _skill_type("governance")
MoatSnapshotSkill = _skill_type("moat")
TrapSnapshotSkill = _skill_type("trap")
ContestsSnapshotSkill = _skill_type("contests")
ChainSnapshotSkill = _skill_type("chain")
FundHoldersSnapshotSkill = _skill_type("fund_holders")
SimilarStocksSnapshotSkill = _skill_type("similar_stocks")


__all__ = [
    "UziSourceSnapshotSkill",
    "KlineSnapshotSkill",
    "MacroSnapshotSkill",
    "PeersSnapshotSkill",
    "ResearchSnapshotSkill",
    "IndustrySnapshotSkill",
    "MaterialsSnapshotSkill",
    "FuturesSnapshotSkill",
    "ValuationSnapshotSkill",
    "GovernanceSnapshotSkill",
    "MoatSnapshotSkill",
    "TrapSnapshotSkill",
    "ContestsSnapshotSkill",
    "ChainSnapshotSkill",
    "FundHoldersSnapshotSkill",
    "SimilarStocksSnapshotSkill",
]
