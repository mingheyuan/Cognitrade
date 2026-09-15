"""Executor wait belongs to the existing stage budget, not a fresh worker budget."""
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event, Lock
from types import SimpleNamespace

import pytest

from domain_packs.finance_ashare.subagents import uzi_llm_support as support


class DeferredExecutor:
    def submit(self, fn, *args, **kwargs):
        self.work = lambda: fn(*args, **kwargs)
        return Future()


class Provider:
    settings = SimpleNamespace(
        uzi_llm_request_timeout_seconds=100,
        uzi_llm_max_concurrency=4,
        uzi_llm_transient_retries=0,
    )

    def __init__(self):
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return "ok"


def submit(pool, fn, *args, **kwargs):
    # The fallback exercises the pre-fix production dispatch, not an import error.
    helper = getattr(support, "submit_uzi_stage", None)
    return helper(pool, fn, *args, **kwargs) if helper else pool.submit(fn, *args, **kwargs)


@pytest.mark.parametrize("delay,reason", [(120, "llm_queue_timeout"), (210, "uzi_stage_deadline_exceeded")])
def test_expired_executor_wait_never_calls_model(monkeypatch, delay, reason):
    now = [1000.0]
    monkeypatch.setattr(support, "monotonic", lambda: now[0])
    pool, provider, progress = DeferredExecutor(), Provider(), []
    submit(pool, support.parse_uzi_structured, provider, progress_callback=progress.append)
    now[0] += delay
    with pytest.raises(support.UziRequestDeadline, match=reason):
        pool.work()
    assert provider.calls == []
    assert progress[-1]["request_timing"]["executor_wait_seconds"] == delay


def test_dispatch_deadline_and_separate_wait_timing(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(support, "monotonic", lambda: now[0])
    pool, provider, progress = DeferredExecutor(), Provider(), []
    submit(pool, support.parse_uzi_structured, provider, progress_callback=progress.append)
    now[0] += 40
    assert pool.work() == "ok"
    assert provider.calls[0]["deadline_at"] == 1200
    timing = progress[-1]["request_timing"]
    assert timing["executor_wait_seconds"] == 40
    assert timing["queue_seconds"] == 0
    assert timing["stage_elapsed_seconds"] == 40
    assert timing["remaining_budget_seconds"] == 160
    # The worker's context must not leak into an unrelated synchronous call.
    support.parse_uzi_structured(provider)
    assert provider.calls[1]["deadline_at"] == 1240


def test_output_repair_shares_dispatch_deadline_without_counting_http_as_queue(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(support, "monotonic", lambda: now[0])
    pool, provider = DeferredExecutor(), Provider()

    def stage():
        support.parse_uzi_structured(provider)
        now[0] += 105  # elapsed first call + schema repair preparation
        support.parse_uzi_structured(provider)

    submit(pool, stage)
    now[0] += 10
    pool.work()
    assert [row["deadline_at"] for row in provider.calls] == [1200, 1200]
    assert provider.calls[1]["timeout_seconds"] == 85


def test_actual_thread_queue_preserves_deadline_and_cancellation(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(support, "monotonic", lambda: now[0])
    entered, release, cancelled = Event(), Event(), Event()
    provider = Provider()

    def occupy():
        entered.set()
        assert release.wait(5)

    with ThreadPoolExecutor(max_workers=1) as pool:
        blocker = pool.submit(occupy)
        assert entered.wait(5)
        waiting = submit(pool, support.parse_uzi_structured, provider, cancellation=cancelled)
        now[0] += 210
        cancelled.set()
        release.set()
        blocker.result(5)
        with pytest.raises(support.UziRequestCancelled):
            waiting.result(5)
        assert provider.calls == []
        cancelled.clear()
        now[0] = 2000
        fresh = submit(pool, support.parse_uzi_structured, provider)
        assert fresh.result(5) == "ok"
        assert provider.calls[-1]["deadline_at"] == 2200


def test_parent_deadline_is_not_extended(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(support, "monotonic", lambda: now[0])
    pool, provider = DeferredExecutor(), Provider()
    submit(pool, support.parse_uzi_structured, provider, deadline_at=1050)
    now[0] += 40
    assert pool.work() == "ok"
    assert provider.calls[0]["deadline_at"] == 1050
    assert provider.calls[0]["timeout_seconds"] == 10


def test_fifth_actual_roleplay_group_records_executor_wait(monkeypatch):
    from domain_packs.finance_ashare.subagents.uzi_agent_roleplay import (
        apply_deep_agent_roleplay,
    )
    from tests.test_uzi_deep_roleplay import _panel, _RoleplayProvider

    now = [1000.0]
    monkeypatch.setattr(support, "monotonic", lambda: now[0])
    entered, enqueued, release, lock = Event(), Event(), Event(), Lock()
    count, progress = [0], []
    from domain_packs.finance_ashare.subagents import uzi_agent_roleplay as role_module
    submitted = [0]

    def record_submit(*args, **kwargs):
        future = support.submit_uzi_stage(*args, **kwargs)
        submitted[0] += 1
        if submitted[0] == 5:
            enqueued.set()
        return future

    monkeypatch.setattr(role_module, "submit_uzi_stage", record_submit)

    class WaitingProvider(_RoleplayProvider):
        def parse(self, **kwargs):
            with lock:
                count[0] += 1
                if count[0] == 4:
                    entered.set()
            assert release.wait(5)
            return super().parse(**{key: kwargs[key] for key in ("schema", "system_prompt", "user_prompt")})

    provider = WaitingProvider()
    provider.settings = SimpleNamespace(uzi_llm_max_concurrency=4)
    controlled = support.with_uzi_request_control(provider, progress_callback=progress.append)
    with ThreadPoolExecutor(max_workers=1) as runner:
        task = runner.submit(apply_deep_agent_roleplay, _panel("deep"), features={}, llm_provider=controlled)
        assert entered.wait(5)
        assert enqueued.wait(5)
        now[0] += 37
        release.set()
        result = task.result(10)
    assert result["roleplay"]["completed_group_count"] == 5
    requests = [row for row in progress if row["request_phase"] == "requesting"]
    assert sorted(row["request_timing"]["executor_wait_seconds"] for row in requests) == [0, 0, 0, 0, 37]
    assert all(row["request_timing"]["queue_seconds"] == 0 for row in requests)
