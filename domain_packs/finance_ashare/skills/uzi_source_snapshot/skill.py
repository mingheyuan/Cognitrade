from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import akshare as ak

from research_system.adapters.vendors.akshare_catalyst_adapter import AkShareCatalystAdapter
from research_system.adapters.vendors.akshare_market_adapter import AkShareMarketAdapter
from research_system.adapters.vendors.akshare_sector_adapter import AkShareSectorAdapter
from research_system.adapters.vendors.akshare_utils import call_with_retry, normalize_value
from research_system.schemas.snapshots import SnapshotSource, VerifiedSnapshot
from domain_packs.finance_ashare.skills.fundamental_snapshot.skill import FundamentalSnapshotSkill


class UziSourceSnapshotSkill:
    """Finance Pack source adapter for UZI's non-core dimensions.

    Each dimension is still an independent Snapshot Artifact. Unsupported
    sources return an explicit degraded record so the Planner and UI can show
    the gap instead of silently pretending that the dimension was collected.
    """

    skill_name = "uzi_source_snapshot_skill"
    snapshot_type = ""

    def run(self, *, query: str, symbol: str | None = None) -> dict[str, Any]:
        snapshot_date = date.today().isoformat()
        source = SnapshotSource(
            skill=self.skill_name,
            vendor="finance_pack",
            from_cache=False,
            provider_id=f"finance_pack.{self.snapshot_type or 'unknown'}",
            source_id="finance_pack",
            chain_position=0,
            dim=self.snapshot_type or "unknown",
        )
        try:
            facts = self._collect(query=query, symbol=symbol)
            if not facts or str(facts.get("availability") or "").lower() == "data_gap":
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
                facts={"error": str(exc)},
            )

    def _collect(self, *, query: str, symbol: str | None) -> dict[str, Any]:
        if not symbol:
            return {"availability": "data_gap", "degrade_reason": "symbol_missing"}
        if self.snapshot_type == "kline":
            payload = AkShareMarketAdapter().get_market_snapshot(
                symbol=symbol,
                start_date=(date.today() - timedelta(days=90)).strftime("%Y%m%d"),
                end_date=date.today().strftime("%Y%m%d"),
            )
            return {"history": payload, "availability": "available" if payload else "data_gap"}
        if self.snapshot_type == "valuation":
            market = AkShareMarketAdapter().get_market_overview(symbol=symbol, latest_bar=None)
            valuation = dict(market.get("valuation") or {}) if isinstance(market, dict) else {}
            return {
                "valuation": valuation,
                "availability": "available" if valuation else "data_gap",
                "data_gap": [] if valuation else ["valuation_snapshot_missing"],
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
            if not comparison:
                context = self._industry_context(symbol)
                resolved = dict(context.get("resolved_sector") or {})
                sector = AkShareSectorAdapter().get_sector_snapshot(
                    query=str(context.get("industry") or symbol),
                    sector_name=str(resolved.get("sector_name") or ""),
                    sector_type=str(resolved.get("sector_type") or "industry"),
                ) if context.get("availability") == "available" else {}
                sector_facts = dict(sector.get("facts") or {}) if isinstance(sector, dict) else {}
                comparison = {
                    "industry": context.get("industry"),
                    "peers": [
                        dict(row)
                        for row in list(sector_facts.get("top_constituents") or [])
                        if isinstance(row, dict) and str(row.get("symbol") or row.get("代码") or "") != symbol
                    ][:10],
                }
            return {
                "comparison": comparison,
                "availability": "available" if comparison else "data_gap",
                "data_gap": [] if comparison else ["peer_group_missing"],
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
        return {
            "availability": "data_gap",
            "data_gap": [f"{self.snapshot_type}_provider_not_configured"],
            "degrade_reason": "provider_not_configured",
        }

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
