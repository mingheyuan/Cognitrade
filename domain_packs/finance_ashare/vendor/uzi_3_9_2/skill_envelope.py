"""Preserve the pinned Agent SKILL's fetcher results inside the hard scheduler.

This is a host execution boundary, not another financial data adapter. Native
main results do not pass through BaseFetcher normalization/quality inference.
The existing process budgets, cancellation and source-policy wrappers remain
in force. This alone does not claim equivalence to all of native stage1.
"""
from __future__ import annotations

import importlib
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Any

CONTRACT_VERSION = "native-skill-main-envelope-v5"
BONUS_FIELDS = {"6_fund_holders": "fund_managers", "similar_stocks": "similar_stocks"}
BONUS_RESULT_PREFIX = "_uzi_bonus_result__"


@dataclass
class DeferredBonusJobs:
    """Per-symbol identity/profile plus late jobs and their remaining budget."""

    jobs: list[Any] = field(default_factory=list)
    remaining_timeout: float = 0.0
    resolved_ticker: str | None = None
    enabled_dimensions: frozenset[str] | None = None

    def complete(self, module, out, *, ticker, max_workers):
        if not self.jobs:
            return
        jobs = [replace(job, args=(job.args[0], ticker, *job.args[2:])) for job in self.jobs]
        if self.remaining_timeout <= 0:
            outcomes = [
                _retain_worker_failure(module.ProcessOutcome(
                    job.key, error="overall timeout before bonus start", timed_out=True,
                ))
                for job in jobs
            ]
        else:
            outcomes = module.run_process_jobs(
                jobs, max_workers=min(max_workers, 2),
                # Native bonus-wave ceiling; never grant a new full budget
                # after consuming the wave from which these jobs were moved.
                overall_timeout=min(self.remaining_timeout, 180.0),
            )
        for outcome in outcomes:
            module._apply_outcome(out, outcome)


_DEFERRED_BONUSES: ContextVar[DeferredBonusJobs | None] = ContextVar("uzi_deferred_bonuses", default=None)


@contextmanager
def defer_bonus_jobs(*, enabled):
    state = DeferredBonusJobs() if enabled else None
    if state is not None:
        state.enabled_dimensions = skill_enabled_dimensions()
    token = _DEFERRED_BONUSES.set(state)
    try:
        yield state
    finally:
        _DEFERRED_BONUSES.reset(token)


def skill_enabled_dimensions():
    try:
        return importlib.import_module("lib.analysis_profile").get_profile().fetchers_enabled
    except Exception:  # noqa: BLE001 — same fallback as pinned collect_raw_data
        return None


def load_skill_previous(ticker, *, raw_previous, resume):
    """Native raw-resume selection, separate from provider TTLs and DC PIT."""
    if not resume or os.environ.get("UZI_NO_RESUME") == "1":
        return {}
    if isinstance(raw_previous, dict):
        previous = raw_previous
    else:
        cache = importlib.import_module("lib.cache")
        router = importlib.import_module("lib.market_router")
        previous = cache.read_task_output(ticker, "raw_data")
        if not previous and not router.is_chinese_name(ticker):
            try:
                full = router.parse_ticker(ticker).full
                if full != ticker:
                    previous = cache.read_task_output(full, "raw_data")
            except Exception:  # noqa: BLE001 — pinned alias-cache fallback
                return {}
    if not isinstance(previous, dict) or not isinstance(previous.get("dimensions"), dict):
        return {}
    enabled = skill_enabled_dimensions()
    # 0_basic contains the price and is always fetched by native SKILL.
    # Bonuses are root values, never reusable pipeline pseudo-dimensions.
    return {**previous, "dimensions": {
        key: value for key, value in previous["dimensions"].items()
        if key != "0_basic" and key not in BONUS_FIELDS
        and (enabled is None or key in enabled)
    }}


def _bonus_outcome(dim_key, result):
    field = BONUS_FIELDS[dim_key]
    # Exact SKILL wave-3 extraction: absent -> [], explicit None remains None.
    # Keep the full original return separately, not inside the renderer's list.
    value = (result.get("data") or {}).get(field, [])
    return dim_key, result, {field: value, BONUS_RESULT_PREFIX + field: deepcopy(result)}


