from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.agents.reporter.service import ReporterService
from research_system.runtime.history.session.session_history import SessionHistory
from research_system.runtime.providers.provider_registry import LLMProviderError


class ReporterProviderUnavailableStub:
    available = False


class ReporterLLMErrorStub:
    available = True

    def parse(self, **kwargs):
        raise LLMProviderError("simulated llm error")


class ReporterUnexpectedLLMStub:
    available = True

    def parse(self, **kwargs):
        raise AssertionError("LLM should not be called for deterministic bank compare reporting")


def _screening_evidences() -> list[dict]:
    return [
        {
            "evidence_id": "screening-scorecard",
            "title": "screening_scorecard",
            "summary": (
                "screening_scorecard\n"
                "{'investment_cycle': 'mid_term', 'candidate_count': 1, "
                "'top_candidate_count': 1, 'deep_checked_count': 1, "
                "'summary': 'mid_term candidates ready'}"
            ),
            "metadata": {"source_type": "screening_scorecard"},
            "source_refs": [],
        },
        {
            "evidence_id": "screen-candidate-1",
            "title": "screen_candidate:000001",
            "summary": (
                "screen_candidate symbol=000001 name=PingAn\n"
                "score=2.0 latest_pct_change=1.2\n"
                "latest_amount=1000000000 notice_count=1\n"
                "deep_checked=True\n"
                "why=leader\n"
                "risk=none"
            ),
            "metadata": {"source_type": "screen_candidate"},
            "source_refs": [],
        },
        {
            "evidence_id": "method-1",
            "title": "comps:000001",
            "summary": (
                "methodology_result type=comps symbol=000001\n"
                "valuation_band={'low': 10.0, 'base': 11.0, 'high': 12.0}\n"
                "outputs={'relative_view': 'relative_discount_like'}"
            ),
            "metadata": {
                "source_type": "methodology_comps",
                "methodology_type": "comps",
                "valuation_band_label": "10.0|11.0|12.0",
            },
            "source_refs": [],
        },
    ]


def _bank_compare_evidences() -> list[dict]:
    return [
        {
            "evidence_id": "compare-request",
            "title": "stock_compare input request",
            "summary": (
                "stock_compare input request\n"
                "{'compare_targets': ["
                "{'symbol': '600000', 'name': 'AAA'}, "
                "{'symbol': '600001', 'name': 'BBB'}]}"
            ),
            "metadata": {"source_type": "compare_request"},
            "source_refs": [],
        },
        {
            "evidence_id": "bank-scorecard",
            "title": "bank_compare_scorecard",
            "summary": (
                "bank_compare_scorecard\n"
                "{'candidate_count': 2, "
                "'ranked_candidates': ["
                "{'symbol': '600000', 'name': 'AAA', 'score': 1.2, 'pb': 0.8, 'highlights': ['cheap']}, "
                "{'symbol': '600001', 'name': 'BBB', 'score': 0.7, 'pb': 1.0, 'highlights': ['stable']}], "
                "'metric_weights': {'pb': 0.4}, "
                "'missing_metrics': []}"
            ),
            "metadata": {"source_type": "bank_compare_scorecard"},
            "source_refs": [],
        },
    ]


