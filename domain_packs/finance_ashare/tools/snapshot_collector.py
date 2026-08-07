from __future__ import annotations

from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import json
from pathlib import Path
from typing import Any

from domain_packs.finance_ashare.skills.capital_flow_snapshot.skill import CapitalFlowSnapshotSkill
from domain_packs.finance_ashare.skills.events_snapshot.skill import EventsSnapshotSkill
from domain_packs.finance_ashare.skills.fundamental_snapshot.skill import FundamentalSnapshotSkill
from domain_packs.finance_ashare.skills.lhb_snapshot.skill import LHBSnapshotSkill
from domain_packs.finance_ashare.skills.market_snapshot.skill import MarketSnapshotSkill
from domain_packs.finance_ashare.skills.notice_snapshot.skill import NoticeSnapshotSkill
from domain_packs.finance_ashare.skills.policy_snapshot.skill import PolicySnapshotSkill
from domain_packs.finance_ashare.skills.sector_snapshot.skill import SectorSnapshotSkill
from domain_packs.finance_ashare.skills.sentiment_snapshot.skill import SentimentSnapshotSkill
from domain_packs.finance_ashare.skills.uzi_source_snapshot.skill import (
    ChainSnapshotSkill,
    ContestsSnapshotSkill,
    FundHoldersSnapshotSkill,
    FuturesSnapshotSkill,
    GovernanceSnapshotSkill,
    IndustrySnapshotSkill,
    KlineSnapshotSkill,
    MacroSnapshotSkill,
    MaterialsSnapshotSkill,
    MoatSnapshotSkill,
    PeersSnapshotSkill,
    ResearchSnapshotSkill,
    SimilarStocksSnapshotSkill,
    TrapSnapshotSkill,
    ValuationSnapshotSkill,
)

from domain_packs.finance_ashare.tools.security_master import AShareSecurityMaster
from research_system.runtime.storage.jsonl import read_json, write_json
from research_system.adapters.vendors.provider_execution import classify_provider_exception, failure_facts
from research_system.adapters.vendors.provider_registry import VendorProviderRegistry
from research_system.runtime.platform.settings import Settings


_SYMBOL_SKILLS = {
    "market": MarketSnapshotSkill,
    "fundamental": FundamentalSnapshotSkill,
    "notice": NoticeSnapshotSkill,
    "capital_flow": CapitalFlowSnapshotSkill,
    "sentiment": SentimentSnapshotSkill,
    "events": EventsSnapshotSkill,
    "lhb": LHBSnapshotSkill,
    "kline": KlineSnapshotSkill,
    "macro": MacroSnapshotSkill,
    "peers": PeersSnapshotSkill,
    "chain": ChainSnapshotSkill,
    "research": ResearchSnapshotSkill,
    "industry": IndustrySnapshotSkill,
    "materials": MaterialsSnapshotSkill,
    "futures": FuturesSnapshotSkill,
    "valuation": ValuationSnapshotSkill,
    "governance": GovernanceSnapshotSkill,
    "moat": MoatSnapshotSkill,
    "trap": TrapSnapshotSkill,
    "contests": ContestsSnapshotSkill,
    "fund_holders": FundHoldersSnapshotSkill,
    "similar_stocks": SimilarStocksSnapshotSkill,
}
_SCREEN_BASE_TYPES = ("market", "fundamental", "notice")
_SCREEN_ENRICHMENT_TYPES = ("capital_flow", "sentiment", "events", "lhb")
_SNAPSHOT_CACHE_TTLS = {
    "market": 3600,
    "capital_flow": 3600,
    "sentiment": 3600,
    "lhb": 3600,
    "kline": 3600,
    "fundamental": 86400,
    "notice": 86400,
    "events": 86400,
    "sector": 86400,
    "policy": 86400,
    "fund_holders": 90 * 86400,
    "similar_stocks": 86400,
}
_DEFAULT_SNAPSHOT_CACHE_TTL = 43200
_SNAPSHOT_CACHE_SCHEMA_VERSIONS = {
    "market": "valuation-history-v2",
    "fundamental": "financial-history-v2",
    "kline": "kline-550-v2",
}
_UNAVAILABLE_QUALITY_STATUSES = {
    "degraded",
    "data_gap",
    "assumption_only",
    "no_data",
    "provider_unavailable",
    "rate_limited",
    "parse_failed",
}
_SNAPSHOT_PROVIDER_HINTS = {
    "market": "akshare",
    "fundamental": "akshare",
    "notice": "cninfo",
    "capital_flow": "akshare",
    "sentiment": "akshare",
    "events": "akshare",
    "lhb": "akshare",
    "sector": "akshare",
    "policy": "cninfo",
    "fund_holders": "akshare",
    "similar_stocks": "akshare",
}


