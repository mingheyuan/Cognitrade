"""Execute pinned SKILL orchestration with request-local leaf I/O.

The callback enforces the shared deadline,
source policy and process isolation; this module contains no provider logic.
"""
from __future__ import annotations

import builtins
import importlib
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar, copy_context
from copy import deepcopy
from dataclasses import dataclass, replace
from types import FunctionType, ModuleType
from typing import Any

from .search_budget import StockSearchBudget

_WAVE_DEADLINE = ContextVar("uzi_native_wave_deadline", default=None)
_BONUSES = {"fetch_fund_holders": "fund_managers", "fetch_similar_stocks": "similar_stocks"}


@dataclass(frozen=True)
class CancelledProcessOutcome:
    """ProcessOutcome-compatible cancellation; not a provider timeout/crash."""

    key: str
    value: Any = None
    error: str | None = "cancelled during source execution"
    timed_out: bool = False
    cancelled: bool = True


def run_cancellable_source(jobs, *, max_workers, overall_timeout, cancellation):
    """Run one admitted native leaf; return only after its child has exited.

    Native threads own the scheduling; this is not a second wave scheduler.
    Reuse the pinned spawn entry/termination protocol, adding an event check
    while awaiting the pipe. Never close a lease while the child is still live.
    """
    runner = importlib.import_module("lib.pipeline.process_runner")
    if len(jobs) != 1 or max_workers != 1 or overall_timeout <= 0:
        raise ValueError("cancellable SKILL boundary requires one admitted source and a positive budget")
    job = jobs[0]
    if cancellation.is_set():
        return [CancelledProcessOutcome(job.key, error="cancelled before source start")]
    started = time.monotonic()
    context = runner.mp.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(
        target=runner._process_entry,
        args=(child, job.target, job.args, job.kwargs), name=f"uzi-fetch-{job.key}",
    )
    launched = False
    try:
        process.start()
        launched = True
        child.close()
        job_started = time.monotonic()
        while True:
            if cancellation.is_set():
                return [CancelledProcessOutcome(job.key)]
            now = time.monotonic()
            overall_left = overall_timeout - (now - started)
            job_left = job.timeout_sec - (now - job_started)
            if overall_left <= 0 or job_left <= 0:
                error = "overall timeout" if overall_left <= 0 else f"fetcher timeout > {job.timeout_sec:g}s"
                return [runner.ProcessOutcome(job.key, error=error, timed_out=True)]
            if parent.poll(min(0.05, overall_left, job_left)):
                try:
                    ok, value, error = parent.recv()
                except (EOFError, OSError) as exc:
                    ok, value, error = False, None, f"worker pipe failed: {type(exc).__name__}: {exc}"
                process.join(timeout=1.0)
                return [runner.ProcessOutcome(job.key, value=value if ok else None, error=error)]
    finally:
        if launched:
            runner._terminate(process)
        parent.close()
        child.close()
        if launched and process.is_alive():
            raise RuntimeError("source worker did not exit after termination")


def run_source_job(dim_key, ticker, call):
    """Pickle-safe leaf entry; native arguments were resolved at thread start."""
    module, args, kwargs = call
    if module in _BONUSES:
        result = importlib.import_module(module).main(*args, **kwargs)
    else:
        native = importlib.import_module("run_real_test")
        assert any(name == module and key == dim_key for name, key, _ in native.FETCHER_MAP)
        assert not kwargs
        result = native.run_fetcher(module, args)
    return dim_key, result, {}


