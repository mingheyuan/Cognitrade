from __future__ import annotations

import unittest
from pathlib import Path

from prompt_toolkit.keys import Keys

from research_system.cli.chat_keybindings import build_chat_keybindings
from research_system.cli.chat_types import ChatSessionState, TuiSession
from research_system.runtime.settings import Settings
from research_system.services.task_router import TaskRouter


class ProviderUnavailableStub:
    available = False


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


class _FakeEvent:
    def __init__(self, app: _FakeApp) -> None:
        self.app = app


def _find_binding(kb, key_name: str):
    for binding in kb.bindings:
        if [str(key) for key in binding.keys] == [key_name]:
            return binding
    raise AssertionError(f"binding not found: {key_name}")


class ChatKeybindingsTests(unittest.TestCase):
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
                "Keys.F2",
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
        self.assertEqual(session.output_mode, "browse")
        down_binding.handler(event)
        self.assertGreaterEqual(session.output_area.buffer.cursor_position, original_cursor - 3)
        self.assertFalse(session.follow_output)
        self.assertTrue(app.invalidated)
        self.assertEqual(focus_targets, [])

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
        self.assertEqual(session.output_mode, "browse")
        home.handler(event)
        self.assertEqual(session.output_area.buffer.cursor_position, 0)
        self.assertFalse(session.follow_output)
        end.handler(event)
        self.assertEqual(session.output_area.buffer.cursor_position, len(session.output_area.text))
        self.assertTrue(session.follow_output)
        self.assertEqual(session.output_mode, "live")
        self.assertEqual(focus_targets, [])

    def test_chat_keybindings_f2_toggles_output_mode(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )

        class _FakeOutputArea:
            def __init__(self) -> None:
                self.text = "line1\nline2\nline3"
                self.buffer = type("Buffer", (), {"cursor_position": len(self.text)})()

        session.output_area = _FakeOutputArea()  # type: ignore[assignment]
        session.input_area = _FakeInputArea("")  # type: ignore[assignment]
        app = _FakeApp()
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

        binding = _find_binding(kb, str(Keys.F2))
        event = _FakeEvent(app)
        self.assertEqual(session.output_mode, "live")
        binding.handler(event)
        self.assertEqual(session.output_mode, "browse")
        self.assertFalse(session.follow_output)
        binding.handler(event)
        self.assertEqual(session.output_mode, "live")
        self.assertTrue(session.follow_output)


if __name__ == "__main__":
    unittest.main()
