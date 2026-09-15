"""Compare timing-sensitive source arguments to the actual pinned SKILL entry."""
from contextlib import nullcontext
from copy import deepcopy
from importlib import import_module
from threading import Event

import pytest

from domain_packs.finance_ashare.vendor.uzi_3_9_2.runtime import Uzi392FetcherRuntime


@pytest.mark.parametrize("parallel_scope", [False, True])
@pytest.mark.parametrize("cached_materials", [False, True])
def test_futures_observes_native_available_materials_not_a_host_dependency_wave(
    tmp_path, monkeypatch, parallel_scope, cached_materials,
):
    runtime = Uzi392FetcherRuntime(workspace_root=tmp_path)
    native = import_module("run_real_test")
    pipeline = import_module("lib.pipeline.collect")
    cache = import_module("lib.cache")
    monkeypatch.setenv("UZI_DEPTH", "medium")
    monkeypatch.delenv("UZI_NO_RESUME", raising=False)
    monkeypatch.setattr(import_module("lib.pipeline.run"), "_preflight_guards", lambda _: None)
    materials = [{"name": "铜", "share": 12.0}]
    side = {"value": "reference"}
    started = {key: Event() for key in ("reference", "host")}
    calls = {key: {} for key in started}

    def fetch(module, args):
        current = side["value"]
        calls[current][module] = deepcopy(args)
        if module == "fetch_basic":
            return {"ticker": "688041.SH", "data": {"industry": "半导体", "price": 123}, "source": "recorded"}
        if module == "fetch_materials":
            # Native can launch futures while materials is still in flight.
            # The finite wait also allows the old host wave to finish red.
            started[current].wait(1)
            return {"data": {"materials_detail": materials}, "source": "recorded"}
        if module == "fetch_futures":
            started[current].set()
            return {"data": {"materials_input": args[1]}, "source": "recorded"}
        return {"data": {"recorded": module}, "source": "recorded"}

    monkeypatch.setattr(native, "run_fetcher", fetch)
    for module, field in (("fetch_fund_holders", "fund_managers"), ("fetch_similar_stocks", "similar_stocks")):
        monkeypatch.setattr(import_module(module), "main", lambda *args, _field=field, **kwargs: {"data": {_field: []}})
    previous = {"ticker": "688041.SH", "dimensions": {
        "8_materials": {"data": {"materials_detail": materials}, "source": "recorded"},
    }} if cached_materials else {}
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path / "reference")
    if previous:
        cache.write_task_output("688041.SH", "raw_data", deepcopy(previous))
    expected = native.collect_raw_data("688041.SH", max_workers=2, resume=True)
    side["value"] = "host"
    # This in-process process-double must not reacquire a parent-held policy
    # lock from a native worker thread. Real children have independent locks;
    # source authorization is covered by the dedicated process-boundary tests.
    monkeypatch.setattr("domain_packs.finance_ashare.vendor.uzi_3_9_2.runtime._contests_policy_scope", lambda *_a, **_k: nullcontext())
    monkeypatch.setattr("domain_packs.finance_ashare.vendor.uzi_3_9_2.runtime.bind_native_counter", lambda *_a, **_k: nullcontext())
    monkeypatch.setattr(cache, "CACHE_ROOT", runtime.workspace_root / "cache/uzi_3_9_2")
    if previous:
        cache.write_task_output("688041.SH", "raw_data", deepcopy(previous))
    monkeypatch.setattr(pipeline, "run_process_jobs", lambda jobs, **kwargs: [
        pipeline.ProcessOutcome(job.key, value=job.target(*job.args, **job.kwargs)) for job in jobs])
    options = {"cognitrade_root_route_mode": "skill"}
    if parallel_scope:
        with runtime.parallel_symbol_collection_scope(options=options) as scoped:
            actual = runtime.collect_symbol(ticker="688041.SH", max_workers=2, options=scoped)
    else:
        actual = runtime.collect_symbol(ticker="688041.SH", max_workers=2, options=options)
    assert calls["reference"]["fetch_futures"] == ("半导体", materials if cached_materials else None)
    assert calls["host"] == calls["reference"]
    assert actual["dimensions"]["9_futures"] == expected["dimensions"]["9_futures"]
    assert actual["dimensions"]["8_materials"] == expected["dimensions"]["8_materials"]