class ProcessSourceBoundary:
    """Existing cache/policy runner, with hard deadline-aware mini admission.

    Caller must already own the runtime's symbol/parallel collection scope.
    Non-mini jobs also check the deadline immediately before dispatch. A mini
    group is admitted here (using the same lock), so it is not requeued by the
    older wave wrapper. Owned sentinel recovery remains limited to hard kills.
    """

    def __init__(self, *, cancellation=None):
        self.cancellation = cancellation
        self.module = importlib.import_module("lib.pipeline.collect")
        native = importlib.import_module("run_real_test")
        self.keys = {name: key for name, key, _ in native.FETCHER_MAP}
        self.keys.update(fetch_fund_holders="6_fund_holders", fetch_similar_stocks="similar_stocks")
        self.mini = native._MINI_RACER_FETCHERS
        self.search_budget = StockSearchBudget()

    def close(self):
        self.search_budget.close()

    def __call__(self, source, args, kwargs, deadline):
        from domain_packs.finance_ashare.vendor.uzi_3_9_2 import runtime
        from domain_packs.finance_ashare.vendor.uzi_3_9_2.skill_envelope import (
            _retain_worker_failure,
        )

        key = self.keys[source]
        def failed(message, *, timed_out=False):
            return _retain_worker_failure(self.module.ProcessOutcome(key, error=message, timed_out=timed_out)).value[1]

        def cancelled():
            return self.cancellation is not None and self.cancellation.is_set()

        acquired = False
        if source in self.mini:
            while not acquired:
                if cancelled():
                    return failed("cancelled before mini-racer start")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return failed("overall timeout before mini-racer start", timed_out=True)
                acquired = runtime._PIPELINE_MINI_RACER_LOCK.acquire(timeout=min(0.1, remaining))
        try:
            if cancelled():
                return failed("cancelled before source start")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return failed("overall timeout before source start", timed_out=True)
            # Existing runtime constructor retains configured per-fetcher
            # overrides; the original wave budget cannot be renewed by a queue.
            timeout = self.module._positive_env_float("UZI_PIPELINE_FETCHER_TIMEOUT", 120.0)
            job = self.module.ProcessJob(key, run_source_job, (key, args[0] if args else "", (source, args, kwargs)), timeout_sec=timeout)
            owner = None
            if acquired:
                owner = uuid.uuid4().hex
                job = replace(job, target=runtime._run_pipeline_owned_mini_racer_job,
                              args=(job.target, job.args, job.kwargs, owner), kwargs={})
            with self.search_budget.transport():
                outcomes = self.module.run_process_jobs([job], max_workers=1, overall_timeout=remaining)
            assert len(outcomes) == 1 and outcomes[0].key == key
            outcome = outcomes[0]
            if owner and (outcome.timed_out or getattr(outcome, "cancelled", False)):
                runtime._recover_timed_out_mini_racer_sentinel(owner)
            if outcome.error:
                outcome = _retain_worker_failure(outcome)
            return outcome.value[1]
        finally:
            if acquired:
                runtime._PIPELINE_MINI_RACER_LOCK.release()


def collect(ticker, *, previous, max_workers, call_source):
    """Use unchanged native code, including lazy args_fn and progress flushes."""
    native = importlib.import_module("run_real_test")
    cache = importlib.import_module("lib.cache")
    native_fn = native.collect_raw_data
    namespace = dict(native_fn.__globals__)
    bonus_results = {}
    shared_after_basic_deadline = None
    pools_created = 0

    class ScopedPool(ThreadPoolExecutor):
        def __init__(self, *args, **kwargs):
            nonlocal shared_after_basic_deadline, pools_created
            super().__init__(*args, **kwargs)
            pools_created += 1
            now = time.monotonic()
            if shared_after_basic_deadline is None:
                shared_after_basic_deadline = now + 300.0
            # Retain the host's existing shared core/bonus budget; do not
            # allocate another full 180 seconds after an exhausted core wave.
            self.deadline = min(shared_after_basic_deadline, now + (300.0 if pools_created == 1 else 180.0))

        def submit(self, fn, /, *args, **kwargs):
            context = copy_context()
            context.run(_WAVE_DEADLINE.set, self.deadline)
            return super().submit(context.run, fn, *args, **kwargs)

    def invoke(module, args, kwargs=None):
        deadline = _WAVE_DEADLINE.get()
        if deadline is None:  # Basic has the existing hard 120-second budget.
            deadline = time.monotonic() + 120.0
        result = call_source(module, deepcopy(args), dict(kwargs or {}), deadline)
        if module in _BONUSES:
            bonus_results[_BONUSES[module]] = deepcopy(result)
        return result

    # Only this function's import namespace is changed. No shared native
    # module/main, cache reader or global ThreadPoolExecutor is monkeypatched.
    proxies = {}
    cache_proxy = ModuleType(cache.__name__)
    cache_proxy.__dict__.update(vars(cache))
    cache_proxy.read_task_output = lambda *_args, **_kwargs: deepcopy(previous)
    proxies["lib.cache"] = cache_proxy
    for name in _BONUSES:
        proxy = ModuleType(name)
        proxy.__dict__.update(vars(importlib.import_module(name)))
        proxy.main = lambda *args, _name=name, **kwargs: invoke(_name, args, kwargs)
        proxies[name] = proxy

    def scoped_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level == 0 and name in proxies:
            return proxies[name]
        return builtins.__import__(name, globals, locals, fromlist, level)

    namespace.update(run_fetcher=invoke, ThreadPoolExecutor=ScopedPool)
    namespace["__builtins__"] = {**vars(builtins), "__import__": scoped_import}
    bound = FunctionType(native_fn.__code__, namespace, native_fn.__name__, native_fn.__defaults__, native_fn.__closure__)
    bound.__kwdefaults__ = native_fn.__kwdefaults__
    try:
        raw = bound(ticker, max_workers=max_workers, resume=True)
        if isinstance(call_source, ProcessSourceBoundary):
            raw["_uzi_search_budget"] = {
                "scope_id": call_source.search_budget.id,
                "scope": "stock_collection", "cap": os.environ.get("UZI_DDG_BUDGET"),
                **call_source.search_budget.read(),
            }
    finally:
        if isinstance(call_source, ProcessSourceBoundary):
            call_source.close()
    raw["_uzi_bonus_results"] = bonus_results
    return raw
