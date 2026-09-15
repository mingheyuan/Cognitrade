"""Same recorded leaf returns through the actual native SKILL and host entry."""
import importlib
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from copy import deepcopy
from functools import partial

import pytest

from domain_packs.finance_ashare.tools.snapshot_collector import (
    _project_uzi_symbol_snapshot,
)
from domain_packs.finance_ashare.vendor.uzi_3_9_2 import runtime as host
from domain_packs.finance_ashare.vendor.uzi_3_9_2 import skill_envelope, skill_schedule


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Bonus regressions cannot request business data")
    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setenv("UZI_DEPTH", "deep")
    monkeypatch.setattr(host, "_contests_policy_scope", lambda *_a, **_k: nullcontext())
    # Only parent-executed process doubles see this patch; actual spawned
    # children import the production counter binding unmodified.
    monkeypatch.setattr(host, "bind_native_counter", lambda *_a, **_k: nullcontext())


@pytest.mark.parametrize("value", [[], None, [{"name": "recorded", "empty": "", "zero": 0}]])
def test_skill_bonus_returns_match_native_without_extra_dimension(tmp_path, monkeypatch, value):
    _compare(tmp_path, monkeypatch, value=value, failed=False)


def test_skill_bonus_source_failure_keeps_native_empty_and_diagnostic(tmp_path, monkeypatch):
    _compare(tmp_path, monkeypatch, value=None, failed=True)


@pytest.mark.parametrize("parallel", [False, True])
def test_bonus_starts_after_core_flush_like_native(tmp_path, monkeypatch, parallel):
    runtime = host.Uzi392FetcherRuntime(workspace_root=tmp_path)
    native = importlib.import_module("run_real_test")
    pipeline = importlib.import_module("lib.pipeline.collect")
    cache = importlib.import_module("lib.cache")
    monkeypatch.setattr(importlib.import_module("lib.pipeline.run"), "_preflight_guards", lambda _: None)
    monkeypatch.setattr(native, "run_fetcher", lambda name, args: {
        "source": name, "data": {"ticker": "688041.SH", "industry": "fixture"},
    })

    def bonus(field):
        def main(ticker, **_):
            raw = cache.read_task_output(ticker, "raw_data") or {}
            return {"data": {field: sorted((raw.get("dimensions") or {}).keys())}, "source": "recorded-cache-observer"}
        return main

    monkeypatch.setattr(importlib.import_module("fetch_fund_holders"), "main", bonus("fund_managers"))
    monkeypatch.setattr(importlib.import_module("fetch_similar_stocks"), "main", bonus("similar_stocks"))
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path / "reference")
    expected = native.collect_raw_data("688041.SH", max_workers=2, resume=False)
    # An independent empty cache prevents the reference flush masking the bug.
    monkeypatch.setattr(cache, "CACHE_ROOT", runtime.workspace_root / "cache/uzi_3_9_2")
    monkeypatch.setattr(pipeline, "run_process_jobs", lambda jobs, **_: [
        pipeline.ProcessOutcome(job.key, value=job.target(*job.args, **job.kwargs)) for job in jobs
    ])
    options = {"cognitrade_root_route_mode": "skill"}
    if parallel:
        with runtime.parallel_symbol_collection_scope(options=options) as scoped:
            actual = runtime.collect_symbol(ticker="688041.SH", resume=False, options=scoped)
    else:
        actual = runtime.collect_symbol(ticker="688041.SH", resume=False, options=options)
    for field in ("fund_managers", "similar_stocks"):
        assert len(expected[field]) == 20
        assert actual[field] == expected[field]


