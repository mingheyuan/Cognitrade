from __future__ import annotations

import asyncio
import contextlib
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from prompt_toolkit.output.win32 import NoConsoleScreenBufferError
from prompt_toolkit.keys import Keys

from research_system.cli.chat_actions import handle_chat_cli_action, handle_chat_meta_action
from research_system.cli.chat_keybindings import build_chat_keybindings
from research_system.cli.chat_controller import handle_chat_line
from research_system.cli.chat_dispatch import (
    answer_basic_chat,
    decide_chat_dispatch,
    print_streamed_basic_chat_answer,
    should_answer_basic_chat,
)
from research_system.cli.chat_interactions import (
    emit_input_update,
    emit_meta_update,
    emit_warning_update,
    request_stop,
    submit_tui_input,
    toggle_commands_popup,
    write_tui_update,
)
from research_system.cli.chat_shell import run_chat_tui
from research_system.cli.chat_rendering import apply_tui_update
from research_system.cli.chat_result import print_chat_result_payload
from research_system.cli.chat_types import ChatSessionState, TuiSession
from research_system.cli.chat_updates import TranscriptUpdate, default_update_for_text
from research_system.runtime.orchestrator import ResearchOrchestrator
from research_system.runtime.settings import Settings, ensure_workspace_layout
from research_system.schemas.protocol import RequestEnvelope
from research_system.services.task_router import TaskRouter


class ProviderUnavailableStub:
    available = False


class ProviderStreamingStub:
    available = True

    def stream_complete(self, **kwargs):
        on_text_delta = kwargs["on_text_delta"]
        on_reasoning_summary = kwargs.get("on_reasoning_summary")
        if callable(on_reasoning_summary):
            on_reasoning_summary("brief reasoning summary")
        on_text_delta("Hello")
        on_text_delta(", streaming reply.")
        return "Hello, streaming reply."

    def complete(self, **kwargs):
        return "fallback should not be used"


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


class _FakeBuffer:
    def __init__(self, text: str = "") -> None:
        self.document = type("Doc", (), {"text": text})()
        self.complete_state = None


class _FakeInputArea:
    def __init__(self, text: str = "") -> None:
        self.buffer = _FakeBuffer(text)


class _FakeApp:
    def __init__(self) -> None:
        self.exit_result = None
        self.invalidated = False
        self.tasks: list[object] = []
        self.layout = type("Layout", (), {"focus": lambda self, target: None})()

    def exit(self, *, result=None) -> None:
        self.exit_result = result

    def invalidate(self) -> None:
        self.invalidated = True

    def create_background_task(self, task) -> None:
        self.tasks.append(task)

    def run(self) -> int:
        return 0


class _FakeEvent:
    def __init__(self, app: _FakeApp) -> None:
        self.app = app


def _find_binding(kb, key_name: str):
    for binding in kb.bindings:
        if [str(key) for key in binding.keys] == [key_name]:
            return binding
    raise AssertionError(f"binding not found: {key_name}")


