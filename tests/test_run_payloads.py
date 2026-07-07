from __future__ import annotations

import unittest

from research_system.services.payloads.runs import (
    events_payload_fields,
    latest_run_context,
    metrics_payload_fields,
    render_event_row_text,
    render_labeled_field_line,
    replay_payload_fields,
    render_run_row_text,
    render_status_counts_text,
    steps_payload_fields,
    subagent_payload_fields,
    thread_payload_fields,
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

    def test_events_payload_fields_use_event_field_order(self) -> None:
        self.assertEqual(
            events_payload_fields(
                {
                    "run_id": "01EVENTS",
                    "request": {"intent": "stock_compare", "user_query": "compare banks"},
                    "summary": {"status": "completed"},
                    "display_summary": "done",
                    "event_counts": {"events": 5, "distinct_event_types": 3},
                }
            ),
            [
                ("run_id", "01EVENTS"),
                ("intent", "stock_compare"),
                ("query", "compare banks"),
                ("summary", "done"),
                ("status", "completed"),
                ("events", "5"),
                ("event_types", "3"),
            ],
        )

    def test_metrics_payload_fields_use_metrics_field_order(self) -> None:
        self.assertEqual(
            metrics_payload_fields(
                {
                    "run_id": "01METRICS",
                    "request": {"intent": "stock_check", "user_query": "inspect runtime"},
                    "summary": {"status": "completed"},
                    "display_summary": "done",
                    "counts": {"events": 5, "items": 4, "threads": 2, "subagents": 3, "llm_calls": 1},
                }
            ),
            [
                ("run_id", "01METRICS"),
                ("intent", "stock_check"),
                ("query", "inspect runtime"),
                ("summary", "done"),
                ("status", "completed"),
                ("events", "5"),
                ("items", "4"),
                ("threads", "2"),
                ("subagents", "3"),
                ("llm_calls", "1"),
            ],
        )

    def test_subagent_payload_fields_use_subagent_field_order(self) -> None:
        self.assertEqual(
            subagent_payload_fields(
                {
                    "run_id": "01SUBAGENT",
                    "invocation_id": "01INV",
                    "request": {"intent": "stock_compare", "user_query": "compare banks"},
                    "summary": {"status": "completed"},
                    "display_summary": "done",
                    "status": "completed",
                    "task_name": "collect:stock_snapshot_collector",
                    "node_id": "collect",
                    "agent_path": "collector/stock_snapshot_collector",
                    "subagent_type": "stock_snapshot_collector",
                    "thread_id": "thread-root",
                    "turn_id": "turn-root",
                    "phase": "collect",
                    "agent": "collector",
                    "llm_used": True,
                    "duration_ms": 42,
                    "event_counts": {"trace_rows": 2, "events": 3},
                }
            ),
            [
                ("run_id", "01SUBAGENT"),
                ("call_id", "01INV"),
                ("intent", "stock_compare"),
                ("query", "compare banks"),
                ("summary", "done"),
                ("run_status", "completed"),
                ("subagent_status", "completed"),
                ("task_name", "collect:stock_snapshot_collector"),
                ("node_id", "collect"),
                ("agent_path", "collector/stock_snapshot_collector"),
                ("subagent_type", "stock_snapshot_collector"),
                ("thread_id", "thread-root"),
                ("turn_id", "turn-root"),
                ("phase", "collect"),
                ("agent", "collector"),
                ("llm_used", "yes"),
                ("duration_ms", "42"),
                ("trace_rows", "2"),
                ("events", "3"),
            ],
        )

    def test_thread_payload_fields_use_thread_field_order(self) -> None:
        self.assertEqual(
            thread_payload_fields(
                {
                    "run_id": "01THREAD",
                    "thread_id": "thread-child",
                    "request": {"intent": "stock_compare", "user_query": "compare banks"},
                    "summary": {"status": "completed"},
                    "display_summary": "done",
                    "thread": {"status": "completed"},
                    "event_counts": {"events": 2, "subagent_trace": 1, "items": 3},
                }
            ),
            [
                ("run_id", "01THREAD"),
                ("thread_id", "thread-child"),
                ("intent", "stock_compare"),
                ("query", "compare banks"),
                ("summary", "done"),
                ("status", "completed"),
                ("thread_status", "completed"),
                ("events", "2"),
                ("subagent_trace", "1"),
                ("items", "3"),
            ],
        )

    def test_steps_payload_fields_use_steps_field_order(self) -> None:
        self.assertEqual(
            steps_payload_fields(
                {
                    "run_id": "01STEPS",
                    "intent": "stock_compare",
                    "request": {"intent": "stock_compare", "user_query": "compare banks"},
                    "summary": {"status": "running"},
                    "display_summary": "run in progress",
                    "strategy": "dynamic",
                    "entry_strategy": "llm",
                    "step_counts": {"total": 4, "active": 1, "completed": 2, "pending": 1},
                }
            ),
            [
                ("run_id", "01STEPS"),
                ("intent", "stock_compare"),
                ("query", "compare banks"),
                ("summary", "run in progress"),
                ("status", "running"),
                ("strategy", "dynamic"),
                ("entry_strategy", "llm"),
                ("steps", "4"),
                ("active_steps", "1"),
                ("completed_steps", "2"),
                ("pending_steps", "1"),
            ],
        )

    def test_render_trace_row_text_renders_subagent_shape(self) -> None:
        text = render_trace_row_text(
            {
                "task_name": "retrieve:retrieval_decider",
                "agent_path": "retriever/retrieval_decider",
                "agent_label": "Retrieval Decider",
                "invocation_id": "01INVOCATION",
                "subagent_type": "retrieval_decider",
                "status": "completed",
                "phase": "retrieve",
                "agent": "retriever",
                "objective": "Select final evidence set and summarize retrieval.",
                "notes": ["evidence_count=6"],
            }
        )
        self.assertIn("retrieve:retrieval_decider", text)
        self.assertIn("label=Retrieval Decider", text)
        self.assertIn("path=retriever/retrieval_decider", text)
        self.assertIn("call_id=01INVOCATION", text)
        self.assertIn("[completed]", text)
        self.assertIn("phase=retrieve agent=retriever", text)
        self.assertIn("notes=evidence_count=6", text)

    def test_render_event_row_text_renders_typed_event_timeline_shape(self) -> None:
        text = render_event_row_text(
            {
                "event_type": "subagent_completed",
                "phase": "collect",
                "agent": "collector",
                "message": "stock_snapshot_collector completed",
                "task_name": "collect:stock_snapshot_collector",
                "agent_path": "collector/stock_snapshot_collector",
                "agent_label": "Stock Snapshot Collector",
                "invocation_id": "01INVOCATION",
                "status": "completed",
                "objective": "Collect stock-level verified snapshots.",
            }
        )
        self.assertIn("[subagent_completed]", text)
        self.assertIn("phase=collect agent=collector", text)
        self.assertIn("collect:stock_snapshot_collector", text)
        self.assertIn("path=collector/stock_snapshot_collector", text)
        self.assertIn("call_id=01INVOCATION", text)
        self.assertIn("[completed]", text)

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
