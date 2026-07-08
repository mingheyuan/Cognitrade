from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from research_system.cli.app import _execute_cli_command
from research_system.cli.commands.chat_command_catalog import print_chat_help
from research_system.cli.rendering.chat_rendering import apply_tui_update, render_tui_output
from research_system.cli.chat_scene import (
    build_commands_popup_view,
    build_hint_bar_view,
    build_output_blocks,
    build_shell_scene_view,
    build_status_bar_items,
    build_threads_blocks,
    build_transcript_blocks_for_session,
    build_tui_blocks,
)
from research_system.cli.transcript.chat_transcript import TranscriptMessage, make_transcript_message
from research_system.cli.state.chat_types import ChatSessionState, TuiSession
from research_system.cli.payloads.payload_rendering import print_payload as package_print_payload
from research_system.cli.payloads.payload_rendering import print_payload
from research_system.cli.transcript.chat_transcript import TranscriptMessage as package_transcript_message
from research_system.runtime.platform.settings import Settings


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

    def list_subagent_discovery_requests(self, *, status=None, limit=50) -> list[dict]:
        return [
            {
                "ticket_id": "01DISC",
                "status": status or "approved",
                "intent": "stock_screen",
                "stage": "collect",
                "capability": "screening_strategy",
                "search_query": "screening strategy resolver",
                "candidate_entry_count": 1,
                "activation_entry_count": 0,
                "updated_at": "2026-07-08T00:00:00+00:00",
            }
        ]

    def show_subagent_discovery_request(self, ticket_id: str) -> dict:
        return {
            "ticket_id": ticket_id,
            "status": "approved",
            "stored_status": "approved",
            "capability_gap": {
                "intent": "stock_screen",
                "stage": "collect",
                "capability": "screening_strategy",
                "preferred_subagent_type": "candidate_screening_strategy",
                "requires_llm": False,
                "reason": "missing capability",
            },
            "tool_discovery_request": {
                "search_query": "screening strategy resolver",
                "preferred_protocols": ["mcp"],
                "reason": "discover resolver",
            },
            "candidate_catalog_entries": [
                {
                    "subagent_type": "candidate_screening_strategy",
                    "stage": "collect",
                    "capabilities": ["screening_strategy"],
                    "tags": ["approved_discovery_candidate"],
                    "summary": "Approved discovery candidate",
                }
            ],
            "activated_entries": [
                {
                    "subagent_type": "candidate_screening_strategy",
                    "delegate_subagent_type": "stock_snapshot_collector",
                    "status": "active",
                    "activated_by": "ops_user",
                }
            ],
            "notes": ["approved candidate available"],
        }

    def review_subagent_discovery_request(
        self,
        ticket_id: str,
        *,
        decision: str,
        reviewer: str,
        reason: str | None = None,
    ) -> dict:
        return {
            "ticket_id": ticket_id,
            "status": "approved" if decision == "approve" else "rejected",
            "message": "discovery ticket approved" if decision == "approve" else "discovery ticket rejected",
            "result": self.show_subagent_discovery_request(ticket_id),
            "reviewer": reviewer,
            "review_reason": reason or "",
        }

    def activate_subagent_discovery_request(
        self,
        ticket_id: str,
        *,
        delegate_subagent_type: str | None = None,
        candidate_subagent_type: str | None = None,
        activated_by: str = "manual_cli_activation",
        reason: str | None = None,
        registration_type: str | None = None,
        skill_name: str | None = None,
        snapshot_type: str | None = None,
        source_id: str | None = None,
        endpoint_path: str | None = None,
        http_method: str | None = None,
        timeout_seconds: float | None = None,
    ) -> dict:
        return {
            "ticket_id": ticket_id,
            "status": "approved",
            "message": f"activated discovery alias {candidate_subagent_type or 'candidate_screening_strategy'}",
            "result": {
                **self.show_subagent_discovery_request(ticket_id),
                "activated_entries": [
                    {
                        "subagent_type": candidate_subagent_type or "candidate_screening_strategy",
                        "delegate_subagent_type": delegate_subagent_type,
                        "runtime_registration": (
                            {
                                "registration_type": registration_type,
                                "skill_name": skill_name,
                                "snapshot_type": snapshot_type,
                                "source_id": source_id,
                                "endpoint_path": endpoint_path,
                                "http_method": http_method,
                                "timeout_seconds": timeout_seconds,
                            }
                            if registration_type
                            else None
                        ),
                        "status": "active",
                        "activated_by": activated_by,
                        "activation_reason": reason or "",
                    }
                ],
            },
        }

    def review_proposal(
        self,
        proposal_id: str,
        *,
        decision: str,
        reviewer: str,
        reason: str | None = None,
    ) -> dict:
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

    def metrics(self, run_id: str) -> dict:
        return {
            "run_id": run_id,
            "request": {"intent": "stock_check", "user_query": "inspect runtime"},
            "summary": {"status": "completed"},
            "display_summary": "metrics ready",
            "counts": {"events": 2, "items": 3, "threads": 2, "subagents": 2, "llm_calls": 1},
            "rates": {"fallback_rate": 0.5, "memory_hit_rate": 1.0, "qdrant_hit_rate": 0.5},
            "latency_ms": {"subagents": {"count": 2}, "actions": {"count": 1}, "per_subagent": {}, "per_action": {}},
            "fanout": {"max_depth": 1, "per_parent_child_count": {"thread-root": 1}},
            "turn_usage": {"turn-root": {"thread_id": "thread-root", "llm_calls": 1}},
            "event_type_counts": {"subagent_completed": 2},
            "degrade_reason_counts": {"llm_budget_exhausted": 1},
        }

    def thread_replay(self, run_id: str, thread_id: str) -> dict:
        return {
            "run_id": run_id,
            "thread_id": thread_id,
            "scope": "thread_subtree",
            "scope_thread_ids": [thread_id],
            "request": {"intent": "stock_check", "user_query": "inspect child"},
            "summary": {"status": "completed"},
            "display_summary": "thread ready",
            "thread": {"thread_id": thread_id, "status": "completed"},
            "threads": [{"thread_id": thread_id, "status": "completed", "children": []}],
            "thread_tree": [{"thread_id": thread_id, "status": "completed", "children": []}],
            "subagent_trace": [],
            "events": [],
            "blackboard": [],
            "wait_trace": [],
            "items": [],
            "metrics": {
                "run_id": run_id,
                "thread_id": thread_id,
                "counts": {"threads": 1, "subagents": 0},
                "rates": {"fallback_rate": 0.0, "memory_hit_rate": None, "qdrant_hit_rate": None},
                "latency_ms": {"subagents": {}, "actions": {}, "per_subagent": {}, "per_action": {}},
                "fanout": {"max_depth": 0, "per_parent_child_count": {}},
                "turn_usage": {},
                "event_type_counts": {},
                "degrade_reason_counts": {},
            },
            "event_counts": {"events": 0, "subagent_trace": 0, "dynamic_trace": 0, "wait_trace": 0, "items": 0, "blackboard": 0, "threads": 1},
        }

    def rerun_from(self, run_id: str, *, from_node: str) -> dict:
        return {"run_id": "01RERUN", "status": "completed", "message": f"reran from {from_node}", "source_run_id": run_id}

    def rerun_subagent_from(
        self,
        run_id: str,
        *,
        action_node: str,
        dag_id: str,
        from_node: str,
    ) -> dict:
        return {
            "run_id": "01SUBRERUN",
            "status": "completed",
            "message": f"reran {action_node}/{dag_id}/{from_node}",
            "source_run_id": run_id,
        }