def collect_snapshots(
    *,
    query: str,
    symbols: Iterable[str],
    snapshot_types: Iterable[str],
    sector: str | None = None,
    cancellation=None,
    cache_root: Path | None = None,
    research_profile: str | None = None,
    max_workers: int | None = None,
    provider_registry: VendorProviderRegistry | None = None,
) -> list[dict[str, Any]]:
    """Collect the Finance Pack's declared snapshots through shared vendor adapters."""
    requested = list(dict.fromkeys(str(item).strip() for item in snapshot_types if str(item).strip()))
    providers = provider_registry or _default_provider_registry()
    resolved_symbols = _unique_symbols(symbols)
    snapshots: list[dict[str, Any]] = []
    sector_snapshot: dict[str, Any] | None = None

    if "sector" in requested and sector and not _cancelled(cancellation):
        sector_snapshot = _load_snapshot_cache(
            cache_root=cache_root,
            snapshot_type="sector",
            target=sector,
            profile=research_profile,
        )
        if sector_snapshot is None:
            sector_snapshot = _instantiate_skill(SectorSnapshotSkill, providers).run(query=sector)
            _save_snapshot_cache(
                cache_root=cache_root,
                snapshot_type="sector",
                target=sector,
                profile=research_profile,
                snapshot=sector_snapshot,
            )
        snapshots.append(sector_snapshot)
        for symbol in _constituent_symbols(sector_snapshot):
            if symbol not in resolved_symbols:
                resolved_symbols.append(symbol)

    if "policy" in requested and sector and not _cancelled(cancellation):
        constituents = _constituents(sector_snapshot)
        policy_snapshot = _load_snapshot_cache(
            cache_root=cache_root,
            snapshot_type="policy",
            target=sector,
            profile=research_profile,
        )
        if policy_snapshot is None:
            policy_snapshot = _instantiate_skill(PolicySnapshotSkill, providers).run(
                query=query,
                sector=sector,
                constituents=constituents,
            )
            _save_snapshot_cache(
                cache_root=cache_root,
                snapshot_type="policy",
                target=sector,
                profile=research_profile,
                snapshot=policy_snapshot,
            )
        snapshots.append(policy_snapshot)

    tasks = [
        (index, symbol, snapshot_type)
        for index, (symbol, snapshot_type) in enumerate(
            (pair for symbol in resolved_symbols for pair in ((symbol, item) for item in requested))
        )
        if snapshot_type in _SYMBOL_SKILLS
    ]
    if not tasks:
        for snapshot_type in requested:
            if snapshot_type in {"sector", "policy"} and not sector:
                snapshots.append(_missing_scope_snapshot(query=query, snapshot_type=snapshot_type))
        return snapshots
    results: dict[int, dict[str, Any]] = {}
    pending_tasks: list[tuple[int, str, str]] = []
    for index, symbol, snapshot_type in tasks:
        cached = _load_snapshot_cache(
            cache_root=cache_root,
            snapshot_type=snapshot_type,
            target=symbol,
            profile=research_profile,
        )
        if cached is None:
            pending_tasks.append((index, symbol, snapshot_type))
        else:
            results[index] = cached
    if pending_tasks:
        requested_workers = 8 if max_workers is None else max(int(max_workers), 1)
        executor = ThreadPoolExecutor(
            max_workers=min(requested_workers, len(pending_tasks)),
            thread_name_prefix="finance-snapshot",
        )
        futures = {
            executor.submit(_run_snapshot_skill, query, symbol, snapshot_type, providers): (index, symbol, snapshot_type)
            for index, symbol, snapshot_type in pending_tasks
        }
        try:
            for future in as_completed(futures):
                index, symbol, snapshot_type = futures[future]
                if _cancelled(cancellation):
                    for pending in futures:
                        pending.cancel()
                    break
                result = future.result()
                results[index] = result
                _save_snapshot_cache(
                    cache_root=cache_root,
                    snapshot_type=snapshot_type,
                    target=symbol,
                    profile=research_profile,
                    snapshot=result,
                )
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
    snapshots.extend(results[index] for index, _symbol, _snapshot_type in tasks if index in results)
    for snapshot_type in requested:
        if snapshot_type in {"sector", "policy"} and not sector:
            snapshots.append(_missing_scope_snapshot(query=query, snapshot_type=snapshot_type))
    return snapshots


