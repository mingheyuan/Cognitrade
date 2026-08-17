from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
import hashlib
import importlib
import os
from pathlib import Path
import sys
import threading
from typing import Any, Callable


UZI_SOURCE_COMMIT = "b004d7a988ca53a4805bf36784af091fa1209f22"
UZI_REPORT_SOURCE_COMMIT = "b004d7a988ca53a4805bf36784af091fa1209f22"
UZI_REPORT_VERSION = "3.9.4"
UZI_REPORT_TEMPLATE_SHA256 = "1984dbf7a5c07dc35e0bd553082c7fe2ab7dffa4c5eb2e23c942fcd4c98ad7c8"
UZI_REPORT_RENDERER_SHA256 = {
    "lib/report/dim_viz.py": "032dd9e0429f8d5e4ffd030dcc33c6360817b4ac81f6983479b4f82adef29304",
    "lib/report/institutional.py": "ca2e0ff2ba3dcc2690834da994e5a292e466b137875f660306be4687506e3fef",
    "lib/report/panel_cards.py": "28dfdb0b9ba226cc92236b0f31e7032c22fe624c2069b140410429923c82e828",
    "lib/report/svg_primitives.py": "2b13c6fdc9bfc1a5182a39c74d75d8c14ce1d52c26b7e88ae32975d1b547f93e",
}
UZI_PURE_PYTHON_TREE_SHA256 = "defc3d44eae6106fa92a3d707eed0268aac74012a4490b8d0f4294cd8b0ef7ce"
UZI_COMMAND_TREE_SHA256 = "e4bedf13fae4dbe429ed1816a235b61a96a179a2b6aa4588421a17d34c2c33e6"
UZI_ANALYST_ASSET_TREE_SHA256 = "8cf78e97cd37ba3f1e6c53decad83ae35dd46b487618aa8f9bb535af55e38350"
UZI_STANDALONE_SKILL_TREE_SHA256 = "8bc93074f001fa97154629dcd33cf5a6dfed653cf79d954fa883570be0a4f1ca"

UZI_FETCHER_IDS: tuple[str, ...] = (
    "0_basic",
    "1_financials",
    "2_kline",
    "3_macro",
    "4_peers",
    "5_chain",
    "6_research",
    "7_industry",
    "8_materials",
    "9_futures",
    "10_valuation",
    "11_governance",
    "12_capital_flow",
    "13_policy",
    "14_moat",
    "15_events",
    "16_lhb",
    "17_sentiment",
    "18_trap",
    "19_contests",
    "6_fund_holders",
    "similar_stocks",
)

UZI_COMMAND_IDS: tuple[str, ...] = (
    "analyze-stock",
    "quick-scan",
    "panel-only",
    "scan-trap",
    "dcf",
    "comps",
    "lbo",
    "segmental-model",
    "initiate",
    "earnings",
    "catalysts",
    "thesis",
    "screen",
    "dd",
    "ic-memo",
    "ai-readiness",
    "earnings-preview",
    "model-update",
    "returns",
    "rebalance",
)


@dataclass(frozen=True)
class _FetcherSpec:
    module: str
    invoke: Callable[[Callable[..., dict[str, Any]], str, str, dict[str, Any], dict[str, Any]], dict[str, Any]]


def _ticker_call(
    main: Callable[..., dict[str, Any]],
    ticker: str,
    _industry: str,
    _dependencies: dict[str, Any],
    _options: dict[str, Any],
) -> dict[str, Any]:
    return main(ticker)


def _industry_call(
    main: Callable[..., dict[str, Any]],
    _ticker: str,
    industry: str,
    _dependencies: dict[str, Any],
    _options: dict[str, Any],
) -> dict[str, Any]:
    return main(industry)


def _materials_call(
    main: Callable[..., dict[str, Any]],
    ticker: str,
    industry: str,
    _dependencies: dict[str, Any],
    _options: dict[str, Any],
) -> dict[str, Any]:
    return main(ticker or industry)