THREAD_SUMMARY = {
    "thread_id": "thread-root",
    "thread_source": "fork",
    "parent_thread_id": "thread-parent",
    "thread_label": "Bank compare shortlist",
    "topic_title": "Bank leaders",
    "topic_intent": "stock_compare",
    "turn_count": 4,
    "lifetime_turn_count": 7,
    "fork_turn_count": 3,
    "archived": False,
    "thread_status": "active",
    "recommended_next_step": "Compare PB and ROE side by side.",
}

THREAD_ROWS = [
    {
        "thread_id": "thread-parent",
        "thread_source": "new",
        "turn_count": 5,
        "thread_label": "Parent",
        "topic_title": "Parent topic",
        "topic_intent": "sector_research",
        "archived": False,
    },
    {
        "thread_id": "thread-root",
        "thread_source": "fork",
        "parent_thread_id": "thread-parent",
        "turn_count": 4,
        "thread_label": "Bank compare shortlist",
        "topic_title": "Bank leaders",
        "topic_intent": "stock_compare",
        "archived": False,
    },
    {
        "thread_id": "thread-child",
        "thread_source": "fork",
        "parent_thread_id": "thread-root",
        "turn_count": 2,
        "thread_label": "Child",
        "topic_title": "Child topic",
        "topic_intent": "stock_check",
        "archived": True,
    },
]

