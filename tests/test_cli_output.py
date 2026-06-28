from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout

from research_system.cli.app import _build_tui_hint_text, _print_chat_help, _print_payload, _render_tui_output, ChatSessionState, TuiSession


class CliOutputTests(unittest.TestCase):
    def test_chat_help_includes_stop_command(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            _print_chat_help()

        output = buffer.getvalue()
        self.assertIn("/stop", output)

    def test_render_tui_output_uses_boxed_sections(self) -> None:
        output = _render_tui_output(
            [
                ("thinking", "先判断任务类型"),
                ("status", "collect > report"),
                ("answer_stream", "这是流式答案"),
            ]
        )
        self.assertIn("┌ thinking", output)
        self.assertIn("┌ status", output)
        self.assertIn("┌ answer", output)

    def test_hint_text_changes_when_clarification_is_pending(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(pending_clarification_question="你偏短线还是中线？"),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        hint = _build_tui_hint_text(session)
        self.assertIn("clarification pending", hint[0][1])

    def test_print_rows_uses_single_line_summary_format(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            _print_payload(
                [
                    {
                        "run_id": "01TEST",
                        "status": "completed",
                        "intent": "review_thesis",
                        "display_summary": "overall_verdict=confirmed; findings=3",
                    }
                ],
                as_json=False,
            )

        output = buffer.getvalue().strip()
        self.assertEqual(
            output,
            "01TEST  [completed]  review_thesis  overall_verdict=confirmed; findings=3",
        )

    def test_print_status_payload_shows_recent_runs(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            _print_payload(
                {
                    "project_root": "D:/work_26_6_24/Cognitrade",
                    "provider_name": "openai",
                    "default_model": "gpt-5.5",
                    "qdrant_mode": "local",
                    "qdrant_collection_name": "research_evidence",
                    "total_runs": 2,
                    "status_counts": {"completed": 2},
                    "recent_runs": [
                        {
                            "run_id": "01TEST",
                            "status": "completed",
                            "intent": "review_thesis",
                            "display_summary": "overall_verdict=confirmed; findings=3",
                        }
                    ],
                },
                as_json=False,
            )

        output = buffer.getvalue()
        self.assertIn("project_root: D:/work_26_6_24/Cognitrade", output)
        self.assertIn("status_counts: completed=2", output)
        self.assertIn("recent_runs:", output)
        self.assertIn("01TEST  [completed]  review_thesis  overall_verdict=confirmed; findings=3", output)

    def test_print_doctor_payload_groups_checks(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            _print_payload(
                {
                    "status": "failed",
                    "checks": [
                        {"name": "env_file", "status": "passed", "path": "D:/work_26_6_24/Cognitrade/.env"},
                        {"name": "openai_api_key", "status": "failed", "detail": "missing"},
                        {"name": "qdrant_local_dir", "status": "skipped", "path": "D:/work_26_6_24/Cognitrade/workspace/qdrant_local"},
                    ],
                },
                as_json=False,
            )

        output = buffer.getvalue()
        self.assertIn("doctor_status: failed", output)
        self.assertIn("failed:", output)
        self.assertIn("  openai_api_key  missing", output)
        self.assertIn("passed:", output)
        self.assertIn("  env_file  D:/work_26_6_24/Cognitrade/.env", output)
        self.assertIn("skipped:", output)


if __name__ == "__main__":
    unittest.main()