def _futures_call(
    main: Callable[..., dict[str, Any]],
    _ticker: str,
    industry: str,
    dependencies: dict[str, Any],
    _options: dict[str, Any],
) -> dict[str, Any]:
    materials = dependencies.get("8_materials") or {}
    detail = (materials.get("data") or {}).get("materials_detail")
    return main(industry, detail if isinstance(detail, list) else None)


def _fund_holders_call(
    main: Callable[..., dict[str, Any]],
    ticker: str,
    _industry: str,
    _dependencies: dict[str, Any],
    options: dict[str, Any],
) -> dict[str, Any]:
    limit = options.get("limit")
    stats_top_n = options.get("fund_stats_top_n")
    if stats_top_n is None:
        return main(ticker, limit=int(limit) if limit is not None else None)
    # UZI's apply_profile_to_env sets this process variable before collection.
    # Agent OS may run different profiles concurrently, so scope the exact
    # variable to this one native call and restore it under a lock.
    with _FUND_PROFILE_ENV_LOCK:
        previous = os.environ.get("UZI_FUND_STATS_TOP")
        try:
            os.environ["UZI_FUND_STATS_TOP"] = str(int(stats_top_n))
            return main(ticker, limit=int(limit) if limit is not None else None)
        finally:
            if previous is None:
                os.environ.pop("UZI_FUND_STATS_TOP", None)
            else:
                os.environ["UZI_FUND_STATS_TOP"] = previous


def _similar_stocks_call(
    main: Callable[..., dict[str, Any]],
    ticker: str,
    _industry: str,
    _dependencies: dict[str, Any],
    options: dict[str, Any],
) -> dict[str, Any]:
    return main(ticker, top_n=int(options.get("top_n") or 4))


_FETCHERS: dict[str, _FetcherSpec] = {
    "0_basic": _FetcherSpec("fetch_basic", _ticker_call),
    "1_financials": _FetcherSpec("fetch_financials", _ticker_call),
    "2_kline": _FetcherSpec("fetch_kline", _ticker_call),
    "3_macro": _FetcherSpec("fetch_macro", _industry_call),
    "4_peers": _FetcherSpec("fetch_peers", _ticker_call),
    "5_chain": _FetcherSpec("fetch_chain", _ticker_call),
    "6_research": _FetcherSpec("fetch_research", _ticker_call),
    "7_industry": _FetcherSpec("fetch_industry", _industry_call),
    "8_materials": _FetcherSpec("fetch_materials", _materials_call),
    "9_futures": _FetcherSpec("fetch_futures", _futures_call),
    "10_valuation": _FetcherSpec("fetch_valuation", _ticker_call),
    "11_governance": _FetcherSpec("fetch_governance", _ticker_call),
    "12_capital_flow": _FetcherSpec("fetch_capital_flow", _ticker_call),
    "13_policy": _FetcherSpec("fetch_policy", _industry_call),
    "14_moat": _FetcherSpec("fetch_moat", _ticker_call),
    "15_events": _FetcherSpec("fetch_events", _ticker_call),
    "16_lhb": _FetcherSpec("fetch_lhb", _ticker_call),
    "17_sentiment": _FetcherSpec("fetch_sentiment", _ticker_call),
    "18_trap": _FetcherSpec("fetch_trap_signals", _ticker_call),
    "19_contests": _FetcherSpec("fetch_contests", _ticker_call),
    "6_fund_holders": _FetcherSpec("fetch_fund_holders", _fund_holders_call),
    "similar_stocks": _FetcherSpec("fetch_similar_stocks", _similar_stocks_call),
}


_IMPORT_LOCK = threading.Lock()
_CONTEST_POLICY_LOCK = threading.Lock()
_FUND_PROFILE_ENV_LOCK = threading.Lock()
UZI_ENVIRONMENT_LOCK = threading.RLock()
_INITIALIZED_ROOTS: set[Path] = set()


