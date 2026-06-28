from __future__ import annotations

import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from research_system.cli.app import (
    ChatSessionState,
    _handle_chat_line,
    _answer_basic_chat,
    _print_chat_result_payload,
    _print_streamed_basic_chat_answer,
    _should_answer_basic_chat,
)
from research_system.runtime.orchestrator import ResearchOrchestrator
from research_system.runtime.settings import Settings, ensure_workspace_layout
from research_system.schemas.protocol import RequestEnvelope


class ProviderUnavailableStub:
    available = False


class ProviderStreamingStub:
    available = True

    def stream_complete(self, **kwargs):
        on_text_delta = kwargs["on_text_delta"]
        on_text_delta("你好")
        on_text_delta("，这里是流式回答。")
        return "你好，这里是流式回答。"

    def complete(self, **kwargs):
        return "fallback should not be used"


class ProviderResultStreamingStub:
    available = True

    def stream_complete(self, **kwargs):
        on_text_delta = kwargs["on_text_delta"]
        on_text_delta("这是转述后的研究结论。")
        return "这是转述后的研究结论。"

    def complete(self, **kwargs):
        return "unused"


class RouterStub:
    def identify(self, request):
        raise AssertionError("router should not be used in this test")


class ChatShellTests(unittest.TestCase):
    def test_basic_identity_question_is_answered_without_research_routing(self) -> None:
        reply = _answer_basic_chat(query="你是谁？", llm_provider=ProviderUnavailableStub())
        self.assertIn("Cognitrade", reply)
        self.assertIn("命令行研究助手", reply)

    def test_basic_question_detector_returns_true_for_identity_question(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            router = ResearchOrchestrator(settings).task_router
            self.assertTrue(_should_answer_basic_chat(query="你是谁？", task_router=router))

    def test_basic_chat_streams_when_provider_supports_streaming(self) -> None:
        buffer = StringIO()
        with redirect_stdout(buffer):
            _print_streamed_basic_chat_answer(query="你好", llm_provider=ProviderStreamingStub())
        self.assertEqual(buffer.getvalue(), "你好，这里是流式回答。\n")

    def test_chat_result_payload_prefers_summary_over_full_json(self) -> None:
        buffer = StringIO()
        with redirect_stdout(buffer):
            _print_chat_result_payload(
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
                query="复核上次结论",
                requested_intent="review_thesis",
            )
        output = buffer.getvalue()
        self.assertIn("overall_verdict=confirmed; findings=2", output)
        self.assertIn("run_id: 01CHAT", output)
        self.assertNotIn("This should not be the primary chat output.", output)

    def test_chat_result_payload_streams_rendered_result_when_llm_available(self) -> None:
        buffer = StringIO()
        with redirect_stdout(buffer):
            _print_chat_result_payload(
                {
                    "run_id": "01CHAT",
                    "status": "completed",
                    "intent": "sector_research",
                    "display_summary": "板块偏弱，但催化仍在",
                    "result": {
                        "conclusion": "原始结构化结论",
                        "risk": "risk text",
                    },
                },
                llm_provider=ProviderResultStreamingStub(),
                query="最近创新药板块怎么样",
                requested_intent="sector_research",
            )
        output = buffer.getvalue()
        self.assertIn("这是转述后的研究结论。", output)
        self.assertIn("run_id: 01CHAT", output)
        self.assertNotIn("板块偏弱，但催化仍在", output)

    def test_chat_result_payload_prints_clarification_question(self) -> None:
        buffer = StringIO()
        with redirect_stdout(buffer):
            _print_chat_result_payload(
                {
                    "status": "needs_clarification",
                    "intent": None,
                    "message": "Clarification required",
                    "clarification": {"question": "你想看个股还是板块？"},
                },
                llm_provider=ProviderUnavailableStub(),
                query="看一下",
                requested_intent=None,
            )
        output = buffer.getvalue()
        self.assertIn("你想看个股还是板块？", output)
        self.assertIn("status: needs_clarification", output)

    def test_handle_chat_line_stores_pending_clarification(self) -> None:
        state = ChatSessionState()

        def fake_run_chat_research(*args, **kwargs):
            return {
                "status": "needs_clarification",
                "intent": None,
                "message": "Clarification required",
                "clarification": {"question": "你偏短线、中线还是长期？"},
            }

        from unittest.mock import patch

        with patch("research_system.cli.app._run_chat_research", side_effect=fake_run_chat_research):
            _handle_chat_line(
                Settings.from_env(Path(".")),
                state,
                "最近有什么股票值得投入",
                llm_provider=ProviderUnavailableStub(),
                task_router=RouterStub(),
                output_writer=lambda *args: None,
            )

        self.assertEqual(state.pending_clarification_query, "最近有什么股票值得投入")
        self.assertEqual(state.pending_clarification_question, "你偏短线、中线还是长期？")

    def test_handle_chat_line_merges_pending_clarification_follow_up(self) -> None:
        state = ChatSessionState(
            pending_clarification_query="最近有什么股票值得投入",
            pending_clarification_question="你偏短线、中线还是长期？",
        )

        captured_queries: list[str] = []

        def fake_run_chat_research(*args, **kwargs):
            captured_queries.append(kwargs["query"])
            return {
                "status": "completed",
                "intent": "stock_screen",
                "run_id": "01DONE",
                "display_summary": "中线候选已整理",
                "result": {"conclusion": "ok"},
            }

        from unittest.mock import patch

        with patch("research_system.cli.app._run_chat_research", side_effect=fake_run_chat_research):
            _handle_chat_line(
                Settings.from_env(Path(".")),
                state,
                "中线",
                llm_provider=ProviderUnavailableStub(),
                task_router=RouterStub(),
                output_writer=lambda *args: None,
            )

        self.assertEqual(len(captured_queries), 1)
        self.assertIn("最近有什么股票值得投入", captured_queries[0])
        self.assertIn("补充信息: 中线", captured_queries[0])
        self.assertIsNone(state.pending_clarification_query)

    def test_handle_chat_line_stop_uses_stop_callback(self) -> None:
        state = ChatSessionState()
        stop_calls: list[bool] = []
        outputs: list[tuple[str, str]] = []

        _handle_chat_line(
            Settings.from_env(Path(".")),
            state,
            "/stop",
            llm_provider=ProviderUnavailableStub(),
            task_router=RouterStub(),
            output_writer=lambda kind, text: outputs.append((kind, text)),
            stop_callback=lambda: stop_calls.append(True),
        )

        self.assertEqual(stop_calls, [True])
        self.assertIn(("status", "stop requested"), outputs)

    def test_orchestrator_event_listener_receives_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            settings = Settings.from_env(repo_root)
            ensure_workspace_layout(settings)
            events: list[str] = []

            def listener(event) -> None:
                events.append(event.event_type)

            orchestrator = ResearchOrchestrator(settings, event_listener=listener)
            orchestrator.retriever.qdrant = None
            orchestrator.retriever.qdrant_available = False
            orchestrator.retriever.qdrant_error = "disabled_for_test"
            response = orchestrator.run(RequestEnvelope(intent="stock_check", user_query="帮我看下 000001 这个股票"))

            self.assertIn(response.status, {"completed", "failed"})
            self.assertIn("request_received", events)
            self.assertIn("run_accepted", events)


if __name__ == "__main__":
    unittest.main()
