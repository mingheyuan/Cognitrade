"""The Agent SKILL consumes main() envelopes, not BaseFetcher-normalized data."""
import importlib
import os
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from domain_packs.finance_ashare.vendor.uzi_3_9_2 import runtime as host
from domain_packs.finance_ashare.vendor.uzi_3_9_2 import skill_envelope, skill_schedule


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Envelope regressions cannot request business data")
    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)


@pytest.mark.parametrize("parallel", [False, True])
@pytest.mark.parametrize("dim,module_name,args,answer", [
    ("0_basic", "fetch_basic", ("688041.SH",), {
        "ticker": "688041.SH", "market": "A", "source": "recorded-basic",
        "data": {"name": "fixture", "industry": None, "price": 230.89},
        "fallback": True, "provider_extension": {"empty": []},
    }),
    ("8_materials", "fetch_materials", ("688041.SH",), {
        "data": {"materials_detail": [], "summary": "", "value": 0, "hedge": False},
        "source": "recorded-materials", "fallback": True,
    }),
    ("9_futures", "fetch_futures", ("电子", [{"name": "fixture"}]), {
        "data": {"futures_prices": [], "inventory": None},
        "source": "recorded-futures", "fallback": True,
    }),
    ("3_macro", "fetch_macro", ("电子",), {
        "data": {}, "source": "recorded-timeout", "error": "fixture upstream timeout",
        "_timeout": True, "fallback": True,
    }),
])
def test_skill_lane_keeps_the_exact_native_main_envelope(
    tmp_path, monkeypatch, dim, module_name, args, answer, parallel,
):
    runtime = host.Uzi392FetcherRuntime(workspace_root=tmp_path)
    pipeline = importlib.import_module("lib.pipeline.collect")
    pipeline_run = importlib.import_module("lib.pipeline.run")
    module = importlib.import_module(module_name)
    native = importlib.import_module("run_real_test")
    native_context = {
        "0_basic": {"data": {"industry": "电子"}},
        "8_materials": {"data": {"materials_detail": [{"name": "fixture"}]}},
    }
    calls = []

    def main(*received):
        calls.append(received)
        return deepcopy(answer)

    monkeypatch.setattr(module, "main", main)
    monkeypatch.setattr(pipeline_run, "_preflight_guards", lambda ticker: None)
    # Compare to the *actual*, unchanged SKILL run_fetcher leaf first.
    expected = native.run_fetcher(module_name, args)
    calls.clear()
    native_fetch = native.run_fetcher

    def fetch(name, received):
        if name == module_name:
            return native_fetch(name, received)
        if name == "fetch_basic":
            return deepcopy(native_context["0_basic"])
        return {"data": {"recorded": name}, "source": "recorded"}

    monkeypatch.setattr(native, "run_fetcher", fetch)
    for name, field in (("fetch_fund_holders", "fund_managers"), ("fetch_similar_stocks", "similar_stocks")):
        monkeypatch.setattr(importlib.import_module(name), "main", lambda *a, _field=field, **k: {"data": {_field: []}})
    monkeypatch.setenv("UZI_DEPTH", "deep")
    monkeypatch.delenv("UZI_NO_RESUME", raising=False)
    monkeypatch.setattr(host, "_contests_policy_scope", lambda *_a, **_k: nullcontext())
    monkeypatch.setattr(host, "bind_native_counter", lambda *_a, **_k: nullcontext())

    def runner(jobs, *, max_workers, overall_timeout):
        return [pipeline.ProcessOutcome(job.key, value=job.target(*job.args, **job.kwargs)) for job in jobs]

    monkeypatch.setattr(pipeline, "run_process_jobs", runner)
    monkeypatch.setattr(pipeline, "collect", lambda *a, **k: pytest.fail("SKILL must not call the old dependency scheduler"))
    monkeypatch.setattr(runtime, "_collect_symbol_legacy", lambda *a, **k: pytest.fail("No whole-stock fallback"))
    options = {"cognitrade_root_route_mode": "skill"}
    previous = {"dimensions": {key: value for key, value in native_context.items() if key != dim}}
    if parallel:
        with runtime.parallel_symbol_collection_scope(options=options) as scoped:
            actual = runtime.collect_symbol(ticker="688041.SH", raw_previous=previous, options=scoped)
    else:
        actual = runtime.collect_symbol(ticker="688041.SH", raw_previous=previous, options=options)
    assert actual["dimensions"][dim] == expected == answer
    assert calls == [args]
    assert actual["_uzi_execution"]["lane"] == "skill_main_process"
    assert actual["_uzi_execution"]["input_contract"] == skill_envelope.CONTRACT_VERSION


