"""Explicit migration-only importer for UZI raw cache dimensions.

This module is intentionally not imported by the normal Agent OS collector.
It converts validated cache facts into Pack Snapshot drafts so an operator can
replay a historical source without making the old cache a runtime database.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from domain_packs.finance_ashare.subagents.uzi_methodology import UZI_SOURCE_COMMIT
from research_system.schemas.artifacts import ArtifactDraft


_DIMENSION_MAP = {
    "basic": "market",
    "0_basic": "market",
    "financials": "fundamental",
    "1_financials": "fundamental",
    "kline": "kline",
    "2_kline": "kline",
    "macro": "macro",
    "3_macro": "macro",
    "peers": "peers",
    "4_peers": "peers",
    "chain": "chain",
    "5_chain": "chain",
    "research": "research",
    "6_research": "research",
    "industry": "industry",
    "7_industry": "industry",
    "materials": "materials",
    "8_materials": "materials",
    "futures": "futures",
    "9_futures": "futures",
    "valuation": "valuation",
    "10_valuation": "valuation",
    "governance": "governance",
    "11_governance": "governance",
    "capital_flow": "capital_flow",
    "12_capital_flow": "capital_flow",
    "policy": "policy",
    "13_policy": "policy",
    "moat": "moat",
    "14_moat": "moat",
    "events": "events",
    "15_events": "events",
    "lhb": "lhb",
    "16_lhb": "lhb",
    "sentiment": "sentiment",
    "17_sentiment": "sentiment",
    "trap": "trap",
    "18_trap": "trap",
    "contests": "contests",
    "19_contests": "contests",
    "fund_holders": "fund_holders",
    "similar_stocks": "similar_stocks",
}


def import_uzi_cache_file(
    path: Path,
    *,
    symbol: str | None = None,
    query: str = "UZI cache import",
) -> list[ArtifactDraft]:
    """Convert one UZI ``raw_data.json`` into validated Snapshot drafts."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("UZI cache root must be an object")
    effective_symbol = str(symbol or payload.get("ticker") or payload.get("symbol") or "").strip()
    if not effective_symbol:
        raise ValueError("UZI cache import requires a ticker/symbol")
    raw_dimensions = payload.get("raw_data")
    if not isinstance(raw_dimensions, dict):
        raw_dimensions = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if isinstance(payload.get("dimensions"), dict):
        raw_dimensions = payload["dimensions"]
    fetched_at = str(payload.get("fetched_at") or payload.get("retrieved_at") or "") or None
    drafts: list[ArtifactDraft] = []
    for raw_name, raw_value in raw_dimensions.items():
        dimension = _DIMENSION_MAP.get(str(raw_name).strip().lower())
        if dimension is None or not isinstance(raw_value, (dict, list)):
            continue
        raw_record = raw_value if isinstance(raw_value, dict) else {"rows": raw_value}
        facts = (
            dict(raw_record.get("data") or {})
            if isinstance(raw_record.get("data"), dict)
            else dict(raw_record)
        )
        facts = _normalize_cache_facts(dimension, facts)
        quality_status = _cache_quality(facts)
        missing_fields = [
            str(item).strip()
            for item in list(facts.get("missing_fields") or facts.get("data_gap") or [])
            if str(item).strip()
        ]
        source_id = f"uzi_cache:{effective_symbol}:{dimension}"
        content = {
            "snapshot_type": dimension,
            "scope": "symbol",
            "symbol": effective_symbol,
            "sector": None,
            "query": query,
            "snapshot_date": fetched_at,
            "data_period": str(facts.get("data_period") or fetched_at or "") or None,
            "retrieved_at": fetched_at,
            "quality_status": quality_status,
            "missing_fields": missing_fields,
            "degrade_reasons": missing_fields if quality_status != "available" else [],
            "facts": facts,
            "source": {
                "source_system": "uzi_cache",
                "source_commit": UZI_SOURCE_COMMIT,
                "cache_path": str(path),
            },
            "source_refs": [
                {
                    "type": "uzi_cache",
                    "id": source_id,
                    "provider": "uzi_cache",
                    "version": UZI_SOURCE_COMMIT,
                }
            ],
        }
        drafts.append(
            ArtifactDraft(
                artifact_type="snapshot",
                content=content,
                source_refs=content["source_refs"],
                confidence=0.45 if quality_status != "available" else 0.7,
                risk_flags=missing_fields,
                metadata={
                    "source_system": "uzi_cache",
                    "source_commit": UZI_SOURCE_COMMIT,
                    "quality_status": quality_status,
                    "cache_import": True,
                },
            )
        )
    return drafts


def _cache_quality(facts: dict[str, Any]) -> str:
    declared = str(
        facts.get("quality_status")
        or facts.get("availability")
        or facts.get("status")
        or ""
    ).strip().lower()
    if declared in {"degraded", "data_gap", "assumption_only"}:
        return declared
    if facts.get("degraded") or facts.get("missing_fields") or facts.get("data_gap"):
        return "degraded"
    return "available"


def _normalize_cache_facts(dimension: str, facts: dict[str, Any]) -> dict[str, Any]:
    """Map numbered UZI cache payloads into Pack snapshot field groups."""

    normalized = dict(facts)
    if dimension == "market" and not isinstance(normalized.get("market_overview"), dict):
        normalized["market_overview"] = {
            "latest_price": normalized.get("price"),
            "pct_change": normalized.get("change_pct"),
            "pe_ttm": normalized.get("pe_ttm"),
            "pb": normalized.get("pb"),
            "market_cap": normalized.get("market_cap_raw") or normalized.get("market_cap"),
            "total_shares": normalized.get("total_shares"),
            "industry": normalized.get("industry"),
        }
    if dimension == "fundamental" and not isinstance(normalized.get("financial_abstract"), dict):
        revenue_history = list(normalized.get("revenue_history") or [])
        profit_history = list(normalized.get("net_profit_history") or [])
        roe_history = list(normalized.get("roe_history") or [])
        health = dict(normalized.get("financial_health") or {})
        normalized["financial_abstract"] = {
            "revenue": revenue_history[-1] if revenue_history else None,
            "net_profit": profit_history[-1] if profit_history else None,
            "roe": roe_history[-1] if roe_history else None,
            "debt_ratio": health.get("debt_ratio"),
            "net_margin": health.get("net_margin_pct"),
            "revenue_growth": normalized.get("revenue_growth"),
        }
    if dimension == "valuation":
        dcf = dict(normalized.get("dcf_simple") or {})
        assumptions = dict(dcf.get("assumptions") or {})
        if assumptions.get("fcf_latest") is not None:
            normalized["fcf_latest_yi"] = float(assumptions["fcf_latest"]) / 100_000_000
        normalized["market_price"] = normalized.get("current_price")
    return normalized


__all__ = ["import_uzi_cache_file"]
