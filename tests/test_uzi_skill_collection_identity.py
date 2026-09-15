"""Exercise the actual native collector's identity, profile and resume choices."""
import importlib
import time
from contextlib import nullcontext
from copy import deepcopy

import pytest

from domain_packs.finance_ashare.vendor.uzi_3_9_2 import runtime as host


def compare_collectors(tmp_path, monkeypatch, *, profile="medium", cache_dims=None,
                       ticker="688041.SH", no_resume=False, alternate_cache=None,
                       cached_market="A", basic_failure=False):
    runtime = host.Uzi392FetcherRuntime(workspace_root=tmp_path)
    native = importlib.import_module("run_real_test")
    cache = importlib.import_module("lib.cache")
    pipeline = importlib.import_module("lib.pipeline.collect")
    monkeypatch.setenv("UZI_DEPTH", profile)
    monkeypatch.setenv("UZI_NO_RESUME", "1" if no_resume else "0")
    monkeypatch.setattr(importlib.import_module("lib.pipeline.run"), "_preflight_guards", lambda _: None)
    calls = []

    def run_fetcher(name, args):
        calls.append((name, deepcopy(args)))
        if name == "fetch_basic":
            return ({"data": {}, "error": "recorded source failure", "source": "recorded"}
                    if basic_failure else {"data": {"ticker": "688041.SH", "industry": "recorded", "price": 123}, "source": "recorded"})
        return {"data": {"fresh": name}, "source": "recorded"}

    monkeypatch.setattr(native, "run_fetcher", run_fetcher)
    for name, field in (("fetch_fund_holders", "fund_managers"), ("fetch_similar_stocks", "similar_stocks")):
        def main(ticker, _field=field, **kwargs):
            calls.append((_field, (ticker, kwargs)))
            return {"data": {_field: []}, "source": "recorded"}
        monkeypatch.setattr(importlib.import_module(name), "main", main)

    def seed(root):
        monkeypatch.setattr(cache, "CACHE_ROOT", root)
        if cache_dims is not None:
            cache.write_task_output(ticker, "raw_data", {"ticker": ticker, "market": cached_market, "dimensions": deepcopy(cache_dims)})
        if alternate_cache is not None:
            cache.write_task_output("688041.SH", "raw_data", {"ticker": "688041.SH", "market": "A", "dimensions": deepcopy(alternate_cache)})

    seed(tmp_path / "reference")
    expected = native.collect_raw_data(ticker, max_workers=2, resume=True)
    expected_calls = sorted(calls, key=lambda item: item[0])
    calls.clear()
    seed(runtime.workspace_root / "cache/uzi_3_9_2")
    monkeypatch.setattr(host, "_contests_policy_scope", lambda *_a, **_k: nullcontext())
    # This double executes source jobs in parent threads, not isolated children.
    # Real cross-process counter transport is tested separately.
    monkeypatch.setattr(host, "bind_native_counter", lambda *_a, **_k: nullcontext())
    monkeypatch.setattr(pipeline, "run_process_jobs", lambda jobs, **_: [
        pipeline.ProcessOutcome(job.key, value=job.target(*job.args, **job.kwargs)) for job in jobs
    ])
    actual = runtime.collect_symbol(ticker=ticker, resume=True, max_workers=2,
                                    options={"cognitrade_root_route_mode": "skill"})
    shape = lambda raw: {k: v for k, v in raw.items() if k != "fetched_at" and not k.startswith("_uzi_")}
    assert shape(actual) == shape(expected)
    assert sorted(calls, key=lambda item: item[0]) == expected_calls
    return actual, calls


@pytest.mark.parametrize("profile", ["lite", "medium", "deep"])
@pytest.mark.parametrize("ticker", ["688041.SH", "688041"])
def test_skill_profile_and_resolved_ticker_match_native(tmp_path, monkeypatch, profile, ticker):
    compare_collectors(tmp_path, monkeypatch, profile=profile, ticker=ticker)


@pytest.mark.parametrize("case", ["basic", "fallback", "error", "timeout", "stale"])
def test_skill_resume_matches_native_without_reusing_realtime_basic(tmp_path, monkeypatch, case):
    dim = "0_basic" if case == "basic" else "1_financials"
    value = {"data": {"old": True}, "source": "recorded-cache", "fetched_at": time.time()}
    if case == "fallback":
        value["fallback"] = True
    elif case == "error":
        value["error"] = "recorded partial source error"
    elif case == "timeout":
        value["_timeout"] = True
    elif case == "stale":
        value["fetched_at"] = 1
    compare_collectors(tmp_path, monkeypatch, cache_dims={dim: value})


def test_skill_no_resume_environment_is_honored(tmp_path, monkeypatch):
    compare_collectors(tmp_path, monkeypatch, no_resume=True, cache_dims={
        "1_financials": {"data": {"old": True}, "fetched_at": time.time()},
    })


def test_skill_original_cache_key_precedes_canonical_alias(tmp_path, monkeypatch):
    compare_collectors(tmp_path, monkeypatch, ticker="688041", cache_dims={
        "1_financials": {"data": {"original": True}},
    }, alternate_cache={"1_financials": {"data": {"canonical": True}, "fetched_at": time.time()}})


def test_skill_cached_market_is_retained_when_basic_source_fails(tmp_path, monkeypatch):
    compare_collectors(tmp_path, monkeypatch, cached_market="H", basic_failure=True,
                       cache_dims={"1_financials": {"data": {"recorded": True}}})