def test_skill_worker_failure_has_top_level_error_and_never_falls_back(tmp_path, monkeypatch):
    runtime = host.Uzi392FetcherRuntime(workspace_root=tmp_path)
    pipeline = importlib.import_module("lib.pipeline.collect")
    monkeypatch.setattr(importlib.import_module("lib.pipeline.run"), "_preflight_guards", lambda t: None)
    monkeypatch.setattr(pipeline, "run_process_jobs", lambda jobs, **kw: [
        pipeline.ProcessOutcome(job.key, error="fetcher timeout > 30s", timed_out=True) for job in jobs
    ])

    monkeypatch.setenv("UZI_DEPTH", "deep")
    monkeypatch.setattr(runtime, "_collect_symbol_legacy", lambda *a, **k: pytest.fail("No whole-stock fallback"))
    actual = runtime.collect_symbol(ticker="688041.SH", resume=False, options={"cognitrade_root_route_mode": "skill"})
    result = actual["dimensions"]["3_macro"]
    assert result["data"] == {}
    assert result["error"] == "fetcher timeout > 30s"
    assert result["_timeout"] is True
    assert result["source"] == "cognitrade:process_timeout"


def test_skill_pipeline_error_is_not_retried_as_a_whole_legacy_stock(tmp_path, monkeypatch):
    runtime = host.Uzi392FetcherRuntime(workspace_root=tmp_path)
    pipeline_run = importlib.import_module("lib.pipeline.run")
    monkeypatch.setattr(pipeline_run, "_preflight_guards", lambda ticker: (_ for _ in ()).throw(RuntimeError("fixture orchestration error")))
    monkeypatch.setattr(runtime, "_collect_symbol_legacy", lambda *a, **k: pytest.fail("No whole-stock fallback"))
    with pytest.raises(RuntimeError, match="fixture orchestration error"):
        runtime.collect_symbol(ticker="688041.SH", resume=False, options={"cognitrade_root_route_mode": "skill"})


def _spawned_skill_probe(dim_key, ticker, call):
    """Patch only the provider leaf in the child, not native run_fetcher."""
    module = importlib.import_module("fetch_macro")
    cache = importlib.import_module("lib.cache")
    before = module.main
    try:
        if dim_key != "3_macro":
            result = {"data": {"industry": "fixture", "fund_managers": [], "similar_stocks": []}}
            return dim_key, result, {}
        module.main = lambda industry: {
            "data": {"industry": industry, "empty": [], "cache_root": str(cache.CACHE_ROOT)},
            "source": "offline-child", "fallback": True, "pid": os.getpid(),
        }
        return skill_schedule.run_source_job(dim_key, ticker, call)
    finally:
        module.main = before


@pytest.mark.parametrize("parallel", [False, True])
def test_skill_contract_reaches_real_spawn_and_retains_cache_binding(tmp_path, monkeypatch, parallel):
    runtime = host.Uzi392FetcherRuntime(workspace_root=tmp_path)
    monkeypatch.setattr(importlib.import_module("lib.pipeline.run"), "_preflight_guards", lambda t: None)
    monkeypatch.setattr(skill_schedule, "run_source_job", _spawned_skill_probe)
    native = importlib.import_module("run_real_test")
    monkeypatch.setattr(native, "FETCHER_MAP", [row for row in native.FETCHER_MAP if row[1] in {"0_basic", "3_macro"}])
    monkeypatch.setenv("UZI_DEPTH", "deep")
    options = {"cognitrade_root_route_mode": "skill"}
    if parallel:
        with runtime.parallel_symbol_collection_scope(options=options) as scoped:
            result = runtime.collect_symbol(ticker="688041.SH", resume=False, options=scoped)
    else:
        result = runtime.collect_symbol(ticker="688041.SH", resume=False, options=options)
    native = result["dimensions"]["3_macro"]
    assert native["source"] == "offline-child" and native["fallback"] is True
    assert native["pid"] != os.getpid()
    assert native["data"]["empty"] == []
    assert native["data"]["industry"] == "fixture"
    assert native["data"]["cache_root"] == str(tmp_path / "cache/uzi_3_9_2")


