from __future__ import annotations

import hashlib
import inspect
import json
import math
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from threading import Event, Lock
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
from domain_packs.finance_ashare.vendor.uzi_3_9_2 import Uzi392FetcherRuntime
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
from research_system.adapters.vendors.uzi_shared_source_cache import (
    install_uzi_shared_source_cache,
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
    "capital_flow": "efinance-recovery-v2",
}
_UZI_NATIVE_SNAPSHOT_CACHE_SCHEMA_VERSION = "uzi-3.9.4-native-raw-v1+quality-v2"
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
_UZI_MINI_RACER_TIMEOUT_ACTIVE = Event()
_UZI_MAX_WORKERS = 6
# Pinned UZI run_real_test.py gives its parallel fetcher wave a five-minute
# hard ceiling.  Keep that native boundary inside the Pack instead of applying
# a Tool Registry timeout to the whole Agent OS capability.
_UZI_FETCHER_WAVE_TIMEOUT_SECONDS = 300.0
# Industry/valuation/capital-flow share UZI's native mini-racer lane.  Give
# each request an independent ceiling so one cold or blocked provider cannot
# consume the whole 300-second parallel wave and make all three dimensions
# look unavailable.  If a timed-out call is still unwinding, later calls use
# field-level recovery instead of queueing behind the occupied native lock.
_UZI_SERIAL_FETCHER_TIMEOUT_SECONDS = 45.0
_UZI_RECOVERY_TIMEOUT_SECONDS = 30.0
_UZI_INDIVIDUAL_DEADLINE_FETCHERS = frozenset({"11_governance", "16_lhb"})
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
            emit_progress(phase="cache_hit", snapshot_type=snapshot_type)
    if pending_tasks:
        initialize_akshare_js_runtime()
        # UZI runs dimension 0 first because the dependent macro/industry/
        # futures/policy dimensions consume the resolved real industry.
        basic_tasks = [task for task in pending_tasks if task[2] == "market"]
        for index, symbol, snapshot_type in basic_tasks:
            emit_progress(phase="collecting", snapshot_type=snapshot_type)
            result = _run_snapshot_skill_with_deadline(
                query=query,
                symbol=symbol,
                snapshot_type=snapshot_type,
                provider_registry=providers,
                dependencies=None,
                timeout_seconds=_UZI_SERIAL_FETCHER_TIMEOUT_SECONDS,
            )
            results[index] = result
            _save_snapshot_cache(
                cache_root=cache_root,
                snapshot_type=snapshot_type,
                target=symbol,
                profile=research_profile,
                snapshot=result,
            )
            emit_progress(phase="completed", snapshot_type=snapshot_type)
        pending_tasks = [task for task in pending_tasks if task[2] != "market"]
        basic_by_symbol = {
            symbol: dependency
            for index, symbol, snapshot_type in tasks
            if snapshot_type == "market"
            and index in results
            and (dependency := _uzi_dependency_result(results[index])) is not None
        }
        # UZI main makes dimension 9 depend on dimension 8 so the futures
        # contract is the material actually identified for the company rather
        # than a generic hard-coded commodity. Material tasks therefore form
        # a small first wave; all remaining independent tasks stay parallel.
        material_tasks = [task for task in pending_tasks if task[2] == "materials"]
        for index, symbol, snapshot_type in material_tasks:
            emit_progress(phase="collecting", snapshot_type=snapshot_type)
            dependencies = (
                {"0_basic": basic_by_symbol[symbol]}
                if symbol in basic_by_symbol
                else None
            )
            result = _run_snapshot_skill_with_deadline(
                query=query,
                symbol=symbol,
                snapshot_type=snapshot_type,
                provider_registry=providers,
                dependencies=dependencies,
                timeout_seconds=_UZI_SERIAL_FETCHER_TIMEOUT_SECONDS,
            )
            results[index] = result
            _save_snapshot_cache(
                cache_root=cache_root,
                snapshot_type=snapshot_type,
                target=symbol,
                profile=research_profile,
                snapshot=result,
            )
            emit_progress(phase="completed", snapshot_type=snapshot_type)
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

        def task_dependencies(symbol: str, snapshot_type: str) -> dict[str, dict[str, Any]] | None:
            dependencies: dict[str, dict[str, Any]] = {}
            if symbol in basic_by_symbol:
                dependencies["0_basic"] = basic_by_symbol[symbol]
            if snapshot_type == "futures":
                if _uses_uzi_fetcher(snapshot_type) and symbol in material_by_symbol:
                    dependencies["8_materials"] = material_by_symbol[symbol]
                elif symbol in material_snapshot_by_symbol:
                    dependencies["materials"] = material_snapshot_by_symbol[symbol]
            return dependencies or None

        # These three native fetchers share a process-wide mini-racer lock.
        # Run them as an explicit serial wave with an individual deadline,
        # instead of letting one slow call expire the unrelated parallel wave.
        serial_tasks = [
            task
            for task in pending_tasks
            if _UZI_FETCHER_BY_SNAPSHOT_TYPE.get(task[2]) in _UZI_MINI_RACER_FETCHERS
        ]
        for index, symbol, snapshot_type in serial_tasks:
            emit_progress(phase="collecting", snapshot_type=snapshot_type)
            dependencies = task_dependencies(symbol, snapshot_type)
            if _UZI_MINI_RACER_TIMEOUT_ACTIVE.is_set():
                result = _fetcher_timeout_snapshot(
                    query=query,
                    symbol=symbol,
                    snapshot_type=snapshot_type,
                    timeout_seconds=_UZI_SERIAL_FETCHER_TIMEOUT_SECONDS,
                    provider_registry=providers,
                    dependencies=dependencies,
                    reason="native serial lane still occupied after an earlier timeout",
                )
            else:
                result = _run_snapshot_skill_with_deadline(
                    query=query,
                    symbol=symbol,
                    snapshot_type=snapshot_type,
                    provider_registry=providers,
                    dependencies=dependencies,
                    timeout_seconds=_UZI_SERIAL_FETCHER_TIMEOUT_SECONDS,
                    occupies_mini_racer_lane=True,
                )
            results[index] = result
            _save_snapshot_cache(
                cache_root=cache_root,
                snapshot_type=snapshot_type,
                target=symbol,
                profile=research_profile,
                snapshot=result,
            )
            emit_progress(phase="completed", snapshot_type=snapshot_type)
        pending_tasks = [task for task in pending_tasks if task not in serial_tasks]

        # UZI's native collectors use six workers. Keep the same in-stock
        # fan-out so provider pressure and cold-cache latency match upstream.
        requested_workers = _UZI_MAX_WORKERS if max_workers is None else max(int(max_workers), 1)
        if pending_tasks:
            executor = ThreadPoolExecutor(
                max_workers=min(requested_workers, len(pending_tasks)),
                thread_name_prefix="finance-snapshot",
            )
            futures = {}
            for index, symbol, snapshot_type in pending_tasks:
                dependencies = task_dependencies(symbol, snapshot_type)
                if (
                    _UZI_FETCHER_BY_SNAPSHOT_TYPE.get(snapshot_type)
                    in _UZI_INDIVIDUAL_DEADLINE_FETCHERS
                ):
                    future = executor.submit(
                        _run_snapshot_skill_with_deadline,
                        query=query,
                        symbol=symbol,
                        snapshot_type=snapshot_type,
                        provider_registry=providers,
                        dependencies=dependencies,
                        timeout_seconds=_UZI_SERIAL_FETCHER_TIMEOUT_SECONDS,
                    )
                else:
                    future = executor.submit(
                        _run_snapshot_skill,
                        query,
                        symbol,
                        snapshot_type,
                        providers,
                        dependencies,
                    )
                futures[future] = (index, symbol, snapshot_type, dependencies)
            if pending_tasks:
                emit_progress(phase="collecting", snapshot_type=pending_tasks[0][2])
            try:
                try:
                    for future in as_completed(
                        futures,
                        timeout=_UZI_FETCHER_WAVE_TIMEOUT_SECONDS,
                    ):
                        index, symbol, snapshot_type, _dependencies = futures[future]
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
                        emit_progress(phase="completed", snapshot_type=snapshot_type)
                except FutureTimeoutError:
                    for future, (index, symbol, snapshot_type, dependencies) in futures.items():
                        if index in results:
                            continue
                        if future.done() and not future.cancelled():
                            result = future.result()
                        else:
                            future.cancel()
                            result = _fetcher_timeout_snapshot(
                                query=query,
                                symbol=symbol,
                                snapshot_type=snapshot_type,
                                timeout_seconds=_UZI_FETCHER_WAVE_TIMEOUT_SECONDS,
                                provider_registry=providers,
                                dependencies=dependencies,
                            )
                        results[index] = result
                        _save_snapshot_cache(
                            cache_root=cache_root,
                            snapshot_type=snapshot_type,
                            target=symbol,
                            profile=research_profile,
                            snapshot=result,
                        )
                        emit_progress(phase="completed", snapshot_type=snapshot_type)
            finally:
                executor.shutdown(wait=False, cancel_futures=True)
    snapshots.extend(results[index] for index, _symbol, _snapshot_type in tasks if index in results)
    snapshots.extend(
        _missing_scope_snapshot(query=query, snapshot_type=snapshot_type)
        for snapshot_type in requested
        if snapshot_type == "sector" and not sector
    )
    return snapshots


