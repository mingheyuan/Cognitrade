"""Offline queue replay and simulated HTTP contracts; no model requests.

The 2026-09-07 export recorded gateway_concurrency_limit but no response
headers. Retry-After/request-ID cases below are simulated SDK contracts, not
claims about headers recovered from that run.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from openai import APIStatusError

from domain_packs.finance_ashare.subagents import uzi_llm_support as support
from research_system.runtime.providers.errors import LLMProviderError


class RecordedGatewayProvider:
    """Message from 688256 qualitative C; response headers were not recorded."""

    settings = SimpleNamespace(uzi_llm_max_concurrency=4)

    def __init__(self, error=None, failures=1):
        self.error = error or LLMProviderError(
            "llm_request_failed: RateLimitError: Error code: 429 - "
            "{'error': {'code': 'gateway_concurrency_limit', 'message': "
            "'Concurrency limit exceeded for user, please retry later', 'type': 'rate_limit_error'}}"
        )
        self.failures = failures
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) <= self.failures:
            raise self.error
        return {"ok": True}


def simulated_status(status, code, *, retry_after=None, error_type="rate_limit_error"):
    headers = {"x-request-id": "req_simulated_429", "authorization": "must-not-log"}
    if retry_after is not None:
        headers["retry-after"] = retry_after
    cause = APIStatusError(
        "message not for diagnostics: token=secret",
        response=httpx.Response(status, headers=headers, request=httpx.Request("POST", "https://test.invalid")),
        body={"error": {"code": code, "type": error_type, "message": "token=secret"}},
    )
    wrapper = LLMProviderError("llm_request_failed: APIStatusError: provider rejected request")
    wrapper.__cause__ = cause
    return wrapper


def test_recorded_gateway_429_is_bounded_retry_not_content_repair():
    provider = RecordedGatewayProvider(failures=2)
    with patch.object(support, "sleep") as waiting, patch.object(support, "random", return_value=0):
        assert support.parse_uzi_structured(provider, schema=dict) == {"ok": True}
    assert len(provider.calls) == 3
    assert [c.args[0] for c in waiting.call_args_list] == [2, 4]
    assert all(c["max_retries"] == 0 for c in provider.calls)


def test_simulated_retry_after_and_request_id_survive_exception_wrapper():
    error = simulated_status(429, "gateway_concurrency_limit", retry_after="75")
    provider = RecordedGatewayProvider(error)
    progress = []
    with patch.object(support, "sleep") as waiting, patch.object(support, "random", return_value=0):
        support.parse_uzi_structured(provider, schema=dict, progress_callback=progress.append)
    assert [c.args[0] for c in waiting.call_args_list] == [75]  # Not capped to local 60s backoff.
    record = support.model_failure_record(error, stage="qualitative_C")
    assert record["provider_status_code"] == 429
    assert record["provider_error_code"] == "gateway_concurrency_limit"
    assert record["provider_request_id"] == "req_simulated_429"
    assert record["retry_after_seconds"] == 75
    assert "secret" not in str(record) and "must-not-log" not in str(record)
    failed = next(row for row in progress if row["request_phase"] == "failed")
    assert failed["request_timing"]["provider_request_id"] == "req_simulated_429"
    assert len({row["request_timing"]["local_request_id"] for row in progress}) == 1


@pytest.mark.parametrize("status,code,error_type", [
    (401, "invalid_api_key", "upstream_error"),
    (403, "permission_denied", "upstream_error"),
    (429, "credit_balance_exhausted", "insufficient_quota"),
    (429, "organization_spend_limit_exceeded", "insufficient_quota"),
    (429, "project_spend_limit_exceeded", "insufficient_quota"),
    (429, "organization_usage_limit_exceeded", "insufficient_quota"),
    (429, "insufficient_quota", "insufficient_quota"),
])
def test_auth_and_quota_cannot_be_recovered_by_backoff(status, code, error_type):
    provider = RecordedGatewayProvider(simulated_status(status, code, error_type=error_type))
    with patch.object(support, "sleep") as waiting, pytest.raises(LLMProviderError):
        support.parse_uzi_structured(provider, schema=dict)
    assert len(provider.calls) == 1
    waiting.assert_not_called()
    record = support.model_failure_record(provider.error, stage="qualitative_C")
    assert record["provider_error_code"] == code
    assert record["provider_retryable"] is False


def test_retry_after_larger_than_shared_budget_never_sends_early_or_moves_deadline():
    now = [100.0]
    provider = RecordedGatewayProvider(simulated_status(429, "slow_down", retry_after="75"))

    def advance(seconds):
        now[0] += seconds

    with patch.object(support, "monotonic", side_effect=lambda: now[0]), \
            patch.object(support, "sleep", side_effect=advance), \
            pytest.raises(support.UziRequestDeadline) as caught:
        support.parse_uzi_structured(provider, schema=dict, deadline_at=110)
    assert now[0] == 110
    assert len(provider.calls) == 1
    assert provider.calls[0]["deadline_at"] == 110
    assert provider.calls[0]["timeout_seconds"] == 10
    record = support.model_failure_record(caught.value, stage="qualitative_C")
    assert record["kind"] == "llm_stage_deadline"
    assert record["provider_request_id"] == "req_simulated_429"


def wait_until(predicate):
    stop = monotonic() + 3
    while not predicate():
        assert monotonic() < stop, "offline worker did not reach controlled barrier"
        sleep(0.001)


def test_five_holding_owners_share_four_slots_without_fifo_fanout_starvation():
    limiter = support._RequestLimiter(4)
    cancelled = Event()
    entered = []
    release = {name: Event() for name in ["a0", "a1", "a2", "a3", "a4", "a5", "b", "c", "d", "e"]}
    base = SimpleNamespace()
    owners = {name: support.with_uzi_request_control(base) for name in "abcde"}
    assert support.with_uzi_request_control(owners["a"])._uzi_request_owner is owners["a"]._uzi_request_owner

    def request(name):
        owner = owners[name[0]]._uzi_request_owner
        limiter.acquire(cancelled, monotonic() + 3, owner=owner)
        try:
            entered.append(name)
            assert release[name].wait(3)
        finally:
            limiter.release(owner=owner)

    with ThreadPoolExecutor(max_workers=10) as executor:
        tasks = []
        try:
            for name in ["a0", "a1", "a2", "a3"]:
                tasks.append(executor.submit(request, name))
                wait_until(lambda: name in entered)
            assert limiter.active == 4  # Single owner borrows otherwise idle slots.
            for name in ["a4", "a5", "b", "c", "d", "e"]:
                tasks.append(executor.submit(request, name))
                expected = len(tasks) - 4
                wait_until(lambda: len(limiter.waiters) == expected)
            for old, next_owner in zip(["a0", "a1", "a2", "a3"], "bcde"):
                release[old].set()
                wait_until(lambda: next_owner in entered)
                assert "a4" not in entered and "a5" not in entered
                assert limiter.active == 4
        finally:
            for event in release.values():
                event.set()
            for task in tasks:
                task.result(timeout=4)
    assert limiter.active == 0 and not limiter.waiters
    assert not limiter.active_by_owner and not limiter.last_admitted


def test_virtual_clock_replays_recorded_medium_queue_600_seconds_without_http():
    """002371 dimensions_6_10 recorded ~600s queue and 0s HTTP; no real wait."""
    now = [100.0]
    limiter = support._RequestLimiter(4)
    progress = []
    provider = RecordedGatewayProvider(failures=0)
    # Replay the recorded admission setting, not today's independent default.
    provider.settings = SimpleNamespace(uzi_llm_max_concurrency=4, uzi_llm_queue_timeout_seconds=600)

    def advance(*, timeout):
        now[0] += timeout

    with patch.object(support, "monotonic", side_effect=lambda: now[0]), \
            patch.object(support, "_uzi_semaphore", return_value=limiter), \
            patch.object(limiter.condition, "wait", side_effect=advance):
        for _ in range(4):
            limiter.acquire(None, 1300)
        try:
            with pytest.raises(support.UziRequestDeadline, match="llm_queue_timeout"):
                support.parse_uzi_structured(provider, schema=dict, research_profile="medium",
                                             uzi_stage="dimensions_6_10", progress_callback=progress.append)
            assert not provider.calls
            assert progress[-1]["request_phase"] == "queue_timeout"
            assert progress[-1]["request_timing"]["queue_seconds"] == 600
            assert progress[-1]["request_timing"]["http_seconds"] == 0
            assert progress[-1]["request_timing"]["remaining_budget_seconds"] == 600
            assert limiter.active == 4 and not limiter.waiters
        finally:
            for _ in range(4):
                limiter.release()


def test_parse_uses_one_owner_for_nested_stages_and_legacy_keeps_none():
    limiter = support._RequestLimiter(4)
    base = RecordedGatewayProvider(failures=0)
    wrapped = support.with_uzi_request_control(base)
    nested = support.with_uzi_request_control(wrapped)
    with patch.object(support, "_uzi_semaphore", return_value=limiter), \
            patch.object(limiter, "acquire", wraps=limiter.acquire) as acquire:
        for provider in (wrapped, nested, base):
            support.parse_uzi_structured(provider, schema=dict)
    assert [c.kwargs["owner"] for c in acquire.call_args_list] == [wrapped._uzi_request_owner] * 2 + [None]
    assert limiter.active == 0 and not limiter.active_by_owner and not limiter.last_admitted


def test_nested_wrapper_or_explicit_stage_argument_cannot_extend_root_deadline():
    base = RecordedGatewayProvider(failures=0)
    with patch.object(support, "monotonic", return_value=100):
        wrapped = support.with_uzi_request_control(base, deadline_at=110)
        nested = support.with_uzi_request_control(wrapped, deadline_at=200)
        support.parse_uzi_structured(nested, schema=dict, deadline_at=300)
    assert base.calls[0]["deadline_at"] == 110
    assert base.calls[0]["timeout_seconds"] == 10


def test_simulated_http_date_retry_after_and_malformed_headers():
    with patch.object(support, "datetime") as clock:
        clock.now.return_value = datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc)
        assert support._retry_after_seconds("Tue, 08 Sep 2026 00:01:15 GMT") == 75
    for value in ("NaN", "inf", "nonsense", [], None):
        assert support._retry_after_seconds(value) is None


def test_slow_progress_callback_cannot_start_http_after_shared_deadline():
    now = [100.0]
    provider = RecordedGatewayProvider(failures=0)
    progress = []

    def callback(row):
        progress.append(row)
        if row["request_phase"] == "requesting":
            now[0] = 111

    with patch.object(support, "monotonic", side_effect=lambda: now[0]), pytest.raises(support.UziRequestDeadline):
        support.parse_uzi_structured(provider, schema=dict, deadline_at=110, progress_callback=callback)
    assert not provider.calls
    assert progress[-1]["request_timing"]["http_seconds"] == 0


def test_legacy_error_metadata_is_bounded_and_never_invents_response_headers():
    record = support.model_failure_record(RecordedGatewayProvider().error, stage="qualitative_C")
    assert record["provider_error_code"] == "gateway_concurrency_limit"
    assert "provider_request_id" not in record and "retry_after_seconds" not in record
    error = LLMProviderError("Error code: 429 - {'code': '" + "a" * 10000 + "'}")
    assert "provider_error_code" not in support.model_failure_record(error, stage="qualitative_C")


def test_ownerless_legacy_queue_stays_fifo():
    limiter = support._RequestLimiter(1)
    limiter.acquire(None, monotonic() + 3)
    entered = []

    def request(number):
        limiter.acquire(None, monotonic() + 3)
        try:
            entered.append(number)
        finally:
            limiter.release()

    with ThreadPoolExecutor(max_workers=3) as executor:
        tasks = []
        try:
            for number in range(3):
                tasks.append(executor.submit(request, number))
                wait_until(lambda: len(limiter.waiters) == number + 1)
        finally:
            limiter.release()
        for task in tasks:
            task.result(timeout=3)
    assert entered == [0, 1, 2]


def test_queued_cancel_reports_zero_http_and_does_not_leak_slot():
    limiter = support._RequestLimiter(1)
    limiter.acquire(None, monotonic() + 3)
    provider = RecordedGatewayProvider(failures=0)
    cancellation = Event()
    progress = []

    def callback(row):
        progress.append(row)
        if row["request_phase"] == "queued":
            cancellation.set()

    try:
        with patch.object(support, "_uzi_semaphore", return_value=limiter), pytest.raises(support.UziRequestCancelled):
            support.parse_uzi_structured(provider, schema=dict, cancellation=cancellation, progress_callback=callback)
        assert not provider.calls
        assert progress[-1]["request_phase"] == "cancelled"
        assert progress[-1]["request_timing"]["http_seconds"] == 0
        assert not limiter.waiters and limiter.active == 1
    finally:
        limiter.release()


def test_cancelling_active_transport_does_not_release_before_cleanup_finishes():
    limiter = support._RequestLimiter(1)
    cancellation, started, cleaning, allow_exit = Event(), Event(), Event(), Event()

    class SlowCleanupProvider:
        def parse(self, **_kwargs):
            started.set()
            assert cancellation.wait(3)
            cleaning.set()
            assert allow_exit.wait(3)
            raise support.UziRequestCancelled("transport stopped")

    next_provider = RecordedGatewayProvider(failures=0)
    with patch.object(support, "_uzi_semaphore", return_value=limiter), ThreadPoolExecutor(max_workers=2) as executor:
        running = executor.submit(support.parse_uzi_structured, SlowCleanupProvider(), cancellation=cancellation, schema=dict)
        assert started.wait(3)
        cancellation.set()
        assert cleaning.wait(3)
        waiting = executor.submit(support.parse_uzi_structured, next_provider, schema=dict)
        try:
            wait_until(lambda: len(limiter.waiters) == 1)
            assert not next_provider.calls and limiter.active == 1
        finally:
            allow_exit.set()
        with pytest.raises(support.UziRequestCancelled):
            running.result(timeout=3)
        assert waiting.result(timeout=3) == {"ok": True}
    assert limiter.active == 0 and not limiter.waiters
