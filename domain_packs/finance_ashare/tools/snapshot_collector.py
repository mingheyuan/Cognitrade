from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import math
import os
from collections.abc import Iterable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import UUID

from domain_packs.finance_ashare.skills.capital_flow_snapshot.skill import (
    CapitalFlowSnapshotSkill,
)
from domain_packs.finance_ashare.skills.events_snapshot.skill import EventsSnapshotSkill
from domain_packs.finance_ashare.skills.fundamental_snapshot.skill import (
    FundamentalSnapshotSkill,
)
from domain_packs.finance_ashare.skills.lhb_snapshot.skill import LHBSnapshotSkill
from domain_packs.finance_ashare.skills.market_snapshot.skill import MarketSnapshotSkill
from domain_packs.finance_ashare.skills.notice_snapshot.skill import NoticeSnapshotSkill
from domain_packs.finance_ashare.skills.policy_snapshot.skill import PolicySnapshotSkill
from domain_packs.finance_ashare.skills.sector_snapshot.skill import SectorSnapshotSkill
from domain_packs.finance_ashare.skills.sentiment_snapshot.skill import (
    SentimentSnapshotSkill,
)
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
from domain_packs.finance_ashare.tools.uzi_data_recovery import (
    assess_integrity,
    collect_qualitative_agent_evidence,
    recover_playwright_snapshots,
    recover_qualitative_snapshots,
    recover_suggested_fixes_until_exhausted,
)
from domain_packs.finance_ashare.vendor.uzi_3_9_2 import (
    UZI_ENVIRONMENT_LOCK,
    Uzi392FetcherRuntime,
)
from research_system.adapters.vendors.akshare_utils import initialize_akshare_js_runtime
from research_system.adapters.vendors.baostock_socket_safety import (
    DEFAULT_BAOSTOCK_SOCKET_TIMEOUT_SECONDS,
    install_baostock_socket_safety,
)
from research_system.adapters.vendors.provider_execution import (
    classify_provider_exception,
    failure_facts,
)
from research_system.adapters.vendors.provider_registry import VendorProviderRegistry
from research_system.adapters.vendors.uzi_request_timeout import (
    uzi_request_timeout_scope,
)
from research_system.runtime.platform.settings import Settings
from research_system.runtime.storage.jsonl import read_json, write_json

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
    "fund_holders": "6_fund_holders",
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
_UZI_NATIVE_SNAPSHOT_CACHE_SCHEMA_VERSION = "uzi-3.9.4-native-raw-v1"
_UNAVAILABLE_QUALITY_STATUSES = {
    "degraded",
    "data_gap",
    "assumption_only",
    "no_data",
    "provider_unavailable",
    "rate_limited",
    "parse_failed",
}
_UZI_MINI_RACER_FETCHERS = frozenset(
    {"7_industry", "10_valuation", "12_capital_flow"}
)
_UZI_MINI_RACER_LOCK = Lock()
_UZI_MAX_WORKERS = 6
_UZI_DEFAULT_SYMBOL_WORKERS = 5
_UZI_MAX_SYMBOL_WORKERS = 5
# Pinned UZI run_real_test.py uses one five-minute wave-2 ceiling and a
# separate three-minute bonus-wave ceiling for every profile.
_UZI_FETCHER_WAVE_TIMEOUT_SECONDS = 300.0
_UZI_BONUS_WAVE_TIMEOUT_SECONDS = 180.0
_UZI_DEFAULT_FETCHER_TIMEOUT_SECONDS = 90.0
_UZI_FETCHER_TIMEOUT_OVERRIDES = {
    "research": 180.0,
    "fundamental": 150.0,
    "valuation": 150.0,
    "events": 120.0,
}
_UZI_BONUS_FETCHER_TIMEOUT_SECONDS = 120.0
_UZI_SIMILAR_STOCKS_TOTAL_TIMEOUT_SECONDS = 30.0
_UZI_SIMILAR_STOCKS_HTTP_TIMEOUT_SECONDS = 5.0
_UZI_BONUS_SNAPSHOT_TYPES = frozenset({"fund_holders", "similar_stocks"})
_UZI_ROOT_ROUTE_INTENTS = frozenset(
    {
        "stock_check",
        "uzi_analyze_stock",
        "stock_compare",
        "uzi_portfolio_batch",
    }
)
_UZI_MULTI_ROOT_INTENTS = frozenset({"stock_compare", "uzi_portfolio_batch"})
_UZI_PROFILE_ENV_KEYS = (
    "UZI_DEPTH",
    "UZI_LITE",
    "UZI_DDG_BUDGET",
    "UZI_FUND_STATS_TOP",
)
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


def _uzi_fetcher_wave_timeout_seconds(profile: str | None) -> float:
    del profile
    return _UZI_FETCHER_WAVE_TIMEOUT_SECONDS


def _uzi_per_fetcher_timeout_seconds(snapshot_type: str) -> float:
    return _UZI_FETCHER_TIMEOUT_OVERRIDES.get(
        snapshot_type,
        _UZI_DEFAULT_FETCHER_TIMEOUT_SECONDS,
    )


@contextmanager
def _uzi_profile_environment(
    profile: str | None,
    provider_registry: VendorProviderRegistry | None,
):
    """Apply one pinned UZI profile for the complete concurrent fetch window.

    UZI 3.9.4 communicates the selected profile to fetchers through process
    environment variables.  A process-global lock is therefore the only safe
    way to retain native intra-run thread parallelism while preventing two
    Agent OS runs with different profiles from observing each other's values.
    """

    settings = (
        provider_registry.settings
        if provider_registry is not None
        else Settings.from_env(Path(__file__).resolve().parents[3])
    )
    # Initialize the pinned import boundary before importing ``lib`` modules.
    Uzi392FetcherRuntime(workspace_root=settings.workspace_root)
    profile_module = importlib.import_module("lib.analysis_profile")
    web_search_module = importlib.import_module("lib.web_search")
    selected = str(profile or "").strip().lower() or None
    with UZI_ENVIRONMENT_LOCK:
        native_profile = profile_module.get_profile(selected)
        previous = {key: os.environ.get(key) for key in _UZI_PROFILE_ENV_KEYS}
        try:
            profile_module.apply_profile_to_env(native_profile)
            # A CLI invocation starts with a fresh module state.  Reset the
            # pinned DDG counter once per Agent OS collection so one report
            # cannot consume the next report's profile budget.
            budget_lock = getattr(web_search_module, "_BUDGET_LOCK")
            budget_state = getattr(web_search_module, "_BUDGET_STATE")
            with budget_lock:
                budget_state["used"] = 0
                budget_state["skipped"] = 0
            yield native_profile
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


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
    progress_callback=None,
    uzi_root_route: bool = False,
    uzi_root_route_mode: str = "single",
    no_resume: bool = False,
) -> list[dict[str, Any]]:
    requested = tuple(
        dict.fromkeys(str(item).strip() for item in snapshot_types if str(item).strip())
    )
    resolved_symbols = _unique_symbols(symbols)
    routed_types = [
        snapshot_type
        for snapshot_type in requested
        if _uses_uzi_fetcher(snapshot_type)
        and not (snapshot_type == "policy" and sector)
    ]
    has_uzi = any(_uses_uzi_fetcher(snapshot_type) for snapshot_type in requested)
    if has_uzi:
        with _uzi_profile_environment(research_profile, provider_registry):
            if uzi_root_route and routed_types:
                return _collect_snapshots_with_uzi_root_router(
                    query=query,
                    symbols=resolved_symbols,
                    snapshot_types=requested,
                    sector=sector,
                    cancellation=cancellation,
                    cache_root=cache_root,
                    research_profile=research_profile,
                    max_workers=max_workers,
                    provider_registry=provider_registry,
                    progress_callback=progress_callback,
                    route_mode=uzi_root_route_mode,
                    no_resume=no_resume,
                )
            # Preserve the established on-demand path: each explicit symbol
            # receives an independent native wave budget.
            if not sector and len(resolved_symbols) > 1:
                collected: list[dict[str, Any]] = []
                per_symbol_total = sum(
                    snapshot_type in _SYMBOL_SKILLS for snapshot_type in requested
                )
                aggregate_total = per_symbol_total * len(resolved_symbols)
                for symbol_index, symbol in enumerate(resolved_symbols):
                    if _cancelled(cancellation):
                        break

                    def emit_symbol_progress(
                        progress: dict[str, Any],
                        *,
                        offset: int = symbol_index * per_symbol_total,
                    ) -> None:
                        if callable(progress_callback):
                            progress_callback(
                                {
                                    **progress,
                                    "completed": offset
                                    + int(progress.get("completed") or 0),
                                    "total": aggregate_total,
                                }
                            )

                    collected.extend(
                        _collect_snapshots(
                            query=query,
                            symbols=[symbol],
                            snapshot_types=requested,
                            sector=None,
                            cancellation=cancellation,
                            cache_root=cache_root,
                            research_profile=research_profile,
                            max_workers=max_workers,
                            provider_registry=provider_registry,
                            progress_callback=emit_symbol_progress,
                        )
                    )
                return collected
            return _collect_snapshots(
                query=query,
                symbols=resolved_symbols,
                snapshot_types=requested,
                sector=sector,
                cancellation=cancellation,
                cache_root=cache_root,
                research_profile=research_profile,
                max_workers=max_workers,
                provider_registry=provider_registry,
                progress_callback=progress_callback,
            )
    return _collect_snapshots(
        query=query,
        symbols=resolved_symbols,
        snapshot_types=requested,
        sector=sector,
        cancellation=cancellation,
        cache_root=cache_root,
        research_profile=research_profile,
        max_workers=max_workers,
        provider_registry=provider_registry,
        progress_callback=progress_callback,
    )


