from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from domain_packs.finance_ashare.subagents.uzi_llm_support import (
    DEFAULT_UZI_LLM_MAX_CONCURRENCY,
    DEFAULT_UZI_LLM_RETRY_BASE_SECONDS,
    DEFAULT_UZI_LLM_RETRY_MAX_SECONDS,
    DEFAULT_UZI_LLM_REQUEST_TIMEOUT_SECONDS,
    DEFAULT_UZI_LLM_TRANSIENT_RETRIES,
    parse_uzi_structured,
)


class _TimeoutAwareProvider:
    def __init__(self, timeout: int) -> None:
        self.settings = SimpleNamespace(
            uzi_llm_request_timeout_seconds=timeout,
            uzi_llm_max_concurrency=3,
        )
        self.received_timeout: float | None = None
        self.received_max_retries: int | None = None

    def parse(self, *, timeout_seconds=None, max_retries=None, **_kwargs):
        self.received_timeout = timeout_seconds
        self.received_max_retries = max_retries
        return {"ok": True}


class _LegacyProvider:
    def parse(self, *, schema):
        return {"schema": schema}


class _TransientProvider(_TimeoutAwareProvider):
    def __init__(self, failures: int, message: str) -> None:
        super().__init__(480)
        self.failures = failures
        self.message = message
        self.calls = 0

    def parse(self, **_kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError(self.message)
        return {"ok": True}


class UziLlmSupportTests(unittest.TestCase):
    def test_dedicated_timeout_is_forwarded_to_capable_provider(self) -> None:
        provider = _TimeoutAwareProvider(321)

        result = parse_uzi_structured(provider, schema=dict)

        self.assertEqual(result, {"ok": True})
        self.assertEqual(provider.received_timeout, 321.0)
        self.assertEqual(provider.received_max_retries, 0)

    def test_legacy_provider_keeps_compatible_parse_signature(self) -> None:
        provider = _LegacyProvider()

        result = parse_uzi_structured(
            provider,
            schema=dict,
            reasoning_effort="low",
        )

        self.assertEqual(result, {"schema": dict})

    def test_default_budget_matches_medium_uzi_profile(self) -> None:
        self.assertEqual(DEFAULT_UZI_LLM_REQUEST_TIMEOUT_SECONDS, 480.0)
        self.assertEqual(DEFAULT_UZI_LLM_MAX_CONCURRENCY, 2)
        self.assertEqual(DEFAULT_UZI_LLM_TRANSIENT_RETRIES, 6)
        self.assertEqual(DEFAULT_UZI_LLM_RETRY_BASE_SECONDS, 2.0)
        self.assertEqual(DEFAULT_UZI_LLM_RETRY_MAX_SECONDS, 60.0)

    @patch("domain_packs.finance_ashare.subagents.uzi_llm_support.random", return_value=0.0)
    @patch("domain_packs.finance_ashare.subagents.uzi_llm_support.sleep")
    def test_transient_upstream_502_retries_with_bounded_exponential_backoff(
        self,
        sleep_mock,
        _random_mock,
    ) -> None:
        provider = _TransientProvider(
            failures=6,
            message="Error code: 502 - upstream_error: Upstream request failed",
        )

        result = parse_uzi_structured(provider, schema=dict)

        self.assertEqual(result, {"ok": True})
        self.assertEqual(provider.calls, 7)
        self.assertEqual(
            [call.args[0] for call in sleep_mock.call_args_list],
            [2.0, 4.0, 8.0, 16.0, 32.0, 60.0],
        )

    @patch("domain_packs.finance_ashare.subagents.uzi_llm_support.random", return_value=0.0)
    @patch("domain_packs.finance_ashare.subagents.uzi_llm_support.sleep")
    def test_transient_api_connection_error_retries_before_returning_to_quality_gate(
        self,
        sleep_mock,
        _random_mock,
    ) -> None:
        provider = _TransientProvider(
            failures=2,
            message="llm_request_failed: APIConnectionError: Connection error.",
        )

        result = parse_uzi_structured(provider, schema=dict)

        self.assertEqual(result, {"ok": True})
        self.assertEqual(provider.calls, 3)
        self.assertEqual(
            [call.args[0] for call in sleep_mock.call_args_list],
            [2.0, 4.0],
        )

    @patch("domain_packs.finance_ashare.subagents.uzi_llm_support.sleep")
    def test_timeout_is_not_retried(self, sleep_mock) -> None:
        provider = _TransientProvider(
            failures=1,
            message="llm_request_timeout: 480s",
        )

        with self.assertRaisesRegex(RuntimeError, "llm_request_timeout"):
            parse_uzi_structured(provider, schema=dict)

        self.assertEqual(provider.calls, 1)
        sleep_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
