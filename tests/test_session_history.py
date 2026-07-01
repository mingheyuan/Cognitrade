from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.runtime.jsonl import append_jsonl, write_json
from research_system.runtime.session_history import SessionHistory


class SessionHistoryTests(unittest.TestCase):
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
        self.assertEqual(len(payload["dynamic_trace"]), 1)
        self.assertEqual(payload["dynamic_trace"][0]["node_id"], "screen_review")
        self.assertEqual(len(payload["wait_trace"]), 1)
        self.assertEqual(payload["wait_trace"][0]["event"], "waiting_external_started")
        self.assertEqual(payload["wait_trace"][0]["node_id"], "collect")
        self.assertEqual(payload["event_counts"]["subagent_trace"], 1)
        self.assertEqual(payload["event_counts"]["dynamic_trace"], 1)
        self.assertEqual(payload["event_counts"]["wait_trace"], 1)


if __name__ == "__main__":
    unittest.main()