def _collect_snapshots_with_uzi_root_router(
    *,
    query: str,
    symbols: Iterable[str],
    snapshot_types: Iterable[str],
    sector: str | None,
    cancellation,
    cache_root: Path | None,
    research_profile: str | None,
    max_workers: int | None,
    provider_registry: VendorProviderRegistry | None,
    progress_callback,
    route_mode: str,
    no_resume: bool,
) -> list[dict[str, Any]]:
    """Collect each symbol through the pinned UZI root-default router once."""

    requested = list(
        dict.fromkeys(str(item).strip() for item in snapshot_types if str(item).strip())
    )
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
            sector_snapshot = _instantiate_skill(SectorSnapshotSkill, providers).run(
                query=sector
            )
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
        policy_snapshot = _load_snapshot_cache(
            cache_root=cache_root,
            snapshot_type="policy",
            target=sector,
            profile=research_profile,
        )
        if policy_snapshot is None:
            # Sector policy research is an explicit Agent OS host capability,
            # not UZI's per-stock 13_policy dimension.  Keep the direct Pack
            # fallback identical to the governed Tool Registry path.
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
        snapshots.append(policy_snapshot)

    symbol_types = [
        item
        for item in requested
        if item in _SYMBOL_SKILLS and not (item == "policy" and sector)
    ]
    uzi_types = [item for item in symbol_types if _uses_uzi_fetcher(item)]
    total = len(resolved_symbols) * len(symbol_types)
    completed = 0
    requested_symbol_workers = (
        _UZI_DEFAULT_SYMBOL_WORKERS
        if max_workers is None
        else max(int(max_workers), 1)
    )
    symbol_workers = min(
        requested_symbol_workers,
        _UZI_MAX_SYMBOL_WORKERS,
        max(len(resolved_symbols), 1),
    )

    settings = providers.settings
    runtime = Uzi392FetcherRuntime(workspace_root=settings.workspace_root)
    install_baostock_socket_safety(
        timeout_seconds=getattr(
            settings,
            "baostock_socket_timeout_seconds",
            DEFAULT_BAOSTOCK_SOCKET_TIMEOUT_SECONDS,
        )
    )
    allowlist = {
        str(item).strip().lower() for item in settings.sensitive_source_allowlist
    }
    xueqiu_enabled = (
        settings.sensitive_sources_enabled
        and "xueqiu" in allowlist
        and bool(settings.xueqiu_cookie)
    )
    route_options = {
        "cognitrade_root_route_mode": route_mode,
        # The v3 pipeline runs fetchers in spawned processes.  Pass the bonus
        # budget to the host runtime so its existing hard process scheduler,
        # rather than a parent-only requests patch, enforces the deadline.
        "cognitrade_pipeline_fetcher_timeout_overrides": {
            "similar_stocks": _UZI_SIMILAR_STOCKS_TOTAL_TIMEOUT_SECONDS,
        },
        "cognitrade_xueqiu_policy": {
            "enabled": xueqiu_enabled,
            "cookie": settings.xueqiu_cookie if xueqiu_enabled else None,
        }
    }

    def emit(*, phase: str, snapshot_type: str) -> None:
        if callable(progress_callback):
            progress_callback(
                {
                    "phase": phase,
                    "snapshot_type": snapshot_type,
                    "completed": completed,
                    "total": total,
                }
            )

    def collect_symbol(symbol: str, scoped_route_options: dict[str, Any]):
        if _cancelled(cancellation):
            return None, None
        symbol_result: dict[str, Any] | None = None
        route_failure: Exception | None = None
        if uzi_types:
            try:
                with uzi_request_timeout_scope():
                    symbol_result = runtime.collect_symbol(
                        ticker=symbol,
                        max_workers=_UZI_MAX_WORKERS,
                        # UZI's dedicated versus/portfolio runners hard-code
                        # resume=True.  Request-local no_resume belongs only to
                        # the single-stock root CLI route.
                        resume=not no_resume if route_mode == "single" else True,
                        options=scoped_route_options,
                    )
                normalized = normalize_json_value(symbol_result)
                if not isinstance(normalized, dict):
                    raise TypeError("UZI symbol collection must normalize to an object")
                symbol_result = normalized
            except Exception as exc:
                route_failure = exc
        return symbol_result, route_failure

    def project_symbol(
        symbol: str,
        symbol_result: dict[str, Any] | None,
        route_failure: Exception | None,
    ) -> list[dict[str, Any]]:
        symbol_snapshots: list[dict[str, Any]] = []
        for snapshot_type in symbol_types:
            if snapshot_type in uzi_types:
                if route_failure is not None or symbol_result is None:
                    snapshot = _fetcher_failure_snapshot(
                        query=query,
                        symbol=symbol,
                        snapshot_type=snapshot_type,
                        exc=route_failure or RuntimeError("UZI symbol collection failed"),
                        symbol_route_failed=route_mode == "multi",
                    )
                else:
                    snapshot = _project_uzi_symbol_snapshot(
                        query=query,
                        requested_symbol=symbol,
                        snapshot_type=snapshot_type,
                        symbol_result=symbol_result,
                    )
            else:
                cached = _load_snapshot_cache(
                    cache_root=cache_root,
                    snapshot_type=snapshot_type,
                    target=symbol,
                    profile=research_profile,
                )
                if cached is not None:
                    snapshot = cached
                else:
                    snapshot = _run_snapshot_skill(
                        query,
                        symbol,
                        snapshot_type,
                        providers,
                        None,
                        research_profile,
                    )
                    _save_snapshot_cache(
                        cache_root=cache_root,
                        snapshot_type=snapshot_type,
                        target=symbol,
                        profile=research_profile,
                        snapshot=snapshot,
                    )
            symbol_snapshots.append(snapshot)
        return symbol_snapshots

    results_by_index: dict[int, list[dict[str, Any]]] = {}
    if resolved_symbols and not _cancelled(cancellation):
        # UZI's six workers remain the *inside-one-stock* fetcher budget.  The
        # request max_workers controls only how many complete symbol pipelines
        # may be in flight, capped at five to bound the worst case at thirty
        # provider child processes.  The runtime installs process-global UZI
        # wrappers once before these threads start, avoiding lock inversion and
        # preserving one profile/source policy for the whole request.
        with runtime.parallel_symbol_collection_scope(
            options=route_options,
            cancellation=cancellation,
        ) as scoped_route_options, ThreadPoolExecutor(
            max_workers=symbol_workers,
            thread_name_prefix="uzi-symbol",
        ) as pool:
            next_index = 0
            in_flight = {}

            def submit_available() -> None:
                nonlocal next_index
                while (
                    not _cancelled(cancellation)
                    and next_index < len(resolved_symbols)
                    and len(in_flight) < symbol_workers
                ):
                    symbol_index = next_index
                    symbol = resolved_symbols[symbol_index]
                    emit(phase="collecting", snapshot_type=uzi_types[0])
                    future = pool.submit(
                        collect_symbol,
                        symbol,
                        scoped_route_options,
                    )
                    in_flight[future] = (symbol_index, symbol)
                    next_index += 1

            submit_available()
            while in_flight:
                done, _pending = wait(
                    in_flight,
                    timeout=0.1,
                    return_when=FIRST_COMPLETED,
                )
                if not done:
                    if _cancelled(cancellation):
                        for future in in_flight:
                            future.cancel()
                    continue

                for future in sorted(done, key=lambda item: in_flight[item][0]):
                    symbol_index, symbol = in_flight.pop(future)
                    if future.cancelled():
                        continue
                    symbol_result, route_failure = future.result()
                    if symbol_result is None and route_failure is None:
                        continue
                    rows = project_symbol(symbol, symbol_result, route_failure)
                    results_by_index[symbol_index] = rows
                    for row in rows:
                        completed += 1
                        emit(
                            phase="completed",
                            snapshot_type=str(row.get("snapshot_type") or ""),
                        )

                submit_available()

    for symbol_index in range(len(resolved_symbols)):
        snapshots.extend(results_by_index.get(symbol_index, []))

    if "sector" in requested and not sector:
        snapshots.append(_missing_scope_snapshot(query=query, snapshot_type="sector"))
    return snapshots


