"""Execute pinned post-fetch code with scoped, observable source boundaries.

The native functions own predicates, queries, parsers and DimResult writes.
Only their imports/source calls are bound per invocation; shared module globals
and frozen inputs are never patched. Search snippets and browser HTML are kept
as source observations, not promoted to verified research-document findings.
"""
from __future__ import annotations

import builtins
import importlib
from collections.abc import Mapping
from copy import copy, deepcopy
from datetime import datetime, timezone
from threading import Lock
from time import monotonic
from types import FunctionType, SimpleNamespace
from uuid import uuid4

from domain_packs.finance_ashare.vendor.uzi_3_9_2.search_budget import (
    native_budget_helper,
    normalize_search_budget,
)
from research_system.adapters.vendors.uzi_browser_adapter import UziBrowserAdapter
from research_system.adapters.vendors.uzi_web_search_adapter import UziWebSearchAdapter

VERSION = "uzi-native-postfetch-v3"
BROWSER_SOURCES = {
    "4_peers": "xueqiu_f10", "8_materials": "em_data", "15_events": "cninfo",
    "17_sentiment": "xueqiu_posts", "3_macro": "stats_gov", "7_industry": "baidu_search",
    "14_moat": "baidu_baike", "13_policy": "csrc", "18_trap": "xiaohongshu",
    "19_contests": "xueqiu_contests",
}


class PostfetchStopped(BaseException):
    """Escape native ``except Exception`` without starting more source calls."""


def _bind(function, *, imports=None, **globals_override):
    namespace = {**function.__globals__, **globals_override}
    if imports:
        original_import = builtins.__import__

        def scoped_import(name, globals=None, locals=None, fromlist=(), level=0):
            if level == 0 and name in imports:
                return imports[name]
            return original_import(name, globals, locals, fromlist, level)

        namespace["__builtins__"] = {**vars(builtins), "__import__": scoped_import}
    bound = FunctionType(function.__code__, namespace, function.__name__, function.__defaults__, function.__closure__)
    bound.__kwdefaults__ = function.__kwdefaults__
    return bound


class _OutsideScope:
    def get(self, *args, **kwargs):
        # Native guards malformed/unavailable dimension data before requesting
        # a source. Use that read boundary for dimensions not authorized by the
        # explicit wave, without duplicating its six local predicate lambdas.
        raise KeyError("dimension outside explicit post-fetch scope")


class _ScopedDimensions(dict):
    def __init__(self, values, allowed):
        super().__init__(values)
        self.allowed = frozenset(allowed)
        self.active = None

    def get(self, key, default=None):
        if key == "0_basic":
            return super().get(key, default)
        self.active = key
        if key not in self.allowed:
            return {"data": _OutsideScope()}
        return super().get(key, default)


