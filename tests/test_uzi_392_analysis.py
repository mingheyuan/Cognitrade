from __future__ import annotations

import importlib
import json
import os
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from time import sleep
from types import SimpleNamespace
from unittest.mock import patch

from jsonschema import Draft202012Validator

from domain_packs.finance_ashare.subagents.uzi_392_analysis import (
    AgentAnalysisPayload,
    AgentBuyZonePayload,
    AgentDimensionPayload,
    AgentNarrativeCorePayload,
    _apply_scoring_exclusions,
    _canonical_evidence_path,
    _deep_methods_compatible_raw,
    _initialize_native_imports,
    _normalize_evidence_paths,
    _normalize_payload_paths,
    _profile_empty_dimension_issues,
    _safe_error_detail,
    _self_review_compatible_raw,
    _snapshot_rows_for_symbol,
    _workflow_dimension_keys,
    execute_native_analysis,
)
from domain_packs.finance_ashare.vendor.uzi_3_9_2.runtime import Uzi392FetcherRuntime
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
            uzi_llm_max_concurrency=2,
        )
        self.timeout = timeout
        self.calls = 0
        self.system_prompts: list[str] = []
        self.user_prompts: list[str] = []
        self.reasoning_efforts: list[str | None] = []

    def parse(self, **kwargs):
        self.calls += 1
        self.system_prompts.append(str(kwargs.get("system_prompt") or ""))
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
            requested_match = re.search(
                r"必须且只能是：([^；]+)；",
                str(kwargs.get("user_prompt") or ""),
            )
            requested_dimensions = (
                requested_match.group(1).split(",")
                if requested_match is not None
                else sorted(_LITE_DIMENSIONS)
            )
            return {
                "agent_reviewed": True,
                "dimensions": [
                    {
                        "dimension_id": key,
                        "commentary": {
                            "text": f"{key} 基于当前档位冻结证据完成核对，未使用任何模拟数据。",
                            "evidence_paths": [evidence_path],
                        },
                    }
                    for key in requested_dimensions
                ],
                "data_gaps": [],
            }
        if schema is AgentNarrativeCorePayload:
            return {
                "panel_insights": payload["panel_insights"],
                "panel_evidence_paths": payload["panel_evidence_paths"],
                "great_divide_override": payload.get("great_divide_override"),
                "core_conclusion": payload["narrative_override"]["core_conclusion"],
                "conclusion_evidence_paths": payload["narrative_override"][
                    "conclusion_evidence_paths"
                ],
                "risks": payload["narrative_override"]["risks"],
            }
        if schema is AgentBuyZonePayload:
            prompt = str(kwargs.get("user_prompt") or "")
            zone_name = next(
                name
                for name in ("value", "growth", "technical", "youzi")
                if f" {name} 买入区间" in prompt
            )
            return {
                "zone": payload["narrative_override"]["buy_zones"][zone_name],
            }
        return payload


class _RepairingEvidenceProvider(_LiteAgentProvider):
    def __init__(self) -> None:
        super().__init__()
        self.dimension_calls = 0

    def parse(self, **kwargs):
        result = super().parse(**kwargs)
        if kwargs.get("schema") is AgentDimensionPayload:
            self.dimension_calls += 1
            if self.dimension_calls == 1:
                result["dimensions"][0]["commentary"]["evidence_paths"] = [
                    "raw.dimensions.0_basic.data.price[0]"
                ]
        return result


class _RepairingBuyZoneProvider(_LiteAgentProvider):
    def __init__(self) -> None:
        super().__init__()
        self.youzi_calls = 0

    def parse(self, **kwargs):
        result = super().parse(**kwargs)
        if (
            kwargs.get("schema") is AgentBuyZonePayload
            and " youzi 买入区间" in str(kwargs.get("user_prompt") or "")
        ):
            self.youzi_calls += 1
            if self.youzi_calls == 1:
                result["zone"]["basis_fields"] = [
                    "raw.dimensions.1_financials.data.revenue_history[0]"
                ]
        return result


