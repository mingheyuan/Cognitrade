from __future__ import annotations

import unittest

from research_system.runtime.presentation.presenter.chat_presenter import ChatStreamPresenter
from research_system.runtime.presentation.presenter.chat_presenter_live_accumulator import ChatPresenterLiveAccumulator
from research_system.runtime.presentation.presenter.chat_presenter_live_payloads import ChatPresenterLivePayloads
from research_system.runtime.presentation.presenter.chat_presenter_payload_state import ChatPresenterPayloadState
from research_system.runtime.presentation.presenter.chat_presenter_summaries import ChatPresenterSummaries
from research_system.runtime.history.session.trace.run_trace_row_builder import RunTraceRowBuilder
from research_system.schemas.runtime import RunEvent


def _event(*, event_type: str, intent: str = "sector_research", phase: str | None = None, agent: str | None = None, message: str = "", payload: dict | None = None) -> RunEvent:
    return RunEvent(
        request_id="req",
        run_id="run",
        intent=intent,
        event_type=event_type,
        phase=phase,
        agent=agent,
        message=message or event_type,
        payload=payload,
    )


class ChatPresenterTests(unittest.TestCase):
    def test_presenter_wires_summary_and_live_payload_helpers(self) -> None:
        presenter = ChatStreamPresenter()
        self.assertIsInstance(presenter.live_payloads, ChatPresenterLivePayloads)
        self.assertIsInstance(presenter.live_payloads.row_builder, RunTraceRowBuilder)
        self.assertIsInstance(presenter.live_payloads.payload_state, ChatPresenterPayloadState)
        self.assertIsInstance(presenter.live_payloads.accumulator, ChatPresenterLiveAccumulator)
        self.assertIs(presenter.live_payloads.accumulator.row_builder, presenter.live_payloads.row_builder)
        self.assertIsInstance(presenter.summaries, ChatPresenterSummaries)

    def test_intent_event_generates_no_thinking_chunks(self) -> None:
        presenter = ChatStreamPresenter()
        chunks = presenter.on_event(_event(event_type="intent_identified", intent="stock_check", message="Intent identified: stock_check"))
        self.assertEqual(chunks, [])

    def test_stock_screen_intent_generates_no_thinking_text(self) -> None:
        presenter = ChatStreamPresenter()
        chunks = presenter.on_event(_event(event_type="intent_identified", intent="stock_screen", message="Intent identified: stock_screen"))
        self.assertEqual(chunks, [])

    def test_phase_started_generates_status_only(self) -> None:
        presenter = ChatStreamPresenter()
        chunks = presenter.on_event(_event(event_type="phase_started", intent="sector_research", phase="collect", agent="collector", message="Phase started: collect"))
        self.assertEqual(chunks[0].kind, "status")
        self.assertEqual(chunks[0].text, "collect")
        self.assertEqual(len(chunks), 1)

    def test_gate_needs_review_generates_summary_only(self) -> None:
        presenter = ChatStreamPresenter()
        chunks = presenter.on_event(_event(event_type="phase_completed", phase="gate", message="VerifiedFactGate: needs_review"))
        self.assertEqual(chunks[0].kind, "status")
        self.assertIn("VerifiedFactGate needs_review", chunks[0].text)
        self.assertEqual(len(chunks), 1)

    def test_full_event_mode_keeps_raw_event_line(self) -> None:
        presenter = ChatStreamPresenter(events_mode="full")
        chunks = presenter.on_event(_event(event_type="run_completed", message="Run completed"))
        self.assertEqual(chunks[0].kind, "event")
        self.assertIn("[run_completed]", chunks[0].text)

    def test_agent_progress_event_is_exposed_as_event_text(self) -> None:
        presenter = ChatStreamPresenter()
        chunks = presenter.on_event(
            _event(
                event_type="agent_progress",
                phase="screen",
                agent="screener",
                message="Enriching top 3 stock_screen candidates",
            )
        )
        self.assertEqual(chunks[0].kind, "event")
        self.assertIn("Enriching top 3 stock_screen candidates", chunks[0].text)

    def test_dynamic_action_inserted_event_is_exposed_as_event_text(self) -> None:
        presenter = ChatStreamPresenter()
        chunks = presenter.on_event(
            _event(
                event_type="dynamic_action_inserted",
                phase="collect",
                agent="collector",
                message="Dynamically inserted action: collect_replenish_notice",
            )
        )
        self.assertEqual(chunks[0].kind, "event")
        self.assertIn("collect_replenish_notice", chunks[0].text)

    def test_supervisor_decision_event_summarizes_next_action(self) -> None:
        presenter = ChatStreamPresenter()
        chunks = presenter.on_event(
            _event(
                event_type="supervisor_decision",
                message="Supervisor evaluated next actions",
                payload={
                    "decision_type": "continue",
                    "next_actions": [
                        {"node_id": "retrieve", "phase": "retrieve", "agent": "retriever"}
                    ],
                    "notes": [],
                },
            )
        )
        self.assertEqual(chunks[0].kind, "event")
        self.assertIn("next: retrieve / retriever / retrieve", chunks[0].text)

    def test_subagent_trace_event_is_exposed_as_event_text(self) -> None:
        presenter = ChatStreamPresenter()
        chunks = presenter.on_event(
            _event(
                event_type="subagent_trace",
                phase="retrieve",
                agent="retriever",
                message="blackboard_evidence_indexer [completed]: Index blackboard entries and expose evidence candidates.",
            )
        )
        self.assertEqual(chunks[0].kind, "event")
        self.assertIn("blackboard_evidence_indexer", chunks[0].text)

    def test_subagent_started_event_updates_status_with_live_progress_summary(self) -> None:
        presenter = ChatStreamPresenter()

        chunks = presenter.on_event(
            _event(
                event_type="subagent_started",
                phase="collect",
                agent="collector",
                payload={
                    "subagent_type": "stock_snapshot_collector",
                    "objective": "Collect stock-level verified snapshots.",
                    "status": "running",
                },
                message="stock_snapshot_collector started",
            )
        )

        self.assertEqual(chunks[0].kind, "status")
        self.assertIn("stock_snapshot_collector", chunks[0].text)
        self.assertIn("Collect stock-level verified snapshots.", chunks[0].text)
        self.assertEqual(chunks[0].state_patch, {"current_phase": "collect"})

    def test_subagent_completed_event_emits_progress_event_summary(self) -> None:
        presenter = ChatStreamPresenter()

        chunks = presenter.on_event(
            _event(
                event_type="subagent_completed",
                phase="retrieve",
                agent="retriever",
                payload={
                    "subagent_type": "retrieval_decider",
                    "objective": "Select final evidence set and summarize retrieval.",
                    "status": "completed",
                },
                message="retrieval_decider completed",
            )
        )

        self.assertEqual(chunks[0].kind, "event")
        self.assertEqual(chunks[0].text, "retrieval_decider: completed")

    def test_subagent_failed_event_emits_failure_summary(self) -> None:
        presenter = ChatStreamPresenter()

        chunks = presenter.on_event(
            _event(
                event_type="subagent_failed",
                phase="report",
                agent="reporter",
                payload={
                    "subagent_type": "llm_report_composer",
                    "objective": "Compose final report via LLM.",
                    "status": "failed",
                    "notes": ["LLMProviderError: timeout"],
                },
                message="llm_report_composer failed",
            )
        )

        self.assertEqual(chunks[0].kind, "event")
        self.assertIn("llm_report_composer: failed", chunks[0].text)
        self.assertIn("timeout", chunks[0].text)

    def test_presenter_accumulates_live_trace_payload_from_runtime_events(self) -> None:
        presenter = ChatStreamPresenter()

        presenter.on_event(
            _event(
                event_type="subagent_started",
                phase="collect",
                agent="collector",
                payload={
                    "invocation_id": "01INVOCATION",
                    "action_id": "act_collect",
                    "node_id": "collect",
                    "action_objective": "Load source records.",
                    "task_name": "collect:stock_snapshot_collector",
                    "agent_path": "collector/stock_snapshot_collector",
                    "subagent_type": "stock_snapshot_collector",
                    "objective": "Collect stock-level verified snapshots.",
                    "status": "running",
                },
            )
        )
        presenter.on_event(
            _event(
                event_type="dynamic_action_inserted",
                phase="review",
                agent="supervisor",
                payload={"action_id": "act_screen_review", "node_id": "screen_review", "objective": "Review screened candidates."},
            )
        )
        presenter.on_event(
            _event(
                event_type="phase_started",
                phase="collect",
                agent="collector",
                message="Entered waiting_external state",
                payload={
                    "action_id": "act_collect",
                    "node_id": "collect",
                    "objective": "Load source records.",
                    "wait_timeout_seconds": 30,
                },
            )
        )

        trace_payload = presenter.current_trace_payload()
        self.assertIsNotNone(trace_payload)
        self.assertEqual(trace_payload["run_id"], "run")
        self.assertEqual(trace_payload["summary"]["status"], "running")
        self.assertEqual(len(trace_payload["subagent_trace"]), 1)
        self.assertEqual(trace_payload["subagent_trace"][0]["subagent_type"], "stock_snapshot_collector")
        self.assertEqual(trace_payload["subagent_trace"][0]["node_id"], "collect")
        self.assertEqual(trace_payload["subagent_trace"][0]["task_name"], "collect:stock_snapshot_collector")
        self.assertEqual(trace_payload["subagent_trace"][0]["agent_path"], "collector/stock_snapshot_collector")
        self.assertEqual(trace_payload["subagent_trace"][0]["agent_label"], "Stock Snapshot Collector")
        self.assertEqual(trace_payload["subagent_trace"][0]["invocation_id"], "01INVOCATION")
        self.assertEqual(len(trace_payload["dynamic_trace"]), 1)
        self.assertEqual(trace_payload["dynamic_trace"][0]["node_id"], "screen_review")
        self.assertEqual(len(trace_payload["wait_trace"]), 1)
        self.assertEqual(trace_payload["wait_trace"][0]["event"], "waiting_external_started")
        self.assertEqual(trace_payload["wait_trace"][0]["node_id"], "collect")

    def test_presenter_accumulates_subagent_dag_events_into_dynamic_trace(self) -> None:
        presenter = ChatStreamPresenter()

        presenter.on_event(
            _event(
                event_type="subagent_dag_node_completed",
                phase="distill",
                agent="distiller",
                payload={
                    "task_name": "distill:preprocess:source_normalizer",
                    "agent_path": "distiller/preprocess/source_normalizer",
                    "subagent_type": "distill_source_normalizer",
                    "status": "completed",
                    "objective": "Normalize raw distillation source records.",
                },
            )
        )

        trace_payload = presenter.current_trace_payload()
        self.assertIsNotNone(trace_payload)
        self.assertEqual(len(trace_payload["dynamic_trace"]), 1)
        self.assertEqual(trace_payload["dynamic_trace"][0]["event"], "subagent_dag_node_completed")
        self.assertEqual(
            trace_payload["dynamic_trace"][0]["task_name"],
            "distill:preprocess:source_normalizer",
        )

    def test_presenter_accumulates_live_events_payload_from_runtime_events(self) -> None:
        presenter = ChatStreamPresenter()

        presenter.on_event(
            _event(
                event_type="subagent_started",
                phase="collect",
                agent="collector",
                message="stock_snapshot_collector started",
                payload={
                    "action_id": "act_collect",
                    "node_id": "collect",
                    "task_name": "collect:stock_snapshot_collector",
                    "agent_path": "collector/stock_snapshot_collector",
                    "agent_label": "Stock Snapshot Collector",
                    "subagent_type": "stock_snapshot_collector",
                    "invocation_id": "01INVOCATION",
                    "objective": "Collect stock-level verified snapshots.",
                    "status": "running",
                },
            )
        )
        presenter.on_event(
            _event(
                event_type="dynamic_action_inserted",
                phase="review",
                agent="supervisor",
                message="Dynamically inserted action: screen_review",
                payload={"action_id": "act_screen_review", "node_id": "screen_review", "objective": "Review screened candidates."},
            )
        )

        events_payload = presenter.current_events_payload()
        self.assertIsNotNone(events_payload)
        self.assertEqual(events_payload["run_id"], "run")
        self.assertEqual(events_payload["summary"]["status"], "running")
        self.assertEqual(len(events_payload["events"]), 2)
        self.assertEqual(events_payload["events"][0]["event_type"], "subagent_started")
        self.assertEqual(events_payload["events"][0]["task_name"], "collect:stock_snapshot_collector")
        self.assertEqual(events_payload["events"][0]["agent_path"], "collector/stock_snapshot_collector")
        self.assertEqual(events_payload["events"][0]["agent_label"], "Stock Snapshot Collector")
        self.assertEqual(events_payload["events"][0]["invocation_id"], "01INVOCATION")
        self.assertEqual(events_payload["events"][1]["event_type"], "dynamic_action_inserted")
        self.assertEqual(events_payload["event_counts"]["events"], 2)
        self.assertEqual(events_payload["event_counts"]["distinct_event_types"], 2)


if __name__ == "__main__":
    unittest.main()