class PostfetchSources:
    """Configured sources with the existing per-call ceilings, never installs."""

    def __init__(self, settings):
        self.settings = settings
        self.mx_available = bool(getattr(settings, "mx_api_key", None))
        self.browser_enabled = bool(getattr(settings, "browser_sources_enabled", False))
        self._reset_budget(None)

    def _reset_budget(self, receipt):
        self._budget_lock = Lock()
        self._budget_state = {"used": 0, "skipped": 0}
        self._budget_scope_id = uuid4().hex
        self._budget_scope = "stock_postfetch"
        self._budget_cap = None
        self._budget_reason = None
        if receipt is None:
            return
        try:
            receipt = normalize_search_budget(receipt)
        except (KeyError, TypeError, ValueError):
            # Unknown consumption must not issue a fresh collection allowance.
            # MX, cached results and independently authorized browser sources
            # remain usable; this is not a report/data completeness gate.
            self._budget_cap = 0
            self._budget_reason = "collection_search_budget_invalid"
            if isinstance(receipt, Mapping) and receipt.get("reason_code") in {
                "collection_search_budget_unrecorded", "collection_search_budget_ambiguous",
            }:
                self._budget_reason = receipt["reason_code"]
            return
        self._budget_scope_id, self._budget_scope = receipt["scope_id"], receipt["scope"]
        self._budget_cap = receipt["cap"]
        self._budget_state = {key: receipt[key] for key in ("used", "skipped")}
        self._budget_reason = receipt.get("reason_code")

    def for_stock(self, receipt=None):
        """A new explicit recovery starts fresh; collection continuation is explicit.

        Copy source configuration, never the old mutable counter/lock. In
        particular, do not infer consumption from a resolver-selected snapshot.
        """
        scoped = copy(self)
        scoped._reset_budget(receipt)
        return scoped

    def configure_budget(self, profile_budget):
        with self._budget_lock:
            self._budget_cap = profile_budget if self._budget_cap is None else min(self._budget_cap, profile_budget)

    def search_budget(self):
        with self._budget_lock:
            return {"scope_id": self._budget_scope_id, "scope": self._budget_scope,
                    "cap": self._budget_cap, **self._budget_state,
                    **({"reason_code": self._budget_reason} if self._budget_reason else {})}

    def mx_query(self, query, *, timeout):
        native = importlib.import_module("lib.mx_api")
        deadline = monotonic() + timeout

        def remaining():
            value = deadline - monotonic()
            if value <= 0:
                raise TimeoutError("MX source request deadline exceeded")
            return value

        def post(*args, **kwargs):
            kwargs["timeout"] = min(float(kwargs.get("timeout", 30)), remaining())
            return native.requests.post(*args, **kwargs)

        def sleep(seconds):
            native.time.sleep(min(seconds, remaining()))

        guarded_post = _bind(native._post, requests=SimpleNamespace(post=post) if native.requests else None,
                             time=SimpleNamespace(sleep=sleep))
        client = native.MXClient(api_key=self.settings.mx_api_key)
        # Keep native cache identity/TTL, extraction and retry count. Only the
        # active request/retreat may consume the caller's remaining deadline.
        return _bind(native.MXClient.query, _post=guarded_post)(client, query)

    def search(self, query, *, max_results, timeout, profile_budget):
        # Native autofill calls search(), not search_trusted(). Do not silently
        # add the host adapter's domain query restriction.
        native = importlib.import_module("lib.web_search")
        self.configure_budget(profile_budget)
        def fetch(query, max_results):
            return UziWebSearchAdapter().search(
                query=query, dimension="", max_results=max_results,
                trusted=False, timeout_seconds=timeout,
            )["results"]
        counter = {"_BUDGET_LOCK": self._budget_lock, "_BUDGET_STATE": self._budget_state}
        # Execute the actual native helpers in a request-private namespace.
        # Neither the process environment nor another stock's native globals
        # are changed. Cache hits and native budget-exceeded filtering stay native.
        allows = _bind(native_budget_helper(native._budget_allows), **counter,
                       imports={"os": SimpleNamespace(environ={"UZI_DDG_BUDGET": str(self._budget_cap)})})
        return _bind(native.search, _ddg_search=fetch, _budget_allows=allows,
            _budget_mark_used=_bind(native_budget_helper(native._budget_mark_used), **counter),
            _budget_mark_skipped=_bind(native_budget_helper(native._budget_mark_skipped), **counter),
        )(query, max_results=max_results)

    def browser_html(self, source_id, url, *, timeout, wait_for=None):
        return UziBrowserAdapter(self.settings).fetch_native_html(
            source_id=source_id, url=url, timeout_seconds=timeout, wait_for=wait_for,
        )