def run_skill_fetcher_job(
    dim_key: str, ticker: Any, raw_context: dict | None = None,
) -> tuple[str, dict, dict]:
    """Spawn entry: use the actual SKILL argument map and run_fetcher function."""
    native = importlib.import_module("run_real_test")
    if dim_key == "6_fund_holders":
        result = importlib.import_module("fetch_fund_holders").main(ticker, limit=None)
        return _bonus_outcome(dim_key, result)
    if dim_key == "similar_stocks":
        result = importlib.import_module("fetch_similar_stocks").main(ticker, top_n=4)
        return _bonus_outcome(dim_key, result)
    for module_name, key, args_fn in native.FETCHER_MAP:
        if key == dim_key:
            args = args_fn(ticker, raw_context or {})
            return dim_key, native.run_fetcher(module_name, args), {}

    raise ValueError(f"No pinned SKILL core fetcher for {dim_key}")


def _retain_worker_failure(outcome):
    result = {
        "data": {}, "error": outcome.error, "fallback": True,
        "_timeout": bool(outcome.timed_out),
        "source": "cognitrade:process_timeout" if outcome.timed_out else "cognitrade:process_worker",
    }
    value = _bonus_outcome(outcome.key, result) if outcome.key in BONUS_FIELDS else (outcome.key, result, {})
    return replace(outcome, error=None, value=value)


@contextmanager
def skill_envelope_scope(module: Any, *, enabled: bool):
    """Replace only native leaf targets before policy/cache/owned-sentinel wraps.

    Worker exceptions are actual host execution failures, not invented source
    responses. Expose them at the native error/timeout fields and identify the
    host boundary explicitly so downstream native checks cannot mistake an
    error for a successful empty response. Do not rerun the whole stock.
    """
    if not enabled:
        yield
        return
    original_runner = module.run_process_jobs
    original_resume_valid = module._is_resume_valid
    core_keys = {key for _, key, _ in importlib.import_module("run_real_test").FETCHER_MAP}
    skill_keys = core_keys | BONUS_FIELDS.keys()

    def runner(jobs, *, max_workers, overall_timeout):
        jobs = list(jobs)
        deferred = _DEFERRED_BONUSES.get()
        if deferred is not None:
            if deferred.enabled_dimensions is not None:
                jobs = [job for job in jobs if job.key not in core_keys
                        or job.key == "0_basic" or job.key in deferred.enabled_dimensions]
            if deferred.resolved_ticker:
                jobs = [replace(job, args=(job.args[0], deferred.resolved_ticker, *job.args[2:]))
                        if job.key in skill_keys and job.key != "0_basic"
                        and job.target is module._run_fetcher_job else job for job in jobs]
        late_jobs = [job for job in jobs if job.key in BONUS_FIELDS] if deferred is not None else []
        if late_jobs:
            deferred.jobs.extend(late_jobs)
            jobs = [job for job in jobs if job.key not in BONUS_FIELDS]
        jobs = [replace(job, target=run_skill_fetcher_job)
                if job.key in skill_keys and job.target is module._run_fetcher_job else job for job in jobs]
        started = time.monotonic()
        outcomes = original_runner(jobs, max_workers=max_workers, overall_timeout=overall_timeout) if jobs else []
        if late_jobs:
            deferred.remaining_timeout = max(0.0, overall_timeout - (time.monotonic() - started))
        retained = []
        for outcome in outcomes:
            if not outcome.error or outcome.key not in skill_keys:
                retained.append(outcome)
                continue
            retained.append(_retain_worker_failure(outcome))
        if deferred is not None:
            for outcome in retained:
                if outcome.key == "0_basic" and outcome.value:
                    basic = outcome.value[1]
                    deferred.resolved_ticker = (basic.get("data") or {}).get("ticker") or basic.get("ticker")
        return retained

    def resume_valid(value, *args, **kwargs):
        if _DEFERRED_BONUSES.get() is None:
            return original_resume_valid(value, *args, **kwargs)
        # collect_raw_data checks data/error/_timeout, not pipeline quality,
        # fallback or a second TTL on the completed task-output cache.
        return isinstance(value, dict) and bool(value.get("data")) and not value.get("error") and not value.get("_timeout")

    module.run_process_jobs = runner
    module._is_resume_valid = resume_valid
    try:
        yield
    finally:
        module.run_process_jobs = original_runner
        module._is_resume_valid = original_resume_valid
