from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from prompt_toolkit.data_structures import Point
from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType

from research_system.cli.app import _execute_cli_command
from research_system.cli.chat_command_catalog import print_chat_help
from research_system.cli.chat_rendering import apply_tui_update, build_tui_hint_text, refresh_tui, render_tui_output
from research_system.cli.chat_scene import (
    build_output_blocks,
    build_trace_task_blocks,
    build_commands_popup_view,
    build_hint_bar_view,
    build_shell_scene_view,
    build_status_bar_items,
    build_tui_blocks,
)
from research_system.cli.chat_transcript import make_transcript_message
from research_system.cli.chat_types import ChatSessionState, TuiSession
from research_system.cli.chat_updates import SessionStatePatch, ShellStatePatch, append_update, default_update_for_text, replace_update, thinking_update
from research_system.cli.chat_view import build_chat_application, build_chat_view, build_layout_children, sync_chat_view_from_scene
from research_system.cli.payload_rendering import print_payload
from research_system.runtime.settings import Settings


class _AppOrchestratorStub:
    instances: list["_AppOrchestratorStub"] = []

    def __init__(self, settings) -> None:
        self.settings = settings
        self.closed = False
        self.__class__.instances.append(self)

    def __enter__(self) -> "_AppOrchestratorStub":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        self.closed = True

    def history(self) -> list[dict]:
        return [{"run_id": "01RUN", "status": "completed", "intent": "stock_check"}]

    def list_proposals(self, *, status=None, limit=50) -> list[dict]:
        return [
            {
                "proposal_id": "01PROP",
                "status": status or "pending",
                "proposal_type": "MethodologyTemplatePatch",
                "title": "Promote policy catalyst",
                "sample_size": 1,
                "review_reason": "pending_manual_review_dimension_sample_size<2",
            }
        ]

    def show_proposal(self, proposal_id: str) -> dict:
        return {
            "proposal_id": proposal_id,
            "status": "pending",
            "proposal_type": "MethodologyTemplatePatch",
            "title": "Promote policy catalyst",
            "summary": "Promote candidate dimension",
            "sample_size": 1,
            "benchmark_scope": "event_driven",
            "review_reason": "pending_manual_review_dimension_sample_size<2",
        }

    def review_proposal(self, proposal_id: str, *, decision: str, reviewer: str, reason: str | None = None) -> dict:
        return {
            "proposal_id": proposal_id,
            "status": "approved" if decision == "approve" else "rejected",
            "proposal_type": "MethodologyTemplatePatch",
            "title": "Promote policy catalyst",
            "message": f"proposal {'approved' if decision == 'approve' else 'rejected'}",
            "result": {
                "proposal_id": proposal_id,
                "status": "approved" if decision == "approve" else "rejected",
                "proposal_type": "MethodologyTemplatePatch",
                "title": "Promote policy catalyst",
                "reviewer": reviewer,
                "review_reason": reason or "",
                "retrieval_policy": {"approved_proposal_ids": [proposal_id]},
            },
        }