def run_native_postfetch(
    raw, *, ticker, dimensions, profile, sources, deadline_at,
    cancellation=None, qualitative=True, browser=True, force_browser=False,
):
    """Return a new raw plus original responses; no database or model access."""
    native = importlib.import_module("lib.pipeline.score_fns")
    playwright = importlib.import_module("lib.playwright_fallback")
    selected = importlib.import_module("lib.analysis_profile").get_profile(profile)
    if isinstance(sources, PostfetchSources):
        sources.configure_budget(selected.ddg_budget)
    result = deepcopy(raw)
    scoped = _ScopedDimensions(result.get("dimensions") or {}, dimensions)
    result["dimensions"] = scoped
    observations, attempts = [], []
    stopped = None
    search_count = 0

    def remaining(ceiling):
        if cancellation is not None and cancellation.is_set():
            raise PostfetchStopped("collection_cancelled")
        seconds = deadline_at - monotonic()
        if seconds <= 0:
            raise PostfetchStopped("research_deadline_exceeded")
        return min(ceiling, seconds)

    def observe(source_id, request, call):
        remaining(30)
        row = {"dimension": scoped.active, "source_id": source_id,
               "request": deepcopy(request), "observed_at": None}
        try:
            value = call()
        except Exception as exc:
            row.update(status="failed", error_type=type(exc).__name__)
            if isinstance(exc, PermissionError) and str(exc) in {
                "browser_sources_disabled", "sensitive_sources_disabled",
                "source_not_allowlisted", "credentials_missing",
            }:
                row["reason"] = str(exc)
            raise
        else:
            row.update(status="returned", response=deepcopy(value))
            return value
        finally:
            row["observed_at"] = datetime.now(timezone.utc).isoformat()
            observations.append(row)

    class Client:
        available = sources.mx_available

        def query(self, query):
            return observe("mx_api", {"query": query}, lambda: sources.mx_query(query, timeout=remaining(30)))

    def search(query, max_results=3):
        nonlocal search_count
        remaining(10)
        if not isinstance(sources, PostfetchSources) and search_count >= selected.ddg_budget:
            observations.append({"dimension": scoped.active, "source_id": "ddgs",
                                 "status": "disabled", "reason": "profile_search_budget_exhausted"})
            return []
        search_count += 1
        return observe("ddgs", {"query": query, "max_results": max_results},
                       lambda: sources.search(query, max_results=max_results, timeout=remaining(10),
                                              profile_budget=selected.ddg_budget))

    before = deepcopy(dict(scoped))
    if qualitative:
        try:
            remaining(30)
            _bind(native._autofill_qualitative_via_mx, imports={
                "lib.mx_api": SimpleNamespace(MXClient=Client),
                "lib.web_search": SimpleNamespace(search=search),
            })(result, ticker)
        except PostfetchStopped as exc:
            stopped = str(exc)
        for key in dimensions:
            current = dict.get(scoped, key)
            if current != before.get(key):
                data = (current or {}).get("data") or {}
                attempts.append({"dimension": key, "stage": "qualitative_recovery",
                                 "status": "available" if data.get("_autofill") else "data_gap"})

    browser_summary = {"enabled": False, "attempted": 0, "succeeded": 0, "failed": 0,
                       "disabled_reason": "browser_sources_disabled"}
    if browser and sources.browser_enabled and not stopped:
        strategies = {}
        browser_attempts = []
        for key, strategy in playwright.DIM_STRATEGIES.items():
            def fetch(url, *, timeout=15, wait_for=None, _key=key):
                scoped.active = _key
                return observe(BROWSER_SOURCES[_key], {"url": url, "wait_for": wait_for},
                               lambda: sources.browser_html(BROWSER_SOURCES[_key], url,
                                   timeout=remaining(timeout), wait_for=wait_for))
            bound_strategy = _bind(strategy, fetch_url=fetch)

            def execute(ticker, raw, _key=key, _strategy=bound_strategy):
                browser_attempts.append(_key)
                return _strategy(ticker, raw)

            strategies[key] = execute
        # Profile and permissions belong to this invocation. No global env
        # changes or force flag leaks into another stock's concurrent work.
        environment = {"UZI_PLAYWRIGHT_ENABLE": "1", "UZI_PLAYWRIGHT_FORCE": "1" if force_browser else "0"}
        scoped_profile = SimpleNamespace(depth=selected.depth, playwright_mode=selected.playwright_mode,
                                         playwright_dims=selected.playwright_dims & frozenset(dimensions))
        before_browser = deepcopy(dict(scoped))
        try:
            remaining(15)
            browser_summary = _bind(playwright.autofill_via_playwright,
                imports={"lib.analysis_profile": SimpleNamespace(get_profile=lambda: scoped_profile)},
                os=SimpleNamespace(environ=environment), DIM_STRATEGIES=strategies,
                # Source adapter only launches installed browsers. In particular,
                # deep must not trigger native's implicit package installation.
                ensure_playwright_installed=lambda auto: True,
            )(result, ticker)
        except PostfetchStopped as exc:
            stopped = str(exc)
        for key in browser_attempts:
            changed = dict.get(scoped, key) != before_browser.get(key)
            attempts.append({"dimension": key, "stage": "playwright_recovery",
                             "status": "available" if changed else "data_gap"})
    result["dimensions"] = dict(scoped)
    return {"version": VERSION, "raw": result, "observations": observations,
            "attempts": attempts, "stopped_reason": stopped, "playwright_recovery": browser_summary}