def _run_snapshot_skill(
    query: str,
    symbol: str,
    snapshot_type: str,
    provider_registry: VendorProviderRegistry | None = None,
) -> dict[str, Any]:
    skill_cls = _SYMBOL_SKILLS[snapshot_type]
    try:
        return _instantiate_skill(skill_cls, provider_registry).run(query=query, symbol=symbol)
    except Exception as exc:
        status = classify_provider_exception(exc)
        return {
            "snapshot_type": snapshot_type,
            "symbol": symbol,
            "query": query,
            "facts": failure_facts(status),
            "degraded": True,
            "degrade_reason": status,
            "source_trace": [{"status": status, "error_type": exc.__class__.__name__}],
        }


def _instantiate_skill(skill_cls, provider_registry: VendorProviderRegistry | None):
    if provider_registry is not None and "provider_registry" in inspect.signature(skill_cls).parameters:
        return skill_cls(provider_registry=provider_registry)
    return skill_cls()


def _default_provider_registry() -> VendorProviderRegistry:
    repository_root = Path(__file__).resolve().parents[3]
    return VendorProviderRegistry(Settings.from_env(repository_root))


def _snapshot_cache_path(
    *,
    cache_root: Path | None,
    snapshot_type: str,
    target: str,
    profile: str | None,
) -> Path | None:
    if cache_root is None:
        return None
    payload = _snapshot_cache_context(
        snapshot_type=snapshot_type,
        target=target,
        profile=profile,
    )
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    return Path(cache_root) / "finance_ashare" / "snapshot_partitions" / f"{digest}.json"


def _snapshot_cache_context(
    *,
    snapshot_type: str,
    target: str,
    profile: str | None,
) -> dict[str, Any]:
    """Describe the Pack-owned raw Provider cache, not a reusable Board fact."""

    return {
        "cache_layer": "provider_raw",
        "domain_id": "finance_ashare",
        "artifact_type": "snapshot",
        "snapshot_type": snapshot_type,
        "provider": _SNAPSHOT_PROVIDER_HINTS.get(snapshot_type, snapshot_type),
        "schema_version": _SNAPSHOT_CACHE_SCHEMA_VERSIONS.get(snapshot_type, "1"),
        "target": str(target).strip(),
        "profile": str(profile or "").strip().lower(),
    }


def _snapshot_quality(snapshot: dict[str, Any]) -> str:
    facts = snapshot.get("facts") if isinstance(snapshot.get("facts"), dict) else {}
    declared = str(
        snapshot.get("quality_status")
        or snapshot.get("status")
        or facts.get("availability")
        or ""
    ).strip().lower()
    if declared in _UNAVAILABLE_QUALITY_STATUSES:
        return declared
    return "degraded" if snapshot.get("degraded") else "available"


def _load_snapshot_cache(
    *,
    cache_root: Path | None,
    snapshot_type: str,
    target: str,
    profile: str | None,
) -> dict[str, Any] | None:
    path = _snapshot_cache_path(
        cache_root=cache_root,
        snapshot_type=snapshot_type,
        target=target,
        profile=profile,
    )
    if path is None:
        return None
    payload = read_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("snapshot"), dict):
        return None
    if payload.get("cache_layer") != "provider_raw":
        return None
    if payload.get("cache_context") != _snapshot_cache_context(
        snapshot_type=snapshot_type,
        target=target,
        profile=profile,
    ):
        return None
    try:
        expires_at = datetime.fromisoformat(str(payload.get("expires_at") or ""))
    except ValueError:
        return None
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        return None
    snapshot = dict(payload["snapshot"])
    if _snapshot_quality(snapshot) != "available":
        # Degraded/data-gap records remain inspectable on disk but can never
        # silently become a reusable fact in a later Run.
        return None
    return snapshot