class Uzi392FetcherRuntime:
    """Call the pinned UZI fetchers in process without an external runtime.

    Upstream result dictionaries are returned unchanged. Agent OS projection
    into Snapshot/Artifact envelopes belongs to the calling DomainPack skill.
    """

    def __init__(self, *, workspace_root: Path) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.vendor_root = Path(__file__).resolve().parent
        self.scripts_root = self.vendor_root / "scripts"
        self._initialize_import_boundary()

    def collect(
        self,
        fetcher_id: str,
        *,
        ticker: str,
        industry: str | None = None,
        dependencies: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            spec = _FETCHERS[fetcher_id]
        except KeyError as exc:
            raise ValueError(f"Unsupported UZI 3.9.4 fetcher: {fetcher_id}") from exc
        module = importlib.import_module(spec.module)
        normalized_options = dict(options or {})
        if fetcher_id == "19_contests" and "cognitrade_xueqiu_policy" in normalized_options:
            result = _collect_contests_with_policy(
                module,
                ticker=str(ticker or "").strip(),
                policy=dict(normalized_options["cognitrade_xueqiu_policy"] or {}),
            )
        else:
            result = spec.invoke(
                module.main,
                str(ticker or "").strip(),
                str(industry or "综合").strip() or "综合",
                dict(dependencies or {}),
                normalized_options,
            )
        if not isinstance(result, dict):
            raise TypeError(f"UZI fetcher {fetcher_id} returned {type(result).__name__}, expected dict")
        return result

    def collect_symbol(
        self,
        *,
        ticker: str,
        raw_previous: dict[str, Any] | None = None,
        max_workers: int = 6,
        resume: bool = True,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run the pinned UZI root collection route once for one symbol.

        UZI 3.9.4's root command selects the v3 pipeline by default, forces
        ``collect_raw_data`` when ``UZI_LEGACY=1``, and falls the whole symbol
        back to legacy only when a preflight guard or the pipeline orchestrator
        raises.  A ``BaseFetcher`` error is already a normal ``DimResult`` and
        therefore must not switch lanes.
        """

        normalized_ticker = str(ticker or "").strip()
        if not normalized_ticker:
            raise ValueError("UZI symbol collection requires a ticker")
        normalized_options = dict(options or {})
        contest_policy = dict(
            normalized_options.get("cognitrade_xueqiu_policy") or {}
        )
        route_mode = str(
            normalized_options.get("cognitrade_root_route_mode") or "single"
        ).strip().lower()
        if route_mode not in {"single", "multi"}:
            raise ValueError(f"unsupported UZI root route mode: {route_mode}")
        # UZI's dedicated versus/portfolio runners call run_pipeline directly
        # and therefore do not honor the root CLI's UZI_LEGACY switch.
        force_legacy = route_mode == "single" and os.environ.get("UZI_LEGACY") == "1"

        if force_legacy:
            raw = self._collect_symbol_legacy(
                normalized_ticker,
                max_workers=max_workers,
                resume=resume,
                contest_policy=contest_policy,
            )
            return {
                **raw,
                "_uzi_execution": {
                    "lane": "legacy_forced",
                    "fallback_reason": None,
                },
            }

        previous: dict[str, Any] = {}
        try:
            pipeline_run = importlib.import_module("lib.pipeline.run")
            pipeline_collect = importlib.import_module("lib.pipeline.collect")
            pipeline_run._preflight_guards(normalized_ticker)
            previous = self._pipeline_previous(
                normalized_ticker,
                raw_previous=raw_previous,
                resume=resume,
            )
            with _pipeline_contests_policy_scope(
                pipeline_collect,
                policy=contest_policy,
            ):
                collected = pipeline_collect.collect(
                    normalized_ticker,
                    raw_previous=previous,
                    max_workers=max(int(max_workers), 1),
                )
            if not isinstance(collected, dict):
                raise TypeError(
                    "UZI pipeline collect returned "
                    f"{type(collected).__name__}, expected dict"
                )
            raw = self._assemble_pipeline_raw(normalized_ticker, collected)
            self._write_native_raw(raw)
            return {
                **raw,
                "_uzi_execution": {
                    "lane": "pipeline",
                    "fallback_reason": None,
                },
            }
        except Exception as exc:
            # Keep route diagnostics useful without persisting provider URLs,
            # credentials or other exception text into report artifacts.
            fallback_reason = type(exc).__name__
            if route_mode == "multi":
                # Pinned versus_runner/portfolio_runner do not retry legacy.
                # They only reuse a *complete* report cache (raw + synthesis),
                # not a raw_data file by itself.  This collection adapter cannot
                # prove that completeness, so it surfaces the symbol failure;
                # the multi-symbol composer then skips it exactly as upstream.
                raise
            raw = self._collect_symbol_legacy(
                normalized_ticker,
                max_workers=max_workers,
                resume=resume,
                contest_policy=contest_policy,
            )
            return {
                **raw,
                "_uzi_execution": {
                    "lane": "legacy_fallback",
                    "fallback_reason": fallback_reason,
                },
            }

    def _pipeline_previous(
        self,
        ticker: str,
        *,
        raw_previous: dict[str, Any] | None,
        resume: bool,
    ) -> dict[str, Any]:
        # The v3 pipeline receives resume as a function argument. The
        # process-wide UZI_NO_RESUME switch belongs to the legacy
        # collect_raw_data path; pinned versus/portfolio always resume.
        if not resume:
            return {}
        if isinstance(raw_previous, dict):
            return raw_previous
        cache = importlib.import_module("lib.cache")
        market_router = importlib.import_module("lib.market_router")
        try:
            cache_ticker = market_router.parse_ticker(ticker).full
        except Exception:
            cache_ticker = ticker
        try:
            # Pinned ``lib.pipeline.run._load_cache`` treats a missing or
            # malformed raw_data.json as an empty resume candidate.  A cache
            # parse failure is not a pipeline-orchestrator failure and must
            # therefore never select the legacy lane.
            previous = cache.read_task_output(cache_ticker, "raw_data")
        except Exception:
            previous = {}
        return previous if isinstance(previous, dict) else {}

    def _assemble_pipeline_raw(
        self,
        ticker: str,
        collected: dict[str, Any],
    ) -> dict[str, Any]:
        market_router = importlib.import_module("lib.market_router")
        ticker_info = market_router.parse_ticker(ticker)
        basic = collected.get("0_basic")
        basic_market = basic.get("market") if isinstance(basic, dict) else None
        dimensions = {
            key: value
            for key, value in collected.items()
            if key not in {"fund_managers", "similar_stocks"}
            and isinstance(value, dict)
        }
        raw: dict[str, Any] = {
            "ticker": ticker,
            "market": basic_market
            if basic_market in {"A", "H", "U"}
            else ticker_info.market,
            "code": ticker_info.code,
            "full": ticker_info.full,
            "dimensions": dimensions,
        }
        for key in ("fund_managers", "similar_stocks"):
            if key in collected:
                raw[key] = collected[key]
        return raw

    def _write_native_raw(self, raw: dict[str, Any]) -> None:
        cache = importlib.import_module("lib.cache")
        cache_ticker = str(raw.get("full") or raw.get("ticker") or "").strip()
        if cache_ticker:
            try:
                # Upstream's pipeline cache write is best-effort and must not
                # turn a successful pipeline collection into a legacy retry.
                cache.write_task_output(cache_ticker, "raw_data", raw)
            except Exception:
                pass

    def _collect_symbol_legacy(
        self,
        ticker: str,
        *,
        max_workers: int,
        resume: bool,
        contest_policy: dict[str, Any],
    ) -> dict[str, Any]:
        run_real_test = importlib.import_module("run_real_test")
        contests = importlib.import_module("fetch_contests")
        resolved_ticker = self._resolve_legacy_equity_ticker(ticker)
        with _contests_policy_scope(contests, policy=contest_policy):
            raw = run_real_test.collect_raw_data(
                resolved_ticker,
                max_workers=max(int(max_workers), 1),
                resume=resume,
            )
        if not isinstance(raw, dict):
            raise TypeError(
                "UZI legacy collect_raw_data returned "
                f"{type(raw).__name__}, expected dict"
            )
        return raw

    def _resolve_legacy_equity_ticker(self, ticker: str) -> str:
        """Preserve stage1's target semantics without embedding its CLI/report work.

        Agent OS owns interactive candidate confirmation and fund routing, but a
        preflight fallback must never continue to score an ETF/LOF/fund/bond as
        an individual company.  Name resolution and security classification are
        the pinned stage1 primitives; only the CLI prompts and report side
        effects remain outside this collection adapter.
        """

        market_router = importlib.import_module("lib.market_router")
        if market_router.is_chinese_name(ticker):
            data_sources = importlib.import_module("lib.data_sources")
            resolved = data_sources.resolve_chinese_name_rich(ticker)
            ticker_info = resolved.get("resolved") if isinstance(resolved, dict) else None
            if ticker_info is None:
                raise ValueError("UZI target name could not be resolved uniquely")
        else:
            ticker_info = market_router.parse_ticker(ticker)

        if ticker_info.market == "A":
            security_type = market_router.classify_security_type(ticker_info.code)
            if security_type in {"etf", "lof", "mutual_fund", "convertible_bond"}:
                raise ValueError(f"UZI target is not an individual stock: {security_type}")
        return ticker_info.full

    def _initialize_import_boundary(self) -> None:
        with _IMPORT_LOCK:
            if self.scripts_root not in _INITIALIZED_ROOTS:
                scripts_path = str(self.scripts_root)
                if scripts_path not in sys.path:
                    sys.path.insert(0, scripts_path)
                _INITIALIZED_ROOTS.add(self.scripts_root)
            cache = importlib.import_module("lib.cache")
            cache.CACHE_ROOT = self.workspace_root / "cache" / "uzi_3_9_2"


def _collect_contests_with_policy(module: Any, *, ticker: str, policy: dict[str, Any]) -> dict[str, Any]:
    """Run the pinned contests fetcher without bypassing Cognitrade's source gate.

    Only the Xueqiu branch is wrapped. The upstream function, public Taoguba /
    10jqka / DPS sources and the returned UZI object shape remain unchanged.
    """

    with _contests_policy_scope(module, policy=policy):
        return module.main(ticker)


def _run_pipeline_contest_job(
    dim_key: str,
    ticker: Any,
    raw_context: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Run only the contest job with its request-local policy in the child."""

    pipeline_collect = importlib.import_module("lib.pipeline.collect")
    contests = importlib.import_module("fetch_contests")
    with _contests_policy_scope(contests, policy=dict(policy or {})):
        return pipeline_collect._run_fetcher_job(dim_key, ticker, raw_context)


@contextmanager
def _pipeline_contests_policy_scope(module: Any, *, policy: dict[str, Any]):
    """Transport the request-local source policy into spawn workers."""

    enabled = bool(policy.get("enabled")) and bool(
        str(policy.get("cookie") or "").strip()
    )
    child_policy = {
        "enabled": enabled,
        "cookie": str(policy.get("cookie") or "").strip() if enabled else None,
    }
    original_runner = module.run_process_jobs

    def controlled_runner(jobs, *, max_workers, overall_timeout):
        scoped_jobs = [
            replace(
                job,
                target=_run_pipeline_contest_job,
                args=(
                    job.args[0],
                    job.args[1],
                    job.args[2] if len(job.args) > 2 else None,
                    child_policy,
                ),
            )
            if job.key == "19_contests"
            else job
            for job in jobs
        ]
        return original_runner(
            scoped_jobs,
            max_workers=max_workers,
            overall_timeout=overall_timeout,
        )

    with _CONTEST_POLICY_LOCK:
        module.run_process_jobs = controlled_runner
        try:
            yield
        finally:
            module.run_process_jobs = original_runner


@contextmanager
def _contests_policy_scope(module: Any, *, policy: dict[str, Any]):
    """Scope Cognitrade's Xueqiu gate around the pinned contests call.

    The policy defaults to disabled.  The rest of ``fetch_contests`` and its
    public sources execute unchanged.
    """

    enabled = bool(policy.get("enabled"))
    cookie = str(policy.get("cookie") or "").strip()
    enabled = enabled and bool(cookie)
    original_fetch = module.fetch_xueqiu_cubes
    original_cached = module.cached

    def controlled_fetch(ticker_info: Any, limit: int = 50):
        if not enabled:
            return [], {
                "http_status": None,
                "source": "cognitrade_policy_disabled",
                "login_required": True,
                "policy_status": "sensitive_source_disabled",
            }
        session = module.requests.Session()
        session.headers.update(
            {
                "User-Agent": module.UA,
                "Referer": "https://xueqiu.com/",
                "Cookie": cookie,
            }
        )
        symbol = module._xq_symbol(ticker_info)
        url = (
            "https://xueqiu.com/query/v1/search/cube/stock.json"
            f"?q={symbol}&count={limit}&page=1"
        )
        meta = {
            "http_status": None,
            "source": "cognitrade_authenticated_http",
            "login_required": False,
            "policy_status": "sensitive_source_enabled",
        }
        try:
            response = session.get(url, timeout=15)
            meta["http_status"] = response.status_code
            if response.status_code == 200:
                payload = response.json()
                cubes = payload.get("list") or payload.get("cubes") or []
                normalized = module._normalize_cubes(cubes)
                if normalized:
                    return normalized, meta
            if response.status_code in (400, 401, 403):
                meta["login_required"] = True
        except Exception as exc:
            meta["http_error_type"] = exc.__class__.__name__
        return [], meta

    def policy_cached(
        cache_ticker: str,
        key: str,
        fetch_fn: Callable[[], Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        if str(key).startswith("xq_cubes__"):
            # Never reuse or persist authenticated Xueqiu observations across
            # policy/cookie states. Public contest-source caches are untouched.
            return fetch_fn()
        return original_cached(cache_ticker, key, fetch_fn, *args, **kwargs)

    with _CONTEST_POLICY_LOCK:
        module.fetch_xueqiu_cubes = controlled_fetch
        module.cached = policy_cached
        try:
            yield
        finally:
            module.fetch_xueqiu_cubes = original_fetch
            module.cached = original_cached


def verify_vendored_uzi_integrity() -> dict[str, Any]:
    root = Path(__file__).resolve().parent
    scripts = root / "scripts"
    command_ids = tuple(sorted(path.stem for path in (root / "commands").glob("*.md")))
    missing_fetchers = [
        fetcher_id
        for fetcher_id, spec in _FETCHERS.items()
        if not (scripts / f"{spec.module}.py").is_file()
    ]
    template = root / "assets" / "report-template.html"
    template_sha256 = hashlib.sha256(template.read_bytes()).hexdigest() if template.is_file() else None
    report_renderer_sha256 = {
        relative_path: (
            hashlib.sha256((scripts / relative_path).read_bytes()).hexdigest()
            if (scripts / relative_path).is_file()
            else None
        )
        for relative_path in UZI_REPORT_RENDERER_SHA256
    }
    report_renderer_matches = report_renderer_sha256 == UZI_REPORT_RENDERER_SHA256
    python_paths = sorted(
        path
        for path in scripts.rglob("*.py")
        if "tests" not in path.parts and path.relative_to(scripts).as_posix() != "__init__.py"
    )
    python_tree = hashlib.sha256()
    for path in python_paths:
        python_tree.update(path.relative_to(scripts).as_posix().encode("utf-8"))
        python_tree.update(b"\0")
        python_tree.update(path.read_bytes())
        python_tree.update(b"\0")
    command_tree = hashlib.sha256()
    for path in sorted((root / "commands").glob("*.md")):
        command_tree.update(path.name.encode("utf-8"))
        command_tree.update(b"\0")
        command_tree.update(path.read_bytes())
        command_tree.update(b"\0")
    python_tree_sha256 = python_tree.hexdigest()
    command_tree_sha256 = command_tree.hexdigest()
    analyst_paths = [
        root / "skill" / "SKILL.md",
        root / "agents" / "investor-panel.md",
        root / "assets" / "data-contracts.md",
        root / "assets" / "disclaimer.md",
        root / "assets" / "quality-checklist.md",
        *(root / "skill" / "references").rglob("*"),
        *(root / "skill" / "personas").rglob("*"),
        *(root / "assets" / "avatars").rglob("*"),
    ]
    analyst_paths = sorted(path for path in analyst_paths if path.is_file())
    analyst_tree = hashlib.sha256()
    for path in analyst_paths:
        analyst_tree.update(path.relative_to(root).as_posix().encode("utf-8"))
        analyst_tree.update(b"\0")
        analyst_tree.update(path.read_bytes())
        analyst_tree.update(b"\0")
    analyst_tree_sha256 = analyst_tree.hexdigest()
    standalone_skill_paths = [*(root / "skills").rglob("*")]
    standalone_skill_paths = sorted(
        path for path in standalone_skill_paths if path.is_file()
    )
    standalone_skill_tree = hashlib.sha256()
    for path in standalone_skill_paths:
        standalone_skill_tree.update(path.relative_to(root).as_posix().encode("utf-8"))
        standalone_skill_tree.update(b"\0")
        standalone_skill_tree.update(path.read_bytes())
        standalone_skill_tree.update(b"\0")
    standalone_skill_tree_sha256 = standalone_skill_tree.hexdigest()
    return {
        "source_commit": UZI_SOURCE_COMMIT,
        "report_source_commit": UZI_REPORT_SOURCE_COMMIT,
        "report_version": UZI_REPORT_VERSION,
        "fetcher_count": len(_FETCHERS),
        "missing_fetchers": missing_fetchers,
        "command_count": len(command_ids),
        "command_ids_match": set(command_ids) == set(UZI_COMMAND_IDS),
        "python_file_count": len(python_paths),
        "python_tree_sha256": python_tree_sha256,
        "python_tree_matches": python_tree_sha256 == UZI_PURE_PYTHON_TREE_SHA256,
        "command_tree_sha256": command_tree_sha256,
        "command_tree_matches": command_tree_sha256 == UZI_COMMAND_TREE_SHA256,
        "report_template_sha256": template_sha256,
        "report_template_matches": template_sha256 == UZI_REPORT_TEMPLATE_SHA256,
        "report_renderer_sha256": report_renderer_sha256,
        "report_renderer_matches": report_renderer_matches,
        "analyst_asset_file_count": len(analyst_paths),
        "analyst_asset_tree_sha256": analyst_tree_sha256,
        "analyst_asset_tree_matches": analyst_tree_sha256 == UZI_ANALYST_ASSET_TREE_SHA256,
        "standalone_skill_file_count": len(standalone_skill_paths),
        "standalone_skill_tree_sha256": standalone_skill_tree_sha256,
        "standalone_skill_tree_matches": (
            standalone_skill_tree_sha256 == UZI_STANDALONE_SKILL_TREE_SHA256
        ),
        "valid": not missing_fetchers
        and set(command_ids) == set(UZI_COMMAND_IDS)
        and python_tree_sha256 == UZI_PURE_PYTHON_TREE_SHA256
        and command_tree_sha256 == UZI_COMMAND_TREE_SHA256
        and template_sha256 == UZI_REPORT_TEMPLATE_SHA256
        and report_renderer_matches
        and analyst_tree_sha256 == UZI_ANALYST_ASSET_TREE_SHA256
        and standalone_skill_tree_sha256 == UZI_STANDALONE_SKILL_TREE_SHA256,
    }


__all__ = [
    "UZI_COMMAND_IDS",
    "UZI_COMMAND_TREE_SHA256",
    "UZI_ANALYST_ASSET_TREE_SHA256",
    "UZI_FETCHER_IDS",
    "UZI_ENVIRONMENT_LOCK",
    "UZI_PURE_PYTHON_TREE_SHA256",
    "UZI_REPORT_RENDERER_SHA256",
    "UZI_REPORT_SOURCE_COMMIT",
    "UZI_REPORT_TEMPLATE_SHA256",
    "UZI_REPORT_VERSION",
    "UZI_SOURCE_COMMIT",
    "UZI_STANDALONE_SKILL_TREE_SHA256",
    "Uzi392FetcherRuntime",
    "verify_vendored_uzi_integrity",
]