def _run_snapshot_skill_with_deadline(
    *,
    query: str,
    symbol: str,
    snapshot_type: str,
    provider_registry: VendorProviderRegistry,
    dependencies: dict[str, dict[str, Any]] | None,
    timeout_seconds: float,
    occupies_mini_racer_lane: bool = False,
) -> dict[str, Any]:
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="uzi-serial-fetcher")
    future = executor.submit(
        _run_snapshot_skill,
        query,
        symbol,
        snapshot_type,
        provider_registry,
        dependencies,
    )
    try:
        return future.result(timeout=timeout_seconds)
    except FutureTimeoutError:
        if occupies_mini_racer_lane:
            _UZI_MINI_RACER_TIMEOUT_ACTIVE.set()

        def release_lane(_future) -> None:
            if occupies_mini_racer_lane:
                _UZI_MINI_RACER_TIMEOUT_ACTIVE.clear()

        future.add_done_callback(release_lane)
        future.cancel()
        return _fetcher_timeout_snapshot(
            query=query,
            symbol=symbol,
            snapshot_type=snapshot_type,
            timeout_seconds=timeout_seconds,
            provider_registry=provider_registry,
            dependencies=dependencies,
        )
    finally:
        # Provider calls have their own transport deadlines but Python cannot
        # forcibly kill an in-flight thread.  Do not block the report response;
        # the event above keeps later runs from queueing behind it meanwhile.
        executor.shutdown(wait=False, cancel_futures=True)


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
    install_baostock_socket_safety(
        timeout_seconds=getattr(
            settings,
            "baostock_socket_timeout_seconds",
            DEFAULT_BAOSTOCK_SOCKET_TIMEOUT_SECONDS,
        )
    )
    # The pinned UZI fetchers remain unchanged.  Only their exact high-cost
    # AkShare transport results are shared across concurrent holding runs so
    # one real full-market response is downloaded once per cache key.
    install_uzi_shared_source_cache(settings.workspace_root)
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
    collect_kwargs = {
        "ticker": str(symbol or "").strip(),
        "industry": industry,
        "dependencies": dependencies,
        "options": options,
    }
    if fetcher_id in _UZI_MINI_RACER_FETCHERS:
        # UZI's run_real_test and v3 pipeline share one lock across industry,
        # valuation and capital-flow because their AkShare paths initialize
        # the same V8/mini-racer runtime. Holding reports may run concurrently
        # at the product layer, but these native fetchers remain serialized.
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
    snapshot = {
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
    if snapshot_type == "market" and not _positive_dimension_value(data.get("price")):
        snapshot = _recover_market_snapshot(
            snapshot=snapshot,
            query=query,
            symbol=target_symbol,
            provider_registry=provider_registry,
        )
    elif snapshot_type == "valuation" and not _valuation_has_core(data):
        snapshot = _recover_valuation_snapshot(
            snapshot=snapshot,
            dependencies=dependencies,
        )
    elif (
        snapshot_type == "capital_flow"
        and "main_fund_flow_20d" in data
        and not list(data.get("main_fund_flow_20d") or [])
    ):
        snapshot = _recover_capital_flow_snapshot(
            snapshot=snapshot,
            query=query,
            symbol=target_symbol,
            provider_registry=provider_registry,
        )
    elif (
        snapshot_type == "governance"
        and ("pledge" in data or "insider_trades_1y" in data)
        and not isinstance(data.get("source_coverage"), dict)
    ):
        snapshot = _recover_governance_snapshot(
            snapshot=snapshot,
            query=query,
            symbol=target_symbol,
            provider_registry=provider_registry,
        )
    elif (
        snapshot_type == "lhb"
        and data.get("lhb_count_30d") == 0
        and not list(data.get("lhb_records") or [])
    ):
        snapshot = _recover_lhb_snapshot(
            snapshot=snapshot,
            query=query,
            symbol=target_symbol,
            provider_registry=provider_registry,
        )
    return _apply_dimension_quality_contract(snapshot)


def _fetcher_timeout_snapshot(
    *,
    query: str,
    symbol: str,
    snapshot_type: str,
    timeout_seconds: float,
    provider_registry: VendorProviderRegistry | None = None,
    dependencies: dict[str, dict[str, Any]] | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    fetcher_id = _UZI_FETCHER_BY_SNAPSHOT_TYPE.get(snapshot_type)
    reason = reason or f"fetcher timeout > {timeout_seconds:g}s"
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
    snapshot = {
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
    if snapshot_type == "market":
        snapshot = _recover_market_snapshot(
            snapshot=snapshot,
            query=query,
            symbol=symbol,
            provider_registry=provider_registry,
        )
    elif snapshot_type == "valuation":
        snapshot = _recover_valuation_snapshot(
            snapshot=snapshot,
            dependencies=dependencies,
        )
    elif snapshot_type == "capital_flow":
        snapshot = _recover_capital_flow_snapshot(
            snapshot=snapshot,
            query=query,
            symbol=symbol,
            provider_registry=provider_registry,
        )
    elif snapshot_type == "governance":
        snapshot = _recover_governance_snapshot(
            snapshot=snapshot,
            query=query,
            symbol=symbol,
            provider_registry=provider_registry,
        )
    elif snapshot_type == "lhb":
        snapshot = _recover_lhb_snapshot(
            snapshot=snapshot,
            query=query,
            symbol=symbol,
            provider_registry=provider_registry,
        )
    return _apply_dimension_quality_contract(snapshot)


def _run_recovery_with_deadline(
    loader: Callable[[], dict[str, Any]],
    *,
    timeout_seconds: float = _UZI_RECOVERY_TIMEOUT_SECONDS,
) -> tuple[dict[str, Any] | None, str | None]:
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="snapshot-recovery")
    future = executor.submit(loader)
    try:
        result = future.result(timeout=timeout_seconds)
        return (dict(result), None) if isinstance(result, dict) else (None, "InvalidRecoveryPayload")
    except FutureTimeoutError:
        future.cancel()
        return None, "RecoveryTimeout"
    except Exception as exc:
        return None, exc.__class__.__name__
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _recover_market_snapshot(
    *,
    snapshot: dict[str, Any],
    query: str,
    symbol: str | None,
    provider_registry: VendorProviderRegistry | None,
) -> dict[str, Any]:
    if not symbol:
        return snapshot
    registry = provider_registry or _default_provider_registry()
    # The native UZI path already attempted its AkShare/XueQiu chain.  Start
    # recovery at the independent zero-key providers to avoid repeating the
    # same failing transport before reaching eFinance or BaoStock.
    fallback_settings = registry.settings.model_copy(deep=True)
    fallback_settings.provider_policy_overrides["market"] = (
        "efinance_market,baostock_market,direct_http_market,tushare_market"
    )
    fallback_registry = VendorProviderRegistry(
        fallback_settings,
        data_sources=registry.data_sources,
    )
    recovery, error_type = _run_recovery_with_deadline(
        lambda: MarketSnapshotSkill(fallback_registry).run(query=query, symbol=symbol)
    )
    if recovery is None:
        return _append_recovery_failure(snapshot, "market_fallback", error_type or "ProviderError")
    recovery_facts = recovery.get("facts") if isinstance(recovery.get("facts"), dict) else {}
    if str(recovery_facts.get("availability") or "").lower() != "available":
        return _append_recovery_trace(snapshot, recovery)

    overview = (
        dict(recovery_facts.get("market_overview") or {})
        if isinstance(recovery_facts.get("market_overview"), dict)
        else {}
    )
    history = (
        dict(recovery_facts.get("market_hist") or {})
        if isinstance(recovery_facts.get("market_hist"), dict)
        else {}
    )
    latest_bar = (
        dict(history.get("latest_bar") or {})
        if isinstance(history.get("latest_bar"), dict)
        else {}
    )
    projected = {
        "name": overview.get("security_name") or latest_bar.get("name"),
        "price": overview.get("latest_price") or latest_bar.get("close"),
        "change_pct": overview.get("pct_change") or latest_bar.get("pct_change"),
        "industry": overview.get("industry"),
        "market_cap_raw": overview.get("market_cap"),
        "circulating_cap_raw": overview.get("float_market_cap"),
        "pb": overview.get("pb"),
        "pe_dynamic": overview.get("pe_dynamic"),
        "open": (overview.get("spot") or {}).get("今开") or latest_bar.get("open"),
        "prev_close": (overview.get("spot") or {}).get("昨收") or latest_bar.get("pre_close"),
        "high": (overview.get("spot") or {}).get("最高") or latest_bar.get("high"),
        "low": (overview.get("spot") or {}).get("最低") or latest_bar.get("low"),
    }
    if not any(not _missing_dimension_value(value) for value in projected.values()):
        return _append_recovery_trace(snapshot, recovery)
    projected["market_overview"] = {
        "security_name": projected["name"],
        "industry": projected["industry"],
        "latest_price": projected["price"],
        "pct_change": projected["change_pct"],
        "market_cap": projected["market_cap_raw"],
        "float_market_cap": projected["circulating_cap_raw"],
        "pb": projected["pb"],
        "pe_dynamic": projected["pe_dynamic"],
        "latest_bar": {
            "close": projected["price"],
            "pct_change": projected["change_pct"],
            "open": projected["open"],
            "pre_close": projected["prev_close"],
            "high": projected["high"],
            "low": projected["low"],
        },
        "valuation": {
            "pb": projected["pb"],
            "pe_dynamic": projected["pe_dynamic"],
            "market_cap": projected["market_cap_raw"],
        },
    }
    return _merge_recovered_fields(
        snapshot,
        recovery=recovery,
        projected=projected,
        partial_reason="uzi_market_incomplete_provider_fields_recovered",
        force_fields={"price"},
    )


def _recover_valuation_snapshot(
    *,
    snapshot: dict[str, Any],
    dependencies: dict[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    basic_result = (dependencies or {}).get("0_basic") or {}
    basic = basic_result.get("data") if isinstance(basic_result.get("data"), dict) else {}
    if not basic:
        return snapshot
    pe = basic.get("pe_ttm")
    pe_basis = "ttm"
    if _missing_dimension_value(pe):
        pe = basic.get("pe_dynamic")
        pe_basis = "dynamic"
    projected = {
        "pe": str(pe) if not _missing_dimension_value(pe) else None,
        "pb": str(basic.get("pb")) if not _missing_dimension_value(basic.get("pb")) else None,
        "current_price": basic.get("price"),
        "market_cap_raw": basic.get("market_cap_raw"),
        "industry": basic.get("industry"),
        "pe_basis": pe_basis if not _missing_dimension_value(pe) else None,
    }
    if not any(not _missing_dimension_value(value) for value in projected.values()):
        return snapshot
    dependency_source = {
        "skill": "uzi_dependency_recovery",
        "vendor": "Agent OS",
        "provider_id": "dependency.0_basic",
        "source_id": str(basic_result.get("source") or "0_basic"),
        "chain_position": 1,
        "dim": "valuation",
        "from_cache": False,
    }
    recovery = {
        "source": dependency_source,
        "source_trace": [
            {
                "provider_id": "dependency.0_basic",
                "source_id": dependency_source["source_id"],
                "status": "available",
            }
        ],
    }
    return _merge_recovered_fields(
        snapshot,
        recovery=recovery,
        projected=projected,
        partial_reason="uzi_valuation_unavailable_current_fields_recovered_from_0_basic",
        force_fields={"pe", "pb"},
    )


def _recover_lhb_snapshot(
    *,
    snapshot: dict[str, Any],
    query: str,
    symbol: str | None,
    provider_registry: VendorProviderRegistry | None,
) -> dict[str, Any]:
    if not symbol:
        return snapshot
    registry = provider_registry or _default_provider_registry()
    recovery, error_type = _run_recovery_with_deadline(
        lambda: LHBSnapshotSkill(registry).run(query=query, symbol=symbol)
    )
    if recovery is None:
        return _append_recovery_failure(snapshot, "lhb_fallback", error_type or "ProviderError")
    recovery_facts = recovery.get("facts") if isinstance(recovery.get("facts"), dict) else {}
    if str(recovery_facts.get("availability") or "").lower() != "available":
        return _append_recovery_trace(snapshot, recovery)
    detail_records = [
        dict(row)
        for row in [
            *list(recovery_facts.get("buy_detail") or []),
            *list(recovery_facts.get("sell_detail") or []),
        ]
        if isinstance(row, dict)
    ]
    statistic_records = [
        dict(row)
        for row in list(recovery_facts.get("lhb_statistics") or [])
        if isinstance(row, dict)
    ]
    records = detail_records or statistic_records
    projected = {
        "lhb_count_30d": recovery_facts.get("lhb_count_30d"),
        "lhb_records": records,
        "lhb_statistics": statistic_records,
        "latest_lhb_date": recovery_facts.get("latest_lhb_date"),
        "latest_historical_lhb_date": recovery_facts.get("latest_historical_lhb_date"),
        "matched_youzi": recovery_facts.get("matched_youzi") or [],
        "buy_top_total": recovery_facts.get("buy_top_total"),
        "sell_top_total": recovery_facts.get("sell_top_total"),
        "browser_evidence": recovery_facts.get("browser_evidence") or {},
        "observation_status": recovery_facts.get("observation_status"),
        "observation_window_days": recovery_facts.get("observation_window_days"),
    }
    return _merge_recovered_fields(
        snapshot,
        recovery=recovery,
        projected=projected,
        partial_reason="uzi_lhb_empty_provider_chain_recovery_applied",
        force_fields=(
            {"lhb_count_30d"}
            if recovery_facts.get("observation_status") == "verified_non_empty"
            else set()
        ),
    )


def _recover_governance_snapshot(
    *,
    snapshot: dict[str, Any],
    query: str,
    symbol: str | None,
    provider_registry: VendorProviderRegistry | None,
) -> dict[str, Any]:
    if not symbol:
        return snapshot
    registry = provider_registry or _default_provider_registry()
    recovery, error_type = _run_recovery_with_deadline(
        lambda: GovernanceSnapshotSkill(provider_registry=registry).run(
            query=query,
            symbol=symbol,
        )
    )
    if recovery is None:
        return _append_recovery_failure(
            snapshot,
            "governance_fallback",
            error_type or "ProviderError",
        )
    recovery_facts = recovery.get("facts") if isinstance(recovery.get("facts"), dict) else {}
    projected = {
        "pledge": recovery_facts.get("pledge") or [],
        "insider_trades_1y": recovery_facts.get("insider_trades_1y") or [],
        "observation_status": recovery_facts.get("observation_status"),
        "source_coverage": recovery_facts.get("source_coverage") or {},
        "governance_evidence": recovery_facts.get("governance_evidence") or [],
        "browser_evidence": recovery_facts.get("browser_evidence") or {},
        "company_info": recovery_facts.get("info") or {},
        "banking_metrics": recovery_facts.get("banking_metrics") or {},
    }
    if not any(not _missing_dimension_value(value) for value in projected.values()):
        return _append_recovery_trace(snapshot, recovery)
    return _merge_recovered_fields(
        snapshot,
        recovery=recovery,
        projected=projected,
        partial_reason="uzi_governance_empty_public_evidence_recovery_applied",
    )


def _recover_capital_flow_snapshot(
    *,
    snapshot: dict[str, Any],
    query: str,
    symbol: str | None,
    provider_registry: VendorProviderRegistry | None,
) -> dict[str, Any]:
    """Recover target-stock flow without replacing the pinned UZI fetcher.

    UZI remains the primary collector for the broad capital-flow dimension.
    When its individual-flow endpoint is empty or the native fetcher times out,
    the ordinary provider chain fills only the target-stock flow fields.  The
    untouched UZI result remains in ``_uzi_raw_result`` for parity and replay.
    """

    if not symbol:
        return snapshot
    registry = provider_registry or _default_provider_registry()
    recovery, error_type = _run_recovery_with_deadline(
        lambda: CapitalFlowSnapshotSkill(provider_registry=registry).run(
            query=query,
            symbol=symbol,
        )
    )
    if recovery is None:
        return _append_recovery_failure(
            snapshot,
            "capital_flow_fallback",
            error_type or "ProviderError",
        )
    recovery_facts = recovery.get("facts") if isinstance(recovery.get("facts"), dict) else {}
    if str(recovery_facts.get("availability") or "").lower() != "available":
        failed = dict(snapshot)
        failed["source_trace"] = [
            *list(snapshot.get("source_trace") or []),
            *list(recovery.get("source_trace") or []),
        ]
        return failed

    recovered_fields = (
        "symbol",
        "normalized_symbol",
        "latest_flow",
        "recent_flows",
        "main_fund_flow_20d",
        "main_20d",
        "main_5d",
        "main_flow_rank",
        "main_flow_rank_status",
    )
    facts = dict(snapshot.get("facts") or {})
    for field in recovered_fields:
        if field in recovery_facts:
            facts[field] = recovery_facts[field]
    facts["availability"] = "available"

    raw_result = facts.get("_uzi_raw_result")
    raw_data = raw_result.get("data") if isinstance(raw_result, dict) else None
    uzi_has_other_data = bool(raw_data) if isinstance(raw_data, dict) else False
    recovery_source = dict(recovery.get("source") or {})
    recovery_provider_id = str(recovery_source.get("provider_id") or "capital_flow_fallback")

    recovered = dict(snapshot)
    recovered.update(
        {
            "facts": facts,
            "quality_status": "available" if uzi_has_other_data else "degraded",
            "degraded": not uzi_has_other_data,
            "degrade_reason": None
            if uzi_has_other_data
            else "uzi_capital_flow_unavailable_core_flow_recovered",
            "missing_fields": [] if uzi_has_other_data else ["capital_flow.uzi_extended_fields"],
            "fallback_applied": True,
            "fallback_sources": [recovery_source] if recovery_source else [],
            "field_sources": {
                **dict(snapshot.get("field_sources") or {}),
                **{
                    field: recovery_provider_id
                    for field in recovered_fields
                    if field in recovery_facts
                },
            },
            "source_trace": [
                *list(snapshot.get("source_trace") or []),
                *list(recovery.get("source_trace") or []),
            ],
        }
    )
    return recovered


def _missing_dimension_value(value: Any) -> bool:
    """Return whether a provider value carries no usable observation.

    Zero is deliberately not treated as missing here: a verified zero can be
    a valid percentage, balance or event count.  Callers that require a
    positive metric (for example price or PE) apply that rule separately.
    """

    if value is None:
        return True
    if isinstance(value, float) and not math.isfinite(value):
        return True
    if isinstance(value, str):
        normalized = value.strip().lower()
        return normalized in {
            "",
            "-",
            "--",
            "—",
            "n/a",
            "na",
            "none",
            "null",
            "nan",
            "暂无",
            "暂无数据",
            "无数据",
        }
    if isinstance(value, (list, tuple, set, frozenset, dict)):
        return not value
    return False


def _positive_dimension_value(value: Any) -> bool:
    if _missing_dimension_value(value) or isinstance(value, bool):
        return False
    try:
        return float(str(value).replace(",", "")) > 0
    except (TypeError, ValueError):
        # Market-cap strings such as ``1.82万亿`` still represent a real
        # provider observation even though they are not plain floats.
        return bool(str(value).strip())


def _valuation_has_core(data: dict[str, Any]) -> bool:
    return any(
        _positive_dimension_value(data.get(field))
        for field in ("pe", "pe_ttm", "pe_dynamic", "pb")
    )


def _append_recovery_failure(
    snapshot: dict[str, Any],
    provider_id: str,
    error_type: str,
) -> dict[str, Any]:
    failed = dict(snapshot)
    failed["source_trace"] = [
        *list(snapshot.get("source_trace") or []),
        {
            "provider_id": provider_id,
            "source_id": provider_id,
            "status": "provider_unavailable",
            "fallback": True,
            "error_type": str(error_type or "ProviderError"),
        },
    ]
    return failed


def _append_recovery_trace(
    snapshot: dict[str, Any],
    recovery: dict[str, Any],
) -> dict[str, Any]:
    traced = dict(snapshot)
    recovery_trace = list(recovery.get("source_trace") or [])
    if not recovery_trace and isinstance(recovery.get("source"), dict):
        source = dict(recovery["source"])
        recovery_trace = [
            {
                "provider_id": source.get("provider_id"),
                "source_id": source.get("source_id"),
                "status": str(
                    (recovery.get("facts") or {}).get("availability") or "data_gap"
                ),
                "fallback": True,
            }
        ]
    traced["source_trace"] = [
        *list(snapshot.get("source_trace") or []),
        *recovery_trace,
    ]
    return traced


def _merge_missing_values(current: Any, incoming: Any) -> tuple[Any, bool]:
    """Recursively fill only absent values and report whether anything changed."""

    if isinstance(current, dict) and isinstance(incoming, dict):
        merged = dict(current)
        changed = False
        for key, value in incoming.items():
            if key in merged:
                merged_value, field_changed = _merge_missing_values(merged[key], value)
                if field_changed:
                    merged[key] = merged_value
                    changed = True
            elif not _missing_dimension_value(value):
                merged[key] = value
                changed = True
        return merged, changed
    if _missing_dimension_value(current) and not _missing_dimension_value(incoming):
        return incoming, True
    return current, False


def _merge_recovered_fields(
    snapshot: dict[str, Any],
    *,
    recovery: dict[str, Any],
    projected: dict[str, Any],
    partial_reason: str,
    force_fields: set[str] | None = None,
) -> dict[str, Any]:
    facts = dict(snapshot.get("facts") or {})
    changed_fields: list[str] = []
    forced = force_fields or set()
    for field, value in projected.items():
        current = facts.get(field)
        if field in forced and not _missing_dimension_value(value) and current != value:
            merged, changed = value, True
        else:
            merged, changed = _merge_missing_values(current, value)
        if changed:
            facts[field] = merged
            changed_fields.append(field)
    if not changed_fields:
        return _append_recovery_trace(snapshot, recovery)

    facts["availability"] = "available"
    facts["_recovery_reason"] = partial_reason
    recovery_source = (
        dict(recovery.get("source") or {})
        if isinstance(recovery.get("source"), dict)
        else {}
    )
    recovery_provider_id = str(
        recovery_source.get("provider_id") or recovery_source.get("source_id") or "provider_recovery"
    )
    fallback_sources = [
        *list(snapshot.get("fallback_sources") or []),
        *([recovery_source] if recovery_source else []),
    ]
    merged_snapshot = dict(snapshot)
    merged_snapshot.update(
        {
            "facts": facts,
            "quality_status": "available",
            "degraded": False,
            "degrade_reason": None,
            "fallback_applied": True,
            "fallback_sources": fallback_sources,
            "field_sources": {
                **dict(snapshot.get("field_sources") or {}),
                **{field: recovery_provider_id for field in changed_fields},
            },
            "source_trace": [
                *list(snapshot.get("source_trace") or []),
                *list(recovery.get("source_trace") or []),
            ],
        }
    )
    return merged_snapshot


def _uzi_dependency_result(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """Expose verified recovery fields to dependent UZI dimensions.

    The pinned native result remains byte-for-byte represented in
    ``_uzi_raw_result``.  This function creates a transient dependency copy so
    downstream fetchers can consume fields recovered by Agent OS without
    rewriting the upstream evidence object.
    """

    raw = _uzi_raw_result(snapshot)
    if raw is None:
        return None
    dependency = dict(raw)
    data = dict(raw.get("data") or {}) if isinstance(raw.get("data"), dict) else {}
    facts = snapshot.get("facts") if isinstance(snapshot.get("facts"), dict) else {}
    metadata = {
        "availability",
        "data_gap",
        "message",
        "market_overview",
    }
    for field, value in facts.items():
        if field.startswith("_") or field in metadata:
            continue
        merged, changed = _merge_missing_values(data.get(field), value)
        if changed:
            data[field] = merged
    dependency["data"] = data
    return dependency


def _dimension_observations(snapshot: dict[str, Any]) -> dict[str, Any]:
    raw = _uzi_raw_result(snapshot) or {}
    observations = (
        dict(raw.get("data") or {}) if isinstance(raw.get("data"), dict) else {}
    )
    facts = snapshot.get("facts") if isinstance(snapshot.get("facts"), dict) else {}
    for field, value in facts.items():
        if field.startswith("_") or field in {"availability", "data_gap", "message"}:
            continue
        merged, changed = _merge_missing_values(observations.get(field), value)
        if changed:
            observations[field] = merged
    return observations


def _peer_observation_count(data: dict[str, Any]) -> int:
    rows = data.get("peer_table") or data.get("peer_comparison") or []
    if isinstance(rows, list):
        non_self = [row for row in rows if not isinstance(row, dict) or not row.get("is_self")]
        # Some upstream tables omit is_self.  A table with at least two rows
        # necessarily contains a comparison even when it cannot label self.
        local_count = len(non_self) if any(
            isinstance(row, dict) and row.get("is_self") for row in rows
        ) else max(0, len(rows) - 1)
    else:
        local_count = 0
    global_comparison = data.get("global_peer_comparison")
    global_count = (
        int(global_comparison.get("peer_count") or 0)
        if isinstance(global_comparison, dict)
        else 0
    )
    return max(local_count, global_count)


def _apply_dimension_quality_contract(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Validate native payloads by report usefulness, not dict truthiness."""

    snapshot_type = str(snapshot.get("snapshot_type") or "")
    data = _dimension_observations(snapshot)
    gaps: list[str] = []
    hard_gap = False
    observation_status: str | None = None

    if snapshot_type == "market":
        price = data.get("price") or data.get("latest_price")
        if not _positive_dimension_value(price):
            gaps.append("market.price")
            hard_gap = True
        if _missing_dimension_value(data.get("name")):
            gaps.append("market.name")
        if _missing_dimension_value(data.get("industry")):
            gaps.append("market.industry")
        if not any(
            _positive_dimension_value(data.get(field))
            for field in ("market_cap_raw", "market_cap", "market_cap_yi")
        ):
            gaps.append("market.market_cap")
    elif snapshot_type == "valuation":
        has_pe = any(
            _positive_dimension_value(data.get(field))
            for field in ("pe", "pe_ttm", "pe_dynamic")
        )
        has_pb = _positive_dimension_value(data.get("pb"))
        if not has_pe:
            gaps.append("valuation.pe")
        if not has_pb:
            gaps.append("valuation.pb")
        if not (has_pe or has_pb):
            hard_gap = True
        if not data.get("pe_history"):
            gaps.append("valuation.pe_history")
        if _missing_dimension_value(data.get("pe_quantile")):
            gaps.append("valuation.pe_quantile")
        if _missing_dimension_value(data.get("pb_quantile")):
            gaps.append("valuation.pb_quantile")
    elif snapshot_type == "peers" and _peer_observation_count(data) < 1:
        gaps.append("peers.comparable_company")
        hard_gap = not data
    elif snapshot_type == "materials":
        if not data.get("materials_detail"):
            gaps.append("materials.materials_detail")
        if not data.get("price_history_12m"):
            gaps.append("materials.price_history_12m")
        hard_gap = not data
    elif snapshot_type == "futures":
        no_direct_contract = str(data.get("linked_contract") or "").strip() == "无直接关联品种"
        if not no_direct_contract:
            if not _positive_dimension_value(data.get("latest_price")):
                gaps.append("futures.latest_price")
            if not data.get("price_history_60d"):
                gaps.append("futures.price_history_60d")
            hard_gap = not data
    elif snapshot_type == "governance":
        observation_status = str(data.get("observation_status") or "")
        coverage = data.get("source_coverage")
        structured_complete = observation_status in {
            "verified_empty",
            "verified_non_empty",
        } and isinstance(coverage, dict) and all(
            coverage.get(field) == "available"
            for field in ("pledge", "insider_trades_1y")
        )
        qualitative_available = bool(
            data.get("governance_evidence") or data.get("browser_evidence")
        )
        if not structured_complete and qualitative_available:
            gaps.append("governance.structured_observation")
            observation_status = observation_status or "qualitative_only"
        elif not structured_complete:
            gaps.append("governance.verified_observation")
            observation_status = observation_status or "unverified_empty"
            hard_gap = not data
    elif snapshot_type == "lhb":
        count = data.get("lhb_count_30d")
        records = data.get("lhb_records") or data.get("lhb_statistics") or []
        verified_empty = data.get("observation_status") == "verified_empty"
        if count in (None, 0, "0") and not records and not verified_empty:
            gaps.append("lhb.verified_30d_observation")
            observation_status = "unverified_empty"
            hard_gap = not data
    elif snapshot_type == "contests":
        if not any(
            data.get(field)
            for field in ("xueqiu_cubes", "tgb_mentions", "ths_simu", "dpswang")
        ):
            gaps.append("contests.verified_observation")
            observation_status = "unverified_empty"
            hard_gap = not data

    facts = dict(snapshot.get("facts") or {})
    existing_gaps = [str(item) for item in list(facts.get("data_gap") or []) if item]
    all_gaps = list(dict.fromkeys([*existing_gaps, *gaps]))
    if observation_status:
        facts["observation_status"] = observation_status
    if all_gaps:
        facts["data_gap"] = all_gaps

    current_quality = str(
        snapshot.get("quality_status") or facts.get("availability") or "available"
    ).lower()
    if hard_gap:
        quality = "data_gap"
    elif gaps or all_gaps:
        quality = "degraded"
    elif current_quality in _UNAVAILABLE_QUALITY_STATUSES:
        quality = current_quality
    else:
        quality = "available"

    checked = dict(snapshot)
    checked["facts"] = facts
    checked["quality_status"] = quality
    checked["degraded"] = quality != "available"
    checked["missing_fields"] = list(
        dict.fromkeys([*list(snapshot.get("missing_fields") or []), *all_gaps])
    )
    if quality == "available":
        checked["degrade_reason"] = None
        checked["missing_fields"] = []
    else:
        checked["degrade_reason"] = (
            (";".join(gaps) if gaps else snapshot.get("degrade_reason"))
            or quality
        )
    facts["availability"] = quality
    return checked


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

    return {
        "cache_layer": "provider_raw",
        "domain_id": "finance_ashare",
        "artifact_type": "snapshot",
        "snapshot_type": snapshot_type,
        "provider": _SNAPSHOT_PROVIDER_HINTS.get(snapshot_type, snapshot_type),
        "schema_version": _snapshot_cache_schema_version(snapshot_type),
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
                progress_callback=progress_callback,
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
                normalized_snapshots,
                query=query,
                symbols=symbols,
                profile=selected_profile,
                settings=settings,
            )
            playwright_recovery = recover_playwright_snapshots(
                normalized_snapshots,
                query=query,
                symbols=symbols,
                profile=selected_profile,
                settings=settings,
            )
            qualitative_agent_evidence = collect_qualitative_agent_evidence(
                normalized_snapshots,
                query=query,
                symbols=symbols,
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
