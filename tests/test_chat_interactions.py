from __future__ import annotations

import asyncio
import contextlib
import time
import unittest
from pathlib import Path

from research_system.cli.interactions import (
    emit_input_update,
    emit_meta_update,
    emit_warning_update,
    request_stop,
    submit_tui_input,
    toggle_commands_popup,
    write_tui_update,
)
from research_system.cli.rendering.chat_rendering import apply_tui_update
from research_system.cli.state.chat_types import ChatSessionState, TuiSession
from research_system.cli.state.chat_updates import default_update_for_text
from research_system.runtime.platform.settings import Settings
from research_system.services.routing.router.task_router import TaskRouter


class ProviderUnavailableStub:
    available = False


class _FakeBuffer:
    def __init__(self, text: str = "") -> None:
        self.document = type("Doc", (), {"text": text})()
        self.complete_state = None


class _FakeInputArea:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.buffer = _FakeBuffer(text)


class ChatInteractionsTests(unittest.TestCase):
    def test_write_tui_output_accepts_text_protocol_for_meta_and_warning(self) -> None:
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

    def test_write_tui_update_batches_stream_appends_before_flushing(self) -> None:
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
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )
        session.input_area = _FakeInputArea("who are you?")  # type: ignore[assignment]
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
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(last_run_id="01LATEST"),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )
        session.input_area = _FakeInputArea("who are you?")  # type: ignore[assignment]

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
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )
        session.input_area = _FakeInputArea("test")  # type: ignore[assignment]

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
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )
        session.input_area = _FakeInputArea("test")  # type: ignore[assignment]

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


if __name__ == "__main__":
    unittest.main()
