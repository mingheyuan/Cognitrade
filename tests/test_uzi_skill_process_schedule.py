"""Pinned orchestration, isolated source boundaries and shared budgets."""
from copy import deepcopy
from importlib import import_module
from threading import Event
from time import monotonic

import pytest

from domain_packs.finance_ashare.vendor.uzi_3_9_2.runtime import Uzi392FetcherRuntime
from domain_packs.finance_ashare.vendor.uzi_3_9_2.skill_schedule import collect


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Scheduler tests cannot request business data")
    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("profile", ["lite", "medium", "deep"])
def test_same_native_schedule_and_raw(tmp_path, monkeypatch, cached, profile):
    Uzi392FetcherRuntime(workspace_root=tmp_path)
    native, cache = import_module("run_real_test"), import_module("lib.cache")
    monkeypatch.setenv("UZI_DEPTH", profile)
    monkeypatch.delenv("UZI_NO_RESUME", raising=False)
    side = ["reference"]
    observations = {key: {} for key in ("reference", "candidate")}
    futures_started = {key: Event() for key in observations}
    materials = [{"name": "铜", "share": 12.0}]
    previous = {"ticker": "688041.SH", "market": "A", "dimensions": {
        "8_materials": {"data": {"materials_detail": materials}, "source": "recorded"},
    }} if cached else {}

    def source(module, args):
        current = side[0]
        observations[current][module] = deepcopy(args)
        if module == "fetch_basic":
            return {"ticker": "688041.SH", "market": "A", "data": {"industry": "半导体", "price": 123}, "source": "recorded"}
        if module == "fetch_materials":
            assert futures_started[current].wait(3)
            return {"data": {"materials_detail": materials}, "source": "recorded"}
        if module == "fetch_futures":
            futures_started[current].set()
            return {"data": {"material_argument": deepcopy(args[1])}, "source": "recorded"}
        return {"data": {"recorded": module}, "source": "recorded"}

    monkeypatch.setattr(native, "run_fetcher", source)
    for module, field in (("fetch_fund_holders", "fund_managers"), ("fetch_similar_stocks", "similar_stocks")):
        def bonus(ticker, _field=field, **kwargs):
            observations[side[0]][_field] = (ticker, kwargs)
            current_raw = cache.read_task_output(ticker, "raw_data")
            return {"data": {_field: sorted(current_raw["dimensions"])}, "source": "recorded"}
        monkeypatch.setattr(import_module(module), "main", bonus)
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path / "reference")
    if previous:
        cache.write_task_output("688041.SH", "raw_data", deepcopy(previous))
    expected = native.collect_raw_data("688041.SH", max_workers=2, resume=True)
    side[0] = "candidate"
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path / "candidate")
    identities = (native.collect_raw_data, native.run_fetcher, native.ThreadPoolExecutor, cache.read_task_output)
    deadlines = []

    def call_source(module, args, kwargs, deadline):
        deadlines.append(deadline)
        assert 0 < deadline - monotonic() <= 300
        if module in {"fetch_fund_holders", "fetch_similar_stocks"}:
            return import_module(module).main(*args, **kwargs)
        assert not kwargs
        return native.run_fetcher(module, args)

    actual = collect("688041.SH", previous=previous, max_workers=2, call_source=call_source)
    strip = lambda raw: {key: value for key, value in raw.items() if key != "fetched_at" and not key.startswith("_uzi_")}
    assert strip(actual) == strip(expected)
    assert observations["reference"] == observations["candidate"]
    assert identities == (native.collect_raw_data, native.run_fetcher, native.ThreadPoolExecutor, cache.read_task_output)
    if profile != "lite":
        assert observations["candidate"]["fetch_futures"] == ("半导体", materials if cached else None)
    assert set(actual["_uzi_bonus_results"]) == {"fund_managers", "similar_stocks"}


