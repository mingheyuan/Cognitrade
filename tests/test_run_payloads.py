from __future__ import annotations

import unittest

from research_system.services.run_payloads import (
    latest_run_context,
    render_labeled_field_line,
    replay_payload_fields,
    render_run_row_text,
    render_status_counts_text,
    render_trace_row_text,
    trace_payload_fields,
    run_payload_fields,
)


class RunPayloadsTests(unittest.TestCase):
    def test_render_run_row_text_uses_single_line_summary_format(self) -> None:
        text = render_run_row_text(
            {
                "run_id": "01TEST",
                "status": "completed",
                "intent": "review_thesis",
                "display_summary": "overall_verdict=confirmed; findings=3",
            }
        )
        self.assertEqual(
            text,
            "01TEST  [completed]  review_thesis  overall_verdict=confirmed; findings=3",
        )

    def test_render_run_row_text_supports_indent(self) -> None:
        text = render_run_row_text(
            {
                "run_id": "01TEST",
                "status": "completed",
                "intent": "review_thesis",
                "display_summary": "overall_verdict=confirmed; findings=3",
            },
            indent="  ",
        )
        self.assertEqual(
            text,
            "  01TEST  [completed]  review_thesis  overall_verdict=confirmed; findings=3",
        )

    def test_render_status_counts_text_uses_sorted_shape(self) -> None:
        self.assertEqual(
            render_status_counts_text({"failed": 1, "completed": 2}),
            "completed=2, failed=1",
        )

    def test_render_labeled_field_line_renders_standard_shape(self) -> None:
        self.assertEqual(render_labeled_field_line("run_id", "01TEST"), "run_id: 01TEST")

    def test_run_payload_fields_use_run_status_intent_order(self) -> None:
        self.assertEqual(
            run_payload_fields(
                {
                    "run_id": "01TEST",
                    "status": "completed",
                    "intent": "stock_check",
                }
            ),
            [
                ("run_id", "01TEST"),
                ("status", "completed"),
                ("intent", "stock_check"),
            ],
        )

    def test_replay_payload_fields_use_replay_field_order(self) -> None:
        self.assertEqual(
            replay_payload_fields(
                {
                    "run_id": "01REPLAY",
                    "request": {"intent": "stock_check", "user_query": "look at 000001"},
                    "summary": {"status": "completed"},
                    "display_summary": "done",
                    "events": [{}, {}],
                    "blackboard": [{}],
                }
            ),
            [
                ("run_id", "01REPLAY"),
                ("intent", "stock_check"),
                ("query", "look at 000001"),
                ("summary", "done"),
                ("status", "completed"),
                ("events", "2"),
                ("blackboard_entries", "1"),
            ],
        )

    def test_trace_payload_fields_use_trace_field_order(self) -> None:
        self.assertEqual(
            trace_payload_fields(
                {
                    "run_id": "01TRACE",
                    "request": {"intent": "stock_compare", "user_query": "compare banks"},
                    "summary": {"status": "completed"},
                    "display_summary": "done",
                    "event_counts": {"subagent_trace": 3, "dynamic_trace": 1, "wait_trace": 0},
                }
            ),
            [
                ("run_id", "01TRACE"),
                ("intent", "stock_compare"),
                ("query", "compare banks"),
                ("summary", "done"),
                ("status", "completed"),
                ("subagent_trace", "3"),
                ("dynamic_trace", "1"),
                ("wait_trace", "0"),
            ],
        )

    def test_render_trace_row_text_renders_subagent_shape(self) -> None:
        text = render_trace_row_text(
            {
                "subagent_type": "retrieval_decider",
                "status": "completed",
                "phase": "retrieve",
                "agent": "retriever",
                "objective": "Select final evidence set and summarize retrieval.",
                "notes": ["evidence_count=6"],
            }
        )
        self.assertIn("retrieval_decider", text)
        self.assertIn("[completed]", text)
        self.assertIn("phase=retrieve agent=retriever", text)
        self.assertIn("notes=evidence_count=6", text)

    def test_latest_run_context_uses_first_row_with_run_and_status(self) -> None:
        run_id, status = latest_run_context(
            [
                {"run_id": "01LATEST", "status": "completed", "intent": "review_thesis"},
                {"run_id": "01OLDER", "status": "failed", "intent": "stock_check"},
            ]
        )
        self.assertEqual(run_id, "01LATEST")
        self.assertEqual(status, "completed")


if __name__ == "__main__":
    unittest.main()