def _save_snapshot_cache(
    *,
    cache_root: Path | None,
    snapshot_type: str,
    target: str,
    profile: str | None,
    snapshot: dict[str, Any],
) -> None:
    path = _snapshot_cache_path(
        cache_root=cache_root,
        snapshot_type=snapshot_type,
        target=target,
        profile=profile,
    )
    if path is None:
        return
    now = datetime.now(timezone.utc)
    ttl = _SNAPSHOT_CACHE_TTLS.get(snapshot_type, _DEFAULT_SNAPSHOT_CACHE_TTL)
    write_json(
        path,
        {
            "cache_layer": "provider_raw",
            "domain_id": "finance_ashare",
            "artifact_type": "snapshot",
            "snapshot_type": snapshot_type,
            "provider": _SNAPSHOT_PROVIDER_HINTS.get(snapshot_type, snapshot_type),
            "schema_version": "1",
            "profile": str(profile or "").strip().lower() or None,
            "cache_context": _snapshot_cache_context(
                snapshot_type=snapshot_type,
                target=target,
                profile=profile,
            ),
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=ttl)).isoformat(),
            "quality_status": _snapshot_quality(snapshot),
            "snapshot": snapshot,
        },
    )


def _missing_scope_snapshot(*, query: str, snapshot_type: str) -> dict[str, Any]:
    return {
        "snapshot_type": snapshot_type,
        "query": query,
        "facts": {
            "availability": "no_data",
            "data_gap": [f"{snapshot_type}_scope_missing"],
        },
        "degraded": True,
        "degrade_reason": f"{snapshot_type}_scope_missing",
        "missing_fields": ["sector"],
        "source": {"provider_id": "finance_pack.scope_resolver"},
    }


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
    cache_root: Path | None = None,
    research_profile: str | None = None,
    provider_registry: VendorProviderRegistry | None = None,
) -> dict[str, Any]:
    """Build a traceable candidate universe and its staged snapshot collection."""
    requested = list(dict.fromkeys(str(item).strip() for item in snapshot_types if str(item).strip()))
    providers = provider_registry or _default_provider_registry()
    resolved_symbols = _unique_symbols(symbols)
    universe_rows: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    universe_source = "structured_symbols" if resolved_symbols else ""

    sector_requested = "sector" in requested or intent in {"stock_screen", "sector_research"}
    policy_requested = "policy" in requested
    sector_snapshot: dict[str, Any] | None = None
    if sector and sector_requested and not _cancelled(cancellation):
        sector_snapshot = _load_snapshot_cache(
            cache_root=cache_root,
            snapshot_type="sector",
            target=sector,
            profile=research_profile,
        )
        if sector_snapshot is None:
            sector_snapshot = _instantiate_skill(SectorSnapshotSkill, providers).run(query=sector)
            _save_snapshot_cache(
                cache_root=cache_root,
                snapshot_type="sector",
                target=sector,
                profile=research_profile,
                snapshot=sector_snapshot,
            )
        snapshots.append(sector_snapshot)
        universe_rows = _normalize_constituents(_constituents(sector_snapshot))
        if universe_rows:
            universe_source = "sector_constituents"

    if sector and policy_requested and not _cancelled(cancellation):
        policy_snapshot = _load_snapshot_cache(
            cache_root=cache_root,
            snapshot_type="policy",
            target=sector,
            profile=research_profile,
        )
        if policy_snapshot is None:
            policy_snapshot = _instantiate_skill(PolicySnapshotSkill, providers).run(
                query=query,
                sector=sector,
                constituents=_constituents(sector_snapshot),
            )
            _save_snapshot_cache(
                cache_root=cache_root,
                snapshot_type="policy",
                target=sector,
                profile=research_profile,
                snapshot=policy_snapshot,
            )
        snapshots.append(
            policy_snapshot
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
    if symbol_types:
        snapshots.extend(
            collect_snapshots(
                query=query,
                symbols=resolved_symbols,
                snapshot_types=symbol_types,
                sector=None,
                cancellation=cancellation,
                cache_root=cache_root,
                research_profile=research_profile,
                provider_registry=providers,
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
                    cache_root=cache_root,
                    research_profile=research_profile,
                    provider_registry=providers,
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
    normalized_snapshots = [_normalize_snapshot_quality(snapshot) for snapshot in snapshots]
    collected_types = list(
        dict.fromkeys(
            str(snapshot.get("snapshot_type") or "")
            for snapshot in normalized_snapshots
            if isinstance(snapshot, dict) and snapshot.get("snapshot_type")
        )
    )
    degraded_rows = [
        snapshot
        for snapshot in normalized_snapshots
        if isinstance(snapshot, dict)
        and str(snapshot.get("quality_status") or "") in _UNAVAILABLE_QUALITY_STATUSES
    ]
    quality_counts: dict[str, int] = {}
    for snapshot in normalized_snapshots:
        quality = str(snapshot.get("quality_status") or "no_data")
        quality_counts[quality] = quality_counts.get(quality, 0) + 1
    if not normalized_snapshots:
        collection_quality = "no_data"
    elif quality_counts.get("available", 0) == 0:
        collection_quality = next(
            (
                status
                for status in ("rate_limited", "provider_unavailable", "parse_failed", "no_data", "data_gap")
                if quality_counts.get(status, 0)
            ),
            "no_data",
        )
    elif degraded_rows:
        collection_quality = "degraded"
    else:
        collection_quality = "available"
    return {
        "snapshots": normalized_snapshots,
        "symbols": _unique_symbols(symbols),
        "candidate_universe": universe_rows,
        "collection": {
            "universe_source": universe_source,
            "candidate_count": len(universe_rows) if universe_rows else len(symbols),
            "requested_snapshot_types": requested,
            "collected_snapshot_types": collected_types,
            "enrichment_snapshot_types": list(_SCREEN_ENRICHMENT_TYPES),
            "enrichment_targets": enrichment_targets,
            "quality_status": collection_quality,
            "quality_counts": quality_counts,
            "degraded": bool(degraded_rows) or not bool(normalized_snapshots),
            "degrade_reasons": list(
                dict.fromkeys(
                    str(
                        snapshot.get("degrade_reason")
                        or snapshot.get("degrade_reasons", [None])[0]
                        or "snapshot_degraded"
                    )
                    for snapshot in degraded_rows
                )
            ),
            "parallelism": "per_snapshot_dimension",
        },
    }


def _normalize_snapshot_quality(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Promote source quality to a stable per-dimension contract."""

    normalized = dict(snapshot)
    facts = normalized.get("facts") if isinstance(normalized.get("facts"), dict) else {}
    declared = str(
        normalized.get("quality_status")
        or normalized.get("status")
        or facts.get("availability")
        or ""
    ).strip().lower()
    if declared not in {"available", *_UNAVAILABLE_QUALITY_STATUSES}:
        declared = "degraded" if bool(normalized.get("degraded")) else "available"
    if bool(normalized.get("degraded")) and declared == "available":
        declared = "degraded"
    if declared in _UNAVAILABLE_QUALITY_STATUSES and not bool(normalized.get("degraded")):
        normalized["degraded"] = True
    reasons = [
        str(item).strip()
        for item in (
            list(normalized.get("degrade_reasons") or [])
            + ([normalized.get("degrade_reason")] if normalized.get("degrade_reason") else [])
            + list(facts.get("data_gap") or [])
        )
        if str(item).strip()
    ]
    normalized["quality_status"] = declared
    normalized["degrade_reasons"] = list(dict.fromkeys(reasons))
    normalized["missing_fields"] = list(
        dict.fromkeys(
            str(item).strip()
            for item in list(normalized.get("missing_fields") or [])
            if str(item).strip()
        )
    )
    normalized["scope"] = (
        "sector"
        if normalized.get("sector")
        or str(normalized.get("snapshot_type") or "") in {"sector", "policy", "macro"}
        else "symbol"
    )
    normalized["data_period"] = str(
        normalized.get("data_period") or normalized.get("snapshot_date") or ""
    ) or None
    return normalized


def _cancelled(cancellation) -> bool:
    return bool(cancellation is not None and cancellation.is_set())


__all__ = ["collect_snapshot_bundle", "collect_snapshots"]
