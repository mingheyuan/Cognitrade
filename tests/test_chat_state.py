from __future__ import annotations

import unittest

from research_system.cli.chat_commands import clear_pending_clarification
from research_system.cli.chat_state import (
    build_new_thread_session_patch,
    build_research_result_session_patch,
    build_show_last_session_patch,
)
from research_system.cli.chat_types import ChatSessionState


class ChatStateTests(unittest.TestCase):
    def test_build_research_result_session_patch_sets_review_target_for_follow_up_review(self) -> None:
        patch = build_research_result_session_patch(
            payload={
                "run_id": "01REVIEWRUN",
                "status": "completed",
                "intent": "review_thesis",
                "display_summary": "review complete",
                "result": {
                    "conclusion": "posterior review says thesis weakened",
                    "follow_up": "compare rejected claims with the original thesis assumptions",
                },
            },
            query="review the previous conclusion",
            effective_intent="review_thesis",
            review_target_run_id="01TARGET",
            pending_patch=clear_pending_clarification(),
        )

        self.assertEqual(patch.last_run_id, "01REVIEWRUN")
        self.assertEqual(patch.last_user_query, "review the previous conclusion")
        self.assertEqual(patch.last_result_intent, "review_thesis")
        self.assertEqual(patch.last_result_summary, "review complete")
        self.assertEqual(patch.last_result_conclusion, "posterior review says thesis weakened")
        self.assertEqual(patch.last_result_follow_up, "compare rejected claims with the original thesis assumptions")
        self.assertEqual(patch.review_target_run_id, "01TARGET")
        self.assertIsNone(patch.pending_clarification_query)
        self.assertIsNone(patch.pending_clarification_question)

    def test_build_new_thread_session_patch_clears_recent_state_and_defaults(self) -> None:
        patch = build_new_thread_session_patch(thread_id="thread_new")

        self.assertEqual(patch.thread_id, "thread_new")
        self.assertIsNone(patch.current_intent)
        self.assertIsNone(patch.review_target_run_id)
        self.assertIsNone(patch.last_run_id)
        self.assertIsNone(patch.last_user_query)
        self.assertIsNone(patch.last_result_summary)
        self.assertIsNone(patch.last_result_follow_up)
        self.assertIsNone(patch.pending_clarification_query)

    def test_build_show_last_session_patch_mirrors_current_state(self) -> None:
        state = ChatSessionState(
            current_intent="stock_screen",
            review_target_run_id="01TARGET",
            last_run_id="01LAST",
            thinking_enabled=False,
            events_mode="full",
            last_user_query="analyze guizhou maotai",
            last_result_intent="stock_check",
            last_result_summary="valuation is not cheap but cashflow is resilient",
            last_result_conclusion="continue watching valuation digestion",
            last_result_follow_up="check whether margins stabilize next quarter",
        )

        patch = build_show_last_session_patch(state)

        self.assertEqual(patch.last_run_id, "01LAST")
        self.assertEqual(patch.current_intent, "stock_screen")
        self.assertEqual(patch.review_target_run_id, "01TARGET")
        self.assertFalse(patch.thinking_enabled)
        self.assertEqual(patch.events_mode, "full")
        self.assertEqual(patch.last_user_query, "analyze guizhou maotai")
        self.assertEqual(patch.last_result_intent, "stock_check")
        self.assertEqual(patch.last_result_summary, "valuation is not cheap but cashflow is resilient")
        self.assertEqual(patch.last_result_conclusion, "continue watching valuation digestion")
        self.assertEqual(patch.last_result_follow_up, "check whether margins stabilize next quarter")


if __name__ == "__main__":
    unittest.main()
