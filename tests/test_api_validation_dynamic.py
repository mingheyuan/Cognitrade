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

    def test_formal_report_accepts_only_an_iso_point_in_time_date(self) -> None:
        request = validate_research_request(
            RequestEnvelope(
                intent="stock_check",
                user_query="回看 600519",
                constraints={
                    "research_profile": "lite",
                    "analysis_as_of_date": "2026-08-20",
                },
            )
        )
        self.assertEqual(request.constraints["analysis_as_of_date"], "2026-08-20")

        with self.assertRaises(ApiError) as invalid:
            validate_research_request(
                RequestEnvelope(
                    intent="stock_check",
                    user_query="回看 600519",
                    constraints={
                        "research_profile": "lite",
                        "analysis_as_of_date": "2026-02-30",
                    },
                )
            )
        self.assertEqual(invalid.exception.code, "invalid_constraint")

    def test_uzi_portfolio_batch_preserves_native_weight_parsing(self) -> None:
        request = validate_research_request(
            RequestEnvelope(
                intent="uzi_portfolio_batch",
                user_query="分析这个组合",
                constraints={
                    "portfolio_holdings": [
                        {"ticker": "600519.SH", "weight": 30},
                        {"ticker": "000858.SZ", "weight": 15},
                        {"ticker": "002594.SZ"},
                    ],
                    "research_profile": "medium",
                    "school": "a",
                },
            )
        )

        holdings = request.constraints["portfolio_holdings"]
        self.assertEqual(holdings[0]["weight"], 0.3)
        self.assertEqual(holdings[1]["weight"], 0.15)
        self.assertIsNone(holdings[2].get("weight"))
        self.assertEqual(request.constraints["school"], "A")

    def test_portfolio_method_validation_preserves_each_pinned_weight_contract(self) -> None:
        returns = validate_research_request(
            RequestEnvelope(
                intent="uzi_returns",
                user_query="组合归因",
                constraints={
                    "portfolio_holdings": [
                        {"ticker": "AAA", "return_pct": 5},
                        {"ticker": "BBB"},
                    ]
                },
            )
        )
        self.assertNotIn("weight", returns.constraints["portfolio_holdings"][0])

        rebalance = validate_research_request(
            RequestEnvelope(
                intent="uzi_rebalance",
                user_query="组合再平衡",
                constraints={
                    "portfolio_holdings": [
                        {"ticker": "AAA", "weight": 1.2},
                        {"ticker": "BBB", "weight": 0.8},
                    ],
                    "rebalance_targets": {"AAA": 0.8},
                },
            )
        )
        self.assertEqual(
            [row["weight"] for row in rebalance.constraints["portfolio_holdings"]],
            [1.2, 0.8],
        )
        self.assertEqual(rebalance.constraints["rebalance_targets"], {"AAA": 0.8})

        strategic = validate_research_request(
            RequestEnvelope(
                intent="uzi_portfolio_rebalance_method",
                user_query="战略资产再平衡",
                constraints={
                    "portfolio_holdings": [
                        {"symbol": "AAA", "value": 100000, "asset_class": "A股蓝筹"}
                    ],
                    "asset_class_targets": {"A股蓝筹": 80, "现金": 20},
                },
            )
        )
        self.assertEqual(
            strategic.constraints["asset_class_targets"],
            {"A股蓝筹": 80, "现金": 20},
        )

        with self.assertRaises(ApiError):
            validate_research_request(
                RequestEnvelope(
                    intent="uzi_portfolio_rebalance_method",
                    user_query="错误百分比",
                    constraints={
                        "portfolio_holdings": [
                            {"ticker": "AAA", "value": 100000, "asset_class": "A股蓝筹"}
                        ],
                        "asset_class_targets": {"A股蓝筹": 0.8, "现金": 0.2},
                    },
                )
            )

        for constraints in (
            {
                "portfolio_holdings": [
                    {"ticker": "AAA", "weight": 0},
                    {"ticker": "BBB", "weight": 0},
                ]
            },
            {
                "portfolio_holdings": [{"ticker": "AAA"}, {"ticker": "BBB"}],
                "rebalance_targets": {"AAA": 0, "BBB": 0},
            },
        ):
            with self.subTest(constraints=constraints), self.assertRaises(ApiError):
                validate_research_request(
                    RequestEnvelope(
                        intent=(
                            "uzi_returns"
                            if "rebalance_targets" not in constraints
                            else "uzi_rebalance"
                        ),
                        user_query="显式零权重",
                        constraints=constraints,
                    )
                )

    def test_standalone_method_inputs_reject_wrong_method_and_float_years(self) -> None:
        cases = (
            (
                "uzi_lbo",
                {"method_assumptions": {"quick_lbo": {"hold_years": 5.0}}},
            ),
            (
                "uzi_dcf",
                {"method_assumptions": {"dcf": {"stage1_years": 5.0}}},
            ),
            (
                "uzi_lbo",
                {"method_assumptions": {"three_statement": {"gross_margin": 0.4}}},
            ),
            (
                "uzi_earnings",
                {
                    "method_assumptions": {
                        "earnings_review": {"consensus": {"rev": 0, "ni": 0}}
                    }
                },
            ),
        )
        for intent, extra in cases:
            with self.subTest(intent=intent, extra=extra), self.assertRaises(ApiError):
                validate_research_request(
                    RequestEnvelope(
                        intent=intent,
                        user_query="运行固定 UZI 方法",
                        constraints={"symbol": "600519", **extra},
                    )
                )

        accepted = validate_research_request(
            RequestEnvelope(
                intent="uzi_lbo",
                user_query="运行 LBO",
                constraints={
                    "symbol": "600519",
                    "method_assumptions": {"quick_lbo": {"hold_years": 5}},
                },
            )
        )
        self.assertEqual(
            accepted.constraints["method_assumptions"]["quick_lbo"]["hold_years"],
            5,
        )

    def test_accretion_target_accepts_losses_and_rejects_nonpositive_shape(self) -> None:
        target = {
            "ticker": "LOSS",
            "name": "亏损标的",
            "shares_yi": 10,
            "price": 5,
            "net_income_yi": -2,
        }
        accepted = validate_research_request(
            RequestEnvelope(
                intent="uzi_accretion_dilution_method",
                user_query="并购测算",
                constraints={
                    "symbol": "600519",
                    "method_assumptions": {
                        "accretion_dilution": {"target": target}
                    },
                },
            )
        )
        self.assertEqual(
            accepted.constraints["method_assumptions"]["accretion_dilution"]["target"],
            target,
        )

        with self.assertRaises(ApiError):
            validate_research_request(
                RequestEnvelope(
                    intent="uzi_accretion_dilution_method",
                    user_query="错误并购测算",
                    constraints={
                        "symbol": "600519",
                        "method_assumptions": {
                            "accretion_dilution": {
                                "target": {**target, "shares_yi": 0}
                            }
                        },
                    },
                )
            )

    def test_removed_host_acceptance_flags_are_not_public_contract(self) -> None:
        with self.assertRaises(ApiError):
            validate_research_request(
                RequestEnvelope(
                    intent="uzi_rebalance",
                    user_query="再平衡",
                    constraints={
                        "portfolio_holdings": [{"ticker": "AAA"}],
                        "accept_uzi_native_fee_schedule": False,
                    },
                )
            )

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
