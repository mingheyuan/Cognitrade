from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.cli.chat_actions import handle_chat_cli_action, handle_chat_meta_action
from research_system.cli.chat_rendering import apply_tui_update
from research_system.cli.chat_state import persist_thread_checkpoint
from research_system.cli.chat_types import ChatSessionState, TuiSession
from research_system.runtime.settings import Settings, ensure_workspace_layout
from research_system.services.task_router import TaskRouter


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

            from research_system.cli.chat_memory import remember_basic_chat_turn

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

            from research_system.cli.chat_memory import remember_basic_chat_turn

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
            self.assertIn("thread: thread_saved", meta_lines)

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

            from research_system.cli.chat_memory import remember_basic_chat_turn

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

            from research_system.cli.chat_memory import remember_basic_chat_turn

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

            from research_system.cli.chat_memory import archive_session_thread, remember_basic_chat_turn

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

            from research_system.cli.chat_memory import archive_session_thread, remember_basic_chat_turn

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


if __name__ == "__main__":
    unittest.main()