def test_parallel_calls_do_not_share_source_context(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from contextvars import ContextVar
    from threading import Barrier

    Uzi392FetcherRuntime(workspace_root=tmp_path)
    native = import_module("run_real_test")
    monkeypatch.setenv("UZI_DEPTH", "lite")
    monkeypatch.delenv("UZI_NO_RESUME", raising=False)
    owner = ContextVar("candidate_test_stock")
    barrier = Barrier(2)
    original = dict(vars(native))

    def stock(ticker):
        token = owner.set(ticker)
        def source(module, args, kwargs, deadline):
            assert owner.get() == ticker
            if module == "fetch_basic":
                barrier.wait(timeout=3)
                return {"ticker": ticker, "data": {"industry": "测试", "price": 1}}
            if module in {"fetch_fund_holders", "fetch_similar_stocks"}:
                field = "fund_managers" if module == "fetch_fund_holders" else "similar_stocks"
                return {"data": {field: [ticker]}}
            return {"data": {"stock": ticker}, "source": "recorded"}
        try:
            return collect(ticker, previous={}, max_workers=2, call_source=source)
        finally:
            owner.reset(token)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {ticker: pool.submit(stock, ticker) for ticker in ("688041.SH", "688012.SH")}
        rows = {ticker: future.result(timeout=10) for ticker, future in futures.items()}
    for ticker, raw in rows.items():
        assert raw["ticker"] == ticker
        assert raw["fund_managers"] == raw["similar_stocks"] == [ticker]
        assert all(dim["data"]["stock"] == ticker for key, dim in raw["dimensions"].items() if key != "0_basic")
    assert all(vars(native)[key] is value for key, value in original.items())


def test_bonus_does_not_receive_a_fresh_wave_budget(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from domain_packs.finance_ashare.vendor.uzi_3_9_2 import skill_schedule as candidate

    Uzi392FetcherRuntime(workspace_root=tmp_path)
    monkeypatch.setenv("UZI_DEPTH", "lite")
    monkeypatch.delenv("UZI_NO_RESUME", raising=False)
    now = [0.0]
    monkeypatch.setattr(candidate, "time", SimpleNamespace(monotonic=lambda: now[0]))
    admitted, expired = [], []

    def source(module, args, kwargs, deadline):
        if now[0] >= deadline:
            expired.append(module)
            return {"data": {}, "source": "cognitrade:process_timeout", "error": "overall timeout before start", "_timeout": True}
        admitted.append(module)
        if module == "fetch_basic":
            now[0] = 2.0
            return {"ticker": "688041.SH", "data": {"industry": "测试"}}
        now[0] = 303.0
        return {"data": {"recorded": module}}

    raw = collect("688041.SH", previous={}, max_workers=1, call_source=source)
    assert not {"fetch_fund_holders", "fetch_similar_stocks"} & set(admitted)
    assert {"fetch_fund_holders", "fetch_similar_stocks"} <= set(expired)
    assert raw["fund_managers"] == raw["similar_stocks"] == []
    assert all(row["_timeout"] for row in raw["_uzi_bonus_results"].values())


def recorded_spawn_job(dim_key, ticker, call):
    import os
    cache = import_module("lib.cache")
    return dim_key, {"data": {"args": call[1], "cache_root": str(cache.CACHE_ROOT)},
                     "source": "offline-child", "pid": os.getpid(), "fallback": True}, {}


@pytest.mark.parametrize("parallel", [False, True])
@pytest.mark.parametrize("timeout", [False, True])
def test_real_process_boundary_and_cache(tmp_path, monkeypatch, parallel, timeout):
    import os

    from domain_packs.finance_ashare.vendor.uzi_3_9_2 import runtime as host
    from domain_packs.finance_ashare.vendor.uzi_3_9_2 import skill_schedule as candidate

    runtime = Uzi392FetcherRuntime(workspace_root=tmp_path)
    pipeline = import_module("lib.pipeline.collect")
    monkeypatch.setattr(candidate, "run_source_job", recorded_spawn_job)
    overrides = {"3_macro": 0.001} if timeout else {"3_macro": 10}
    options = {"cognitrade_root_route_mode": "skill", "cognitrade_pipeline_fetcher_timeout_overrides": overrides}
    scope = (runtime.parallel_symbol_collection_scope(options=options) if parallel else
             host._pipeline_symbol_collection_scope(pipeline, cache_root=tmp_path / "cache/uzi_3_9_2",
                policy={}, timeout_overrides=overrides, native_skill_inputs=True))
    with scope:
        value = candidate.ProcessSourceBoundary()("fetch_macro", ("fixture",), {}, monotonic() + 15)
    if timeout:
        assert value["_timeout"] and value["source"] == "cognitrade:process_timeout"
        assert value["data"] == {}
    else:
        assert value["pid"] != os.getpid()
        assert value["data"] == {"args": ("fixture",), "cache_root": str(tmp_path / "cache/uzi_3_9_2")}
        assert value["fallback"] is True


@pytest.mark.parametrize("cancel", [False, True])
def test_mini_queue_spends_shared_deadline_without_starting_process(tmp_path, monkeypatch, cancel):
    from domain_packs.finance_ashare.vendor.uzi_3_9_2 import runtime as host
    from domain_packs.finance_ashare.vendor.uzi_3_9_2.skill_schedule import (
        ProcessSourceBoundary,
    )

    Uzi392FetcherRuntime(workspace_root=tmp_path)
    pipeline = import_module("lib.pipeline.collect")
    monkeypatch.setattr(pipeline, "run_process_jobs", lambda *_args, **_kwargs: pytest.fail("Expired/cancelled queue must not launch"))
    cancellation = Event()
    if cancel:
        cancellation.set()
    assert host._PIPELINE_MINI_RACER_LOCK.acquire(blocking=False)
    start = monotonic()
    try:
        value = ProcessSourceBoundary(cancellation=cancellation)("fetch_industry", ("fixture",), {}, start + 0.03)
    finally:
        host._PIPELINE_MINI_RACER_LOCK.release()
    assert monotonic() - start < 1
    assert value["data"] == {}
    assert value["_timeout"] is (not cancel)
    assert ("cancelled" if cancel else "overall timeout") in value["error"]


def test_sensitive_source_policy_still_controls_native_leaf(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace

    from domain_packs.finance_ashare.vendor.uzi_3_9_2 import runtime as host
    from domain_packs.finance_ashare.vendor.uzi_3_9_2.skill_schedule import (
        ProcessSourceBoundary,
    )

    Uzi392FetcherRuntime(workspace_root=tmp_path)
    pipeline, contests = import_module("lib.pipeline.collect"), import_module("fetch_contests")
    monkeypatch.setattr(contests.requests, "Session", lambda: pytest.fail("Disabled sensitive source requested HTTP"))
    def main(ticker):
        rows, metadata = contests.fetch_xueqiu_cubes(SimpleNamespace(market="A", code="688041"))
        return {"data": {"rows": rows, "metadata": metadata}, "source": "offline-native-policy"}
    monkeypatch.setattr(contests, "main", main)
    monkeypatch.setattr(pipeline, "run_process_jobs", lambda jobs, **_kwargs: [
        pipeline.ProcessOutcome(job.key, value=job.target(*job.args, **job.kwargs)) for job in jobs])
    monkeypatch.setattr(host, "bind_native_counter", lambda *_a, **_k: nullcontext())
    with host._pipeline_symbol_collection_scope(pipeline, cache_root=tmp_path / "cache/uzi_3_9_2",
            policy={"enabled": False}, timeout_overrides={}, native_skill_inputs=True):
        value = ProcessSourceBoundary()("fetch_contests", ("688041.SH",), {}, monotonic() + 15)
    assert value["data"]["rows"] == []
    assert value["data"]["metadata"]["policy_status"] == "sensitive_source_disabled"


def blocking_spawn_job(dim_key, ticker, call, *, ready_path, sentinel_path=None, crash=False):
    """Offline child handshake; never starts a provider or external socket."""
    import os
    import time
    from pathlib import Path
    if sentinel_path:
        native = import_module("run_real_test")
        native._MINI_RACER_SENTINEL = Path(sentinel_path)
        native._arm_mini_racer_sentinel("fetch_industry")
    Path(ready_path).write_text(str(os.getpid()))
    if crash:
        os._exit(23)
    time.sleep(15)
    raise AssertionError("Cancelled child should have been terminated")


@pytest.mark.parametrize("parallel", [False, True])
@pytest.mark.parametrize("mini", [False, True])
def test_active_source_cancellation_waits_for_child_exit(tmp_path, monkeypatch, parallel, mini):
    import os
    import time
    from functools import partial
    from threading import Thread

    from domain_packs.finance_ashare.vendor.uzi_3_9_2 import runtime as host
    from domain_packs.finance_ashare.vendor.uzi_3_9_2 import skill_schedule as schedule

    runtime = Uzi392FetcherRuntime(workspace_root=tmp_path)
    pipeline = import_module("lib.pipeline.collect")
    ready = tmp_path / "child-started"
    sentinel = tmp_path / "v8-sentinel"
    monkeypatch.setattr(import_module("run_real_test"), "_MINI_RACER_SENTINEL", sentinel)
    monkeypatch.setattr(schedule, "run_source_job", partial(blocking_spawn_job, ready_path=str(ready),
                                                           sentinel_path=str(sentinel) if mini else None))
    cancellation = Event()
    cancelled_at = []

    def cancel_after_start():
        deadline = monotonic() + 10
        while not ready.exists() and monotonic() < deadline:
            time.sleep(0.02)
        if ready.exists():
            cancelled_at.append(monotonic())
        cancellation.set()

    options = {"cognitrade_root_route_mode": "skill"}
    scope = (runtime.parallel_symbol_collection_scope(options=options, cancellation=cancellation) if parallel else
             host._pipeline_symbol_collection_scope(pipeline, cache_root=tmp_path / "cache/uzi_3_9_2",
                policy={}, timeout_overrides={}, native_skill_inputs=True, cancellation=cancellation))
    worker = Thread(target=cancel_after_start)
    worker.start()
    try:
        with scope:
            result = schedule.ProcessSourceBoundary(cancellation=cancellation)("fetch_industry" if mini else "fetch_macro", ("fixture",), {}, monotonic() + 12)
    finally:
        cancellation.set()
        worker.join(timeout=10)
    assert cancelled_at and monotonic() - cancelled_at[0] < 3
    assert not worker.is_alive()
    assert result["error"] == "cancelled during source execution"
    assert result["_timeout"] is False
    with pytest.raises(ProcessLookupError):
        os.kill(int(ready.read_text()), 0)
    assert not sentinel.exists(), "Only the intentionally killed worker's owned marker can be recovered"


def test_crashed_mini_child_keeps_native_sentinel(tmp_path, monkeypatch):
    from functools import partial

    from domain_packs.finance_ashare.vendor.uzi_3_9_2 import skill_schedule as schedule

    runtime = Uzi392FetcherRuntime(workspace_root=tmp_path)
    sentinel = tmp_path / "crash-sentinel"
    monkeypatch.setattr(import_module("run_real_test"), "_MINI_RACER_SENTINEL", sentinel)
    monkeypatch.setattr(schedule, "run_source_job", partial(blocking_spawn_job,
        ready_path=str(tmp_path / "crashed-child"), sentinel_path=str(sentinel), crash=True))
    cancellation = Event()
    with runtime.parallel_symbol_collection_scope(options={"cognitrade_root_route_mode": "skill"}, cancellation=cancellation):
        result = schedule.ProcessSourceBoundary(cancellation=cancellation)("fetch_industry", ("fixture",), {}, monotonic() + 12)
    assert "worker pipe failed" in result["error"]
    assert not result["_timeout"]
    assert "cognitrade-timeout-owner=" in sentinel.read_text()


@pytest.mark.parametrize("parallel", [False, True])
def test_full_skill_entry_propagates_cancellation_without_new_sources(tmp_path, monkeypatch, parallel):
    runtime = Uzi392FetcherRuntime(workspace_root=tmp_path)
    pipeline = import_module("lib.pipeline.collect")
    monkeypatch.setattr(import_module("lib.pipeline.run"), "_preflight_guards", lambda _: None)
    monkeypatch.setattr(pipeline, "run_process_jobs", lambda *_a, **_k: pytest.fail("Cancelled task launched a source"))
    cancellation = Event()
    cancellation.set()
    options = {"cognitrade_root_route_mode": "skill"}
    if parallel:
        with runtime.parallel_symbol_collection_scope(options=options, cancellation=cancellation) as scoped:
            raw = runtime.collect_symbol(ticker="688041.SH", resume=False, options=scoped)
    else:
        raw = runtime.collect_symbol(ticker="688041.SH", resume=False, options=options, cancellation=cancellation)
    values = [*raw["dimensions"].values(), *raw["_uzi_bonus_results"].values()]
    assert values and all("cancelled before" in value["error"] for value in values)
    assert all(not value["_timeout"] and value["data"] == {} for value in values)
