from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from research_system.runtime.execution.handlers.dataflow.data import run_retriever
from research_system.runtime.execution.handlers.distillation.distillation import run_distiller
from research_system.runtime.execution.handlers.reporting import run_reporter
from research_system.runtime.orchestration.orchestrator_context import build_runtime_context
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout
from research_system.runtime.storage.runs.run_store import RunStore
from research_system.schemas.planning import ActionPlan, ActionSpec
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.runtime import RunStateSnapshot


def _build_runtime():
    temp_dir = tempfile.TemporaryDirectory()
    settings = Settings.from_env(Path(temp_dir.name))
    ensure_workspace_layout(settings)
    run_store = RunStore(settings.runs_root)
    request = RequestEnvelope(user_query="test runtime execution decisions")
    run_id, paths, snapshot = run_store.create_run(request, "stock_check")
    context = build_runtime_context(
        run_id=run_id,
        paths=paths,
        state=snapshot,
        run_timeout_seconds=settings.run_timeout_seconds,
        max_llm_calls_per_turn=settings.max_llm_calls_per_turn,
        max_subagent_calls_per_turn=settings.max_subagent_calls_per_turn,
        max_child_threads_per_turn=settings.max_child_threads_per_turn,
    )
    return temp_dir, settings, context, request


class RuntimeExecutionDecisionTests(unittest.TestCase):
    def test_run_reporter_passes_runtime_execution_mode(self) -> None:
        temp_dir, _settings, context, request = _build_runtime()
        try:
            reporter = Mock()
            reporter.llm_provider = object()
            reporter.run.return_value = {"conclusion": "ok", "evidence": []}
            support = Mock()
            support.resolve_llm_execution.return_value = Mock(can_use_llm=False, degrade_reason="turn_llm_budget_exhausted")
            support.record_payload_degradation.return_value = True
            support.build_debug_payload.return_value = {}
            host = Mock(reporter=reporter)

            run_reporter(
                host,
                support,
                context=context,
                request=request,
                plan=ActionPlan(intent="stock_check", actions=[]),
                intent="stock_check",
                action=ActionSpec(
                    action_id="a1",
                    node_id="n1",
                    phase="report",
                    agent="reporter",
                    objective="report",
                    inputs={},
                ),
                phase="report",
            )

            self.assertEqual(reporter.run.call_args.kwargs["execution_mode"], "fallback")
            self.assertEqual(reporter.run.call_args.kwargs["runtime_degraded_note"], "turn_llm_budget_exhausted")
        finally:
            temp_dir.cleanup()

    def test_run_distiller_passes_runtime_execution_mode(self) -> None:
        temp_dir, _settings, context, request = _build_runtime()
        try:
            distiller = Mock()
            distiller.llm_provider = object()
            distiller.run.return_value = {
                "summary": {"processing_notes": [], "major_risks": []},
                "logic_blocks": [],
                "distilled_dimensions": [],
            }
            support = Mock()
            support.resolve_llm_execution.return_value = Mock(can_use_llm=False, degrade_reason="turn_llm_budget_exhausted")
            support.record_payload_degradation.return_value = True
            host = Mock(distiller=distiller)

            run_distiller(
                host,
                support,
                context=context,
                request=request,
                plan=ActionPlan(intent="distill_kol_logic", actions=[]),
                intent="distill_kol_logic",
                action=ActionSpec(
                    action_id="a1",
                    node_id="n1",
                    phase="distill",
                    agent="distiller",
                    objective="distill",
                    inputs={},
                ),
                phase="distill",
            )

            self.assertEqual(distiller.run.call_args.kwargs["execution_mode"], "fallback")
            self.assertEqual(distiller.run.call_args.kwargs["runtime_degraded_reason"], "turn_llm_budget_exhausted")
        finally:
            temp_dir.cleanup()

    def test_run_retriever_passes_runtime_decisions(self) -> None:
        temp_dir, _settings, context, request = _build_runtime()
        try:
            retriever = Mock()
            retriever.llm_provider = object()
            retriever.run.return_value = {
                "evidences": [],
                "retrieval_summary": "fallback",
            }
            support = Mock()
            support.resolve_llm_execution.return_value = Mock(can_use_llm=False, degrade_reason="turn_llm_budget_exhausted")
            support.record_payload_degradation.return_value = True
            host = Mock(retriever=retriever)

            run_retriever(
                host,
                support,
                context=context,
                request=request,
                plan=ActionPlan(intent="stock_check", actions=[]),
                intent="stock_check",
                action=ActionSpec(
                    action_id="a1",
                    node_id="n1",
                    phase="retrieve",
                    agent="retriever",
                    objective="retrieve",
                    inputs={},
                ),
                phase="retrieve",
            )

            self.assertFalse(retriever.run.call_args.kwargs["allow_llm_decision"])
            self.assertEqual(retriever.run.call_args.kwargs["runtime_degraded_reason"], "turn_llm_budget_exhausted")
            self.assertEqual(retriever.run.call_args.kwargs["search_mode"], "blackboard_only")
        finally:
            temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
