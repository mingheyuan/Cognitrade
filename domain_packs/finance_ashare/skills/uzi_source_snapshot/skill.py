from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import akshare as ak
import requests

from research_system.adapters.vendors.akshare_catalyst_adapter import AkShareCatalystAdapter
from research_system.adapters.vendors.akshare_market_adapter import AkShareMarketAdapter
from research_system.adapters.vendors.akshare_sector_adapter import AkShareSectorAdapter
from research_system.adapters.vendors.akshare_utils import call_with_retry, coerce_numeric, normalize_value
from research_system.schemas.snapshots import SnapshotSource, VerifiedSnapshot
from research_system.adapters.vendors.provider_registry import VendorProviderRegistry
from domain_packs.finance_ashare.skills.fundamental_snapshot.skill import FundamentalSnapshotSkill


class UziSourceSnapshotSkill:
    """Finance Pack source adapter for UZI's non-core dimensions.

    Each dimension is still an independent Snapshot Artifact. Unsupported
    sources return an explicit degraded record so the Planner and UI can show
    the gap instead of silently pretending that the dimension was collected.
    """

    skill_name = "uzi_source_snapshot_skill"
    snapshot_type = ""

    def __init__(self, provider_registry: VendorProviderRegistry | None = None) -> None:
        self.provider_registry = provider_registry

    def run(self, *, query: str, symbol: str | None = None) -> dict[str, Any]:
        snapshot_date = date.today().isoformat()
        provider = self._selected_provider()
        source = SnapshotSource(
            skill=self.skill_name,
            vendor=provider.vendor_name if provider is not None else "finance_pack",
            from_cache=False,
            provider_id=provider.provider_id if provider is not None else f"finance_pack.{self.snapshot_type or 'unknown'}",
            source_id=provider.source_id if provider is not None else "finance_pack",
            chain_position=0,
            dim=self.snapshot_type or "unknown",
        )
        try:
            facts = self._collect(query=query, symbol=symbol)
            availability = str((facts or {}).get("availability") or "").lower()
            if not facts or availability in {"data_gap", "degraded"}:
                return self._degraded(
                    query=query,
                    symbol=symbol,
                    snapshot_date=snapshot_date,
                    source=source,
                    reason=str((facts or {}).get("degrade_reason") or "source_data_gap"),
                    facts=facts or {},
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
            ).model_dump(mode="json")
        except Exception as exc:
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
            )

    def _collect(self, *, query: str, symbol: str | None) -> dict[str, Any]:
        if not symbol:
            return {"availability": "data_gap", "degrade_reason": "symbol_missing"}
        if self.snapshot_type == "kline":
            payload = AkShareMarketAdapter().get_market_snapshot(
                symbol=symbol,
                start_date=(date.today() - timedelta(days=365)).strftime("%Y%m%d"),
                end_date=date.today().strftime("%Y%m%d"),
            )
            return {"history": payload, "availability": "available" if payload else "data_gap"}
        if self.snapshot_type == "valuation":
            market = AkShareMarketAdapter().get_market_overview(symbol=symbol, latest_bar=None)
            valuation = dict(market.get("valuation") or {}) if isinstance(market, dict) else {}
            industry_context = self._industry_context(symbol)
            industry = str(industry_context.get("industry") or "").strip()
            market_pe, pe_date, pe_column = self._cninfo_market_weighted_pe()
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
            return {
                "valuation": valuation,
                "industry": industry or None,
                "availability": availability,
                "data_gap": gaps if valuation else ["valuation_snapshot_missing", *gaps],
                "degrade_reason": gaps[0] if gaps else None,
            }
        if self.snapshot_type == "governance":
            fundamental = FundamentalSnapshotSkill().run(query=query, symbol=symbol)
            facts = dict(fundamental.get("facts") or {})
            result = {
                "info": dict(facts.get("info") or {}),
                "banking_metrics": dict(facts.get("banking_metrics") or {}),
                "bank_peer_comparison": dict(facts.get("bank_peer_comparison") or {}),
            }
            return {
                **result,
                "availability": "available" if any(result.values()) else "data_gap",
                "data_gap": [] if any(result.values()) else ["governance_snapshot_missing"],
            }
        if self.snapshot_type == "research":
            reports = AkShareCatalystAdapter().get_symbol_research_reports(symbol=symbol, limit=10)
            return {
                "reports": reports,
                "availability": "available" if reports else "data_gap",
                "data_gap": [] if reports else ["research_reports_missing"],
            }
        if self.snapshot_type == "macro":
            adapter = AkShareSectorAdapter()
            benchmark = adapter.get_benchmark_snapshot(
                start_date=(date.today() - timedelta(days=30)).strftime("%Y%m%d"),
                end_date=date.today().strftime("%Y%m%d"),
            )
            return {
                "benchmark": benchmark,
                "availability": "available" if benchmark else "data_gap",
                "data_gap": [] if benchmark else ["macro_benchmark_missing"],
            }
        if self.snapshot_type == "industry":
            return self._industry_context(symbol)
        if self.snapshot_type == "chain":
            context = self._industry_context(symbol)
            if context.get("availability") != "available":
                return context
            resolved = dict(context.get("resolved_sector") or {})
            sector = AkShareSectorAdapter().get_sector_snapshot(
                query=str(context.get("industry") or symbol),
                sector_name=str(resolved.get("sector_name") or ""),
                sector_type=str(resolved.get("sector_type") or "industry"),
            )
            return {
                "industry": context.get("industry"),
                "resolved_sector": resolved,
                "chain_snapshot": sector,
                "availability": "available",
            }
        if self.snapshot_type == "materials":
            rows = self._futures_rows(("RB0", "CU0", "AL0"))
            return {
                "contracts": rows,
                "availability": "available" if rows else "data_gap",
                "data_gap": [] if rows else ["materials_provider_missing"],
            }
        if self.snapshot_type == "futures":
            rows = self._futures_rows(("RB0", "CU0", "AL0", "SC0"))
            return {
                "contracts": rows,
                "availability": "available" if rows else "data_gap",
                "data_gap": [] if rows else ["futures_provider_missing"],
            }
        if self.snapshot_type == "moat":
            fundamental = FundamentalSnapshotSkill().run(query=query, symbol=symbol)
            facts = dict(fundamental.get("facts") or {})
            moat_inputs = {
                "company_info": dict(facts.get("info") or {}),
                "financial_abstract": dict(facts.get("financial_abstract") or {}),
                "banking_metrics": dict(facts.get("banking_metrics") or {}),
            }
            return {
                **moat_inputs,
                "availability": "available" if any(moat_inputs.values()) else "data_gap",
                "data_gap": [] if any(moat_inputs.values()) else ["moat_inputs_missing"],
            }
        if self.snapshot_type == "trap":
            news = AkShareCatalystAdapter().get_symbol_news(symbol=symbol, limit=10)
            return {
                "news": news,
                "availability": "available" if news else "data_gap",
                "data_gap": [] if news else ["trap_news_missing"],
            }
        if self.snapshot_type == "peers":
            fundamental = FundamentalSnapshotSkill().run(query=query, symbol=symbol)
            facts = dict(fundamental.get("facts") or {})
            comparison = dict(facts.get("bank_peer_comparison") or {})
            if comparison:
                return {
                    "comparison": comparison,
                    "availability": "available",
                    "data_gap": [],
                }
            context = self._industry_context(symbol)
            resolved = dict(context.get("resolved_sector") or {})
            sector = AkShareSectorAdapter().get_sector_snapshot(
                query=str(context.get("industry") or symbol),
                sector_name=str(resolved.get("sector_name") or ""),
                sector_type=str(resolved.get("sector_type") or "industry"),
            ) if context.get("availability") == "available" else {}
            sector_facts = dict(sector.get("facts") or {}) if isinstance(sector, dict) else {}
            peers = [
                dict(row)
                for row in list(sector_facts.get("top_constituents") or [])
                if isinstance(row, dict)
                and str(row.get("symbol") or row.get("代码") or "") != symbol
            ][:10]
            if peers:
                return {
                    "comparison": {
                        "industry": context.get("industry"),
                        "peers": peers,
                        "fallback_used": False,
                    },
                    "availability": "available",
                    "data_gap": [],
                }
            info = dict(facts.get("info") or {})
            fallback_reason = (
                "basic.industry 缺失 · 仅返回公司自身"
                if not context.get("industry")
                else "行业成分数据缺失 · 仅返回公司自身"
            )
            return {
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
                },
                "availability": "degraded",
                "data_gap": ["peer_group_missing"],
                "degrade_reason": fallback_reason,
            }
        if self.snapshot_type == "fund_holders":
            fetcher = getattr(ak, "stock_fund_stock_holder", None)
            if not callable(fetcher):
                return {"availability": "data_gap", "data_gap": ["fund_holders_provider_missing"]}
            frame = call_with_retry(lambda: fetcher(symbol=symbol), retries=1, delay_seconds=0.2)
            rows = [
                {str(key): normalize_value(value) for key, value in row.items()}
                for row in frame.to_dict("records")
            ] if frame is not None and not getattr(frame, "empty", True) else []
            return {
                "holders": rows,
                "active_funds_count": len(rows),
                "availability": "available" if rows else "data_gap",
                "data_gap": [] if rows else ["fund_holders_missing"],
            }
        if self.snapshot_type == "similar_stocks":
            context = self._industry_context(symbol)
            if context.get("availability") != "available":
                return context
            resolved = dict(context.get("resolved_sector") or {})
            sector = AkShareSectorAdapter().get_sector_snapshot(
                query=str(context.get("industry") or symbol),
                sector_name=str(resolved.get("sector_name") or ""),
                sector_type=str(resolved.get("sector_type") or "industry"),
            )
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
        if settings is None or not settings.sensitive_sources_enabled:
            return {
                "availability": "data_gap",
                "data_gap": ["sensitive_source_disabled:xueqiu"],
                "degrade_reason": "sensitive_source_disabled",
            }
        if "xueqiu" not in settings.sensitive_source_allowlist:
            return {
                "availability": "data_gap",
                "data_gap": ["sensitive_source_not_allowlisted:xueqiu"],
                "degrade_reason": "sensitive_source_not_allowlisted",
            }
        cookie = str(settings.xueqiu_cookie or "").strip()
        if not cookie:
            return {
                "availability": "data_gap",
                "data_gap": ["sensitive_source_credentials_missing:xueqiu"],
                "degrade_reason": "sensitive_source_credentials_missing",
            }
        xueqiu_symbol = self._xueqiu_symbol(symbol)
        response = requests.get(
            "https://xueqiu.com/query/v1/search/cube/stock.json",
            params={"q": xueqiu_symbol, "count": 50, "page": 1},
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; Cognitrade/1.0; +https://github.com)",
                "Referer": f"https://xueqiu.com/S/{xueqiu_symbol}",
                "Cookie": cookie,
            },
            timeout=15,
        )
        if response.status_code != 200:
            reason = "sensitive_source_authentication_failed" if response.status_code in {400, 401, 403} else "sensitive_source_request_failed"
            return {
                "availability": "data_gap",
                "data_gap": [f"{reason}:xueqiu"],
                "degrade_reason": reason,
                "http_status": response.status_code,
            }
        try:
            payload = response.json()
        except ValueError:
            return {
                "availability": "data_gap",
                "data_gap": ["sensitive_source_parse_failed:xueqiu"],
                "degrade_reason": "sensitive_source_parse_failed",
            }
        raw_rows = payload.get("list") or payload.get("cubes") or []
        cubes = []
        for row in raw_rows if isinstance(raw_rows, list) else []:
            if not isinstance(row, dict):
                continue
            owner = row.get("owner") if isinstance(row.get("owner"), dict) else {}
            cube_symbol = str(row.get("symbol") or "").strip()
            cubes.append(
                {
                    "name": row.get("name"),
                    "owner": owner.get("screen_name"),
                    "symbol": cube_symbol or None,
                    "daily_gain": normalize_value(row.get("daily_gain")),
                    "monthly_gain": normalize_value(row.get("monthly_gain")),
                    "total_gain": normalize_value(row.get("total_gain")),
                    "annualized_gain_rate": normalize_value(row.get("annualized_gain_rate")),
                    "stocks_count": normalize_value(row.get("stocks_count")),
                    "url": f"https://xueqiu.com/P/{cube_symbol}" if cube_symbol else None,
                }
            )
        return {
            "xueqiu_cubes": cubes,
            "summary": {
                "cube_count": len(cubes),
                "high_return_cube_count": sum(
                    1
                    for row in cubes
                    if (coerce_numeric(row.get("total_gain")) or float("-inf")) > 50
                ),
            },
            "availability": "available" if cubes else "data_gap",
            "data_gap": [] if cubes else ["xueqiu_contests_empty"],
            "degrade_reason": None if cubes else "xueqiu_contests_empty",
            "source_policy": "explicit_sensitive_source",
        }

    @staticmethod
    def _xueqiu_symbol(symbol: str) -> str:
        normalized = str(symbol or "").strip().upper()
        if normalized.startswith(("SH", "SZ", "BJ")):
            return normalized
        if normalized.startswith(("4", "8")):
            return f"BJ{normalized}"
        return f"SH{normalized}" if normalized.startswith(("5", "6", "9")) else f"SZ{normalized}"

    def _industry_context(self, symbol: str) -> dict[str, Any]:
        info_df = call_with_retry(lambda: ak.stock_individual_info_em(symbol=symbol))
        info = {
            str(row.get("item")): normalize_value(row.get("value"))
            for row in info_df.to_dict("records")
            if row.get("item")
        }
        industry = str(info.get("行业") or info.get("所属行业") or "").strip()
        if not industry:
            return {"availability": "data_gap", "data_gap": ["industry_missing"]}
        resolved = AkShareSectorAdapter().resolve_sector(query=industry)
        return {
            "industry": industry,
            "resolved_sector": resolved,
            "availability": "available",
        }

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
