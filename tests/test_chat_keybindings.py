from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from prompt_toolkit.keys import Keys

from research_system.cli.chat_keybindings import build_chat_keybindings
from research_system.cli.state.chat_types import ChatSessionState, TuiSession
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
                "Keys.ControlF10",
                "Keys.ControlF11",
                "Keys.ControlF12",
                "Keys.ControlF3",
                "Keys.ControlF4",
                "Keys.ControlF8",
                "Keys.ControlF9",
                "Keys.ControlJ",
                "Keys.ControlM",
                "Keys.End",
                "Keys.Escape",
                "Keys.F1",
                "Keys.F10",
                "Keys.F11",
                "Keys.F12",
                "Keys.F2",
                "Keys.F3",
                "Keys.F4",
                "Keys.F5",
                "Keys.F6",
                "Keys.F7",
                "Keys.F8",
                "Keys.F9",
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

    def test_chat_keybindings_f2_toggles_trace_panel_and_loads_trace_payload(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(last_run_id="01TRACE"),
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
        trace_calls: list[tuple[str, str]] = []
        kb = build_chat_keybindings(
            session,
            handle_chat_line=lambda *args, **kwargs: None,
            execute_cli_command=lambda _settings, *, command, run_id, **kwargs: (
                trace_calls.append((command, run_id))
                or {
                    "run_id": run_id,
                    "request": {"intent": "stock_compare", "user_query": "compare banks"},
                    "summary": {"status": "completed"},
                    "subagent_trace": [],
                    "dynamic_trace": [],
                    "wait_trace": [],
                }
            ),
            submit_tui_input=lambda *args, **kwargs: None,
            request_stop=lambda *args, **kwargs: None,
            toggle_commands_popup=lambda *args, **kwargs: None,
            emit_warning_update=lambda *args, **kwargs: None,
        )

        binding = _find_binding(kb, str(Keys.F2))
        event = _FakeEvent(app)
        self.assertEqual(session.output_panel, "transcript")
        binding.handler(event)
        self.assertEqual(trace_calls, [("trace", "01TRACE")])
        self.assertEqual(session.output_panel, "trace")
        self.assertFalse(session.follow_output)
        self.assertEqual(session.trace_payload["run_id"], "01TRACE")
        binding.handler(event)
        self.assertEqual(session.output_panel, "transcript")
        self.assertEqual(session.output_mode, "live")
        self.assertTrue(session.follow_output)
        session.output_mode = "browse"
        session.follow_output = False
        binding.handler(event)
        self.assertEqual(session.output_panel, "trace")
        binding.handler(event)
        self.assertEqual(session.output_panel, "transcript")
        self.assertFalse(session.follow_output)
        self.assertEqual(session.output_mode, "browse")

    def test_chat_keybindings_f2_uses_live_trace_cache_without_cli_fetch(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(last_run_id="01TRACE"),
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
        session.trace_payload = {
            "run_id": "01TRACE",
            "request": {"intent": "stock_compare", "user_query": "compare banks"},
            "summary": {"status": "running"},
            "subagent_trace": [],
            "dynamic_trace": [],
            "wait_trace": [],
            "event_counts": {"events": 1, "subagent_trace": 0, "dynamic_trace": 0, "wait_trace": 0},
        }
        app = _FakeApp()
        session.app = app
        trace_calls: list[tuple[str, str]] = []
        kb = build_chat_keybindings(
            session,
            handle_chat_line=lambda *args, **kwargs: None,
            execute_cli_command=lambda _settings, *, command, run_id, **kwargs: trace_calls.append((command, run_id)) or {},
            submit_tui_input=lambda *args, **kwargs: None,
            request_stop=lambda *args, **kwargs: None,
            toggle_commands_popup=lambda *args, **kwargs: None,
            emit_warning_update=lambda *args, **kwargs: None,
        )

        binding = _find_binding(kb, str(Keys.F2))
        binding.handler(_FakeEvent(app))

        self.assertEqual(session.output_panel, "trace")
        self.assertEqual(trace_calls, [])

    def test_chat_keybindings_f6_cycles_trace_events_threads_panels(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(last_run_id="01TRACE"),
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
        session.output_panel = "trace"
        session.trace_payload = {
            "run_id": "01TRACE",
            "request": {"intent": "stock_compare", "user_query": "compare banks"},
            "summary": {"status": "running"},
            "subagent_trace": [],
            "dynamic_trace": [],
            "wait_trace": [],
            "event_counts": {"subagent_trace": 0, "dynamic_trace": 0, "wait_trace": 0},
        }
        app = _FakeApp()
        session.app = app
        calls: list[tuple[str, str]] = []
        kb = build_chat_keybindings(
            session,
            handle_chat_line=lambda *args, **kwargs: None,
            execute_cli_command=lambda _settings, *, command, run_id, **kwargs: (
                calls.append((command, run_id))
                or {
                    "run_id": run_id,
                    "request": {"intent": "stock_compare", "user_query": "compare banks"},
                    "summary": {"status": "completed"},
                    "event_counts": {"events": 1, "distinct_event_types": 1},
                    "events": [],
                }
            ),
            submit_tui_input=lambda *args, **kwargs: None,
            request_stop=lambda *args, **kwargs: None,
            toggle_commands_popup=lambda *args, **kwargs: None,
            emit_warning_update=lambda *args, **kwargs: None,
        )

        toggle_binding = _find_binding(kb, str(Keys.F6))
        toggle_binding.handler(_FakeEvent(app))
        self.assertEqual(session.output_panel, "events")
        self.assertEqual(calls, [("events", "01TRACE")])
        self.assertEqual(session.events_payload["run_id"], "01TRACE")
        toggle_binding.handler(_FakeEvent(app))
        self.assertEqual(session.output_panel, "threads")
        toggle_binding.handler(_FakeEvent(app))
        self.assertEqual(session.output_panel, "trace")

    def test_chat_keybindings_f6_opens_events_panel_with_trace_contextual_slice(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(last_run_id="01TRACE"),
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
        session.output_panel = "trace"
        session.trace_focus_index = 0
        session.trace_payload = {
            "run_id": "01TRACE",
            "request": {"intent": "stock_compare", "user_query": "compare banks"},
            "summary": {"status": "running"},
            "subagent_trace": [
                {
                    "action_id": "act_collect",
                    "node_id": "collect",
                    "phase": "collect",
                    "agent": "collector",
                    "task_name": "collect:compare:600519:stock_snapshot_collector",
                    "agent_path": "collector/compare/600519/stock_snapshot_collector",
                    "subagent_type": "stock_snapshot_collector",
                    "status": "running",
                }
            ],
            "dynamic_trace": [],
            "wait_trace": [],
            "event_counts": {"subagent_trace": 1, "dynamic_trace": 0, "wait_trace": 0},
        }
        app = _FakeApp()
        session.app = app
        calls: list[tuple[str, str]] = []
        kb = build_chat_keybindings(
            session,
            handle_chat_line=lambda *args, **kwargs: None,
            execute_cli_command=lambda _settings, *, command, run_id, **kwargs: (
                calls.append((command, run_id))
                or {
                    "run_id": run_id,
                    "request": {"intent": "stock_compare", "user_query": "compare banks"},
                    "summary": {"status": "running"},
                    "event_counts": {"events": 3, "distinct_event_types": 2},
                    "events": [
                        {
                            "event_type": "phase_started",
                            "phase": "collect",
                            "agent": "collector",
                            "message": "Phase started: collect",
                            "action_id": "act_collect",
                            "node_id": "collect",
                        },
                        {
                            "event_type": "subagent_started",
                            "phase": "collect",
                            "agent": "collector",
                            "message": "stock_snapshot_collector started",
                            "action_id": "act_collect",
                            "node_id": "collect",
                            "task_name": "collect:compare:600519:stock_snapshot_collector",
                        },
                        {
                            "event_type": "phase_started",
                            "phase": "report",
                            "agent": "reporter",
                            "message": "Phase started: report",
                            "action_id": "act_report",
                            "node_id": "report",
                        },
                    ],
                }
            ),
            submit_tui_input=lambda *args, **kwargs: None,
            request_stop=lambda *args, **kwargs: None,
            toggle_commands_popup=lambda *args, **kwargs: None,
            emit_warning_update=lambda *args, **kwargs: None,
        )

        toggle_binding = _find_binding(kb, str(Keys.F6))
        toggle_binding.handler(_FakeEvent(app))
        self.assertEqual(session.output_panel, "events")
        self.assertEqual(calls, [("events", "01TRACE")])
        self.assertEqual(session.events_focus_index, 0)

    def test_chat_keybindings_f3_and_f4_focus_and_clear_events_panel(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(last_run_id="01EVENTS"),
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
        session.output_panel = "events"
        session.events_payload = {
            "run_id": "01EVENTS",
            "request": {"intent": "stock_compare", "user_query": "compare banks"},
            "summary": {"status": "running"},
            "event_counts": {"events": 2, "distinct_event_types": 2},
            "events": [
                {"event_type": "phase_started", "phase": "collect", "agent": "collector", "message": "Phase started: collect"},
                {"event_type": "subagent_started", "phase": "collect", "agent": "collector", "message": "stock_snapshot_collector started"},
            ],
        }
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

        next_binding = _find_binding(kb, str(Keys.F3))
        clear_binding = _find_binding(kb, str(Keys.F4))
        next_binding.handler(_FakeEvent(app))
        self.assertEqual(session.events_focus_index, 0)
        next_binding.handler(_FakeEvent(app))
        self.assertEqual(session.events_focus_index, 1)
        clear_binding.handler(_FakeEvent(app))
        self.assertIsNone(session.events_focus_index)

    def test_chat_keybindings_resyncs_events_focus_when_trace_focus_changes(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(last_run_id="01TRACE"),
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
        session.output_panel = "events"
        session.trace_focus_index = 0
        session.events_focus_index = 1
        session.trace_payload = {
            "run_id": "01TRACE",
            "request": {"intent": "stock_compare", "user_query": "compare banks"},
            "summary": {"status": "running"},
            "subagent_trace": [
                {
                    "action_id": "act_collect",
                    "node_id": "collect",
                    "phase": "collect",
                    "agent": "collector",
                    "task_name": "collect:compare:600519:stock_snapshot_collector",
                    "agent_path": "collector/compare/600519/stock_snapshot_collector",
                    "subagent_type": "stock_snapshot_collector",
                    "status": "running",
                },
                {
                    "action_id": "act_report",
                    "node_id": "report",
                    "phase": "report",
                    "agent": "reporter",
                    "task_name": "report:bank_compare_report_composer",
                    "agent_path": "reporter/deterministic/bank_compare_report_composer",
                    "subagent_type": "bank_compare_report_composer",
                    "status": "completed",
                },
            ],
            "dynamic_trace": [],
            "wait_trace": [],
            "event_counts": {"subagent_trace": 2, "dynamic_trace": 0, "wait_trace": 0},
        }
        session.events_payload = {
            "run_id": "01TRACE",
            "request": {"intent": "stock_compare", "user_query": "compare banks"},
            "summary": {"status": "running"},
            "event_counts": {"events": 3, "distinct_event_types": 3},
            "events": [
                {
                    "event_type": "phase_started",
                    "phase": "collect",
                    "agent": "collector",
                    "message": "Phase started: collect",
                    "action_id": "act_collect",
                    "node_id": "collect",
                },
                {
                    "event_type": "phase_started",
                    "phase": "report",
                    "agent": "reporter",
                    "message": "Phase started: report",
                    "action_id": "act_report",
                    "node_id": "report",
                },
                {
                    "event_type": "subagent_completed",
                    "phase": "report",
                    "agent": "reporter",
                    "message": "bank_compare_report_composer completed",
                    "action_id": "act_report",
                    "node_id": "report",
                    "task_name": "report:bank_compare_report_composer",
                },
            ],
        }
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

        next_binding = _find_binding(kb, str(Keys.F3))
        next_binding.handler(_FakeEvent(app))
        self.assertEqual(session.events_focus_index, 0)

        session.output_panel = "trace"
        next_binding.handler(_FakeEvent(app))
        self.assertEqual(session.trace_focus_index, 1)
        self.assertEqual(session.events_focus_index, 0)

    def test_chat_keybindings_f9_jumps_from_focused_event_to_trace_call(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(last_run_id="01EVENTS"),
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
        session.output_panel = "events"
        session.events_focus_index = 0
        session.events_payload = {
            "run_id": "01TRACE",
            "request": {"intent": "stock_compare", "user_query": "compare banks"},
            "summary": {"status": "running"},
            "event_counts": {"events": 1, "distinct_event_types": 1},
            "events": [
                {
                    "event_type": "subagent_started",
                    "phase": "collect",
                    "agent": "collector",
                    "message": "stock_snapshot_collector started",
                    "action_id": "act_collect",
                    "node_id": "collect",
                    "task_name": "collect:compare:600519:stock_snapshot_collector",
                    "agent_path": "collector/compare/600519/stock_snapshot_collector",
                    "invocation_id": "01INV-600519",
                    "status": "running",
                }
            ],
        }
        trace_payload = {
            "run_id": "01TRACE",
            "request": {"intent": "stock_compare", "user_query": "compare banks"},
            "summary": {"status": "running"},
            "event_counts": {"subagent_trace": 1, "dynamic_trace": 0, "wait_trace": 0},
            "subagent_trace": [
                {
                    "action_id": "act_collect",
                    "node_id": "collect",
                    "phase": "collect",
                    "agent": "collector",
                    "task_name": "collect:compare:600519:stock_snapshot_collector",
                    "agent_path": "collector/compare/600519/stock_snapshot_collector",
                    "subagent_type": "stock_snapshot_collector",
                    "invocation_id": "01INV-600519",
                    "status": "running",
                }
            ],
            "dynamic_trace": [],
            "wait_trace": [],
        }
        app = _FakeApp()
        session.app = app
        calls: list[tuple[str, str]] = []
        kb = build_chat_keybindings(
            session,
            handle_chat_line=lambda *args, **kwargs: None,
            execute_cli_command=lambda _settings, *, command, run_id, **kwargs: calls.append((command, run_id)) or trace_payload,
            submit_tui_input=lambda *args, **kwargs: None,
            request_stop=lambda *args, **kwargs: None,
            toggle_commands_popup=lambda *args, **kwargs: None,
            emit_warning_update=lambda *args, **kwargs: None,
        )

        jump_binding = _find_binding(kb, str(Keys.F9))
        jump_binding.handler(_FakeEvent(app))

        self.assertEqual(calls, [("trace", "01TRACE")])
        self.assertEqual(session.output_panel, "trace")
        self.assertEqual(session.trace_focus_index, 0)
        self.assertEqual(session.trace_branch_focus_path, ("collector", "compare", "600519"))
        self.assertEqual(session.trace_child_focus_index, 0)

    def test_chat_keybindings_f9_jumps_from_focused_trace_call_to_events_panel(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(last_run_id="01TRACE"),
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
        session.output_panel = "trace"
        session.trace_focus_index = 0
        session.trace_branch_focus_path = ("collector", "compare", "600519")
        session.trace_child_focus_index = 0
        session.trace_payload = {
            "run_id": "01TRACE",
            "request": {"intent": "stock_compare", "user_query": "compare banks"},
            "summary": {"status": "running"},
            "event_counts": {"subagent_trace": 1, "dynamic_trace": 0, "wait_trace": 0},
            "subagent_trace": [
                {
                    "action_id": "act_collect",
                    "node_id": "collect",
                    "phase": "collect",
                    "agent": "collector",
                    "task_name": "collect:compare:600519:stock_snapshot_collector",
                    "agent_path": "collector/compare/600519/stock_snapshot_collector",
                    "subagent_type": "stock_snapshot_collector",
                    "invocation_id": "01INV-600519",
                    "status": "running",
                }
            ],
            "dynamic_trace": [],
            "wait_trace": [],
        }
        events_payload = {
            "run_id": "01TRACE",
            "request": {"intent": "stock_compare", "user_query": "compare banks"},
            "summary": {"status": "running"},
            "event_counts": {"events": 2, "distinct_event_types": 2},
            "events": [
                {
                    "event_type": "phase_started",
                    "phase": "collect",
                    "agent": "collector",
                    "message": "Phase started: collect",
                    "node_id": "collect",
                },
                {
                    "event_type": "subagent_started",
                    "phase": "collect",
                    "agent": "collector",
                    "message": "stock_snapshot_collector started",
                    "action_id": "act_collect",
                    "node_id": "collect",
                    "task_name": "collect:compare:600519:stock_snapshot_collector",
                    "agent_path": "collector/compare/600519/stock_snapshot_collector",
                    "invocation_id": "01INV-600519",
                    "status": "running",
                },
            ],
        }
        app = _FakeApp()
        session.app = app
        calls: list[tuple[str, str]] = []
        kb = build_chat_keybindings(
            session,
            handle_chat_line=lambda *args, **kwargs: None,
            execute_cli_command=lambda _settings, *, command, run_id, **kwargs: calls.append((command, run_id)) or events_payload,
            submit_tui_input=lambda *args, **kwargs: None,
            request_stop=lambda *args, **kwargs: None,
            toggle_commands_popup=lambda *args, **kwargs: None,
            emit_warning_update=lambda *args, **kwargs: None,
        )

        jump_binding = _find_binding(kb, str(Keys.F9))
        jump_binding.handler(_FakeEvent(app))

        self.assertEqual(calls, [("events", "01TRACE")])
        self.assertEqual(session.output_panel, "events")
        self.assertEqual(session.events_focus_index, 1)

    def test_chat_keybindings_f9_prefers_latest_matching_event_for_focused_trace_task(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(last_run_id="01TRACE"),
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
        session.output_panel = "trace"
        session.trace_focus_index = 0
        session.trace_payload = {
            "run_id": "01TRACE",
            "request": {"intent": "stock_compare", "user_query": "compare banks"},
            "summary": {"status": "running"},
            "event_counts": {"subagent_trace": 1, "dynamic_trace": 0, "wait_trace": 0},
            "subagent_trace": [
                {
                    "action_id": "act_collect",
                    "node_id": "collect",
                    "phase": "collect",
                    "agent": "collector",
                    "task_name": "collect:compare:600519:stock_snapshot_collector",
                    "agent_path": "collector/compare/600519/stock_snapshot_collector",
                    "subagent_type": "stock_snapshot_collector",
                    "status": "completed",
                }
            ],
            "dynamic_trace": [],
            "wait_trace": [],
        }
        events_payload = {
            "run_id": "01TRACE",
            "request": {"intent": "stock_compare", "user_query": "compare banks"},
            "summary": {"status": "running"},
            "event_counts": {"events": 3, "distinct_event_types": 3},
            "events": [
                {
                    "event_type": "phase_started",
                    "phase": "collect",
                    "agent": "collector",
                    "message": "Phase started: collect",
                    "action_id": "act_collect",
                    "node_id": "collect",
                },
                {
                    "event_type": "subagent_started",
                    "phase": "collect",
                    "agent": "collector",
                    "message": "stock_snapshot_collector started",
                    "action_id": "act_collect",
                    "node_id": "collect",
                    "task_name": "collect:compare:600519:stock_snapshot_collector",
                },
                {
                    "event_type": "subagent_completed",
                    "phase": "collect",
                    "agent": "collector",
                    "message": "stock_snapshot_collector completed",
                    "action_id": "act_collect",
                    "node_id": "collect",
                    "task_name": "collect:compare:600519:stock_snapshot_collector",
                    "status": "completed",
                },
            ],
        }
        app = _FakeApp()
        session.app = app
        kb = build_chat_keybindings(
            session,
            handle_chat_line=lambda *args, **kwargs: None,
            execute_cli_command=lambda _settings, *, command, run_id, **kwargs: events_payload,
            submit_tui_input=lambda *args, **kwargs: None,
            request_stop=lambda *args, **kwargs: None,
            toggle_commands_popup=lambda *args, **kwargs: None,
            emit_warning_update=lambda *args, **kwargs: None,
        )

        jump_binding = _find_binding(kb, str(Keys.F9))
        jump_binding.handler(_FakeEvent(app))

        self.assertEqual(session.output_panel, "events")
        self.assertEqual(session.events_focus_index, 2)

    def test_chat_keybindings_ctrl_f8_cycles_thread_tree_focus_and_syncs_thread_focus(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(thread_id="thread_child_02"),
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
        session.output_panel = "threads"
        session.thread_panel_scope = "all"
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

        ctrl_f8 = _find_binding(kb, str(Keys.ControlF8))
        with patch("research_system.cli.chat_keybindings.list_chat_threads") as list_threads:
            list_threads.return_value = [
                {
                    "thread_id": "thread_parent_00",
                    "turn_count": 7,
                    "thread_source": "new",
                    "parent_thread_id": None,
                    "topic_title": "Parent thread",
                    "topic_intent": "stock_check",
                    "archived": False,
                },
                {
                    "thread_id": "thread_child_02",
                    "turn_count": 3,
                    "thread_source": "fork",
                    "parent_thread_id": "thread_parent_00",
                    "topic_title": "Child thread",
                    "topic_intent": "stock_compare",
                    "archived": False,
                },
            ]
            ctrl_f8.handler(_FakeEvent(app))
            self.assertEqual(session.thread_tree_focus_index, 0)
            self.assertEqual(session.thread_focus_index, 0)
            ctrl_f8.handler(_FakeEvent(app))
            self.assertEqual(session.thread_tree_focus_index, 1)
            self.assertEqual(session.thread_focus_index, 1)

    def test_chat_keybindings_f3_cycles_trace_task_focus_and_f4_clears_it(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(last_run_id="01TRACE"),
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
        session.output_panel = "trace"
        session.trace_payload = {
            "run_id": "01TRACE",
            "request": {"intent": "stock_compare", "user_query": "compare banks"},
            "summary": {"status": "running"},
            "subagent_trace": [
                {
                    "action_id": "act_collect",
                    "node_id": "collect",
                    "phase": "collect",
                    "agent": "collector",
                    "status": "completed",
                    "subagent_type": "stock_snapshot_collector",
                },
                {
                    "action_id": "act_retrieve",
                    "node_id": "retrieve",
                    "phase": "retrieve",
                    "agent": "retriever",
                    "status": "completed",
                    "subagent_type": "retrieval_decider",
                },
            ],
            "dynamic_trace": [],
            "wait_trace": [],
        }
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

        next_binding = _find_binding(kb, str(Keys.F3))
        clear_binding = _find_binding(kb, str(Keys.F4))
        next_binding.handler(_FakeEvent(app))
        self.assertEqual(session.trace_focus_index, 0)
        self.assertEqual(session.output_area.buffer.cursor_position, 0)
        next_binding.handler(_FakeEvent(app))
        self.assertEqual(session.trace_focus_index, 1)
        next_binding.handler(_FakeEvent(app))
        self.assertEqual(session.trace_focus_index, 0)
        clear_binding.handler(_FakeEvent(app))
        self.assertIsNone(session.trace_focus_index)

    def test_chat_keybindings_ctrl_f9_cycles_trace_branch_focus_and_ctrl_f10_clears_it(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(last_run_id="01TRACE"),
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
        session.output_panel = "trace"
        session.trace_payload = {
            "run_id": "01TRACE",
            "request": {"intent": "stock_compare", "user_query": "compare banks"},
            "summary": {"status": "running"},
            "subagent_trace": [
                {
                    "action_id": "act_collect",
                    "node_id": "collect",
                    "phase": "collect",
                    "agent": "collector",
                    "status": "completed",
                    "task_name": "collect:stock_snapshot_collector",
                    "agent_path": "collector/stock_snapshot_collector",
                    "subagent_type": "stock_snapshot_collector",
                },
                {
                    "action_id": "act_collect",
                    "node_id": "collect",
                    "phase": "collect",
                    "agent": "collector",
                    "status": "completed",
                    "task_name": "collect:sector_snapshot_collector",
                    "agent_path": "collector/sector_snapshot_collector",
                    "subagent_type": "sector_snapshot_collector",
                },
            ],
            "dynamic_trace": [],
            "wait_trace": [],
        }
        session.trace_focus_index = 0
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

        next_binding = _find_binding(kb, str(Keys.ControlF9))
        clear_binding = _find_binding(kb, str(Keys.ControlF10))
        next_binding.handler(_FakeEvent(app))
        self.assertEqual(session.trace_branch_focus_path, ("collector",))
        next_binding.handler(_FakeEvent(app))
        self.assertEqual(session.trace_branch_focus_path, ("collector",))
        clear_binding.handler(_FakeEvent(app))
        self.assertEqual(session.trace_branch_focus_path, ("collector",))
        clear_binding.handler(_FakeEvent(app))
        self.assertIsNone(session.trace_branch_focus_path)

    def test_chat_keybindings_ctrl_f10_moves_up_nested_trace_branch_before_clearing(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(last_run_id="01TRACE"),
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
        session.output_panel = "trace"
        session.trace_payload = {
            "run_id": "01TRACE",
            "request": {"intent": "distill_kol_logic", "user_query": "distill records"},
            "summary": {"status": "running"},
            "subagent_trace": [
                {
                    "action_id": "act_distill",
                    "node_id": "distill",
                    "phase": "distill",
                    "agent": "distiller",
                    "status": "completed",
                    "task_name": "distill:chunks:chunk_1_of_2:llm_chunk_distiller",
                    "agent_path": "distiller/chunks/chunk_1_of_2/llm_chunk_distiller",
                    "subagent_type": "llm_chunk_distiller",
                },
                {
                    "action_id": "act_distill",
                    "node_id": "distill",
                    "phase": "distill",
                    "agent": "distiller",
                    "status": "completed",
                    "task_name": "distill:postprocess:chunk_result_merger",
                    "agent_path": "distiller/postprocess/chunk_result_merger",
                    "subagent_type": "chunk_result_merger",
                },
            ],
            "dynamic_trace": [],
            "wait_trace": [],
        }
        session.trace_focus_index = 0
        session.trace_branch_focus_path = ("distiller", "chunks", "chunk_1_of_2")
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

        clear_binding = _find_binding(kb, str(Keys.ControlF10))
        session.trace_child_focus_index = 0
        clear_binding.handler(_FakeEvent(app))
        self.assertEqual(session.trace_branch_focus_path, ("distiller", "chunks", "chunk_1_of_2"))
        self.assertIsNone(session.trace_child_focus_index)
        clear_binding.handler(_FakeEvent(app))
        self.assertEqual(session.trace_branch_focus_path, ("distiller", "chunks"))
        self.assertIsNone(session.trace_child_focus_index)
        session.trace_child_focus_index = 0
        clear_binding.handler(_FakeEvent(app))
        self.assertEqual(session.trace_branch_focus_path, ("distiller", "chunks"))
        self.assertIsNone(session.trace_child_focus_index)
        clear_binding.handler(_FakeEvent(app))
        self.assertEqual(session.trace_branch_focus_path, ("distiller",))
        self.assertIsNone(session.trace_child_focus_index)
        clear_binding.handler(_FakeEvent(app))
        self.assertIsNone(session.trace_branch_focus_path)
        self.assertIsNone(session.trace_child_focus_index)

    def test_chat_keybindings_ctrl_f11_toggles_trace_branch_collapse(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(last_run_id="01TRACE"),
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
        session.output_panel = "trace"
        session.trace_payload = {
            "run_id": "01TRACE",
            "request": {"intent": "stock_compare", "user_query": "compare banks"},
            "summary": {"status": "running"},
            "subagent_trace": [
                {
                    "action_id": "act_collect",
                    "node_id": "collect",
                    "phase": "collect",
                    "agent": "collector",
                    "status": "completed",
                    "task_name": "collect:stock_snapshot_collector",
                    "agent_path": "collector/stock_snapshot_collector",
                    "subagent_type": "stock_snapshot_collector",
                },
                {
                    "action_id": "act_collect",
                    "node_id": "collect",
                    "phase": "collect",
                    "agent": "collector",
                    "status": "completed",
                    "task_name": "collect:sector_snapshot_collector",
                    "agent_path": "collector/sector_snapshot_collector",
                    "subagent_type": "sector_snapshot_collector",
                },
            ],
            "dynamic_trace": [],
            "wait_trace": [],
        }
        session.trace_focus_index = 0
        session.trace_branch_focus_path = ("collector",)
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

        toggle_binding = _find_binding(kb, str(Keys.ControlF11))
        self.assertEqual(session.trace_collapsed_branches, set())
        toggle_binding.handler(_FakeEvent(app))
        self.assertEqual(session.trace_collapsed_branches, {("collector",)})
        toggle_binding.handler(_FakeEvent(app))
        self.assertEqual(session.trace_collapsed_branches, set())

    def test_chat_keybindings_ctrl_f9_drills_down_nested_trace_branches_before_leaving_subtree(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(last_run_id="01TRACE"),
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
        session.output_panel = "trace"
        session.trace_payload = {
            "run_id": "01TRACE",
            "request": {"intent": "distill_kol_logic", "user_query": "distill records"},
            "summary": {"status": "running"},
            "subagent_trace": [
                {
                    "action_id": "act_distill",
                    "node_id": "distill",
                    "phase": "distill",
                    "agent": "distiller",
                    "status": "completed",
                    "task_name": "distill:chunks:chunk_1_of_2:llm_chunk_distiller",
                    "agent_path": "distiller/chunks/chunk_1_of_2/llm_chunk_distiller",
                    "subagent_type": "llm_chunk_distiller",
                },
                {
                    "action_id": "act_distill",
                    "node_id": "distill",
                    "phase": "distill",
                    "agent": "distiller",
                    "status": "completed",
                    "task_name": "distill:chunks:chunk_2_of_2:llm_chunk_distiller",
                    "agent_path": "distiller/chunks/chunk_2_of_2/llm_chunk_distiller",
                    "subagent_type": "llm_chunk_distiller",
                },
                {
                    "action_id": "act_distill",
                    "node_id": "distill",
                    "phase": "distill",
                    "agent": "distiller",
                    "status": "completed",
                    "task_name": "distill:postprocess:chunk_result_merger",
                    "agent_path": "distiller/postprocess/chunk_result_merger",
                    "subagent_type": "chunk_result_merger",
                },
            ],
            "dynamic_trace": [],
            "wait_trace": [],
        }
        session.trace_focus_index = 0
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

        next_binding = _find_binding(kb, str(Keys.ControlF9))
        session.trace_child_focus_index = 0
        next_binding.handler(_FakeEvent(app))
        self.assertEqual(session.trace_branch_focus_path, ("distiller",))
        self.assertIsNone(session.trace_child_focus_index)
        session.trace_child_focus_index = 0
        next_binding.handler(_FakeEvent(app))
        self.assertEqual(session.trace_branch_focus_path, ("distiller", "chunks"))
        self.assertIsNone(session.trace_child_focus_index)
        session.trace_child_focus_index = 0
        next_binding.handler(_FakeEvent(app))
        self.assertEqual(session.trace_branch_focus_path, ("distiller", "chunks", "chunk_1_of_2"))
        self.assertEqual(session.trace_child_focus_index, 0)
        session.trace_child_focus_index = 0
        next_binding.handler(_FakeEvent(app))
        self.assertEqual(session.trace_branch_focus_path, ("distiller", "chunks", "chunk_2_of_2"))
        self.assertEqual(session.trace_child_focus_index, 0)
        next_binding.handler(_FakeEvent(app))
        self.assertEqual(session.trace_branch_focus_path, ("distiller", "postprocess"))
        self.assertEqual(session.trace_child_focus_index, 0)

    def test_chat_keybindings_ctrl_f9_cycles_leaf_branch_work_units_before_leaving_branch(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(last_run_id="01TRACE"),
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
        session.output_panel = "trace"
        session.trace_payload = {
            "run_id": "01TRACE",
            "request": {"intent": "stock_compare", "user_query": "compare banks"},
            "summary": {"status": "running"},
            "subagent_trace": [
                {
                    "action_id": "act_collect",
                    "node_id": "collect",
                    "phase": "collect",
                    "agent": "collector",
                    "status": "completed",
                    "task_name": "collect:compare:600519:stock_snapshot_collector",
                    "agent_path": "collector/compare/600519/stock_snapshot_collector",
                    "subagent_type": "stock_snapshot_collector",
                    "invocation_id": "01INV-600519-A",
                },
                {
                    "action_id": "act_collect",
                    "node_id": "collect",
                    "phase": "collect",
                    "agent": "collector",
                    "status": "completed",
                    "task_name": "collect:compare:600519:notice_snapshot_collector",
                    "agent_path": "collector/compare/600519/notice_snapshot_collector",
                    "subagent_type": "notice_snapshot_collector",
                    "invocation_id": "01INV-600519-B",
                },
                {
                    "action_id": "act_collect",
                    "node_id": "collect",
                    "phase": "collect",
                    "agent": "collector",
                    "status": "completed",
                    "task_name": "collect:compare:300750:stock_snapshot_collector",
                    "agent_path": "collector/compare/300750/stock_snapshot_collector",
                    "subagent_type": "stock_snapshot_collector",
                    "invocation_id": "01INV-300750-A",
                },
            ],
            "dynamic_trace": [],
            "wait_trace": [],
        }
        session.trace_focus_index = 0
        session.trace_branch_focus_path = ("collector", "compare", "600519")
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

        next_binding = _find_binding(kb, str(Keys.ControlF9))
        self.assertIsNone(session.trace_child_focus_index)
        next_binding.handler(_FakeEvent(app))
        self.assertEqual(session.trace_branch_focus_path, ("collector", "compare", "600519"))
        self.assertEqual(session.trace_child_focus_index, 0)
        next_binding.handler(_FakeEvent(app))
        self.assertEqual(session.trace_branch_focus_path, ("collector", "compare", "600519"))
        self.assertEqual(session.trace_child_focus_index, 1)
        next_binding.handler(_FakeEvent(app))
        self.assertEqual(session.trace_branch_focus_path, ("collector", "compare", "300750"))
        self.assertEqual(session.trace_child_focus_index, 0)

    def test_chat_keybindings_ctrl_f3_cycles_trace_child_focus_and_ctrl_f4_clears_it(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(last_run_id="01TRACE"),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )

        class _FakeOutputArea:
            def __init__(self) -> None:
                self.text = "line1\nline2\nline3"
                self.buffer = type("Buffer", (), {"cursor_position": len(self.text)})()

        session.output_area = _FakeOutputArea()  # type: ignore[assignment]
        session.output_panel = "trace"
        session.trace_payload = {
            "run_id": "01TRACE",
            "request": {"intent": "stock_compare", "user_query": "compare banks"},
            "summary": {"status": "running"},
            "subagent_trace": [
                {
                    "action_id": "act_retrieve",
                    "node_id": "retrieve",
                    "phase": "retrieve",
                    "agent": "retriever",
                    "status": "completed",
                    "task_name": "retrieve:retrieval_decider",
                    "subagent_type": "retrieval_decider",
                },
                {
                    "action_id": "act_retrieve",
                    "node_id": "retrieve",
                    "phase": "retrieve",
                    "agent": "retriever",
                    "status": "completed",
                    "task_name": "retrieve:artifact_evidence_indexer",
                    "subagent_type": "artifact_evidence_indexer",
                },
            ],
            "dynamic_trace": [],
            "wait_trace": [],
        }
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

        session.trace_focus_index = 0
        next_binding = _find_binding(kb, str(Keys.ControlF3))
        clear_binding = _find_binding(kb, str(Keys.ControlF4))
        next_binding.handler(_FakeEvent(app))
        self.assertEqual(session.trace_child_focus_index, 0)
        next_binding.handler(_FakeEvent(app))
        self.assertEqual(session.trace_child_focus_index, 1)
        next_binding.handler(_FakeEvent(app))
        self.assertEqual(session.trace_child_focus_index, 0)
        clear_binding.handler(_FakeEvent(app))
        self.assertIsNone(session.trace_child_focus_index)

    def test_chat_keybindings_ctrl_f3_cycles_trace_child_focus_within_focused_branch(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(last_run_id="01TRACE"),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )

        class _FakeOutputArea:
            def __init__(self) -> None:
                self.text = "line1\nline2\nline3"
                self.buffer = type("Buffer", (), {"cursor_position": len(self.text)})()

        session.output_area = _FakeOutputArea()  # type: ignore[assignment]
        session.output_panel = "trace"
        session.trace_payload = {
            "run_id": "01TRACE",
            "request": {"intent": "stock_compare", "user_query": "compare banks"},
            "summary": {"status": "running"},
            "subagent_trace": [
                {
                    "action_id": "act_collect",
                    "node_id": "collect",
                    "phase": "collect",
                    "agent": "collector",
                    "status": "completed",
                    "task_name": "collect:compare:600519:stock_snapshot_collector",
                    "agent_path": "collector/compare/600519/stock_snapshot_collector",
                    "subagent_type": "stock_snapshot_collector",
                },
                {
                    "action_id": "act_collect",
                    "node_id": "collect",
                    "phase": "collect",
                    "agent": "collector",
                    "status": "completed",
                    "task_name": "collect:compare:300750:stock_snapshot_collector",
                    "agent_path": "collector/compare/300750/stock_snapshot_collector",
                    "subagent_type": "stock_snapshot_collector",
                },
            ],
            "dynamic_trace": [],
            "wait_trace": [],
        }
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

        session.trace_focus_index = 0
        session.trace_branch_focus_path = ("collector", "compare", "600519")
        next_binding = _find_binding(kb, str(Keys.ControlF3))
        next_binding.handler(_FakeEvent(app))
        self.assertEqual(session.trace_child_focus_index, 0)
        next_binding.handler(_FakeEvent(app))
        self.assertEqual(session.trace_child_focus_index, 0)

    def test_chat_keybindings_f5_toggles_trace_raw_sections(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(last_run_id="01TRACE"),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )
        session.output_panel = "trace"
        session.trace_payload = {
            "run_id": "01TRACE",
            "request": {"intent": "stock_compare", "user_query": "compare banks"},
            "summary": {"status": "running"},
            "subagent_trace": [],
            "dynamic_trace": [],
            "wait_trace": [],
        }
        session.app = _FakeApp()
        kb = build_chat_keybindings(
            session,
            handle_chat_line=lambda *args, **kwargs: None,
            execute_cli_command=lambda *args, **kwargs: {},
            submit_tui_input=lambda *args, **kwargs: None,
            request_stop=lambda *args, **kwargs: None,
            toggle_commands_popup=lambda *args, **kwargs: None,
            emit_warning_update=lambda *args, **kwargs: None,
        )
        toggle_binding = _find_binding(kb, str(Keys.F5))
        self.assertTrue(session.trace_show_raw_sections)
        toggle_binding.handler(_FakeEvent(session.app))
        self.assertFalse(session.trace_show_raw_sections)
        toggle_binding.handler(_FakeEvent(session.app))
        self.assertTrue(session.trace_show_raw_sections)

    def test_chat_keybindings_f12_toggles_trace_child_details(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(last_run_id="01TRACE"),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )
        session.output_panel = "trace"
        session.trace_payload = {
            "run_id": "01TRACE",
            "request": {"intent": "stock_compare", "user_query": "compare banks"},
            "summary": {"status": "running"},
            "subagent_trace": [],
            "dynamic_trace": [],
            "wait_trace": [],
        }
        session.app = _FakeApp()
        kb = build_chat_keybindings(
            session,
            handle_chat_line=lambda *args, **kwargs: None,
            execute_cli_command=lambda *args, **kwargs: {},
            submit_tui_input=lambda *args, **kwargs: None,
            request_stop=lambda *args, **kwargs: None,
            toggle_commands_popup=lambda *args, **kwargs: None,
            emit_warning_update=lambda *args, **kwargs: None,
        )
        toggle_binding = _find_binding(kb, str(Keys.F12))
        self.assertTrue(session.trace_expand_child_details)
        toggle_binding.handler(_FakeEvent(session.app))
        self.assertFalse(session.trace_expand_child_details)
        toggle_binding.handler(_FakeEvent(session.app))
        self.assertTrue(session.trace_expand_child_details)

    def test_chat_keybindings_ctrl_f12_toggles_focused_trace_task_collapse(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(last_run_id="01TRACE"),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )
        session.output_panel = "trace"
        session.trace_payload = {
            "run_id": "01TRACE",
            "request": {"intent": "stock_compare", "user_query": "compare banks"},
            "summary": {"status": "running"},
            "subagent_trace": [
                {
                    "action_id": "act_retrieve",
                    "node_id": "retrieve",
                    "phase": "retrieve",
                    "agent": "retriever",
                    "status": "completed",
                    "subagent_type": "retrieval_decider",
                }
            ],
            "dynamic_trace": [],
            "wait_trace": [],
        }
        session.trace_focus_index = 0
        session.app = _FakeApp()
        kb = build_chat_keybindings(
            session,
            handle_chat_line=lambda *args, **kwargs: None,
            execute_cli_command=lambda *args, **kwargs: {},
            submit_tui_input=lambda *args, **kwargs: None,
            request_stop=lambda *args, **kwargs: None,
            toggle_commands_popup=lambda *args, **kwargs: None,
            emit_warning_update=lambda *args, **kwargs: None,
        )
        toggle_binding = _find_binding(kb, str(Keys.ControlF12))
        self.assertEqual(session.trace_collapsed_tasks, set())
        toggle_binding.handler(_FakeEvent(session.app))
        self.assertEqual(session.trace_collapsed_tasks, {"act_retrieve|retrieve|retriever"})
        toggle_binding.handler(_FakeEvent(session.app))
        self.assertEqual(session.trace_collapsed_tasks, set())

    def test_chat_keybindings_f6_toggles_between_trace_and_threads_panels(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(last_run_id="01TRACE"),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )
        session.output_panel = "trace"
        session.trace_payload = {
            "run_id": "01TRACE",
            "request": {"intent": "stock_compare", "user_query": "compare banks"},
            "summary": {"status": "running"},
            "subagent_trace": [],
            "dynamic_trace": [],
            "wait_trace": [],
        }
        session.app = _FakeApp()
        kb = build_chat_keybindings(
            session,
            handle_chat_line=lambda *args, **kwargs: None,
            execute_cli_command=lambda *args, **kwargs: {},
            submit_tui_input=lambda *args, **kwargs: None,
            request_stop=lambda *args, **kwargs: None,
            toggle_commands_popup=lambda *args, **kwargs: None,
            emit_warning_update=lambda *args, **kwargs: None,
        )
        toggle_binding = _find_binding(kb, str(Keys.F6))
        toggle_binding.handler(_FakeEvent(session.app))
        self.assertEqual(session.output_panel, "events")
        toggle_binding.handler(_FakeEvent(session.app))
        self.assertEqual(session.output_panel, "threads")
        toggle_binding.handler(_FakeEvent(session.app))
        self.assertEqual(session.output_panel, "trace")

    def test_chat_keybindings_f7_cycles_threads_panel_scope(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(thread_id="thread_active_01"),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )

        class _FakeOutputArea:
            def __init__(self) -> None:
                self.text = "thread1\nthread2\nthread3"
                self.buffer = type("Buffer", (), {"cursor_position": len(self.text)})()

        session.output_area = _FakeOutputArea()  # type: ignore[assignment]
        session.output_panel = "threads"
        session.thread_panel_scope = "active"
        session.app = _FakeApp()
        kb = build_chat_keybindings(
            session,
            handle_chat_line=lambda *args, **kwargs: None,
            execute_cli_command=lambda *args, **kwargs: {},
            submit_tui_input=lambda *args, **kwargs: None,
            request_stop=lambda *args, **kwargs: None,
            toggle_commands_popup=lambda *args, **kwargs: None,
            emit_warning_update=lambda *args, **kwargs: None,
        )
        cycle_binding = _find_binding(kb, str(Keys.F7))
        cycle_binding.handler(_FakeEvent(session.app))
        self.assertEqual(session.thread_panel_scope, "all")
        self.assertEqual(session.output_area.buffer.cursor_position, 0)
        cycle_binding.handler(_FakeEvent(session.app))
        self.assertEqual(session.thread_panel_scope, "archived")
        cycle_binding.handler(_FakeEvent(session.app))
        self.assertEqual(session.thread_panel_scope, "active")

    def test_chat_keybindings_f8_cycles_threads_panel_focus(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(thread_id="thread_active_01"),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )

        class _FakeOutputArea:
            def __init__(self) -> None:
                self.text = "thread1\nthread2\nthread3"
                self.buffer = type("Buffer", (), {"cursor_position": len(self.text)})()

        session.output_area = _FakeOutputArea()  # type: ignore[assignment]
        session.output_panel = "threads"
        session.thread_panel_scope = "active"
        session.app = _FakeApp()
        kb = build_chat_keybindings(
            session,
            handle_chat_line=lambda *args, **kwargs: None,
            execute_cli_command=lambda *args, **kwargs: {},
            submit_tui_input=lambda *args, **kwargs: None,
            request_stop=lambda *args, **kwargs: None,
            toggle_commands_popup=lambda *args, **kwargs: None,
            emit_warning_update=lambda *args, **kwargs: None,
        )
        focus_binding = _find_binding(kb, str(Keys.F8))
        from unittest.mock import patch

        with patch("research_system.cli.chat_keybindings.list_chat_threads") as list_threads:
            list_threads.return_value = [
                {"thread_id": "thread_active_01"},
                {"thread_id": "thread_parent_00"},
            ]
            focus_binding.handler(_FakeEvent(session.app))
            self.assertEqual(session.thread_focus_index, 0)
            focus_binding.handler(_FakeEvent(session.app))
            self.assertEqual(session.thread_focus_index, 1)
            focus_binding.handler(_FakeEvent(session.app))
            self.assertEqual(session.thread_focus_index, 0)

    def test_chat_keybindings_f9_switches_to_focused_thread(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(thread_id="thread_active_01"),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )
        session.output_panel = "threads"
        session.thread_panel_scope = "active"
        session.thread_focus_index = 1
        session.app = _FakeApp()
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
        switch_binding = _find_binding(kb, str(Keys.F9))
        from unittest.mock import patch

        with patch("research_system.cli.chat_keybindings.list_chat_threads") as list_threads, patch(
            "research_system.cli.chat_keybindings.resume_session_thread"
        ) as resume_thread:
            list_threads.return_value = [
                {"thread_id": "thread_active_01"},
                {"thread_id": "thread_parent_00"},
            ]
            resume_thread.return_value = {
                "thread_id": "thread_parent_00",
                "previous_thread_id": "thread_active_01",
                "has_checkpoint": True,
                "has_compact_summary": False,
            }
            switch_binding.handler(_FakeEvent(session.app))
            resume_thread.assert_called_once()
            self.assertEqual(resume_thread.call_args.kwargs["thread_id"], "thread_parent_00")
            self.assertEqual(warnings, [])
            meta_lines = [message.text for message in session.output_lines if message.kind == "meta"]
            self.assertIn("thread_resumed: thread_parent_00", meta_lines)
            self.assertIn("thread_checkpointed: yes", meta_lines)

    def test_chat_keybindings_f10_forks_focused_thread(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(thread_id="thread_active_01"),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )
        session.output_panel = "threads"
        session.thread_panel_scope = "active"
        session.thread_focus_index = 1
        session.app = _FakeApp()
        kb = build_chat_keybindings(
            session,
            handle_chat_line=lambda *args, **kwargs: None,
            execute_cli_command=lambda *args, **kwargs: {},
            submit_tui_input=lambda *args, **kwargs: None,
            request_stop=lambda *args, **kwargs: None,
            toggle_commands_popup=lambda *args, **kwargs: None,
            emit_warning_update=lambda *args, **kwargs: None,
        )
        fork_binding = _find_binding(kb, str(Keys.F10))
        from unittest.mock import patch

        with patch("research_system.cli.chat_keybindings.list_chat_threads") as list_threads, patch(
            "research_system.cli.chat_keybindings.fork_session_thread"
        ) as fork_thread:
            list_threads.side_effect = [
                [
                    {"thread_id": "thread_active_01", "archived": False},
                    {"thread_id": "thread_parent_00", "archived": False},
                ],
                [
                    {"thread_id": "thread_child_02", "archived": False},
                    {"thread_id": "thread_active_01", "archived": False},
                ],
                [
                    {"thread_id": "thread_child_02", "archived": False},
                    {"thread_id": "thread_active_01", "archived": False},
                ],
            ]
            fork_thread.return_value = "thread_child_02"
            fork_binding.handler(_FakeEvent(session.app))
            fork_thread.assert_called_once()
            self.assertEqual(fork_thread.call_args.kwargs["source_thread_id"], "thread_parent_00")
            self.assertEqual(session.thread_panel_scope, "active")
            self.assertEqual(session.thread_focus_index, 0)
            meta_lines = [message.text for message in session.output_lines if message.kind == "meta"]
            self.assertIn("forked thread: thread_child_02", meta_lines)
            self.assertIn("forked_from: thread_parent_00", meta_lines)

    def test_chat_keybindings_f11_archives_focused_thread(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(thread_id="thread_active_01"),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )
        session.output_panel = "threads"
        session.thread_panel_scope = "all"
        session.thread_focus_index = 0
        session.app = _FakeApp()
        kb = build_chat_keybindings(
            session,
            handle_chat_line=lambda *args, **kwargs: None,
            execute_cli_command=lambda *args, **kwargs: {},
            submit_tui_input=lambda *args, **kwargs: None,
            request_stop=lambda *args, **kwargs: None,
            toggle_commands_popup=lambda *args, **kwargs: None,
            emit_warning_update=lambda *args, **kwargs: None,
        )
        archive_binding = _find_binding(kb, str(Keys.F11))
        from unittest.mock import patch

        with patch("research_system.cli.chat_keybindings.list_chat_threads") as list_threads, patch(
            "research_system.cli.chat_keybindings.archive_session_thread"
        ) as archive_thread:
            list_threads.side_effect = [
                [{"thread_id": "thread_active_01", "archived": False}],
                [{"thread_id": "thread_active_01", "archived": True}],
                [{"thread_id": "thread_active_01", "archived": True}],
            ]
            archive_thread.return_value = {
                "thread_id": "thread_active_01",
                "archived_at": "2026-07-01T10:00:00Z",
                "next_thread_id": "thread_next_02",
            }
            archive_binding.handler(_FakeEvent(session.app))
            archive_thread.assert_called_once()
            meta_lines = [message.text for message in session.output_lines if message.kind == "meta"]
            self.assertIn("thread_archived: thread_active_01", meta_lines)
            self.assertIn("thread_archived_at: 2026-07-01T10:00:00Z", meta_lines)
            self.assertIn("thread_switched_to: thread_next_02", meta_lines)

    def test_chat_keybindings_f11_unarchives_focused_thread(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(thread_id="thread_active_01"),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )
        session.output_panel = "threads"
        session.thread_panel_scope = "archived"
        session.thread_focus_index = 0
        session.app = _FakeApp()
        kb = build_chat_keybindings(
            session,
            handle_chat_line=lambda *args, **kwargs: None,
            execute_cli_command=lambda *args, **kwargs: {},
            submit_tui_input=lambda *args, **kwargs: None,
            request_stop=lambda *args, **kwargs: None,
            toggle_commands_popup=lambda *args, **kwargs: None,
            emit_warning_update=lambda *args, **kwargs: None,
        )
        archive_binding = _find_binding(kb, str(Keys.F11))
        from unittest.mock import patch

        with patch("research_system.cli.chat_keybindings.list_chat_threads") as list_threads, patch(
            "research_system.cli.chat_keybindings.unarchive_session_thread"
        ) as unarchive_thread:
            list_threads.side_effect = [
                [{"thread_id": "thread_archived_01", "archived": True}],
                [],
                [],
            ]
            unarchive_thread.return_value = {
                "thread_id": "thread_archived_01",
                "unarchived_at": "2026-07-01T10:05:00Z",
            }
            archive_binding.handler(_FakeEvent(session.app))
            unarchive_thread.assert_called_once()
            meta_lines = [message.text for message in session.output_lines if message.kind == "meta"]
            self.assertIn("thread_unarchived: thread_archived_01", meta_lines)
            self.assertIn("thread_unarchived_at: 2026-07-01T10:05:00Z", meta_lines)


if __name__ == "__main__":
    unittest.main()