def recover_snapshot_postfetch(snapshots, *, symbols, profile, settings, cancellation=None,
                               deadline_at=None, qualitative=True, browser=True, sources=None,
                               force_browser=False, active_browser=False, collection_search_budgets=None):
    """Compatibility in-memory entry; DC appends changes under its write fence."""
    from domain_packs.finance_ashare.tools.uzi_data_recovery import _SNAPSHOT_TO_UZI_DIM
    from domain_packs.finance_ashare.vendor.uzi_3_9_2.runtime import (
        Uzi392FetcherRuntime,
    )
    if getattr(settings, "workspace_root", None) is not None:
        Uzi392FetcherRuntime(workspace_root=settings.workspace_root)._initialize_import_boundary()
    source_template = sources if sources is not None else PostfetchSources(settings)
    deadline = deadline_at if deadline_at is not None else monotonic() + 600
    attempts, all_observations, browser_passes = [], [], []
    search_budgets = {}
    stopped = None
    for symbol in dict.fromkeys(symbols):
        selected_sources = source_template
        if isinstance(source_template, PostfetchSources):
            selected_sources = source_template.for_stock((collection_search_budgets or {}).get(symbol))
        rows = {_SNAPSHOT_TO_UZI_DIM[row["snapshot_type"]]: row for row in snapshots
                if row.get("symbol") == symbol and row.get("snapshot_type") in _SNAPSHOT_TO_UZI_DIM}
        envelopes = {}
        for key, row in rows.items():
            facts = row.get("facts") or {}
            envelope = facts.get("_uzi_raw_result")
            envelopes[key] = deepcopy(envelope) if isinstance(envelope, Mapping) else {
                "data": {k: deepcopy(v) for k, v in facts.items()
                         if not k.startswith("_") and k not in {"availability", "data_gap", "message"}},
                "source": "uzi:draft",
            }
        output = run_native_postfetch({"ticker": symbol, "dimensions": envelopes},
            ticker=symbol, dimensions=set(rows) - {"0_basic"}, profile=profile,
            sources=selected_sources, deadline_at=deadline, cancellation=cancellation,
            qualitative=qualitative, browser=browser, force_browser=force_browser)
        if browser:
            browser_passes.append({"symbol": symbol, "phase": "postfetch", "force": force_browser,
                                   **output["playwright_recovery"]})
        # Fixed SKILL HARD-GATE-PLAYWRIGHT-AUTOFILL Step C requires a second
        # active pass before Agent research even after ordinary post-fetch.
        # It remains inside the explicit DC wave, not the frozen report, and
        # uses the same deadline, permissions, profile and requested slot set.
        if active_browser and browser and not output["stopped_reason"]:
            active = run_native_postfetch(output["raw"], ticker=symbol,
                dimensions=set(rows) - {"0_basic"}, profile=profile, sources=selected_sources,
                deadline_at=deadline, cancellation=cancellation,
                qualitative=False, browser=True, force_browser=True)
            browser_passes.append({"symbol": symbol, "phase": "agent_active", "force": True,
                                   **active["playwright_recovery"]})
            output = {**active,
                      "observations": [*output["observations"], *active["observations"]],
                      "attempts": [*output["attempts"],
                                   *[{**item, "phase": "agent_active"} for item in active["attempts"]]]}
        budget = selected_sources.search_budget() if isinstance(selected_sources, PostfetchSources) else None
        if budget is not None:
            search_budgets[symbol] = budget
        for key, row in rows.items():
            updated = output["raw"]["dimensions"].get(key)
            observations = [item for item in output["observations"] if item["dimension"] == key]
            if updated == envelopes[key] and not observations and budget is None:
                continue
            facts = row.setdefault("facts", {})
            facts.setdefault("_uzi_collector_original_result", deepcopy(envelopes[key]))
            if updated != envelopes[key]:
                # The old envelope survives separately; this is native's actual
                # new result, not a host merge of selected display fields.
                facts.update(deepcopy(updated.get("data") or {}))
                facts["_uzi_raw_result"] = deepcopy(updated)
                facts["availability"] = "degraded"
                row.update(fallback_applied=True, degraded=True, quality_status="degraded")
            previous_observations = (facts.get("_uzi_postfetch") or {}).get("observations") or []
            facts["_uzi_postfetch"] = {"version": VERSION, "observations": [*previous_observations, *observations],
                                      **({"search_budget": deepcopy(budget)} if budget is not None else {})}
            row["source_trace"] = [*(row.get("source_trace") or []), *[
                {k: item[k] for k in ("source_id", "status", "error_type", "reason") if k in item}
                for item in observations
            ]]
        attempts.extend({**item, "symbol": symbol} for item in output["attempts"])
        all_observations.extend({**item, "symbol": symbol} for item in output["observations"])
        if output["stopped_reason"]:
            stopped = output["stopped_reason"]
            break
    succeeded = sum(item["status"] == "available" for item in attempts)
    return {"schema_version": VERSION, "profile": profile, "enabled": qualitative or profile != "lite",
            "order": ["mx_api", "ddgs"] if qualitative and not browser else ["mx_api", "ddgs", "playwright"],
            "attempted": len(attempts), "succeeded": succeeded, "failed": len(attempts) - succeeded,
            "attempts": attempts, "observation_count": len(all_observations),
            "browser_passes": browser_passes,
            "search_budgets": search_budgets,
            "status": "cancelled" if stopped == "collection_cancelled" else "partial" if stopped or len(attempts) != succeeded else "completed",
            "reason_code": stopped}
