"""Real spawned-process cache transport, with no provider/network requests."""

from __future__ import annotations

import importlib
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from domain_packs.finance_ashare.vendor.uzi_3_9_2 import runtime as host


def _cache_probe(dimension, ticker, context=None, *, marker="kept", ttl=37):
    """Picklable child target exercising the unchanged native cache function."""
    cache = importlib.import_module("lib.cache")
    value = {"marker": marker, "context": context}
    first = cache.cached(ticker, f"spawn_{dimension}", lambda: value, ttl=ttl)

    def no_second_fetch():
        raise AssertionError("native cache TTL/hit semantics changed")

    second = cache.cached(ticker, f"spawn_{dimension}", no_second_fetch, ttl=ttl)
    stored = json.loads(cache._cache_path(ticker, f"spawn_{dimension}").read_text())
    return dimension, {"data": {
        "cache_root": str(cache.CACHE_ROOT), "cwd": str(Path.cwd()),
        "pid": os.getpid(), "value": first, "cache_hit": first == second,
        "stored_ttl": stored["_ttl"],
    }}, {}


def _offline_contest_entry(dimension, ticker, context, policy):
    """Exercise the real source-policy wrapper with only in-memory transport."""
    pipeline = importlib.import_module("lib.pipeline.collect")
    contests = importlib.import_module("fetch_contests")
    original_job, original_session = pipeline._run_fetcher_job, contests.requests.Session
    http_calls = []

    class OfflineSession:
        def __init__(self):
            self.headers = {}

        def get(self, _url, *, timeout):
            http_calls.append({"timeout": timeout, "fixture_cookie_preserved": self.headers.get("Cookie") == "fixture-cookie"})
            return SimpleNamespace(status_code=200, json=lambda: {"list": []})

    def native_job(dim_key, symbol, raw_context):
        _, meta = contests.fetch_xueqiu_cubes(
            SimpleNamespace(market="A", code="688041", full="688041.SH")
        )
        return _cache_probe(dim_key, symbol, {"raw_context": raw_context,
            "policy_status": meta["policy_status"], "http_calls": http_calls})

    contests.requests.Session = OfflineSession
    pipeline._run_fetcher_job = native_job
    try:
        # This is a fresh spawned interpreter: host still has the original
        # function here, not the parent's test-only dispatch replacement.
        return host._run_pipeline_contest_job(dimension, ticker, context, policy)
    finally:
        pipeline._run_fetcher_job, contests.requests.Session = original_job, original_session


def _raise_permission_error():
    raise PermissionError("offline injected cache permission denial")


@pytest.fixture
def isolated_runtime(tmp_path, monkeypatch):
    # Only the test process changes cwd; the runtime must never do so. A fresh
    # writable cwd makes the old wrong-directory write observable without
    # changing any real directory's ownership or permissions.
    cwd = tmp_path / "unrelated-cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.delenv("STOCK_NO_CACHE", raising=False)
    runtime = host.Uzi392FetcherRuntime(workspace_root=tmp_path / "workspace")
    monkeypatch.setattr(runtime, "_collect_symbol_legacy", lambda *a, **kw: pytest.fail("offline spawn probe must not fall back to provider collection"))
    pipeline = importlib.import_module("lib.pipeline.collect")
    monkeypatch.setattr(importlib.import_module("lib.pipeline.run"), "_preflight_guards", lambda _: None)
    yield runtime, pipeline, cwd


@pytest.mark.parametrize("parallel", [False, True])
def test_real_spawn_uses_runtime_cache_for_basic_regular_dependent_and_mini(isolated_runtime, monkeypatch, parallel):
    runtime, pipeline, cwd = isolated_runtime
    original_runner = pipeline.run_process_jobs
    received = []
    dimensions = ("0_basic", "6_research", "9_futures", "10_valuation")

    def offline_collect(ticker, *, raw_previous, max_workers):
        assert raw_previous == {} and max_workers == 2
        jobs = [pipeline.ProcessJob(
            dimension, _cache_probe,
            args=(dimension, ticker, {"8_materials": {"data": {"core_material": "fixture"}}} if dimension == "9_futures" else None),
            kwargs={"marker": "original-kwarg", "ttl": 37},
            timeout_sec=10, serial_group="mini_racer" if dimension == "10_valuation" else None,
        ) for dimension in dimensions]
        outcomes = pipeline.run_process_jobs(jobs, max_workers=max_workers, overall_timeout=20)
        received.extend(outcomes)
        assert all(row.error is None for row in outcomes), outcomes
        return {row.key: row.value[1] for row in outcomes}

    monkeypatch.setattr(pipeline, "collect", offline_collect)
    if parallel:
        with runtime.parallel_symbol_collection_scope() as options:
            result = runtime.collect_symbol(ticker="688041.SH", resume=False, max_workers=2, options=options)
    else:
        result = runtime.collect_symbol(ticker="688041.SH", resume=False, max_workers=2)
    assert result["_uzi_execution"]["lane"] == "pipeline"
    assert len(received) == 4
    expected_root = runtime.workspace_root / "cache" / "uzi_3_9_2"
    for row in received:
        data = row.value[1]["data"]
        assert data["cache_root"] == str(expected_root)
        assert data["cwd"] == str(cwd) and data["pid"] != os.getpid()
        assert data["value"]["marker"] == "original-kwarg"
        assert data["cache_hit"] is True and data["stored_ttl"] == 37
        if row.key == "9_futures":
            assert data["value"]["context"]["8_materials"]["data"]["core_material"] == "fixture"
    assert len(list(expected_root.rglob("api_cache/*.json"))) == 4
    assert not (cwd / ".cache").exists()
    assert Path.cwd() == cwd
    assert pipeline.run_process_jobs is original_runner