def test_skill_contest_worker_keeps_disabled_sensitive_source_gate(tmp_path, monkeypatch):
    host.Uzi392FetcherRuntime(workspace_root=tmp_path)
    contests = importlib.import_module("fetch_contests")
    monkeypatch.setattr(contests.requests, "Session", lambda: pytest.fail("Disabled source made HTTP request"))

    def main(ticker):
        rows, meta = contests.fetch_xueqiu_cubes(SimpleNamespace(market="A", code="688041"))
        return {"data": {"xueqiu_cubes": rows, "meta": meta}, "source": "fixture-contests", "fallback": True}

    monkeypatch.setattr(contests, "main", main)
    key, value, spill = host._run_pipeline_contest_job(
        "19_contests", "688041.SH", None, {"enabled": False}, native_skill_inputs=True,
    )
    assert key == "19_contests" and spill == {}
    assert value["source"] == "fixture-contests"
    assert value["data"]["meta"]["policy_status"] == "sensitive_source_disabled"


@pytest.mark.parametrize("enabled", [False, True])
def test_bonus_jobs_keep_failure_and_budgets_without_changing_old_lane(tmp_path, monkeypatch, enabled):
    host.Uzi392FetcherRuntime(workspace_root=tmp_path)
    pipeline = importlib.import_module("lib.pipeline.collect")
    received = []

    def runner(jobs, **kwargs):
        received.extend(jobs)
        return [pipeline.ProcessOutcome(job.key, error="fetcher timeout > 30s", timed_out=True) for job in jobs]

    monkeypatch.setattr(pipeline, "run_process_jobs", runner)
    job = pipeline.ProcessJob("similar_stocks", pipeline._run_fetcher_job, ("similar_stocks", "688041.SH"), timeout_sec=30)
    with skill_envelope.skill_envelope_scope(pipeline, enabled=enabled):
        result = pipeline.run_process_jobs([job], max_workers=1, overall_timeout=30)[0]
    assert result.timed_out is True
    assert received[0].timeout_sec == job.timeout_sec == 30
    if enabled:
        assert received[0].target is skill_envelope.run_skill_fetcher_job
        assert result.error is None
        assert result.value[1]["error"] == "fetcher timeout > 30s"
        assert result.value[2]["similar_stocks"] == []
    else:
        assert received == [job]
        assert result.error == "fetcher timeout > 30s"


