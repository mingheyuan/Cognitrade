from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import akshare as ak

from research_system.adapters.vendors.akshare_catalyst_adapter import (
    AkShareCatalystAdapter,
)
from research_system.adapters.vendors.akshare_sector_adapter import AkShareSectorAdapter
from research_system.adapters.vendors.akshare_utils import (
    call_with_retry,
    coerce_numeric,
    normalize_value,
)
from research_system.adapters.vendors.market_router import MarketRouter
from research_system.schemas.snapshots import SnapshotSource, VerifiedSnapshot
from research_system.adapters.vendors.provider_registry import VendorProviderRegistry
from domain_packs.finance_ashare.skills.fundamental_snapshot.skill import (
    FundamentalSnapshotSkill,
)
from domain_packs.finance_ashare.skills.market_snapshot.skill import MarketSnapshotSkill
from research_system.adapters.vendors.uzi_contest_adapter import UziContestAdapter
from research_system.adapters.vendors.uzi_browser_adapter import UziBrowserAdapter
from research_system.adapters.vendors.uzi_hottrend_adapter import UziHotTrendAdapter
from research_system.adapters.vendors.uzi_news_adapter import UziNewsAdapter
from research_system.adapters.vendors.uzi_web_search_adapter import UziWebSearchAdapter
from research_system.adapters.vendors.yfinance_fundamental_adapter import (
    YFinanceFundamentalAdapter,
)
from research_system.adapters.vendors.uzi_global_peer_adapter import (
    fetch_global_peer_comparison,
    global_peers_enabled,
)


