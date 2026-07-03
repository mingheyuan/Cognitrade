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
    build_transcript_blocks_for_session,
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

    def test_build_transcript_blocks_for_session_splits_committed_transcript_and_live_tail(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[
                make_transcript_message(kind="thinking", text="plan first"),
                make_transcript_message(kind="meta", text="run_id: 01TEST"),
                make_transcript_message(kind="answer_stream", text="streaming answer", slot_key="answer_stream", stream_key="answer_stream"),
            ],
        )
        live_message = session.output_lines[-1]
        session.active_message_ids["answer_stream"] = live_message.message_id

        blocks = build_transcript_blocks_for_session(session)

        self.assertEqual(blocks[0].title, "thinking")
        self.assertIsNone(blocks[1].title)
        self.assertEqual(blocks[-1].title, "live_tail")
        self.assertEqual(blocks[-1].lines, ("[answer] streaming answer",))

    def test_build_transcript_blocks_for_session_falls_back_to_plain_transcript_without_active_tail(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[
                make_transcript_message(kind="thinking", text="plan first"),
                make_transcript_message(kind="answer_stream", text="done", stream_key="answer_stream"),
            ],
        )

        blocks = build_transcript_blocks_for_session(session)

        self.assertEqual([block.title for block in blocks], ["thinking", "answer"])

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
        self.assertEqual(items[7].label, "inspect")
        self.assertEqual(items[7].value, "-")

    def test_build_commands_popup_view_exposes_view_model(self) -> None:
        popup = build_commands_popup_view()
        self.assertEqual(popup.lines[0], "Commands")
        self.assertIn("/stop                     stop the current run", popup.lines)
        self.assertIn("/proposals [limit] [status] list methodology proposals", popup.lines)
        self.assertIn("/proposal <proposal_id>   show proposal detail", popup.lines)
        self.assertIn("F1      toggle popup help", popup.lines)
        self.assertIn("F3      next trace task focus", popup.lines)
        self.assertIn("F4      clear trace task focus", popup.lines)
        self.assertIn("F5      toggle trace raw sections", popup.lines)
        self.assertIn("F12     toggle child detail expansion", popup.lines)
        self.assertIn("Ctrl+F12 toggle focused task collapse", popup.lines)
        self.assertIn("Ctrl+F3  next child focus", popup.lines)
        self.assertIn("Ctrl+F4  clear child focus", popup.lines)
        self.assertIn("Ctrl+F9  next trace drill target", popup.lines)
        self.assertIn("Ctrl+F10 move up trace branch / clear call", popup.lines)
        self.assertIn("Ctrl+F11 collapse trace branch", popup.lines)
        self.assertIn("F6      cycle trace/events/threads panel", popup.lines)
        self.assertIn("F7      cycle threads panel scope", popup.lines)
        self.assertIn("F8      focus next thread", popup.lines)
        self.assertIn("Ctrl+F8  focus next thread tree node", popup.lines)
        self.assertIn("F9      resume focused thread", popup.lines)
        self.assertIn("F10     fork focused thread", popup.lines)
        self.assertIn("F11     archive/unarchive focused thread", popup.lines)

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
        self.assertEqual(scene.transcript_blocks[-1].title, "answer")
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
        self.assertIn("live-tail:off", hint.text)
        self.assertIn("PageUp/PageDown", hint.text)
        session.output_mode = "browse"
        hint = build_hint_bar_view(session)
        self.assertIn("output transcript:browse", hint.text)
        self.assertIn("live-tail:off", hint.text)

    @patch("research_system.cli.chat_scene.current_thread_summary")
    def test_hint_bar_uses_idle_transcript_context_when_shell_is_ready(self, current_summary) -> None:
        current_summary.return_value = {
            "thread_label": "Bank compare shortlist",
            "topic_title": "Bank leaders",
            "thread_status": "active",
            "recommended_next_step": "Compare PB and ROE side by side.",
        }
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        session.state.current_intent = "stock_compare"
        session.current_phase = "collect"
        session.run_status_label = "completed"

        hint = build_hint_bar_view(session)

        self.assertIn("ready | output transcript:live", hint.text)
        self.assertIn("context stock_compare / collect / completed", hint.text)
        self.assertIn("thread Bank compare shortlist", hint.text)
        self.assertIn("topic Bank leaders", hint.text)
        self.assertIn("next Compare PB and ROE side by side.", hint.text)
        self.assertIn("live-tail:off", hint.text)

    def test_hint_bar_gracefully_falls_back_when_idle_thread_context_is_unavailable(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )

        hint = build_hint_bar_view(session)

        self.assertIn("ready | output transcript:live", hint.text)
        self.assertNotIn("thread ", hint.text)
        self.assertNotIn("topic ", hint.text)

    def test_hint_bar_mentions_live_tail_when_transcript_has_active_slot(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[make_transcript_message(kind="answer_stream", text="streaming answer", slot_key="answer_stream", stream_key="answer_stream")],
        )
        session.active_message_ids["answer_stream"] = session.output_lines[0].message_id
        hint = build_hint_bar_view(session)
        self.assertIn("live-tail:on", hint.text)

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
        self.assertIn("raw:on", hint.text)
        self.assertIn("children:expanded", hint.text)
        self.assertIn("branches:open", hint.text)
        self.assertIn("tasks:mixed", hint.text)

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
        session.trace_child_focus_index = 0
        hint = build_hint_bar_view(session)
        self.assertIn("focus task 2", hint.text)
        self.assertIn("inspect:focused-call", hint.text)
        self.assertIn("child:1", hint.text)
        self.assertIn("branch:all", hint.text)
        self.assertIn("Ctrl+F11 collapse branch", hint.text)
        self.assertIn("F4 all tasks", hint.text)
        self.assertIn("Ctrl+F3 child", hint.text)
        self.assertIn("Ctrl+F12 collapse", hint.text)
        self.assertIn("Ctrl+F9 drill next", hint.text)

    def test_hint_bar_mentions_raw_off_when_trace_sections_are_collapsed(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(last_run_id="01TRACE"),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        session.output_panel = "trace"
        session.trace_show_raw_sections = False
        session.trace_expand_child_details = False
        hint = build_hint_bar_view(session)
        self.assertIn("raw:off", hint.text)
        self.assertIn("F5 raw", hint.text)
        self.assertIn("children:summary", hint.text)
        self.assertIn("F12 children", hint.text)

    def test_hint_bar_mentions_trace_branch_focus_when_selected(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(last_run_id="01TRACE"),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        session.output_panel = "trace"
        session.trace_focus_index = 0
        session.trace_branch_focus_path = ("collector",)
        hint = build_hint_bar_view(session)
        self.assertIn("inspect:branch", hint.text)
        self.assertIn("branch:collector", hint.text)
        self.assertIn("Ctrl+F10 up branch/clear call", hint.text)

    def test_hint_bar_mentions_trace_task_scope_when_only_task_is_focused(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(last_run_id="01TRACE"),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        session.output_panel = "trace"
        session.trace_focus_index = 0
        hint = build_hint_bar_view(session)
        self.assertIn("inspect:task", hint.text)

    def test_build_status_bar_items_exposes_trace_inspect_scope(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(last_run_id="01TRACE"),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        session.output_panel = "trace"
        session.trace_focus_index = 0
        items = build_status_bar_items(session)
        self.assertEqual(next(item.value for item in items if item.label == "inspect"), "task")
        session.trace_branch_focus_path = ("collector",)
        items = build_status_bar_items(session)
        self.assertEqual(next(item.value for item in items if item.label == "inspect"), "branch")
        session.trace_child_focus_index = 0
        items = build_status_bar_items(session)
        self.assertEqual(next(item.value for item in items if item.label == "inspect"), "call")

    def test_hint_bar_mentions_threads_panel_scope(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(thread_id="thread_active_01"),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        session.output_panel = "threads"
        session.thread_panel_scope = "active"
        hint = build_hint_bar_view(session)
        self.assertIn("threads panel", hint.text)
        self.assertIn("scope active", hint.text)
        self.assertIn("F6 trace", hint.text)
        self.assertIn("F7 next scope", hint.text)

    def test_hint_bar_mentions_events_panel(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(last_run_id="01EVENTS"),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        session.output_panel = "events"
        hint = build_hint_bar_view(session)
        self.assertIn("events panel", hint.text)
        self.assertIn("inspect:all", hint.text)
        self.assertIn("01EVENTS", hint.text)
        self.assertIn("F6 next panel", hint.text)
        self.assertIn("/events <run_id|last>", hint.text)

    def test_hint_bar_mentions_events_panel_trace_context_scope(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(last_run_id="01EVENTS"),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        session.output_panel = "events"
        session.trace_focus_index = 0
        session.trace_branch_focus_path = ("collector",)
        hint = build_hint_bar_view(session)
        self.assertIn("inspect:branch", hint.text)

    def test_build_status_bar_items_exposes_events_inspect_scope(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(last_run_id="01EVENTS"),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        session.output_panel = "events"
        items = build_status_bar_items(session)
        self.assertEqual(next(item.value for item in items if item.label == "inspect"), "all")
        session.trace_focus_index = 0
        items = build_status_bar_items(session)
        self.assertEqual(next(item.value for item in items if item.label == "inspect"), "task")
        session.trace_branch_focus_path = ("collector",)
        items = build_status_bar_items(session)
        self.assertEqual(next(item.value for item in items if item.label == "inspect"), "branch")

    def test_hint_bar_mentions_threads_focus_controls(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(thread_id="thread_active_01"),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        session.output_panel = "threads"
        session.thread_panel_scope = "active"
        session.thread_focus_index = 1
        hint = build_hint_bar_view(session)
        self.assertIn("focus thread 2", hint.text)
        self.assertIn("tree:all", hint.text)
        self.assertIn("F8 next thread", hint.text)
        self.assertIn("Ctrl+F8 next tree node", hint.text)
        self.assertIn("F9 resume", hint.text)
        self.assertIn("F10 fork", hint.text)
        self.assertIn("F11 archive", hint.text)

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
                    "task_name": "retrieve:retrieval_decider",
                    "agent_path": "retriever/retrieval_decider",
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
        self.assertTrue(any("path=retriever/retrieval_decider" in line for line in blocks[1].lines))
        self.assertTrue(any("retrieve:retrieval_decider" in line for line in blocks[1].lines))

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
                    "agent_path": "retriever/retrieval_decider",
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
        self.assertTrue(any(line == "  retriever/" for line in blocks[0].lines))
        self.assertTrue(any(line == "    retrieve:retrieval_decider" for line in blocks[0].lines))
        self.assertTrue(any("      path: retriever/retrieval_decider" in line for line in blocks[0].lines))
        self.assertTrue(any("      status_counts: completed=1" in line for line in blocks[0].lines))
        self.assertTrue(any("      objective: Select final evidence set and summarize retrieval." in line for line in blocks[0].lines))
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

    def test_build_output_blocks_hides_raw_trace_sections_when_disabled(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(last_run_id="01TRACE"),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        session.output_panel = "trace"
        session.trace_show_raw_sections = False
        session.trace_payload = {
            "run_id": "01TRACE",
            "request": {"intent": "stock_compare", "user_query": "compare banks"},
            "summary": {"status": "running"},
            "subagent_trace": [
                {
                    "action_id": "act_retrieve",
                    "node_id": "retrieve",
                    "action_objective": "Retrieve evidence.",
                    "task_name": "retrieve:retrieval_decider",
                    "agent_path": "retriever/retrieval_decider",
                    "subagent_type": "retrieval_decider",
                    "status": "completed",
                    "phase": "retrieve",
                    "agent": "retriever",
                }
            ],
            "dynamic_trace": [{"event": "dynamic_action_inserted", "node_id": "screen_review", "phase": "review", "agent": "screen_reviewer"}],
            "wait_trace": [{"event": "waiting_external_started", "node_id": "collect", "phase": "collect", "agent": "collector"}],
        }
        blocks = build_output_blocks(session)
        self.assertFalse(any(block.title == "subagent_trace" for block in blocks))
        self.assertFalse(any(block.title == "dynamic_trace" for block in blocks))
        self.assertFalse(any(block.title == "wait_trace" for block in blocks))

    def test_build_output_blocks_collapses_child_trace_details_when_disabled(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(last_run_id="01TRACE"),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        session.output_panel = "trace"
        session.trace_expand_child_details = False
        session.trace_payload = {
            "run_id": "01TRACE",
            "request": {"intent": "stock_compare", "user_query": "compare banks"},
            "summary": {"status": "running"},
            "subagent_trace": [
                {
                    "action_id": "act_retrieve",
                    "node_id": "retrieve",
                    "action_objective": "Retrieve evidence.",
                    "task_name": "retrieve:retrieval_decider",
                    "agent_path": "retriever/retrieval_decider",
                    "subagent_type": "retrieval_decider",
                    "status": "completed",
                    "phase": "retrieve",
                    "agent": "retriever",
                    "objective": "Select final evidence set and summarize retrieval.",
                }
            ],
            "dynamic_trace": [],
            "wait_trace": [],
        }
        blocks = build_output_blocks(session)
        task_block = next(block for block in blocks if block.title == "retrieve | retrieve / retriever")
        self.assertTrue(any(line == "spawned_subagents:" for line in task_block.lines))
        self.assertTrue(any(line == "  retriever/" for line in task_block.lines))
        self.assertTrue(any(line == "    retrieve:retrieval_decider" for line in task_block.lines))
        self.assertFalse(any(line.strip().startswith("path:") for line in task_block.lines))
        self.assertFalse(any(line.strip().startswith("status_counts:") and line.startswith("      ") for line in task_block.lines))
        self.assertFalse(any(line.strip().startswith("objective:") and line.startswith("      ") for line in task_block.lines))

    def test_build_output_blocks_collapses_focused_trace_task_when_task_is_marked_collapsed(self) -> None:
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
                    "action_id": "act_retrieve",
                    "node_id": "retrieve",
                    "action_objective": "Retrieve evidence.",
                    "task_name": "retrieve:retrieval_decider",
                    "agent_path": "retriever/retrieval_decider",
                    "subagent_type": "retrieval_decider",
                    "status": "completed",
                    "phase": "retrieve",
                    "agent": "retriever",
                    "objective": "Select final evidence set and summarize retrieval.",
                }
            ],
            "dynamic_trace": [],
            "wait_trace": [],
        }
        session.trace_focus_index = 0
        session.trace_collapsed_tasks = {"act_retrieve|retrieve|retriever"}
        blocks = build_output_blocks(session)
        task_block = next(block for block in blocks if block.title == "retrieve | retrieve / retriever [collapsed]")
        self.assertTrue(any(line == "spawned_subagents: 1 hidden" for line in task_block.lines))
        self.assertFalse(any(line.strip() == "retriever/" for line in task_block.lines))

    def test_build_output_blocks_collapses_focused_trace_branch_when_marked_collapsed(self) -> None:
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
                    "invocation_id": "01INV-STOCK",
                    "node_id": "collect",
                    "action_objective": "Collect bank snapshots.",
                    "task_name": "collect:stock_snapshot_collector",
                    "agent_path": "collector/stock_snapshot_collector",
                    "subagent_type": "stock_snapshot_collector",
                    "status": "completed",
                    "phase": "collect",
                    "agent": "collector",
                    "objective": "Collect stock-level verified snapshots.",
                }
            ],
            "dynamic_trace": [],
            "wait_trace": [],
        }
        session.trace_focus_index = 0
        session.trace_branch_focus_path = ("collector",)
        session.trace_collapsed_branches = {("collector",)}
        blocks = build_output_blocks(session)
        task_block = next(block for block in blocks if block.title == "collect | collect / collector")
        self.assertTrue(any(line.startswith("  > collector/ [collapsed]") for line in task_block.lines))
        self.assertFalse(any("Stock Snapshot Collector" in line or "collect:stock_snapshot_collector" in line for line in task_block.lines))

    def test_build_output_blocks_filters_to_focused_trace_child(self) -> None:
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
                    "action_id": "act_retrieve",
                    "node_id": "retrieve",
                    "action_objective": "Retrieve evidence.",
                    "task_name": "retrieve:retrieval_decider",
                    "agent_path": "retriever/retrieval_decider",
                    "subagent_type": "retrieval_decider",
                    "status": "completed",
                    "phase": "retrieve",
                    "agent": "retriever",
                    "objective": "Select final evidence set and summarize retrieval.",
                },
                {
                    "action_id": "act_retrieve",
                    "node_id": "retrieve",
                    "action_objective": "Retrieve evidence.",
                    "task_name": "retrieve:blackboard_evidence_indexer",
                    "agent_path": "retriever/blackboard_evidence_indexer",
                    "subagent_type": "blackboard_evidence_indexer",
                    "status": "completed",
                    "phase": "retrieve",
                    "agent": "retriever",
                    "objective": "Index blackboard entries and expose evidence candidates.",
                },
            ],
            "dynamic_trace": [],
            "wait_trace": [],
        }
        session.trace_focus_index = 0
        session.trace_child_focus_index = 1
        blocks = build_output_blocks(session)
        task_block = next(block for block in blocks if block.title == "retrieve | retrieve / retriever")
        self.assertTrue(any(line == "    retrieve:blackboard_evidence_indexer" for line in task_block.lines))
        self.assertFalse(any(line == "    retrieve:retrieval_decider" for line in task_block.lines))
        focused_child_block = next(block for block in blocks if block.title == "focused_work_unit")
        self.assertTrue(any(line == "work_unit: retrieve:blackboard_evidence_indexer" for line in focused_child_block.lines))
        self.assertTrue(any("parent_task: retrieve" in line for line in focused_child_block.lines))
        self.assertTrue(any("trace_rows:" in line for line in focused_child_block.lines))

    def test_build_output_blocks_filters_raw_trace_sections_to_focused_task(self) -> None:
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
        session.trace_focus_index = 1
        blocks = build_output_blocks(session)
        raw_subagent_block = next(block for block in blocks if block.title == "subagent_trace")
        self.assertTrue(any("retrieval_decider" in line for line in raw_subagent_block.lines))
        self.assertFalse(any("stock_snapshot_collector" in line for line in raw_subagent_block.lines))
        raw_dynamic_block = next(block for block in blocks if block.title == "dynamic_trace")
        self.assertTrue(any("dynamic_action_inserted" in line for line in raw_dynamic_block.lines))
        raw_wait_block = next(block for block in blocks if block.title == "wait_trace")
        self.assertTrue(any("waiting_external_started" in line for line in raw_wait_block.lines))

    def test_build_output_blocks_renders_focused_trace_child_call_inspection(self) -> None:
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
                    "invocation_id": "01INV-STOCK-A",
                    "node_id": "collect",
                    "action_objective": "Collect bank snapshots.",
                    "task_name": "collect:stock_snapshot_collector",
                    "agent_path": "collector/compare/600036/stock_snapshot_collector",
                    "agent_label": "Stock Snapshot Collector",
                    "subagent_type": "stock_snapshot_collector",
                    "status": "completed",
                    "phase": "collect",
                    "agent": "collector",
                    "objective": "Collect stock-level verified snapshots for 600036.",
                },
                {
                    "action_id": "act_collect",
                    "invocation_id": "01INV-STOCK-B",
                    "node_id": "collect",
                    "action_objective": "Collect bank snapshots.",
                    "task_name": "collect:stock_snapshot_collector",
                    "agent_path": "collector/compare/600036/stock_snapshot_collector",
                    "agent_label": "Stock Snapshot Collector",
                    "subagent_type": "stock_snapshot_collector",
                    "status": "completed",
                    "phase": "collect",
                    "agent": "collector",
                    "objective": "Collect stock-level verified snapshots for 600036 retry.",
                },
            ],
            "dynamic_trace": [],
            "wait_trace": [],
        }
        session.trace_focus_index = 0
        session.trace_child_focus_index = 1
        blocks = build_output_blocks(session)
        focused_child_block = next(block for block in blocks if block.title == "focused_work_unit")
        self.assertTrue(any(line == "work_unit: Stock Snapshot Collector" for line in focused_child_block.lines))
        self.assertTrue(any(line == "call_id: 01INV-STOCK-B" for line in focused_child_block.lines))
        self.assertTrue(any("agent_path: collector/compare/600036/stock_snapshot_collector" in line for line in focused_child_block.lines))
        self.assertTrue(any("objective: Collect stock-level verified snapshots for 600036 retry." in line for line in focused_child_block.lines))
        self.assertTrue(any("call_id=01INV-STOCK-B" in line for line in focused_child_block.lines))
        raw_subagent_block = next(block for block in blocks if block.title == "subagent_trace")
        self.assertTrue(any("call_id=01INV-STOCK-B" in line for line in raw_subagent_block.lines))
        self.assertFalse(any("call_id=01INV-STOCK-A" in line for line in raw_subagent_block.lines))

    def test_build_output_blocks_renders_focused_trace_child_within_focused_branch(self) -> None:
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
                    "invocation_id": "01INV-600519",
                    "node_id": "collect",
                    "action_objective": "Collect comparison snapshots.",
                    "task_name": "collect:compare:600519:stock_snapshot_collector",
                    "agent_path": "collector/compare/600519/stock_snapshot_collector",
                    "agent_label": "Stock Snapshot Collector",
                    "subagent_type": "stock_snapshot_collector",
                    "status": "completed",
                    "phase": "collect",
                    "agent": "collector",
                    "objective": "Collect stock-level verified snapshots for 600519.",
                },
                {
                    "action_id": "act_collect",
                    "invocation_id": "01INV-300750",
                    "node_id": "collect",
                    "action_objective": "Collect comparison snapshots.",
                    "task_name": "collect:compare:300750:stock_snapshot_collector",
                    "agent_path": "collector/compare/300750/stock_snapshot_collector",
                    "agent_label": "Stock Snapshot Collector",
                    "subagent_type": "stock_snapshot_collector",
                    "status": "completed",
                    "phase": "collect",
                    "agent": "collector",
                    "objective": "Collect stock-level verified snapshots for 300750.",
                },
            ],
            "dynamic_trace": [],
            "wait_trace": [],
        }
        session.trace_focus_index = 0
        session.trace_branch_focus_path = ("collector", "compare", "600519")
        session.trace_child_focus_index = 0
        session.events_payload = {
            "run_id": "01TRACE",
            "request": {"intent": "stock_compare", "user_query": "compare banks"},
            "summary": {"status": "running"},
            "event_counts": {"events": 2, "distinct_event_types": 2},
            "events": [
                {
                    "event_type": "subagent_started",
                    "phase": "collect",
                    "agent": "collector",
                    "message": "stock_snapshot_collector started",
                    "action_id": "act_collect",
                    "node_id": "collect",
                    "task_name": "collect:compare:600519:stock_snapshot_collector",
                    "invocation_id": "01INV-600519",
                    "status": "running",
                },
                {
                    "event_type": "subagent_completed",
                    "phase": "collect",
                    "agent": "collector",
                    "message": "stock_snapshot_collector completed",
                    "action_id": "act_collect",
                    "node_id": "collect",
                    "task_name": "collect:compare:300750:stock_snapshot_collector",
                    "invocation_id": "01INV-300750",
                    "status": "completed",
                },
            ],
        }
        blocks = build_output_blocks(session)
        focused_branch_block = next(block for block in blocks if block.title == "focused_branch")
        self.assertTrue(
            any(line == "branch: collector/compare/600519  @ linked-event:subagent_started:running" for line in focused_branch_block.lines)
        )
        self.assertTrue(any(line == "work_units: 1" for line in focused_branch_block.lines))
        self.assertTrue(any(line == "direct_work_units: 1" for line in focused_branch_block.lines))
        focused_child_block = next(block for block in blocks if block.title == "focused_work_unit")
        self.assertTrue(any(line == "call_id: 01INV-600519" for line in focused_child_block.lines))
        self.assertFalse(any("01INV-300750" in line for line in focused_child_block.lines))
        raw_subagent_block = next(block for block in blocks if block.title == "subagent_trace")
        self.assertTrue(any("call_id=01INV-600519" in line for line in raw_subagent_block.lines))
        self.assertFalse(any("call_id=01INV-300750" in line for line in raw_subagent_block.lines))

    def test_branch_drilldown_to_leaf_branch_naturally_exposes_first_work_unit(self) -> None:
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
            "request": {"intent": "distill_kol_logic", "user_query": "distill records"},
            "summary": {"status": "running"},
            "subagent_trace": [
                {
                    "action_id": "act_distill",
                    "node_id": "distill",
                    "action_objective": "Distill source content.",
                    "task_name": "distill:chunks:chunk_1_of_2:llm_chunk_distiller",
                    "agent_path": "distiller/chunks/chunk_1_of_2/llm_chunk_distiller",
                    "subagent_type": "llm_chunk_distiller",
                    "status": "completed",
                    "phase": "distill",
                    "agent": "distiller",
                    "objective": "Distill chunk_1_of_2 with LLM.",
                },
                {
                    "action_id": "act_distill",
                    "node_id": "distill",
                    "action_objective": "Distill source content.",
                    "task_name": "distill:chunks:chunk_2_of_2:llm_chunk_distiller",
                    "agent_path": "distiller/chunks/chunk_2_of_2/llm_chunk_distiller",
                    "subagent_type": "llm_chunk_distiller",
                    "status": "completed",
                    "phase": "distill",
                    "agent": "distiller",
                    "objective": "Distill chunk_2_of_2 with LLM.",
                },
            ],
            "dynamic_trace": [],
            "wait_trace": [],
        }
        session.trace_focus_index = 0
        session.trace_branch_focus_path = ("distiller", "chunks", "chunk_1_of_2")
        session.trace_child_focus_index = 0
        blocks = build_output_blocks(session)
        focused_branch_block = next(block for block in blocks if block.title == "focused_branch")
        self.assertTrue(any(line == "child_branches: -" or line == "child_branches: " for line in focused_branch_block.lines) is False)
        self.assertTrue(any(line == "direct_work_units: 1" for line in focused_branch_block.lines))
        self.assertTrue(any(line == "work_units: 1" for line in focused_branch_block.lines))
        focused_child_block = next(block for block in blocks if block.title == "focused_work_unit")
        self.assertTrue(any("work_unit: distill:chunks:chunk_1_of_2:llm_chunk_distiller" in line for line in focused_child_block.lines))

    def test_build_output_blocks_does_not_render_focused_work_unit_when_branch_changes_without_child_refocus(self) -> None:
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
            "request": {"intent": "distill_kol_logic", "user_query": "distill records"},
            "summary": {"status": "running"},
            "subagent_trace": [
                {
                    "action_id": "act_distill",
                    "node_id": "distill",
                    "action_objective": "Distill source content.",
                    "task_name": "distill:chunks:chunk_1_of_2:llm_chunk_distiller",
                    "agent_path": "distiller/chunks/chunk_1_of_2/llm_chunk_distiller",
                    "subagent_type": "llm_chunk_distiller",
                    "status": "completed",
                    "phase": "distill",
                    "agent": "distiller",
                    "objective": "Distill chunk_1_of_2 with LLM.",
                },
                {
                    "action_id": "act_distill",
                    "node_id": "distill",
                    "action_objective": "Distill source content.",
                    "task_name": "distill:postprocess:chunk_result_merger",
                    "agent_path": "distiller/postprocess/chunk_result_merger",
                    "subagent_type": "chunk_result_merger",
                    "status": "completed",
                    "phase": "distill",
                    "agent": "distiller",
                    "objective": "Merge chunk-level distillation outputs.",
                },
            ],
            "dynamic_trace": [],
            "wait_trace": [],
        }
        session.trace_focus_index = 0
        session.trace_branch_focus_path = ("distiller", "chunks")
        session.trace_child_focus_index = None
        blocks = build_output_blocks(session)
        self.assertFalse(any(block.title == "focused_work_unit" for block in blocks))

    def test_build_output_blocks_can_return_from_focused_work_unit_to_same_branch_context(self) -> None:
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
            "request": {"intent": "distill_kol_logic", "user_query": "distill records"},
            "summary": {"status": "running"},
            "subagent_trace": [
                {
                    "action_id": "act_distill",
                    "node_id": "distill",
                    "action_objective": "Distill source content.",
                    "task_name": "distill:chunks:chunk_1_of_2:llm_chunk_distiller",
                    "agent_path": "distiller/chunks/chunk_1_of_2/llm_chunk_distiller",
                    "subagent_type": "llm_chunk_distiller",
                    "status": "completed",
                    "phase": "distill",
                    "agent": "distiller",
                    "objective": "Distill chunk_1_of_2 with LLM.",
                },
            ],
            "dynamic_trace": [],
            "wait_trace": [],
        }
        session.trace_focus_index = 0
        session.trace_branch_focus_path = ("distiller", "chunks", "chunk_1_of_2")
        session.trace_child_focus_index = None
        blocks = build_output_blocks(session)
        self.assertFalse(any(block.title == "focused_work_unit" for block in blocks))
        focused_branch_block = next(block for block in blocks if block.title == "focused_branch")
        self.assertTrue(any(line == "branch: distiller/chunks/chunk_1_of_2" for line in focused_branch_block.lines))

    def test_build_trace_task_blocks_renders_child_lineage_tree_from_agent_path(self) -> None:
        payload = {
            "run_id": "01TRACE",
            "request": {"intent": "stock_compare", "user_query": "compare banks"},
            "summary": {"status": "running"},
            "subagent_trace": [
                {
                    "action_id": "act_collect",
                    "invocation_id": "01INV-STOCK",
                    "node_id": "collect",
                    "action_objective": "Collect bank snapshots.",
                    "task_name": "collect:stock_snapshot_collector",
                    "agent_path": "collector/stock_snapshot_collector",
                    "agent_label": "Stock Snapshot Collector",
                    "subagent_type": "stock_snapshot_collector",
                    "status": "completed",
                    "phase": "collect",
                    "agent": "collector",
                    "objective": "Collect stock-level verified snapshots.",
                },
                {
                    "action_id": "act_collect",
                    "invocation_id": "01INV-SECTOR",
                    "node_id": "collect",
                    "action_objective": "Collect bank snapshots.",
                    "task_name": "collect:sector_snapshot_collector",
                    "agent_path": "collector/sector_snapshot_collector",
                    "agent_label": "Sector Snapshot Collector",
                    "subagent_type": "sector_snapshot_collector",
                    "status": "completed",
                    "phase": "collect",
                    "agent": "collector",
                    "objective": "Collect sector-level verified snapshots.",
                },
            ],
            "dynamic_trace": [],
            "wait_trace": [],
        }
        blocks = build_trace_task_blocks(payload)
        self.assertEqual(blocks[0].title, "collect | collect / collector")
        self.assertTrue(any(line == "  collector/" for line in blocks[0].lines))
        self.assertTrue(any(line == "    Stock Snapshot Collector" for line in blocks[0].lines))
        self.assertTrue(any(line == "    Sector Snapshot Collector" for line in blocks[0].lines))
        self.assertTrue(any("      path: collector/stock_snapshot_collector" in line for line in blocks[0].lines))
        self.assertTrue(any("call_id: 01INV-STOCK" in line for line in blocks[0].lines))

    def test_build_trace_task_blocks_splits_same_child_label_by_invocation_id(self) -> None:
        payload = {
            "run_id": "01TRACE",
            "request": {"intent": "stock_compare", "user_query": "compare banks"},
            "summary": {"status": "running"},
            "subagent_trace": [
                {
                    "action_id": "act_collect",
                    "invocation_id": "01INV-STOCK-A",
                    "node_id": "collect",
                    "action_objective": "Collect bank snapshots.",
                    "task_name": "collect:stock_snapshot_collector",
                    "agent_path": "collector/compare/600036/stock_snapshot_collector",
                    "agent_label": "Stock Snapshot Collector",
                    "subagent_type": "stock_snapshot_collector",
                    "status": "completed",
                    "phase": "collect",
                    "agent": "collector",
                    "objective": "Collect stock-level verified snapshots for 600036.",
                },
                {
                    "action_id": "act_collect",
                    "invocation_id": "01INV-STOCK-B",
                    "node_id": "collect",
                    "action_objective": "Collect bank snapshots.",
                    "task_name": "collect:stock_snapshot_collector",
                    "agent_path": "collector/compare/600036/stock_snapshot_collector",
                    "agent_label": "Stock Snapshot Collector",
                    "subagent_type": "stock_snapshot_collector",
                    "status": "completed",
                    "phase": "collect",
                    "agent": "collector",
                    "objective": "Collect stock-level verified snapshots for 600036 retry.",
                },
            ],
            "dynamic_trace": [],
            "wait_trace": [],
        }
        blocks = build_trace_task_blocks(payload)
        self.assertEqual(blocks[0].title, "collect | collect / collector")
        self.assertEqual(
            sum(1 for line in blocks[0].lines if line == "        Stock Snapshot Collector"),
            2,
        )
        self.assertTrue(any("call_id: 01INV-STOCK-A" in line for line in blocks[0].lines))
        self.assertTrue(any("call_id: 01INV-STOCK-B" in line for line in blocks[0].lines))
        self.assertEqual(
            sum(
                1
                for line in blocks[0].lines
                if "status_counts: completed=1" in line
            ),
            2,
        )

    def test_build_trace_task_blocks_renders_deeper_child_lineage_tree(self) -> None:
        payload = {
            "run_id": "01TRACE",
            "request": {"intent": "distill_kol_logic", "user_query": "distill records"},
            "summary": {"status": "running"},
            "subagent_trace": [
                {
                    "action_id": "act_distill",
                    "node_id": "distill",
                    "action_objective": "Distill source content.",
                    "task_name": "distill:preprocess:source_normalizer",
                    "agent_path": "distiller/preprocess/source_normalizer",
                    "subagent_type": "distill_source_normalizer",
                    "status": "completed",
                    "phase": "distill",
                    "agent": "distiller",
                    "objective": "Normalize raw distillation source records.",
                },
                {
                    "action_id": "act_distill",
                    "node_id": "distill",
                    "action_objective": "Distill source content.",
                    "task_name": "distill:chunks:chunk_1_of_2:llm_chunk_distiller",
                    "agent_path": "distiller/chunks/chunk_1_of_2/llm_chunk_distiller",
                    "subagent_type": "llm_chunk_distiller",
                    "status": "completed",
                    "phase": "distill",
                    "agent": "distiller",
                    "objective": "Distill chunk_1_of_2 with LLM.",
                },
                {
                    "action_id": "act_distill",
                    "node_id": "distill",
                    "action_objective": "Distill source content.",
                    "task_name": "distill:postprocess:chunk_result_merger",
                    "agent_path": "distiller/postprocess/chunk_result_merger",
                    "subagent_type": "chunk_result_merger",
                    "status": "completed",
                    "phase": "distill",
                    "agent": "distiller",
                    "objective": "Merge chunk-level distillation outputs.",
                },
            ],
            "dynamic_trace": [],
            "wait_trace": [],
        }
        blocks = build_trace_task_blocks(payload)
        self.assertEqual(blocks[0].title, "distill | distill / distiller")
        self.assertTrue(any(line == "  distiller/" for line in blocks[0].lines))
        self.assertTrue(any(line == "    subtree: child_branches=3, direct_work_units=0, total_work_units=3" for line in blocks[0].lines))
        self.assertTrue(any(line == "    preprocess/" for line in blocks[0].lines))
        self.assertTrue(any(line == "      subtree: child_branches=0, direct_work_units=1, total_work_units=1" for line in blocks[0].lines))
        self.assertTrue(any(line == "      distill:preprocess:source_normalizer" for line in blocks[0].lines))
        self.assertTrue(any(line == "    chunks/" for line in blocks[0].lines))
        self.assertTrue(any(line == "      subtree: child_branches=1, direct_work_units=0, total_work_units=1" for line in blocks[0].lines))
        self.assertTrue(any(line == "      chunk_1_of_2/" for line in blocks[0].lines))
        self.assertTrue(any(line == "        subtree: child_branches=0, direct_work_units=1, total_work_units=1" for line in blocks[0].lines))
        self.assertTrue(any(line == "        distill:chunks:chunk_1_of_2:llm_chunk_distiller" for line in blocks[0].lines))
        self.assertTrue(any(line == "    postprocess/" for line in blocks[0].lines))
        self.assertTrue(any(line == "      subtree: child_branches=0, direct_work_units=1, total_work_units=1" for line in blocks[0].lines))
        self.assertTrue(any(line == "      distill:postprocess:chunk_result_merger" for line in blocks[0].lines))

    def test_build_trace_task_blocks_renders_compare_symbol_subtree(self) -> None:
        payload = {
            "run_id": "01TRACE",
            "request": {"intent": "stock_compare", "user_query": "compare maotai byd catl"},
            "summary": {"status": "running"},
            "subagent_trace": [
                {
                    "action_id": "act_collect",
                    "node_id": "collect",
                    "action_objective": "Collect comparison snapshots.",
                    "task_name": "collect:compare:600519:stock_snapshot_collector",
                    "agent_path": "collector/compare/600519/stock_snapshot_collector",
                    "subagent_type": "stock_snapshot_collector",
                    "status": "completed",
                    "phase": "collect",
                    "agent": "collector",
                    "objective": "Collect stock-level verified snapshots for comparison targets.",
                },
                {
                    "action_id": "act_collect",
                    "node_id": "collect",
                    "action_objective": "Collect comparison snapshots.",
                    "task_name": "collect:compare:300750:stock_snapshot_collector",
                    "agent_path": "collector/compare/300750/stock_snapshot_collector",
                    "subagent_type": "stock_snapshot_collector",
                    "status": "completed",
                    "phase": "collect",
                    "agent": "collector",
                    "objective": "Collect stock-level verified snapshots for comparison targets.",
                },
            ],
            "dynamic_trace": [],
            "wait_trace": [],
        }
        blocks = build_trace_task_blocks(payload)
        self.assertEqual(blocks[0].title, "collect | collect / collector")
        self.assertTrue(any(line == "  collector/" for line in blocks[0].lines))
        self.assertTrue(any(line == "    compare/" for line in blocks[0].lines))
        self.assertTrue(any(line == "      600519/" for line in blocks[0].lines))
        self.assertTrue(any(line == "        collect:compare:600519:stock_snapshot_collector" for line in blocks[0].lines))
        self.assertTrue(any(line == "      300750/" for line in blocks[0].lines))
        self.assertTrue(any(line == "        collect:compare:300750:stock_snapshot_collector" for line in blocks[0].lines))

    def test_build_trace_task_blocks_renders_nested_retriever_subtree(self) -> None:
        payload = {
            "run_id": "01TRACE",
            "request": {"intent": "stock_compare", "user_query": "compare banks"},
            "summary": {"status": "running"},
            "subagent_trace": [
                {
                    "action_id": "act_retrieve",
                    "node_id": "retrieve",
                    "action_objective": "Retrieve evidence.",
                    "task_name": "retrieve:indexing:blackboard_evidence_indexer",
                    "agent_path": "retriever/indexing/blackboard_evidence_indexer",
                    "subagent_type": "blackboard_evidence_indexer",
                    "status": "completed",
                    "phase": "retrieve",
                    "agent": "retriever",
                    "objective": "Index blackboard entries and expose evidence candidates.",
                },
                {
                    "action_id": "act_retrieve",
                    "node_id": "retrieve",
                    "action_objective": "Retrieve evidence.",
                    "task_name": "retrieve:search:qdrant_evidence_searcher",
                    "agent_path": "retriever/search/qdrant_evidence_searcher",
                    "subagent_type": "qdrant_evidence_searcher",
                    "status": "degraded",
                    "phase": "retrieve",
                    "agent": "retriever",
                    "objective": "Search indexed evidence candidates via Qdrant.",
                },
                {
                    "action_id": "act_retrieve",
                    "node_id": "retrieve",
                    "action_objective": "Retrieve evidence.",
                    "task_name": "retrieve:decision:retrieval_decider",
                    "agent_path": "retriever/decision/retrieval_decider",
                    "subagent_type": "retrieval_decider",
                    "status": "completed",
                    "phase": "retrieve",
                    "agent": "retriever",
                    "objective": "Select final evidence set and summarize retrieval.",
                },
            ],
            "dynamic_trace": [],
            "wait_trace": [],
        }
        blocks = build_trace_task_blocks(payload)
        self.assertEqual(blocks[0].title, "retrieve | retrieve / retriever")
        self.assertTrue(any(line == "  retriever/" for line in blocks[0].lines))
        self.assertTrue(any(line == "    subtree: child_branches=3, direct_work_units=0, total_work_units=3" for line in blocks[0].lines))
        self.assertTrue(any(line == "    indexing/" for line in blocks[0].lines))
        self.assertTrue(any(line == "      subtree: child_branches=0, direct_work_units=1, total_work_units=1" for line in blocks[0].lines))
        self.assertTrue(any(line == "      retrieve:indexing:blackboard_evidence_indexer" for line in blocks[0].lines))
        self.assertTrue(any(line == "    search/" for line in blocks[0].lines))
        self.assertTrue(any(line == "      subtree: child_branches=0, direct_work_units=1, total_work_units=1" for line in blocks[0].lines))
        self.assertTrue(any(line == "      retrieve:search:qdrant_evidence_searcher" for line in blocks[0].lines))
        self.assertTrue(any(line == "    decision/" for line in blocks[0].lines))
        self.assertTrue(any(line == "      subtree: child_branches=0, direct_work_units=1, total_work_units=1" for line in blocks[0].lines))
        self.assertTrue(any(line == "      retrieve:decision:retrieval_decider" for line in blocks[0].lines))

    def test_build_trace_task_blocks_renders_nested_reporter_subtree(self) -> None:
        payload = {
            "run_id": "01TRACE",
            "request": {"intent": "review_thesis", "user_query": "review thesis"},
            "summary": {"status": "running"},
            "subagent_trace": [
                {
                    "action_id": "act_report",
                    "node_id": "report",
                    "action_objective": "Compose final report.",
                    "task_name": "report:strategy:report_strategy_resolver",
                    "agent_path": "reporter/strategy/report_strategy_resolver",
                    "subagent_type": "report_strategy_resolver",
                    "status": "completed",
                    "phase": "report",
                    "agent": "reporter",
                    "objective": "Resolve report strategy from retrieval summary and planning context.",
                },
                {
                    "action_id": "act_report",
                    "node_id": "report",
                    "action_objective": "Compose final report.",
                    "task_name": "report:fallback:fallback_report_composer",
                    "agent_path": "reporter/fallback/fallback_report_composer",
                    "subagent_type": "fallback_report_composer",
                    "status": "completed",
                    "phase": "report",
                    "agent": "reporter",
                    "objective": "Compose degraded fallback report.",
                },
            ],
            "dynamic_trace": [],
            "wait_trace": [],
        }
        blocks = build_trace_task_blocks(payload)
        self.assertEqual(blocks[0].title, "report | report / reporter")
        self.assertTrue(any(line == "  reporter/" for line in blocks[0].lines))
        self.assertTrue(any(line == "    strategy/" for line in blocks[0].lines))
        self.assertTrue(any(line == "      report:strategy:report_strategy_resolver" for line in blocks[0].lines))
        self.assertTrue(any(line == "    fallback/" for line in blocks[0].lines))
        self.assertTrue(any(line == "      report:fallback:fallback_report_composer" for line in blocks[0].lines))

    def test_build_output_blocks_marks_focused_trace_branch(self) -> None:
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
                    "action_objective": "Collect bank snapshots.",
                    "task_name": "collect:stock_snapshot_collector",
                    "agent_path": "collector/stock_snapshot_collector",
                    "subagent_type": "stock_snapshot_collector",
                    "status": "completed",
                    "phase": "collect",
                    "agent": "collector",
                    "objective": "Collect stock-level verified snapshots.",
                },
                {
                    "action_id": "act_collect",
                    "node_id": "collect",
                    "action_objective": "Collect bank snapshots.",
                    "task_name": "collect:sector_snapshot_collector",
                    "agent_path": "collector/sector_snapshot_collector",
                    "subagent_type": "sector_snapshot_collector",
                    "status": "completed",
                    "phase": "collect",
                    "agent": "collector",
                    "objective": "Collect sector-level verified snapshots.",
                },
            ],
            "dynamic_trace": [],
            "wait_trace": [],
        }
        session.trace_focus_index = 0
        session.trace_branch_focus_path = ("collector",)
        blocks = build_output_blocks(session)
        task_block = next(block for block in blocks if block.title == "collect | collect / collector")
        self.assertTrue(any(line.startswith("  > collector/") for line in task_block.lines))

    def test_build_output_blocks_focuses_deeper_trace_branch_as_subtree(self) -> None:
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
                    "action_objective": "Collect comparison snapshots.",
                    "task_name": "collect:compare:600519:stock_snapshot_collector",
                    "agent_path": "collector/compare/600519/stock_snapshot_collector",
                    "subagent_type": "stock_snapshot_collector",
                    "status": "completed",
                    "phase": "collect",
                    "agent": "collector",
                    "objective": "Collect stock-level verified snapshots for comparison targets.",
                },
                {
                    "action_id": "act_collect",
                    "node_id": "collect",
                    "action_objective": "Collect comparison snapshots.",
                    "task_name": "collect:compare:300750:stock_snapshot_collector",
                    "agent_path": "collector/compare/300750/stock_snapshot_collector",
                    "subagent_type": "stock_snapshot_collector",
                    "status": "completed",
                    "phase": "collect",
                    "agent": "collector",
                    "objective": "Collect stock-level verified snapshots for comparison targets.",
                },
            ],
            "dynamic_trace": [],
            "wait_trace": [],
        }
        session.trace_focus_index = 0
        session.trace_branch_focus_path = ("collector", "compare", "600519")
        blocks = build_output_blocks(session)
        task_block = next(block for block in blocks if block.title == "collect | collect / collector")
        self.assertTrue(any(line.startswith("  - collector/") for line in task_block.lines))
        self.assertTrue(any(line.startswith("    - compare/") for line in task_block.lines))
        self.assertTrue(any(line.startswith("      > 600519/") for line in task_block.lines))
        self.assertFalse(any(line.strip() == "300750/" or "300750" in line for line in task_block.lines))
        focused_branch_block = next(block for block in blocks if block.title == "focused_branch")
        self.assertTrue(any(line == "branch: collector/compare/600519" for line in focused_branch_block.lines))
        self.assertTrue(any(line == "parent_branch: collector/compare" for line in focused_branch_block.lines))
        self.assertTrue(any(line == "next_work_unit_focus: collect:compare:600519:stock_snapshot_collector" for line in focused_branch_block.lines))
        self.assertTrue(any(line == "next_drill_target: collect:compare:600519:stock_snapshot_collector" for line in focused_branch_block.lines))
        self.assertTrue(any(line == "work_units: 1" for line in focused_branch_block.lines))
        self.assertTrue(any(line == "direct_work_units: 1" for line in focused_branch_block.lines))
        self.assertTrue(
            any(
                "collect:compare:600519:stock_snapshot_collector" in line
                and "path=collector/compare/600519/stock_snapshot_collector" in line
                for line in focused_branch_block.lines
            )
        )
        raw_subagent_block = next(block for block in blocks if block.title == "subagent_trace")
        self.assertTrue(any("600519" in line for line in raw_subagent_block.lines))
        self.assertFalse(any("300750" in line for line in raw_subagent_block.lines))

    def test_build_output_blocks_renders_events_panel(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(last_run_id="01EVENTS"),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        session.output_panel = "events"
        session.events_payload = {
            "run_id": "01EVENTS",
            "request": {"intent": "stock_compare", "user_query": "compare banks"},
            "summary": {"status": "completed"},
            "display_summary": "done",
            "event_counts": {"events": 1, "distinct_event_types": 1},
            "events": [
                {
                    "event_type": "subagent_completed",
                    "phase": "collect",
                    "agent": "collector",
                    "message": "stock_snapshot_collector completed",
                    "task_name": "collect:stock_snapshot_collector",
                    "agent_path": "collector/stock_snapshot_collector",
                    "status": "completed",
                }
            ],
        }
        blocks = build_output_blocks(session)
        self.assertEqual(blocks[0].title, "events")
        self.assertTrue(any(line == "run_id: 01EVENTS" for line in blocks[0].lines))
        timeline_block = next(block for block in blocks if block.title == "event_timeline")
        self.assertTrue(any("[subagent_completed]" in line for line in timeline_block.lines))
        self.assertTrue(any("collect:stock_snapshot_collector" in line for line in timeline_block.lines))

    def test_build_output_blocks_renders_focused_event_block(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(last_run_id="01EVENTS"),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        session.output_panel = "events"
        session.events_focus_index = 0
        session.events_payload = {
            "run_id": "01EVENTS",
            "request": {"intent": "stock_compare", "user_query": "compare banks"},
            "summary": {"status": "running"},
            "event_counts": {"events": 1, "distinct_event_types": 1},
            "events": [
                {
                    "event_type": "subagent_started",
                    "phase": "collect",
                    "agent": "collector",
                    "message": "stock_snapshot_collector started",
                    "task_name": "collect:stock_snapshot_collector",
                    "agent_path": "collector/stock_snapshot_collector",
                    "node_id": "collect",
                    "invocation_id": "01INVOCATION",
                    "status": "running",
                }
            ],
        }
        blocks = build_output_blocks(session)
        focused_block = next(block for block in blocks if block.title == "focused_event")
        self.assertTrue(any(line == "event_type: subagent_started" for line in focused_block.lines))
        self.assertTrue(any(line == "task_name: collect:stock_snapshot_collector" for line in focused_block.lines))
        self.assertTrue(any(line == "call_id: 01INVOCATION" for line in focused_block.lines))
        timeline_block = next(block for block in blocks if block.title == "event_timeline")
        self.assertTrue(any(line.startswith("> ") for line in timeline_block.lines))

    def test_build_output_blocks_renders_linked_trace_for_focused_event(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(last_run_id="01TRACE"),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        session.output_panel = "events"
        session.events_focus_index = 0
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
                    "objective": "Collect stock snapshots.",
                }
            ],
            "dynamic_trace": [],
            "wait_trace": [],
        }
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
                    "task_name": "collect:compare:600519:stock_snapshot_collector",
                    "agent_path": "collector/compare/600519/stock_snapshot_collector",
                    "node_id": "collect",
                    "status": "running",
                }
            ],
        }
        blocks = build_output_blocks(session)
        linked_block = next(block for block in blocks if block.title == "linked_trace")
        self.assertTrue(any(line == "task: collect" for line in linked_block.lines))
        self.assertTrue(any(line == "phase: collect" for line in linked_block.lines))
        self.assertTrue(any(line == "agent: collector" for line in linked_block.lines))
        self.assertTrue(any(line == "objective: Collect stock snapshots." for line in linked_block.lines))
        timeline_block = next(block for block in blocks if block.title == "event_timeline")
        self.assertTrue(any(line.startswith(">@ ") for line in timeline_block.lines))

    def test_build_output_blocks_renders_linked_trace_call_context_for_focused_event(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(last_run_id="01TRACE"),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        session.output_panel = "events"
        session.events_focus_index = 0
        session.trace_focus_index = 0
        session.trace_branch_focus_path = ("collector", "compare", "600519")
        session.trace_child_focus_index = 0
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
                    "invocation_id": "01INV-600519",
                    "status": "running",
                }
            ],
            "dynamic_trace": [],
            "wait_trace": [],
        }
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
                    "task_name": "collect:compare:600519:stock_snapshot_collector",
                    "agent_path": "collector/compare/600519/stock_snapshot_collector",
                    "node_id": "collect",
                    "invocation_id": "01INV-600519",
                    "status": "running",
                }
            ],
        }
        blocks = build_output_blocks(session)
        linked_block = next(block for block in blocks if block.title == "linked_trace")
        self.assertTrue(any(line == "branch: collector/compare/600519" for line in linked_block.lines))
        self.assertTrue(any(line == "work_unit: collect:compare:600519:stock_snapshot_collector" for line in linked_block.lines))
        self.assertTrue(any(line == "call_id: 01INV-600519" for line in linked_block.lines))

    def test_build_output_blocks_renders_linked_event_for_focused_trace_task(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(last_run_id="01TRACE"),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
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
        }
        session.events_payload = {
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
                    "status": "running",
                },
            ],
        }
        blocks = build_output_blocks(session)
        task_block = next(block for block in blocks if block.title.startswith("collect | collect / collector"))
        self.assertTrue(any("task: collect  @ linked-event:subagent_started:running" == line for line in task_block.lines))
        linked_block = next(block for block in blocks if block.title == "linked_event")
        self.assertTrue(any(line == "event_type: subagent_started" for line in linked_block.lines))
        self.assertTrue(any(line == "message: stock_snapshot_collector started" for line in linked_block.lines))
        self.assertTrue(any(line == "task_name: collect:compare:600519:stock_snapshot_collector" for line in linked_block.lines))

    def test_build_output_blocks_renders_linked_event_for_focused_trace_call(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(last_run_id="01TRACE"),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        session.output_panel = "trace"
        session.trace_focus_index = 0
        session.trace_branch_focus_path = ("collector", "compare", "600519")
        session.trace_child_focus_index = 0
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
                    "invocation_id": "01INV-600519",
                    "status": "completed",
                }
            ],
            "dynamic_trace": [],
            "wait_trace": [],
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
                    "event_type": "subagent_started",
                    "phase": "collect",
                    "agent": "collector",
                    "message": "stock_snapshot_collector started",
                    "action_id": "act_collect",
                    "node_id": "collect",
                    "task_name": "collect:compare:600519:stock_snapshot_collector",
                    "invocation_id": "01INV-600519",
                    "status": "running",
                },
                {
                    "event_type": "subagent_completed",
                    "phase": "collect",
                    "agent": "collector",
                    "message": "stock_snapshot_collector completed",
                    "action_id": "act_collect",
                    "node_id": "collect",
                    "task_name": "collect:compare:600519:stock_snapshot_collector",
                    "invocation_id": "01INV-600519",
                    "status": "completed",
                },
            ],
        }
        blocks = build_output_blocks(session)
        task_block = next(block for block in blocks if block.title.startswith("collect | collect / collector"))
        self.assertTrue(any("task: collect  @ linked-event:subagent_completed:completed" == line for line in task_block.lines))
        self.assertTrue(any(line.startswith("  collector/") or line.startswith("  - collector/") or line.startswith("  > collector/") for line in task_block.lines))
        linked_block = next(block for block in blocks if block.title == "linked_event")
        self.assertTrue(any(line == "event_type: subagent_completed" for line in linked_block.lines))
        self.assertTrue(any(line == "call_id: 01INV-600519" for line in linked_block.lines))
        self.assertTrue(any(line == "status: completed" for line in linked_block.lines))
        focused_child_block = next(block for block in blocks if block.title == "focused_work_unit")
        self.assertTrue(
            any(
                line.startswith("work_unit: ") and line.endswith("@ linked-event:subagent_completed:completed")
                for line in focused_child_block.lines
            )
        )

    def test_build_output_blocks_marks_linked_branch_and_work_unit_inside_trace_tree(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(last_run_id="01TRACE"),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        session.output_panel = "trace"
        session.trace_focus_index = 0
        session.trace_branch_focus_path = ("collector", "compare", "600519")
        session.trace_child_focus_index = 0
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
                    "invocation_id": "01INV-600519",
                    "status": "completed",
                },
                {
                    "action_id": "act_collect",
                    "node_id": "collect",
                    "phase": "collect",
                    "agent": "collector",
                    "task_name": "collect:compare:300750:stock_snapshot_collector",
                    "agent_path": "collector/compare/300750/stock_snapshot_collector",
                    "subagent_type": "stock_snapshot_collector",
                    "invocation_id": "01INV-300750",
                    "status": "completed",
                },
            ],
            "dynamic_trace": [],
            "wait_trace": [],
        }
        session.events_payload = {
            "run_id": "01TRACE",
            "request": {"intent": "stock_compare", "user_query": "compare banks"},
            "summary": {"status": "running"},
            "event_counts": {"events": 2, "distinct_event_types": 2},
            "events": [
                {
                    "event_type": "subagent_started",
                    "phase": "collect",
                    "agent": "collector",
                    "message": "stock_snapshot_collector started",
                    "action_id": "act_collect",
                    "node_id": "collect",
                    "task_name": "collect:compare:600519:stock_snapshot_collector",
                    "invocation_id": "01INV-600519",
                    "status": "running",
                },
                {
                    "event_type": "subagent_completed",
                    "phase": "collect",
                    "agent": "collector",
                    "message": "stock_snapshot_collector completed",
                    "action_id": "act_collect",
                    "node_id": "collect",
                    "task_name": "collect:compare:300750:stock_snapshot_collector",
                    "invocation_id": "01INV-300750",
                    "status": "completed",
                },
            ],
        }
        blocks = build_output_blocks(session)
        task_block = next(block for block in blocks if block.title.startswith("collect | collect / collector"))
        self.assertTrue(any(line.startswith("      > 600519/") and "@ linked-event" in line for line in task_block.lines))
        self.assertTrue(
            any(
                "collect:compare:600519:stock_snapshot_collector  @ linked-event" in line
                for line in task_block.lines
            )
        )

    def test_build_output_blocks_filters_events_panel_by_focused_trace_task(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(last_run_id="01TRACE"),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        session.output_panel = "events"
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
                    "status": "running",
                }
            ],
            "dynamic_trace": [],
            "wait_trace": [],
        }
        session.events_payload = {
            "run_id": "01TRACE",
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
        blocks = build_output_blocks(session)
        timeline_block = next(block for block in blocks if block.title == "event_timeline")
        self.assertTrue(any("act_collect" in line or "collect:compare:600519:stock_snapshot_collector" in line for line in timeline_block.lines))
        self.assertFalse(any("Phase started: report" in line for line in timeline_block.lines))

    def test_build_output_blocks_renders_focused_branch_subtree_metadata(self) -> None:
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
            "request": {"intent": "distill_kol_logic", "user_query": "distill records"},
            "summary": {"status": "running"},
            "subagent_trace": [
                {
                    "action_id": "act_distill",
                    "node_id": "distill",
                    "action_objective": "Distill source content.",
                    "task_name": "distill:chunks:chunk_1_of_2:llm_chunk_distiller",
                    "agent_path": "distiller/chunks/chunk_1_of_2/llm_chunk_distiller",
                    "subagent_type": "llm_chunk_distiller",
                    "status": "completed",
                    "phase": "distill",
                    "agent": "distiller",
                    "objective": "Distill chunk_1_of_2 with LLM.",
                },
                {
                    "action_id": "act_distill",
                    "node_id": "distill",
                    "action_objective": "Distill source content.",
                    "task_name": "distill:chunks:chunk_2_of_2:llm_chunk_distiller",
                    "agent_path": "distiller/chunks/chunk_2_of_2/llm_chunk_distiller",
                    "subagent_type": "llm_chunk_distiller",
                    "status": "completed",
                    "phase": "distill",
                    "agent": "distiller",
                    "objective": "Distill chunk_2_of_2 with LLM.",
                },
            ],
            "dynamic_trace": [],
            "wait_trace": [],
        }
        session.trace_focus_index = 0
        session.trace_branch_focus_path = ("distiller", "chunks")
        blocks = build_output_blocks(session)
        focused_branch_block = next(block for block in blocks if block.title == "focused_branch")
        self.assertTrue(any(line == "branch: distiller/chunks" for line in focused_branch_block.lines))
        self.assertTrue(any(line == "parent_branch: distiller" for line in focused_branch_block.lines))
        self.assertTrue(any(line == "up_branch_focus: distiller" for line in focused_branch_block.lines))
        self.assertTrue(any(line == "next_branch_focus: distiller/chunks/chunk_1_of_2" for line in focused_branch_block.lines))
        self.assertTrue(any(line == "next_work_unit_focus: -" for line in focused_branch_block.lines))
        self.assertTrue(any(line == "next_drill_target: distiller/chunks/chunk_1_of_2" for line in focused_branch_block.lines))
        self.assertTrue(any(line == "child_branches: chunk_1_of_2, chunk_2_of_2" for line in focused_branch_block.lines))
        self.assertTrue(
            any(
                line == "next_branch_targets: distiller/chunks/chunk_1_of_2, distiller/chunks/chunk_2_of_2"
                for line in focused_branch_block.lines
            )
        )
        self.assertTrue(any(line == "direct_work_units: 0" for line in focused_branch_block.lines))

    def test_build_output_blocks_renders_threads_panel(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(thread_id="thread_active_01"),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        session.output_panel = "threads"
        session.thread_panel_scope = "active"
        with patch("research_system.cli.chat_scene.current_thread_summary") as current_summary, patch(
            "research_system.cli.chat_scene.list_chat_threads"
        ) as list_threads:
            current_summary.return_value = {
                "thread_id": "thread_active_01",
                "thread_label": "Bank compare shortlist",
                "thread_source": "fork",
                "parent_thread_id": "thread_parent_00",
                "archived": False,
                "turn_count": 5,
                "lifetime_turn_count": 12,
                "fork_turn_count": 5,
                "topic_title": "Compare bank stocks",
                "topic_intent": "stock_compare",
            }
            list_threads.return_value = [
                {
                    "thread_id": "thread_active_01",
                    "turn_count": 5,
                    "thread_label": "Bank compare shortlist",
                    "thread_source": "fork",
                    "parent_thread_id": "thread_parent_00",
                    "topic_title": "Compare bank stocks",
                    "topic_intent": "stock_compare",
                    "archived": False,
                },
                {
                    "thread_id": "thread_parent_00",
                    "turn_count": 7,
                    "thread_source": "new",
                    "parent_thread_id": None,
                    "topic_title": "Original bank thread",
                    "topic_intent": "stock_check",
                    "archived": False,
                },
            ]
            blocks = build_output_blocks(session)
        self.assertEqual(blocks[0].title, "thread")
        self.assertTrue(any("thread_name: Bank compare shortlist" in line for line in blocks[0].lines))
        self.assertTrue(any("thread_id: thread_active_01" in line for line in blocks[0].lines))
        self.assertTrue(any("parent_thread_id: thread_parent_00" in line for line in blocks[0].lines))
        self.assertEqual(blocks[1].title, "thread_lineage")
        self.assertTrue(any("parent: thread_parent_00" in line for line in blocks[1].lines))
        self.assertTrue(any("fork_turns: 5" in line for line in blocks[1].lines))
        self.assertEqual(blocks[2].title, "thread_tree:active")
        self.assertTrue(any("* thread_active_01 (fork)" in line for line in blocks[2].lines))
        self.assertEqual(blocks[3].title, "threads:active")
        self.assertTrue(any("* thread_active_01" in line for line in blocks[3].lines))
        self.assertTrue(any("source=fork:thread_parent_00" in line for line in blocks[3].lines))

    def test_build_output_blocks_renders_focused_thread_details(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(thread_id="thread_active_01"),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        session.output_panel = "threads"
        session.thread_panel_scope = "active"
        session.thread_focus_index = 1
        with patch("research_system.cli.chat_scene.current_thread_summary") as current_summary, patch(
            "research_system.cli.chat_scene.list_chat_threads"
        ) as list_threads, patch("research_system.cli.chat_scene.thread_preview_payload") as thread_preview:
            current_summary.return_value = {
                "thread_id": "thread_active_01",
                "thread_label": "Bank compare shortlist",
                "thread_source": "fork",
                "parent_thread_id": "thread_parent_00",
                "archived": False,
                "turn_count": 5,
                "lifetime_turn_count": 12,
                "fork_turn_count": 5,
                "topic_title": "Compare bank stocks",
                "topic_intent": "stock_compare",
            }
            list_threads.return_value = [
                {
                    "thread_id": "thread_active_01",
                    "turn_count": 5,
                    "thread_label": "Bank compare shortlist",
                    "thread_source": "fork",
                    "parent_thread_id": "thread_parent_00",
                    "topic_title": "Compare bank stocks",
                    "topic_intent": "stock_compare",
                    "archived": False,
                },
                {
                    "thread_id": "thread_parent_00",
                    "turn_count": 7,
                    "thread_label": "Parent bank thread",
                    "thread_source": "new",
                    "parent_thread_id": None,
                    "topic_title": "Original bank thread",
                    "topic_intent": "stock_check",
                    "archived": False,
                },
            ]
            thread_preview.return_value = {
                "thread_id": "thread_parent_00",
                "thread_label": "Parent bank thread",
                "summary_text": "recent_query: parent thread\nrecommended_next_step: inspect valuation gap",
                "summary_turn_count": 7,
                "recent_turns": [
                    {"role": "user", "content": "compare bank valuation"},
                    {"role": "assistant", "content": "ranking still inconclusive"},
                ],
                "has_checkpoint": True,
            }
            blocks = build_output_blocks(session)
        focused_block = next(block for block in blocks if block.title == "focused_thread")
        self.assertTrue(any("thread_id: thread_parent_00" in line for line in focused_block.lines))
        self.assertTrue(any("thread_name: Parent bank thread" in line for line in focused_block.lines))
        self.assertTrue(any("intent: stock_check" in line for line in focused_block.lines))
        self.assertTrue(any("actions: F9 resume | F10 fork | F11 archive/unarchive | Ctrl+F8 tree focus" in line for line in focused_block.lines))
        family_block = next(block for block in blocks if block.title == "focused_thread_family")
        self.assertTrue(any("parent: -" in line for line in family_block.lines))
        self.assertTrue(any("current: thread_parent_00  name=Parent bank thread  intent=stock_check" in line for line in family_block.lines))
        self.assertTrue(any("ancestry: -" in line for line in family_block.lines))
        self.assertTrue(any("children:" == line for line in family_block.lines))
        self.assertTrue(any("  - thread_active_01  name=Bank compare shortlist  intent=stock_compare" in line for line in family_block.lines))
        self.assertTrue(any("descendants:" == line for line in family_block.lines))
        self.assertTrue(any("  - thread_active_01  name=Bank compare shortlist  intent=stock_compare" in line for line in family_block.lines))
        preview_block = next(block for block in blocks if block.title == "focused_thread_preview")
        self.assertTrue(any("thread_name: Parent bank thread" in line for line in preview_block.lines))
        self.assertTrue(any("summary_turns: 7" in line for line in preview_block.lines))
        self.assertTrue(any("recent_query: parent thread" in line for line in preview_block.lines))
        self.assertTrue(any("user: compare bank valuation" in line for line in preview_block.lines))
        self.assertTrue(any("checkpoint: available" in line for line in preview_block.lines))
        tree_block = next(block for block in blocks if block.title == "thread_tree:active")
        self.assertTrue(any("thread_parent_00 (new)" in line for line in tree_block.lines))
        threads_block = next(block for block in blocks if block.title == "threads:active")
        self.assertTrue(any(">* thread_active_01" in line or ">- thread_parent_00" in line for line in threads_block.lines))
        self.assertTrue(any("name=Parent bank thread" in line for line in threads_block.lines))

    def test_build_output_blocks_renders_focused_thread_family(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(thread_id="thread_child_02"),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        session.output_panel = "threads"
        session.thread_panel_scope = "all"
        session.thread_focus_index = 1
        with patch("research_system.cli.chat_scene.current_thread_summary") as current_summary, patch(
            "research_system.cli.chat_scene.list_chat_threads"
        ) as list_threads, patch("research_system.cli.chat_scene.thread_preview_payload") as thread_preview:
            current_summary.return_value = {
                "thread_id": "thread_active_01",
                "thread_label": "Bank compare shortlist",
                "thread_source": "fork",
                "parent_thread_id": "thread_parent_00",
                "archived": False,
                "turn_count": 5,
                "lifetime_turn_count": 12,
                "fork_turn_count": 5,
                "topic_title": "Compare bank stocks",
                "topic_intent": "stock_compare",
            }
            list_threads.return_value = [
                {
                    "thread_id": "thread_parent_00",
                    "turn_count": 7,
                    "thread_label": "Parent bank thread",
                    "thread_source": "new",
                    "parent_thread_id": None,
                    "topic_title": "Parent thread",
                    "topic_intent": "stock_check",
                    "archived": False,
                },
                {
                    "thread_id": "thread_child_02",
                    "turn_count": 3,
                    "thread_label": "Child compare branch",
                    "thread_source": "fork",
                    "parent_thread_id": "thread_parent_00",
                    "topic_title": "Child thread",
                    "topic_intent": "stock_compare",
                    "archived": False,
                },
                {
                    "thread_id": "thread_grandchild_03",
                    "turn_count": 2,
                    "thread_label": "Grandchild branch",
                    "thread_source": "fork",
                    "parent_thread_id": "thread_child_02",
                    "topic_title": "Grandchild thread",
                    "topic_intent": "review_thesis",
                    "archived": False,
                },
            ]
            thread_preview.return_value = {}
            blocks = build_output_blocks(session)
        family_block = next(block for block in blocks if block.title == "focused_thread_family")
        self.assertTrue(any("parent: thread_parent_00  name=Parent bank thread  intent=stock_check" in line for line in family_block.lines))
        self.assertTrue(any("current: thread_child_02  name=Child compare branch  intent=stock_compare" in line for line in family_block.lines))
        self.assertTrue(any("ancestry:" == line for line in family_block.lines))
        self.assertTrue(any("  - thread_parent_00  name=Parent bank thread  intent=stock_check" in line for line in family_block.lines))
        self.assertTrue(any("children:" == line for line in family_block.lines))
        self.assertTrue(any("  - thread_grandchild_03  name=Grandchild branch  intent=review_thesis" in line for line in family_block.lines))
        self.assertTrue(any("descendants:" == line for line in family_block.lines))
        self.assertTrue(any("  - thread_grandchild_03  name=Grandchild branch  intent=review_thesis" in line for line in family_block.lines))

    def test_build_output_blocks_renders_recursive_thread_descendants(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(thread_id="thread_root_00"),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        session.output_panel = "threads"
        session.thread_panel_scope = "all"
        session.thread_focus_index = 0
        with patch("research_system.cli.chat_scene.current_thread_summary") as current_summary, patch(
            "research_system.cli.chat_scene.list_chat_threads"
        ) as list_threads, patch("research_system.cli.chat_scene.thread_preview_payload") as thread_preview:
            current_summary.return_value = {
                "thread_id": "thread_root_00",
                "thread_label": "Root thread",
                "thread_source": "new",
                "parent_thread_id": None,
                "archived": False,
                "turn_count": 9,
                "lifetime_turn_count": 9,
                "fork_turn_count": 0,
                "topic_title": "Root topic",
                "topic_intent": "stock_check",
            }
            list_threads.return_value = [
                {
                    "thread_id": "thread_root_00",
                    "turn_count": 9,
                    "thread_label": "Root thread",
                    "thread_source": "new",
                    "parent_thread_id": None,
                    "topic_title": "Root topic",
                    "topic_intent": "stock_check",
                    "archived": False,
                },
                {
                    "thread_id": "thread_child_01",
                    "turn_count": 5,
                    "thread_label": "Child branch",
                    "thread_source": "fork",
                    "parent_thread_id": "thread_root_00",
                    "topic_title": "Child topic",
                    "topic_intent": "stock_compare",
                    "archived": False,
                },
                {
                    "thread_id": "thread_grandchild_02",
                    "turn_count": 2,
                    "thread_label": "Grandchild branch",
                    "thread_source": "fork",
                    "parent_thread_id": "thread_child_01",
                    "topic_title": "Grandchild topic",
                    "topic_intent": "review_thesis",
                    "archived": False,
                },
            ]
            thread_preview.return_value = {}
            blocks = build_output_blocks(session)
        family_block = next(block for block in blocks if block.title == "focused_thread_family")
        self.assertTrue(any("descendants:" == line for line in family_block.lines))
        self.assertTrue(any("  - thread_child_01  name=Child branch  intent=stock_compare" == line for line in family_block.lines))
        self.assertTrue(any("    - thread_grandchild_02  name=Grandchild branch  intent=review_thesis" == line for line in family_block.lines))

    def test_build_output_blocks_renders_thread_lineage_tree_with_nested_child(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(thread_id="thread_child_02"),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        session.output_panel = "threads"
        session.thread_panel_scope = "all"
        with patch("research_system.cli.chat_scene.current_thread_summary") as current_summary, patch(
            "research_system.cli.chat_scene.list_chat_threads"
        ) as list_threads:
            current_summary.return_value = {
                "thread_id": "thread_child_02",
                "thread_label": "Child compare branch",
                "thread_source": "fork",
                "parent_thread_id": "thread_parent_00",
                "archived": False,
                "turn_count": 3,
                "lifetime_turn_count": 3,
                "fork_turn_count": 3,
                "topic_title": "Child thread",
                "topic_intent": "stock_compare",
            }
            list_threads.return_value = [
                {
                    "thread_id": "thread_child_02",
                    "turn_count": 3,
                    "thread_label": "Child compare branch",
                    "thread_source": "fork",
                    "parent_thread_id": "thread_parent_00",
                    "topic_title": "Child thread",
                    "topic_intent": "stock_compare",
                    "archived": False,
                },
                {
                    "thread_id": "thread_parent_00",
                    "turn_count": 7,
                    "thread_label": "Parent bank thread",
                    "thread_source": "new",
                    "parent_thread_id": None,
                    "topic_title": "Parent thread",
                    "topic_intent": "stock_check",
                    "archived": False,
                },
            ]
            blocks = build_output_blocks(session)
        tree_block = next(block for block in blocks if block.title == "thread_tree:all")
        self.assertTrue(any(line.startswith(" - thread_parent_00 (new)") and "name=Parent bank thread" in line for line in tree_block.lines))
        self.assertTrue(any(line.startswith("   * thread_child_02 (fork)") and "name=Child compare branch" in line for line in tree_block.lines))

    def test_build_output_blocks_renders_thread_tree_focus_marker(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(thread_id="thread_child_02"),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        session.output_panel = "threads"
        session.thread_panel_scope = "all"
        session.thread_tree_focus_index = 1
        with patch("research_system.cli.chat_scene.current_thread_summary") as current_summary, patch(
            "research_system.cli.chat_scene.list_chat_threads"
        ) as list_threads:
            current_summary.return_value = {
                "thread_id": "thread_child_02",
                "thread_source": "fork",
                "parent_thread_id": "thread_parent_00",
                "archived": False,
                "turn_count": 3,
                "lifetime_turn_count": 3,
                "fork_turn_count": 3,
                "topic_title": "Child thread",
                "topic_intent": "stock_compare",
            }
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
            blocks = build_output_blocks(session)
        tree_block = next(block for block in blocks if block.title == "thread_tree:all")
        self.assertTrue(any(line.startswith("  >* thread_child_02 (fork)") for line in tree_block.lines))

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
        self.assertIn("retrieve:retrieval_decider", session.output_area.text)

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

    def test_apply_tui_update_live_events_patch_auto_focuses_latest_contextual_event(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        session.output_panel = "events"
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
        }
        apply_tui_update(
            session,
            append_update(
                "meta",
                "",
                state_patch=ShellStatePatch(
                    current_run_id="01TRACE",
                    events_payload={
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
                            },
                        ],
                    },
                ),
            ),
        )
        self.assertEqual(session.events_focus_index, 2)

    def test_apply_tui_update_live_events_patch_does_not_force_focus_outside_events_panel(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        session.output_panel = "trace"
        session.events_focus_index = 1
        apply_tui_update(
            session,
            append_update(
                "meta",
                "",
                state_patch=ShellStatePatch(
                    current_run_id="01TRACE",
                    events_payload={
                        "run_id": "01TRACE",
                        "request": {"intent": "stock_compare", "user_query": "compare banks"},
                        "summary": {"status": "running"},
                        "event_counts": {"events": 1, "distinct_event_types": 1},
                        "events": [
                            {
                                "event_type": "phase_started",
                                "phase": "collect",
                                "agent": "collector",
                                "message": "Phase started: collect",
                                "action_id": "act_collect",
                                "node_id": "collect",
                            }
                        ],
                    },
                ),
            ),
        )
        self.assertEqual(session.events_focus_index, 1)

    def test_apply_tui_update_live_events_patch_preserves_latest_contextual_focus_outside_events_panel_when_trace_context_exists(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
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
        }
        apply_tui_update(
            session,
            append_update(
                "meta",
                "",
                state_patch=ShellStatePatch(
                    current_run_id="01TRACE",
                    events_payload={
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
                            },
                        ],
                    },
                ),
            ),
        )
        self.assertEqual(session.events_focus_index, 2)

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

    def test_print_events_payload_renders_event_timeline(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            print_payload(
                {
                    "run_id": "01EVENTS",
                    "request": {"intent": "stock_compare", "user_query": "compare banks"},
                    "summary": {"status": "completed"},
                    "display_summary": "done",
                    "event_counts": {"events": 1, "distinct_event_types": 1},
                    "events": [
                        {
                            "event_type": "subagent_completed",
                            "phase": "collect",
                            "agent": "collector",
                            "message": "stock_snapshot_collector completed",
                            "task_name": "collect:stock_snapshot_collector",
                            "agent_path": "collector/stock_snapshot_collector",
                            "status": "completed",
                        }
                    ],
                },
                as_json=False,
                emit_text=lambda output_writer, update: print(update.text if getattr(update, "text", "") else "", end="\n"),
            )

        output = buffer.getvalue()
        self.assertIn("run_id: 01EVENTS", output)
        self.assertIn("events:", output)
        self.assertIn("[subagent_completed]", output)
        self.assertIn("collect:stock_snapshot_collector", output)

    def test_print_events_payload_updates_tui_events_payload_cache(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        payload = {
            "run_id": "01EVENTS",
            "request": {"intent": "stock_compare", "user_query": "compare banks"},
            "summary": {"status": "completed"},
            "event_counts": {"events": 0, "distinct_event_types": 0},
            "events": [],
        }
        print_payload(
            payload,
            as_json=False,
            emit_text=lambda output_writer, update: apply_tui_update(session, update),
            output_writer=lambda update: apply_tui_update(session, update),
        )
        self.assertEqual(session.events_payload, payload)

    def test_print_subagent_payload_updates_tui_subagent_payload_cache(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )
        payload = {
            "run_id": "01SUBAGENT",
            "invocation_id": "01INVOCATION",
            "trace_rows": [],
            "events": [],
            "event_counts": {"trace_rows": 0, "events": 0},
        }
        print_payload(
            payload,
            as_json=False,
            emit_text=lambda output_writer, update: apply_tui_update(session, update),
            output_writer=lambda update: apply_tui_update(session, update),
        )
        self.assertEqual(session.subagent_payload, payload)

    def test_print_subagent_payload_renders_call_scoped_sections(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            print_payload(
                {
                    "run_id": "01SUBAGENT",
                    "request": {"intent": "stock_compare", "user_query": "compare banks"},
                    "summary": {"status": "completed"},
                    "display_summary": "done",
                    "invocation_id": "01INVOCATION",
                    "task_name": "collect:stock_snapshot_collector",
                    "agent_path": "collector/stock_snapshot_collector",
                    "subagent_type": "stock_snapshot_collector",
                    "status": "completed",
                    "objective": "Collect stock-level verified snapshots.",
                    "notes": ["symbol=600519"],
                    "event_counts": {"trace_rows": 1, "events": 1},
                    "trace_rows": [
                        {
                            "task_name": "collect:stock_snapshot_collector",
                            "agent_path": "collector/stock_snapshot_collector",
                            "invocation_id": "01INVOCATION",
                            "status": "completed",
                            "phase": "collect",
                            "agent": "collector",
                        }
                    ],
                    "events": [
                        {
                            "event_type": "subagent_completed",
                            "task_name": "collect:stock_snapshot_collector",
                            "agent_path": "collector/stock_snapshot_collector",
                            "invocation_id": "01INVOCATION",
                            "status": "completed",
                            "phase": "collect",
                            "agent": "collector",
                        }
                    ],
                },
                as_json=False,
                emit_text=lambda output_writer, update: print(update.text if getattr(update, "text", "") else "", end="\n"),
            )

        output = buffer.getvalue()
        self.assertIn("call_id: 01INVOCATION", output)
        self.assertIn("subagent_type: stock_snapshot_collector", output)
        self.assertIn("trace_rows:", output)
        self.assertIn("events:", output)
        self.assertIn("[subagent_completed]", output)

    def test_print_steps_payload_renders_run_step_sections(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            print_payload(
                {
                    "run_id": "01STEPS",
                    "request": {"intent": "stock_compare", "user_query": "compare banks"},
                    "summary": {"status": "running"},
                    "display_summary": "run in progress",
                    "intent": "stock_compare",
                    "strategy": "dynamic",
                    "entry_strategy": "llm",
                    "dispatch_trace": {"source": "route_table", "intent": "stock_compare"},
                    "notes": ["compare flow activated"],
                    "step_counts": {"total": 2, "active": 1, "completed": 1, "pending": 0},
                    "status_counts": {"active": 1, "completed": 1},
                    "steps": [
                        {
                            "step_index": 1,
                            "node_id": "collect",
                            "phase": "collect",
                            "agent": "collector",
                            "objective": "Collect comparison snapshots.",
                            "status": "completed",
                            "tags": ["compare"],
                            "inputs": {"symbols": ["600036", "601166"]},
                            "active_from_supervisor": False,
                            "supervisor_note": None,
                        },
                        {
                            "step_index": 2,
                            "node_id": "retrieve",
                            "phase": "retrieve",
                            "agent": "retriever",
                            "objective": "Retrieve final evidence.",
                            "status": "active",
                            "tags": [],
                            "inputs": {},
                            "active_from_supervisor": True,
                            "supervisor_note": "advance to retrieval",
                        },
                    ],
                },
                as_json=False,
                emit_text=lambda output_writer, update: print(update.text if getattr(update, "text", "") else "", end="\n"),
            )

        output = buffer.getvalue()
        self.assertIn("run_id: 01STEPS", output)
        self.assertIn("strategy: dynamic", output)
        self.assertIn("dispatch_trace:", output)
        self.assertIn("status_counts: active=1, completed=1", output)
        self.assertIn("steps:", output)
        self.assertIn("1. collect [completed]  phase=collect agent=collector", output)
        self.assertIn("2. retrieve [active]  phase=retrieve agent=retriever  next=active", output)
        self.assertIn("supervisor_note: advance to retrieval", output)

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