def _compare(tmp_path, monkeypatch, *, value, failed):
    runtime = host.Uzi392FetcherRuntime(workspace_root=tmp_path)
    native = importlib.import_module("run_real_test")
    pipeline = importlib.import_module("lib.pipeline.collect")
    cache = importlib.import_module("lib.cache")
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path / "native-cache")
    monkeypatch.setattr(importlib.import_module("lib.pipeline.run"), "_preflight_guards", lambda _: None)
    monkeypatch.setattr(native, "run_fetcher", lambda name, args: {
        "source": name, "data": {"ticker": "688041.SH", "industry": "recorded"} if name == "fetch_basic" else {"recorded": True},
    })
    answers = {
        key: ({"data": {}, "source": "recorded-source", "error": "recorded-unavailable", "_timeout": True, "fallback": True}
              if failed else {"data": {key: deepcopy(value), "optional": ""}, "source": "recorded-source", "fallback": True})
        for key in ("fund_managers", "similar_stocks")
    }
    calls = []
    for name, key in [("fetch_fund_holders", "fund_managers"), ("fetch_similar_stocks", "similar_stocks")]:
        def main(*args, _key=key, **kwargs):
            calls.append((_key, args, kwargs))
            return deepcopy(answers[_key])
        monkeypatch.setattr(importlib.import_module(name), "main", main)
    expected = native.collect_raw_data("688041.SH", max_workers=2, resume=False)
    expected_calls = sorted(calls)
    calls.clear()

    def runner(jobs, *, max_workers, overall_timeout):
        outcomes = []
        for job in jobs:
            try:
                outcomes.append(pipeline.ProcessOutcome(job.key, value=job.target(*job.args, **job.kwargs)))
            except Exception as exc:  # noqa: BLE001 — simulate the real process-outcome boundary
                outcomes.append(pipeline.ProcessOutcome(job.key, error=str(exc)))
        return outcomes
    monkeypatch.setattr(pipeline, "run_process_jobs", runner)
    actual = runtime.collect_symbol(ticker="688041.SH", resume=False, options={"cognitrade_root_route_mode": "skill"})
    # Only true collection timestamps/host diagnostic namespaces are excluded.
    shape = lambda raw: {k: v for k, v in raw.items() if k != "fetched_at" and not k.startswith("_uzi_")}
    assert shape(actual) == shape(expected)
    assert "fetched_at" in actual
    assert sorted(calls) == expected_calls
    assert actual["_uzi_bonus_results"] == answers
    for kind, key in [("fund_holders", "fund_managers"), ("similar_stocks", "similar_stocks")]:
        projected = _project_uzi_symbol_snapshot(query="test", requested_symbol="688041.SH", snapshot_type=kind, symbol_result=actual)
        facts = projected["facts"]
        assert facts["_uzi_raw_result"] == answers[key]
        assert facts["_uzi_top_level_fields"][key]["value"] == ([] if failed else value)
        if failed or value is None:
            assert facts["availability"] == "data_gap"


@pytest.mark.parametrize("dimension,field", [("6_fund_holders", "fund_managers"), ("similar_stocks", "similar_stocks")])
def test_killed_bonus_worker_exposes_gap_without_root_error_dictionary(tmp_path, monkeypatch, dimension, field):
    runtime = host.Uzi392FetcherRuntime(workspace_root=tmp_path)
    pipeline = importlib.import_module("lib.pipeline.collect")
    monkeypatch.setattr(importlib.import_module("lib.pipeline.run"), "_preflight_guards", lambda _: None)
    monkeypatch.setattr(pipeline, "run_process_jobs", lambda jobs, **_: [pipeline.ProcessOutcome(job.key, error="recorded hard timeout", timed_out=True) for job in jobs])
    raw = runtime.collect_symbol(ticker="688041.SH", resume=False, options={"cognitrade_root_route_mode": "skill"})
    assert raw[field] == []
    assert dimension not in raw["dimensions"]
    assert raw["_uzi_bonus_results"][field]["_timeout"] is True
    assert raw["_uzi_bonus_results"][field]["error"] == "recorded hard timeout"


def _spawned_bonus_cache_probe(dim_key, ticker, call, *, failed=False):
    """Only replace the provider leaf; use the real child cache and protocol."""
    field = skill_envelope.BONUS_FIELDS[dim_key]
    module_name = "fetch_fund_holders" if field == "fund_managers" else "fetch_similar_stocks"
    module = importlib.import_module(module_name)
    cache = importlib.import_module("lib.cache")
    original = module.main
    raw = cache.read_task_output(ticker, "raw_data") or {}
    result = {
        "data": {field: [{"ticker": raw.get("ticker"), "core_keys": sorted(raw.get("dimensions", {})),
                          "pid": os.getpid(), "cache_root": str(cache.CACHE_ROOT)}]},
        "source": "offline-bonus-child",
    }
    if failed:
        result = {"data": {}, "source": "offline-bonus-child", "error": "recorded provider failure", "_timeout": True}
    module.main = lambda *args, **kwargs: result
    try:
        return skill_schedule.run_source_job(dim_key, ticker, call)
    finally:
        module.main = original