@pytest.mark.parametrize("enabled", [False, True])
def test_real_spawn_contest_policy_composes_after_cache_transport(isolated_runtime, monkeypatch, enabled):
    runtime, pipeline, cwd = isolated_runtime
    original_runner = pipeline.run_process_jobs
    monkeypatch.setattr(host, "_run_pipeline_contest_job", _offline_contest_entry)

    def offline_collect(ticker, *, raw_previous, max_workers):
        job = pipeline.ProcessJob("19_contests", _cache_probe,
            args=("19_contests", ticker, {"original": "context"}), timeout_sec=10)
        outcome = pipeline.run_process_jobs([job], max_workers=max_workers, overall_timeout=15)[0]
        assert outcome.error is None, outcome
        return {outcome.key: outcome.value[1]}

    monkeypatch.setattr(pipeline, "collect", offline_collect)
    with runtime.parallel_symbol_collection_scope(options={
        "cognitrade_xueqiu_policy": {"enabled": enabled, "cookie": "fixture-cookie"},
    }) as options:
        result = runtime.collect_symbol(ticker="688041.SH", resume=False, options=options)
    data = result["dimensions"]["19_contests"]["data"]
    assert data["cache_root"] == str(runtime.workspace_root / "cache" / "uzi_3_9_2")
    context = data["value"]["context"]
    assert context["raw_context"] == {"original": "context"}
    assert context["policy_status"] == ("sensitive_source_enabled" if enabled else "sensitive_source_disabled")
    assert context["http_calls"] == ([{"timeout": 15, "fixture_cookie_preserved": True}] if enabled else [])
    assert not (cwd / ".cache").exists()
    assert pipeline.run_process_jobs is original_runner


def test_real_spawn_keeps_original_errors_and_hard_timeout(isolated_runtime):
    runtime, pipeline, cwd = isolated_runtime
    jobs = [
        pipeline.ProcessJob("permission", _raise_permission_error, timeout_sec=10),
        pipeline.ProcessJob("slow", time.sleep, args=(10,), timeout_sec=0.05),
    ]
    started = time.monotonic()
    with host._pipeline_process_cache_scope(pipeline, cache_root=runtime.workspace_root / "cache" / "uzi_3_9_2"):
        outcomes = {row.key: row for row in pipeline.run_process_jobs(jobs, max_workers=2, overall_timeout=12)}
    assert "PermissionError" in outcomes["permission"].error
    assert not outcomes["permission"].timed_out
    assert outcomes["slow"].timed_out and "timeout" in outcomes["slow"].error
    assert time.monotonic() - started < 12
    assert not (cwd / ".cache").exists()


def test_cache_wrapper_restores_parent_module_and_does_not_hide_errors(isolated_runtime):
    runtime, _, _ = isolated_runtime
    cache = importlib.import_module("lib.cache")
    previous = cache.CACHE_ROOT
    with pytest.raises(PermissionError, match="offline injected"):
        host._run_pipeline_cache_bound_job(str(runtime.workspace_root / "another-cache"), _raise_permission_error, (), {})
    assert cache.CACHE_ROOT is previous
    with pytest.raises(ValueError, match="must be absolute"):
        host._run_pipeline_cache_bound_job(".cache", _raise_permission_error, (), {})
    assert cache.CACHE_ROOT is previous


def test_scope_preserves_scheduler_parameters_and_restores_after_failure(isolated_runtime):
    runtime, pipeline, _ = isolated_runtime
    received = []

    def scheduler(jobs, **kwargs):
        received.append((jobs, kwargs))
        raise RuntimeError("offline scheduler failure")

    module = SimpleNamespace(run_process_jobs=scheduler)
    job = pipeline.ProcessJob("9_futures", _cache_probe, args=("9_futures", "688041.SH", {"source": "fixed"}),
                              kwargs={"marker": "kept"}, timeout_sec=17, serial_group="fixture-group")
    with pytest.raises(RuntimeError, match="offline scheduler failure"):
        with host._pipeline_process_cache_scope(module, cache_root=runtime.workspace_root / "cache" / "uzi_3_9_2"):
            module.run_process_jobs([job], max_workers=3, overall_timeout=29)
    assert module.run_process_jobs is scheduler
    jobs, kwargs = received[0]
    wrapped = jobs[0]
    assert kwargs == {"max_workers": 3, "overall_timeout": 29}
    assert (wrapped.key, wrapped.timeout_sec, wrapped.serial_group) == (job.key, 17, "fixture-group")
    assert wrapped.args[1:] == (job.target, job.args, job.kwargs)
    assert wrapped.kwargs == {} and job.target is _cache_probe


def test_cache_boundary_does_not_modify_pinned_native_tree():
    assert host.verify_vendored_uzi_integrity()["valid"]