class _RepairingNarrativeProvider(_LiteAgentProvider):
    def __init__(self) -> None:
        super().__init__()
        self.narrative_calls = 0

    def parse(self, **kwargs):
        result = super().parse(**kwargs)
        if kwargs.get("schema") is AgentNarrativeCorePayload:
            self.narrative_calls += 1
            if self.narrative_calls == 1:
                result["panel_evidence_paths"] = [
                    "panel.consensus_final"
                ]
        return result


class _ConcurrencyProvider(_LiteAgentProvider):
    def __init__(self) -> None:
        super().__init__()
        self._active_lock = Lock()
        self.active = 0
        self.max_active = 0

    def parse(self, **kwargs):
        with self._active_lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            sleep(0.02)
            return super().parse(**kwargs)
        finally:
            with self._active_lock:
                self.active -= 1


class Uzi392AnalysisGateTests(unittest.TestCase):
    def test_terminal_historical_gap_has_zero_weight_and_remains_disclosed(self) -> None:
        _initialize_native_imports()
        raw = {
            "ticker": "600000",
            "dimensions": {
                "1_financials": {
                    "data": {
                        "roe": 12,
                        "roe_history": [10, 11, 12],
                        "revenue_history": [100, 110],
                    }
                },
                "12_capital_flow": {
                    "data": {},
                    "error": "historical_reconstruction_no_proof",
                    "_excluded_from_scoring": True,
                    "_degradation": {
                        "reason_code": "historical_reconstruction_no_proof",
                        "message": "目标日没有可验证记录",
                    },
                },
            },
        }
        score_fns = importlib.import_module("lib.pipeline.score_fns")

        scored = _apply_scoring_exclusions(score_fns.score_dimensions(raw), raw)

        excluded = scored["dimensions"]["12_capital_flow"]
        self.assertEqual(excluded["weight"], 0)
        self.assertEqual(excluded["scoring_status"], "excluded")
        self.assertEqual(
            scored["scoring_coverage"]["excluded_dimensions"],
            ["12_capital_flow"],
        )

    def test_terminal_historical_gap_degrades_but_does_not_block_report(self) -> None:
        rows = [row for row in _lite_snapshots() if row["snapshot_type"] != "lhb"]
        rows.append(
            {
                "snapshot_type": "lhb",
                "symbol": "600000",
                "quality_status": "data_gap",
                "facts": {"availability": "historically_unavailable"},
                "query_manifest": {
                    "degradation": {
                        "safe_for_report": True,
                        "excluded_from_scoring": True,
                        "reason_code": "historical_reconstruction_no_proof",
                        "message": "目标日没有可验证龙虎榜记录",
                    }
                },
            }
        )

        result = execute_native_analysis(
            rows,
            ["600000"],
            profile="lite",
            llm_provider=_LiteAgentProvider(),
        )

        self.assertTrue(result["report_allowed"])
        self.assertEqual(result["quality_status"], "degraded")
        stock = result["results"][0]
        self.assertEqual(stock["quality_status"], "degraded")
        self.assertEqual(stock["dimensions"]["dimensions"]["16_lhb"]["weight"], 0)
        disclosures = stock["synthesis"]["data_gaps"]["tasks"]
        self.assertTrue(
            any(
                row.get("dim") == "16_lhb"
                and row.get("status") == "excluded_from_scoring"
                for row in disclosures
            )
        )

    def test_independent_agent_stages_honor_configured_parallelism(self) -> None:
        provider = _ConcurrencyProvider()

        result = execute_native_analysis(
            _lite_snapshots(),
            ["600000"],
            profile="lite",
            llm_provider=provider,
        )

        self.assertTrue(result["report_allowed"])
        self.assertEqual(provider.max_active, 2)

    def test_full_agent_workflow_expands_lite_to_all_dimensions_and_hard_gates(self) -> None:
        self.assertEqual(len(_workflow_dimension_keys("lite", full_agent_workflow=True)), 20)

        result = execute_native_analysis(
            _lite_snapshots(),
            ["600000"],
            profile="lite",
            llm_provider=None,
            full_agent_workflow=True,
        )

        analysis = result["results"][0]
        self.assertEqual(result["agent_workflow_mode"], "full")
        self.assertFalse(result["report_allowed"])
        self.assertTrue(analysis["roleplay"]["required"])
        self.assertTrue(analysis["qualitative_execution"]["required"])
        self.assertEqual(len(analysis["self_review"]["enabled_dimensions"]), 20)

    def test_nullable_moat_scores_are_normalized_only_for_deep_methods(self) -> None:
        raw = {"dimensions": {"14_moat": {"data": {"scores": None}}}}

        compatible = _deep_methods_compatible_raw(raw)

        self.assertIsNone(raw["dimensions"]["14_moat"]["data"]["scores"])
        self.assertEqual(
            compatible["dimensions"]["14_moat"]["data"]["scores"],
            {},
        )

    def test_profile_empty_check_includes_native_number_six_bonus_dimension(self) -> None:
        issues = _profile_empty_dimension_issues(
            {
                "dimensions": {
                    "6_research": {"data": {"reports": ["ok"]}},
                    "6_fund_holders": {"data": {}, "error": "provider failed"},
                }
            },
            enabled_dimension_keys={"6_research"},
        )

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["dim"], "6_fund_holders")
        self.assertEqual(issues[0]["severity"], "warning")

    def test_lite_analysis_accepts_nullable_optional_moat_scores(self) -> None:
        rows = _lite_snapshots()
        rows.append(
            {
                "snapshot_type": "moat",
                "symbol": "600000",
                "facts": {
                    "_uzi_raw_result": {
                        "data": {"scores": None},
                        "sources": [{"provider": "fixture"}],
                    }
                },
            }
        )

        result = execute_native_analysis(
            rows,
            ["600000"],
            profile="lite",
            llm_provider=_LiteAgentProvider(),
        )

        analysis = result["results"][0]
        self.assertIsNone(
            analysis["raw"]["dimensions"]["14_moat"]["data"]["scores"]
        )
        self.assertNotIn("computation_error", analysis)
        self.assertTrue(result["report_allowed"])

    def test_multi_symbol_qualitative_rows_are_isolated(self) -> None:
        rows = [
            {
                "snapshot_type": "policy",
                "symbol": "600519",
                "facts": {"evidence": [{"url": "https://example.test/maotai"}]},
            },
            {
                "snapshot_type": "policy",
                "symbol": "000858.SZ",
                "facts": {"evidence": [{"url": "https://example.test/wuliangye"}]},
            },
            {
                "snapshot_type": "macro",
                "symbol": None,
                "facts": {"evidence": [{"url": "https://example.test/global"}]},
            },
        ]

        selected = _snapshot_rows_for_symbol(rows, "600519.SH")

        self.assertEqual(
            [row.get("symbol") for row in selected],
            ["600519", None],
        )
        self.assertNotIn("wuliangye", str(selected))

    def test_multi_symbol_analysis_keeps_success_when_a_peer_pipeline_fails(self) -> None:
        successful = {
            "symbol": "600519",
            "raw": {"ticker": "600519.SH", "dimensions": {}},
            "self_review": {
                "critical_count": 0,
                "warning_count": 0,
                "issues": [],
            },
            "report_allowed": True,
            "recovery_history": [],
        }
        with patch(
            "domain_packs.finance_ashare.subagents.uzi_392_analysis._execute_symbol",
            side_effect=[successful, RuntimeError("https://provider.test?q=1&token=secret")],
        ):
            result = execute_native_analysis(
                [],
                ["600519", "000858"],
                profile="lite",
                llm_provider=None,
            )

        self.assertEqual(len(result["results"]), 2)
        self.assertTrue(result["results"][0]["report_allowed"])
        self.assertFalse(result["results"][1]["report_allowed"])
        self.assertEqual(result["results"][1]["self_review"]["critical_count"], 1)
        self.assertNotIn("secret", str(result["results"][1]))
        self.assertEqual(result["quality_status"], "degraded")
        self.assertFalse(result["report_allowed"])
        schema = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "domain_packs/finance_ashare/artifact_schemas/uzi_native_analysis.json"
            ).read_text(encoding="utf-8")
        )
        Draft202012Validator(schema).validate(result)

    def test_multi_root_collection_failure_skips_native_analysis(self) -> None:
        snapshots = [{
            "snapshot_type": "capital_flow",
            "symbol": "000858",
            "facts": {
                "availability": "data_gap",
                "_uzi_symbol_route_failed": True,
            },
            "quality_status": "data_gap",
        }]
        with patch(
            "domain_packs.finance_ashare.subagents.uzi_392_analysis._execute_symbol",
        ) as execute_symbol:
            result = execute_native_analysis(
                snapshots,
                ["000858"],
                profile="lite",
                llm_provider=None,
            )

        execute_symbol.assert_not_called()
        failed = result["results"][0]
        self.assertFalse(failed["report_allowed"])
        self.assertEqual(failed["computation_error"], "RootCollectionError: analysis_unavailable")
        self.assertEqual(result["quality_status"], "quality_gate_blocked")

    def test_multi_symbol_recovery_history_is_scoped_and_not_duplicated(self) -> None:
        received: dict[str, list[dict]] = {}

        def execute_symbol(_rows, symbol, **kwargs):
            received[symbol] = list(kwargs["recovery_history"])
            return {
                "symbol": symbol,
                "raw": {"ticker": symbol, "dimensions": {}},
                "self_review": {"critical_count": 0, "warning_count": 0, "issues": []},
                "report_allowed": True,
                "recovery_history": [
                    *kwargs["recovery_history"],
                    {"stage": "self_review", "symbol": symbol, "status": "passed"},
                ],
            }

        source_history = [
            {"stage": "global", "status": "completed"},
            {"stage": "fetch", "symbol": "600519.SH", "status": "recovered"},
            {"stage": "fetch", "symbol": "000858.SZ", "status": "recovered"},
        ]
        with patch(
            "domain_packs.finance_ashare.subagents.uzi_392_analysis._execute_symbol",
            side_effect=execute_symbol,
        ):
            result = execute_native_analysis(
                [],
                ["600519", "000858"],
                profile="lite",
                llm_provider=None,
                recovery_history=source_history,
            )

        self.assertEqual(
            received["600519"],
            [{"stage": "fetch", "symbol": "600519.SH", "status": "recovered"}],
        )
        self.assertEqual(
            received["000858"],
            [{"stage": "fetch", "symbol": "000858.SZ", "status": "recovered"}],
        )
        self.assertEqual(
            sum(1 for row in result["recovery_history"] if row["stage"] == "global"),
            1,
        )
        self.assertEqual(len(result["recovery_history"]), 5)

    def test_self_review_view_normalizes_nullable_strings_and_dcf_key_only(self) -> None:
        raw = {
            "dimensions": {
                "0_basic": {"data": {"industry": None}},
                "7_industry": {
                    "data": {"cninfo_metrics": {"industry_name_match": None}}
                },
                "20_valuation_models": {
                    "data": {"dcf": {"intrinsic_per_share": 42.5}}
                },
            }
        }

        compatible = _self_review_compatible_raw(raw)

        self.assertIsNone(raw["dimensions"]["0_basic"]["data"]["industry"])
        self.assertEqual(compatible["dimensions"]["0_basic"]["data"]["industry"], "")
        self.assertEqual(
            compatible["dimensions"]["7_industry"]["data"]["cninfo_metrics"][
                "industry_name_match"
            ],
            "",
        )
        self.assertEqual(
            compatible["dimensions"]["20_valuation_models"]["data"]["dcf"][
                "intrinsic_value_per_share"
            ],
            42.5,
        )
        self.assertNotIn(
            "intrinsic_value_per_share",
            raw["dimensions"]["20_valuation_models"]["data"]["dcf"],
        )
        Uzi392FetcherRuntime(
            workspace_root=Path(__file__).resolve().parents[1] / "workspace"
        )
        self_review = importlib.import_module("lib.self_review")
        ctx = {
            "raw": compatible,
            "dims": compatible["dimensions"],
            "syn": {},
            "panel": {},
            "market": "A",
        }
        self.assertEqual(self_review.check_industry_mapping_sanity(ctx), [])
        self.assertEqual(self_review.check_metals_materials_populated(ctx), [])
        self.assertFalse(
            any(
                "DCF 内在价值" in issue.issue
                for issue in self_review.check_valuation_sanity(ctx)
            )
        )

    def test_unknown_dimension_evidence_retries_only_the_failed_stage(self) -> None:
        provider = _RepairingEvidenceProvider()

        result = execute_native_analysis(
            _lite_snapshots(),
            ["600000"],
            profile="lite",
            llm_provider=provider,
        )

        self.assertTrue(result["report_allowed"])
        self.assertEqual(provider.dimension_calls, 3)
        self.assertTrue(
            any(
                "MECHANICAL_EVIDENCE_REPAIR=" in prompt
                for prompt in provider.user_prompts
            )
        )

    def test_unknown_buy_zone_evidence_retries_only_the_failed_stage(self) -> None:
        provider = _RepairingBuyZoneProvider()
        progress: list[dict] = []

        result = execute_native_analysis(
            _lite_snapshots(),
            ["600000"],
            profile="lite",
            llm_provider=provider,
            progress_callback=progress.append,
        )

        self.assertTrue(result["report_allowed"])
        self.assertEqual(provider.calls, 8)
        self.assertEqual(provider.youzi_calls, 2)
        self.assertEqual(
            sum(
                row.get("stage") == "buy_zone_youzi_repair"
                for row in progress
            ),
            1,
        )

    def test_unknown_narrative_evidence_retries_only_narrative_stage(self) -> None:
        provider = _RepairingNarrativeProvider()
        progress: list[dict] = []

        result = execute_native_analysis(
            _lite_snapshots(),
            ["600000"],
            profile="lite",
            llm_provider=provider,
            progress_callback=progress.append,
        )

        self.assertTrue(result["report_allowed"])
        self.assertEqual(provider.calls, 8)
        self.assertEqual(provider.narrative_calls, 2)
        self.assertEqual(
            sum(
                row.get("stage") == "narrative_core_repair"
                for row in progress
            ),
            1,
        )
        self.assertTrue(
            any(
                "MECHANICAL_EVIDENCE_REPAIR=" in prompt
                for prompt in provider.user_prompts
            )
        )

    def test_validated_agent_stages_resume_from_persistent_cache(self) -> None:
        snapshots = _lite_snapshots()
        with TemporaryDirectory() as temporary:
            first_provider = _LiteAgentProvider()
            first_provider.settings.workspace_root = Path(temporary)
            first = execute_native_analysis(
                snapshots,
                ["600000"],
                profile="lite",
                llm_provider=first_provider,
            )
            second_provider = _LiteAgentProvider()
            second_provider.settings.workspace_root = Path(temporary)
            second = execute_native_analysis(
                snapshots,
                ["600000"],
                profile="lite",
                llm_provider=second_provider,
            )

        self.assertTrue(first["report_allowed"])
        self.assertTrue(second["report_allowed"])
        self.assertGreater(first_provider.calls, 0)
        self.assertEqual(second_provider.calls, 0)

    def test_lite_profile_drives_native_panel_agent_schema_and_self_review(self) -> None:
        provider = _LiteAgentProvider()
        progress: list[dict] = []

        with patch.dict(os.environ, {"UZI_DEPTH": "medium", "UZI_LITE": "0"}):
            result = execute_native_analysis(
                _lite_snapshots(),
                ["600000"],
                profile="lite",
                llm_provider=provider,
                progress_callback=progress.append,
            )

        analysis = result["results"][0]
        missing_dimensions = {
            str(issue.get("dim") or "")
            for issue in analysis["self_review"]["issues"]
            if "完全缺失" in str(issue.get("issue") or "")
        }
        self.assertEqual(missing_dimensions, set())
        self.assertEqual(len(analysis["panel"]["investors"]), 66)
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
        self.assertEqual(provider.calls, 7)
        self.assertEqual(provider.reasoning_efforts, ["low"] * 7)
        self.assertNotIn('"panel":', provider.system_prompts[0])
        self.assertIn(
            "cited_real_evidence_values_by_original_path",
            provider.system_prompts[-5],
        )
        self.assertIn(
            "ALLOWED_NUMERIC_BASIS_VALUES",
            provider.system_prompts[-4],
        )
        self.assertIn(
            "raw.dimensions.1_financials.",
            provider.system_prompts[-4],
        )
        self.assertNotIn(
            "raw.dimensions.16_lhb.",
            provider.system_prompts[-4],
        )
        self.assertIn(
            "raw.dimensions.16_lhb.",
            provider.system_prompts[-2],
        )
        self.assertNotIn(
            "raw.dimensions.1_financials.",
            provider.system_prompts[-2],
        )
        self.assertLess(max(map(len, provider.system_prompts)), 100_000)
        self.assertTrue(any(row.get("stage") == "dimensions_1_5" for row in progress))
        self.assertTrue(any(row.get("stage") == "self_review" for row in progress))
        self.assertTrue(all(0 <= int(row.get("progress_percent") or 0) <= 100 for row in progress))

    def test_school_lock_applies_to_native_panel_synthesis_and_agent_prompt(self) -> None:
        provider = _LiteAgentProvider()

        result = execute_native_analysis(
            _lite_snapshots(),
            ["600000"],
            profile="lite",
            school="A",
            llm_provider=provider,
        )

        analysis = result["results"][0]
        self.assertEqual(result["school"], "A")
        self.assertEqual(analysis["synthesis"]["school_lock"], {"group": "A", "label": "价值派"})
        self.assertTrue(all(
            row.get("signal") == "skip"
            for row in analysis["panel"]["investors"]
            if row.get("group") != "A"
        ))
        self.assertTrue(all("UZI_SCHOOL=A" in prompt for prompt in provider.system_prompts))

    def test_native_panel_obeys_all_profile_seat_counts(self) -> None:
        for profile, expected in (("lite", 66), ("medium", 66), ("deep", 66)):
            with self.subTest(profile=profile):
                result = execute_native_analysis(
                    _lite_snapshots(),
                    ["600000"],
                    profile=profile,
                    llm_provider=None,
                )
                panel = result["results"][0]["panel"]
                self.assertEqual(len(panel["investors"]), expected)
                short_total = panel["short_consensus"]["total"]
                self.assertEqual(short_total, 2)
                self.assertEqual(
                    sum(panel["signal_distribution"].values()) + short_total,
                    expected,
                )
                self.assertEqual(
                    sum(panel["vote_distribution"].values()) + short_total,
                    expected,
                )

    def test_lite_transport_timeout_is_a_native_warning_without_content_retry(self) -> None:
        provider = _LiteAgentProvider(timeout=True)

        result = execute_native_analysis(
            _lite_snapshots(),
            ["600000"],
            profile="lite",
            llm_provider=provider,
        )

        analysis = result["results"][0]
        self.assertGreaterEqual(provider.calls, 1)
        self.assertLessEqual(
            provider.calls,
            provider.settings.uzi_llm_max_concurrency,
        )
        self.assertEqual(len(analysis["recovery_history"]), 1)
        self.assertEqual(analysis["recovery_history"][0]["status"], "passed")
        self.assertTrue(result["report_allowed"])
        timeout_issues = [
            issue
            for issue in analysis["self_review"]["issues"]
            if issue.get("evidence") == "llm_transport_timeout"
        ]
        self.assertEqual(len(timeout_issues), 1)
        self.assertEqual(timeout_issues[0]["severity"], "warning")

    def test_medium_transport_timeout_does_not_retry_for_downstream_qualitative_stage(self) -> None:
        provider = _LiteAgentProvider(timeout=True)

        result = execute_native_analysis(
            _lite_snapshots(),
            ["600000"],
            profile="medium",
            llm_provider=provider,
        )

        analysis = result["results"][0]
        self.assertGreaterEqual(provider.calls, 1)
        self.assertLessEqual(
            provider.calls,
            provider.settings.uzi_llm_max_concurrency,
        )
        self.assertTrue(all(effort == "high" for effort in provider.reasoning_efforts))
        self.assertEqual(len(analysis["recovery_history"]), 1)
        self.assertEqual(
            analysis["recovery_history"][0]["exhausted_reason"],
            "no_remaining_agent_recovery_action",
        )

    def test_qualitative_repair_reuses_validated_agent_analysis(self) -> None:
        provider = _LiteAgentProvider()
        qualitative_gap = {
            "qualitative_agent_execution": {
                "required": True,
                "status": "incomplete",
                "agent_reviewed": False,
                "errors": [
                    "B · Industry-Events:ValueError:qualitative evidence URL not in frozen bundle"
                ],
            },
            "qualitative_deep_dive": {},
        }
        qualitative_complete = {
            "qualitative_agent_execution": {
                "required": True,
                "status": "completed",
                "agent_reviewed": True,
            },
            "qualitative_deep_dive": {},
        }
        qualitative_critical = {
            "severity": "critical",
            "category": "agent-os-boundary",
            "dim": "qualitative_deep_dive",
            "issue": "六维定性研究未满足 UZI 证据门",
            "evidence": "status=incomplete",
            "suggested_fix": "完成三组分析后重算",
        }
        reviews = [
            {
                "issues": [qualitative_critical],
                "critical_count": 1,
                "warning_count": 0,
                "info_count": 0,
            },
            {
                "issues": [],
                "critical_count": 0,
                "warning_count": 0,
                "info_count": 0,
            },
        ]

        with (
            patch(
                "domain_packs.finance_ashare.subagents.uzi_392_analysis.apply_qualitative_deep_dive",
                side_effect=[qualitative_gap, qualitative_complete],
            ) as qualitative_mock,
            patch(
                "domain_packs.finance_ashare.subagents.uzi_392_analysis.run_native_self_review",
                side_effect=reviews,
            ),
            patch(
                "domain_packs.finance_ashare.subagents.uzi_392_analysis._validate_agent_analysis",
                return_value=[],
            ),
        ):
            result = execute_native_analysis(
                _lite_snapshots(),
                ["600000"],
                profile="medium",
                llm_provider=provider,
            )

        self.assertEqual(provider.calls, 9)
        self.assertEqual(qualitative_mock.call_count, 2)
        self.assertEqual(
            qualitative_mock.call_args_list[1].kwargs["repair_feedback"],
            ["B · Industry-Events:ValueError:qualitative evidence URL not in frozen bundle"],
        )
        history = result["results"][0]["recovery_history"]
        self.assertFalse(history[0]["reused_agent_analysis"])
        self.assertTrue(history[1]["reused_agent_analysis"])
        self.assertTrue(result["report_allowed"])

    def test_content_repair_continues_when_the_exact_critical_changes(self) -> None:
        def critical(issue: str) -> dict[str, object]:
            return {
                "severity": "critical",
                "category": "agent-os-boundary",
                "dim": "agent_analysis",
                "issue": issue,
                "evidence": "agent_content_invalid",
                "suggested_fix": "按自审问题修正结构化 Agent 分析后重算",
            }

        reviews = [
            {"issues": [critical("unknown evidence path A")], "critical_count": 1, "warning_count": 0, "info_count": 0},
            {"issues": [critical("unknown evidence path B")], "critical_count": 1, "warning_count": 0, "info_count": 0},
            {"issues": [], "critical_count": 0, "warning_count": 0, "info_count": 0},
        ]
        with (
            patch(
                "domain_packs.finance_ashare.subagents.uzi_392_analysis._generate_agent_analysis",
                return_value=None,
            ) as generate_mock,
            patch(
                "domain_packs.finance_ashare.subagents.uzi_392_analysis.run_native_self_review",
                side_effect=reviews,
            ),
        ):
            result = execute_native_analysis(
                _lite_snapshots(),
                ["600000"],
                profile="lite",
                llm_provider=_LiteAgentProvider(),
            )

        self.assertEqual(generate_mock.call_count, 3)
        self.assertEqual(len(result["results"][0]["recovery_history"]), 3)
        self.assertTrue(result["report_allowed"])

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

    def test_multiple_paths_in_one_model_element_are_split_only_when_grounded(self) -> None:
        allowed = {
            "raw.dimensions.0_basic.data.market_overview.latest_bar.close",
            "raw.dimensions.0_basic.data.market_overview.valuation.pe",
        }

        normalized = _normalize_evidence_paths(
            [
                "raw.dimensions.0_basic.data.market_overview.latest_bar、"
                "raw.dimensions.0_basic.data.market_overview.valuation"
            ],
            allowed_paths=allowed,
        )

        self.assertEqual(normalized, sorted(allowed))

    def test_expanded_evidence_paths_respect_the_destination_contract_limit(self) -> None:
        allowed = {
            f"panel.school_scores.group_{index}.consensus"
            for index in range(12)
        }

        normalized = _normalize_evidence_paths(
            ["panel.school_scores"],
            allowed_paths=allowed,
            max_items=10,
        )

        self.assertEqual(normalized, sorted(allowed)[:10])

    def test_explanatory_suffix_after_exact_leaf_path_is_removed(self) -> None:
        payload = AgentAnalysisPayload.model_validate(
            _LiteAgentProvider().parse()
        )
        payload.panel_evidence_paths = [
            "panel.signal_distribution.bearish characteristic path unavailable; corrected below"
        ]

        _normalize_payload_paths(
            payload,
            allowed_paths={
                "panel.signal_distribution.bearish",
                "raw.dimensions.0_basic.data.price",
            },
        )

        self.assertEqual(
            payload.panel_evidence_paths,
            ["panel.signal_distribution.bearish"],
        )

    def test_non_path_suffix_after_exact_leaf_path_is_removed(self) -> None:
        normalized = _normalize_evidence_paths(
            ["panel.school_scores.C.verdictลง"],
            allowed_paths={"panel.school_scores.C.verdict"},
        )

        self.assertEqual(normalized, ["panel.school_scores.C.verdict"])

    def test_unknown_child_path_is_not_silently_rewritten(self) -> None:
        payload = AgentAnalysisPayload.model_validate(
            _LiteAgentProvider().parse()
        )
        payload.panel_evidence_paths = [
            "panel.signal_distribution.bearish.unavailable"
        ]

        _normalize_payload_paths(
            payload,
            allowed_paths={"panel.signal_distribution.bearish"},
        )

        self.assertEqual(
            payload.panel_evidence_paths,
            ["panel.signal_distribution.bearish.unavailable"],
        )


if __name__ == "__main__":
    unittest.main()
