from __future__ import annotations

from types import SimpleNamespace
import unittest

from domain_packs.finance_ashare.subagents.uzi_llm_support import (
    DEFAULT_UZI_LLM_MAX_CONCURRENCY,
    DEFAULT_UZI_LLM_REQUEST_TIMEOUT_SECONDS,
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
        self.assertEqual(DEFAULT_UZI_LLM_MAX_CONCURRENCY, 5)


if __name__ == "__main__":
    unittest.main()
