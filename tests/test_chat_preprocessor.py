from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.cli.chat_preprocessor import prepare_chat_run_request
from research_system.cli.chat_types import ChatSessionState
from research_system.runtime.settings import Settings, ensure_workspace_layout


class ChatPreprocessorTests(unittest.TestCase):
    def test_prepare_chat_run_request_returns_memory_reply_for_recent_topic_query(self) -> None:
        settings = Settings.from_env(Path("."))
        state = ChatSessionState(
            last_user_query="分析茅台",
            last_result_intent="stock_check",
            last_result_summary="估值不便宜",
            last_run_id="01LAST",
        )

        prepared = prepare_chat_run_request(
            parsed={"action": "run", "query": "what were we just talking about?"},
            state=state,
            settings=settings,
            conversation_context=[],
        )

        self.assertIsNotNone(prepared.memory_reply)
        self.assertIn("分析茅台", prepared.memory_reply or "")

    def test_prepare_chat_run_request_resolves_follow_up_compare_context(self) -> None:
        settings = Settings.from_env(Path("."))
        state = ChatSessionState(
            last_user_query="compare guizhou maotai, ningde shidai, and byd. who is more worth investing in?",
            last_result_intent="stock_compare",
            last_run_id="01COMPARE",
        )

        prepared = prepare_chat_run_request(
            parsed={"action": "run", "query": "who is more resilient?"},
            state=state,
            settings=settings,
            conversation_context=[],
        )

        self.assertEqual(prepared.intent, "stock_compare")
        self.assertIsNone(prepared.run_id)
        self.assertIn("compare guizhou maotai, ningde shidai, and byd", prepared.query)

    def test_prepare_chat_run_request_rebuilds_context_after_follow_up_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(Path(temp_dir))
            ensure_workspace_layout(settings)
            state = ChatSessionState(
                last_user_query="compare guizhou maotai, ningde shidai, and byd",
                last_result_intent="stock_compare",
                last_result_summary="cashflow resilience still needs deeper comparison",
                last_run_id="01COMPARE",
            )
            from research_system.cli.chat_memory import remember_basic_chat_turn

            for idx in range(9):
                remember_basic_chat_turn(
                    settings=settings,
                    state=state,
                    query=f"question {idx} about guizhou maotai cashflow resilience",
                    answer=f"answer {idx} about cashflow resilience",
                )

            prepared = prepare_chat_run_request(
                parsed={"action": "run", "query": "who is more resilient?"},
                state=state,
                settings=settings,
                conversation_context=[],
            )

            self.assertEqual(prepared.intent, "stock_compare")
            self.assertTrue(any(item.get("type", "").startswith("relevant_") for item in prepared.conversation_context))


if __name__ == "__main__":
    unittest.main()
