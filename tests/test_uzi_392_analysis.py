from __future__ import annotations

import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from domain_packs.finance_ashare.subagents.uzi_392_analysis import (
    AgentAnalysisPayload,
    AgentDimensionPayload,
    AgentNarrativePayload,
    _canonical_evidence_path,
    _normalize_payload_paths,
    _safe_error_detail,
    execute_native_analysis,
)
from tests.test_uzi_392_native_methods import _snapshots


_LITE_DIMENSIONS = {
    "0_basic",
    "1_financials",
    "2_kline",
    "10_valuation",
    "11_governance",
    "15_events",
    "16_lhb",
}


def _lite_snapshots() -> list[dict]:
    rows = _snapshots()
    rows.extend(
        [
            {
                "snapshot_type": "governance",
                "symbol": "600000",
                "facts": {
                    "_uzi_raw_result": {
                        "data": {"pledge": [], "insider_trades_1y": []},
                        "sources": [{"provider": "fixture"}],
                    }
                },
            },
            {
                "snapshot_type": "lhb",
                "symbol": "600000",
                "facts": {
                    "_uzi_raw_result": {
                        "data": {"lhb_count_30d": 0, "matched_youzi": []},
                        "sources": [{"provider": "fixture"}],
                    }
                },
            },
        ]
    )
    return rows


class _LiteAgentProvider:
    available = True

    def __init__(self, *, timeout: bool = False) -> None:
        self.settings = SimpleNamespace(
            uzi_llm_request_timeout_seconds=480,
            uzi_llm_max_concurrency=5,
        )
        self.timeout = timeout
        self.calls = 0
        self.user_prompts: list[str] = []
        self.reasoning_efforts: list[str | None] = []

    def parse(self, **kwargs):
        self.calls += 1
        self.user_prompts.append(str(kwargs.get("user_prompt") or ""))
        self.reasoning_efforts.append(kwargs.get("reasoning_effort"))
        if self.timeout:
            raise RuntimeError("llm_request_timeout: 480s")
        evidence_path = "raw.dimensions.0_basic.data.price"
        payload = {
            "agent_reviewed": True,
            "dim_commentary": {
                key: {
                    "text": f"{key} 基于当前 Lite 档位冻结证据完成核对，未使用任何模拟数据。",
                    "evidence_paths": [evidence_path],
                }
                for key in _LITE_DIMENSIONS
            },
            "panel_insights": "Lite 档位十位评委基于同一份冻结证据形成分歧，结论仅用于研究参考。",
            "panel_evidence_paths": ["panel.panel_consensus"],
            "narrative_override": {
                "core_conclusion": "基于当前冻结证据形成谨慎结论，真实风险和数据缺口必须继续保留。",
                "conclusion_evidence_paths": [evidence_path],
                "risks": [
                    {
                        "text": f"风险 {index} 仍需结合后续真实披露继续验证。",
                        "evidence_paths": [evidence_path],
                    }
                    for index in range(1, 4)
                ],
                "buy_zones": {
                    name: {
                        "price": 10.0,
                        "rationale": "仅使用冻结行情价格作为可审计计算基准。",
                        "basis_fields": [evidence_path],
                        "calculation": "冻结行情价格 10.0 乘以 1.0 得到 10.0。",
                    }
                    for name in ("value", "growth", "technical", "youzi")
                },
            },
            "data_gap_acknowledged": {},
        }
        schema = kwargs.get("schema")
        if schema is AgentDimensionPayload:
            return {
                "agent_reviewed": True,
                "dimensions": [
                    {
                        "dimension_id": key,
                        "commentary": commentary,
                    }
                    for key, commentary in payload["dim_commentary"].items()
                ],
                "data_gaps": [],
            }
        if schema is AgentNarrativePayload:
            return {
                "panel_insights": payload["panel_insights"],
                "panel_evidence_paths": payload["panel_evidence_paths"],
                "great_divide_override": payload.get("great_divide_override"),
                "narrative_override": payload["narrative_override"],
            }
        return payload


