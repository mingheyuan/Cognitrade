from __future__ import annotations

import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from research_system.cli.chat_rendering import apply_tui_update
from research_system.cli.chat_result import print_chat_result_payload
from research_system.cli.chat_types import ChatSessionState, TuiSession
from research_system.cli.chat_updates import default_update_for_text
from research_system.services.task_router import TaskRouter
from research_system.runtime.settings import Settings


class ProviderUnavailableStub:
    available = False


class ProviderResultStreamingStub:
    available = True

    def stream_complete(self, **kwargs):
        on_text_delta = kwargs["on_text_delta"]
        on_reasoning_summary = kwargs.get("on_reasoning_summary")
        if callable(on_reasoning_summary):
            on_reasoning_summary("rendering reasoning summary")
        on_text_delta("Rendered")
        on_text_delta(" result.")
        return "Rendered result."

    def complete(self, **kwargs):
        return "unused"


class ChatResultTests(unittest.TestCase):
    def test_chat_result_payload_includes_summary_and_conclusion_when_both_exist(self) -> None:
        buffer = StringIO()
        with redirect_stdout(buffer):
            print_chat_result_payload(
                {
                    "run_id": "01CHAT",
                    "status": "completed",
                    "intent": "review_thesis",
                    "display_summary": "overall_verdict=confirmed; findings=2",
                    "result": {
                        "conclusion": "This should not be the primary chat output.",
                    },
                },
                llm_provider=ProviderUnavailableStub(),
                query="review the previous thesis",
                requested_intent="review_thesis",
                payload_printer=lambda payload, as_json=False, output_writer=None: print(payload),
            )
        output = buffer.getvalue()
        self.assertIn("overall_verdict=confirmed; findings=2", output)
        self.assertIn("run_id: 01CHAT", output)
        self.assertIn("This should not be the primary chat output.", output)

    def test_chat_result_payload_merges_summary_and_conclusion_when_both_exist(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )
        print_chat_result_payload(
            {
                "run_id": "01MERGE",
                "status": "completed",
                "intent": "stock_check",
                "display_summary": "summary line",
                "result": {
                    "conclusion": "longer detailed conclusion",
                },
            },
            llm_provider=ProviderUnavailableStub(),
            query="help me check 000001",
            requested_intent="stock_check",
            payload_printer=lambda payload, as_json=False, output_writer=None: None,
            output_writer=lambda update: apply_tui_update(session, update),
        )
        answers = [message.text for message in session.output_lines if message.kind == "answer"]
        self.assertEqual(answers, ["summary line\n\nlonger detailed conclusion"])

    def test_chat_result_payload_updates_last_run_id_via_session_patch(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )
        print_chat_result_payload(
            {
                "run_id": "01CHATPATCH",
                "status": "completed",
                "intent": "review_thesis",
                "display_summary": "overall_verdict=confirmed; findings=2",
                "result": {
                    "conclusion": "This should not be the primary chat output.",
                },
            },
            llm_provider=ProviderUnavailableStub(),
            query="review the previous thesis",
            requested_intent="review_thesis",
            payload_printer=lambda payload, as_json=False, output_writer=None: None,
            output_writer=lambda update: apply_tui_update(session, update),
        )
        self.assertEqual(session.current_run_id, "01CHATPATCH")
        self.assertEqual(session.state.last_run_id, "01CHATPATCH")

    def test_chat_result_payload_clarification_uses_explicit_updates(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )
        print_chat_result_payload(
            {
                "run_id": "01CLARIFY",
                "status": "needs_clarification",
                "intent": "stock_screen",
                "clarification": {"question": "Short-term, mid-term, or long-term?"},
            },
            llm_provider=ProviderUnavailableStub(),
            query="recent watchlist ideas",
            requested_intent="stock_screen",
            payload_printer=lambda payload, as_json=False, output_writer=None: None,
            output_writer=lambda update: apply_tui_update(session, update),
        )
        self.assertEqual(session.current_run_id, "01CLARIFY")
        self.assertEqual(session.state.last_run_id, "01CLARIFY")
        self.assertEqual(session.run_status_label, "needs_clarification")
        self.assertEqual(session.output_lines[-1].text, "intent: stock_screen")

    def test_chat_result_payload_clarification_preserves_existing_thinking_messages(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )
        apply_tui_update(session, default_update_for_text("thinking", "first presenter thought"))
        print_chat_result_payload(
            {
                "run_id": "01CLARIFY",
                "status": "needs_clarification",
                "intent": "stock_screen",
                "clarification": {"question": "Short-term, mid-term, or long-term?"},
            },
            llm_provider=ProviderUnavailableStub(),
            query="recent watchlist ideas",
            requested_intent="stock_screen",
            payload_printer=lambda payload, as_json=False, output_writer=None: None,
            output_writer=lambda update: apply_tui_update(session, update),
        )
        thinking_messages = [message.text for message in session.output_lines if message.kind == "thinking"]
        self.assertEqual(thinking_messages, ["first presenter thought"])

    def test_mixed_presenter_and_result_thinking_messages_all_accumulate(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )
        apply_tui_update(session, default_update_for_text("thinking", "phase thinking"))
        print_chat_result_payload(
            {
                "run_id": "01CLARIFY",
                "status": "needs_clarification",
                "intent": "stock_screen",
                "clarification": {"question": "Short-term, mid-term, or long-term?"},
            },
            llm_provider=ProviderUnavailableStub(),
            query="recent watchlist ideas",
            requested_intent="stock_screen",
            payload_printer=lambda payload, as_json=False, output_writer=None: None,
            output_writer=lambda update: apply_tui_update(session, update),
        )
        apply_tui_update(session, default_update_for_text("thinking", "post-clarification thinking"))
        thinking_messages = [message.text for message in session.output_lines if message.kind == "thinking"]
        self.assertEqual(
            thinking_messages,
            [
                "phase thinking",
                "post-clarification thinking",
            ],
        )

    def test_chat_result_payload_streams_rendered_result_when_llm_available(self) -> None:
        buffer = StringIO()
        with redirect_stdout(buffer):
            print_chat_result_payload(
                {
                    "run_id": "01CHAT",
                    "status": "completed",
                    "intent": "sector_research",
                    "display_summary": "sector is weak but catalysts remain",
                    "result": {
                        "conclusion": "original structured conclusion",
                        "risk": "risk text",
                    },
                },
                llm_provider=ProviderResultStreamingStub(),
                query="How is the innovative drug sector recently?",
                requested_intent="sector_research",
                payload_printer=lambda payload, as_json=False, output_writer=None: print(payload),
            )
        output = buffer.getvalue()
        self.assertIn("rendering reasoning summary", output)
        self.assertIn("Rendered result.", output)
        self.assertIn("run_id: 01CHAT", output)
        self.assertNotIn("sector is weak but catalysts remain", output)

    def test_chat_result_payload_prints_clarification_question(self) -> None:
        buffer = StringIO()
        with redirect_stdout(buffer):
            print_chat_result_payload(
                {
                    "status": "needs_clarification",
                    "intent": "stock_screen",
                    "message": "Clarification required",
                    "clarification": {"question": "Do you want a stock or a sector?"},
                },
                llm_provider=ProviderUnavailableStub(),
                query="Look into one",
                requested_intent=None,
                payload_printer=lambda payload, as_json=False, output_writer=None: print(payload),
            )
        output = buffer.getvalue()
        self.assertIn("Do you want a stock or a sector?", output)
        self.assertIn("status: needs_clarification", output)


if __name__ == "__main__":
    unittest.main()
