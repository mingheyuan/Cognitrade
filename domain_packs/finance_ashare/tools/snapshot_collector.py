from __future__ import annotations

from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
import hashlib
import importlib
import inspect
import json
import math
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import UUID

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
from domain_packs.finance_ashare.vendor.uzi_3_9_2 import Uzi392FetcherRuntime

from domain_packs.finance_ashare.tools.security_master import AShareSecurityMaster
from domain_packs.finance_ashare.tools.uzi_data_recovery import (
    assess_integrity,
    recover_suggested_fixes_until_exhausted,
)
from research_system.runtime.storage.jsonl import read_json, write_json
from research_system.adapters.vendors.akshare_utils import initialize_akshare_js_runtime
from research_system.adapters.vendors.provider_execution import classify_provider_exception, failure_facts
from research_system.adapters.vendors.provider_registry import VendorProviderRegistry
from research_system.runtime.platform.settings import Settings


_SYMBOL_SKILLS = {
    "market": MarketSnapshotSkill,
    "fundamental": FundamentalSnapshotSkill,
    "notice": NoticeSnapshotSkill,
    "policy": PolicySnapshotSkill,
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
_CANONICAL_SYMBOL_SKILLS = dict(_SYMBOL_SKILLS)
_UZI_FETCHER_BY_SNAPSHOT_TYPE = {
    "market": "0_basic",
    "fundamental": "1_financials",
    "kline": "2_kline",
    "macro": "3_macro",
    "peers": "4_peers",
    "chain": "5_chain",
    "research": "6_research",
    "industry": "7_industry",
    "materials": "8_materials",
    "futures": "9_futures",
    "valuation": "10_valuation",
    "governance": "11_governance",
    "capital_flow": "12_capital_flow",
    "policy": "13_policy",
    "moat": "14_moat",
    "events": "15_events",
    "lhb": "16_lhb",
    "sentiment": "17_sentiment",
    "trap": "18_trap",
    "contests": "19_contests",
    "fund_holders": "fund_holders",
    "similar_stocks": "similar_stocks",
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
    "fundamental": "financial-history-v3",
    "kline": "kline-550-v2",
    "peers": "global-peers-v2",
    "materials": "uzi-industry-materials-v2",
    "futures": "materials-linked-v2",
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
_UZI_CAPITAL_FLOW_COMPAT_LOCK = Lock()
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


def normalize_json_value(value: Any) -> Any:
    """Convert provider-native scalar values into strict JSON values.

    AkShare and pandas may expose ``date``, ``Timestamp`` or NumPy scalar
    objects inside otherwise ordinary dictionaries.  Pack artifacts and the
    raw-provider cache are JSON contracts, so those runtime-specific values
    must be normalized at the Pack boundary instead of relying on permissive
    ``default=str`` persistence.
    """

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value) if value.is_finite() else None
    if isinstance(value, Enum):
        return normalize_json_value(value.value)
    if isinstance(value, (Path, UUID)):
        return str(value)
    if isinstance(value, dict):
        return {
            str(normalize_json_value(key)): normalize_json_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [normalize_json_value(item) for item in value]

    # NumPy scalar values expose ``item``; pandas Timestamp-like values expose
    # ``to_pydatetime``.  Keep these imports optional because providers are
    # allowed to run without pandas/NumPy in lightweight tests.
    to_pydatetime = getattr(value, "to_pydatetime", None)
    if callable(to_pydatetime):
        converted = to_pydatetime()
        if converted is not value:
            return normalize_json_value(converted)
    scalar_item = getattr(value, "item", None)
    if callable(scalar_item):
        converted = scalar_item()
        if converted is not value:
            return normalize_json_value(converted)
    if str(value) in {"<NA>", "NaT", "nan"}:
        return None
    return value


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
            policy_snapshot = _run_uzi_fetcher_snapshot(
                query=query,
                symbol=None,
                snapshot_type="policy",
                provider_registry=providers,
                industry=sector,
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
        if snapshot_type in _SYMBOL_SKILLS and not (snapshot_type == "policy" and sector)
    ]
    if not tasks:
        for snapshot_type in requested:
            if snapshot_type == "sector" and not sector:
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
        initialize_akshare_js_runtime()
        # UZI runs dimension 0 first because the dependent macro/industry/
        # futures/policy dimensions consume the resolved real industry.
        basic_tasks = [task for task in pending_tasks if task[2] == "market"]
        for index, symbol, snapshot_type in basic_tasks:
            result = _run_snapshot_skill(query, symbol, snapshot_type, providers)
            results[index] = result
            _save_snapshot_cache(
                cache_root=cache_root,
                snapshot_type=snapshot_type,
                target=symbol,
                profile=research_profile,
                snapshot=result,
            )
        pending_tasks = [task for task in pending_tasks if task[2] != "market"]
        basic_by_symbol = {
            symbol: raw
            for index, symbol, snapshot_type in tasks
            if snapshot_type == "market"
            and index in results
            and (raw := _uzi_raw_result(results[index])) is not None
        }
        # UZI main makes dimension 9 depend on dimension 8 so the futures
        # contract is the material actually identified for the company rather
        # than a generic hard-coded commodity. Material tasks therefore form
        # a small first wave; all remaining independent tasks stay parallel.
        material_tasks = [task for task in pending_tasks if task[2] == "materials"]
        for index, symbol, snapshot_type in material_tasks:
            dependencies = (
                {"0_basic": basic_by_symbol[symbol]}
                if symbol in basic_by_symbol
                else None
            )
            result = _run_snapshot_skill(
                query,
                symbol,
                snapshot_type,
                providers,
                dependencies,
            )
            results[index] = result
            _save_snapshot_cache(
                cache_root=cache_root,
                snapshot_type=snapshot_type,
                target=symbol,
                profile=research_profile,
                snapshot=result,
            )
        pending_tasks = [task for task in pending_tasks if task[2] != "materials"]
        material_by_symbol = {
            symbol: raw
            for index, symbol, snapshot_type in tasks
            if snapshot_type == "materials"
            and index in results
            and (raw := _uzi_raw_result(results[index])) is not None
        }
        material_snapshot_by_symbol = {
            symbol: results[index]
            for index, symbol, snapshot_type in tasks
            if snapshot_type == "materials" and index in results
        }
        requested_workers = 8 if max_workers is None else max(int(max_workers), 1)
        if pending_tasks:
            executor = ThreadPoolExecutor(
                max_workers=min(requested_workers, len(pending_tasks)),
                thread_name_prefix="finance-snapshot",
            )
            futures = {}
            for index, symbol, snapshot_type in pending_tasks:
                dependencies: dict[str, dict[str, Any]] = {}
                if symbol in basic_by_symbol:
                    dependencies["0_basic"] = basic_by_symbol[symbol]
                if snapshot_type == "futures":
                    if _uses_uzi_fetcher(snapshot_type) and symbol in material_by_symbol:
                        dependencies["8_materials"] = material_by_symbol[symbol]
                    elif symbol in material_snapshot_by_symbol:
                        dependencies["materials"] = material_snapshot_by_symbol[symbol]
                future = executor.submit(
                    _run_snapshot_skill,
                    query,
                    symbol,
                    snapshot_type,
                    providers,
                    dependencies or None,
                )
                futures[future] = (index, symbol, snapshot_type)
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
        if snapshot_type == "sector" and not sector:
            snapshots.append(_missing_scope_snapshot(query=query, snapshot_type=snapshot_type))
    return snapshots


def _run_snapshot_skill(
    query: str,
    symbol: str,
    snapshot_type: str,
    provider_registry: VendorProviderRegistry | None = None,
    dependencies: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if _uses_uzi_fetcher(snapshot_type):
        raw_basic = (dependencies or {}).get("0_basic") or {}
        industry = str((raw_basic.get("data") or {}).get("industry") or "").strip() or None
        return dict(
            normalize_json_value(
                _run_uzi_fetcher_snapshot(
                    query=query,
                    symbol=symbol,
                    snapshot_type=snapshot_type,
                    provider_registry=provider_registry,
                    industry=industry,
                    dependencies=dependencies,
                )
            )
        )
    skill_cls = _SYMBOL_SKILLS[snapshot_type]
    try:
        skill = _instantiate_skill(skill_cls, provider_registry)
        kwargs: dict[str, Any] = {"query": query, "symbol": symbol}
        if "dependencies" in inspect.signature(skill.run).parameters:
            kwargs["dependencies"] = dependencies or {}
        return dict(normalize_json_value(skill.run(**kwargs)))
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


def _uses_uzi_fetcher(snapshot_type: str) -> bool:
    return (
        snapshot_type in _UZI_FETCHER_BY_SNAPSHOT_TYPE
        and _SYMBOL_SKILLS.get(snapshot_type) is _CANONICAL_SYMBOL_SKILLS.get(snapshot_type)
    )


def _run_uzi_fetcher_snapshot(
    *,
    query: str,
    symbol: str | None,
    snapshot_type: str,
    provider_registry: VendorProviderRegistry | None,
    industry: str | None = None,
    dependencies: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    fetcher_id = _UZI_FETCHER_BY_SNAPSHOT_TYPE[snapshot_type]
    settings = (
        provider_registry.settings
        if provider_registry is not None
        else Settings.from_env(Path(__file__).resolve().parents[3])
    )
    runtime = Uzi392FetcherRuntime(workspace_root=settings.workspace_root)
    options: dict[str, Any] | None = None
    if fetcher_id == "19_contests":
        allowlist = {str(item).strip().lower() for item in settings.sensitive_source_allowlist}
        xueqiu_enabled = (
            settings.sensitive_sources_enabled
            and "xueqiu" in allowlist
            and bool(settings.xueqiu_cookie)
        )
        options = {
            "cognitrade_xueqiu_policy": {
                "enabled": xueqiu_enabled,
                "cookie": settings.xueqiu_cookie if xueqiu_enabled else None,
            }
        }
    result = _collect_uzi_fetcher(
        runtime,
        fetcher_id=fetcher_id,
        ticker=str(symbol or "").strip(),
        industry=industry,
        dependencies=dependencies,
        options=options,
    )
    data = dict(result.get("data") or {}) if isinstance(result.get("data"), dict) else {}
    error = str(result.get("error") or "").strip()
    availability = "available" if data and not error else "data_gap"
    source_text = str(result.get("source") or f"uzi:{fetcher_id}").strip()
    facts: dict[str, Any] = {
        **data,
        "availability": availability,
        "_uzi_fetcher_id": fetcher_id,
        "_uzi_raw_result": result,
    }
    # Compatibility projections are derived only from the exact UZI payload;
    # no values are invented. Downstream UZI parity work reads the raw object.
    if snapshot_type == "market":
        facts["market_overview"] = {
            "security_name": data.get("name"),
            "industry": data.get("industry"),
            "latest_bar": {
                "close": data.get("price"),
                "pct_change": data.get("change_pct"),
            },
            "valuation": {
                "pe_ttm": data.get("pe_ttm"),
                "pb": data.get("pb"),
                "market_cap": data.get("market_cap_raw") or data.get("market_cap"),
            },
        }
    elif snapshot_type == "fundamental":
        facts["financial_abstract"] = data
    elif snapshot_type == "kline":
        facts["history"] = data
    retrieved_at = datetime.now(timezone.utc).isoformat()
    target_symbol = str(result.get("ticker") or symbol or "").strip() or None
    return {
        "snapshot_type": snapshot_type,
        "symbol": target_symbol,
        "sector": industry if snapshot_type in {"macro", "industry", "policy"} else None,
        "query": query,
        "snapshot_date": retrieved_at[:10],
        "retrieved_at": retrieved_at,
        "facts": facts,
        "quality_status": availability,
        "degraded": availability != "available",
        "degrade_reason": error or (None if data else f"{fetcher_id}_data_missing"),
        "missing_fields": [] if availability == "available" else [snapshot_type],
        "fallback_applied": bool(result.get("fallback")),
        "source": {
            "skill": f"uzi_{snapshot_type}_snapshot_skill",
            "vendor": "UZI-Skill",
            "provider_id": f"uzi_3_9_2.{fetcher_id}",
            "source_id": source_text,
            "chain_position": 0,
            "dim": snapshot_type,
            "from_cache": False,
        },
        "source_trace": [
            {
                "source_id": source_text,
                "provider_id": f"uzi_3_9_2.{fetcher_id}",
                "status": availability,
                "fallback": bool(result.get("fallback")),
                **({"error_type": error.split(":", 1)[0]} if error else {}),
            }
        ],
    }


def _collect_uzi_fetcher(
    runtime: Uzi392FetcherRuntime,
    *,
    fetcher_id: str,
    ticker: str,
    industry: str | None,
    dependencies: dict[str, dict[str, Any]] | None,
    options: dict[str, Any] | None,
) -> dict[str, Any]:
    """Invoke a pinned UZI fetcher through Pack-owned provider compatibility.

    UZI 3.9.2 calls ``stock_zh_a_gdhs(symbol=<stock code>)`` in its capital
    flow dimension. Current AkShare defines that function's ``symbol`` as a
    quarter (or ``\"\u6700\u65b0\"``), so a six-digit stock code falls into a full-market
    quarterly scan. That scan currently spans hundreds of pages and makes a
    five-stock holding batch hit the Tool Registry's 300-second deadline.

    AkShare exposes the intended stock-scoped endpoint as
    ``stock_zh_a_gdhs_detail_em``. Route only six-digit stock identifiers to
    it, add the field alias consumed by UZI's holder-trend calculation, and
    restore the provider immediately after the call. The lock keeps the
    temporary compatibility shim isolated from concurrent capital-flow calls.
    No UZI source file or returned report contract is replaced.
    """

    def invoke() -> dict[str, Any]:
        return runtime.collect(
            fetcher_id,
            ticker=ticker,
            industry=industry,
            dependencies=dependencies,
            options=options,
        )

    if fetcher_id != "12_capital_flow":
        return invoke()

    with _UZI_CAPITAL_FLOW_COMPAT_LOCK:
        module = importlib.import_module("fetch_capital_flow")
        akshare = getattr(module, "ak", None)
        broad_loader = getattr(akshare, "stock_zh_a_gdhs", None)
        detail_loader = getattr(akshare, "stock_zh_a_gdhs_detail_em", None)
        if not callable(broad_loader) or not callable(detail_loader):
            return invoke()

        class StockScopedAkshareProxy:
            def __getattr__(self, name: str) -> Any:
                return getattr(akshare, name)

            @staticmethod
            def stock_zh_a_gdhs(symbol: str = "\u6700\u65b0"):
                normalized = str(symbol or "").strip()
                if len(normalized) != 6 or not normalized.isdigit():
                    return broad_loader(symbol=symbol)
                frame = detail_loader(symbol=normalized)
                if frame is None or getattr(frame, "empty", True):
                    return frame
                normalized_frame = frame.copy()
                if (
                    "\u80a1\u4e1c\u6237\u6570-\u672c\u6b21" in normalized_frame.columns
                    and "\u80a1\u4e1c\u6237\u6570" not in normalized_frame.columns
                ):
                    normalized_frame["\u80a1\u4e1c\u6237\u6570"] = normalized_frame[
                        "\u80a1\u4e1c\u6237\u6570-\u672c\u6b21"
                    ]
                return normalized_frame

        module.ak = StockScopedAkshareProxy()
        try:
            return invoke()
        finally:
            module.ak = akshare


def _uzi_raw_result(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    facts = snapshot.get("facts") if isinstance(snapshot.get("facts"), dict) else {}
    result = facts.get("_uzi_raw_result")
    return dict(result) if isinstance(result, dict) else None


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
    safe_snapshot = normalize_json_value(snapshot)
    if not isinstance(safe_snapshot, dict):
        raise TypeError("snapshot cache payload must normalize to an object")
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
            "quality_status": _snapshot_quality(safe_snapshot),
            "snapshot": safe_snapshot,
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
            query=query,
            research_profile=research_profile,
            settings=providers.settings,
            cancellation=cancellation,
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
        query=query,
        research_profile=research_profile,
        settings=providers.settings,
        cancellation=cancellation,
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
    query: str,
    research_profile: str | None,
    settings: Settings,
    cancellation: Any | None = None,
) -> dict[str, Any]:
    # Execute the pinned suggested-fix worklist until every real action either
    # resolves its target or records an explicit exhaustion reason.  There is
    # deliberately no fixed retry/round count.
    normalized_snapshots = [_normalize_snapshot_quality(snapshot) for snapshot in snapshots]
    integrity_before = assess_integrity(normalized_snapshots, symbols)
    selected_profile = str(research_profile or "").strip().lower()
    if not selected_profile:
        raise ValueError("research_profile is required; choose lite, medium or deep")
    if selected_profile in {"lite", "medium", "deep"}:
        has_native_payload = any(
            isinstance((snapshot.get("facts") or {}).get("_uzi_raw_result"), dict)
            for snapshot in normalized_snapshots
            if isinstance(snapshot, dict) and isinstance(snapshot.get("facts"), dict)
        )
        strict_recovery = (
            recover_suggested_fixes_until_exhausted(
                normalized_snapshots,
                query=query,
                symbols=symbols,
                profile=selected_profile,
                settings=settings,
                cancellation=cancellation,
            )
            if has_native_payload
            else {
                "schema_version": "uzi-3.9.2-suggested-fix-loop-v1",
                "profile": selected_profile,
                "stage_order": [],
                "attempted": 0,
                "succeeded": 0,
                "failed": 0,
                "attempts": [],
                "cycles": [],
                "critical_cleared": False,
                "exhausted": True,
                "exhausted_reason": "native_uzi_payload_unavailable",
                "round_limit": None,
            }
        )
        recovery_attempts = list(strict_recovery.get("attempts") or [])
        qualitative_recovery = {
            **strict_recovery,
            "attempts": [
                row for row in recovery_attempts
                if row.get("stage") in {"official_web_ddgs", "mx_api", "qualitative_agent", "qualitative_inference"}
            ],
        }
        playwright_recovery = {
            **strict_recovery,
            "attempts": [row for row in recovery_attempts if row.get("stage") == "playwright"],
        }
    elif selected_profile == "workspace_market_data":
        # Non-UZI workspaces (for example the standalone market-data view)
        # share the collector but must not silently enter an analysis profile.
        qualitative_recovery = {
            "schema_version": "uzi-3.9.2-agent-os-v1",
            "profile": selected_profile,
            "enabled": False,
            "attempted": 0,
            "succeeded": 0,
            "failed": 0,
            "attempts": [],
            "disabled_reason": "non_uzi_collection_profile",
        }
        playwright_recovery = dict(qualitative_recovery)
        strict_recovery = dict(qualitative_recovery)
    else:
        raise ValueError("research_profile must be lite, medium or deep")
    normalized_snapshots = [_normalize_snapshot_quality(snapshot) for snapshot in normalized_snapshots]
    integrity_after = assess_integrity(normalized_snapshots, symbols)
    integrity = {
        **integrity_after,
        "before_recovery": integrity_before,
        "after_recovery": integrity_after,
    }
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
            "uzi_integrity": integrity,
            "uzi_recovery": _recovery_summary(
                normalized_snapshots,
                qualitative=qualitative_recovery,
                playwright=playwright_recovery,
                strict=strict_recovery,
            ),
        },
    }


def _recovery_summary(
    snapshots: list[dict[str, Any]],
    *,
    qualitative: dict[str, Any] | None = None,
    playwright: dict[str, Any] | None = None,
    strict: dict[str, Any] | None = None,
) -> dict[str, Any]:
    strict_payload = dict(strict or {})
    if strict_payload:
        return strict_payload
    attempts = []
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        facts = snapshot.get("facts") if isinstance(snapshot.get("facts"), dict) else {}
        recovery = facts.get("_uzi_recovery") if isinstance(facts.get("_uzi_recovery"), dict) else None
        if recovery is not None:
            attempts.append({
                "stage": "mx_ddgs",
                "symbol": snapshot.get("symbol"),
                "snapshot_type": snapshot.get("snapshot_type"),
                "status": recovery.get("status"),
                "source_id": recovery.get("source"),
                "query": recovery.get("query"),
            })
        playwright_fact = facts.get("_uzi_playwright_recovery") if isinstance(facts.get("_uzi_playwright_recovery"), dict) else None
        if playwright_fact is not None:
            attempts.append({
                "stage": "playwright",
                "symbol": snapshot.get("symbol"),
                "snapshot_type": snapshot.get("snapshot_type"),
                "status": playwright_fact.get("status"),
                "source_ids": playwright_fact.get("source_ids"),
                "trigger_reason": playwright_fact.get("trigger_reason"),
            })
    return {
        "schema_version": "uzi-3.9.2-agent-os-v1",
        "stage_order": ["primary_providers", "integrity_check", "mx_api", "ddgs", "playwright", "integrity_recheck"],
        "order": ["mx_api", "ddgs", "playwright"],
        "attempted": len(attempts),
        "succeeded": sum(row.get("status") == "available" for row in attempts),
        "failed": sum(row.get("status") != "available" for row in attempts),
        "attempts": attempts,
        "qualitative_stage": dict(qualitative or {}),
        "playwright_stage": dict(playwright or {}),
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
