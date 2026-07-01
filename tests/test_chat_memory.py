from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.cli.chat_memory import (
    archive_session_thread,
    build_chat_conversation_context,
    build_relevant_memory_context,
    compact_session_thread,
    current_thread_summary,
    fork_session_thread,
    list_chat_threads,
    remember_basic_chat_turn,
    unarchive_session_thread,
)
from research_system.cli.chat_thread_store import ChatThreadStore
from research_system.cli.chat_types import ChatSessionState
from research_system.runtime.settings import Settings, ensure_workspace_layout


class ChatMemoryTests(unittest.TestCase):
    def test_build_chat_conversation_context_combines_thread_and_recent_research(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            state = ChatSessionState(
                last_user_query="analyze guizhou maotai",
                last_result_intent="stock_check",
                last_result_summary="valuation is not cheap but cashflow is resilient",
                last_run_id="01LAST",
            )
            remember_basic_chat_turn(settings=settings, state=state, query="hello", answer="I am Cognitrade")

            context = build_chat_conversation_context(settings=settings, state=state)

            self.assertTrue(any(item.get("type") == "chat_thread_turn" for item in context))
            self.assertTrue(any(item.get("type") == "recent_research_memory" for item in context))

    def test_compact_session_thread_adds_thread_summary_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            state = ChatSessionState(
                last_user_query="compare guizhou maotai, ningde shidai, and byd",
                last_result_intent="stock_compare",
                last_result_summary="current ranking is inconclusive",
                last_run_id="01COMPARE",
            )
            remember_basic_chat_turn(
                settings=settings,
                state=state,
                query="continue with cashflow",
                answer="compare cashflow resilience and valuation spread",
            )

            compact_session_thread(settings=settings, state=state)
            context = build_chat_conversation_context(settings=settings, state=state)

            self.assertTrue(any(item.get("type") == "chat_thread_summary" for item in context))
            summary_item = next(item for item in context if item.get("type") == "chat_thread_summary")
            self.assertIn("recent_query: compare guizhou maotai, ningde shidai, and byd", summary_item.get("summary") or "")

    def test_auto_compact_thread_replaces_old_turns_after_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            state = ChatSessionState(
                last_user_query="analyze guizhou maotai",
                last_result_intent="stock_check",
                last_result_summary="valuation is not cheap but cashflow is resilient",
                last_run_id="01LAST",
            )

            for idx in range(9):
                remember_basic_chat_turn(settings=settings, state=state, query=f"question {idx}", answer=f"answer {idx}")

            thread_id = state.thread_id
            self.assertIsNotNone(thread_id)
            assert thread_id is not None
            store = ChatThreadStore(settings)
            turns = store.read_all_turns(thread_id=thread_id)
            summary_payload = store.load_thread_summary(thread_id=thread_id)
            archived_turns = store.read_archived_turns(thread_id=thread_id)

            self.assertLessEqual(len(turns), 8)
            self.assertIsInstance(summary_payload, dict)
            self.assertIn("recent_query:", str((summary_payload or {}).get("summary_text") or ""))
            self.assertTrue(archived_turns)

    def test_repeated_compaction_builds_summary_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            state = ChatSessionState(
                last_user_query="analyze guizhou maotai",
                last_result_intent="stock_check",
                last_result_summary="valuation is not cheap but cashflow is resilient",
                last_run_id="01LAST",
            )

            for idx in range(9):
                remember_basic_chat_turn(settings=settings, state=state, query=f"question {idx}", answer=f"answer {idx}")
            for idx in range(9, 18):
                remember_basic_chat_turn(settings=settings, state=state, query=f"question {idx}", answer=f"answer {idx}")

            thread_id = state.thread_id
            self.assertIsNotNone(thread_id)
            assert thread_id is not None
            store = ChatThreadStore(settings)
            summary_rows = store.read_recent_summaries(thread_id=thread_id, limit=5)
            context = build_chat_conversation_context(settings=settings, state=state)

            self.assertGreaterEqual(len(summary_rows), 2)
            summary_contexts = [item for item in context if item.get("type") == "chat_thread_summary"]
            self.assertGreaterEqual(len(summary_contexts), 2)

    def test_repeated_compaction_tracks_incremental_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            state = ChatSessionState(
                last_user_query="analyze guizhou maotai",
                last_result_intent="stock_check",
                last_result_summary="valuation is not cheap but cashflow is resilient",
                last_run_id="01LAST",
            )

            for idx in range(9):
                remember_basic_chat_turn(settings=settings, state=state, query=f"question {idx}", answer=f"answer {idx}")
            first = compact_session_thread(settings=settings, state=state)
            for idx in range(9, 12):
                remember_basic_chat_turn(settings=settings, state=state, query=f"question {idx}", answer=f"answer {idx}")
            second = compact_session_thread(settings=settings, state=state)
            store = ChatThreadStore(settings)
            remaining_turns = store.read_all_turns(thread_id=state.thread_id or "")

            self.assertEqual(first["covered_turn_count"], 18)
            self.assertEqual(second["covered_turn_count"], 24)
            self.assertEqual(second["delta_turn_count"], 6)
            self.assertIn("covered_turns_before:", second["summary_text"])
            self.assertEqual(remaining_turns[-1]["turn_seq"], 24)

    def test_build_relevant_memory_context_finds_matching_archived_and_summary_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            state = ChatSessionState(
                last_user_query="compare guizhou maotai, ningde shidai, and byd",
                last_result_intent="stock_compare",
                last_result_summary="cashflow resilience still needs deeper comparison",
                last_run_id="01COMPARE",
            )

            for idx in range(9):
                remember_basic_chat_turn(
                    settings=settings,
                    state=state,
                    query=f"question {idx} about guizhou maotai cashflow resilience",
                    answer=f"answer {idx} about cashflow resilience",
                )

            relevant = build_relevant_memory_context(
                settings=settings,
                state=state,
                query="who is more resilient on cashflow, guizhou maotai or byd?",
            )

            self.assertTrue(relevant)
            self.assertTrue(any(item.get("type") in {"relevant_thread_summary", "relevant_thread_archive"} for item in relevant))
            self.assertTrue(any("cashflow" in " ".join(item.get("matched_terms") or []).lower() for item in relevant))

    def test_current_thread_summary_and_thread_list_include_topic_title(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            state = ChatSessionState(
                last_user_query="compare guizhou maotai, ningde shidai, and byd",
                last_result_intent="stock_compare",
                last_result_summary="cashflow resilience still needs deeper comparison",
                last_run_id="01COMPARE",
            )
            for idx in range(9):
                remember_basic_chat_turn(settings=settings, state=state, query=f"question {idx}", answer=f"answer {idx}")
            compact_session_thread(settings=settings, state=state)

            summary = current_thread_summary(settings=settings, state=state)
            rows = list_chat_threads(settings=settings, limit=5)

            self.assertEqual(summary["topic_title"], "compare guizhou maotai, ningde shidai, and byd")
            self.assertTrue(rows)
            self.assertEqual(rows[0]["topic_title"], "compare guizhou maotai, ningde shidai, and byd")

    def test_build_chat_conversation_context_includes_thread_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            state = ChatSessionState(
                last_user_query="compare guizhou maotai, ningde shidai, and byd",
                last_result_intent="stock_compare",
                last_result_summary="cashflow resilience still needs deeper comparison",
                last_result_follow_up="supplement same-basis cashflow and valuation assumptions",
                last_run_id="01COMPARE",
            )
            for idx in range(9):
                remember_basic_chat_turn(settings=settings, state=state, query=f"question {idx}", answer=f"answer {idx}")
            compact_session_thread(settings=settings, state=state)

            context = build_chat_conversation_context(settings=settings, state=state, query="who is more resilient?")

            profile = next(item for item in context if item.get("type") == "chat_thread_profile")
            self.assertEqual(profile["topic_title"], "compare guizhou maotai, ningde shidai, and byd")
            self.assertEqual(profile["topic_intent"], "stock_compare")
            self.assertEqual(profile["recommended_next_step"], "supplement same-basis cashflow and valuation assumptions")

    def test_thread_profile_exposes_open_question_and_follow_up_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            state = ChatSessionState(
                last_user_query="compare guizhou maotai, ningde shidai, and byd",
                last_result_intent="stock_compare",
                last_result_summary="ranking is still inconclusive",
                last_result_follow_up="supplement same-basis cashflow and valuation assumptions",
                last_run_id="01COMPARE",
                pending_clarification_question="Do you want medium-term or long-term comparison?",
            )
            remember_basic_chat_turn(
                settings=settings,
                state=state,
                query="who is more resilient?",
                answer="current ranking is inconclusive",
            )

            summary = current_thread_summary(settings=settings, state=state)
            context = build_chat_conversation_context(settings=settings, state=state, query="who is more resilient?")

            self.assertEqual(summary["thread_status"], "clarification_pending")
            self.assertEqual(summary["open_question"], "Do you want medium-term or long-term comparison?")
            self.assertEqual(summary["recommended_next_step"], "supplement same-basis cashflow and valuation assumptions")
            profile = next(item for item in context if item.get("type") == "chat_thread_profile")
            self.assertEqual(profile["open_question"], "Do you want medium-term or long-term comparison?")
            self.assertEqual(profile["recommended_next_step"], "supplement same-basis cashflow and valuation assumptions")

    def test_relevant_memory_context_uses_cross_thread_chat_memory_search(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            state = ChatSessionState(
                thread_id="active-thread",
                last_user_query="compare guizhou maotai and catl",
                last_result_intent="stock_compare",
                last_result_summary="same-thread summary",
                last_run_id="01ACTIVE",
            )

            from research_system.services.memory_service import MemoryService
            from research_system.adapters.storage.mem0_adapter import Mem0Adapter

            service = MemoryService(Mem0Adapter(settings))
            service.write_chat_research_memory(
                query="compare guizhou maotai cashflow resilience",
                intent="stock_compare",
                run_id="01OTHER",
                summary="other-thread summary",
                conclusion="other-thread conclusion",
                follow_up="other-thread next step",
                status="completed",
                thread_id="archived-thread",
            )
            service.write_chat_research_memory(
                query="compare same-thread cashflow resilience",
                intent="stock_compare",
                run_id="01SAME",
                summary="same-thread archived summary",
                conclusion="same-thread archived conclusion",
                follow_up="same-thread archived next step",
                status="completed",
                thread_id="active-thread",
            )

            relevant = build_relevant_memory_context(
                settings=settings,
                state=state,
                query="who has stronger cashflow resilience?",
            )

            cross_thread = [item for item in relevant if item.get("type") == "relevant_chat_memory"]
            self.assertTrue(cross_thread)
            self.assertTrue(all(item.get("thread_id") != "active-thread" for item in cross_thread))
            self.assertEqual(cross_thread[0]["thread_id"], "archived-thread")
            self.assertEqual(cross_thread[0]["memory_scope"], "chat_research")
            self.assertTrue(str(cross_thread[0]["snapshot_id"]).startswith("chat_snapshot:"))

    def test_fork_session_thread_copies_turns_and_exposes_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            state = ChatSessionState(
                last_user_query="compare guizhou maotai and byd",
                last_result_intent="stock_compare",
                last_result_summary="ranking is still inconclusive",
                last_result_follow_up="supplement same-basis valuation assumptions",
                last_run_id="01COMPARE",
            )
            remember_basic_chat_turn(settings=settings, state=state, query="question 1", answer="answer 1")
            remember_basic_chat_turn(settings=settings, state=state, query="question 2", answer="answer 2")
            parent_thread_id = state.thread_id
            self.assertIsNotNone(parent_thread_id)
            compact_session_thread(settings=settings, state=state)

            child_thread_id = fork_session_thread(settings=settings, state=state)
            self.assertNotEqual(child_thread_id, parent_thread_id)

            store = ChatThreadStore(settings)
            child_turns = store.read_all_turns(thread_id=child_thread_id)
            child_metadata = store.load_thread_metadata(thread_id=child_thread_id) or {}
            summary = current_thread_summary(settings=settings, state=state)
            context = build_chat_conversation_context(settings=settings, state=state, query="continue compare")
            profile = next(item for item in context if item.get("type") == "chat_thread_profile")
            rows = list_chat_threads(settings=settings, limit=10)
            child_row = next(row for row in rows if row.get("thread_id") == child_thread_id)

            self.assertEqual(len(child_turns), 4)
            self.assertEqual(child_turns[0]["thread_id"], child_thread_id)
            self.assertEqual(child_metadata["parent_thread_id"], parent_thread_id)
            self.assertEqual(child_metadata["thread_source"], "fork")
            self.assertEqual(summary["thread_source"], "fork")
            self.assertEqual(summary["parent_thread_id"], parent_thread_id)
            self.assertEqual(profile["thread_source"], "fork")
            self.assertEqual(profile["parent_thread_id"], parent_thread_id)
            self.assertEqual(child_row["thread_source"], "fork")
            self.assertEqual(child_row["parent_thread_id"], parent_thread_id)

    def test_archive_session_thread_hides_thread_from_active_list_and_exposes_archived_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            state = ChatSessionState(
                last_user_query="compare banks",
                last_result_intent="stock_compare",
                last_result_summary="ranking is still inconclusive",
                last_run_id="01COMPARE",
            )
            remember_basic_chat_turn(settings=settings, state=state, query="question", answer="answer")
            archived_thread_id = state.thread_id
            self.assertIsNotNone(archived_thread_id)

            payload = archive_session_thread(settings=settings, state=state)

            self.assertEqual(payload["thread_id"], archived_thread_id)
            self.assertNotEqual(state.thread_id, archived_thread_id)
            active_rows = list_chat_threads(settings=settings, limit=10)
            archived_rows = list_chat_threads(settings=settings, limit=10, scope="archived")
            all_rows = list_chat_threads(settings=settings, limit=10, scope="all")
            archived_summary = current_thread_summary(
                settings=settings,
                state=ChatSessionState(thread_id=archived_thread_id),
            )

            self.assertTrue(all(row.get("thread_id") != archived_thread_id for row in active_rows))
            self.assertTrue(any(row.get("thread_id") == archived_thread_id for row in archived_rows))
            self.assertTrue(any(row.get("thread_id") == archived_thread_id for row in all_rows))
            self.assertTrue(archived_summary["archived"])
            self.assertIsNotNone(archived_summary["archived_at"])

    def test_unarchive_session_thread_restores_thread_to_active_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            state = ChatSessionState(
                last_user_query="compare banks",
                last_result_intent="stock_compare",
                last_result_summary="ranking is still inconclusive",
                last_run_id="01COMPARE",
            )
            remember_basic_chat_turn(settings=settings, state=state, query="question", answer="answer")
            archived_thread_id = state.thread_id
            self.assertIsNotNone(archived_thread_id)
            archive_session_thread(settings=settings, state=state)

            payload = unarchive_session_thread(settings=settings, thread_id=str(archived_thread_id))

            self.assertEqual(payload["thread_id"], archived_thread_id)
            active_rows = list_chat_threads(settings=settings, limit=10)
            archived_rows = list_chat_threads(settings=settings, limit=10, scope="archived")
            summary = current_thread_summary(
                settings=settings,
                state=ChatSessionState(thread_id=str(archived_thread_id)),
            )

            self.assertTrue(any(row.get("thread_id") == archived_thread_id for row in active_rows))
            self.assertTrue(all(row.get("thread_id") != archived_thread_id for row in archived_rows))
            self.assertFalse(summary["archived"])


if __name__ == "__main__":
    unittest.main()