class Uzi392AnalysisGateTests(unittest.TestCase):
    def test_lite_profile_drives_native_panel_agent_schema_and_self_review(self) -> None:
        provider = _LiteAgentProvider()

        with patch.dict(os.environ, {"UZI_DEPTH": "medium", "UZI_LITE": "0"}):
            result = execute_native_analysis(
                _lite_snapshots(),
                ["600000"],
                profile="lite",
                llm_provider=provider,
            )

        analysis = result["results"][0]
        missing_dimensions = {
            str(issue.get("dim") or "")
            for issue in analysis["self_review"]["issues"]
            if "完全缺失" in str(issue.get("issue") or "")
        }
        self.assertEqual(missing_dimensions, set())
        self.assertEqual(len(analysis["panel"]["investors"]), 10)
        self.assertEqual(
            set(analysis["agent_analysis"]["dim_commentary"]),
            _LITE_DIMENSIONS,
        )
        self.assertEqual(analysis["self_review"]["profile"], "lite")
        self.assertEqual(
            set(analysis["self_review"]["enabled_dimensions"]),
            _LITE_DIMENSIONS,
        )
        self.assertTrue(
            all(
                str(task.get("dim") or "") in _LITE_DIMENSIONS
                for task in analysis["recovery_tasks"]
            )
        )
        self.assertEqual(
            set(analysis["integrity"]["profile_enabled_dimensions"]),
            _LITE_DIMENSIONS,
        )
        self.assertNotIn("great_divide_override", analysis["agent_analysis"])
        self.assertEqual(provider.calls, 2)
        self.assertEqual(provider.reasoning_efforts, ["low", "low"])

    def test_native_panel_obeys_all_profile_seat_counts(self) -> None:
        for profile, expected in (("lite", 10), ("medium", 51), ("deep", 66)):
            with self.subTest(profile=profile):
                result = execute_native_analysis(
                    _lite_snapshots(),
                    ["600000"],
                    profile=profile,
                    llm_provider=None,
                )
                panel = result["results"][0]["panel"]
                self.assertEqual(len(panel["investors"]), expected)
                self.assertEqual(
                    sum(panel["signal_distribution"].values()),
                    expected,
                )
                self.assertEqual(
                    sum(panel["vote_distribution"].values()),
                    expected,
                )

    def test_transport_timeout_is_not_retried_as_agent_content_repair(self) -> None:
        provider = _LiteAgentProvider(timeout=True)

        result = execute_native_analysis(
            _lite_snapshots(),
            ["600000"],
            profile="lite",
            llm_provider=provider,
        )

        analysis = result["results"][0]
        self.assertEqual(provider.calls, 1)
        self.assertEqual(len(analysis["recovery_history"]), 1)
        self.assertEqual(
            analysis["recovery_history"][0]["exhausted_reason"],
            "no_remaining_agent_recovery_action",
        )
        timeout_issues = [
            issue
            for issue in analysis["self_review"]["issues"]
            if issue.get("evidence") == "llm_transport_timeout"
        ]
        self.assertEqual(len(timeout_issues), 1)
        self.assertFalse(result["report_allowed"])

    def test_medium_transport_timeout_does_not_retry_for_downstream_qualitative_stage(self) -> None:
        provider = _LiteAgentProvider(timeout=True)

        result = execute_native_analysis(
            _lite_snapshots(),
            ["600000"],
            profile="medium",
            llm_provider=provider,
        )

        analysis = result["results"][0]
        self.assertEqual(provider.calls, 1)
        self.assertEqual(provider.reasoning_efforts, ["high"])
        self.assertEqual(len(analysis["recovery_history"]), 1)
        self.assertEqual(
            analysis["recovery_history"][0]["exhausted_reason"],
            "no_remaining_agent_recovery_action",
        )

    def test_enabled_empty_dimension_remains_a_non_bypassable_critical(self) -> None:
        rows = _lite_snapshots()
        governance = next(
            row for row in rows if row.get("snapshot_type") == "governance"
        )
        governance["facts"]["_uzi_raw_result"]["data"] = {}

        with patch.dict(os.environ, {"UZI_DEPTH": "deep", "UZI_SKIP_REVIEW": "1"}):
            result = execute_native_analysis(
                rows,
                ["600000"],
                profile="lite",
                llm_provider=_LiteAgentProvider(),
            )

        issues = result["results"][0]["self_review"]["issues"]
        governance_issues = [
            issue
            for issue in issues
            if issue.get("dim") == "11_governance"
            and issue.get("severity") == "critical"
        ]
        self.assertTrue(governance_issues)
        self.assertFalse(result["report_allowed"])

    def test_missing_agent_stage_stops_at_critical_gate_without_report_permission(self) -> None:
        result = execute_native_analysis(
            _snapshots(),
            ["600000"],
            profile="lite",
            llm_provider=None,
        )

        self.assertGreater(result["critical_count"], 0)
        self.assertFalse(result["report_allowed"])
        self.assertEqual(result["quality_status"], "quality_gate_blocked")
        self.assertEqual(
            result["recovery_history"][-1]["exhausted_reason"],
            "no_remaining_agent_recovery_action",
        )

    def test_environment_cannot_bypass_native_self_review(self) -> None:
        with patch.dict(os.environ, {"UZI_SKIP_REVIEW": "1"}):
            result = execute_native_analysis(
                _snapshots(),
                ["600000"],
                profile="lite",
                llm_provider=None,
            )

        self.assertGreater(result["critical_count"], 0)
        self.assertFalse(result["report_allowed"])
        self.assertEqual(result["quality_status"], "quality_gate_blocked")

    def test_profile_is_an_explicit_user_choice(self) -> None:
        for profile in ("", "mid", "automatic"):
            with (
                self.subTest(profile=profile),
                self.assertRaisesRegex(ValueError, "research_profile is required"),
            ):
                execute_native_analysis(
                    _snapshots(),
                    ["600000"],
                    profile=profile,
                    llm_provider=None,
                )

    def test_plain_and_exchange_suffixed_symbols_are_analyzed_once(self) -> None:
        rows = _snapshots()
        rows[0]["symbol"] = "600000.SH"

        result = execute_native_analysis(
            rows,
            ["600000.SH", "600000"],
            profile="lite",
            llm_provider=None,
        )

        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["raw"]["ticker"], "600000.SH")

    def test_agent_error_detail_is_preserved_without_credentials(self) -> None:
        detail = _safe_error_detail(
            RuntimeError("llm_request_timeout: 25s token=super-secret")
        )

        self.assertIn("llm_request_timeout: 25s", detail)
        self.assertNotIn("super-secret", detail)
        self.assertIn("<redacted>", detail)

    def test_model_dot_index_paths_are_canonicalized_before_grounding(self) -> None:
        self.assertEqual(
            _canonical_evidence_path(
                "raw.dimensions.1_financials.data.revenue_history.5"
            ),
            "raw.dimensions.1_financials.data.revenue_history[5]",
        )
        self.assertEqual(
            _canonical_evidence_path("recovery_tasks.0.suggested_actions.2"),
            "recovery_tasks[0].suggested_actions[2]",
        )
        self.assertEqual(
            _canonical_evidence_path("0_basic.data.price"),
            "raw.dimensions.0_basic.data.price",
        )
        self.assertEqual(
            _canonical_evidence_path("dimensions.10_valuation.data.pe"),
            "raw.dimensions.10_valuation.data.pe",
        )
        self.assertEqual(
            _canonical_evidence_path(
                "raw.dimensions.10_valuation_models.data.dcf.intrinsic_per_share"
            ),
            "raw.dimensions.20_valuation_models.data.dcf.intrinsic_per_share",
        )
        self.assertEqual(
            _canonical_evidence_path("20_valuation_models.data.dcf.safety_margin_pct"),
            "raw.dimensions.20_valuation_models.data.dcf.safety_margin_pct",
        )
        self.assertEqual(
            _canonical_evidence_path("2_kline.data.indicators.ma_align"),
            "raw.dimensions.2_kline.data.ma_align",
        )

    def test_real_container_evidence_paths_expand_to_frozen_leaf_paths(self) -> None:
        payload = AgentAnalysisPayload.model_validate(
            _LiteAgentProvider().parse()
        )
        payload.dim_commentary["0_basic"].evidence_paths = [
            "raw.dimensions.0_basic.data.history"
        ]

        _normalize_payload_paths(
            payload,
            allowed_paths={
                "raw.dimensions.0_basic.data.history[0].date",
                "raw.dimensions.0_basic.data.history[0].value",
                "raw.dimensions.0_basic.data.price",
            },
        )

        self.assertEqual(
            payload.dim_commentary["0_basic"].evidence_paths,
            [
                "raw.dimensions.0_basic.data.history[0].date",
                "raw.dimensions.0_basic.data.history[0].value",
            ],
        )


if __name__ == "__main__":
    unittest.main()
