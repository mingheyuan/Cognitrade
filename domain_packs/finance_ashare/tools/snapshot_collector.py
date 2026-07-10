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


_SYMBOL_SKILLS = {
    "market": MarketSnapshotSkill,
    "fundamental": FundamentalSnapshotSkill,
    "notice": NoticeSnapshotSkill,
    "capital_flow": CapitalFlowSnapshotSkill,
    "sentiment": SentimentSnapshotSkill,
    "events": EventsSnapshotSkill,
    "lhb": LHBSnapshotSkill,
}


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


def _cancelled(cancellation) -> bool:
    return bool(cancellation is not None and cancellation.is_set())


__all__ = ["collect_snapshots"]