@pytest.mark.parametrize("elapsed,remaining", [(11.0, 19.0), (31.0, 0.0)])
def test_deferred_bonus_does_not_reset_original_wave_budget(tmp_path, monkeypatch, elapsed, remaining):
    host.Uzi392FetcherRuntime(workspace_root=tmp_path)
    pipeline = importlib.import_module("lib.pipeline.collect")
    clock = iter([0.0, elapsed, elapsed, elapsed])
    monkeypatch.setattr(skill_envelope, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    seen = []

    def runner(jobs, *, max_workers, overall_timeout):
        seen.append((jobs, max_workers, overall_timeout))
        return [pipeline.ProcessOutcome(job.key, error="recorded failure") for job in jobs]

    monkeypatch.setattr(pipeline, "run_process_jobs", runner)
    basic = pipeline.ProcessJob("0_basic", pipeline._run_fetcher_job, ("0_basic", "input-name"), timeout_sec=12)
    bonus = pipeline.ProcessJob("similar_stocks", pipeline._run_fetcher_job, ("similar_stocks", "input-name"), timeout_sec=17)
    with skill_envelope.skill_envelope_scope(pipeline, enabled=True):
        with skill_envelope.defer_bonus_jobs(enabled=True) as deferred:
            pipeline.run_process_jobs([basic, bonus], max_workers=5, overall_timeout=30)
        assert deferred.remaining_timeout == remaining
        assert [j.key for j in seen[0][0]] == ["0_basic"]
        out = {}
        deferred.complete(pipeline, out, ticker="688041.SH", max_workers=5)
    assert out["similar_stocks"] == []
    if remaining:
        jobs, workers, budget = seen[1]
        assert budget == remaining and workers == 2
        assert jobs[0].args == ("similar_stocks", "688041.SH")
        assert jobs[0].timeout_sec == 17
    else:
        assert len(seen) == 1
        assert out["_uzi_bonus_result__similar_stocks"]["_timeout"] is True
        assert out["_uzi_bonus_result__similar_stocks"]["source"] == "cognitrade:process_timeout"


@pytest.mark.parametrize("key,module_name", [("7_industry", "fetch_industry"), ("12_capital_flow", "fetch_capital_flow")])
def test_native_disabled_v8_error_is_preserved_not_normalized_to_empty(tmp_path, monkeypatch, key, module_name):
    host.Uzi392FetcherRuntime(workspace_root=tmp_path)
    native = importlib.import_module("run_real_test")
    monkeypatch.setattr(native, "_mini_racer_disabled", lambda: True)
    module = importlib.import_module(module_name)
    monkeypatch.setattr(module, "main", lambda *a: pytest.fail("Disabled V8 source called"))
    args_fn = next(fn for name, dim, fn in native.FETCHER_MAP if dim == key)
    context = {"0_basic": {"data": {"industry": "fixture"}}}
    expected = native.run_fetcher(module_name, args_fn("688041.SH", context))
    _, actual, _ = skill_envelope.run_skill_fetcher_job(key, "688041.SH", context)
    assert actual == expected
    assert actual["fallback"] is True and actual["error"]
    assert actual["data"]["_disabled"]


def test_native_safe_valuation_retains_original_empty_fields_and_source(tmp_path, monkeypatch):
    host.Uzi392FetcherRuntime(workspace_root=tmp_path)
    native = importlib.import_module("run_real_test")
    module = importlib.import_module("fetch_valuation")
    monkeypatch.setattr(native, "_mini_racer_disabled", lambda: True)
    expected = {"data": {"pe_history": [], "industry_pe": None}, "source": "fixture-safe-valuation", "fallback": True}
    monkeypatch.setattr(module, "main_safe", lambda *a: deepcopy(expected))
    _, actual, _ = skill_envelope.run_skill_fetcher_job("10_valuation", "688041.SH")
    assert actual == expected


def test_collection_contract_and_original_error_survive_snapshot_adapter(tmp_path):
    from domain_packs.finance_ashare.tools.snapshot_collector import (
        _project_uzi_symbol_snapshot,
    )
    from research_system.market_data.uzi_adapter import UziSnapshotAdapter

    envelope = {"data": {}, "source": "fixture-timeout", "error": "fixture timed out", "_timeout": True, "fallback": True}
    snapshot = _project_uzi_symbol_snapshot(
        query="fixture", requested_symbol="688041.SH", snapshot_type="macro",
        symbol_result={"ticker": "688041.SH", "market": "A", "dimensions": {"3_macro": envelope},
                       "_uzi_execution": {"lane": "skill_main_process", "input_contract": skill_envelope.CONTRACT_VERSION}},
    )
    day = datetime.now(timezone.utc).astimezone().date()
    adapted = UziSnapshotAdapter().adapt(snapshot, symbol="688041.SH", market="cn_equity", exchange_timezone="Asia/Shanghai", as_of_date=day)
    assert adapted.payload["_uzi_raw_result"] == envelope
    assert adapted.payload["_uzi_execution"] == {
        "lane": "skill_main_process", "input_contract": skill_envelope.CONTRACT_VERSION,
    }
    assert "_uzi_execution" not in adapted.payload["raw"]
