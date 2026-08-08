from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib
from pathlib import Path
import sys
import threading
from typing import Any, Callable


UZI_SOURCE_COMMIT = "22e65f2e0cee2cb41cb40fdbe391c9600b7fe359"
UZI_REPORT_TEMPLATE_SHA256 = "f3baeb8b1bc769b2bfe78a89fa89b7625607e44814388ecf0db5e3b97091617f"
UZI_PURE_PYTHON_TREE_SHA256 = "1e72e08a910724ac7d927422cbdff4ac173b0a15bdf70e5a68c9eae02b35ccf3"
UZI_COMMAND_TREE_SHA256 = "18a54c151139e8cfe50ffa093035bf90ebbf3bd6327248adc5b5bfdbda7ccc61"
UZI_ANALYST_ASSET_TREE_SHA256 = "876a9e4714ba2894454195f3bc06b9326938bc243a7531848536129addc4da85"

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
    "fund_holders",
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
    return main(ticker, limit=int(limit) if limit is not None else None)


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
    "fund_holders": _FetcherSpec("fetch_fund_holders", _fund_holders_call),
    "similar_stocks": _FetcherSpec("fetch_similar_stocks", _similar_stocks_call),
}


_IMPORT_LOCK = threading.Lock()
_CONTEST_POLICY_LOCK = threading.Lock()
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
            raise ValueError(f"Unsupported UZI 3.9.2 fetcher: {fetcher_id}") from exc
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
            return module.main(ticker)
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
    return {
        "source_commit": UZI_SOURCE_COMMIT,
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
        "analyst_asset_file_count": len(analyst_paths),
        "analyst_asset_tree_sha256": analyst_tree_sha256,
        "analyst_asset_tree_matches": analyst_tree_sha256 == UZI_ANALYST_ASSET_TREE_SHA256,
        "valid": not missing_fetchers
        and set(command_ids) == set(UZI_COMMAND_IDS)
        and python_tree_sha256 == UZI_PURE_PYTHON_TREE_SHA256
        and command_tree_sha256 == UZI_COMMAND_TREE_SHA256
        and template_sha256 == UZI_REPORT_TEMPLATE_SHA256
        and analyst_tree_sha256 == UZI_ANALYST_ASSET_TREE_SHA256,
    }


__all__ = [
    "UZI_COMMAND_IDS",
    "UZI_COMMAND_TREE_SHA256",
    "UZI_ANALYST_ASSET_TREE_SHA256",
    "UZI_FETCHER_IDS",
    "UZI_PURE_PYTHON_TREE_SHA256",
    "UZI_REPORT_TEMPLATE_SHA256",
    "UZI_SOURCE_COMMIT",
    "Uzi392FetcherRuntime",
    "verify_vendored_uzi_integrity",
]