class ChatShellTests(unittest.TestCase):
    def test_app_chat_falls_back_when_windows_console_is_unavailable(self) -> None:
        from unittest.mock import patch

        from research_system.cli import app as cli_app

        with patch("research_system.cli.app.run_chat_tui", side_effect=NoConsoleScreenBufferError()), patch(
            "research_system.cli.app.run_chat_fallback", return_value=0
        ) as fallback:
            result = cli_app.main(["chat"])

        self.assertEqual(result, 0)
        fallback.assert_called_once()

    def test_run_chat_tui_assigns_view_handles_for_scene_sync(self) -> None:
        captured = {}

        def _fake_build_chat_view(session):
            captured["session"] = session
            return type(
                "View",
                (),
                {
                    "output_area": object(),
                    "main_stack": object(),
                    "commands_popup": object(),
                    "commands_popup_float": object(),
                    "input_area": object(),
                    "status_bar": object(),
                    "hint_bar": object(),
                },
            )()

        def _fake_build_chat_application(*, session, view, key_bindings):
            return _FakeApp()

        from unittest.mock import patch

        with patch("research_system.cli.chat_shell.build_chat_view", _fake_build_chat_view), patch(
            "research_system.cli.chat_shell.build_chat_application", _fake_build_chat_application
        ):
            result = run_chat_tui(
                Settings.from_env(Path(".")),
                initial_intent=None,
                initial_run_id=None,
                thinking="on",
                events="compact",
                handle_chat_line=lambda *args, **kwargs: None,
                execute_cli_command=lambda *args, **kwargs: {},
            )

        session = captured["session"]
        self.assertEqual(result, 0)
        self.assertIsNotNone(session.main_stack)
        self.assertIsNotNone(session.commands_popup_float)

    def test_write_tui_update_batches_stream_appends_before_flushing(self) -> None:
        import time

        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )
        write_tui_update(session, default_update_for_text("answer_delta", "Hello"))
        write_tui_update(session, default_update_for_text("answer_delta", ", world"))
        self.assertEqual(session.output_lines, [])
        time.sleep(0.08)
        self.assertEqual(len(session.output_lines), 1)
        self.assertEqual(session.output_lines[0].text, "Hello, world")

    def test_build_chat_keybindings_exposes_expected_shortcuts(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )
        session.input_area = _FakeInputArea("")  # type: ignore[assignment]
        kb = build_chat_keybindings(
            session,
            handle_chat_line=lambda *args, **kwargs: None,
            execute_cli_command=lambda *args, **kwargs: {},
            submit_tui_input=lambda *args, **kwargs: None,
            request_stop=lambda *args, **kwargs: None,
            toggle_commands_popup=lambda *args, **kwargs: None,
            emit_warning_update=lambda *args, **kwargs: None,
        )
        keys = sorted(" ".join(str(key) for key in binding.keys) for binding in kb.bindings)
        self.assertEqual(
            keys,
            [
                "Keys.ControlC",
                "Keys.ControlD",
                "Keys.ControlJ",
                "Keys.ControlM",
                "Keys.End",
                "Keys.Escape",
                "Keys.F1",
                "Keys.Home",
                "Keys.PageDown",
                "Keys.PageUp",
                "Keys.ScrollDown",
                "Keys.ScrollUp",
            ],
        )

    def test_chat_keybindings_ctrl_d_warns_when_run_active(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )
        session.running = True
        session.input_area = _FakeInputArea("hello")  # type: ignore[assignment]
        warnings: list[str] = []
        kb = build_chat_keybindings(
            session,
            handle_chat_line=lambda *args, **kwargs: None,
            execute_cli_command=lambda *args, **kwargs: {},
            submit_tui_input=lambda *args, **kwargs: None,
            request_stop=lambda *args, **kwargs: None,
            toggle_commands_popup=lambda *args, **kwargs: None,
            emit_warning_update=lambda _session, message: warnings.append(message),
        )
        event = _FakeEvent(_FakeApp())
        binding = _find_binding(kb, "Keys.ControlD")
        binding.handler(event)
        self.assertIsNone(event.app.exit_result)
        self.assertEqual(warnings, ["A run is still active. Stop it with Ctrl+C or /stop first."])

    def test_chat_keybindings_scroll_events_move_output_window(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )

        class _FakeOutputWindow:
            def __init__(self) -> None:
                self.vertical_scroll = 5

        focus_targets: list[object] = []

        class _FakeOutputArea:
            def __init__(self) -> None:
                self.window = _FakeOutputWindow()
                self.text = "line1\nline2\nline3\nline4\nline5\nline6\nline7"
                self.buffer = type(
                    "Buffer",
                    (),
                    {
                        "cursor_position": len(self.text),
                        "cursor_up": lambda _self, count=1: setattr(_self, "cursor_position", max(0, _self.cursor_position - count)),
                        "cursor_down": lambda _self, count=1: setattr(_self, "cursor_position", min(len(self.text), _self.cursor_position + count)),
                    },
                )()

        session.output_area = _FakeOutputArea()  # type: ignore[assignment]
        session.input_area = object()  # type: ignore[assignment]
        app = _FakeApp()
        app.layout = type("Layout", (), {"focus": lambda _self, target: focus_targets.append(target)})()
        session.app = app
        kb = build_chat_keybindings(
            session,
            handle_chat_line=lambda *args, **kwargs: None,
            execute_cli_command=lambda *args, **kwargs: {},
            submit_tui_input=lambda *args, **kwargs: None,
            request_stop=lambda *args, **kwargs: None,
            toggle_commands_popup=lambda *args, **kwargs: None,
            emit_warning_update=lambda *args, **kwargs: None,
        )

        up_binding = _find_binding(kb, str(Keys.ScrollUp))
        down_binding = _find_binding(kb, str(Keys.ScrollDown))
        event = _FakeEvent(app)
        original_cursor = session.output_area.buffer.cursor_position
        up_binding.handler(event)
        self.assertLess(session.output_area.buffer.cursor_position, original_cursor)
        self.assertFalse(session.follow_output)
        down_binding.handler(event)
        self.assertGreaterEqual(session.output_area.buffer.cursor_position, original_cursor - 3)
        self.assertFalse(session.follow_output)
        self.assertTrue(app.invalidated)
        self.assertTrue(focus_targets)
        self.assertIs(focus_targets[-1], session.input_area)

    def test_chat_keybindings_page_navigation_moves_output_window(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )

        class _FakeOutputWindow:
            def __init__(self) -> None:
                self.vertical_scroll = 12

        focus_targets: list[object] = []

        class _FakeOutputArea:
            def __init__(self) -> None:
                self.window = _FakeOutputWindow()
                self.text = "line1\nline2\nline3"
                self.buffer = type(
                    "Buffer",
                    (),
                    {
                        "cursor_position": 0,
                        "cursor_up": lambda _self, count=1: setattr(_self, "cursor_position", max(0, _self.cursor_position - count)),
                        "cursor_down": lambda _self, count=1: setattr(_self, "cursor_position", min(len(self.text), _self.cursor_position + count)),
                    },
                )()

        session.output_area = _FakeOutputArea()  # type: ignore[assignment]
        session.input_area = object()  # type: ignore[assignment]
        app = _FakeApp()
        app.layout = type("Layout", (), {"focus": lambda _self, target: focus_targets.append(target)})()
        session.app = app
        kb = build_chat_keybindings(
            session,
            handle_chat_line=lambda *args, **kwargs: None,
            execute_cli_command=lambda *args, **kwargs: {},
            submit_tui_input=lambda *args, **kwargs: None,
            request_stop=lambda *args, **kwargs: None,
            toggle_commands_popup=lambda *args, **kwargs: None,
            emit_warning_update=lambda *args, **kwargs: None,
        )

        page_up = _find_binding(kb, str(Keys.PageUp))
        page_down = _find_binding(kb, str(Keys.PageDown))
        home = _find_binding(kb, str(Keys.Home))
        end = _find_binding(kb, str(Keys.End))
        event = _FakeEvent(app)

        page_up.handler(event)
        self.assertEqual(session.output_area.buffer.cursor_position, 0)
        page_down.handler(event)
        self.assertGreater(session.output_area.buffer.cursor_position, 0)
        self.assertFalse(session.follow_output)
        home.handler(event)
        self.assertEqual(session.output_area.buffer.cursor_position, 0)
        self.assertFalse(session.follow_output)
        end.handler(event)
        self.assertEqual(session.output_area.buffer.cursor_position, len(session.output_area.text))
        self.assertTrue(session.follow_output)
        self.assertTrue(focus_targets)
        self.assertIs(focus_targets[-1], session.input_area)

    def test_write_tui_output_accepts_text_protocol_for_meta_and_warning(self) -> None:
        from research_system.cli.chat_updates import default_update_for_text

        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )
        apply_tui_update(session, default_update_for_text("meta", "Cognitrade terminal shell"))
        emit_meta_update(session, "second meta line")
        emit_input_update(session, "> test input")
        emit_warning_update(session, "A run is still active.")
        self.assertEqual(session.output_lines[0].text, "Cognitrade terminal shell")
        self.assertEqual(session.output_lines[0].kind, "meta")
        self.assertEqual(session.output_lines[1].kind, "meta")
        self.assertEqual(session.output_lines[2].kind, "input")
        self.assertEqual(session.output_lines[3].kind, "warning")
        self.assertEqual(session.run_status_label, "warning")
        self.assertEqual(session.output_lines[0].update_mode, "append")

    def test_basic_identity_question_is_answered_without_research_routing(self) -> None:
        reply = answer_basic_chat(query="who are you?", llm_provider=ProviderUnavailableStub())
        self.assertIn("Cognitrade", reply)

    def test_basic_identity_question_is_answered_in_chinese_without_garbled_text(self) -> None:
        reply = answer_basic_chat(query="你是谁？", llm_provider=ProviderUnavailableStub())
        self.assertIn("Cognitrade", reply)
        self.assertIn("A 股研究", reply)
        self.assertNotIn("閹存垶妲", reply)

    def test_basic_question_detector_returns_true_for_identity_question(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            orchestrator = ResearchOrchestrator(settings)
            try:
                router = orchestrator.task_router
                self.assertTrue(should_answer_basic_chat(query="who are you?", task_router=router))
            finally:
                orchestrator.close()

    def test_chat_dispatch_prefers_direct_answer_for_index_definition_question(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            orchestrator = ResearchOrchestrator(settings)
            try:
                router = orchestrator.task_router
                dispatch = decide_chat_dispatch(query="上证指数是什么？", task_router=router)
                self.assertEqual(dispatch.mode, "direct_answer")
                self.assertIsNone(dispatch.intent)
            finally:
                orchestrator.close()

    def test_chat_dispatch_prefers_research_for_time_sensitive_index_question(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            orchestrator = ResearchOrchestrator(settings)
            try:
                router = orchestrator.task_router
                dispatch = decide_chat_dispatch(query="最近上证指数怎么样？", task_router=router)
                self.assertEqual(dispatch.mode, "research")
            finally:
                orchestrator.close()

    def test_basic_chat_streams_when_provider_supports_streaming(self) -> None:
        buffer = StringIO()
        with redirect_stdout(buffer):
            print_streamed_basic_chat_answer(
                query="Hello",
                llm_provider=ProviderStreamingStub(),
                emit_text=lambda output_writer, update: print(update.text, end="" if update.action == "stream_append" else "\n"),
            )
        output = buffer.getvalue()
        self.assertIn("brief reasoning summary", output)
        self.assertIn("Hello, streaming reply.", output)

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
            query="最近有什么股票值得投入",
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
            query="最近有什么股票值得投入",
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
            query="最近有什么股票值得投入",
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

    def test_chat_research_writes_dispatch_trace_into_result_debug(self) -> None:
        class SkillRuntimeStub:
            def run(self, skill_name: str, **kwargs):
                snapshot_type = skill_name.removesuffix("_snapshot_skill").replace("_snapshot", "")
                return {
                    "snapshot_type": snapshot_type,
                    "symbol": kwargs.get("symbol"),
                    "query": kwargs.get("query"),
                    "snapshot_date": "2026-06-28",
                    "facts": {},
                    "source": {"skill": skill_name},
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            settings = Settings.from_env(repo_root)
            ensure_workspace_layout(settings)
            orchestrator = ResearchOrchestrator(settings)
            try:
                orchestrator.collector.skill_runtime = SkillRuntimeStub()
                orchestrator.collector.registry._subagents["stock_snapshot_collector"].skill_runtime = orchestrator.collector.skill_runtime
                orchestrator.collector.registry._subagents["sector_snapshot_collector"].skill_runtime = orchestrator.collector.skill_runtime
                orchestrator.collector.registry._subagents["candidate_universe_builder"].skill_runtime = orchestrator.collector.skill_runtime
                orchestrator.retriever.qdrant_available = False
                orchestrator.retriever.qdrant_error = "disabled_for_test"
                response = orchestrator.run(
                    RequestEnvelope(
                        intent="stock_check",
                        user_query="帮我看下 000001",
                        constraints={
                            "chat_dispatch": {
                                "mode": "research",
                                "intent": "stock_check",
                                "reason": "chat_dispatch_test",
                                "source": "chat_dispatch",
                            }
                        },
                    )
                )
                self.assertEqual(response.status, "completed")
                result = response.result or {}
                report = result.get("report") or {}
                debug = report.get("debug") or {}
                dispatch_trace = debug.get("dispatch_trace") or {}
                self.assertEqual(dispatch_trace.get("mode"), "research")
                self.assertEqual(dispatch_trace.get("intent"), "stock_check")
                self.assertEqual(dispatch_trace.get("reason"), "chat_dispatch_test")
            finally:
                orchestrator.close()

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

    def test_handle_chat_line_stores_pending_clarification(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )

        def fake_run_chat_research(*args, **kwargs):
            return {
                "status": "needs_clarification",
                "intent": "stock_screen",
                "message": "Clarification required",
                "clarification": {"question": "Short-term, mid-term, or long-term?"},
            }

        handle_chat_line(
                Settings.from_env(Path(".")),
                session.state,
                "最近有什么股票值得投入",
                llm_provider=ProviderUnavailableStub(),
                task_router=TaskRouter(),
                cli_command_runner=lambda *args, **kwargs: {},
                payload_printer=lambda payload, as_json=False, output_writer=None: None,
            output_writer=lambda update: apply_tui_update(session, update),
            run_chat_research_fn=fake_run_chat_research,
        )

        self.assertEqual(session.state.pending_clarification_query, "最近有什么股票值得投入")
        self.assertEqual(session.state.pending_clarification_question, "Short-term, mid-term, or long-term?")

    def test_handle_chat_line_updates_last_run_id_via_session_patch(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )

        def fake_run_chat_research(*args, **kwargs):
            return {
                "run_id": "01RUNFLOW",
                "status": "completed",
                "intent": "stock_check",
                "display_summary": "done",
            }

        handle_chat_line(
                Settings.from_env(Path(".")),
                session.state,
                "帮我看下 000001",
                llm_provider=ProviderUnavailableStub(),
                task_router=TaskRouter(),
                cli_command_runner=lambda *args, **kwargs: {},
                payload_printer=lambda payload, as_json=False, output_writer=None: None,
            output_writer=lambda update: apply_tui_update(session, update),
            run_chat_research_fn=fake_run_chat_research,
        )

        self.assertEqual(session.state.last_run_id, "01RUNFLOW")

    def test_handle_chat_line_stop_uses_stop_callback(self) -> None:
        state = ChatSessionState()
        stop_calls: list[bool] = []
        outputs: list[TranscriptUpdate] = []

        handle_chat_line(
            Settings.from_env(Path(".")),
            state,
            "/stop",
            llm_provider=ProviderUnavailableStub(),
            task_router=TaskRouter(),
            cli_command_runner=lambda *args, **kwargs: {},
            payload_printer=lambda payload, as_json=False, output_writer=None: None,
            output_writer=outputs.append,
            stop_callback=lambda: stop_calls.append(True),
        )

        self.assertEqual(stop_calls, [True])
        self.assertTrue(outputs)
        self.assertEqual(outputs[-1].kind, "meta")
        self.assertEqual(outputs[-1].text, "stop requested")

    def test_handle_chat_line_review_thesis_requires_run_id_emits_warning_update(self) -> None:
        state = ChatSessionState(current_intent="review_thesis")
        outputs: list[TranscriptUpdate] = []

        handle_chat_line(
            Settings.from_env(Path(".")),
            state,
            "复核上次结论",
            llm_provider=ProviderUnavailableStub(),
            task_router=TaskRouter(),
            cli_command_runner=lambda *args, **kwargs: {},
            payload_printer=lambda payload, as_json=False, output_writer=None: None,
            output_writer=outputs.append,
        )

        self.assertTrue(outputs)
        self.assertEqual(outputs[-1].kind, "warning")
        self.assertIn("review_thesis requires", outputs[-1].text)

    def test_handle_chat_meta_action_emits_session_patch_for_intent_change(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )
        handle_chat_meta_action(
            action="set_intent",
            parsed={"intent": "stock_screen"},
            state=session.state,
            settings=session.settings,
            emit_text=lambda output_writer, update: apply_tui_update(session, update),
            output_writer=None,
        )
        self.assertEqual(session.state.current_intent, "stock_screen")
        self.assertEqual(session.current_phase, "-")

    def test_handle_chat_meta_action_show_last_refreshes_session_patch(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(
                current_intent="stock_screen",
                review_target_run_id="01TARGET",
                last_run_id="01LAST",
                thinking_enabled=False,
                events_mode="full",
            ),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )
        handle_chat_meta_action(
            action="show_last",
            parsed={"action": "show_last"},
            state=session.state,
            settings=session.settings,
            emit_text=lambda output_writer, update: apply_tui_update(session, update),
            output_writer=None,
        )
        self.assertEqual(session.state.last_run_id, "01LAST")
        self.assertEqual(session.state.current_intent, "stock_screen")
        self.assertEqual(session.state.review_target_run_id, "01TARGET")
        self.assertFalse(session.state.thinking_enabled)
        self.assertEqual(session.state.events_mode, "full")
        self.assertEqual(session.current_run_id, "01LAST")

    def test_handle_chat_cli_action_updates_last_run_id_via_session_patch(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(last_run_id="01OLD"),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )

        def fake_cli_command_runner(settings, *, command, run_id, **kwargs):
            self.assertEqual(command, "replay")
            self.assertEqual(run_id, "01NEW")
            return {"run_id": "01NEW", "status": "completed", "message": "ok"}

        handled = handle_chat_cli_action(
            action="replay",
            parsed={"action": "replay", "run_id": "01NEW"},
            state=session.state,
            settings=session.settings,
            emit_text=lambda output_writer, update: apply_tui_update(session, update),
            cli_command_runner=fake_cli_command_runner,
            payload_printer=lambda payload, as_json, output_writer=None: None,
            output_writer=None,
        )
        self.assertTrue(handled)
        self.assertEqual(session.state.last_run_id, "01NEW")

    def test_request_stop_updates_shell_state_via_structured_patch(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )
        session.running = True
        session.current_run_id = "01RUN"

        def fake_execute_cli_command(settings, *, command, run_id, **kwargs):
            self.assertEqual(command, "cancel")
            self.assertEqual(run_id, "01RUN")
            return {"message": "cancelling"}

        request_stop(session, execute_cli_command=fake_execute_cli_command)
        self.assertEqual(session.run_status_label, "cancelling")
        self.assertEqual(session.current_phase, "cancelling")

    def test_toggle_commands_popup_updates_shell_state_via_structured_patch(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )
        self.assertFalse(session.commands_popup_visible)
        toggle_commands_popup(session, visible=True)
        self.assertTrue(session.commands_popup_visible)
        toggle_commands_popup(session, visible=False)
        self.assertFalse(session.commands_popup_visible)

    def test_submit_tui_input_applies_run_lifecycle_state_patches(self) -> None:
        class DummyBuffer:
            def __init__(self, text: str) -> None:
                self.document = type("Doc", (), {"text": text})()
                self.complete_state = None

        class DummyInputArea:
            def __init__(self, text: str) -> None:
                self.text = text
                self.buffer = DummyBuffer(text)

        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )
        session.input_area = DummyInputArea("who are you?")  # type: ignore[assignment]
        session.follow_output = False
        seen_states: list[tuple[str, str, bool]] = []

        def fake_handle_chat_line(*args, **kwargs):
            seen_states.append((session.run_status_label, session.current_phase, session.running))
            return None

        asyncio.run(
            submit_tui_input(
                session,
                handle_chat_line=fake_handle_chat_line,
                execute_cli_command=lambda *args, **kwargs: {},
            )
        )
        if session.worker is not None:
            session.worker.join(timeout=2)
        self.assertIn(("running", "routing", True), seen_states)
        self.assertEqual(session.current_gate, "pending")
        self.assertEqual(session.run_status_label, "idle")
        self.assertFalse(session.running)
        self.assertTrue(session.follow_output)

    def test_submit_tui_input_sets_current_run_id_via_structured_patch(self) -> None:
        class DummyBuffer:
            def __init__(self, text: str) -> None:
                self.document = type("Doc", (), {"text": text})()
                self.complete_state = None

        class DummyInputArea:
            def __init__(self, text: str) -> None:
                self.text = text
                self.buffer = DummyBuffer(text)

        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(last_run_id="01LATEST"),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )
        session.input_area = DummyInputArea("who are you?")  # type: ignore[assignment]

        def fake_handle_chat_line(*args, **kwargs):
            return None

        asyncio.run(
            submit_tui_input(
                session,
                handle_chat_line=fake_handle_chat_line,
                execute_cli_command=lambda *args, **kwargs: {},
            )
        )
        if session.worker is not None:
            session.worker.join(timeout=2)
        self.assertEqual(session.current_run_id, "01LATEST")

    def test_submit_tui_input_captures_stderr_and_exception_into_tui(self) -> None:
        class DummyBuffer:
            def __init__(self, text: str) -> None:
                self.document = type("Doc", (), {"text": text})()
                self.complete_state = None

        class DummyInputArea:
            def __init__(self, text: str) -> None:
                self.text = text
                self.buffer = DummyBuffer(text)

        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )
        session.input_area = DummyInputArea("test")  # type: ignore[assignment]

        def fake_handle_chat_line(*args, **kwargs):
            import sys

            print("stderr line", file=sys.stderr)
            raise RuntimeError("boom")

        with contextlib.suppress(Exception):
            asyncio.run(
                submit_tui_input(
                    session,
                    handle_chat_line=fake_handle_chat_line,
                    execute_cli_command=lambda *args, **kwargs: {},
                )
            )
        if session.worker is not None:
            session.worker.join(timeout=2)

        texts = [item.text for item in session.output_lines]
        self.assertTrue(any("run failed: boom" in text for text in texts))
        self.assertTrue(any("stderr line" in text for text in texts))

    def test_submit_tui_input_filters_transient_progress_lines_from_captured_output(self) -> None:
        class DummyBuffer:
            def __init__(self, text: str) -> None:
                self.document = type("Doc", (), {"text": text})()
                self.complete_state = None

        class DummyInputArea:
            def __init__(self, text: str) -> None:
                self.text = text
                self.buffer = DummyBuffer(text)

        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )
        session.input_area = DummyInputArea("test")  # type: ignore[assignment]

        def fake_handle_chat_line(*args, **kwargs):
            import sys

            print("Please wait for a moment: 94% | 66/70 [00:11<00:00, 4.41it/s]", file=sys.stderr)
            print("100% | 1/1 [00:00<00:00, 9.67it/s]", file=sys.stderr)
            print("real stderr line", file=sys.stderr)
            return None

        asyncio.run(
            submit_tui_input(
                session,
                handle_chat_line=fake_handle_chat_line,
                execute_cli_command=lambda *args, **kwargs: {},
            )
        )
        if session.worker is not None:
            session.worker.join(timeout=2)

        texts = [item.text for item in session.output_lines]
        self.assertTrue(any("real stderr line" in text for text in texts))
        self.assertFalse(any("Please wait for a moment" in text for text in texts))
        self.assertFalse(any("100% | 1/1" in text for text in texts))

    def test_orchestrator_event_listener_receives_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            settings = Settings.from_env(repo_root)
            ensure_workspace_layout(settings)
            events: list[str] = []

            def listener(event) -> None:
                events.append(event.event_type)

            orchestrator = ResearchOrchestrator(settings, event_listener=listener)
            try:
                orchestrator.retriever.qdrant_available = False
                orchestrator.retriever.qdrant_error = "disabled_for_test"
                response = orchestrator.run(RequestEnvelope(intent="stock_check", user_query="帮我看下 000001 这个股票"))

                self.assertIn(response.status, {"completed", "failed"})
                self.assertIn("request_received", events)
                self.assertIn("run_accepted", events)
            finally:
                orchestrator.close()


if __name__ == "__main__":
    unittest.main()
