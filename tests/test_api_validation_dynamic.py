from __future__ import annotations

import unittest

from research_system.api.errors import ApiError
from research_system.api.validation import validate_research_request
from research_system.schemas.protocol import RequestEnvelope


class DynamicApiValidationTests(unittest.TestCase):
    def test_stock_research_requires_an_explicit_supported_profile(self) -> None:
        with self.assertRaises(ApiError) as missing:
            validate_research_request(
                RequestEnvelope(intent="stock_check", user_query="分析 600519")
            )
        self.assertEqual(missing.exception.code, "missing_constraint")

        for profile in ("lite", "medium", "deep"):
            request = validate_research_request(
                RequestEnvelope(
                    intent="stock_check",
                    user_query="分析 600519",
                    constraints={"research_profile": profile},
                )
            )
            self.assertEqual(request.constraints["research_profile"], profile)

        with self.assertRaises(ApiError) as obsolete:
            validate_research_request(
                RequestEnvelope(
                    intent="stock_check",
                    user_query="分析 600519",
                    constraints={"research_profile": "quick"},
                )
            )
        self.assertEqual(obsolete.exception.code, "invalid_constraint")

    def test_stock_screen_requires_an_explicit_investment_cycle(self) -> None:
        with self.assertRaises(ApiError) as missing:
            validate_research_request(
                RequestEnvelope(
                    intent="stock_screen",
                    user_query="筛选 A 股",
                    constraints={"research_profile": "lite"},
                )
            )
        self.assertEqual(missing.exception.code, "missing_constraint")
        self.assertEqual(missing.exception.details["field"], "investment_cycle")

        request = validate_research_request(
            RequestEnvelope(
                intent="stock_screen",
                user_query="筛选 A 股",
                constraints={
                    "research_profile": "lite",
                    "investment_cycle": "short_term",
                },
            )
        )
        self.assertEqual(request.constraints["investment_cycle"], "short_term")

    def test_pack_owned_intent_is_validated_from_runtime_catalog(self) -> None:
        request = validate_research_request(
            RequestEnvelope(
                intent="audit.review",
                user_query="Review the audit evidence.",
                constraints={"pack_option": "strict"},
            ),
            supported_intents=["audit.review"],
        )

        self.assertEqual(request.intent, "audit.review")
        self.assertEqual(request.constraints["pack_option"], "strict")

    def test_core_intent_does_not_depend_on_a_domain_pack(self) -> None:
        request = validate_research_request(
            RequestEnvelope(
                intent="core.conversation.answer",
                user_query="Who are you?",
            ),
            supported_intents=[],
        )

        self.assertEqual(request.intent, "core.conversation.answer")

    def test_unknown_intent_is_rejected_when_not_in_runtime_catalog(self) -> None:
        with self.assertRaises(ApiError):
            validate_research_request(
                RequestEnvelope(intent="not_installed.answer", user_query="Run it."),
                supported_intents=["audit.review"],
            )


if __name__ == "__main__":
    unittest.main()
