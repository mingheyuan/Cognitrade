from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from research_system.skills.capital_flow_snapshot.skill import CapitalFlowSnapshotSkill
from research_system.skills.events_snapshot.skill import EventsSnapshotSkill
from research_system.skills.fundamental_snapshot.skill import FundamentalSnapshotSkill
from research_system.skills.lhb_snapshot.skill import LHBSnapshotSkill
from research_system.skills.market_snapshot.skill import MarketSnapshotSkill
from research_system.skills.notice_snapshot.skill import NoticeSnapshotSkill
from research_system.skills.policy_snapshot.skill import PolicySnapshotSkill
from research_system.skills.sector_snapshot.skill import SectorSnapshotSkill
from research_system.skills.sentiment_snapshot.skill import SentimentSnapshotSkill

from domain_packs.finance_ashare.tools.security_master import AShareSecurityMaster


_SYMBOL_SKILLS = {
    "market": MarketSnapshotSkill,
    "fundamental": FundamentalSnapshotSkill,
    "notice": NoticeSnapshotSkill,
    "capital_flow": CapitalFlowSnapshotSkill,
    "sentiment": SentimentSnapshotSkill,
    "events": EventsSnapshotSkill,
    "lhb": LHBSnapshotSkill,
}
_SCREEN_BASE_TYPES = ("market", "fundamental", "notice")
_SCREEN_ENRICHMENT_TYPES = ("capital_flow", "sentiment", "events", "lhb")


def collect_snapshots(
    *,
    query: str,
    symbols: Iterable[str],
    snapshot_types: Iterable[str],
    sector: str | None = None,
    cancellation=None,
) -> list[dict[str, Any]]:
    """Collect the Finance Pack's declared snapshots through shared vendor adapters."""
    requested = list(dict.fromkeys(str(item).strip() for item in snapshot_types if str(item).strip()))
    resolved_symbols = _unique_symbols(symbols)
    snapshots: list[dict[str, Any]] = []
    sector_snapshot: dict[str, Any] | None = None

    if "sector" in requested and sector and not _cancelled(cancellation):
        sector_snapshot = SectorSnapshotSkill().run(query=sector)
        snapshots.append(sector_snapshot)
        for symbol in _constituent_symbols(sector_snapshot):
            if symbol not in resolved_symbols:
                resolved_symbols.append(symbol)

    if "policy" in requested and sector and not _cancelled(cancellation):
        constituents = _constituents(sector_snapshot)
        snapshots.append(
            PolicySnapshotSkill().run(
                query=query,
                sector=sector,
                constituents=constituents,
            )
        )

    for symbol in resolved_symbols:
        for snapshot_type in requested:
            if _cancelled(cancellation):
                return snapshots
            skill_cls = _SYMBOL_SKILLS.get(snapshot_type)
            if skill_cls is not None:
                snapshots.append(skill_cls().run(query=query, symbol=symbol))
    return snapshots


