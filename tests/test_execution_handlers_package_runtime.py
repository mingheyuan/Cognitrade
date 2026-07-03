from __future__ import annotations

import unittest

from research_system.runtime.execution import handlers as handlers_package
from research_system.runtime.execution.handlers import dataflow as dataflow_package
from research_system.runtime.execution.handlers import distillation as distillation_package
from research_system.runtime.execution.handlers import reviewing as reviewing_package
from research_system.runtime.execution.handlers import screening as screening_package
from research_system.runtime.execution.handlers.data import run_collector, run_retriever
from research_system.runtime.execution.handlers.data_context import collect_blackboard_entries_json
from research_system.runtime.execution.handlers.collector_entries import (
    add_collector_outputs,
    add_stock_methodology_entries,
)
from research_system.runtime.execution.handlers.collector_wait import handle_collector_wait
from research_system.runtime.execution.handlers.retriever_entries import add_retrieved_evidence
from research_system.runtime.execution.handlers.distillation import run_critic, run_distiller
from research_system.runtime.execution.handlers.distillation_context import (
    collect_critic_inputs,
    collect_distiller_source_records,
)
from research_system.runtime.execution.handlers.distillation_entries import (
    add_critic_outputs,
    add_distillation_outputs,
)
from research_system.runtime.execution.handlers.registry import OrchestratorAgentHandlers
from research_system.runtime.execution.handlers.reporting import run_reporter
from research_system.runtime.execution.handlers.review import run_reviewer, run_screen_reviewer
from research_system.runtime.execution.handlers.review_context import (
    collect_review_request_context,
    collect_screen_review_context,
)
from research_system.runtime.execution.handlers.review_entries import add_review_findings, add_review_summary
from research_system.runtime.execution.handlers.review_events import (
    emit_reviewer_completed,
    emit_reviewer_requested,
    emit_screen_reviewer_completed,
    emit_screen_reviewer_requested,
)
from research_system.runtime.execution.handlers.review_flow import run_reviewer_flow, run_screen_reviewer_flow
from research_system.runtime.execution.handlers.review_trace import (
    extend_reviewer_trace,
    extend_screen_reviewer_trace,
)
from research_system.runtime.execution.handlers.screening import run_screener
from research_system.runtime.execution.handlers.screening_context import collect_screening_inputs
from research_system.runtime.execution.handlers.screening_enrichment import run_screening_enrichment
from research_system.runtime.execution.handlers.screening_entries import (
    add_screening_candidates,
    add_screening_scorecard,
)


class ExecutionHandlersPackageRuntimeTests(unittest.TestCase):
    def test_handlers_package_exports(self) -> None:
        self.assertIs(handlers_package.OrchestratorAgentHandlers, OrchestratorAgentHandlers)
        self.assertIs(handlers_package.run_collector, run_collector)
        self.assertIs(handlers_package.run_retriever, run_retriever)
        self.assertIs(handlers_package.run_screener, run_screener)
        self.assertIs(handlers_package.run_distiller, run_distiller)
        self.assertIs(handlers_package.run_critic, run_critic)
        self.assertIs(handlers_package.run_reviewer, run_reviewer)
        self.assertIs(handlers_package.run_screen_reviewer, run_screen_reviewer)
        self.assertIs(handlers_package.run_reporter, run_reporter)

    def test_reviewing_package_exports(self) -> None:
        self.assertIs(reviewing_package.run_reviewer, run_reviewer)
        self.assertIs(reviewing_package.run_screen_reviewer, run_screen_reviewer)
        self.assertIs(reviewing_package.collect_review_request_context, collect_review_request_context)
        self.assertIs(reviewing_package.collect_screen_review_context, collect_screen_review_context)
        self.assertIs(reviewing_package.add_review_findings, add_review_findings)
        self.assertIs(reviewing_package.add_review_summary, add_review_summary)
        self.assertIs(reviewing_package.emit_reviewer_requested, emit_reviewer_requested)
        self.assertIs(reviewing_package.emit_reviewer_completed, emit_reviewer_completed)
        self.assertIs(reviewing_package.emit_screen_reviewer_requested, emit_screen_reviewer_requested)
        self.assertIs(reviewing_package.emit_screen_reviewer_completed, emit_screen_reviewer_completed)
        self.assertIs(reviewing_package.run_reviewer_flow, run_reviewer_flow)
        self.assertIs(reviewing_package.run_screen_reviewer_flow, run_screen_reviewer_flow)
        self.assertIs(reviewing_package.extend_reviewer_trace, extend_reviewer_trace)
        self.assertIs(reviewing_package.extend_screen_reviewer_trace, extend_screen_reviewer_trace)

    def test_screening_package_exports(self) -> None:
        self.assertIs(screening_package.run_screener, run_screener)
        self.assertIs(screening_package.collect_screening_inputs, collect_screening_inputs)
        self.assertIs(screening_package.run_screening_enrichment, run_screening_enrichment)
        self.assertIs(screening_package.add_screening_candidates, add_screening_candidates)
        self.assertIs(screening_package.add_screening_scorecard, add_screening_scorecard)

    def test_distillation_package_exports(self) -> None:
        self.assertIs(distillation_package.run_distiller, run_distiller)
        self.assertIs(distillation_package.run_critic, run_critic)
        self.assertIs(distillation_package.collect_distiller_source_records, collect_distiller_source_records)
        self.assertIs(distillation_package.collect_critic_inputs, collect_critic_inputs)
        self.assertIs(distillation_package.add_distillation_outputs, add_distillation_outputs)
        self.assertIs(distillation_package.add_critic_outputs, add_critic_outputs)

    def test_dataflow_package_exports(self) -> None:
        self.assertIs(dataflow_package.run_collector, run_collector)
        self.assertIs(dataflow_package.run_retriever, run_retriever)
        self.assertIs(dataflow_package.collect_blackboard_entries_json, collect_blackboard_entries_json)
        self.assertIs(dataflow_package.add_collector_outputs, add_collector_outputs)
        self.assertIs(dataflow_package.add_stock_methodology_entries, add_stock_methodology_entries)
        self.assertIs(dataflow_package.handle_collector_wait, handle_collector_wait)
        self.assertIs(dataflow_package.add_retrieved_evidence, add_retrieved_evidence)


if __name__ == "__main__":
    unittest.main()