@pytest.mark.parametrize("parallel", [False, True])
@pytest.mark.parametrize("failed", [False, True])
def test_late_bonus_real_spawn_reads_core_and_keeps_failure(tmp_path, monkeypatch, parallel, failed):
    runtime = host.Uzi392FetcherRuntime(workspace_root=tmp_path)
    pipeline = importlib.import_module("lib.pipeline.collect")
    monkeypatch.setattr(importlib.import_module("lib.pipeline.run"), "_preflight_guards", lambda _: None)
    monkeypatch.setattr(skill_schedule, "run_source_job", partial(_spawned_bonus_cache_probe, failed=failed))
    native = importlib.import_module("run_real_test")
    monkeypatch.setattr(native, "FETCHER_MAP", [row for row in native.FETCHER_MAP if row[1] in {"0_basic", "1_financials"}])
    real_runner = pipeline.run_process_jobs

    def runner(jobs, **kwargs):
        if all(job.key in skill_envelope.BONUS_FIELDS for job in jobs):
            return real_runner(jobs, **kwargs)
        return [pipeline.ProcessOutcome(job.key, value=(job.key, {"data": {"ticker": "688041.SH"}}, {})) for job in jobs]

    monkeypatch.setattr(pipeline, "run_process_jobs", runner)
    options = {"cognitrade_root_route_mode": "skill"}
    if parallel:
        with runtime.parallel_symbol_collection_scope(options=options) as scoped:
            raw = runtime.collect_symbol(ticker="688041.SH", resume=False, options=scoped)
    else:
        raw = runtime.collect_symbol(ticker="688041.SH", resume=False, options=options)
    for field in skill_envelope.BONUS_FIELDS.values():
        if failed:
            assert raw[field] == []
            assert raw["_uzi_bonus_results"][field]["error"] == "recorded provider failure"
            assert raw["_uzi_bonus_results"][field]["_timeout"] is True
        else:
            value = raw[field][0]
            assert value["ticker"] == "688041.SH"
            assert value["core_keys"] == ["0_basic", "1_financials"]
            assert value["pid"] != os.getpid()
            assert value["cache_root"] == str(runtime.workspace_root / "cache/uzi_3_9_2")


def test_parallel_symbols_do_not_share_native_bonus_jobs(tmp_path, monkeypatch):
    runtime = host.Uzi392FetcherRuntime(workspace_root=tmp_path)
    pipeline = importlib.import_module("lib.pipeline.collect")
    monkeypatch.setattr(importlib.import_module("lib.pipeline.run"), "_preflight_guards", lambda _: None)
    barrier = threading.Barrier(2)

    def fetch(name, args):
        if name == "fetch_basic":
            barrier.wait(timeout=5)
        return {"data": {"ticker": args[0], "industry": "fixture"}}

    monkeypatch.setattr(importlib.import_module("run_real_test"), "run_fetcher", fetch)
    monkeypatch.setattr(pipeline, "run_process_jobs", lambda jobs, **_: [
        pipeline.ProcessOutcome(job.key, value=job.target(*job.args, **job.kwargs)) for job in jobs
    ])
    for name, field in (("fetch_fund_holders", "fund_managers"), ("fetch_similar_stocks", "similar_stocks")):
        monkeypatch.setattr(importlib.import_module(name), "main", lambda ticker, _field=field, **_: {"data": {_field: [ticker]}})
    with (
        runtime.parallel_symbol_collection_scope(options={"cognitrade_root_route_mode": "skill"}) as scoped,
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        futures = {ticker: pool.submit(runtime.collect_symbol, ticker=ticker, resume=False, options=scoped)
                   for ticker in ("688041.SH", "688012.SH")}
        results = {ticker: future.result(timeout=10) for ticker, future in futures.items()}
    for ticker, raw in results.items():
        assert raw["fund_managers"] == raw["similar_stocks"] == [ticker]


@pytest.mark.parametrize("route", ["skill", "single", "multi"])
def test_old_bonus_pseudo_dimension_cannot_skip_skill_producer(tmp_path, monkeypatch, route):
    runtime = host.Uzi392FetcherRuntime(workspace_root=tmp_path)
    pipeline = importlib.import_module("lib.pipeline.collect")
    monkeypatch.setattr(importlib.import_module("lib.pipeline.run"), "_preflight_guards", lambda _: None)
    previous = {"dimensions": {
        "0_basic": {"data": {"ticker": "688041.SH"}},
        "6_fund_holders": {"data": {"fund_managers": ["stale"]}},
        "similar_stocks": {"data": {"similar_stocks": ["stale"]}},
        "1_financials": {"data": {"recorded": True}},
    }}
    before = deepcopy(previous)
    received = []

    def core_collect(ticker, *, raw_previous, **_):
        received.append(raw_previous)
        return {"0_basic": {"data": {"ticker": ticker}}}

    monkeypatch.setattr(pipeline, "collect", core_collect)
    # This test checks the selected resume input, not source execution. The
    # actual new scheduler is covered by the independent native comparisons.
    def skill_collect(ticker, *, previous, **kwargs):
        received.append(previous)
        return {"ticker": ticker, "dimensions": {}}
    monkeypatch.setattr(skill_schedule, "collect", skill_collect)
    runtime.collect_symbol(ticker="688041.SH", resume=True, raw_previous=previous,
                           options={"cognitrade_root_route_mode": route})
    assert previous == before
    assert received[0]["dimensions"]["1_financials"] == previous["dimensions"]["1_financials"]
    for key in skill_envelope.BONUS_FIELDS:
        assert (key in received[0]["dimensions"]) is (route != "skill")