_UZI_INDUSTRY_MATERIALS: dict[str, tuple[tuple[str, str], ...]] = {
    "光学光电子": (("玻璃", "FG0"), ("铜", "CU0")),
    "半导体": (("铜", "CU0"), ("黄金", "AU0"), ("白银", "AG0")),
    "医药生物": (("黄金", "AU0"),),
    "电池": (("碳酸锂", "LC0"), ("镍", "NI0"), ("钴", "")),
    "钢铁": (("铁矿石", "I0"), ("焦炭", "J0"), ("螺纹钢", "RB0")),
    "建材": (("水泥", ""), ("玻璃", "FG0"), ("沥青", "BU0")),
    "化工": (("原油", "SC0"), ("聚丙烯", "PP0"), ("PVC", "V0"), ("甲醇", "MA0")),
    "白酒": (("玉米", "C0"), ("大豆", "A0")),
    "养殖业": (("豆粕", "M0"), ("玉米", "C0"), ("生猪", "LH0")),
    "农业": (("大豆", "A0"), ("玉米", "C0"), ("棕榈油", "P0")),
    "工业金属": (("沪铝", "AL0"), ("沪铜", "CU0"), ("沪锌", "ZN0")),
    "有色金属": (("沪铝", "AL0"), ("沪铜", "CU0"), ("沪镍", "NI0")),
    "贵金属": (("黄金", "AU0"), ("白银", "AG0")),
    "能源金属": (("碳酸锂", "LC0"), ("镍", "NI0")),
    "小金属": (("沪锡", "SN0"), ("沪铅", "PB0")),
    "煤炭开采": (("焦煤", "JM0"), ("动力煤", "ZC0")),
    "煤炭": (("焦煤", "JM0"), ("动力煤", "ZC0")),
    "焦炭": (("焦炭", "J0"), ("焦煤", "JM0")),
    "油气开采": (("原油", "SC0"),),
}


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

    def run(
        self,
        *,
        query: str,
        symbol: str | None = None,
        dependencies: dict[str, dict[str, Any]] | None = None,
        as_of_date: date | None = None,
        cutoff_at: datetime | None = None,
    ) -> dict[str, Any]:
        del cutoff_at
        snapshot_date = (as_of_date or date.today()).isoformat()
        provider = self._selected_provider()
        try:
            facts = self._collect(
                query=query,
                symbol=symbol,
                dependencies=dependencies or {},
                as_of_date=as_of_date,
            )
            source_meta = dict(facts.pop("_source", {}) or {})
            source_trace = list(facts.pop("_source_trace", []) or [])
            source = SnapshotSource(
                skill=self.skill_name,
                vendor=str(
                    source_meta.get("vendor")
                    or (
                        provider.vendor_name if provider is not None else "finance_pack"
                    )
                ),
                from_cache=False,
                provider_id=str(
                    source_meta.get("provider_id")
                    or (
                        provider.provider_id
                        if provider is not None
                        else f"finance_pack.{self.snapshot_type or 'unknown'}"
                    )
                ),
                source_id=str(
                    source_meta.get("source_id")
                    or (provider.source_id if provider is not None else "finance_pack")
                ),
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
                    reason=str(
                        (facts or {}).get("degrade_reason") or "source_data_gap"
                    ),
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
                provider_id=provider.provider_id
                if provider is not None
                else f"finance_pack.{self.snapshot_type or 'unknown'}",
                source_id=provider.source_id
                if provider is not None
                else "finance_pack",
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
                source_trace=[
                    {
                        "source_id": source.source_id,
                        "status": "provider_unavailable",
                        "error_type": exc.__class__.__name__,
                    }
                ],
            )

    def _collect(
        self,
        *,
        query: str,
        symbol: str | None,
        dependencies: dict[str, dict[str, Any]] | None = None,
        as_of_date: date | None = None,
    ) -> dict[str, Any]:
        dependencies = dependencies or {}
        if not symbol:
            return {"availability": "data_gap", "degrade_reason": "symbol_missing"}
        if self.snapshot_type == "contests" and as_of_date is not None:
            return {
                "availability": "data_gap",
                "data_gap": ["historical_contest_archive_unavailable"],
                "degrade_reason": "historical_contest_archive_unavailable",
                "_source": {
                    "vendor": "uzi_contests",
                    "provider_id": "uzi_contests_multi",
                    "source_id": "contest_archive",
                },
                "_source_trace": [
                    {
                        "source_id": "contest_archive",
                        "status": "not_configured",
                        "reason_code": "current_only",
                    }
                ],
            }
        if self.snapshot_type == "kline":
            snapshot = MarketSnapshotSkill(
                provider_registry=self.provider_registry
            ).run(
                query=query,
                symbol=symbol,
                # True Weinstein Stage analysis compares MA200 with its value
                # 60 trading sessions ago.  365 calendar days often yield only
                # ~240 bars, so request enough history for the full rule input.
                history_days=550,
                as_of_date=as_of_date,
            )
            facts = dict(snapshot.get("facts") or {})
            payload = dict(facts.get("market_hist") or {})
            overview = dict(facts.get("market_overview") or {})
            overview.pop("valuation_history", None)
            return self._from_snapshot(
                snapshot,
                {
                    "history": payload,
                    "market_overview": overview,
                    "availability": "available" if payload else "data_gap",
                    "data_gap": [] if payload else ["kline_missing"],
                },
            )
        if self.snapshot_type == "valuation":
            market_snapshot = MarketSnapshotSkill(
                provider_registry=self.provider_registry
            ).run(query=query, symbol=symbol, as_of_date=as_of_date)
            market_facts = dict(market_snapshot.get("facts") or {})
            market = dict(market_facts.get("market_overview") or {})
            valuation = dict(market.get("valuation") or {})
            valuation["history"] = list(market.get("valuation_history") or [])
            industry_context = self._industry_context(
                symbol, query=query, historical=as_of_date is not None
            )
            industry = str(industry_context.get("industry") or "").strip()
            route = MarketRouter().classify(symbol=symbol, query=query)
            market_pe, pe_date, pe_column = (
                self._cninfo_market_weighted_pe(as_of_date=as_of_date)
                if route.market == "cn_equity"
                else (None, None, None)
            )
            browser = (
                {"evidence": {}, "source_trace": []}
                if as_of_date is not None
                else self._safe_browser(
                    source_ids=("legulegu",), symbol=symbol, query=query
                )
            )
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
            return self._from_snapshot(
                market_snapshot,
                {
                    "valuation": valuation,
                    "industry": industry or None,
                    "browser_evidence": browser.get("evidence") or {},
                    "availability": availability,
                    "data_gap": gaps
                    if valuation
                    else ["valuation_snapshot_missing", *gaps],
                    "degrade_reason": gaps[0] if gaps else None,
                    "_source_trace": [
                        *list(market_snapshot.get("source_trace") or []),
                        *list(browser.get("source_trace") or []),
                    ],
                },
            )
        if self.snapshot_type == "governance":
            fundamental = FundamentalSnapshotSkill(
                provider_registry=self.provider_registry
            ).run(query=query, symbol=symbol, as_of_date=as_of_date)
            facts = dict(fundamental.get("facts") or {})
            structured = self._structured_governance(symbol, as_of_date=as_of_date)
            result = {
                "info": dict(facts.get("info") or {}),
                "banking_metrics": dict(facts.get("banking_metrics") or {}),
                "bank_peer_comparison": dict(facts.get("bank_peer_comparison") or {}),
                "pledge": structured["pledge"],
                "insider_trades_1y": structured["insider_trades_1y"],
                "observation_status": structured["observation_status"],
                "source_coverage": structured["source_coverage"],
            }
            search = (
                {"results": [], "source_trace": []}
                if as_of_date is not None
                else self._safe_search(
                    query=f"{query} 公司治理 股权质押 违规 减持",
                    dimension="governance",
                )
            )
            browser = (
                {"evidence": {}, "source_trace": []}
                if as_of_date is not None
                else self._safe_browser(
                    source_ids=("ths_f10", "xueqiu_f10"),
                    symbol=symbol,
                    query=query,
                )
            )
            result["governance_evidence"] = search.get("results") or []
            result["browser_evidence"] = browser.get("evidence") or {}
            structured_verified = structured["observation_status"] in {
                "verified_empty",
                "verified_non_empty",
            }
            qualitative_available = bool(
                result["governance_evidence"] or result["browser_evidence"]
            )
            available = structured_verified or qualitative_available
            return self._from_snapshot(
                fundamental,
                {
                    **result,
                    "availability": "available"
                    if available
                    else "degraded"
                    if any(result.values())
                    else "data_gap",
                    "data_gap": []
                    if available
                    else ["governance_structured_observation_missing"],
                    "degrade_reason": None
                    if available
                    else "governance_structured_observation_missing",
                    "_source_trace": [
                        *list(fundamental.get("source_trace") or []),
                        *structured["source_trace"],
                        *list(search.get("source_trace") or []),
                        *list(browser.get("source_trace") or []),
                    ],
                },
            )
        if self.snapshot_type == "research":
            try:
                reports = AkShareCatalystAdapter().get_symbol_research_reports(
                    symbol=symbol, limit=50 if as_of_date is not None else 10
                )
                if as_of_date is not None:
                    reports = self._bounded_rows(
                        reports,
                        as_of_date=as_of_date,
                        window_days=365,
                    )[:10]
                trace = [
                    {
                        "source_id": "em_data",
                        "status": "available" if reports else "no_data",
                        "count": len(reports),
                    }
                ]
            except Exception as exc:
                reports = []
                trace = [
                    {
                        "source_id": "em_data",
                        "status": "provider_unavailable",
                        "error_type": exc.__class__.__name__,
                    }
                ]
            search = (
                {"results": [], "source_trace": []}
                if as_of_date is not None
                else self._safe_search(
                    query=f"{query} 研报 评级 目标价", dimension="research"
                )
            )
            browser = (
                {"evidence": {}, "source_trace": []}
                if as_of_date is not None
                else self._safe_browser(
                    source_ids=("stockstar",), symbol=symbol, query=query
                )
            )
            return {
                "reports": reports,
                "web_research": search.get("results") or [],
                "browser_evidence": browser.get("evidence") or {},
                "availability": "available"
                if reports or search.get("results") or browser.get("evidence")
                else "data_gap",
                "data_gap": []
                if reports or search.get("results") or browser.get("evidence")
                else ["research_reports_missing"],
                "_source": {
                    "vendor": "akshare",
                    "provider_id": "akshare_research",
                    "source_id": "em_data",
                },
                "_source_trace": [
                    *trace,
                    *list(search.get("source_trace") or []),
                    *list(browser.get("source_trace") or []),
                ],
            }
        if self.snapshot_type == "macro":
            adapter = AkShareSectorAdapter()
            try:
                benchmark = adapter.get_benchmark_snapshot(
                    start_date=(
                        (as_of_date or date.today()) - timedelta(days=30)
                    ).strftime("%Y%m%d"),
                    end_date=(as_of_date or date.today()).strftime("%Y%m%d"),
                )
                trace = [
                    {
                        "source_id": "em_push2",
                        "status": "available" if benchmark else "no_data",
                    }
                ]
            except Exception as exc:
                benchmark = {}
                trace = [
                    {
                        "source_id": "em_push2",
                        "status": "provider_unavailable",
                        "error_type": exc.__class__.__name__,
                    }
                ]
            search = (
                {"results": [], "source_trace": []}
                if as_of_date is not None
                else self._safe_search(
                    query=f"{query} 利率 汇率 宏观 政策 大宗商品",
                    dimension="macro",
                )
            )
            news = (
                {"sources": {}, "source_trace": []}
                if as_of_date is not None
                else self._safe_news(symbol=symbol, name=query)
            )
            available = bool(benchmark or search.get("results") or news.get("sources"))
            return {
                "benchmark": benchmark,
                "web_search": search.get("results") or [],
                "news_multi_source": news.get("sources") or {},
                "availability": "available" if available else "data_gap",
                "data_gap": [] if available else ["macro_evidence_missing"],
                "_source": {
                    "vendor": "uzi_macro",
                    "provider_id": "uzi_macro_multi",
                    "source_id": "ddgs" if search.get("results") else "em_push2",
                },
                "_source_trace": [
                    *trace,
                    *list(search.get("source_trace") or []),
                    *list(news.get("source_trace") or []),
                ],
            }
        if self.snapshot_type == "industry":
            return self._industry_context(
                symbol, query=query, historical=as_of_date is not None
            )
        if self.snapshot_type == "chain":
            context = self._industry_context(
                symbol, query=query, historical=as_of_date is not None
            )
            resolved = dict(context.get("resolved_sector") or {})
            sector = {}
            if context.get("industry") and as_of_date is None:
                try:
                    sector = AkShareSectorAdapter().get_sector_snapshot(
                        query=str(context.get("industry") or symbol),
                        sector_name=str(resolved.get("sector_name") or ""),
                        sector_type=str(resolved.get("sector_type") or "industry"),
                    )
                except Exception:
                    sector = {}
            search = self._historical_safe_search(
                query=f"{query} 产业链 上游 下游 供应商 客户",
                dimension="chain",
                historical=as_of_date is not None,
            )
            browser = self._historical_safe_browser(
                source_ids=("iwencai", "ths_f10"),
                symbol=symbol,
                query=query,
                historical=as_of_date is not None,
            )
            available = bool(sector or search.get("results") or browser.get("evidence"))
            return {
                "industry": context.get("industry"),
                "resolved_sector": resolved,
                "chain_snapshot": sector,
                "web_search": search.get("results") or [],
                "browser_evidence": browser.get("evidence") or {},
                "availability": "available" if available else "data_gap",
                "data_gap": [] if available else ["chain_snapshot_missing"],
                "_source": {
                    "vendor": "uzi_chain",
                    "provider_id": "uzi_chain_multi",
                    "source_id": "em_data" if sector else "ddgs",
                },
                "_source_trace": [
                    *list(context.get("_source_trace") or []),
                    *list(search.get("source_trace") or []),
                    *list(browser.get("source_trace") or []),
                ],
            }
        if self.snapshot_type == "materials":
            context = self._industry_context(
                symbol, query=query, historical=as_of_date is not None
            )
            industry = str(context.get("industry") or "").strip()
            contracts = self._material_contracts(industry)
            rows = self._futures_rows(contracts, as_of_date=as_of_date)
            search = self._historical_safe_search(
                query=f"{query} 原材料 价格 库存 基差",
                dimension="materials",
                historical=as_of_date is not None,
            )
            available = bool(rows or search.get("results"))
            trend_values = [
                float(row["trend_pct_12m"])
                for row in rows
                if row.get("trend_pct_12m") is not None
            ]
            return {
                "contracts": rows,
                "materials_detail": rows,
                "core_material": " / ".join(
                    str(row.get("name") or "") for row in rows if row.get("name")
                )
                or None,
                "price_trend": (
                    f"12月均 {sum(trend_values) / len(trend_values):+.1f}%"
                    if trend_values
                    else None
                ),
                "industry_resolved": industry or None,
                "web_search": search.get("results") or [],
                "availability": "available" if available else "data_gap",
                "data_gap": []
                if available
                else (
                    ["industry_material_mapping_missing"]
                    if not contracts
                    else ["materials_provider_missing"]
                ),
                "_source": {
                    "vendor": "uzi_materials",
                    "provider_id": "uzi_materials_multi",
                    "source_id": "akshare" if rows else "ddgs",
                },
                "_source_trace": [
                    {
                        "source_id": "akshare",
                        "status": "available" if rows else "no_data",
                        "count": len(rows),
                    },
                    *list(search.get("source_trace") or []),
                ],
            }
        if self.snapshot_type == "futures":
            material_snapshot = dict(
                dependencies.get("materials") or dependencies.get("8_materials") or {}
            )
            material_facts = dict(
                material_snapshot.get("facts")
                or material_snapshot.get("data")
                or material_snapshot
            )
            material_rows = [
                row
                for row in material_facts.get("materials_detail") or []
                if isinstance(row, dict)
            ]
            linked = next(
                (
                    row
                    for row in material_rows
                    if row.get("code") or row.get("contract")
                ),
                None,
            )
            context = self._industry_context(
                symbol, query=query, historical=as_of_date is not None
            )
            industry = str(context.get("industry") or "").strip()
            contracts = (
                (
                    (
                        str(linked.get("name") or linked.get("contract")),
                        str(linked.get("code") or linked.get("contract")),
                    ),
                )
                if linked
                else self._material_contracts(industry)[:1]
            )
            rows = self._futures_rows(contracts, as_of_date=as_of_date)
            search = self._historical_safe_search(
                query=f"{query} 期货 库存 仓单 现货 基差",
                dimension="futures",
                historical=as_of_date is not None,
            )
            available = bool(rows or search.get("results"))
            return {
                "contracts": rows,
                "linked_contract": rows[0].get("name") if rows else None,
                "contract_code": rows[0].get("code") if rows else None,
                "latest_price": rows[0].get("latest_price") if rows else None,
                "contract_trend": rows[0].get("trend_pct_60d") if rows else None,
                "price_history_60d": rows[0].get("price_history_60d") if rows else [],
                "source_note": "8_materials 识别的原材料期货"
                if linked
                else "industry material mapping",
                "dependency_lineage": {
                    "snapshot_type": "materials",
                    "symbol": material_snapshot.get("symbol"),
                    "snapshot_date": material_snapshot.get("snapshot_date"),
                    "used": bool(linked),
                },
                "web_search": search.get("results") or [],
                "availability": "available" if available else "data_gap",
                "data_gap": [] if available else ["futures_provider_missing"],
                "_source": {
                    "vendor": "uzi_futures",
                    "provider_id": "uzi_futures_multi",
                    "source_id": "akshare" if rows else "ddgs",
                },
                "_source_trace": [
                    {
                        "source_id": "akshare",
                        "status": "available" if rows else "no_data",
                        "count": len(rows),
                    },
                    *list(search.get("source_trace") or []),
                ],
            }
        if self.snapshot_type == "moat":
            fundamental = FundamentalSnapshotSkill(
                provider_registry=self.provider_registry
            ).run(query=query, symbol=symbol, as_of_date=as_of_date)
            facts = dict(fundamental.get("facts") or {})
            moat_inputs = {
                "company_info": dict(facts.get("info") or {}),
                "financial_abstract": dict(facts.get("financial_abstract") or {}),
                "banking_metrics": dict(facts.get("banking_metrics") or {}),
            }
            search = self._historical_safe_search(
                query=f"{query} 竞争壁垒 市占率 品牌 定价权 专利",
                dimension="moat",
                historical=as_of_date is not None,
            )
            browser = self._historical_safe_browser(
                source_ids=("ths_f10",),
                symbol=symbol,
                query=query,
                historical=as_of_date is not None,
            )
            moat_inputs["web_search"] = search.get("results") or []
            moat_inputs["browser_evidence"] = browser.get("evidence") or {}
            return self._from_snapshot(
                fundamental,
                {
                    **moat_inputs,
                    "availability": "available"
                    if any(moat_inputs.values())
                    else "data_gap",
                    "data_gap": []
                    if any(moat_inputs.values())
                    else ["moat_inputs_missing"],
                    "_source_trace": [
                        *list(fundamental.get("source_trace") or []),
                        *list(search.get("source_trace") or []),
                        *list(browser.get("source_trace") or []),
                    ],
                },
            )
        if self.snapshot_type == "trap":
            try:
                catalyst_news = AkShareCatalystAdapter().get_symbol_news(
                    symbol=symbol, limit=50 if as_of_date is not None else 10
                )
                if as_of_date is not None:
                    catalyst_news = self._bounded_rows(
                        catalyst_news,
                        as_of_date=as_of_date,
                        window_days=365,
                    )[:10]
                trace = [
                    {
                        "source_id": "em_data",
                        "status": "available" if catalyst_news else "no_data",
                        "count": len(catalyst_news),
                    }
                ]
            except Exception as exc:
                catalyst_news = []
                trace = [
                    {
                        "source_id": "em_data",
                        "status": "provider_unavailable",
                        "error_type": exc.__class__.__name__,
                    }
                ]
            direct_news = (
                {"sources": {}, "source_trace": []}
                if as_of_date is not None
                else self._safe_news(symbol=symbol, name=query)
            )
            search = self._historical_safe_search(
                query=f"{query} 风险 骗局 荐股 庄家 股吧",
                dimension="trap",
                historical=as_of_date is not None,
            )
            hot = (
                {"mentions": {}, "total_hits": 0, "source_trace": []}
                if as_of_date is not None
                else self._safe_hottrend(name=query, symbol=symbol)
            )
            available = bool(
                catalyst_news
                or direct_news.get("sources")
                or search.get("results")
                or hot.get("total_hits")
            )
            return {
                "news": catalyst_news,
                "news_multi_source": direct_news.get("sources") or {},
                "web_search": search.get("results") or [],
                "hot_trend_mentions": hot,
                "availability": "available" if available else "data_gap",
                "data_gap": [] if available else ["trap_news_missing"],
                "_source": {
                    "vendor": "uzi_trap",
                    "provider_id": "uzi_trap_multi",
                    "source_id": "ddgs" if search.get("results") else "em_data",
                },
                "_source_trace": [
                    *trace,
                    *list(direct_news.get("source_trace") or []),
                    *list(search.get("source_trace") or []),
                    *list(hot.get("source_trace") or []),
                ],
            }
        if self.snapshot_type == "peers":
            fundamental = FundamentalSnapshotSkill(
                provider_registry=self.provider_registry
            ).run(query=query, symbol=symbol, as_of_date=as_of_date)
            facts = dict(fundamental.get("facts") or {})
            info = dict(facts.get("info") or {})
            context = self._industry_context(
                symbol, query=query, historical=as_of_date is not None
            )
            target_name = next(
                (
                    str(info.get(key) or "").strip()
                    for key in ("股票简称", "名称", "公司名称", "longName", "shortName")
                    if str(info.get(key) or "").strip()
                ),
                "",
            )
            target_industry = str(
                context.get("industry") or info.get("industry") or ""
            ).strip()
            if as_of_date is not None:
                global_comparison = {
                    "conclusion_status": "historical_unavailable",
                    "peer_count": 0,
                    "peers": [],
                }
                global_trace = {
                    "source_id": "yfinance",
                    "status": "not_queried",
                    "reason_code": "current_only",
                }
            elif not global_peers_enabled():
                global_comparison = {
                    "conclusion_status": "disabled",
                    "peer_count": 0,
                    "peers": [],
                }
                global_trace = {"source_id": "yfinance", "status": "disabled"}
            elif not target_name or not target_industry:
                global_comparison = {
                    "conclusion_status": "insufficient_target_profile",
                    "peer_count": 0,
                    "peers": [],
                    "data_gap": ["global_peer_target_name_or_industry_missing"],
                }
                global_trace = {
                    "source_id": "yfinance",
                    "status": "no_data",
                    "reason": "target_profile_missing",
                }
            else:
                try:
                    global_comparison = fetch_global_peer_comparison(
                        symbol,
                        basic={"name": target_name, "industry": target_industry},
                        limit=8,
                    )
                    global_trace = {
                        "source_id": "yfinance",
                        "status": "available"
                        if global_comparison.get("peer_count")
                        else "no_data",
                        "count": int(global_comparison.get("peer_count") or 0),
                    }
                except Exception as exc:
                    global_comparison = {
                        "conclusion_status": "unavailable",
                        "peer_count": 0,
                        "peers": [],
                        "error_type": exc.__class__.__name__,
                        "data_gap": ["global_peer_provider_unavailable"],
                    }
                    global_trace = {
                        "source_id": "yfinance",
                        "status": "provider_unavailable",
                        "error_type": exc.__class__.__name__,
                    }
            comparison = dict(facts.get("bank_peer_comparison") or {})
            if comparison:
                return self._from_snapshot(
                    fundamental,
                    {
                        "comparison": comparison,
                        "global_peer_comparison": global_comparison,
                        "availability": "available",
                        "data_gap": [],
                        "_source_trace": [
                            *list(fundamental.get("source_trace") or []),
                            global_trace,
                        ],
                    },
                )
            resolved = dict(context.get("resolved_sector") or {})
            if as_of_date is not None:
                sector = {}
            else:
                try:
                    sector = (
                        AkShareSectorAdapter().get_sector_snapshot(
                            query=str(context.get("industry") or symbol),
                            sector_name=str(resolved.get("sector_name") or ""),
                            sector_type=str(resolved.get("sector_type") or "industry"),
                        )
                        if context.get("industry")
                        else {}
                    )
                except Exception:
                    sector = {}
            sector_facts = (
                dict(sector.get("facts") or {}) if isinstance(sector, dict) else {}
            )
            peers = [
                dict(row)
                for row in list(sector_facts.get("top_constituents") or [])
                if isinstance(row, dict)
                and str(row.get("symbol") or row.get("代码") or "") != symbol
            ][:10]
            if peers:
                peers, roe_comparison, roe_trace = self._enrich_local_peer_roe(
                    target_symbol=symbol,
                    peers=peers,
                )
                return self._from_snapshot(
                    fundamental,
                    {
                        "comparison": {
                            "industry": context.get("industry"),
                            "peers": peers,
                            "roe_comparison": roe_comparison,
                            "fallback_used": False,
                        },
                        "global_peer_comparison": global_comparison,
                        "availability": "available",
                        "data_gap": [],
                        "_source_trace": [
                            *list(fundamental.get("source_trace") or []),
                            *roe_trace,
                            global_trace,
                        ],
                    },
                )
            search = self._historical_safe_search(
                query=f"{query} 同行业 可比公司 竞争对手",
                dimension="peers",
                historical=as_of_date is not None,
            )
            browser = self._historical_safe_browser(
                source_ids=("iwencai", "ths_f10"),
                symbol=symbol,
                query=query,
                historical=as_of_date is not None,
            )
            fallback_reason = (
                "basic.industry 缺失 · 仅返回公司自身"
                if not context.get("industry")
                else "行业成分数据缺失 · 仅返回公司自身"
            )
            return self._from_snapshot(
                fundamental,
                {
                    "comparison": {
                        "industry": context.get("industry"),
                        "peers": [],
                        "self_only": [
                            {
                                "symbol": symbol,
                                "name": info.get("股票简称")
                                or info.get("名称")
                                or info.get("公司名称"),
                            }
                        ],
                        "fallback_used": True,
                        "fallback_reason": fallback_reason,
                        "web_search": search.get("results") or [],
                        "browser_evidence": browser.get("evidence") or {},
                    },
                    "global_peer_comparison": global_comparison,
                    "availability": "available"
                    if search.get("results")
                    or browser.get("evidence")
                    or global_comparison.get("peer_count")
                    else "degraded",
                    "data_gap": []
                    if search.get("results")
                    or browser.get("evidence")
                    or global_comparison.get("peer_count")
                    else ["peer_group_missing"],
                    "degrade_reason": None
                    if search.get("results")
                    or browser.get("evidence")
                    or global_comparison.get("peer_count")
                    else fallback_reason,
                    "_source_trace": [
                        *list(fundamental.get("source_trace") or []),
                        *list(search.get("source_trace") or []),
                        *list(browser.get("source_trace") or []),
                        global_trace,
                    ],
                },
            )
        if self.snapshot_type == "fund_holders":
            fetcher = getattr(ak, "stock_fund_stock_holder", None)
            if not callable(fetcher):
                return {
                    "availability": "data_gap",
                    "data_gap": ["fund_holders_provider_missing"],
                }
            try:
                frame = call_with_retry(
                    lambda: fetcher(symbol=symbol), retries=1, delay_seconds=0.2
                )
            except Exception as exc:
                return {
                    "availability": "data_gap",
                    "data_gap": ["fund_holders_provider_unavailable"],
                    "degrade_reason": "fund_holders_provider_unavailable",
                    "_source": {
                        "vendor": "akshare",
                        "provider_id": "akshare_fund_holders",
                        "source_id": "em_data",
                    },
                    "_source_trace": [
                        {
                            "source_id": "em_data",
                            "status": "provider_unavailable",
                            "error_type": exc.__class__.__name__,
                        }
                    ],
                }
            rows = (
                [
                    {str(key): normalize_value(value) for key, value in row.items()}
                    for row in frame.to_dict("records")
                ]
                if frame is not None and not getattr(frame, "empty", True)
                else []
            )
            if as_of_date is not None:
                rows = self._bounded_rows(
                    rows,
                    as_of_date=as_of_date,
                    window_days=5 * 366,
                )
            return {
                "holders": rows,
                "active_funds_count": len(rows),
                "availability": "available" if rows else "data_gap",
                "data_gap": [] if rows else ["fund_holders_missing"],
                "_source": {
                    "vendor": "akshare",
                    "provider_id": "akshare_fund_holders",
                    "source_id": "em_data",
                },
                "_source_trace": [
                    {
                        "source_id": "em_data",
                        "status": "available" if rows else "no_data",
                        "count": len(rows),
                    }
                ],
            }
        if self.snapshot_type == "similar_stocks":
            context = self._industry_context(
                symbol, query=query, historical=as_of_date is not None
            )
            resolved = dict(context.get("resolved_sector") or {})
            if as_of_date is not None:
                sector = {}
            else:
                try:
                    sector = (
                        AkShareSectorAdapter().get_sector_snapshot(
                            query=str(context.get("industry") or symbol),
                            sector_name=str(resolved.get("sector_name") or ""),
                            sector_type=str(resolved.get("sector_type") or "industry"),
                        )
                        if context.get("industry")
                        else {}
                    )
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
                "_source": {
                    "vendor": "akshare",
                    "provider_id": "akshare_similar_stocks",
                    "source_id": "em_data",
                },
                "_source_trace": [
                    {
                        "source_id": "em_data",
                        "status": "available" if similar else "no_data",
                        "count": len(similar),
                    }
                ],
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
        chain = self.provider_registry.get_provider_chain(
            self.snapshot_type, "cn_equity"
        )
        return chain[0] if chain else None

    def _collect_contests(self, symbol: str) -> dict[str, Any]:
        settings = (
            self.provider_registry.settings
            if self.provider_registry is not None
            else None
        )
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

    def _industry_context(
        self,
        symbol: str,
        *,
        query: str = "",
        historical: bool = False,
    ) -> dict[str, Any]:
        route = MarketRouter().classify(symbol=symbol, query=query)
        info: dict[str, Any] = {}
        trace: list[dict[str, Any]] = []
        if historical:
            return {
                "industry": None,
                "company_info": {},
                "resolved_sector": {},
                "web_search": [],
                "browser_evidence": {},
                "availability": "data_gap",
                "data_gap": ["historical_industry_classification_unavailable"],
                "degrade_reason": "historical_industry_classification_unavailable",
                "_source": {
                    "vendor": "finance_pack",
                    "provider_id": "historical_industry",
                    "source_id": "classification_archive",
                },
                "_source_trace": [
                    {
                        "source_id": "classification_archive",
                        "status": "not_configured",
                        "reason_code": "historical_classification_archive_missing",
                    }
                ],
            }
        if route.market == "cn_equity":
            try:
                info_df = call_with_retry(
                    lambda: ak.stock_individual_info_em(symbol=symbol)
                )
                info = {
                    str(row.get("item")): normalize_value(row.get("value"))
                    for row in info_df.to_dict("records")
                    if row.get("item")
                }
                trace.append(
                    {
                        "source_id": "em_data",
                        "status": "available" if info else "no_data",
                    }
                )
            except Exception as exc:
                trace.append(
                    {
                        "source_id": "em_data",
                        "status": "provider_unavailable",
                        "error_type": exc.__class__.__name__,
                    }
                )
            if not (info.get("行业") or info.get("所属行业")):
                try:
                    profile = call_with_retry(
                        lambda: ak.stock_profile_cninfo(symbol=symbol)
                    )
                    profile_info = (
                        {
                            str(key): normalize_value(value)
                            for key, value in profile.iloc[0].to_dict().items()
                        }
                        if profile is not None and not profile.empty
                        else {}
                    )
                    info = {**profile_info, **info}
                    trace.append(
                        {
                            "source_id": "cninfo",
                            "status": "available" if profile_info else "no_data",
                        }
                    )
                except Exception as exc:
                    trace.append(
                        {
                            "source_id": "cninfo",
                            "status": "provider_unavailable",
                            "error_type": exc.__class__.__name__,
                        }
                    )
        else:
            try:
                foreign = YFinanceFundamentalAdapter().get_fundamental_snapshot(
                    symbol=symbol
                )
                info = dict(foreign.get("info") or {})
                trace.append(
                    {
                        "source_id": "yfinance",
                        "status": "available" if info else "no_data",
                    }
                )
            except Exception as exc:
                trace.append(
                    {
                        "source_id": "yfinance",
                        "status": "provider_unavailable",
                        "error_type": exc.__class__.__name__,
                    }
                )
        industry = str(
            info.get("行业")
            or info.get("所属行业")
            or info.get("industry")
            or info.get("sector")
            or ""
        ).strip()
        search = self._safe_search(
            query=f"{query or symbol} 所属行业 行业分析", dimension="industry"
        )
        browser = self._safe_browser(
            source_ids=("iwencai", "legulegu"), symbol=symbol, query=query or symbol
        )
        if not industry:
            return {
                "industry": None,
                "web_search": search.get("results") or [],
                "browser_evidence": browser.get("evidence") or {},
                "availability": "available"
                if search.get("results") or browser.get("evidence")
                else "data_gap",
                "data_gap": []
                if search.get("results") or browser.get("evidence")
                else ["industry_missing"],
                "degrade_reason": None
                if search.get("results") or browser.get("evidence")
                else "industry_missing",
                "_source": {
                    "vendor": "ddgs",
                    "provider_id": "ddgs_industry",
                    "source_id": "ddgs",
                },
                "_source_trace": [
                    *trace,
                    *list(search.get("source_trace") or []),
                    *list(browser.get("source_trace") or []),
                ],
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
                "provider_id": "akshare_industry"
                if route.market == "cn_equity"
                else "yfinance_industry",
                "source_id": next(
                    (
                        str(row.get("source_id"))
                        for row in trace
                        if row.get("status") == "available"
                    ),
                    "em_data" if route.market == "cn_equity" else "yfinance",
                ),
            },
            "_source_trace": [
                *trace,
                *list(search.get("source_trace") or []),
                *list(browser.get("source_trace") or []),
            ],
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

    @classmethod
    def _structured_governance(
        cls, symbol: str, *, as_of_date: date | None = None
    ) -> dict[str, Any]:
        code = "".join(
            character for character in str(symbol or "") if character.isdigit()
        )[-6:]
        pledge: list[dict[str, Any]] = []
        insider: list[dict[str, Any]] = []
        coverage: dict[str, str] = {}
        trace: list[dict[str, Any]] = []

        endpoints = (
            (
                "em_data.stock_gpzy_pledge_ratio",
                lambda: ak.stock_gpzy_pledge_ratio_em(),
                "pledge",
            ),
            (
                "em_data.stock_ggcg",
                lambda: ak.stock_ggcg_em(symbol="近一年"),
                "insider_trades_1y",
            ),
        )
        for source_id, loader, field in endpoints:
            try:
                frame = call_with_retry(loader, retries=1, delay_seconds=0.2)
                if frame is None or getattr(frame, "empty", True):
                    coverage[field] = "no_data"
                    trace.append({"source_id": source_id, "status": "no_data"})
                    continue
                symbol_column = next(
                    (
                        column
                        for column in frame.columns
                        if str(column) in {"股票代码", "证券代码", "代码"}
                    ),
                    None,
                )
                if symbol_column is None:
                    coverage[field] = "parse_failed"
                    trace.append({"source_id": source_id, "status": "parse_failed"})
                    continue
                normalized_codes = (
                    frame[symbol_column]
                    .astype(str)
                    .str.replace(r"\.0$", "", regex=True)
                    .str.extract(r"(\d+)", expand=False)
                    .fillna("")
                    .str.zfill(6)
                )
                rows = frame[normalized_codes == code].head(20).to_dict("records")
                normalized_rows = [
                    {str(key): normalize_value(value) for key, value in row.items()}
                    for row in rows
                ]
                if as_of_date is not None:
                    normalized_rows = cls._bounded_rows(
                        normalized_rows,
                        as_of_date=as_of_date,
                        window_days=365,
                    )
                if field == "pledge":
                    pledge = normalized_rows
                else:
                    insider = normalized_rows
                coverage[field] = "available"
                trace.append(
                    {
                        "source_id": source_id,
                        "status": "available",
                        "count": len(normalized_rows),
                    }
                )
            except Exception as exc:
                coverage[field] = "provider_unavailable"
                trace.append(
                    {
                        "source_id": source_id,
                        "status": "provider_unavailable",
                        "error_type": exc.__class__.__name__,
                    }
                )

        complete = all(
            coverage.get(field) == "available"
            for field in ("pledge", "insider_trades_1y")
        )
        if pledge or insider:
            observation_status = (
                "verified_non_empty" if complete else "partially_verified_non_empty"
            )
        elif complete:
            observation_status = "verified_empty"
        elif any(status == "available" for status in coverage.values()):
            observation_status = "partially_verified_empty"
        else:
            observation_status = "unverified_empty"
        return {
            "pledge": pledge,
            "insider_trades_1y": insider,
            "observation_status": observation_status,
            "source_coverage": coverage,
            "source_trace": trace,
        }

    def _safe_search(self, *, query: str, dimension: str) -> dict[str, Any]:
        try:
            payload = UziWebSearchAdapter().search(
                query=query, dimension=dimension, max_results=8
            )
            return {
                **payload,
                "source_trace": list(
                    payload.get("source_trace")
                    or [
                        {
                            "source_id": "ddgs",
                            "status": "available",
                            "count": payload.get("result_count", 0),
                        }
                    ]
                ),
            }
        except Exception as exc:
            return {
                "results": [],
                "source_trace": [
                    {
                        "source_id": "ddgs",
                        "status": "provider_unavailable",
                        "error_type": exc.__class__.__name__,
                    }
                ],
            }

    def _historical_safe_search(
        self,
        *,
        query: str,
        dimension: str,
        historical: bool,
    ) -> dict[str, Any]:
        if historical:
            return {
                "results": [],
                "source_trace": [
                    {
                        "source_id": "ddgs",
                        "status": "not_queried",
                        "reason_code": "unbounded_search_not_pit_safe",
                    }
                ],
            }
        return self._safe_search(query=query, dimension=dimension)

    @staticmethod
    def _safe_news(*, symbol: str, name: str) -> dict[str, Any]:
        try:
            return UziNewsAdapter().get_news(symbol=symbol, name=name, limit=20)
        except Exception as exc:
            return {
                "sources": {},
                "source_trace": [
                    {
                        "source_id": "uzi_news_http",
                        "status": "provider_unavailable",
                        "error_type": exc.__class__.__name__,
                    }
                ],
            }

    @staticmethod
    def _safe_hottrend(*, name: str, symbol: str) -> dict[str, Any]:
        try:
            return UziHotTrendAdapter().get_mentions(name=name, extra_keywords=[symbol])
        except Exception as exc:
            return {
                "mentions": {},
                "total_hits": 0,
                "source_trace": [
                    {
                        "source_id": "hottrend",
                        "status": "provider_unavailable",
                        "error_type": exc.__class__.__name__,
                    }
                ],
            }

    def _safe_browser(
        self, *, source_ids: tuple[str, ...], symbol: str, query: str
    ) -> dict[str, Any]:
        settings = (
            self.provider_registry.settings
            if self.provider_registry is not None
            else None
        )
        if settings is None:
            return {
                "evidence": {},
                "source_trace": [
                    {
                        "source_id": source_id,
                        "status": "disabled",
                        "reason": "settings_missing",
                    }
                    for source_id in source_ids
                ],
            }
        return UziBrowserAdapter(settings).collect_many(
            source_ids=source_ids, symbol=symbol, query=query
        )

    def _historical_safe_browser(
        self,
        *,
        source_ids: tuple[str, ...],
        symbol: str,
        query: str,
        historical: bool,
    ) -> dict[str, Any]:
        if historical:
            return {
                "evidence": {},
                "source_trace": [
                    {
                        "source_id": source_id,
                        "status": "not_queried",
                        "reason_code": "current_page_not_pit_safe",
                    }
                    for source_id in source_ids
                ],
            }
        return self._safe_browser(source_ids=source_ids, symbol=symbol, query=query)

    @staticmethod
    def _from_snapshot(
        snapshot: dict[str, Any], facts: dict[str, Any]
    ) -> dict[str, Any]:
        source = dict(snapshot.get("source") or {})
        explicit_trace = list(facts.pop("_source_trace", []) or [])
        facts["_source"] = {
            "vendor": source.get("vendor") or "finance_pack",
            "provider_id": source.get("provider_id") or "finance_pack",
            "source_id": source.get("source_id") or "finance_pack",
            "chain_position": source.get("chain_position") or 0,
        }
        facts["_source_trace"] = explicit_trace or list(
            snapshot.get("source_trace") or []
        )
        return facts

    @staticmethod
    def _cninfo_market_weighted_pe(
        *, as_of_date: date | None = None
    ) -> tuple[float | None, str | None, str | None]:
        for offset in range(1, 8):
            snapshot_date = (as_of_date or date.today()) - timedelta(days=offset)
            try:
                frame = call_with_retry(
                    lambda snapshot_date=snapshot_date: (
                        ak.stock_industry_pe_ratio_cninfo(
                            symbol="证监会行业分类",
                            date=snapshot_date.strftime("%Y%m%d"),
                        )
                    ),
                    retries=1,
                    delay_seconds=0.2,
                )
            except Exception:
                continue
            if frame is None or getattr(frame, "empty", True):
                continue
            if as_of_date is not None:
                date_column = next(
                    (
                        column
                        for column in ("date", "日期", "交易日期", "trade_date")
                        if column in frame.columns
                    ),
                    None,
                )
                if date_column is None:
                    continue
                parsed = frame[date_column].map(UziSourceSnapshotSkill._calendar_date)
                frame = frame[
                    parsed.map(lambda value: value is not None and value <= as_of_date)
                ]
                if frame.empty:
                    continue
            pe_column = next(
                (
                    str(column)
                    for column in frame.columns
                    if "市盈率" in str(column) and "加权" in str(column)
                ),
                None,
            )
            if not pe_column:
                continue
            values = [
                number
                for number in (
                    coerce_numeric(value) for value in frame[pe_column].tolist()
                )
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
    def _material_contracts(industry: str) -> tuple[tuple[str, str], ...]:
        if industry in _UZI_INDUSTRY_MATERIALS:
            return tuple(row for row in _UZI_INDUSTRY_MATERIALS[industry] if row[1])
        for key, rows in _UZI_INDUSTRY_MATERIALS.items():
            if key[:2] in industry or industry[:2] in key:
                return tuple(row for row in rows if row[1])
        return ()

    @staticmethod
    def _futures_rows(
        contracts: tuple[tuple[str, str], ...],
        *,
        as_of_date: date | None = None,
    ) -> list[dict[str, Any]]:
        fetcher = getattr(ak, "futures_main_sina", None) or getattr(
            ak, "futures_zh_daily_sina", None
        )
        if not callable(fetcher):
            return []
        rows: list[dict[str, Any]] = []
        for name, contract in contracts[:3]:
            try:
                frame = call_with_retry(
                    lambda contract=contract: fetcher(symbol=contract),
                    retries=1,
                    delay_seconds=0.2,
                )
            except Exception:
                continue
            if frame is None or getattr(frame, "empty", True):
                continue
            close_column = next(
                (
                    column
                    for column in ("收盘价", "close", "收盘")
                    if column in frame.columns
                ),
                None,
            )
            closes = [
                float(parsed)
                for value in (
                    frame[close_column].tail(250).tolist() if close_column else []
                )
                if (parsed := coerce_numeric(value)) is not None and float(parsed) > 0
            ]
            latest = frame.tail(1).to_dict("records")
            if not latest:
                continue
            history_60d = closes[-60:]
            rows.append(
                {
                    "name": name,
                    "code": contract,
                    "contract": contract,
                    "latest_price": round(closes[-1], 4) if closes else None,
                    "price_history_60d": [round(value, 4) for value in history_60d],
                    "price_history_12m": [round(value, 4) for value in closes],
                    "trend_pct_60d": round(
                        (history_60d[-1] / history_60d[0] - 1) * 100, 2
                    )
                    if len(history_60d) >= 2
                    else None,
                    "trend_pct_12m": round((closes[-1] / closes[0] - 1) * 100, 2)
                    if len(closes) >= 2
                    else None,
                    "data_points": len(closes),
                    "latest_record": {
                        str(k): normalize_value(v) for k, v in latest[0].items()
                    },
                }
            )
        return rows

    @classmethod
    def _bounded_rows(
        cls,
        rows: list[dict[str, Any]],
        *,
        as_of_date: date,
        window_days: int,
    ) -> list[dict[str, Any]]:
        start = as_of_date - timedelta(days=window_days)
        bounded: list[tuple[date, dict[str, Any]]] = []
        for row in rows:
            record_date = next(
                (
                    cls._calendar_date(row.get(key))
                    for key in (
                        "published_at",
                        "publish_time",
                        "date",
                        "trade_date",
                        "report_date",
                        "period",
                        "公告日期",
                        "发布日期",
                        "报告期",
                        "持仓截止日期",
                        "交易日",
                        "变动日期",
                        "质押日期",
                        "统计日期",
                    )
                    if row.get(key) not in (None, "")
                    and cls._calendar_date(row.get(key)) is not None
                ),
                None,
            )
            if record_date is not None and start <= record_date <= as_of_date:
                bounded.append((record_date, row))
        bounded.sort(key=lambda item: item[0], reverse=True)
        return [row for _record_date, row in bounded]

    @staticmethod
    def _calendar_date(value: Any) -> date | None:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        text = str(value or "").strip().replace("/", "-")
        for fmt, width in (
            ("%Y-%m-%d", 10),
            ("%Y%m%d", 8),
            ("%Y-%m", 7),
            ("%Y", 4),
        ):
            try:
                return datetime.strptime(text[:width], fmt).date()
            except ValueError:
                continue
        return None

    @staticmethod
    def _enrich_local_peer_roe(
        *,
        target_symbol: str,
        peers: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
        """Fill real ROE for the target and the first five local peers.

        This mirrors latest UZI's per-security Eastmoney financial-indicator
        fallback. A provider failure stays a per-row gap; it never turns into
        a zero, a peer average, or an inferred value.
        """

        normalized_peers = [dict(row) for row in peers]
        requested: list[tuple[str, dict[str, Any] | None]] = [(target_symbol, None)]
        for row in normalized_peers[:5]:
            code = str(
                row.get("symbol") or row.get("code") or row.get("代码") or ""
            ).strip()
            if code:
                requested.append((code, row))
        for row in normalized_peers[5:]:
            row.setdefault("roe", None)
            row.setdefault("roe_status", "not_requested_outside_top5")

        values: dict[str, float | None] = {}
        trace: list[dict[str, Any]] = []
        for raw_code, row in requested:
            code = raw_code.split(".", 1)[0].zfill(6)
            market = "SH" if code.startswith(("6", "9")) else "SZ"
            try:
                frame = call_with_retry(
                    lambda code=code, market=market: (
                        ak.stock_financial_analysis_indicator_em(
                            symbol=f"{code}.{market}"
                        )
                    ),
                    retries=1,
                    delay_seconds=0.2,
                )
                if (
                    frame is None
                    or getattr(frame, "empty", True)
                    or "ROEJQ" not in frame.columns
                ):
                    roe = None
                    status = "no_data"
                else:
                    annual = frame
                    if "REPORT_DATE_NAME" in frame.columns:
                        selected = frame[
                            frame["REPORT_DATE_NAME"]
                            .astype(str)
                            .str.contains("年报", na=False)
                        ]
                        if not selected.empty:
                            annual = selected
                    roe = coerce_numeric(annual.iloc[-1].get("ROEJQ"))
                    roe = round(float(roe), 2) if roe is not None else None
                    status = "available" if roe is not None else "no_data"
            except Exception as exc:
                roe = None
                status = "provider_unavailable"
                trace.append(
                    {
                        "source_id": "em_data.finance_analysis_indicator",
                        "status": status,
                        "symbol": code,
                        "error_type": exc.__class__.__name__,
                    }
                )
            else:
                trace.append(
                    {
                        "source_id": "em_data.finance_analysis_indicator",
                        "status": status,
                        "symbol": code,
                    }
                )
            values[code] = roe
            if row is not None:
                row["roe"] = roe
                row["roe_status"] = status

        peer_values = [
            float(row["roe"])
            for row in normalized_peers[:5]
            if row.get("roe") is not None
        ]
        target_code = target_symbol.split(".", 1)[0].zfill(6)
        return (
            normalized_peers,
            {
                "metric": "roe",
                "higher_is_better": True,
                "target": values.get(target_code),
                "peer_average": round(sum(peer_values) / len(peer_values), 2)
                if peer_values
                else None,
                "peer_count": len(peer_values),
                "requested_peer_count": min(len(normalized_peers), 5),
                "source_id": "em_data.finance_analysis_indicator",
            },
            trace,
        )

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
        {
            "snapshot_type": snapshot_type,
            "skill_name": f"uzi_{snapshot_type}_snapshot_skill",
        },
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
