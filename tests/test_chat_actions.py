from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.cli.runtime.chat_actions import handle_chat_cli_action, handle_chat_meta_action
from research_system.cli.memory.chat_memory import (
    archive_session_thread,
    compact_session_thread,
    remember_basic_chat_turn,
    rename_session_thread,
)
from research_system.cli.rendering.chat_rendering import apply_tui_update
from research_system.cli.state import persist_thread_checkpoint
from research_system.cli.state.chat_types import ChatSessionState, TuiSession
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout
from research_system.services.routing.router.task_router import TaskRouter


class ProviderUnavailableStub:
    available = False


class ChatActionsTests(unittest.TestCase):
    def test_handle_chat_meta_action_compact_thread_emits_summary_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            session = TuiSession(
                settings=settings,
                state=ChatSessionState(
                    last_user_query="analyze guizhou maotai",
                    last_result_intent="stock_check",
                    last_result_summary="valuation is not cheap but cashflow is resilient",
                    last_run_id="01LAST",
                ),
                llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
                task_router=TaskRouter(),
                output_lines=[],
            )

            handle_chat_meta_action(
                action="compact_thread",
                parsed={"action": "compact_thread"},
                state=session.state,
                settings=session.settings,
                emit_text=lambda output_writer, update: apply_tui_update(session, update),
                output_writer=None,
            )

            meta_lines = [message.text for message in session.output_lines if message.kind == "meta"]
            self.assertIn(f"thread compacted: {session.state.thread_id}", meta_lines)
            self.assertTrue(any(line.startswith("summary_turns: ") for line in meta_lines))

    def test_handle_chat_meta_action_show_thread_displays_topic_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            session = TuiSession(
                settings=settings,
                state=ChatSessionState(
                    last_user_query="compare guizhou maotai, ningde shidai, and byd",
                    last_result_intent="stock_compare",
                    last_result_summary="cashflow resilience still needs deeper comparison",
                    last_run_id="01COMPARE",
                ),
                llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
                task_router=TaskRouter(),
                output_lines=[],
            )

            for idx in range(9):
                remember_basic_chat_turn(settings=settings, state=session.state, query=f"question {idx}", answer=f"answer {idx}")

            handle_chat_meta_action(
                action="compact_thread",
                parsed={"action": "compact_thread"},
                state=session.state,
                settings=session.settings,
                emit_text=lambda output_writer, update: apply_tui_update(session, update),
                output_writer=None,
            )
            handle_chat_meta_action(
                action="show_thread",
                parsed={"action": "show_thread"},
                state=session.state,
                settings=session.settings,
                emit_text=lambda output_writer, update: apply_tui_update(session, update),
                output_writer=None,
            )

            meta_lines = [message.text for message in session.output_lines if message.kind == "meta"]
            self.assertTrue(any(line.startswith("thread_topic: ") for line in meta_lines))
            self.assertTrue(any(line.startswith("thread_lifetime_turns: ") for line in meta_lines))

    def test_handle_chat_meta_action_rename_thread_emits_explicit_thread_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            session = TuiSession(
                settings=settings,
                state=ChatSessionState(
                    last_user_query="compare guizhou maotai, ningde shidai, and byd",
                    last_result_intent="stock_compare",
                    last_result_summary="cashflow resilience still needs deeper comparison",
                    last_run_id="01COMPARE",
                ),
                llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
                task_router=TaskRouter(),
                output_lines=[],
            )

            handle_chat_meta_action(
                action="rename_thread",
                parsed={"thread_label": "Bank compare shortlist"},
                state=session.state,
                settings=session.settings,
                emit_text=lambda output_writer, update: apply_tui_update(session, update),
                output_writer=None,
            )
            handle_chat_meta_action(
                action="show_thread",
                parsed={"action": "show_thread"},
                state=session.state,
                settings=session.settings,
                emit_text=lambda output_writer, update: apply_tui_update(session, update),
                output_writer=None,
            )

            meta_lines = [message.text for message in session.output_lines if message.kind == "meta"]
            self.assertIn(f"thread_renamed: {session.state.thread_id}", meta_lines)
            self.assertIn("thread_name: Bank compare shortlist", meta_lines)

    def test_handle_chat_meta_action_show_thread_displays_status_and_next_step(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            session = TuiSession(
                settings=settings,
                state=ChatSessionState(
                    last_user_query="compare guizhou maotai, ningde shidai, and byd",
                    last_result_intent="stock_compare",
                    last_result_summary="cashflow resilience still needs deeper comparison",
                    last_result_follow_up="supplement same-basis cashflow and valuation assumptions",
                    pending_clarification_question="Do you want medium-term or long-term comparison?",
                    last_run_id="01COMPARE",
                ),
                llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
                task_router=TaskRouter(),
                output_lines=[],
            )

            handle_chat_meta_action(
                action="show_thread",
                parsed={"action": "show_thread"},
                state=session.state,
                settings=session.settings,
                emit_text=lambda output_writer, update: apply_tui_update(session, update),
                output_writer=None,
            )

            meta_lines = [message.text for message in session.output_lines if message.kind == "meta"]
            self.assertIn("thread_status: clarification_pending", meta_lines)
            self.assertIn("thread_open_question: Do you want medium-term or long-term comparison?", meta_lines)
            self.assertIn("thread_next_step: supplement same-basis cashflow and valuation assumptions", meta_lines)

    def test_handle_chat_cli_action_threads_displays_active_marker_and_topic(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            session = TuiSession(
                settings=settings,
                state=ChatSessionState(
                    last_user_query="compare guizhou maotai, ningde shidai, and byd",
                    last_result_intent="stock_compare",
                    last_result_summary="cashflow resilience still needs deeper comparison",
                    last_run_id="01COMPARE",
                ),
                llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
                task_router=TaskRouter(),
                output_lines=[],
            )

            for idx in range(9):
                remember_basic_chat_turn(settings=settings, state=session.state, query=f"question {idx}", answer=f"answer {idx}")

            handle_chat_meta_action(
                action="compact_thread",
                parsed={"action": "compact_thread"},
                state=session.state,
                settings=session.settings,
                emit_text=lambda output_writer, update: apply_tui_update(session, update),
                output_writer=None,
            )
            handled = handle_chat_cli_action(
                action="threads",
                parsed={"action": "threads", "limit": 10},
                state=session.state,
                settings=session.settings,
                emit_text=lambda output_writer, update: apply_tui_update(session, update),
                cli_command_runner=lambda *args, **kwargs: {},
                payload_printer=lambda payload, as_json=False, output_writer=None: None,
                output_writer=None,
            )

            self.assertTrue(handled)
            meta_lines = [message.text for message in session.output_lines if message.kind == "meta"]
            self.assertTrue(any(line.startswith("* ") for line in meta_lines))
            self.assertTrue(any("intent=stock_compare" in line for line in meta_lines))
            self.assertTrue(any("topic=compare guizhou maotai, ningde shidai, and byd" in line for line in meta_lines))

    def test_handle_chat_cli_action_threads_displays_explicit_thread_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            session = TuiSession(
                settings=settings,
                state=ChatSessionState(
                    last_user_query="compare guizhou maotai, ningde shidai, and byd",
                    last_result_intent="stock_compare",
                    last_result_summary="cashflow resilience still needs deeper comparison",
                    last_run_id="01COMPARE",
                ),
                llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
                task_router=TaskRouter(),
                output_lines=[],
            )

            handle_chat_meta_action(
                action="rename_thread",
                parsed={"thread_label": "Bank compare shortlist"},
                state=session.state,
                settings=session.settings,
                emit_text=lambda output_writer, update: apply_tui_update(session, update),
                output_writer=None,
            )
            handled = handle_chat_cli_action(
                action="threads",
                parsed={"action": "threads", "limit": 10},
                state=session.state,
                settings=session.settings,
                emit_text=lambda output_writer, update: apply_tui_update(session, update),
                cli_command_runner=lambda *args, **kwargs: {},
                payload_printer=lambda payload, as_json=False, output_writer=None: None,
                output_writer=None,
            )

            self.assertTrue(handled)
            meta_lines = [message.text for message in session.output_lines if message.kind == "meta"]
            self.assertTrue(any("name=Bank compare shortlist" in line for line in meta_lines))

    def test_handle_chat_cli_action_subagent_read_dispatches_call_scoped_inspect(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(last_run_id="01RUN"),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )
        calls: list[tuple[str, str | None, str | None]] = []
        handled = handle_chat_cli_action(
            action="subagent-read",
            parsed={"action": "subagent-read", "run_id": "last", "call_id": "01INV"},
            state=session.state,
            settings=session.settings,
            emit_text=lambda output_writer, update: apply_tui_update(session, update),
            cli_command_runner=lambda _settings, **kwargs: calls.append((kwargs.get("command"), kwargs.get("run_id"), kwargs.get("query"))) or {
                "run_id": "01RUN",
                "invocation_id": "01INV",
                "trace_rows": [],
                "events": [],
                "event_counts": {"trace_rows": 0, "events": 0},
            },
            payload_printer=lambda payload, as_json=False, output_writer=None: None,
            output_writer=None,
        )

        self.assertTrue(handled)
        self.assertEqual(calls, [("subagent-read", "01RUN", "01INV")])

    def test_handle_chat_cli_action_run_steps_dispatches_run_scoped_plan_inspect(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(last_run_id="01RUN"),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )
        calls: list[tuple[str, str | None, str | None]] = []
        handled = handle_chat_cli_action(
            action="run-steps",
            parsed={"action": "run-steps", "run_id": "last"},
            state=session.state,
            settings=session.settings,
            emit_text=lambda output_writer, update: apply_tui_update(session, update),
            cli_command_runner=lambda _settings, **kwargs: calls.append((kwargs.get("command"), kwargs.get("run_id"), kwargs.get("query"))) or {
                "run_id": "01RUN",
                "steps": [],
                "step_counts": {"total": 0, "active": 0, "completed": 0, "pending": 0},
            },
            payload_printer=lambda payload, as_json=False, output_writer=None: None,
            output_writer=None,
        )

        self.assertTrue(handled)
        self.assertEqual(calls, [("run-steps", "01RUN", None)])

    def test_handle_chat_cli_action_metrics_dispatches_run_scoped_metrics_inspect(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(last_run_id="01RUN"),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )
        calls: list[tuple[str, str | None, str | None]] = []
        handled = handle_chat_cli_action(
            action="metrics",
            parsed={"action": "metrics", "run_id": "last"},
            state=session.state,
            settings=session.settings,
            emit_text=lambda output_writer, update: apply_tui_update(session, update),
            cli_command_runner=lambda _settings, **kwargs: calls.append((kwargs.get("command"), kwargs.get("run_id"), kwargs.get("query"))) or {
                "run_id": "01RUN",
                "request": {"intent": "stock_check", "user_query": "inspect runtime"},
                "summary": {"status": "completed"},
                "counts": {"events": 1, "items": 1, "threads": 1, "subagents": 1, "llm_calls": 0},
                "rates": {"fallback_rate": 0.0, "memory_hit_rate": None, "qdrant_hit_rate": None},
                "latency_ms": {"subagents": {}, "actions": {}, "per_subagent": {}, "per_action": {}},
                "fanout": {"max_depth": 0, "per_parent_child_count": {}},
                "turn_usage": {},
                "event_type_counts": {},
                "degrade_reason_counts": {},
            },
            payload_printer=lambda payload, as_json=False, output_writer=None: None,
            output_writer=None,
        )

        self.assertTrue(handled)
        self.assertEqual(calls, [("metrics", "01RUN", None)])

    def test_handle_chat_cli_action_run_thread_dispatches_thread_scoped_inspect(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(last_run_id="01RUN"),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )
        calls: list[tuple[str, str | None, str | None]] = []
        handled = handle_chat_cli_action(
            action="run-thread",
            parsed={"action": "run-thread", "run_id": "last", "thread_id": "thread-child"},
            state=session.state,
            settings=session.settings,
            emit_text=lambda output_writer, update: apply_tui_update(session, update),
            cli_command_runner=lambda _settings, **kwargs: calls.append((kwargs.get("command"), kwargs.get("run_id"), kwargs.get("query"))) or {
                "run_id": "01RUN",
                "thread_id": "thread-child",
                "thread": {"thread_id": "thread-child", "status": "completed"},
                "events": [],
                "subagent_trace": [],
                "wait_trace": [],
                "items": [],
                "event_counts": {"events": 0, "subagent_trace": 0, "wait_trace": 0, "items": 0},
            },
            payload_printer=lambda payload, as_json=False, output_writer=None: None,
            output_writer=None,
        )

        self.assertTrue(handled)
        self.assertEqual(calls, [("run-thread", "01RUN", "thread-child")])

    def test_handle_chat_cli_action_rerun_from_updates_last_run_to_new_rerun(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(last_run_id="01SOURCE"),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )

        handled = handle_chat_cli_action(
            action="rerun-from",
            parsed={"action": "rerun-from", "run_id": "last", "node_id": "retrieve"},
            state=session.state,
            settings=session.settings,
            emit_text=lambda output_writer, update: apply_tui_update(session, update),
            cli_command_runner=lambda _settings, **kwargs: {
                "run_id": "01RERUN",
                "status": "completed",
                "message": "reran from retrieve",
                "source_run_id": kwargs.get("run_id"),
            },
            payload_printer=lambda payload, as_json=False, output_writer=None: None,
            output_writer=None,
        )

        self.assertTrue(handled)
        self.assertEqual(session.state.last_run_id, "01RERUN")

    def test_handle_chat_cli_action_rerun_subagent_updates_last_run_to_new_rerun(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(last_run_id="01SOURCE"),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )

        calls: list[dict[str, object]] = []
        handled = handle_chat_cli_action(
            action="rerun-subagent",
            parsed={
                "action": "rerun-subagent",
                "run_id": "last",
                "action_node_id": "retrieve",
                "dag_id": "phase_two",
                "subagent_node_id": "retrieval_decider",
            },
            state=session.state,
            settings=session.settings,
            emit_text=lambda output_writer, update: apply_tui_update(session, update),
            cli_command_runner=lambda _settings, **kwargs: calls.append(dict(kwargs)) or {
                "run_id": "01SUBRERUN",
                "status": "completed",
                "message": "reran retrieve/phase_two/retrieval_decider",
                "source_run_id": kwargs.get("run_id"),
            },
            payload_printer=lambda payload, as_json=False, output_writer=None: None,
            output_writer=None,
        )

        self.assertTrue(handled)
        self.assertEqual(session.state.last_run_id, "01SUBRERUN")
        self.assertEqual(
            calls,
            [
                {
                    "command": "rerun-subagent",
                    "run_id": "01SOURCE",
                    "action_node_id": "retrieve",
                    "dag_id": "phase_two",
                    "subagent_node_id": "retrieval_decider",
                }
            ],
        )

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

    def test_handle_chat_meta_action_set_thread_restores_saved_thread_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            persisted = ChatSessionState(
                thread_id="thread_saved",
                last_user_query="analyze guizhou maotai",
                last_result_intent="stock_check",
                last_result_summary="valuation is not cheap but cashflow is resilient",
                last_run_id="01LATEST",
            )
            persist_thread_checkpoint(settings=settings, state=persisted)
            rename_session_thread(settings=settings, state=persisted, thread_label="Saved banking review")
            session = TuiSession(
                settings=settings,
                state=ChatSessionState(thread_id="thread_old", last_user_query="temporary query"),
                llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
                task_router=TaskRouter(),
                output_lines=[],
            )

            handle_chat_meta_action(
                action="set_thread",
                parsed={"thread_id": "thread_saved"},
                state=session.state,
                settings=session.settings,
                emit_text=lambda output_writer, update: apply_tui_update(session, update),
                output_writer=None,
            )

            self.assertEqual(session.state.thread_id, "thread_saved")
            self.assertEqual(session.state.last_user_query, "analyze guizhou maotai")
            self.assertEqual(session.state.last_result_summary, "valuation is not cheap but cashflow is resilient")
            meta_lines = [message.text for message in session.output_lines if message.kind == "meta"]
            self.assertIn("thread_resumed: thread_saved", meta_lines)
            self.assertIn("thread_resumed_from: thread_old", meta_lines)
            self.assertIn("thread_name: Saved banking review", meta_lines)
            self.assertIn("thread_checkpointed: yes", meta_lines)

    def test_handle_chat_meta_action_read_thread_inspects_without_resuming(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            saved = ChatSessionState(
                thread_id="thread_saved",
                last_user_query="compare banks",
                last_result_intent="stock_compare",
                last_result_summary="ranking still inconclusive",
                last_run_id="01COMPARE",
            )
            remember_basic_chat_turn(settings=settings, state=saved, query="which bank is stronger", answer="still inconclusive")
            rename_session_thread(settings=settings, state=saved, thread_label="Saved bank compare")
            compact_session_thread(settings=settings, state=saved)
            persist_thread_checkpoint(settings=settings, state=saved)

            session = TuiSession(
                settings=settings,
                state=ChatSessionState(thread_id="thread_active", last_user_query="temporary query"),
                llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
                task_router=TaskRouter(),
                output_lines=[],
            )

            handle_chat_meta_action(
                action="read_thread",
                parsed={"thread_id": "thread_saved"},
                state=session.state,
                settings=session.settings,
                emit_text=lambda output_writer, update: apply_tui_update(session, update),
                output_writer=None,
            )

            self.assertEqual(session.state.thread_id, "thread_active")
            self.assertEqual(session.state.last_user_query, "temporary query")
            meta_lines = [message.text for message in session.output_lines if message.kind == "meta"]
            self.assertIn("thread_read: thread_saved", meta_lines)
            self.assertIn("thread_name: Saved bank compare", meta_lines)
            self.assertIn("thread_compacted: yes", meta_lines)
            self.assertIn("thread_checkpointed: yes", meta_lines)
            self.assertTrue(any(line.startswith("thread_summary: ") for line in meta_lines))
            self.assertTrue(any(line.startswith("thread_turn: ") for line in meta_lines))

    def test_handle_chat_meta_action_fork_thread_creates_child_thread_and_reports_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            session = TuiSession(
                settings=settings,
                state=ChatSessionState(
                    last_user_query="compare guizhou maotai and byd",
                    last_result_intent="stock_compare",
                    last_result_summary="ranking is still inconclusive",
                    last_run_id="01COMPARE",
                ),
                llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
                task_router=TaskRouter(),
                output_lines=[],
            )

            remember_basic_chat_turn(settings=settings, state=session.state, query="question", answer="answer")
            parent_thread_id = session.state.thread_id

            handle_chat_meta_action(
                action="fork_thread",
                parsed={"action": "fork_thread"},
                state=session.state,
                settings=session.settings,
                emit_text=lambda output_writer, update: apply_tui_update(session, update),
                output_writer=None,
            )

            self.assertNotEqual(session.state.thread_id, parent_thread_id)
            self.assertEqual(session.state.thread_source, "fork")
            self.assertEqual(session.state.parent_thread_id, parent_thread_id)
            meta_lines = [message.text for message in session.output_lines if message.kind == "meta"]
            self.assertTrue(any(line.startswith("forked thread: ") for line in meta_lines))
            self.assertIn(f"forked_from: {parent_thread_id}", meta_lines)

    def test_handle_chat_meta_action_archive_thread_switches_active_thread(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            session = TuiSession(
                settings=settings,
                state=ChatSessionState(
                    last_user_query="compare banks",
                    last_result_intent="stock_compare",
                    last_result_summary="ranking is still inconclusive",
                    last_run_id="01COMPARE",
                ),
                llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
                task_router=TaskRouter(),
                output_lines=[],
            )

            remember_basic_chat_turn(settings=settings, state=session.state, query="question", answer="answer")
            archived_thread_id = session.state.thread_id

            handle_chat_meta_action(
                action="archive_thread",
                parsed={"action": "archive_thread"},
                state=session.state,
                settings=session.settings,
                emit_text=lambda output_writer, update: apply_tui_update(session, update),
                output_writer=None,
            )

            self.assertNotEqual(session.state.thread_id, archived_thread_id)
            meta_lines = [message.text for message in session.output_lines if message.kind == "meta"]
            self.assertIn(f"thread_archived: {archived_thread_id}", meta_lines)
            self.assertTrue(any(line.startswith("thread_switched_to: ") for line in meta_lines))

    def test_handle_chat_meta_action_unarchive_thread_reports_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            session = TuiSession(
                settings=settings,
                state=ChatSessionState(
                    last_user_query="compare banks",
                    last_result_intent="stock_compare",
                    last_result_summary="ranking is still inconclusive",
                    last_run_id="01COMPARE",
                ),
                llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
                task_router=TaskRouter(),
                output_lines=[],
            )

            remember_basic_chat_turn(settings=settings, state=session.state, query="question", answer="answer")
            archived_thread_id = session.state.thread_id
            archive_session_thread(settings=settings, state=session.state)

            handle_chat_meta_action(
                action="unarchive_thread",
                parsed={"action": "unarchive_thread", "thread_id": archived_thread_id},
                state=session.state,
                settings=session.settings,
                emit_text=lambda output_writer, update: apply_tui_update(session, update),
                output_writer=None,
            )

            meta_lines = [message.text for message in session.output_lines if message.kind == "meta"]
            self.assertIn(f"thread_unarchived: {archived_thread_id}", meta_lines)

    def test_handle_chat_meta_action_stop_uses_stop_callback(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(last_run_id="01RUN"),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )
        called: list[str] = []

        handle_chat_meta_action(
            action="stop",
            parsed={"action": "stop"},
            state=session.state,
            settings=session.settings,
            emit_text=lambda output_writer, update: apply_tui_update(session, update),
            output_writer=None,
            stop_callback=lambda: called.append("stop"),
        )

        self.assertEqual(called, ["stop"])
        meta_lines = [message.text for message in session.output_lines if message.kind == "meta"]
        self.assertIn("stop requested", meta_lines)

    def test_handle_chat_meta_action_show_last_hydrates_checkpointed_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            persisted = ChatSessionState(
                thread_id="thread_saved",
                current_intent="stock_compare",
                review_target_run_id="01TARGET",
                last_run_id="01LATEST",
                last_user_query="compare guizhou maotai and catl",
                last_result_summary="ranking is still inconclusive",
            )
            persist_thread_checkpoint(settings=settings, state=persisted)
            session = TuiSession(
                settings=settings,
                state=ChatSessionState(thread_id="thread_saved"),
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

            self.assertEqual(session.state.last_run_id, "01LATEST")
            self.assertEqual(session.state.current_intent, "stock_compare")
            self.assertEqual(session.state.review_target_run_id, "01TARGET")
            meta_lines = [message.text for message in session.output_lines if message.kind == "meta"]
            self.assertIn("last_run_id: 01LATEST", meta_lines)
            self.assertIn("intent: stock_compare", meta_lines)

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

    def test_handle_chat_cli_action_supports_trace_command(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(last_run_id="01OLD"),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )

        def fake_cli_command_runner(settings, *, command, run_id, **kwargs):
            self.assertEqual(command, "trace")
            self.assertEqual(run_id, "01TRACE")
            return {
                "run_id": "01TRACE",
                "status": "completed",
                "request": {"intent": "stock_compare", "user_query": "compare banks"},
                "summary": {"status": "completed"},
                "subagent_trace": [],
                "dynamic_trace": [],
                "wait_trace": [],
            }

        rendered: list[object] = []
        handled = handle_chat_cli_action(
            action="trace",
            parsed={"action": "trace", "run_id": "01TRACE"},
            state=session.state,
            settings=session.settings,
            emit_text=lambda output_writer, update: apply_tui_update(session, update),
            cli_command_runner=fake_cli_command_runner,
            payload_printer=lambda payload, as_json=False, output_writer=None: rendered.append(payload),
            output_writer=None,
        )

        self.assertTrue(handled)
        self.assertEqual(session.state.last_run_id, "01TRACE")
        self.assertEqual(rendered[0]["run_id"], "01TRACE")

    def test_handle_chat_cli_action_supports_events_command(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(last_run_id="01OLD"),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )

        def fake_cli_command_runner(settings, *, command, run_id, **kwargs):
            self.assertEqual(command, "events")
            self.assertEqual(run_id, "01EVENTS")
            return {
                "run_id": "01EVENTS",
                "status": "completed",
                "request": {"intent": "stock_compare", "user_query": "compare banks"},
                "summary": {"status": "completed"},
                "event_counts": {"events": 1, "distinct_event_types": 1},
                "events": [],
            }

        rendered: list[object] = []
        handled = handle_chat_cli_action(
            action="events",
            parsed={"action": "events", "run_id": "01EVENTS"},
            state=session.state,
            settings=session.settings,
            emit_text=lambda output_writer, update: apply_tui_update(session, update),
            cli_command_runner=fake_cli_command_runner,
            payload_printer=lambda payload, as_json=False, output_writer=None: rendered.append(payload),
            output_writer=None,
        )

        self.assertTrue(handled)
        self.assertEqual(session.state.last_run_id, "01EVENTS")
        self.assertEqual(rendered[0]["run_id"], "01EVENTS")

    def test_handle_chat_cli_action_threads_supports_archived_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            session = TuiSession(
                settings=settings,
                state=ChatSessionState(
                    last_user_query="compare banks",
                    last_result_intent="stock_compare",
                    last_result_summary="ranking is still inconclusive",
                    last_run_id="01COMPARE",
                ),
                llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
                task_router=TaskRouter(),
                output_lines=[],
            )

            remember_basic_chat_turn(settings=settings, state=session.state, query="question", answer="answer")
            archived_thread_id = session.state.thread_id
            archive_session_thread(settings=settings, state=session.state)

            handled = handle_chat_cli_action(
                action="threads",
                parsed={"action": "threads", "limit": 10, "scope": "archived"},
                state=session.state,
                settings=session.settings,
                emit_text=lambda output_writer, update: apply_tui_update(session, update),
                cli_command_runner=lambda *args, **kwargs: {},
                payload_printer=lambda payload, as_json=False, output_writer=None: None,
                output_writer=None,
            )

            self.assertTrue(handled)
            meta_lines = [message.text for message in session.output_lines if message.kind == "meta"]
            self.assertTrue(any(archived_thread_id in line and "status=archived" in line for line in meta_lines))

    def test_handle_chat_cli_action_supports_proposals_and_review_proposal(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )
        calls: list[dict] = []

        def fake_cli_command_runner(settings, *, command, **kwargs):
            calls.append({"command": command, **kwargs})
            if command == "proposals":
                return [{"proposal_id": "01PROP", "status": "pending", "proposal_type": "MethodologyTemplatePatch", "title": "Promote policy catalyst"}]
            if command == "proposal":
                return {"proposal_id": "01PROP", "status": "pending", "proposal_type": "MethodologyTemplatePatch", "title": "Promote policy catalyst"}
            if command == "review-proposal":
                return {"proposal_id": "01PROP", "status": "approved", "proposal_type": "MethodologyTemplatePatch", "title": "Promote policy catalyst"}
            raise AssertionError(command)

        rendered: list[object] = []

        handled = handle_chat_cli_action(
            action="proposals",
            parsed={"action": "proposals", "limit": 10, "status": "pending"},
            state=session.state,
            settings=session.settings,
            emit_text=lambda output_writer, update: apply_tui_update(session, update),
            cli_command_runner=fake_cli_command_runner,
            payload_printer=lambda payload, as_json=False, output_writer=None: rendered.append(payload),
            output_writer=None,
        )
        self.assertTrue(handled)

        handled = handle_chat_cli_action(
            action="proposal",
            parsed={"action": "proposal", "proposal_id": "01PROP"},
            state=session.state,
            settings=session.settings,
            emit_text=lambda output_writer, update: apply_tui_update(session, update),
            cli_command_runner=fake_cli_command_runner,
            payload_printer=lambda payload, as_json=False, output_writer=None: rendered.append(payload),
            output_writer=None,
        )
        self.assertTrue(handled)

        handled = handle_chat_cli_action(
            action="review-proposal",
            parsed={"action": "review-proposal", "proposal_id": "01PROP", "decision": "approve", "reason": "looks good"},
            state=session.state,
            settings=session.settings,
            emit_text=lambda output_writer, update: apply_tui_update(session, update),
            cli_command_runner=fake_cli_command_runner,
            payload_printer=lambda payload, as_json=False, output_writer=None: rendered.append(payload),
            output_writer=None,
        )
        self.assertTrue(handled)

        self.assertEqual(calls[0]["command"], "proposals")
        self.assertEqual(calls[0]["status_filter"], "pending")
        self.assertEqual(calls[1]["command"], "proposal")
        self.assertEqual(calls[1]["run_id"], "01PROP")
        self.assertEqual(calls[2]["command"], "review-proposal")
        self.assertEqual(calls[2]["run_id"], "01PROP")
        self.assertEqual(calls[2]["query"], "approve")
        self.assertEqual(calls[2]["status_filter"], "looks good")
        self.assertEqual(len(rendered), 3)

    def test_handle_chat_cli_action_supports_subagent_discovery_commands(self) -> None:
        session = TuiSession(
            settings=Settings.from_env(Path(".")),
            state=ChatSessionState(),
            llm_provider=ProviderUnavailableStub(),  # type: ignore[arg-type]
            task_router=TaskRouter(),
            output_lines=[],
        )
        calls: list[dict] = []

        def fake_cli_command_runner(settings, *, command, **kwargs):
            calls.append({"command": command, **kwargs})
            if command == "subagent-discoveries":
                return [
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
            if command == "subagent-discovery":
                return {
                    "ticket_id": "01DISC",
                    "status": "approved",
                    "capability_gap": {"capability": "screening_strategy"},
                    "tool_discovery_request": {"search_query": "screening strategy resolver"},
                }
            if command == "review-subagent-discovery":
                return {"ticket_id": "01DISC", "status": "approved"}
            if command == "activate-subagent-discovery":
                return {"ticket_id": "01DISC", "status": "approved", "result": {"activated_entries": []}}
            raise AssertionError(command)

        rendered: list[object] = []

        for action, parsed in [
            ("subagent-discoveries", {"action": "subagent-discoveries", "limit": 10, "status": "approved"}),
            ("subagent-discovery", {"action": "subagent-discovery", "ticket_id": "01DISC"}),
            (
                "review-subagent-discovery",
                {
                    "action": "review-subagent-discovery",
                    "ticket_id": "01DISC",
                    "decision": "approve",
                    "reason": "validated",
                },
            ),
            (
                "activate-subagent-discovery",
                {
                    "action": "activate-subagent-discovery",
                    "ticket_id": "01DISC",
                    "delegate_subagent_type": "stock_snapshot_collector",
                    "candidate_subagent_type": "candidate_screening_strategy",
                },
            ),
            (
                "activate-subagent-discovery",
                {
                    "action": "activate-subagent-discovery",
                    "ticket_id": "01DISC",
                    "delegate_subagent_type": None,
                    "candidate_subagent_type": "candidate_collect_http_extension",
                    "registration_type": "http_json_runner",
                    "skill_name": None,
                    "snapshot_type": None,
                    "source_id": "eastmoney",
                    "tool_name": None,
                    "endpoint_path": "/api/market-signal",
                    "http_method": "GET",
                    "openapi_spec_uri": None,
                    "openapi_operation_id": None,
                    "timeout_seconds": 9.5,
                },
            ),
            (
                "activate-subagent-discovery",
                {
                    "action": "activate-subagent-discovery",
                    "ticket_id": "01DISC",
                    "delegate_subagent_type": None,
                    "candidate_subagent_type": "candidate_collect_http_extension",
                    "registration_type": "openapi_http_runner",
                    "skill_name": None,
                    "snapshot_type": None,
                    "source_id": "eastmoney",
                    "tool_name": None,
                    "endpoint_path": None,
                    "http_method": None,
                    "openapi_spec_uri": "https://example.com/openapi.json",
                    "openapi_operation_id": "getMarketSignal",
                    "timeout_seconds": None,
                },
            ),
            (
                "activate-subagent-discovery",
                {
                    "action": "activate-subagent-discovery",
                    "ticket_id": "01DISC",
                    "delegate_subagent_type": None,
                    "candidate_subagent_type": "candidate_collect_mcp_extension",
                    "registration_type": "mcp_tool_runner",
                    "skill_name": None,
                    "snapshot_type": None,
                    "source_id": "akshare_mcp",
                    "tool_name": "market_signal_lookup",
                    "endpoint_path": None,
                    "http_method": None,
                    "openapi_spec_uri": None,
                    "openapi_operation_id": None,
                    "timeout_seconds": 7.5,
                },
            ),
            (
                "activate-subagent-discovery",
                {
                    "action": "activate-subagent-discovery",
                    "ticket_id": "01DISC",
                    "delegate_subagent_type": None,
                    "candidate_subagent_type": "candidate_collect_http_extension",
                    "registration_template_id": "openapi:getMarketSignal",
                    "registration_type": None,
                    "skill_name": None,
                    "snapshot_type": None,
                    "source_id": None,
                    "tool_name": None,
                    "endpoint_path": None,
                    "http_method": None,
                    "openapi_spec_uri": None,
                    "openapi_operation_id": None,
                    "timeout_seconds": None,
                },
            ),
        ]:
            handled = handle_chat_cli_action(
                action=action,
                parsed=parsed,
                state=session.state,
                settings=session.settings,
                emit_text=lambda output_writer, update: apply_tui_update(session, update),
                cli_command_runner=fake_cli_command_runner,
                payload_printer=lambda payload, as_json=False, output_writer=None: rendered.append(payload),
                output_writer=None,
            )
            self.assertTrue(handled)

        self.assertEqual(calls[0]["command"], "subagent-discoveries")
        self.assertEqual(calls[0]["status_filter"], "approved")
        self.assertEqual(calls[1]["command"], "subagent-discovery")
        self.assertEqual(calls[1]["ticket_id"], "01DISC")
        self.assertEqual(calls[2]["command"], "review-subagent-discovery")
        self.assertEqual(calls[2]["query"], "approve")
        self.assertEqual(calls[2]["ticket_id"], "01DISC")
        self.assertEqual(calls[3]["command"], "activate-subagent-discovery")
        self.assertEqual(calls[3]["query"], "stock_snapshot_collector")
        self.assertEqual(calls[3]["subagent_node_id"], "candidate_screening_strategy")
        self.assertEqual(calls[4]["command"], "activate-subagent-discovery")
        self.assertEqual(calls[4]["registration_type"], "http_json_runner")
        self.assertEqual(calls[4]["source_id"], "eastmoney")
        self.assertEqual(calls[4]["endpoint_path"], "/api/market-signal")
        self.assertEqual(calls[4]["http_method"], "GET")
        self.assertEqual(calls[4]["timeout_seconds"], 9.5)
        self.assertEqual(calls[5]["command"], "activate-subagent-discovery")
        self.assertEqual(calls[5]["registration_type"], "openapi_http_runner")
        self.assertEqual(calls[5]["openapi_spec_uri"], "https://example.com/openapi.json")
        self.assertEqual(calls[5]["openapi_operation_id"], "getMarketSignal")
        self.assertEqual(calls[6]["command"], "activate-subagent-discovery")
        self.assertEqual(calls[6]["registration_type"], "mcp_tool_runner")
        self.assertEqual(calls[6]["source_id"], "akshare_mcp")
        self.assertEqual(calls[6]["tool_name"], "market_signal_lookup")
        self.assertEqual(calls[6]["timeout_seconds"], 7.5)
        self.assertEqual(calls[7]["command"], "activate-subagent-discovery")
        self.assertEqual(calls[7]["registration_template_id"], "openapi:getMarketSignal")
        self.assertEqual(calls[7]["subagent_node_id"], "candidate_collect_http_extension")
        self.assertEqual(len(rendered), 8)


if __name__ == "__main__":
    unittest.main()