class ReporterServiceTests(unittest.TestCase):
    def test_stock_screen_provider_unavailable_uses_runtime_fallback_report(self) -> None:
        payload = ReporterService(ReporterProviderUnavailableStub()).run(
            intent="stock_screen",
            query="screen stocks",
            retrieval_summary="Stock screening: candidates=1",
            evidences=_screening_evidences(),
        )

        self.assertTrue(payload["degraded"])
        self.assertEqual(payload["degraded_note"], "llm_unavailable")
        self.assertEqual(payload["degrade_reason"], "llm_unavailable")
        self.assertIn("Relative valuation views:", payload["conclusion"])
        self.assertEqual(payload["candidates"][0]["symbol"], "000001")
        self.assertTrue(payload["candidates"][0]["deep_checked"])
        self.assertEqual(len(list(payload.get("subagent_plans") or [])), 1)
        report_plan = list(payload.get("subagent_plans") or [])[0]
        terminal_node = next(node for node in list(report_plan.get("nodes") or []) if str(node.get("node_id") or "") == "report_terminal")
        self.assertEqual(str(terminal_node.get("subagent_type") or ""), "fallback_report_composer")
        patch_op_types = [
            str(operation.get("op_type") or "")
            for patch in list(payload.get("subagent_plan_patches") or [])
            for operation in list((patch or {}).get("operations") or [])
        ]
        self.assertIn("replace_subgraph", patch_op_types)
        trace_types = [item["subagent_type"] for item in payload["subagent_trace"]]
        self.assertEqual(trace_types, ["report_strategy_resolver", "fallback_report_composer"])

    def test_stock_screen_llm_error_uses_error_report_composer(self) -> None:
        payload = ReporterService(ReporterLLMErrorStub()).run(
            intent="stock_screen",
            query="screen stocks",
            retrieval_summary="Stock screening: candidates=1",
            evidences=_screening_evidences(),
        )

        self.assertTrue(payload["degraded"])
        self.assertEqual(payload["degraded_note"], "llm_error:LLMProviderError")
        self.assertEqual(payload["degrade_reason"], "llm_error:LLMProviderError")
        self.assertEqual(payload["candidates"][0]["symbol"], "000001")
        self.assertEqual(len(list(payload.get("subagent_plan_patches") or [])), 0)
        report_plan = list(payload.get("subagent_plans") or [])[0]
        terminal_node = next(node for node in list(report_plan.get("nodes") or []) if str(node.get("node_id") or "") == "report_terminal")
        self.assertEqual(str(terminal_node.get("subagent_type") or ""), "llm_report_composer")
        trace = payload["subagent_trace"]
        self.assertEqual(trace[1]["subagent_type"], "llm_report_composer")
        self.assertEqual(trace[1]["status"], "failed")
        self.assertEqual(trace[2]["subagent_type"], "llm_error_report_composer")
        self.assertEqual(trace[2]["status"], "completed")

    def test_stock_compare_uses_deterministic_bank_compare_without_llm(self) -> None:
        payload = ReporterService(ReporterUnexpectedLLMStub()).run(
            intent="stock_compare",
            query="compare banks",
            retrieval_summary="Stock compare: bank scorecard ready",
            evidences=_bank_compare_evidences(),
        )

        self.assertIn("AAA", payload["conclusion"])
        self.assertIn("BBB", payload["conclusion"])
        self.assertIsNone(payload["degraded_note"])
        report_plan = list(payload.get("subagent_plans") or [])[0]
        terminal_node = next(node for node in list(report_plan.get("nodes") or []) if str(node.get("node_id") or "") == "report_terminal")
        self.assertEqual(str(terminal_node.get("subagent_type") or ""), "bank_compare_report_composer")
        patch_op_types = [
            str(operation.get("op_type") or "")
            for patch in list(payload.get("subagent_plan_patches") or [])
            for operation in list((patch or {}).get("operations") or [])
        ]
        self.assertIn("replace_subgraph", patch_op_types)
        self.assertEqual(
            [item["subagent_type"] for item in payload["subagent_trace"]],
            ["report_strategy_resolver", "bank_compare_report_composer"],
        )

    def test_reporter_persists_subagent_dag_trace_and_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runs_root = Path(temp_dir) / "runs"
            payload = ReporterService(ReporterProviderUnavailableStub(), runs_root=runs_root).run(
                intent="stock_screen",
                query="screen stocks",
                retrieval_summary="Stock screening: candidates=1",
                evidences=_screening_evidences(),
            )
            run_root = next(runs_root.iterdir())
            trace_payload = SessionHistory(run_root).trace()
            metrics_payload = SessionHistory(run_root).metrics()

        self.assertTrue(payload["subagent_trace"])
        dag_events = [str(row.get("event") or "") for row in trace_payload["dynamic_trace"]]
        self.assertIn("subagent_dag_initialized", dag_events)
        self.assertIn("subagent_dag_node_completed", dag_events)
        self.assertGreaterEqual(
            metrics_payload["dag_event_counts"].get("subagent_dag_node_completed", 0),
            2,
        )


if __name__ == "__main__":
    unittest.main()