THREAD_PREVIEW = {"preview_lines": ["latest answer"]}


class CliOutputTests(unittest.TestCase):
    def _session(
        self,
        *,
        state: ChatSessionState | None = None,
        output_lines: list[TranscriptMessage] | None = None,
    ) -> TuiSession:
        return TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=state or ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=output_lines or [],
        )

    def _capture_payload_text(self, payload: object) -> str:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            print_payload(
                payload,
                as_json=False,
                emit_text=lambda output_writer, update: print(
                    update.text if getattr(update, "text", "") else "",
                    end="\n",
                ),
            )
        return buffer.getvalue()

    def _emit_payload_to_session(self, session: TuiSession, payload: object) -> None:
        print_payload(
            payload,
            as_json=False,
            emit_text=lambda output_writer, update: apply_tui_update(session, update),
            output_writer=lambda update: apply_tui_update(session, update),
        )

    def test_top_level_payload_rendering_remains_compatible_with_payloads_package(self) -> None:
        self.assertIs(print_payload, package_print_payload)

    def test_top_level_transcript_remains_compatible_with_transcript_package(self) -> None:
        self.assertIs(TranscriptMessage, package_transcript_message)

    def test_chat_help_includes_stop_and_resume_commands(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            print_chat_help()

        output = buffer.getvalue()
        self.assertIn("/stop", output)
        self.assertIn("/resume <run_id|last>", output)
        self.assertIn("/metrics <run_id|last>", output)
        self.assertIn("/rerun-from <run_id|last> <node_id>", output)
        self.assertIn("/rerun-subagent <run_id|last> <action_node_id> <dag_id> <subagent_node_id>", output)
        self.assertIn("/subagent-discoveries [limit] [status]", output)
        self.assertIn("/activate-subagent-discovery <id> <delegate> [candidate]", output)
        self.assertIn("show checkpoint resumability", output)

    def test_execute_cli_command_closes_orchestrator_for_history(self) -> None:
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
        self.assertEqual(reviewed["result"]["retrieval_policy"]["approved_proposal_ids"], ["01PROP"])

    def test_execute_cli_command_supports_subagent_discovery_commands(self) -> None:
        _AppOrchestratorStub.instances.clear()
        with patch("research_system.cli.app.ResearchOrchestrator", _AppOrchestratorStub):
            rows = _execute_cli_command(
                Settings.from_env(Path(".")),
                command="subagent-discoveries",
                limit=10,
                status_filter="approved",
            )
            detail = _execute_cli_command(
                Settings.from_env(Path(".")),
                command="subagent-discovery",
                ticket_id="01DISC",
            )
            reviewed = _execute_cli_command(
                Settings.from_env(Path(".")),
                command="review-subagent-discovery",
                ticket_id="01DISC",
                query="approve",
                intent="ops_reviewer",
                status_filter="validated",
            )
            activated = _execute_cli_command(
                Settings.from_env(Path(".")),
                command="activate-subagent-discovery",
                ticket_id="01DISC",
                query="stock_snapshot_collector",
                activated_by="ops_user",
                status_filter="delegate approved",
                candidate_subagent_type="candidate_screening_strategy",
            )
            runtime_activated = _execute_cli_command(
                Settings.from_env(Path(".")),
                command="activate-subagent-discovery",
                ticket_id="01DISC",
                activated_by="ops_user",
                status_filter="activate runtime skill",
                candidate_subagent_type="candidate_screening_strategy",
                registration_type="snapshot_skill_runner",
                skill_name="market_snapshot_skill",
                snapshot_type="market",
            )
            http_runtime_activated = _execute_cli_command(
                Settings.from_env(Path(".")),
                command="activate-subagent-discovery",
                ticket_id="01DISC",
                activated_by="ops_user",
                status_filter="activate http runtime skill",
                candidate_subagent_type="candidate_collect_http_extension",
                registration_type="http_json_runner",
                source_id="eastmoney",
                endpoint_path="/api/market-signal",
                http_method="GET",
                timeout_seconds=9.5,
            )

        self.assertEqual(rows[0]["ticket_id"], "01DISC")
        self.assertEqual(detail["ticket_id"], "01DISC")
        self.assertEqual(reviewed["status"], "approved")
        self.assertEqual(
            activated["result"]["activated_entries"][0]["delegate_subagent_type"],
            "stock_snapshot_collector",
        )
        self.assertEqual(
            runtime_activated["result"]["activated_entries"][0]["runtime_registration"]["skill_name"],
            "market_snapshot_skill",
        )
        self.assertEqual(
            http_runtime_activated["result"]["activated_entries"][0]["runtime_registration"]["source_id"],
            "eastmoney",
        )

    def test_render_tui_output_uses_ascii_sections(self) -> None:
        output = render_tui_output(
            [
                make_transcript_message(kind="thinking", text="plan first"),
                make_transcript_message(kind="status", text="collect > report"),
                make_transcript_message(kind="answer_stream", text="done", stream_key="answer_stream"),
            ]
        )
        self.assertIn("[thinking]", output)
        self.assertIn("[status]", output)
        self.assertIn("[answer]", output)

    def test_build_tui_blocks_exposes_titles_and_inline_rows(self) -> None:
        blocks = build_tui_blocks(
            [
                make_transcript_message(kind="thinking", text="plan first"),
                make_transcript_message(kind="meta", text="run_id: 01TEST"),
                make_transcript_message(kind="answer_stream", text="done", stream_key="answer_stream"),
            ]
        )

        self.assertEqual(blocks[0].title, "thinking")
        self.assertEqual(blocks[0].lines, ("plan first",))
        self.assertIsNone(blocks[1].title)
        self.assertTrue(blocks[1].inline)
        self.assertEqual(blocks[2].title, "answer")

    def test_build_transcript_blocks_for_session_appends_live_tail_for_active_stream(self) -> None:
        session = self._session(
            output_lines=[
                make_transcript_message(kind="thinking", text="plan first"),
                make_transcript_message(kind="meta", text="run_id: 01TEST"),
                make_transcript_message(
                    kind="answer_stream",
                    text="streaming answer",
                    slot_key="answer_stream",
                    stream_key="answer_stream",
                ),
            ]
        )
        live_message = session.output_lines[-1]
        session.active_message_ids["answer_stream"] = live_message.message_id

        blocks = build_transcript_blocks_for_session(session)

        self.assertEqual(blocks[0].title, "thinking")
        self.assertIsNone(blocks[1].title)
        self.assertEqual(blocks[-1].title, "live_tail")
        self.assertEqual(blocks[-1].lines, ("[answer] streaming answer",))

    def test_build_status_bar_items_include_mode_run_phase_and_output(self) -> None:
        session = self._session(state=ChatSessionState(current_intent="stock_screen", events_mode="full"))
        session.current_run_id = "01RUN"
        session.current_phase = "collect"

        items = build_status_bar_items(session)

        self.assertEqual(items[0].label, "mode")
        self.assertEqual(items[0].value, "codex")
        self.assertEqual(items[2].value, "stock_screen")
        self.assertEqual(items[3].value, "01RUN")
        self.assertEqual(items[4].value, "collect")
        self.assertEqual(items[6].label, "output")
        self.assertEqual(items[6].value, "transcript:live")
        self.assertEqual(items[7].label, "inspect")

    def test_build_commands_popup_view_lists_runtime_navigation_commands(self) -> None:
        popup = build_commands_popup_view()

        self.assertEqual(popup.lines[0], "Commands")
        self.assertIn("/stop                     stop the current run", popup.lines)
        self.assertIn("/proposal <proposal_id>   show proposal detail", popup.lines)
        self.assertIn("/subagent-discoveries [limit] [status] list subagent discovery tickets", popup.lines)
        self.assertIn("F6      cycle trace/events/threads panel", popup.lines)
        self.assertIn("F12     toggle child detail expansion", popup.lines)
        self.assertIn("Ctrl+F8  focus next thread tree node", popup.lines)

    @patch("research_system.cli.chat_scene.current_thread_summary", return_value=THREAD_SUMMARY)
    def test_build_hint_bar_view_uses_idle_thread_context(self, _current_thread_summary) -> None:
        session = self._session(state=ChatSessionState(current_intent="stock_compare"))
        session.current_phase = "collect"
        session.run_status_label = "completed"

        hint = build_hint_bar_view(session)

        self.assertIn("ready | output transcript:live", hint.text)
        self.assertIn("context stock_compare / collect / completed", hint.text)
        self.assertIn("thread Bank compare shortlist", hint.text)
        self.assertIn("topic Bank leaders", hint.text)
        self.assertIn("next Compare PB and ROE side by side.", hint.text)

    def test_build_hint_bar_view_describes_trace_focus(self) -> None:
        session = self._session(state=ChatSessionState(last_run_id="01TRACE"))
        session.output_panel = "trace"
        session.trace_focus_index = 1
        session.trace_child_focus_index = 0

        hint = build_hint_bar_view(session)

        self.assertIn("trace panel", hint.text)
        self.assertIn("run 01TRACE", hint.text)
        self.assertIn("focus task 2", hint.text)
        self.assertIn("inspect:focused-call", hint.text)
        self.assertIn("child:1", hint.text)

    @patch("research_system.cli.chat_scene.current_thread_summary", return_value=THREAD_SUMMARY)
    @patch("research_system.cli.chat_scene.list_chat_threads", return_value=THREAD_ROWS)
    @patch("research_system.cli.chat_scene.thread_preview_payload", return_value=THREAD_PREVIEW)
    def test_build_threads_blocks_render_lineage_tree_and_focused_family(
        self,
        _thread_preview_payload,
        _list_chat_threads,
        _current_thread_summary,
    ) -> None:
        session = self._session(state=ChatSessionState(thread_id="thread-root"))
        session.thread_focus_index = 1
        session.thread_tree_focus_index = 2

        blocks = build_threads_blocks(session)
        by_title = {block.title: block for block in blocks}

        self.assertIn("thread", by_title)
        self.assertIn("thread_lineage", by_title)
        self.assertIn("focused_thread_family", by_title)
        self.assertIn("thread_tree:active", by_title)
        self.assertIn("threads:active", by_title)
        self.assertIn("parent: thread-parent", by_title["thread_lineage"].lines[0])
        self.assertIn("current: thread-root", "\n".join(by_title["focused_thread_family"].lines))
        self.assertIn("> -".replace(" ", ""), by_title["thread_tree:active"].lines[-1].replace(" ", ""))
        self.assertIn("source=fork:thread-parent", "\n".join(by_title["threads:active"].lines))

    @patch("research_system.cli.chat_scene.current_thread_summary", return_value=THREAD_SUMMARY)
    @patch("research_system.cli.chat_scene.list_chat_threads", return_value=THREAD_ROWS)
    @patch("research_system.cli.chat_scene.thread_preview_payload", return_value=THREAD_PREVIEW)
    def test_build_output_blocks_uses_threads_panel(
        self,
        _thread_preview_payload,
        _list_chat_threads,
        _current_thread_summary,
    ) -> None:
        session = self._session(state=ChatSessionState(thread_id="thread-root"))
        session.output_panel = "threads"

        blocks = build_output_blocks(session)

        self.assertEqual(blocks[0].title, "thread")
        self.assertIn("thread_tree:active", [block.title for block in blocks])

    @patch("research_system.cli.chat_scene.current_thread_summary", return_value=THREAD_SUMMARY)
    def test_build_shell_scene_view_exposes_help_layout(self, _current_thread_summary) -> None:
        session = self._session(
            state=ChatSessionState(
                current_intent="stock_screen",
                pending_clarification_question="question",
            ),
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

        self.assertEqual(scene.layout_mode, "help")
        self.assertEqual(scene.header.brand, "COGNITRADE")
        self.assertEqual(scene.header.mode_tag, "HELP")
        self.assertEqual(scene.transcript_blocks[0].title, "thinking")
        self.assertEqual(scene.transcript_blocks[-1].title, "answer")
        self.assertIn("output", {item.label for item in scene.status_items})
        self.assertIn("popup |", scene.hint.text)
        self.assertEqual(scene.commands_popup.lines[0], "Commands")
        self.assertTrue(scene.commands_popup_visible)
        self.assertEqual(scene.input_bar.prompt, "help> ")
        self.assertEqual(scene.input_bar.focus_target, "input")
        self.assertEqual(scene.input_bar.mode_label, "insert")

    def test_print_trace_payload_renders_trace_sections_and_populates_session_trace_panel(self) -> None:
        payload = {
            "run_id": "01TRACE",
            "summary": {"status": "completed"},
            "display_summary": "summary line",
            "subagent_trace": [
                {
                    "task_name": "collect:stock_snapshot_collector",
                    "agent_label": "Stock Snapshot Collector",
                    "agent_path": "collector/stock_snapshot_collector",
                    "status": "completed",
                }
            ],
            "dynamic_trace": [
                {
                    "event": "dynamic_action_inserted",
                    "phase": "review",
                    "agent": "screen_reviewer",
                    "objective": "Review candidates",
                    "node_id": "screen_review",
                }
            ],
            "wait_trace": [
                {"event": "waiting_external_started", "node_id": "collect"}
            ],
        }

        output = self._capture_payload_text(payload)
        session = self._session()
        self._emit_payload_to_session(session, payload)

        self.assertIn("run_id: 01TRACE", output)
        self.assertIn("subagent_trace:", output)
        self.assertIn("dynamic_trace:", output)
        self.assertIn("wait_trace:", output)
        self.assertIn("collect:stock_snapshot_collector", output)
        self.assertIn("Review candidates", output)
        self.assertEqual(session.trace_payload, payload)

    def test_print_events_payload_renders_event_rows_and_populates_session_events_panel(self) -> None:
        payload = {
            "run_id": "01EVENTS",
            "summary": {"status": "running"},
            "event_counts": {"events": 2, "distinct_event_types": 2},
            "events": [
                {
                    "event_type": "subagent_started",
                    "task_name": "collect:stock_snapshot_collector",
                    "agent_path": "collector/stock_snapshot_collector",
                    "agent_label": "Stock Snapshot Collector",
                    "invocation_id": "01INVOCATION",
                },
                {
                    "event_type": "dynamic_action_inserted",
                    "node_id": "screen_review",
                    "objective": "Review candidates",
                },
            ],
        }

        output = self._capture_payload_text(payload)
        session = self._session()
        self._emit_payload_to_session(session, payload)

        self.assertIn("events: 2", output)
        self.assertIn("event_types: 2", output)
        self.assertIn("[subagent_started]", output)
        self.assertIn("call_id=01INVOCATION", output)
        self.assertIn("[dynamic_action_inserted]", output)
        self.assertEqual(session.events_payload, payload)

    def test_print_metrics_payload_renders_runtime_observability_sections(self) -> None:
        payload = {
            "run_id": "01METRICS",
            "request": {"intent": "stock_check", "user_query": "inspect runtime"},
            "summary": {"status": "completed"},
            "display_summary": "metrics ready",
            "counts": {"events": 2, "items": 3, "threads": 2, "subagents": 2, "llm_calls": 1},
            "rates": {"fallback_rate": 0.5, "memory_hit_rate": 1.0, "qdrant_hit_rate": 0.5},
            "latency_ms": {"subagents": {"count": 2}, "actions": {"count": 1}, "per_subagent": {}, "per_action": {}},
            "fanout": {"max_depth": 1, "per_parent_child_count": {"thread-root": 1}},
            "turn_usage": {"turn-root": {"thread_id": "thread-root", "llm_calls": 1}},
            "event_type_counts": {"subagent_completed": 2},
            "degrade_reason_counts": {"llm_budget_exhausted": 1},
        }

        output = self._capture_payload_text(payload)

        self.assertIn("run_id: 01METRICS", output)
        self.assertIn("counts:", output)
        self.assertIn("rates:", output)
        self.assertIn("latency_ms:", output)
        self.assertIn("turn_usage:", output)

    def test_print_thread_payload_renders_thread_scoped_trace_and_items(self) -> None:
        payload = {
            "run_id": "01RUN",
            "thread_id": "thread-child",
            "request": {"intent": "stock_check", "user_query": "inspect child"},
            "summary": {"status": "completed"},
            "display_summary": "thread ready",
            "thread": {"thread_id": "thread-child", "status": "completed"},
            "subagent_trace": [
                {
                    "task_name": "retrieve:qdrant_evidence_searcher",
                    "agent_label": "Qdrant Evidence Searcher",
                    "agent_path": "retriever/qdrant_evidence_searcher",
                    "status": "completed",
                }
            ],
            "events": [
                {
                    "event_type": "subagent_completed",
                    "task_name": "retrieve:qdrant_evidence_searcher",
                    "agent_path": "retriever/qdrant_evidence_searcher",
                }
            ],
            "wait_trace": [],
            "items": [{"item_id": "item-1", "item_type": "subagent", "status": "completed"}],
            "event_counts": {"events": 1, "subagent_trace": 1, "wait_trace": 0, "items": 1},
        }

        output = self._capture_payload_text(payload)

        self.assertIn("thread_id: thread-child", output)
        self.assertIn("thread:", output)
        self.assertIn("subagent_trace:", output)
        self.assertIn("events:", output)
        self.assertIn("items:", output)

    def test_print_steps_payload_renders_strategy_dispatch_and_steps(self) -> None:
        payload = {
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
        }

        output = self._capture_payload_text(payload)

        self.assertIn("strategy: dynamic", output)
        self.assertIn("dispatch_trace:", output)
        self.assertIn("status_counts: active=1, completed=1", output)
        self.assertIn("1. collect [completed]  phase=collect agent=collector", output)
        self.assertIn("2. retrieve [active]  phase=retrieve agent=retriever  next=active", output)
        self.assertIn("supervisor_note: advance to retrieval", output)

    def test_print_subagent_payload_renders_call_trace_and_populates_subagent_panel(self) -> None:
        payload = {
            "run_id": "01RUN",
            "invocation_id": "01INVOCATION",
            "subagent_type": "stock_snapshot_collector",
            "objective": "Collect stock-level verified snapshots.",
            "thread_id": "thread-root",
            "turn_id": "turn-root",
            "phase": "collect",
            "agent": "collector",
            "llm_used": False,
            "duration_ms": 42,
            "item": {
                "item_id": "item-1",
                "thread_id": "thread-root",
                "turn_id": "turn-root",
                "status": "completed",
                "degrade_reason": None,
            },
            "trace_rows": [
                {
                    "task_name": "collect:stock_snapshot_collector",
                    "agent_label": "Stock Snapshot Collector",
                    "agent_path": "collector/stock_snapshot_collector",
                    "status": "completed",
                }
            ],
            "events": [
                {
                    "event_type": "subagent_completed",
                    "task_name": "collect:stock_snapshot_collector",
                    "agent_path": "collector/stock_snapshot_collector",
                }
            ],
        }

        output = self._capture_payload_text(payload)
        session = self._session()
        self._emit_payload_to_session(session, payload)

        self.assertIn("call_id: 01INVOCATION", output)
        self.assertIn("objective: Collect stock-level verified snapshots.", output)
        self.assertIn("thread_id: thread-root", output)
        self.assertIn("duration_ms: 42", output)
        self.assertIn("item:", output)
        self.assertIn("trace_rows:", output)
        self.assertIn("events:", output)
        self.assertEqual(session.subagent_payload, payload)

    def test_print_doctor_payload_groups_checks_and_updates_status(self) -> None:
        payload = {
            "status": "failed",
            "checks": [
                {"name": "env_file", "status": "passed", "path": "D:/Cognitrade/.env"},
                {"name": "openai_api_key", "status": "failed", "detail": "missing"},
                {"name": "qdrant_local_dir", "status": "skipped", "path": "D:/Cognitrade/workspace/qdrant_local"},
            ],
        }

        output = self._capture_payload_text(payload)
        session = self._session()
        self._emit_payload_to_session(session, payload)

        self.assertIn("doctor_status: failed", output)
        self.assertIn("failed:", output)
        self.assertIn("  openai_api_key  missing", output)
        self.assertIn("passed:", output)
        self.assertIn("skipped:", output)
        self.assertEqual(session.run_status_label, "doctor:failed")

    def test_print_payload_renders_proposal_rows_and_detail(self) -> None:
        rows_output = self._capture_payload_text(
            [
                {
                    "proposal_id": "01PROP",
                    "status": "pending",
                    "proposal_type": "MethodologyTemplatePatch",
                    "title": "Promote policy catalyst",
                    "sample_size": 1,
                    "review_reason": "pending_manual_review_dimension_sample_size<2",
                }
            ]
        )
        detail_output = self._capture_payload_text(
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
            }
        )

        self.assertIn("01PROP  [pending]  MethodologyTemplatePatch  Promote policy catalyst  sample_size=1", rows_output)
        self.assertIn("proposal_id: 01PROP", detail_output)
        self.assertIn("benchmark_scope: event_driven", detail_output)
        self.assertIn("review_history:", detail_output)
        self.assertIn("[pending] reviewer=system_threshold_gate source=auto_gate", detail_output)

    def test_print_payload_renders_subagent_discovery_rows_and_detail(self) -> None:
        rows_output = self._capture_payload_text(
            [
                {
                    "ticket_id": "01DISC",
                    "status": "approved",
                    "intent": "stock_screen",
                    "stage": "collect",
                    "capability": "screening_strategy",
                    "search_query": "screening strategy resolver",
                    "candidate_entry_count": 1,
                    "activation_entry_count": 0,
                    "updated_at": "2026-07-08T00:00:00+00:00",
                }
            ]
        )
        detail_output = self._capture_payload_text(
            {
                "ticket_id": "01DISC",
                "status": "approved",
                "stored_status": "approved",
                "capability_gap": {
                    "intent": "stock_screen",
                    "stage": "collect",
                    "capability": "screening_strategy",
                    "preferred_subagent_type": "candidate_screening_strategy",
                    "requires_llm": False,
                    "reason": "missing capability",
                },
                "tool_discovery_request": {
                    "search_query": "screening strategy resolver",
                    "preferred_protocols": ["mcp"],
                    "reason": "discover resolver",
                },
                "candidate_catalog_entries": [
                    {
                        "subagent_type": "candidate_screening_strategy",
                        "stage": "collect",
                        "capabilities": ["screening_strategy"],
                        "tags": ["approved_discovery_candidate"],
                    }
                ],
                "activated_entries": [
                    {
                        "subagent_type": "candidate_screening_strategy",
                        "delegate_subagent_type": "stock_snapshot_collector",
                        "status": "active",
                        "activated_by": "ops_user",
                    }
                ],
                "notes": ["approved candidate available"],
            }
        )
        runtime_detail_output = self._capture_payload_text(
            {
                "ticket_id": "01DISC",
                "status": "approved",
                "stored_status": "approved",
                "capability_gap": {
                    "intent": "stock_screen",
                    "stage": "collect",
                    "capability": "screening_strategy",
                    "preferred_subagent_type": "candidate_screening_strategy",
                    "requires_llm": False,
                    "reason": "missing capability",
                },
                "tool_discovery_request": {
                    "search_query": "screening strategy resolver",
                    "preferred_protocols": ["mcp"],
                    "reason": "discover resolver",
                },
                "candidate_catalog_entries": [],
                "activated_entries": [
                    {
                        "subagent_type": "candidate_screening_strategy",
                        "delegate_subagent_type": None,
                        "runtime_registration": {
                            "registration_type": "snapshot_skill_runner",
                            "skill_name": "market_snapshot_skill",
                            "snapshot_type": "market",
                        },
                        "status": "active",
                        "activated_by": "ops_user",
                    }
                ],
                "notes": ["approved candidate available"],
            }
        )
        http_runtime_detail_output = self._capture_payload_text(
            {
                "ticket_id": "01DISC",
                "status": "approved",
                "stored_status": "approved",
                "capability_gap": {
                    "intent": "stock_screen",
                    "stage": "collect",
                    "capability": "screening_strategy",
                    "preferred_subagent_type": "candidate_screening_strategy",
                    "requires_llm": False,
                    "reason": "missing capability",
                },
                "tool_discovery_request": {
                    "search_query": "screening strategy resolver",
                    "preferred_protocols": ["mcp"],
                    "reason": "discover resolver",
                },
                "candidate_catalog_entries": [],
                "activated_entries": [
                    {
                        "subagent_type": "candidate_collect_http_extension",
                        "delegate_subagent_type": None,
                        "runtime_registration": {
                            "registration_type": "http_json_runner",
                            "skill_name": None,
                            "snapshot_type": None,
                            "source_id": "eastmoney",
                            "endpoint_path": "/api/market-signal",
                            "http_method": "GET",
                            "timeout_seconds": 9.5,
                        },
                        "status": "active",
                        "activated_by": "ops_user",
                    }
                ],
                "notes": ["approved candidate available"],
            }
        )

        self.assertIn("01DISC  [approved]", rows_output)
        self.assertIn("capability=screening_strategy", rows_output)
        self.assertIn("ticket_id: 01DISC", detail_output)
        self.assertIn("search_query: screening strategy resolver", detail_output)
        self.assertIn("candidate_catalog_entries:", detail_output)
        self.assertIn("activated_entries:", detail_output)
        self.assertIn("candidate_screening_strategy -> stock_snapshot_collector", detail_output)
        self.assertIn("runtime:snapshot_skill_runner skill=market_snapshot_skill snapshot=market", runtime_detail_output)
        self.assertIn(
            "runtime:http_json_runner source=eastmoney method=GET path=/api/market-signal",
            http_runtime_detail_output,
        )


if __name__ == "__main__":
    unittest.main()
