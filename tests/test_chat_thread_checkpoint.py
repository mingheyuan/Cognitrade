from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.cli.chat_memory import fork_session_thread, hydrate_session_from_persistent_memory, start_new_session_thread, switch_session_thread
from research_system.cli.chat_state import persist_thread_checkpoint
from research_system.cli.chat_thread_store import ChatThreadStore
from research_system.cli.chat_types import ChatSessionState
from research_system.runtime.settings import Settings, ensure_workspace_layout


class ChatThreadCheckpointTests(unittest.TestCase):
    def test_hydrate_session_from_thread_checkpoint_restores_recent_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            state = ChatSessionState(
                thread_id="thread_restore",
                current_intent="stock_compare",
                review_target_run_id="01TARGET",
                last_run_id="01RUN",
                last_user_query="compare maotai and catl",
                last_result_intent="stock_compare",
                last_result_summary="current ranking is still inconclusive",
                last_result_conclusion="continue comparing cashflow and valuation assumptions",
                last_result_follow_up="supplement same-basis cashflow and valuation assumptions",
            )
            persist_thread_checkpoint(settings=settings, state=state)

            restored = ChatSessionState(thread_id="thread_restore")
            hydrated = hydrate_session_from_persistent_memory(settings=settings, state=restored)

            self.assertTrue(hydrated)
            self.assertEqual(restored.current_intent, "stock_compare")
            self.assertEqual(restored.review_target_run_id, "01TARGET")
            self.assertEqual(restored.last_run_id, "01RUN")
            self.assertEqual(restored.last_user_query, "compare maotai and catl")
            self.assertEqual(restored.last_result_summary, "current ranking is still inconclusive")
            self.assertEqual(restored.last_result_conclusion, "continue comparing cashflow and valuation assumptions")
            self.assertEqual(restored.last_result_follow_up, "supplement same-basis cashflow and valuation assumptions")

    def test_persist_thread_checkpoint_writes_store_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            state = ChatSessionState(thread_id="thread_checkpoint", last_user_query="what were we just talking about")

            payload = persist_thread_checkpoint(settings=settings, state=state)
            stored = ChatThreadStore(settings).load_thread_checkpoint(thread_id="thread_checkpoint")

            self.assertEqual(payload["thread_id"], "thread_checkpoint")
            self.assertIsInstance(payload.get("session_state"), dict)
            self.assertEqual(stored["session_state"]["last_user_query"], "what were we just talking about")

    def test_switch_session_thread_clears_current_memory_and_hydrates_target_thread(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            state = ChatSessionState(
                thread_id="thread_old",
                current_intent="stock_screen",
                last_user_query="temporary query",
                last_result_summary="temporary summary",
            )
            persisted = ChatSessionState(
                thread_id="thread_saved",
                last_user_query="analyze guizhou maotai",
                last_result_intent="stock_check",
                last_result_summary="valuation is not cheap but cashflow is resilient",
                last_result_conclusion="continue watching valuation digestion",
                last_result_follow_up="check whether margins stabilize next quarter",
                last_run_id="01LATEST",
            )
            persist_thread_checkpoint(settings=settings, state=persisted)

            switched = switch_session_thread(settings=settings, state=state, thread_id="thread_saved")

            self.assertTrue(switched)
            self.assertEqual(state.thread_id, "thread_saved")
            self.assertEqual(state.last_user_query, "analyze guizhou maotai")
            self.assertEqual(state.last_result_intent, "stock_check")
            self.assertEqual(state.last_result_summary, "valuation is not cheap but cashflow is resilient")
            self.assertEqual(state.last_result_conclusion, "continue watching valuation digestion")
            self.assertEqual(state.last_result_follow_up, "check whether margins stabilize next quarter")
            self.assertEqual(state.last_run_id, "01LATEST")

    def test_start_new_session_thread_resets_recent_state_and_assigns_new_thread(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            state = ChatSessionState(
                thread_id="thread_old",
                current_intent="stock_screen",
                review_target_run_id="01TARGET",
                last_run_id="01LAST",
                last_user_query="temporary query",
                last_result_summary="temporary summary",
                last_result_follow_up="temporary next step",
            )

            new_thread_id = start_new_session_thread(settings=settings, state=state)

            self.assertEqual(state.thread_id, new_thread_id)
            self.assertNotEqual(new_thread_id, "thread_old")
            self.assertIsNone(state.last_run_id)
            self.assertIsNone(state.last_user_query)
            self.assertIsNone(state.last_result_summary)
            self.assertIsNone(state.last_result_follow_up)
            self.assertIsNone(state.current_intent)
            self.assertIsNone(state.review_target_run_id)

    def test_forked_thread_checkpoint_restores_lineage_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            parent = ChatSessionState(
                thread_id="thread_parent",
                current_intent="stock_compare",
                last_user_query="compare banks",
                last_result_intent="stock_compare",
                last_result_summary="parent summary",
            )
            persist_thread_checkpoint(settings=settings, state=parent)

            child = ChatSessionState(thread_id="thread_parent")
            forked_thread_id = fork_session_thread(settings=settings, state=child, source_thread_id="thread_parent")
            restored = ChatSessionState(thread_id=forked_thread_id)
            hydrated = hydrate_session_from_persistent_memory(settings=settings, state=restored)

            self.assertTrue(hydrated)
            self.assertEqual(restored.thread_id, forked_thread_id)
            self.assertEqual(restored.thread_source, "fork")
            self.assertEqual(restored.parent_thread_id, "thread_parent")


if __name__ == "__main__":
    unittest.main()