def _collect_snapshots(
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
    progress_callback=None,
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
        policy_snapshot = _load_snapshot_cache(
            cache_root=cache_root,
            snapshot_type="policy",
            target=sector,
            profile=research_profile,
        )
        if policy_snapshot is None:
            try:
                with uzi_request_timeout_scope():
                    policy_snapshot = _run_uzi_fetcher_snapshot(
                        query=query,
                        symbol=None,
                        snapshot_type="policy",
                        provider_registry=providers,
                        industry=sector,
                    )
            except Exception as exc:
                policy_snapshot = _fetcher_failure_snapshot(
                    query=query,
                    symbol=None,
                    snapshot_type="policy",
                    exc=exc,
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
        snapshots.extend(
            _missing_scope_snapshot(query=query, snapshot_type=snapshot_type)
            for snapshot_type in requested
            if snapshot_type == "sector" and not sector
        )
        return snapshots
    results: dict[int, dict[str, Any]] = {}
    pending_tasks: list[tuple[int, str, str]] = []
    total_tasks = len(tasks)

    def emit_progress(*, phase: str, snapshot_type: str, completed: int | None = None) -> None:
        if not callable(progress_callback):
            return
        progress_callback({
            "phase": phase,
            "snapshot_type": snapshot_type,
            "completed": len(results) if completed is None else completed,
            "total": total_tasks,
        })

    for index, symbol, snapshot_type in tasks:
        # Match UZI raw_data resume: basic and both top-level bonus collectors
        # always run, while valid numbered dimensions are reused.
        cached = (
            None
            if _uses_uzi_fetcher(snapshot_type)
            and not _uses_uzi_resume_cache(snapshot_type)
            else _load_snapshot_cache(
                cache_root=cache_root,
                snapshot_type=snapshot_type,
                target=symbol,
                profile=research_profile,
                allow_expired=_uses_uzi_resume_cache(snapshot_type),
            )
        )
        if cached is None:
            pending_tasks.append((index, symbol, snapshot_type))
        else:
            results[index] = cached
            emit_progress(phase="cache_hit", snapshot_type=snapshot_type)
    if pending_tasks:
        wave_timeout_seconds = _uzi_fetcher_wave_timeout_seconds(research_profile)
        initialize_akshare_js_runtime()

        def store_result(
            index: int,
            symbol: str,
            snapshot_type: str,
            result: dict[str, Any],
        ) -> None:
            if _uses_uzi_fetcher(snapshot_type) and result.get("symbol") != symbol:
                # Provider invocation follows UZI's canonical ticker returned
                # by basic, while ArtifactBoard entity ownership remains the
                # caller's requested symbol across every dimension.
                result = {**result, "symbol": symbol}
            results[index] = result
            if not _uses_uzi_fetcher(snapshot_type) or _uses_uzi_resume_cache(
                snapshot_type
            ):
                _save_snapshot_cache(
                    cache_root=cache_root,
                    snapshot_type=snapshot_type,
                    target=symbol,
                    profile=research_profile,
                    snapshot=result,
                )
            emit_progress(phase="completed", snapshot_type=snapshot_type)

        # UZI runs dimension 0 first because the dependent macro/industry/
        # futures/policy dimensions consume the resolved real industry.
        basic_tasks = [task for task in pending_tasks if task[2] == "market"]
        for index, symbol, snapshot_type in basic_tasks:
            if _cancelled(cancellation):
                break
            emit_progress(phase="collecting", snapshot_type=snapshot_type)
            result = _run_snapshot_skill(
                query,
                symbol,
                snapshot_type,
                providers,
                None,
                research_profile,
            )
            store_result(index, symbol, snapshot_type, result)
        pending_tasks = [task for task in pending_tasks if task[2] != "market"]
        basic_by_symbol = {
            symbol: raw
            for index, symbol, snapshot_type in tasks
            if snapshot_type == "market"
            and index in results
            and (raw := _uzi_raw_result(results[index])) is not None
        }
        invocation_symbol_by_symbol = {
            symbol: str(raw.get("ticker") or symbol).strip() or symbol
            for symbol, raw in basic_by_symbol.items()
        }
        cached_material_by_symbol = {
            symbol: results[index]
            for index, symbol, snapshot_type in tasks
            if snapshot_type == "materials" and index in results
        }
        bonus_tasks = [
            task for task in pending_tasks if task[2] in _UZI_BONUS_SNAPSHOT_TYPES
        ]
        wave2_tasks = [
            task for task in pending_tasks if task[2] not in _UZI_BONUS_SNAPSHOT_TYPES
        ]

        # UZI's numbered fetchers all belong to one six-worker wave.  Futures
        # remains in that wave but consumes the materials future for the same
        # symbol, so its native call receives the intended dimension-8 input.
        requested_workers = _UZI_MAX_WORKERS if max_workers is None else max(int(max_workers), 1)
        if wave2_tasks:
            executor = ThreadPoolExecutor(
                max_workers=min(requested_workers, len(wave2_tasks)),
                thread_name_prefix="finance-snapshot",
            )
            futures: dict[Any, tuple[int, str, str]] = {}
            material_futures: dict[str, Any] = {}
            futures_tasks = [task for task in wave2_tasks if task[2] == "futures"]
            other_wave2_tasks = [task for task in wave2_tasks if task[2] != "futures"]

            for index, symbol, snapshot_type in other_wave2_tasks:
                dependencies: dict[str, dict[str, Any]] = {}
                if symbol in basic_by_symbol:
                    dependencies["0_basic"] = basic_by_symbol[symbol]
                invocation_symbol = (
                    invocation_symbol_by_symbol.get(symbol, symbol)
                    if _uses_uzi_fetcher(snapshot_type)
                    else symbol
                )
                future = executor.submit(
                    _run_snapshot_skill,
                    query,
                    invocation_symbol,
                    snapshot_type,
                    providers,
                    dependencies or None,
                    research_profile,
                )
                futures[future] = (index, symbol, snapshot_type)
                if snapshot_type == "materials":
                    material_futures[symbol] = future

            def run_futures_task(
                symbol: str,
                invocation_symbol: str,
                material_future: Any | None,
            ) -> dict[str, Any]:
                dependencies: dict[str, dict[str, Any]] = {}
                if symbol in basic_by_symbol:
                    dependencies["0_basic"] = basic_by_symbol[symbol]
                material_snapshot = None
                if material_future is not None:
                    material_snapshot = material_future.result()
                elif symbol in cached_material_by_symbol:
                    material_snapshot = cached_material_by_symbol[symbol]
                if isinstance(material_snapshot, dict):
                    if _uses_uzi_fetcher("futures"):
                        material_raw = _uzi_raw_result(material_snapshot)
                        if material_raw is not None:
                            dependencies["8_materials"] = material_raw
                    else:
                        dependencies["materials"] = material_snapshot
                return _run_snapshot_skill(
                    query,
                    invocation_symbol,
                    "futures",
                    providers,
                    dependencies or None,
                    research_profile,
                )

            for index, symbol, snapshot_type in futures_tasks:
                future = executor.submit(
                    run_futures_task,
                    symbol,
                    invocation_symbol_by_symbol.get(symbol, symbol),
                    material_futures.get(symbol),
                )
                futures[future] = (index, symbol, snapshot_type)

            emit_progress(
                phase="collecting",
                snapshot_type=wave2_tasks[0][2],
            )
            try:
                try:
                    for future in as_completed(
                        futures,
                        timeout=wave_timeout_seconds,
                    ):
                        index, symbol, snapshot_type = futures[future]
                        if _cancelled(cancellation):
                            for pending in futures:
                                pending.cancel()
                            break
                        try:
                            result = future.result(
                                timeout=_uzi_per_fetcher_timeout_seconds(snapshot_type)
                            )
                        except FutureTimeoutError:
                            result = _fetcher_timeout_snapshot(
                                query=query,
                                symbol=symbol,
                                snapshot_type=snapshot_type,
                                timeout_seconds=_uzi_per_fetcher_timeout_seconds(
                                    snapshot_type
                                ),
                            )
                        except Exception as exc:
                            result = _fetcher_failure_snapshot(
                                query=query,
                                symbol=symbol,
                                snapshot_type=snapshot_type,
                                exc=exc,
                            )
                        store_result(index, symbol, snapshot_type, result)
                except FutureTimeoutError:
                    for future, (index, symbol, snapshot_type) in futures.items():
                        if index in results:
                            continue
                        if future.done() and not future.cancelled():
                            try:
                                result = future.result(
                                    timeout=_uzi_per_fetcher_timeout_seconds(snapshot_type)
                                )
                            except FutureTimeoutError:
                                result = _fetcher_timeout_snapshot(
                                    query=query,
                                    symbol=symbol,
                                    snapshot_type=snapshot_type,
                                    timeout_seconds=_uzi_per_fetcher_timeout_seconds(
                                        snapshot_type
                                    ),
                                )
                            except Exception as exc:
                                result = _fetcher_failure_snapshot(
                                    query=query,
                                    symbol=symbol,
                                    snapshot_type=snapshot_type,
                                    exc=exc,
                                )
                        else:
                            future.cancel()
                            result = _fetcher_timeout_snapshot(
                                query=query,
                                symbol=symbol,
                                snapshot_type=snapshot_type,
                                timeout_seconds=wave_timeout_seconds,
                            )
                        store_result(index, symbol, snapshot_type, result)
            finally:
                # Pinned UZI uses a ThreadPoolExecutor context manager, which
                # waits for already-running calls before the profile env can be
                # restored.  Keep the same lifetime guarantee here.
                executor.shutdown(wait=True, cancel_futures=True)

        # Wave 3 is deliberately separate from the 19 numbered fetchers.  The
        # two bonus collectors run for every profile with two workers and the
        # pinned 180-second overall timeout.
        if bonus_tasks and not _cancelled(cancellation):
            executor = ThreadPoolExecutor(
                max_workers=min(2, len(bonus_tasks)),
                thread_name_prefix="finance-snapshot-bonus",
            )
            futures = {}
            for index, symbol, snapshot_type in bonus_tasks:
                dependencies = (
                    {"0_basic": basic_by_symbol[symbol]}
                    if symbol in basic_by_symbol
                    else None
                )
                future = executor.submit(
                    _run_snapshot_skill,
                    query,
                    invocation_symbol_by_symbol.get(symbol, symbol),
                    snapshot_type,
                    providers,
                    dependencies,
                    research_profile,
                )
                futures[future] = (index, symbol, snapshot_type)
            emit_progress(phase="collecting", snapshot_type=bonus_tasks[0][2])
            try:
                try:
                    for future in as_completed(
                        futures,
                        timeout=_UZI_BONUS_WAVE_TIMEOUT_SECONDS,
                    ):
                        index, symbol, snapshot_type = futures[future]
                        try:
                            result = future.result(
                                timeout=_UZI_BONUS_FETCHER_TIMEOUT_SECONDS
                            )
                        except FutureTimeoutError:
                            result = _fetcher_timeout_snapshot(
                                query=query,
                                symbol=symbol,
                                snapshot_type=snapshot_type,
                                timeout_seconds=_UZI_BONUS_FETCHER_TIMEOUT_SECONDS,
                            )
                        except Exception as exc:
                            result = _fetcher_failure_snapshot(
                                query=query,
                                symbol=symbol,
                                snapshot_type=snapshot_type,
                                exc=exc,
                            )
                        store_result(index, symbol, snapshot_type, result)
                except FutureTimeoutError:
                    for future, (index, symbol, snapshot_type) in futures.items():
                        if index in results:
                            continue
                        if future.done() and not future.cancelled():
                            try:
                                result = future.result(
                                    timeout=_UZI_BONUS_FETCHER_TIMEOUT_SECONDS
                                )
                            except FutureTimeoutError:
                                result = _fetcher_timeout_snapshot(
                                    query=query,
                                    symbol=symbol,
                                    snapshot_type=snapshot_type,
                                    timeout_seconds=_UZI_BONUS_FETCHER_TIMEOUT_SECONDS,
                                )
                            except Exception as exc:
                                result = _fetcher_failure_snapshot(
                                    query=query,
                                    symbol=symbol,
                                    snapshot_type=snapshot_type,
                                    exc=exc,
                                )
                        else:
                            future.cancel()
                            result = _fetcher_timeout_snapshot(
                                query=query,
                                symbol=symbol,
                                snapshot_type=snapshot_type,
                                timeout_seconds=_UZI_BONUS_WAVE_TIMEOUT_SECONDS,
                            )
                        store_result(index, symbol, snapshot_type, result)
            finally:
                executor.shutdown(wait=True, cancel_futures=True)
    snapshots.extend(results[index] for index, _symbol, _snapshot_type in tasks if index in results)
    snapshots.extend(
        _missing_scope_snapshot(query=query, snapshot_type=snapshot_type)
        for snapshot_type in requested
        if snapshot_type == "sector" and not sector
    )
    return snapshots


def _run_snapshot_skill(
    query: str,
    symbol: str,
    snapshot_type: str,
    provider_registry: VendorProviderRegistry | None = None,
    dependencies: dict[str, dict[str, Any]] | None = None,
    research_profile: str | None = None,
) -> dict[str, Any]:
    try:
        if _uses_uzi_fetcher(snapshot_type):
            raw_basic = (dependencies or {}).get("0_basic") or {}
            industry = str((raw_basic.get("data") or {}).get("industry") or "").strip() or None
            request_scope_kwargs = (
                {
                    "timeout_seconds": _UZI_SIMILAR_STOCKS_HTTP_TIMEOUT_SECONDS,
                    "total_timeout_seconds": _UZI_SIMILAR_STOCKS_TOTAL_TIMEOUT_SECONDS,
                }
                if snapshot_type == "similar_stocks"
                else {}
            )
            with uzi_request_timeout_scope(**request_scope_kwargs):
                return dict(
                    normalize_json_value(
                        _run_uzi_fetcher_snapshot(
                            query=query,
                            symbol=symbol,
                            snapshot_type=snapshot_type,
                            provider_registry=provider_registry,
                            industry=industry,
                            dependencies=dependencies,
                            research_profile=research_profile,
                        )
                    )
                )
        skill_cls = _SYMBOL_SKILLS[snapshot_type]
        skill = _instantiate_skill(skill_cls, provider_registry)
        kwargs: dict[str, Any] = {"query": query, "symbol": symbol}
        if "dependencies" in inspect.signature(skill.run).parameters:
            kwargs["dependencies"] = dependencies or {}
        return dict(normalize_json_value(skill.run(**kwargs)))
    except Exception as exc:
        return _fetcher_failure_snapshot(
            query=query,
            symbol=symbol,
            snapshot_type=snapshot_type,
            exc=exc,
        )


def _uses_uzi_fetcher(snapshot_type: str) -> bool:
    return (
        snapshot_type in _UZI_FETCHER_BY_SNAPSHOT_TYPE
        and _SYMBOL_SKILLS.get(snapshot_type) is _CANONICAL_SYMBOL_SKILLS.get(snapshot_type)
    )


def _uses_uzi_resume_cache(snapshot_type: str) -> bool:
    return _uses_uzi_fetcher(snapshot_type) and snapshot_type not in {
        "market",
        *_UZI_BONUS_SNAPSHOT_TYPES,
    }


def _project_uzi_symbol_snapshot(
    *,
    query: str,
    requested_symbol: str,
    snapshot_type: str,
    symbol_result: dict[str, Any],
) -> dict[str, Any]:
    """Project one requested Artifact without changing UZI's raw envelope."""

    fetcher_id = _UZI_FETCHER_BY_SNAPSHOT_TYPE[snapshot_type]
    dimensions = (
        symbol_result.get("dimensions")
        if isinstance(symbol_result.get("dimensions"), dict)
        else {}
    )
    raw_result = dimensions.get(fetcher_id)
    if not isinstance(raw_result, dict):
        raw_result = None
    execution = (
        dict(symbol_result.get("_uzi_execution") or {})
        if isinstance(symbol_result.get("_uzi_execution"), dict)
        else {"lane": "unknown", "fallback_reason": None}
    )
    lane = str(execution.get("lane") or "unknown")
    execution = {
        "lane": lane,
        "fallback_reason": execution.get("fallback_reason"),
    }

    top_level_fields = {
        key: {
            "present": key in symbol_result,
            "value": symbol_result.get(key),
        }
        for key in ("fund_managers", "similar_stocks")
    }
    root_fields = {
        key: {
            "present": key in symbol_result,
            "value": symbol_result.get(key),
        }
        for key in ("ticker", "market", "code", "full", "fetched_at")
    }
    data = (
        dict(raw_result.get("data") or {})
        if raw_result is not None and isinstance(raw_result.get("data"), dict)
        else {}
    )
    bonus_field = {
        "fund_holders": "fund_managers",
        "similar_stocks": "similar_stocks",
    }.get(snapshot_type)
    bonus_present = False
    bonus_value: Any = None
    if bonus_field is not None:
        spill = top_level_fields[bonus_field]
        bonus_present = bool(spill["present"])
        bonus_value = spill["value"]
        if bonus_present:
            data[bonus_field] = bonus_value

    pipeline_meta = (
        raw_result.get("_pipeline")
        if raw_result is not None and isinstance(raw_result.get("_pipeline"), dict)
        else {}
    )
    error = str(
        (raw_result or {}).get("error")
        or pipeline_meta.get("error")
        or ""
    ).strip()
    if raw_result is not None:
        availability = "available" if data and not error else "data_gap"
        source_text = str(
            raw_result.get("source") or f"uzi:{fetcher_id}"
        ).strip()
        dimension_fallback = bool(raw_result.get("fallback"))
    elif bonus_field is not None:
        # Legacy has no bonus dimension envelopes; pipeline also spills
        # similar_stocks only at symbol-root.  Presence is therefore the only
        # faithful signal.  Keep [] and None distinct in `_uzi_top_level_fields`.
        availability = (
            "available" if bonus_present and bonus_value is not None else "data_gap"
        )
        source_text = f"uzi:{lane}:top_level:{bonus_field}"
        dimension_fallback = False
    else:
        availability = "data_gap"
        source_text = f"uzi:{lane}:missing:{fetcher_id}"
        dimension_fallback = True
        error = f"{fetcher_id}_missing_from_{lane}_collection"

    quality_floor, pipeline_gaps, pipeline_reasons = _uzi_pipeline_quality_floor(
        snapshot_type=snapshot_type,
        symbol=str(root_fields.get("full") or root_fields.get("ticker") or requested_symbol),
        data=data,
        pipeline_meta=pipeline_meta,
        error=error,
    )
    availability = _apply_quality_floor(availability, quality_floor)

    fallback_reason = execution.get("fallback_reason")
    fallback_applied = dimension_fallback or bool(fallback_reason)
    native_source_trace = [
        {
            "source_id": source_text,
            "provider_id": f"uzi_3_9_2.{fetcher_id}",
            "status": availability,
            "fallback": fallback_applied,
            **({"error_type": error.split(":", 1)[0]} if error else {}),
        }
    ]
    facts: dict[str, Any] = {
        **data,
        "availability": availability,
        "_uzi_fetcher_id": fetcher_id,
        "_uzi_execution": execution,
        "_uzi_root_fields": root_fields,
        "_uzi_source_trace": native_source_trace,
        "_uzi_top_level_fields": top_level_fields,
    }
    if raw_result is not None:
        facts["_uzi_raw_result"] = raw_result

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

    basic = dimensions.get("0_basic")
    basic_data = (
        basic.get("data")
        if isinstance(basic, dict) and isinstance(basic.get("data"), dict)
        else {}
    )
    industry = str(basic_data.get("industry") or "").strip() or None
    retrieved_at = datetime.now(timezone.utc).isoformat()
    return {
        "snapshot_type": snapshot_type,
        "symbol": requested_symbol,
        "sector": industry if snapshot_type in {"macro", "industry", "policy"} else None,
        "query": query,
        "snapshot_date": retrieved_at[:10],
        "retrieved_at": retrieved_at,
        "facts": facts,
        "quality_status": availability,
        "degraded": availability != "available",
        "degrade_reason": error or (pipeline_reasons[0] if pipeline_reasons else (
            None if availability == "available" else f"{fetcher_id}_data_missing"
        )),
        "degrade_reasons": pipeline_reasons,
        "missing_fields": pipeline_gaps or (
            [] if availability == "available" else [snapshot_type]
        ),
        "fallback_applied": fallback_applied,
        "source": {
            "skill": f"uzi_{snapshot_type}_snapshot_skill",
            "vendor": "UZI-Skill",
            "provider_id": f"uzi_3_9_2.{fetcher_id}",
            "source_id": source_text,
            "chain_position": 0,
            "dim": snapshot_type,
            "from_cache": bool(pipeline_meta.get("cached")),
        },
        "source_trace": native_source_trace,
    }


def _run_uzi_fetcher_snapshot(
    *,
    query: str,
    symbol: str | None,
    snapshot_type: str,
    provider_registry: VendorProviderRegistry | None,
    industry: str | None = None,
    dependencies: dict[str, dict[str, Any]] | None = None,
    research_profile: str | None = None,
) -> dict[str, Any]:
    fetcher_id = _UZI_FETCHER_BY_SNAPSHOT_TYPE[snapshot_type]
    settings = (
        provider_registry.settings
        if provider_registry is not None
        else Settings.from_env(Path(__file__).resolve().parents[3])
    )
    runtime = Uzi392FetcherRuntime(workspace_root=settings.workspace_root)
    install_baostock_socket_safety(
        timeout_seconds=getattr(
            settings,
            "baostock_socket_timeout_seconds",
            DEFAULT_BAOSTOCK_SOCKET_TIMEOUT_SECONDS,
        )
    )
    options: dict[str, Any] | None = None
    if fetcher_id == "6_fund_holders":
        profile_stats_top_n = {"lite": 5, "medium": 20, "deep": 100}
        options = {
            "fund_stats_top_n": profile_stats_top_n.get(
                str(research_profile or "").strip().lower(),
                20,
            )
        }
    elif fetcher_id == "19_contests":
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
    collect_kwargs = {
        "ticker": str(symbol or "").strip(),
        "industry": industry,
        "dependencies": dependencies,
        "options": options,
    }
    if fetcher_id in _UZI_MINI_RACER_FETCHERS:
        # Preserve the established on-demand path for API/quick-scan callers.
        # Full reports bypass this host wave and use collect_symbol(), whose
        # pinned v3/legacy orchestrators own their mini-racer behavior.
        with _UZI_MINI_RACER_LOCK:
            result = runtime.collect(fetcher_id, **collect_kwargs)
    else:
        result = runtime.collect(fetcher_id, **collect_kwargs)
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
    # UZI fetchers do not all return the same ticker spelling: some return
    # ``002371.SZ`` while others echo ``002371``.  The ArtifactBoard entity
    # key must remain the caller's requested symbol so the twenty dimensions
    # are not split into separate stock analyses.  The untouched upstream
    # ticker remains available inside ``_uzi_raw_result``.
    target_symbol = str(symbol or result.get("ticker") or "").strip() or None
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


def _fetcher_timeout_snapshot(
    *,
    query: str,
    symbol: str,
    snapshot_type: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    fetcher_id = _UZI_FETCHER_BY_SNAPSHOT_TYPE.get(snapshot_type)
    reason = f"fetcher wave timeout > {timeout_seconds:g}s"
    raw_result = {
        "ticker": symbol,
        "data": {},
        "_timeout": True,
        "fallback": True,
        "error": reason,
        "source": "timeout",
    }
    facts: dict[str, Any] = {
        "availability": "data_gap",
        "_uzi_raw_result": raw_result,
    }
    if fetcher_id:
        facts["_uzi_fetcher_id"] = fetcher_id
    retrieved_at = datetime.now(timezone.utc).isoformat()
    provider_id = f"uzi_3_9_2.{fetcher_id}" if fetcher_id else snapshot_type
    return {
        "snapshot_type": snapshot_type,
        "symbol": symbol,
        "query": query,
        "snapshot_date": retrieved_at[:10],
        "retrieved_at": retrieved_at,
        "facts": facts,
        "quality_status": "data_gap",
        "degraded": True,
        "degrade_reason": reason,
        "missing_fields": [snapshot_type],
        "fallback_applied": True,
        "source": {
            "skill": f"uzi_{snapshot_type}_snapshot_skill",
            "vendor": "UZI-Skill",
            "provider_id": provider_id,
            "source_id": "timeout",
            "chain_position": 0,
            "dim": snapshot_type,
            "from_cache": False,
        },
        "source_trace": [
            {
                "source_id": "timeout",
                "provider_id": provider_id,
                "status": "data_gap",
                "fallback": True,
                "error_type": "fetcher_wave_timeout",
            }
        ],
    }


def _fetcher_failure_snapshot(
    *,
    query: str,
    symbol: str,
    snapshot_type: str,
    exc: Exception,
    symbol_route_failed: bool = False,
) -> dict[str, Any]:
    """Represent one native fetcher crash without aborting sibling fetchers."""

    fetcher_id = _UZI_FETCHER_BY_SNAPSHOT_TYPE.get(snapshot_type)
    status = classify_provider_exception(exc)
    # Provider boundaries expose the exception type and stable taxonomy only;
    # raw transport text may contain credentials or private upstream details.
    reason = f"{exc.__class__.__name__}: {status}"
    raw_result = {
        "ticker": symbol,
        "data": {},
        "fallback": True,
        "error": reason,
        "source": "lib.pipeline.collect" if symbol_route_failed else fetcher_id or snapshot_type,
    }
    facts = {
        **failure_facts(status),
        "availability": "data_gap",
        "_uzi_raw_result": raw_result,
    }
    if fetcher_id and not symbol_route_failed:
        facts["_uzi_fetcher_id"] = fetcher_id
    if symbol_route_failed:
        # Pinned versus/portfolio skip a symbol whose pipeline orchestration
        # failed (unless a complete report cache is available). Prevent host
        # recovery from rebuilding it dimension by dimension.
        facts["_uzi_symbol_route_failed"] = True
    retrieved_at = datetime.now(timezone.utc).isoformat()
    provider_id = (
        "uzi_3_9_2.root_collection"
        if symbol_route_failed
        else f"uzi_3_9_2.{fetcher_id}" if fetcher_id else snapshot_type
    )
    source_id = "root_symbol_route" if symbol_route_failed else fetcher_id or snapshot_type
    return {
        "snapshot_type": snapshot_type,
        "symbol": symbol,
        "query": query,
        "snapshot_date": retrieved_at[:10],
        "retrieved_at": retrieved_at,
        "facts": facts,
        "quality_status": "data_gap",
        "degraded": True,
        "degrade_reason": reason,
        "missing_fields": [snapshot_type],
        "fallback_applied": True,
        "source": {
            "skill": "uzi_root_collection" if symbol_route_failed else f"uzi_{snapshot_type}_snapshot_skill",
            "vendor": "UZI-Skill" if fetcher_id else "finance_ashare",
            "provider_id": provider_id,
            "source_id": source_id,
            "chain_position": 0,
            "dim": snapshot_type,
            "from_cache": False,
        },
        "source_trace": [
            {
                "source_id": source_id,
                "provider_id": provider_id,
                "status": status,
                "fallback": True,
                "error_type": exc.__class__.__name__,
            }
        ],
    }


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


def _snapshot_cache_schema_version(snapshot_type: str) -> str:
    base_version = _SNAPSHOT_CACHE_SCHEMA_VERSIONS.get(snapshot_type, "1")
    if snapshot_type in _UZI_FETCHER_BY_SNAPSHOT_TYPE:
        return f"{base_version}+{_UZI_NATIVE_SNAPSHOT_CACHE_SCHEMA_VERSION}"
    return base_version


def _snapshot_cache_context(
    *,
    snapshot_type: str,
    target: str,
    profile: str | None,
) -> dict[str, Any]:
    """Describe the Pack-owned raw Provider cache, not a reusable Board fact."""

    cache_profile = (
        ""
        if _uses_uzi_resume_cache(snapshot_type)
        else str(profile or "").strip().lower()
    )
    return {
        "cache_layer": "provider_raw",
        "domain_id": "finance_ashare",
        "artifact_type": "snapshot",
        "snapshot_type": snapshot_type,
        "provider": _SNAPSHOT_PROVIDER_HINTS.get(snapshot_type, snapshot_type),
        "schema_version": _snapshot_cache_schema_version(snapshot_type),
        "target": str(target).strip(),
        "profile": cache_profile,
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


def _normalized_security_code(value: Any) -> str:
    """Return the six-digit A-share code used by UZI row payloads."""

    text = str(value or "").strip().upper()
    if not text:
        return ""
    for token in (text.split(".", 1)[0], text[2:] if text[:2] in {"SH", "SZ", "BJ"} else text):
        digits = "".join(character for character in token if character.isdigit())
        if len(digits) >= 6:
            return digits[-6:]
    return ""


def _rows_include_symbol(rows: Any, *, symbol: str) -> bool:
    if not isinstance(rows, list) or not rows:
        return False
    target = _normalized_security_code(symbol)
    if not target:
        return False
    code_fields = ("代码", "股票代码", "证券代码", "symbol", "ticker", "code")
    return any(
        isinstance(row, dict)
        and any(
            _normalized_security_code(row.get(field)) == target
            for field in code_fields
            if row.get(field) is not None
        )
        for row in rows
    )


def _capital_flow_has_target_observation(
    data: dict[str, Any],
    *,
    symbol: str,
) -> bool:
    """Reject the fixed UZI capital-flow skeleton as a real observation.

    The pinned fetcher always returns quarter labels and zero-valued series,
    and its current GDHS call can return an unrelated all-market slice.  Those
    values keep the upstream payload intact but must not make the host claim
    that target-security capital-flow data is available.
    """

    main_flow = data.get("main_fund_flow_20d")
    if isinstance(main_flow, list) and any(isinstance(row, dict) and row for row in main_flow):
        return True

    northbound = data.get("northbound")
    if isinstance(northbound, dict):
        history = northbound.get("flow_history")
        if isinstance(history, list) and any(isinstance(row, dict) and row for row in history):
            return True

    for field in (
        "margin_recent",
        "holder_count_history",
        "block_trades_recent",
        "unlock_recent",
    ):
        if _rows_include_symbol(data.get(field), symbol=symbol):
            return True

    unlock_schedule = data.get("unlock_schedule")
    if isinstance(unlock_schedule, list) and any(
        isinstance(row, dict) and row for row in unlock_schedule
    ):
        return True

    institutional = data.get("institutional_history")
    if isinstance(institutional, dict) and any(
        isinstance(value, (int, float)) and not isinstance(value, bool) and value != 0
        for field in ("fund", "qfii", "shehui")
        for value in (institutional.get(field) or [])
    ):
        return True

    market_value_history = data.get("mv_history_30d")
    return isinstance(market_value_history, list) and any(
        isinstance(row, dict) and row for row in market_value_history
    )


def _uzi_pipeline_quality_floor(
    *,
    snapshot_type: str,
    symbol: str,
    data: dict[str, Any],
    pipeline_meta: dict[str, Any],
    error: str,
) -> tuple[str | None, list[str], list[str]]:
    """Map pinned DimResult quality without changing its raw payload."""

    pipeline_quality = str(pipeline_meta.get("quality") or "").strip().lower()
    pipeline_gaps = list(
        dict.fromkeys(
            str(item).strip()
            for item in list(pipeline_meta.get("data_gaps") or [])
            if str(item).strip()
        )
    )
    if error or pipeline_quality in {"error", "missing"}:
        return "data_gap", pipeline_gaps, ["uzi_pipeline_error"]
    if pipeline_quality == "partial" or pipeline_gaps:
        if snapshot_type == "capital_flow" and not _capital_flow_has_target_observation(
            data,
            symbol=symbol,
        ):
            return (
                "data_gap",
                list(dict.fromkeys([*pipeline_gaps, "capital_flow_target_observation"])),
                ["capital_flow_target_observation_missing", "uzi_pipeline_partial"],
            )
        return "degraded", pipeline_gaps, ["uzi_pipeline_partial"]
    if pipeline_quality == "full":
        if data:
            return None, [], []
        return "data_gap", [], ["uzi_pipeline_data_missing"]
    return None, [], []


def _apply_quality_floor(declared: str, quality_floor: str | None) -> str:
    if quality_floor == "data_gap":
        return "data_gap"
    if quality_floor == "degraded" and declared == "available":
        return "degraded"
    return declared


def _load_snapshot_cache(
    *,
    cache_root: Path | None,
    snapshot_type: str,
    target: str,
    profile: str | None,
    allow_expired: bool = False,
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
    if expires_at <= datetime.now(timezone.utc) and not allow_expired:
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
    cache_context = _snapshot_cache_context(
        snapshot_type=snapshot_type,
        target=target,
        profile=profile,
    )
    write_json(
        path,
        {
            "cache_layer": "provider_raw",
            "domain_id": "finance_ashare",
            "artifact_type": "snapshot",
            "snapshot_type": snapshot_type,
            "provider": _SNAPSHOT_PROVIDER_HINTS.get(snapshot_type, snapshot_type),
            "schema_version": cache_context["schema_version"],
            "profile": str(profile or "").strip().lower() or None,
            "cache_context": cache_context,
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
    no_resume: bool = False,
    cancellation=None,
    security_master: AShareSecurityMaster | None = None,
    cache_root: Path | None = None,
    research_profile: str | None = None,
    provider_registry: VendorProviderRegistry | None = None,
    progress_callback=None,
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
            intent=intent,
            research_profile=research_profile,
            settings=providers.settings,
            cancellation=cancellation,
            parallelism="per_snapshot_dimension",
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
                progress_callback=progress_callback,
                uzi_root_route=intent in _UZI_ROOT_ROUTE_INTENTS,
                uzi_root_route_mode=(
                    "multi" if intent in _UZI_MULTI_ROOT_INTENTS else "single"
                ),
                no_resume=no_resume,
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
                    progress_callback=progress_callback,
                    uzi_root_route=False,
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
        intent=intent,
        research_profile=research_profile,
        settings=providers.settings,
        cancellation=cancellation,
        parallelism=(
            "per_symbol_uzi_pipeline"
            if intent in _UZI_ROOT_ROUTE_INTENTS
            else "per_snapshot_dimension"
        ),
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
    intent: str | None = None,
    cancellation: Any | None = None,
    parallelism: str = "per_snapshot_dimension",
) -> dict[str, Any]:
    # Execute the pinned suggested-fix worklist until every real action either
    # resolves its target or records an explicit exhaustion reason.  There is
    # deliberately no fixed retry/round count.
    normalized_snapshots = [_normalize_snapshot_quality(snapshot) for snapshot in snapshots]
    integrity_before = assess_integrity(normalized_snapshots, symbols)
    selected_profile = str(research_profile or "").strip().lower()
    normalized_intent = str(intent or "").strip()
    profileless_uzi_command = (
        normalized_intent == "quick_scan"
        or (
            normalized_intent.startswith("uzi_")
            and normalized_intent not in _UZI_ROOT_ROUTE_INTENTS
        )
    )
    if not selected_profile and not profileless_uzi_command:
        raise ValueError("research_profile is required; choose lite, medium or deep")
    if profileless_uzi_command:
        command_stage_disabled = {
            "schema_version": "uzi-3.9.4-agent-os-v1",
            "profile": selected_profile or "uzi_command",
            "enabled": False,
            "attempted": 0,
            "succeeded": 0,
            "failed": 0,
            "attempts": [],
            "disabled_reason": "standalone_uzi_command",
        }
        qualitative_recovery = dict(command_stage_disabled)
        playwright_recovery = dict(command_stage_disabled)
        qualitative_agent_evidence = dict(command_stage_disabled)
        strict_recovery = dict(command_stage_disabled)
    elif selected_profile in {"lite", "medium", "deep"}:
        route_failed_symbols = {
            str(snapshot.get("symbol") or "").strip()
            for snapshot in normalized_snapshots
            if isinstance(snapshot, dict)
            and isinstance(snapshot.get("facts"), dict)
            and snapshot["facts"].get("_uzi_symbol_route_failed") is True
            and str(snapshot.get("symbol") or "").strip()
        }
        recovery_snapshots = [
            snapshot
            for snapshot in normalized_snapshots
            if str(snapshot.get("symbol") or "").strip() not in route_failed_symbols
        ]
        recovery_symbols = [
            symbol for symbol in symbols if str(symbol).strip() not in route_failed_symbols
        ]
        has_native_payload = any(
            isinstance((snapshot.get("facts") or {}).get("_uzi_raw_result"), dict)
            for snapshot in recovery_snapshots
            if isinstance(snapshot, dict) and isinstance(snapshot.get("facts"), dict)
        )
        strict_recovery = (
            recover_suggested_fixes_until_exhausted(
                recovery_snapshots,
                query=query,
                symbols=recovery_symbols,
                profile=selected_profile,
                settings=settings,
                cancellation=cancellation,
            )
            if has_native_payload
            else {
                "schema_version": "uzi-3.9.4-suggested-fix-loop-v1",
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
        if has_native_payload:
            # Exact UZI 3.9.4 Stage 1 order: integrity/suggested-fix worklist,
            # six-dimension MX→DDGS empty-field autofill, then the
            # profile-scoped Playwright fallback.  Agent task2.5 evidence is
            # collected afterwards in three parallel groups and frozen for
            # the analysis node.
            qualitative_recovery = recover_qualitative_snapshots(
                recovery_snapshots,
                query=query,
                symbols=recovery_symbols,
                profile=selected_profile,
                settings=settings,
            )
            playwright_recovery = recover_playwright_snapshots(
                recovery_snapshots,
                query=query,
                symbols=recovery_symbols,
                profile=selected_profile,
                settings=settings,
            )
            qualitative_agent_evidence = collect_qualitative_agent_evidence(
                recovery_snapshots,
                query=query,
                symbols=recovery_symbols,
                profile=selected_profile,
                settings=settings,
                cancellation=cancellation,
            )
        else:
            stage_disabled = {
                "schema_version": "uzi-3.9.4-agent-os-v1",
                "profile": selected_profile,
                "enabled": False,
                "attempted": 0,
                "succeeded": 0,
                "failed": 0,
                "attempts": [],
                "disabled_reason": "native_uzi_payload_unavailable",
            }
            qualitative_recovery = dict(stage_disabled)
            playwright_recovery = dict(stage_disabled)
            qualitative_agent_evidence = dict(stage_disabled)
    elif selected_profile == "workspace_market_data":
        # Non-UZI workspaces (for example the standalone market-data view)
        # share the collector but must not silently enter an analysis profile.
        qualitative_recovery = {
            "schema_version": "uzi-3.9.4-agent-os-v1",
            "profile": selected_profile,
            "enabled": False,
            "attempted": 0,
            "succeeded": 0,
            "failed": 0,
            "attempts": [],
            "disabled_reason": "non_uzi_collection_profile",
        }
        playwright_recovery = dict(qualitative_recovery)
        qualitative_agent_evidence = dict(qualitative_recovery)
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
                for status in (
                    "degraded",
                    "rate_limited",
                    "provider_unavailable",
                    "parse_failed",
                    "no_data",
                    "data_gap",
                )
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
            "parallelism": parallelism,
            "uzi_integrity": integrity,
            "uzi_recovery": _recovery_summary(
                normalized_snapshots,
                qualitative=qualitative_recovery,
                playwright=playwright_recovery,
                agent_evidence=qualitative_agent_evidence,
                strict=strict_recovery,
            ),
        },
    }


def _recovery_summary(
    snapshots: list[dict[str, Any]],
    *,
    qualitative: dict[str, Any] | None = None,
    playwright: dict[str, Any] | None = None,
    agent_evidence: dict[str, Any] | None = None,
    strict: dict[str, Any] | None = None,
) -> dict[str, Any]:
    strict_payload = dict(strict or {})
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
        "schema_version": "uzi-3.9.4-agent-os-v1",
        "stage_order": [
            "primary_providers", "integrity_check", "suggested_fix_loop",
            "mx_api", "ddgs", "playwright", "qualitative_agent_evidence",
            "integrity_recheck",
        ],
        "order": ["mx_api", "ddgs", "playwright"],
        "attempted": int(strict_payload.get("attempted") or 0) + len(attempts),
        "succeeded": int(strict_payload.get("succeeded") or 0) + sum(row.get("status") == "available" for row in attempts),
        "failed": int(strict_payload.get("failed") or 0) + sum(row.get("status") != "available" for row in attempts),
        "attempts": [*list(strict_payload.get("attempts") or []), *attempts],
        "strict_stage": strict_payload,
        "qualitative_stage": dict(qualitative or {}),
        "playwright_stage": dict(playwright or {}),
        "qualitative_agent_evidence_stage": dict(agent_evidence or {}),
    }


def _normalize_snapshot_quality(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Promote source quality to a stable per-dimension contract."""

    normalized = dict(snapshot)
    facts = (
        dict(normalized.get("facts"))
        if isinstance(normalized.get("facts"), dict)
        else {}
    )
    normalized["facts"] = facts
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
    raw_result = facts.get("_uzi_raw_result")
    pipeline_meta = (
        raw_result.get("_pipeline")
        if isinstance(raw_result, dict) and isinstance(raw_result.get("_pipeline"), dict)
        else {}
    )
    raw_data = (
        raw_result.get("data")
        if isinstance(raw_result, dict) and isinstance(raw_result.get("data"), dict)
        else {}
    )
    raw_error = str(
        (raw_result or {}).get("error")
        or pipeline_meta.get("error")
        or ""
    ).strip() if isinstance(raw_result, dict) else ""
    quality_floor, pipeline_gaps, pipeline_reasons = _uzi_pipeline_quality_floor(
        snapshot_type=str(normalized.get("snapshot_type") or ""),
        symbol=str(
            (raw_result or {}).get("full")
            or (raw_result or {}).get("ticker")
            or normalized.get("symbol")
            or ""
        ) if isinstance(raw_result, dict) else str(normalized.get("symbol") or ""),
        data=dict(raw_data),
        pipeline_meta=dict(pipeline_meta),
        error=raw_error,
    )
    declared = _apply_quality_floor(declared, quality_floor)
    normalized["degraded"] = declared != "available"
    reasons = [
        str(item).strip()
        for item in (
            list(normalized.get("degrade_reasons") or [])
            + ([normalized.get("degrade_reason")] if normalized.get("degrade_reason") else [])
            + list(facts.get("data_gap") or [])
            + pipeline_reasons
        )
        if str(item).strip()
    ]
    normalized["quality_status"] = declared
    normalized["degrade_reasons"] = list(dict.fromkeys(reasons))
    normalized["missing_fields"] = list(
        dict.fromkeys(
            str(item).strip()
            for item in list(normalized.get("missing_fields") or [])
            + pipeline_gaps
            if str(item).strip()
        )
    )
    facts["availability"] = declared
    normalized["scope"] = (
        "symbol"
        if str(normalized.get("symbol") or "").strip()
        else "sector"
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