class CliOutputTests(unittest.TestCase):
    def test_chat_help_includes_stop_command(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            print_chat_help()

        output = buffer.getvalue()
        self.assertIn("/stop", output)
        self.assertIn("show checkpoint resumability", output)

    def test_execute_cli_command_closes_orchestrator(self) -> None:
        _AppOrchestratorStub.instances.clear()
        with patch("research_system.cli.app.ResearchOrchestrator", _AppOrchestratorStub):
            rows = _execute_cli_command(
                Settings.from_env(Path(".")),
                command="history",
                limit=20,
            )

        self.assertEqual(rows, [{"run_id": "01RUN", "status": "completed", "intent": "stock_check"}])
        self.assertEqual(len(_AppOrchestratorStub.instances), 1)
        self.assertTrue(_AppOrchestratorStub.instances[0].closed)

    def test_execute_cli_command_supports_proposal_commands(self) -> None:
        _AppOrchestratorStub.instances.clear()
        with patch("research_system.cli.app.ResearchOrchestrator", _AppOrchestratorStub):
            rows = _execute_cli_command(
                Settings.from_env(Path(".")),
                command="proposals",
                limit=10,
                status_filter="pending",
            )
            detail = _execute_cli_command(
                Settings.from_env(Path(".")),
                command="proposal",
                run_id="01PROP",
            )
            reviewed = _execute_cli_command(
                Settings.from_env(Path(".")),
                command="review-proposal",
                run_id="01PROP",
                query="approve",
                intent="human_reviewer",
                status_filter="looks good",
            )

        self.assertEqual(rows[0]["proposal_id"], "01PROP")
        self.assertEqual(detail["proposal_id"], "01PROP")
        self.assertEqual(reviewed["status"], "approved")

    def test_render_tui_output_uses_ascii_sections(self) -> None:
        output = render_tui_output(
            [
                make_transcript_message(kind="thinking", text="先判断任务类型"),
                make_transcript_message(kind="status", text="collect > report"),
                make_transcript_message(kind="answer_stream", text="这是流式答案", stream_key="answer_stream"),
            ]
        )
        self.assertIn("[thinking]", output)
        self.assertIn("[status]", output)
        self.assertIn("[answer]", output)

    def test_build_tui_blocks_exposes_view_model(self) -> None:
        blocks = build_tui_blocks(
            [
                make_transcript_message(kind="thinking", text="plan first"),
                make_transcript_message(kind="meta", text="run_id: 01TEST"),
                make_transcript_message(kind="answer_stream", text="done", stream_key="answer_stream"),
            ]
        )
        self.assertEqual(blocks[0].title, "thinking")
        self.assertEqual(blocks[0].lines, ("plan first",))
        self.assertFalse(blocks[0].inline)
        self.assertIsNone(blocks[1].title)
        self.assertTrue(blocks[1].inline)
        self.assertEqual(blocks[2].title, "answer")

    def test_build_status_bar_items_exposes_view_model(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(current_intent="stock_screen", events_mode="full"),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        session.current_run_id = "01RUN"
        session.current_phase = "collect"
        items = build_status_bar_items(session)
        self.assertEqual(items[0].label, "mode")
        self.assertEqual(items[1].label, "thread")
        self.assertEqual(items[2].value, "stock_screen")
        self.assertEqual(items[3].value, "01RUN")
        self.assertEqual(items[4].value, "collect")
        self.assertEqual(items[6].label, "output")
        self.assertEqual(items[6].value, "transcript:live")

    def test_build_commands_popup_view_exposes_view_model(self) -> None:
        popup = build_commands_popup_view()
        self.assertEqual(popup.lines[0], "Commands")
        self.assertIn("/stop                     stop the current run", popup.lines)
        self.assertIn("/proposals [limit] [status] list methodology proposals", popup.lines)
        self.assertIn("/proposal <proposal_id>   show proposal detail", popup.lines)
        self.assertIn("F1      toggle popup help", popup.lines)
        self.assertIn("F3      next trace task focus", popup.lines)
        self.assertIn("F4      clear trace task focus", popup.lines)

    def test_build_shell_scene_view_exposes_unified_scene_model(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(current_intent="stock_screen", pending_clarification_question="question"),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[
                make_transcript_message(kind="thinking", text="plan first"),
                make_transcript_message(kind="answer_stream", text="done", stream_key="answer_stream"),
            ],
        )
        session.current_run_id = "01RUN"
        session.current_phase = "collect"
        session.commands_popup_visible = True
        session.help_mode = "commands"
        session.focus_target = "input"
        session.input_mode = "insert"

        scene = build_shell_scene_view(session)
        self.assertEqual(scene.transcript_blocks[0].title, "thinking")
        self.assertEqual(scene.header.brand, "COGNITRADE")
        self.assertEqual(scene.header.mode_tag, "HELP")
        self.assertEqual(scene.status_items[2].value, "stock_screen")
        self.assertIn("output", {item.label for item in scene.status_items})
        self.assertIn("popup |", scene.hint.text)
        self.assertEqual(scene.commands_popup.lines[0], "Commands")
        self.assertTrue(scene.commands_popup_visible)
        self.assertEqual(scene.input_bar.prompt, "help> ")
        self.assertEqual(scene.input_bar.focus_target, "input")
        self.assertEqual(scene.input_bar.mode_label, "insert")
        self.assertEqual(scene.status_bar_height, 1)
        self.assertEqual(scene.hint_bar_height, 0)
        self.assertEqual(scene.popup_layout.top, 2)
        self.assertEqual(scene.popup_layout.left, 4)
        self.assertEqual(scene.layout_mode, "help")

    def test_scene_layout_mode_variants_cover_normal_running_command_and_help(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        self.assertEqual(build_shell_scene_view(session).layout_mode, "normal")

        session.running = True
        running_scene = build_shell_scene_view(session)
        self.assertEqual(running_scene.layout_mode, "running")
        self.assertEqual(running_scene.input_bar.prompt, "wait> ")

        session.running = False
        session.input_mode = "command"
        command_scene = build_shell_scene_view(session)
        self.assertEqual(command_scene.layout_mode, "command")
        self.assertEqual(command_scene.input_bar.prompt, "/ ")

        session.help_mode = "commands"
        self.assertEqual(build_shell_scene_view(session).layout_mode, "help")

    def test_hint_bar_mentions_output_mode_and_transcript_navigation(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        hint = build_hint_bar_view(session)
        self.assertIn("output transcript:live", hint.text)
        self.assertIn("PageUp/PageDown", hint.text)
        session.output_mode = "browse"
        hint = build_hint_bar_view(session)
        self.assertIn("output transcript:browse", hint.text)

    def test_hint_bar_mentions_trace_panel_when_trace_is_active(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(last_run_id="01TRACE"),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        session.output_panel = "trace"
        hint = build_hint_bar_view(session)
        self.assertIn("trace panel", hint.text)
        self.assertIn("01TRACE", hint.text)

    def test_hint_bar_mentions_trace_focus_when_trace_task_is_selected(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(last_run_id="01TRACE"),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        session.output_panel = "trace"
        session.trace_focus_index = 1
        hint = build_hint_bar_view(session)
        self.assertIn("focus task 2", hint.text)
        self.assertIn("F4 all tasks", hint.text)

    def test_build_output_blocks_renders_trace_panel_sections(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(last_run_id="01TRACE"),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[make_transcript_message(kind="meta", text="should not render in trace panel")],
        )
        session.output_panel = "trace"
        session.trace_payload = {
            "run_id": "01TRACE",
            "request": {"intent": "stock_compare", "user_query": "compare banks"},
            "summary": {"status": "completed"},
            "display_summary": "done",
            "event_counts": {"subagent_trace": 1, "dynamic_trace": 0, "wait_trace": 0},
            "subagent_trace": [
                {
                    "action_id": "act_retrieve",
                    "node_id": "retrieve",
                    "action_objective": "Retrieve evidence.",
                    "subagent_type": "retrieval_decider",
                    "status": "completed",
                    "phase": "retrieve",
                    "agent": "retriever",
                }
            ],
            "dynamic_trace": [],
            "wait_trace": [],
        }
        blocks = build_output_blocks(session)
        self.assertEqual(blocks[0].title, "trace")
        self.assertTrue(any("run_id: 01TRACE" in line for line in blocks[0].lines))
        self.assertEqual(blocks[1].title, "retrieve | retrieve / retriever")
        self.assertTrue(any("task: retrieve" in line for line in blocks[1].lines))
        self.assertTrue(any("objective: Retrieve evidence." in line for line in blocks[1].lines))
        self.assertTrue(any("status_counts: completed=1" in line for line in blocks[1].lines))
        self.assertTrue(any("retrieval_decider" in line for line in blocks[1].lines))

    def test_build_trace_task_blocks_groups_subagent_dynamic_and_wait_by_phase_agent(self) -> None:
        payload = {
            "run_id": "01TRACE",
            "request": {"intent": "stock_compare", "user_query": "compare banks"},
            "summary": {"status": "running"},
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
            "wait_trace": [
                {
                    "event": "waiting_external_started",
                    "action_id": "act_collect",
                    "node_id": "collect",
                    "phase": "collect",
                    "agent": "collector",
                    "objective": "Load source records.",
                    "wait_timeout_seconds": 30,
                }
            ],
        }
        blocks = build_trace_task_blocks(payload)
        self.assertEqual(blocks[0].title, "retrieve | retrieve / retriever")
        self.assertTrue(any("task: retrieve" in line for line in blocks[0].lines))
        self.assertTrue(any("objective: Retrieve evidence." in line for line in blocks[0].lines))
        self.assertTrue(any("status_counts: completed=1" in line for line in blocks[0].lines))
        self.assertTrue(any("spawned_subagents:" in line for line in blocks[0].lines))
        self.assertTrue(any("retrieval_decider" in line for line in blocks[0].lines))
        self.assertEqual(blocks[1].title, "screen_review | review / screen_reviewer")
        self.assertTrue(any("dynamic_inserts:" in line for line in blocks[1].lines))
        self.assertEqual(blocks[2].title, "collect | collect / collector")
        self.assertTrue(any("waits:" in line for line in blocks[2].lines))

    def test_build_output_blocks_filters_trace_task_cards_when_focus_is_set(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(last_run_id="01TRACE"),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        session.output_panel = "trace"
        session.trace_payload = {
            "run_id": "01TRACE",
            "request": {"intent": "stock_compare", "user_query": "compare banks"},
            "summary": {"status": "running"},
            "subagent_trace": [
                {
                    "action_id": "act_collect",
                    "node_id": "collect",
                    "action_objective": "Load source records.",
                    "subagent_type": "stock_snapshot_collector",
                    "status": "completed",
                    "phase": "collect",
                    "agent": "collector",
                },
                {
                    "action_id": "act_retrieve",
                    "node_id": "retrieve",
                    "action_objective": "Retrieve evidence.",
                    "subagent_type": "retrieval_decider",
                    "status": "completed",
                    "phase": "retrieve",
                    "agent": "retriever",
                },
            ],
            "dynamic_trace": [],
            "wait_trace": [],
        }
        session.trace_focus_index = 1
        blocks = build_output_blocks(session)
        self.assertEqual(blocks[0].title, "trace")
        self.assertEqual(blocks[1].title, "retrieve | retrieve / retriever")
        self.assertFalse(any(block.title == "collect | collect / collector" for block in blocks[1:]))
        self.assertTrue(any(block.title == "subagent_trace" for block in blocks[1:]))

    def test_build_chat_view_composes_prompt_toolkit_components(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        session.input_mode = "command"
        view = build_chat_view(session)
        self.assertIsNotNone(view.output_area)
        self.assertIsNotNone(view.header_bar)
        self.assertIsNotNone(view.input_area)
        self.assertIsNotNone(view.status_bar)
        self.assertIsNotNone(view.hint_bar)
        self.assertFalse(view.input_area.buffer.multiline())
        self.assertEqual(view.input_area.window.height.preferred, 1)
        self.assertEqual(view.status_bar.height, 1)
        self.assertEqual(view.main_stack.children[0], view.header_bar)

    def test_build_chat_view_preserves_popup_focus_target_state(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        session.help_mode = "commands"
        session.commands_popup_visible = True
        session.focus_target = "commands_popup"
        view = build_chat_view(session)
        scene = build_shell_scene_view(session)
        self.assertIsNotNone(view.commands_popup)
        self.assertTrue(scene.commands_popup_visible)
        self.assertEqual(scene.input_bar.focus_target, "commands_popup")
        self.assertEqual(scene.popup_layout.bottom, 4)
        self.assertEqual(scene.popup_layout.right, 4)

    def test_build_chat_application_uses_explicit_terminal_io(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        view = build_chat_view(session)

        from unittest.mock import patch

        class DummyApp:
            pass

        with patch("research_system.cli.chat_view.create_terminal_io", return_value=("IN", "OUT")) as terminal_io, patch(
            "research_system.cli.chat_view.Application", return_value=DummyApp()
        ) as application_ctor:
            app = build_chat_application(session=session, view=view, key_bindings=None)

        terminal_io.assert_called_once()
        kwargs = application_ctor.call_args.kwargs
        self.assertEqual(kwargs["input"], "IN")
        self.assertEqual(kwargs["output"], "OUT")
        self.assertIsInstance(app, DummyApp)

    def test_refresh_tui_syncs_scene_state_into_widgets(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        view = build_chat_view(session)
        session.output_area = view.output_area
        session.main_stack = view.main_stack
        session.commands_popup = view.commands_popup
        session.commands_popup_float = view.commands_popup_float
        session.input_area = view.input_area
        session.status_bar = view.status_bar
        session.input_hint_bar = view.hint_bar

        session.help_mode = "commands"
        session.commands_popup_visible = True
        session.focus_target = "commands_popup"
        session.input_mode = "command"

        sync_chat_view_from_scene(session)

        self.assertFalse(session.input_area.buffer.multiline())
        self.assertEqual(session.input_area.window.height, 1)
        self.assertEqual(session.input_hint_bar.height, 0)
        self.assertEqual(session.commands_popup_float.top, 1)
        self.assertEqual(session.commands_popup_float.left, 2)
        self.assertEqual([type(child).__name__ for child in session.main_stack.children], ["Window"] * 4)
        self.assertTrue(all(hasattr(child, "preferred_height") for child in session.main_stack.children))
        self.assertEqual(session.main_stack.children[0], view.header_bar)

    def test_output_window_mouse_scroll_changes_vertical_scroll(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[make_transcript_message(kind="meta", text=f"line {index}") for index in range(30)],
        )
        view = build_chat_view(session)
        session.output_area = view.output_area
        session.main_stack = view.main_stack
        session.commands_popup = view.commands_popup
        session.commands_popup_float = view.commands_popup_float
        session.input_area = view.input_area
        session.status_bar = view.status_bar
        session.input_hint_bar = view.hint_bar

        refresh_tui(session)
        window = session.output_area.window
        window.height = 5
        session.output_area.buffer.load_history_if_not_yet_loaded = lambda: None
        # Force a realistic render info for the window scroll handler.
        ui_content = window.content.create_content(width=40, height=5)
        window.render_info = type(
            "Info",
            (),
            {
                "content_height": ui_content.line_count,
                "window_height": 5,
                "cursor_position": ui_content.cursor_position,
                "configured_scroll_offsets": window.scroll_offsets,
                "vertical_scroll": 3,
            },
        )()
        window.vertical_scroll = 3

        window._mouse_handler(
            MouseEvent(
                position=Point(x=0, y=0),
                event_type=MouseEventType.SCROLL_UP,
                button=MouseButton.NONE,
                modifiers=frozenset(),
            )
        )
        self.assertEqual(window.vertical_scroll, 2)

        window.render_info = type(
            "Info",
            (),
            {
                "content_height": ui_content.line_count,
                "window_height": 5,
                "cursor_position": ui_content.cursor_position,
                "configured_scroll_offsets": window.scroll_offsets,
                "vertical_scroll": 2,
            },
        )()
        window._mouse_handler(
            MouseEvent(
                position=Point(x=0, y=0),
                event_type=MouseEventType.SCROLL_DOWN,
                button=MouseButton.NONE,
                modifiers=frozenset(),
            )
        )
        self.assertEqual(window.vertical_scroll, 3)

    def test_output_area_is_focusable_for_history_navigation(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        view = build_chat_view(session)
        self.assertTrue(view.output_area.control.focusable())

    def test_sync_chat_view_from_scene_updates_main_layout_children_for_command_mode(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        view = build_chat_view(session)
        session.output_area = view.output_area
        session.main_stack = view.main_stack
        session.commands_popup = view.commands_popup
        session.commands_popup_float = view.commands_popup_float
        session.input_area = view.input_area
        session.status_bar = view.status_bar
        session.input_hint_bar = view.hint_bar

        session.input_mode = "command"
        sync_chat_view_from_scene(session)

        self.assertEqual([type(child).__name__ for child in session.main_stack.children], ["Window"] * 5)
        self.assertTrue(all(hasattr(child, "preferred_height") for child in session.main_stack.children))
        self.assertEqual(session.main_stack.children[0], view.header_bar)

    def test_refresh_tui_delegates_view_sync_and_updates_output(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[make_transcript_message(kind="thinking", text="plan first")],
        )
        view = build_chat_view(session)
        session.output_area = view.output_area
        session.main_stack = view.main_stack
        session.commands_popup = view.commands_popup
        session.commands_popup_float = view.commands_popup_float
        session.input_area = view.input_area
        session.status_bar = view.status_bar
        session.input_hint_bar = view.hint_bar

        session.help_mode = "commands"
        session.input_mode = "command"

        refresh_tui(session)

        self.assertIn("[thinking]", session.output_area.text)
        self.assertFalse(session.input_area.buffer.multiline())

    def test_refresh_tui_renders_trace_panel_blocks(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(last_run_id="01TRACE"),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[make_transcript_message(kind="thinking", text="plan first")],
        )
        view = build_chat_view(session)
        session.output_area = view.output_area
        session.main_stack = view.main_stack
        session.commands_popup = view.commands_popup
        session.commands_popup_float = view.commands_popup_float
        session.input_area = view.input_area
        session.status_bar = view.status_bar
        session.input_hint_bar = view.hint_bar
        session.output_panel = "trace"
        session.trace_payload = {
            "run_id": "01TRACE",
            "request": {"intent": "stock_compare", "user_query": "compare banks"},
            "summary": {"status": "completed"},
            "subagent_trace": [
                {
                    "action_id": "act_retrieve",
                    "node_id": "retrieve",
                    "action_objective": "Retrieve evidence.",
                    "subagent_type": "retrieval_decider",
                    "status": "completed",
                    "phase": "retrieve",
                    "agent": "retriever",
                }
            ],
            "dynamic_trace": [],
            "wait_trace": [],
        }

        refresh_tui(session)

        self.assertIn("[trace]", session.output_area.text)
        self.assertIn("run_id: 01TRACE", session.output_area.text)
        self.assertIn("[retrieve | retrieve / retriever]", session.output_area.text)
        self.assertIn("task: retrieve", session.output_area.text)
        self.assertIn("objective: Retrieve evidence.", session.output_area.text)
        self.assertIn("spawned_subagents:", session.output_area.text)
        self.assertIn("status_counts: completed=1", session.output_area.text)

    def test_apply_tui_update_live_trace_patch_populates_trace_panel_cache(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        apply_tui_update(
            session,
            append_update(
                "meta",
                "",
                state_patch=ShellStatePatch(
                    current_run_id="01TRACE",
                    trace_payload={
                        "run_id": "01TRACE",
                        "request": {"intent": "stock_compare", "user_query": "compare banks"},
                        "summary": {"status": "running"},
                        "subagent_trace": [{"subagent_type": "retrieval_decider"}],
                        "dynamic_trace": [],
                        "wait_trace": [],
                    },
                ),
            ),
        )
        self.assertEqual(session.current_run_id, "01TRACE")
        self.assertEqual(session.trace_payload["run_id"], "01TRACE")
        self.assertEqual(session.trace_payload["subagent_trace"][0]["subagent_type"], "retrieval_decider")

    def test_refresh_tui_skips_rewriting_output_when_text_is_unchanged(self) -> None:
        class _Buffer:
            def __init__(self) -> None:
                self.cursor_position = 0

        class _OutputArea:
            def __init__(self) -> None:
                self._text = ""
                self.text_set_count = 0
                self.buffer = _Buffer()
                self.window = type("Window", (), {"vertical_scroll": 0, "vertical_scroll_2": 0})()

            @property
            def text(self):
                return self._text

            @text.setter
            def text(self, value):
                self.text_set_count += 1
                self._text = value

        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[make_transcript_message(kind="answer", text="same text")],
        )
        session.output_area = _OutputArea()  # type: ignore[assignment]
        session.app = type("App", (), {"invalidate": lambda self: None})()  # type: ignore[assignment]

        refresh_tui(session)
        first_count = session.output_area.text_set_count
        refresh_tui(session)

        self.assertEqual(session.output_area.text_set_count, first_count)

    def test_sync_chat_view_from_scene_skips_repeated_focus_when_scene_is_unchanged(self) -> None:
        class _Layout:
            def __init__(self) -> None:
                self.focus_calls = []

            def focus(self, target) -> None:
                self.focus_calls.append(target)

        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        view = build_chat_view(session)
        session.output_area = view.output_area
        session.main_stack = view.main_stack
        session.commands_popup = view.commands_popup
        session.commands_popup_float = view.commands_popup_float
        session.input_area = view.input_area
        session.status_bar = view.status_bar
        session.input_hint_bar = view.hint_bar
        session.app = type("App", (), {"layout": _Layout()})()  # type: ignore[assignment]

        sync_chat_view_from_scene(session)
        first_focus_calls = len(session.app.layout.focus_calls)
        sync_chat_view_from_scene(session)

        self.assertEqual(len(session.app.layout.focus_calls), first_focus_calls)

    def test_refresh_tui_does_not_force_follow_when_output_window_scrolled_up(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[make_transcript_message(kind="thinking", text="plan first")],
        )
        view = build_chat_view(session)
        session.output_area = view.output_area
        session.main_stack = view.main_stack
        session.commands_popup = view.commands_popup
        session.commands_popup_float = view.commands_popup_float
        session.input_area = view.input_area
        session.status_bar = view.status_bar
        session.input_hint_bar = view.hint_bar

        session.output_area.text = "old transcript"
        session.output_area.window.vertical_scroll = 4
        session.output_area.buffer.cursor_position = 3
        session.follow_output = False
        session.output_lines = [
            make_transcript_message(kind="meta", text=f"line {index}")
            for index in range(8)
        ]

        refresh_tui(session)

        self.assertNotEqual(session.output_area.buffer.cursor_position, len(session.output_area.text))
        self.assertEqual(session.output_area.buffer.cursor_position, 3)

    def test_refresh_tui_respects_explicit_follow_output_state_even_at_scroll_zero(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[make_transcript_message(kind="thinking", text="plan first")],
        )
        view = build_chat_view(session)
        session.output_area = view.output_area
        session.main_stack = view.main_stack
        session.commands_popup = view.commands_popup
        session.commands_popup_float = view.commands_popup_float
        session.input_area = view.input_area
        session.status_bar = view.status_bar
        session.input_hint_bar = view.hint_bar

        session.output_area.text = "old transcript"
        session.output_area.window.vertical_scroll = 0
        session.output_area.buffer.cursor_position = 3
        session.follow_output = False

        refresh_tui(session)

        self.assertEqual(session.output_area.buffer.cursor_position, 3)
        self.assertNotEqual(session.output_area.buffer.cursor_position, len(session.output_area.text))

    def test_refresh_tui_preserves_secondary_wrapped_scroll_state(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[make_transcript_message(kind="meta", text="x" * 200)],
        )
        view = build_chat_view(session)
        session.output_area = view.output_area
        session.main_stack = view.main_stack
        session.commands_popup = view.commands_popup
        session.commands_popup_float = view.commands_popup_float
        session.input_area = view.input_area
        session.status_bar = view.status_bar
        session.input_hint_bar = view.hint_bar

        session.output_area.text = "wrapped transcript"
        session.output_area.window.vertical_scroll = 2
        session.output_area.window.vertical_scroll_2 = 3
        session.output_area.buffer.cursor_position = 5
        session.follow_output = False

        refresh_tui(session)

        self.assertEqual(session.output_area.buffer.cursor_position, 5)

    def test_scene_layout_variant_changes_with_help_and_command_mode(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        session.help_mode = "commands"
        session.input_mode = "command"
        scene = build_shell_scene_view(session)
        self.assertEqual(scene.layout_mode, "help")
        self.assertEqual(scene.input_bar.height, 1)
        self.assertEqual(scene.hint_bar_height, 0)
        self.assertEqual(scene.popup_layout.top, 1)
        self.assertEqual(scene.popup_layout.left, 2)

    def test_build_layout_children_uses_layout_mode_variants(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        normal_scene = build_shell_scene_view(session)
        normal_children = build_layout_children(normal_scene, "output", "status", "hint", "input")
        self.assertEqual(normal_children, ["output", "status", "hint", "input"])

        session.input_mode = "command"
        command_scene = build_shell_scene_view(session)
        command_children = build_layout_children(command_scene, "output", "status", "hint", "input")
        self.assertEqual(command_children, ["output", "hint", "input", "status"])

        session.help_mode = "commands"
        session.input_mode = "command"
        help_scene = build_shell_scene_view(session)
        help_children = build_layout_children(help_scene, "output", "status", "hint", "input")
        self.assertEqual(help_children, ["output", "status", "input"])

        session.help_mode = "hidden"
        session.running = True
        session.input_mode = "insert"
        running_scene = build_shell_scene_view(session)
        running_children = build_layout_children(running_scene, "output", "status", "hint", "input")
        self.assertEqual(running_children, ["output", "status", "hint", "input"])

    def test_build_hint_bar_view_exposes_view_model(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(pending_clarification_question="question"),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        hint = build_hint_bar_view(session)
        self.assertIn("clarification pending", hint.text)

    def test_hint_text_changes_when_clarification_is_pending(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(pending_clarification_question="你偏短线还是中线？"),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        hint = build_tui_hint_text(session)
        self.assertIn("clarification pending", hint[0][1])

    def test_print_rows_emits_run_context_feed_for_latest_row(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        print_payload(
            [
                {
                    "run_id": "01ROWLATEST",
                    "status": "completed",
                    "intent": "review_thesis",
                    "display_summary": "overall_verdict=confirmed; findings=3",
                },
                {
                    "run_id": "01OLDER",
                    "status": "failed",
                    "intent": "stock_check",
                    "display_summary": "symbol_missing",
                },
            ],
            as_json=False,
            emit_text=lambda output_writer, update: apply_tui_update(session, update),
            output_writer=lambda update: apply_tui_update(session, update),
        )
        self.assertEqual(session.current_run_id, "01ROWLATEST")
        self.assertEqual(session.run_status_label, "completed")
        self.assertEqual(session.state.last_run_id, "01ROWLATEST")

    def test_print_status_payload_emits_state_patch_for_latest_run(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        print_payload(
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
                        "run_id": "01LATEST",
                        "status": "completed",
                        "intent": "review_thesis",
                        "display_summary": "overall_verdict=confirmed; findings=3",
                    }
                ],
            },
            as_json=False,
            emit_text=lambda output_writer, update: apply_tui_update(session, update),
            output_writer=lambda update: apply_tui_update(session, update),
        )
        self.assertEqual(session.current_run_id, "01LATEST")
        self.assertEqual(session.run_status_label, "completed")
        self.assertEqual(session.state.last_run_id, "01LATEST")

    def test_print_replay_payload_emits_session_patch_for_last_run(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        print_payload(
            {
                "run_id": "01REPLAY",
                "request": {"intent": "stock_check", "user_query": "look at 000001"},
                "summary": {"status": "completed"},
                "events": [],
                "blackboard": [],
            },
            as_json=False,
            emit_text=lambda output_writer, update: apply_tui_update(session, update),
            output_writer=lambda update: apply_tui_update(session, update),
        )
        self.assertEqual(session.current_run_id, "01REPLAY")
        self.assertEqual(session.state.last_run_id, "01REPLAY")

    def test_print_replay_payload_renders_shared_run_detail_lines(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            print_payload(
                {
                    "run_id": "01REPLAY",
                    "request": {"intent": "stock_check", "user_query": "look at 000001"},
                    "summary": {"status": "completed"},
                    "display_summary": "done",
                    "events": [{}, {}],
                    "blackboard": [{}],
                },
                as_json=False,
                emit_text=lambda output_writer, update: print(update.text if getattr(update, "text", "") else "", end="\n"),
            )

        output = buffer.getvalue()
        self.assertIn("run_id: 01REPLAY", output)
        self.assertIn("intent: stock_check", output)
        self.assertIn("query: look at 000001", output)
        self.assertIn("summary: done", output)
        self.assertIn("status: completed", output)
        self.assertIn("events: 2", output)
        self.assertIn("blackboard_entries: 1", output)

    def test_print_trace_payload_renders_trace_sections(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            print_payload(
                {
                    "run_id": "01TRACE",
                    "request": {"intent": "stock_compare", "user_query": "compare banks"},
                    "summary": {"status": "completed"},
                    "display_summary": "done",
                    "event_counts": {"subagent_trace": 1, "dynamic_trace": 1, "wait_trace": 1},
                    "subagent_trace": [
                        {
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
                            "node_id": "screen_review",
                            "phase": "review",
                            "agent": "screen_reviewer",
                        }
                    ],
                    "wait_trace": [
                        {
                            "event": "waiting_external_started",
                            "phase": "collect",
                            "agent": "collector",
                            "wait_timeout_seconds": 30,
                        }
                    ],
                },
                as_json=False,
                emit_text=lambda output_writer, update: print(update.text if getattr(update, "text", "") else "", end="\n"),
            )

        output = buffer.getvalue()
        self.assertIn("run_id: 01TRACE", output)
        self.assertIn("subagent_trace:", output)
        self.assertIn("retrieval_decider", output)
        self.assertIn("dynamic_trace:", output)
        self.assertIn("screen_review", output)
        self.assertIn("wait_trace:", output)
        self.assertIn("waiting_external_started", output)

    def test_print_trace_payload_updates_tui_trace_payload_cache(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        payload = {
            "run_id": "01TRACE",
            "request": {"intent": "stock_compare", "user_query": "compare banks"},
            "summary": {"status": "completed"},
            "subagent_trace": [],
            "dynamic_trace": [],
            "wait_trace": [],
        }
        print_payload(
            payload,
            as_json=False,
            emit_text=lambda output_writer, update: apply_tui_update(session, update),
            output_writer=lambda update: apply_tui_update(session, update),
        )
        self.assertEqual(session.trace_payload, payload)

    def test_print_doctor_payload_groups_checks(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            print_payload(
                {
                    "status": "failed",
                    "checks": [
                        {"name": "env_file", "status": "passed", "path": "D:/work_26_6_24/Cognitrade/.env"},
                        {"name": "openai_api_key", "status": "failed", "detail": "missing"},
                        {"name": "qdrant_local_dir", "status": "skipped", "path": "D:/work_26_6_24/Cognitrade/workspace/qdrant_local"},
                        {"name": "router_runtime", "status": "passed", "detail": "version=file-v1, refresh_source=default, cached_version=file-v1"},
                        {"name": "provider_chain_market", "status": "passed", "detail": "providers=akshare_market,baostock_market; fallback=yes"},
                        {"name": "network_domestic", "status": "passed", "detail": "[{'name': 'cninfo', 'reachable': True}]"},
                    ],
                },
                as_json=False,
                emit_text=lambda output_writer, update: print(update.text if getattr(update, "text", "") else "", end="\n"),
            )

        output = buffer.getvalue()
        self.assertIn("doctor_status: failed", output)
        self.assertIn("failed:", output)
        self.assertIn("  openai_api_key  missing", output)
        self.assertIn("passed:", output)
        self.assertIn("  env_file  D:/work_26_6_24/Cognitrade/.env", output)
        self.assertIn("  router_runtime  version=file-v1, refresh_source=default, cached_version=file-v1", output)
        self.assertIn("  provider_chain_market  providers=akshare_market,baostock_market; fallback=yes", output)
        self.assertIn("  network_domestic  [{'name': 'cninfo', 'reachable': True}]", output)
        self.assertIn("skipped:", output)

    def test_print_doctor_payload_emits_status_patch(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        print_payload(
            {
                "status": "failed",
                "checks": [
                    {"name": "openai_api_key", "status": "failed", "detail": "missing"},
                ],
            },
            as_json=False,
            emit_text=lambda output_writer, update: apply_tui_update(session, update),
            output_writer=lambda update: apply_tui_update(session, update),
        )
        self.assertEqual(session.run_status_label, "doctor:failed")

    def test_print_payload_renders_proposal_rows_and_detail(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            print_payload(
                [
                    {
                        "proposal_id": "01PROP",
                        "status": "pending",
                        "proposal_type": "MethodologyTemplatePatch",
                        "title": "Promote policy catalyst",
                        "sample_size": 1,
                        "review_reason": "pending_manual_review_dimension_sample_size<2",
                    }
                ],
                as_json=False,
                emit_text=lambda output_writer, update: print(update.text if getattr(update, "text", "") else "", end="\n"),
            )
            print_payload(
                {
                    "proposal_id": "01PROP",
                    "status": "pending",
                    "proposal_type": "MethodologyTemplatePatch",
                    "title": "Promote policy catalyst",
                    "summary": "Promote candidate dimension",
                    "sample_size": 1,
                    "benchmark_scope": "event_driven",
                    "review_reason": "pending_manual_review_dimension_sample_size<2",
                    "review_history": [
                        {
                            "decision": "pending",
                            "reviewer": "system_threshold_gate",
                            "source": "auto_gate",
                            "reason": "pending_manual_review_dimension_sample_size<2",
                        }
                    ],
                },
                as_json=False,
                emit_text=lambda output_writer, update: print(update.text if getattr(update, "text", "") else "", end="\n"),
            )

        output = buffer.getvalue()
        self.assertIn("01PROP  [pending]  MethodologyTemplatePatch  Promote policy catalyst  sample_size=1", output)
        self.assertIn("proposal_id: 01PROP", output)
        self.assertIn("benchmark_scope: event_driven", output)
        self.assertIn("review_history:", output)
        self.assertIn("[pending] reviewer=system_threshold_gate source=auto_gate", output)

    def test_print_payload_renders_review_proposal_retrieval_policy_result(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            print_payload(
                {
                    "proposal_id": "01PROP",
                    "status": "approved",
                    "proposal_type": "MethodologyTemplatePatch",
                    "title": "Promote policy catalyst",
                    "result": {
                        "retrieval_policy": {"approved_proposal_ids": ["01PROP"]},
                    },
                },
                as_json=False,
                emit_text=lambda output_writer, update: print(update.text if getattr(update, "text", "") else "", end="\n"),
            )

        output = buffer.getvalue()
        self.assertIn("\"retrieval_policy\": {", output)
        self.assertIn("\"approved_proposal_ids\": [", output)

    def test_append_tui_line_updates_stream_message_in_place(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        apply_tui_update(session, default_update_for_text("answer_delta", "Hello"))
        first_id = session.output_lines[0].message_id
        apply_tui_update(session, default_update_for_text("answer_delta", ", world"))
        self.assertEqual(len(session.output_lines), 1)
        self.assertEqual(session.output_lines[0].message_id, first_id)
        self.assertEqual(session.output_lines[0].text, "Hello, world")
        self.assertEqual(session.output_lines[0].role, "answer")
        self.assertEqual(session.output_lines[0].update_mode, "stream")
        self.assertEqual(session.output_lines[0].scope, "turn")
        self.assertEqual(session.output_lines[0].slot_key, "answer_stream")

    def test_append_tui_line_replaces_status_in_place(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        apply_tui_update(session, default_update_for_text("status", "collect"))
        first_id = session.output_lines[0].message_id
        apply_tui_update(session, default_update_for_text("status", "report"))
        self.assertEqual(len(session.output_lines), 1)
        self.assertEqual(session.output_lines[0].message_id, first_id)
        self.assertEqual(session.output_lines[0].text, "report")
        self.assertEqual(session.output_lines[0].role, "status")
        self.assertEqual(session.output_lines[0].update_mode, "replace")
        self.assertEqual(session.output_lines[0].scope, "turn")
        self.assertEqual(session.output_lines[0].slot_key, "status")

    def test_append_tui_line_appends_meta_as_new_messages(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        apply_tui_update(session, default_update_for_text("meta", "line one"))
        apply_tui_update(session, default_update_for_text("meta", "line two"))
        self.assertEqual(len(session.output_lines), 2)
        self.assertNotEqual(session.output_lines[0].message_id, session.output_lines[1].message_id)
        self.assertEqual(session.output_lines[0].role, "system")
        self.assertEqual(session.output_lines[0].update_mode, "append")
        self.assertEqual(session.output_lines[0].scope, "persistent")

    def test_input_resets_turn_scoped_slots(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        apply_tui_update(session, default_update_for_text("status", "collect"))
        apply_tui_update(session, default_update_for_text("thinking", "working"))
        apply_tui_update(session, default_update_for_text("input", "> next turn"))
        apply_tui_update(session, default_update_for_text("status", "routing"))
        status_messages = [message for message in session.output_lines if message.kind == "status"]
        self.assertEqual(len(status_messages), 2)
        self.assertIn(status_messages[0].message_id, session.view_state.closed_message_ids)

    def test_input_does_not_close_previous_answer_stream_message(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        apply_tui_update(session, default_update_for_text("answer_delta", "first answer"))
        apply_tui_update(session, default_update_for_text("answer_done", ""))
        first_answer_id = session.output_lines[0].message_id
        apply_tui_update(session, default_update_for_text("input", "> next turn"))
        rendered = render_tui_output(session.output_lines)
        self.assertIn("first answer", rendered)
        self.assertNotIn(first_answer_id, session.view_state.closed_message_ids)

    def test_thinking_messages_accumulate_instead_of_replacing(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        apply_tui_update(session, default_update_for_text("thinking", "first thought"))
        apply_tui_update(session, default_update_for_text("thinking", "second thought"))
        thinking_messages = [message for message in session.output_lines if message.kind == "thinking"]
        self.assertEqual(len(thinking_messages), 2)
        self.assertEqual(thinking_messages[0].text, "first thought")
        self.assertEqual(thinking_messages[1].text, "second thought")

    def test_thinking_update_helper_appends_instead_of_replacing(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        apply_tui_update(session, thinking_update("first helper thought"))
        apply_tui_update(session, thinking_update("second helper thought"))
        thinking_messages = [message for message in session.output_lines if message.kind == "thinking"]
        self.assertEqual([message.text for message in thinking_messages], ["first helper thought", "second helper thought"])

    def test_apply_tui_update_accepts_transcript_update_protocol(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        apply_tui_update(session, replace_update("status", "collect"))
        apply_tui_update(session, replace_update("status", "report"))
        self.assertEqual(len(session.output_lines), 1)
        self.assertEqual(session.output_lines[0].text, "report")

    def test_apply_tui_update_uses_state_patch_without_text_parsing(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        apply_tui_update(
            session,
            append_update(
                "meta",
                "run metadata",
                state_patch=ShellStatePatch(current_run_id="01RUN", run_status_label="completed"),
            ),
        )
        self.assertEqual(session.current_run_id, "01RUN")
        self.assertEqual(session.run_status_label, "completed")

    def test_apply_tui_update_uses_session_patch_without_text_parsing(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        apply_tui_update(
            session,
            append_update(
                "status",
                "intent changed",
                session_patch=SessionStatePatch(
                    current_intent="stock_screen",
                    thinking_enabled=False,
                ),
            ),
        )
        self.assertEqual(session.state.current_intent, "stock_screen")
        self.assertFalse(session.state.thinking_enabled)

    def test_apply_tui_update_does_not_infer_state_from_plain_append_without_patch(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        apply_tui_update(session, append_update("meta", "run_id: 01TEXT"))
        self.assertIsNone(session.current_run_id)

    def test_apply_tui_update_uses_inferred_patch_from_default_text_update(self) -> None:
        from research_system.cli.chat_updates import default_update_for_text

        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        apply_tui_update(session, default_update_for_text("meta", "run_id: 01INFERRED"))
        apply_tui_update(session, default_update_for_text("status", "gate: EvidenceGate pass"))
        self.assertEqual(session.current_run_id, "01INFERRED")
        self.assertEqual(session.current_gate, "EvidenceGate pass")

    def test_print_payload_emits_state_patch_for_run_and_status(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        print_payload(
            {
                "run_id": "01PAYLOAD",
                "status": "completed",
                "intent": "stock_check",
                "message": "done",
            },
            as_json=False,
            emit_text=lambda output_writer, update: apply_tui_update(session, update),
            output_writer=lambda update: apply_tui_update(session, update),
        )
        self.assertEqual(session.current_run_id, "01PAYLOAD")
        self.assertEqual(session.run_status_label, "completed")


if __name__ == "__main__":
    unittest.main()