def collect_snapshot_bundle(
    *,
    query: str,
    symbols: Iterable[str],
    snapshot_types: Iterable[str],
    intent: str,
    sector: str | None = None,
    candidate_pool_size: int = 8,
    enrichment_top_n: int = 2,
    skip_enrichment: bool = False,
    cancellation=None,
    security_master: AShareSecurityMaster | None = None,
) -> dict[str, Any]:
    """Build a traceable candidate universe and its staged snapshot collection."""
    requested = list(dict.fromkeys(str(item).strip() for item in snapshot_types if str(item).strip()))
    resolved_symbols = _unique_symbols(symbols)
    universe_rows: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    universe_source = "structured_symbols" if resolved_symbols else ""

    sector_requested = "sector" in requested or intent in {"stock_screen", "sector_research"}
    policy_requested = "policy" in requested
    sector_snapshot: dict[str, Any] | None = None
    if sector and sector_requested and not _cancelled(cancellation):
        sector_snapshot = SectorSnapshotSkill().run(query=sector)
        snapshots.append(sector_snapshot)
        universe_rows = _normalize_constituents(_constituents(sector_snapshot))
        if universe_rows:
            universe_source = "sector_constituents"

    if sector and policy_requested and not _cancelled(cancellation):
        snapshots.append(
            PolicySnapshotSkill().run(
                query=query,
                sector=sector,
                constituents=_constituents(sector_snapshot),
            )
        )

    if intent == "sector_research":
        return _bundle_result(
            snapshots=snapshots,
            symbols=[str(row.get("symbol") or "") for row in universe_rows if row.get("symbol")],
            requested=requested,
            universe_rows=universe_rows,
            universe_source=universe_source or "sector_unavailable",
            enrichment_targets=[],
        )

    pool_size = max(int(candidate_pool_size), 1)
    if intent == "stock_screen":
        if universe_rows:
            universe_rows = universe_rows[:pool_size]
        else:
            master = security_master or AShareSecurityMaster()
            universe_rows = master.candidate_universe(limit=pool_size, supplied_symbols=resolved_symbols)
            if universe_rows:
                universe_source = "structured_symbols" if resolved_symbols else "ashare_liquidity_ranked"
        if universe_rows:
            resolved_symbols = _unique_symbols(
                [row.get("symbol") for row in universe_rows if row.get("symbol")]
            )
        else:
            resolved_symbols = resolved_symbols[:pool_size]

    symbol_types = [item for item in requested if item in _SYMBOL_SKILLS]
    if intent == "stock_screen":
        symbol_types = [item for item in _SCREEN_BASE_TYPES if item in symbol_types or not symbol_types]
        if not symbol_types:
            symbol_types = list(_SCREEN_BASE_TYPES)
    snapshots.extend(
        collect_snapshots(
            query=query,
            symbols=resolved_symbols,
            snapshot_types=symbol_types,
            sector=None,
            cancellation=cancellation,
        )
    )

    enrichment_targets: list[str] = []
    if intent == "stock_screen" and not skip_enrichment and not _cancelled(cancellation):
        enrichment_targets = resolved_symbols[: max(int(enrichment_top_n), 0)]
        if enrichment_targets:
            snapshots.extend(
                collect_snapshots(
                    query=query,
                    symbols=enrichment_targets,
                    snapshot_types=_SCREEN_ENRICHMENT_TYPES,
                    sector=None,
                    cancellation=cancellation,
                )
            )

    return _bundle_result(
        snapshots=snapshots,
        symbols=resolved_symbols,
        requested=requested,
        universe_rows=universe_rows,
        universe_source=universe_source or ("structured_symbols" if resolved_symbols else "unavailable"),
        enrichment_targets=enrichment_targets,
    )


def _unique_symbols(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        symbol = str(value or "").strip()
        if symbol and symbol not in result:
            result.append(symbol)
    return result


def _constituents(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(snapshot, dict):
        return []
    facts = snapshot.get("facts") if isinstance(snapshot.get("facts"), dict) else {}
    rows = facts.get("top_constituents") or facts.get("constituent_breadth_source") or []
    return [dict(row) for row in rows if isinstance(row, dict)]


def _constituent_symbols(snapshot: dict[str, Any] | None) -> list[str]:
    result: list[str] = []
    for row in _constituents(snapshot):
        symbol = str(row.get("code") or row.get("symbol") or "").strip()
        if symbol and symbol not in result:
            result.append(symbol)
    return result


def _normalize_constituents(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        symbol = str(row.get("code") or row.get("symbol") or "").strip()
        if not symbol:
            continue
        result.append(
            {
                "symbol": symbol,
                "name": str(row.get("name") or symbol),
                "amount": row.get("amount") or row.get("成交额"),
                "market_cap": row.get("market_cap") or row.get("总市值"),
                "latest_price": row.get("latest_price") or row.get("price") or row.get("最新价"),
                "pct_change": row.get("pct_change") or row.get("涨跌幅"),
                "source": "sector_snapshot",
            }
        )
    return result


def _bundle_result(
    *,
    snapshots: list[dict[str, Any]],
    symbols: list[str],
    requested: list[str],
    universe_rows: list[dict[str, Any]],
    universe_source: str,
    enrichment_targets: list[str],
) -> dict[str, Any]:
    collected_types = list(
        dict.fromkeys(
            str(snapshot.get("snapshot_type") or "")
            for snapshot in snapshots
            if isinstance(snapshot, dict) and snapshot.get("snapshot_type")
        )
    )
    return {
        "snapshots": snapshots,
        "symbols": _unique_symbols(symbols),
        "candidate_universe": universe_rows,
        "collection": {
            "universe_source": universe_source,
            "candidate_count": len(universe_rows) if universe_rows else len(symbols),
            "requested_snapshot_types": requested,
            "collected_snapshot_types": collected_types,
            "enrichment_snapshot_types": list(_SCREEN_ENRICHMENT_TYPES),
            "enrichment_targets": enrichment_targets,
            "degraded": not bool(snapshots),
        },
    }


def _cancelled(cancellation) -> bool:
    return bool(cancellation is not None and cancellation.is_set())


__all__ = ["collect_snapshot_bundle", "collect_snapshots"]
