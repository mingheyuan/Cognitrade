from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from research_system.runtime.jsonl import append_jsonl, write_json
from research_system.runtime.run_trace_row_builder import RunTraceRowBuilder
from research_system.runtime.session_history_subagent_payload_builder import (
    SessionHistorySubagentPayloadBuilder,
)
from research_system.runtime.session_history_step_builder import SessionHistoryStepBuilder
from research_system.runtime.session_history_trace_rebuilder import (
    SessionHistoryTraceRebuilder,
)
from research_system.runtime.session_history import SessionHistory


class SessionHistoryTests(unittest.TestCase):
    def test_session_history_wires_trace_rebuilder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir) / "01WIRE"
            run_root.mkdir(parents=True, exist_ok=True)
            history = SessionHistory(run_root)
            self.assertIsInstance(history.trace_rebuilder, SessionHistoryTraceRebuilder)
            self.assertIsInstance(history.trace_rebuilder.row_builder, RunTraceRowBuilder)
            self.assertIsInstance(
                history.trace_rebuilder.subagent_payload_builder,
                SessionHistorySubagentPayloadBuilder,
            )
            self.assertIsInstance(history.step_builder, SessionHistoryStepBuilder)

    def test_events_builds_typed_event_timeline_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir) / "01EVENTS"
            run_root.mkdir(parents=True, exist_ok=True)

            write_json(run_root / "request.json", {"intent": "stock_compare", "user_query": "compare banks"})
            write_json(run_root / "result.json", {"report": {"summary": "done"}})
            write_json(run_root / "summary.json", {"status": "completed", "message": "Run completed"})
            append_jsonl(
                run_root / "events.jsonl",
                {
                    "event_id": "evt1",
                    "request_id": "req1",
                    "run_id": "01EVENTS",
                    "intent": "stock_compare",
                    "event_type": "subagent_completed",
                    "phase": "collect",
                    "agent": "collector",
                    "message": "stock_snapshot_collector completed",
                    "payload": {
                        "action_id": "act_collect",
                        "node_id": "collect",
                        "task_name": "collect:stock_snapshot_collector",
                        "agent_path": "collector/stock_snapshot_collector",
                        "agent_label": "Stock Snapshot Collector",
                        "subagent_type": "stock_snapshot_collector",
                        "invocation_id": "01INVOCATION",
                        "status": "completed",
                        "objective": "Collect stock-level verified snapshots.",
                    },
                    "artifacts": [],
                    "created_at": "2026-07-02T10:00:00+00:00",
                },
            )

            payload = SessionHistory(run_root).events()

        self.assertEqual(payload["run_id"], "01EVENTS")
        self.assertEqual(payload["request"]["intent"], "stock_compare")
        self.assertEqual(len(payload["events"]), 1)
        self.assertEqual(payload["events"][0]["event_type"], "subagent_completed")
        self.assertEqual(payload["events"][0]["task_name"], "collect:stock_snapshot_collector")
        self.assertEqual(payload["events"][0]["agent_path"], "collector/stock_snapshot_collector")
        self.assertEqual(payload["events"][0]["agent_label"], "Stock Snapshot Collector")
        self.assertEqual(payload["events"][0]["invocation_id"], "01INVOCATION")
        self.assertEqual(payload["event_counts"]["events"], 1)
        self.assertEqual(payload["event_counts"]["distinct_event_types"], 1)

    def test_trace_aggregates_debug_and_wait_sections(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir) / "01TRACE"
            run_root.mkdir(parents=True, exist_ok=True)

            write_json(run_root / "request.json", {"intent": "stock_compare", "user_query": "compare banks"})
            write_json(
                run_root / "result.json",
                {
                    "report": {
                        "debug": {
                            "subagent_trace": [
                                {
                                    "action_id": "act_retrieve",
                                    "node_id": "retrieve",
                                    "action_objective": "Retrieve evidence.",
                                    "task_name": "retrieve:retrieval_decider",
                                    "agent_path": "retriever/retrieval_decider",
                                    "agent_label": "Retrieval Decider",
                                    "invocation_id": "01INVOCATION",
                                    "subagent_type": "retrieval_decider",
                                    "status": "completed",
                                    "phase": "retrieve",
                                    "agent": "retriever",
                                    "objective": "Select final evidence set and summarize retrieval.",
                                }
                            ],
                            "dynamic_trace": [
                                {
                                    "event": "dynamic_action_inserted",
                                    "action_id": "act_screen_review",
                                    "node_id": "screen_review",
                                    "phase": "review",
                                    "agent": "screen_reviewer",
                                    "objective": "Review screened candidates.",
                                }
                            ],
                        }
                    }
                },
            )
            write_json(run_root / "summary.json", {"status": "completed", "message": "Run completed"})
            append_jsonl(
                run_root / "events.jsonl",
                {
                    "event_id": "evt1",
                    "request_id": "req1",
                    "run_id": "01TRACE",
                    "intent": "stock_compare",
                    "event_type": "phase_started",
                    "phase": "collect",
                    "agent": "collector",
                    "message": "Entered waiting_external state",
                    "payload": {
                        "action_id": "act_collect",
                        "node_id": "collect",
                        "objective": "Load source records.",
                        "wait_timeout_seconds": 30,
                    },
                    "artifacts": [],
                    "created_at": "2026-07-01T12:00:00+00:00",
                },
            )

            payload = SessionHistory(run_root).trace()

        self.assertEqual(payload["run_id"], "01TRACE")
        self.assertEqual(payload["request"]["intent"], "stock_compare")
        self.assertEqual(len(payload["subagent_trace"]), 1)
        self.assertEqual(payload["subagent_trace"][0]["subagent_type"], "retrieval_decider")
        self.assertEqual(payload["subagent_trace"][0]["node_id"], "retrieve")
        self.assertEqual(payload["subagent_trace"][0]["task_name"], "retrieve:retrieval_decider")
        self.assertEqual(payload["subagent_trace"][0]["agent_path"], "retriever/retrieval_decider")
        self.assertEqual(payload["subagent_trace"][0]["agent_label"], "Retrieval Decider")
        self.assertEqual(payload["subagent_trace"][0]["invocation_id"], "01INVOCATION")
        self.assertEqual(len(payload["dynamic_trace"]), 1)
        self.assertEqual(payload["dynamic_trace"][0]["node_id"], "screen_review")
        self.assertEqual(len(payload["wait_trace"]), 1)
        self.assertEqual(payload["wait_trace"][0]["event"], "waiting_external_started")
        self.assertEqual(payload["wait_trace"][0]["node_id"], "collect")
        self.assertEqual(payload["event_counts"]["subagent_trace"], 1)
        self.assertEqual(payload["event_counts"]["dynamic_trace"], 1)
        self.assertEqual(payload["event_counts"]["wait_trace"], 1)

    def test_subagent_builds_call_scoped_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir) / "01SUBAGENT"
            run_root.mkdir(parents=True, exist_ok=True)

            write_json(run_root / "request.json", {"intent": "stock_compare", "user_query": "compare banks"})
            write_json(
                run_root / "result.json",
                {
                    "report": {
                        "debug": {
                            "subagent_trace": [
                                {
                                    "action_id": "act_collect",
                                    "node_id": "collect",
                                    "task_name": "collect:stock_snapshot_collector",
                                    "agent_path": "collector/stock_snapshot_collector",
                                    "agent_label": "Stock Snapshot Collector",
                                    "invocation_id": "01INVOCATION",
                                    "subagent_type": "stock_snapshot_collector",
                                    "status": "completed",
                                    "phase": "collect",
                                    "agent": "collector",
                                    "objective": "Collect stock-level verified snapshots.",
                                    "notes": ["symbol=600519"],
                                }
                            ]
                        }
                    }
                },
            )
            write_json(run_root / "summary.json", {"status": "completed", "message": "Run completed"})
            append_jsonl(
                run_root / "events.jsonl",
                {
                    "event_id": "evt1",
                    "request_id": "req1",
                    "run_id": "01SUBAGENT",
                    "intent": "stock_compare",
                    "event_type": "subagent_started",
                    "phase": "collect",
                    "agent": "collector",
                    "message": "stock_snapshot_collector started",
                    "payload": {
                        "action_id": "act_collect",
                        "node_id": "collect",
                        "task_name": "collect:stock_snapshot_collector",
                        "agent_path": "collector/stock_snapshot_collector",
                        "agent_label": "Stock Snapshot Collector",
                        "subagent_type": "stock_snapshot_collector",
                        "invocation_id": "01INVOCATION",
                        "status": "running",
                        "objective": "Collect stock-level verified snapshots.",
                    },
                    "artifacts": [],
                    "created_at": "2026-07-02T10:00:00+00:00",
                },
            )

            payload = SessionHistory(run_root).subagent("01INVOCATION")

        self.assertEqual(payload["run_id"], "01SUBAGENT")
        self.assertEqual(payload["invocation_id"], "01INVOCATION")
        self.assertEqual(payload["task_name"], "collect:stock_snapshot_collector")
        self.assertEqual(payload["agent_path"], "collector/stock_snapshot_collector")
        self.assertEqual(payload["subagent_type"], "stock_snapshot_collector")
        self.assertEqual(payload["status"], "running")
        self.assertEqual(len(payload["trace_rows"]), 1)
        self.assertEqual(len(payload["events"]), 1)
        self.assertEqual(payload["event_counts"]["trace_rows"], 1)
        self.assertEqual(payload["event_counts"]["events"], 1)

    def test_steps_returns_plan_and_supervisor_active_step_view(self) -> None:
        with TemporaryDirectory() as tmpdir:
            run_root = Path(tmpdir) / "01STEPS"
            run_root.mkdir(parents=True)
            write_json(run_root / "request.json", {"request_id": "req-steps", "intent": "stock_compare", "user_query": "compare banks"})
            write_json(
                run_root / "task_plan.json",
                {
                    "plan_id": "01PLAN",
                    "intent": "stock_compare",
                    "strategy": "dynamic",
                    "entry_strategy": "llm",
                    "entry_reason": "dynamic compare flow",
                    "subagent_required": True,
                    "dispatch_trace": {"source": "route_table", "intent": "stock_compare"},
                    "notes": ["compare flow activated"],
                    "actions": [
                        {
                            "action_id": "act_collect",
                            "node_id": "collect",
                            "phase": "collect",
                            "agent": "collector",
                            "objective": "Collect comparison snapshots.",
                            "inputs": {"symbols": ["600036", "601166"]},
                            "status": "pending",
                            "tags": ["compare"],
                        },
                        {
                            "action_id": "act_retrieve",
                            "node_id": "retrieve",
                            "phase": "retrieve",
                            "agent": "retriever",
                            "objective": "Retrieve final evidence.",
                            "inputs": {},
                            "status": "pending",
                            "tags": [],
                        },
                    ],
                },
            )
            write_json(run_root / "summary.json", {"status": "running", "message": "Run in progress"})
            append_jsonl(
                run_root / "events.jsonl",
                {
                    "request_id": "req-steps",
                    "run_id": "01STEPS",
                    "intent": "stock_compare",
                    "event_type": "supervisor_decision",
                    "phase": "collect",
                    "agent": "supervisor",
                    "message": "Supervisor evaluated next actions",
                    "payload": {
                        "completed_action_ids": ["act_collect"],
                        "next_actions": [
                            {
                                "action_id": "act_retrieve",
                                "node_id": "retrieve",
                                "phase": "retrieve",
                                "agent": "retriever",
                                "objective": "Retrieve final evidence.",
                            }
                        ],
                        "notes": ["advance to retrieval"],
                    },
                    "artifacts": [],
                    "created_at": "2026-07-02T11:00:00+00:00",
                },
            )

            payload = SessionHistory(run_root).steps()

        self.assertEqual(payload["run_id"], "01STEPS")
        self.assertEqual(payload["plan_id"], "01PLAN")
        self.assertEqual(payload["strategy"], "dynamic")
        self.assertEqual(payload["entry_strategy"], "llm")
        self.assertEqual(payload["dispatch_trace"]["source"], "route_table")
        self.assertEqual(payload["step_counts"]["total"], 2)
        self.assertEqual(payload["step_counts"]["completed"], 1)
        self.assertEqual(payload["step_counts"]["active"], 1)
        self.assertEqual(payload["status_counts"]["completed"], 1)
        self.assertEqual(payload["status_counts"]["active"], 1)
        self.assertEqual(payload["steps"][0]["node_id"], "collect")
        self.assertEqual(payload["steps"][0]["status"], "completed")
        self.assertEqual(payload["steps"][1]["node_id"], "retrieve")
        self.assertEqual(payload["steps"][1]["status"], "active")
        self.assertTrue(payload["steps"][1]["active_from_supervisor"])
        self.assertEqual(payload["steps"][1]["supervisor_note"], "advance to retrieval")


if __name__ == "__main__":
    unittest.main()
