from __future__ import annotations

import unittest

from research_system.cli.app import (
    ChatSessionState,
    _chat_completion_candidates,
    _chat_completion_current_token,
    _merge_pending_clarification,
    _parse_chat_input,
    _resolve_chat_run_id,
    _should_resume_pending_clarification,
)


class ChatCliTests(unittest.TestCase):
    def test_plain_text_maps_to_run(self) -> None:
        parsed = _parse_chat_input("最近创新药板块怎么样")
        self.assertEqual(parsed, {"action": "run", "query": "最近创新药板块怎么样"})

    def test_resume_command_is_parsed(self) -> None:
        parsed = _parse_chat_input("/resume 01ABC")
        self.assertEqual(parsed, {"action": "resume", "run_id": "01ABC"})

    def test_review_command_sets_intent_and_run_id(self) -> None:
        parsed = _parse_chat_input('/review 01TARGET "复核上次创新药判断"')
        self.assertEqual(
            parsed,
            {
                "action": "run",
                "intent": "review_thesis",
                "run_id": "01TARGET",
                "query": "复核上次创新药判断",
            },
        )

    def test_resolve_last_run_id(self) -> None:
        state = ChatSessionState(last_run_id="01LAST")
        self.assertEqual(_resolve_chat_run_id("last", state), "01LAST")
        self.assertEqual(_resolve_chat_run_id("01ABC", state), "01ABC")

    def test_intent_auto_clears_default(self) -> None:
        parsed = _parse_chat_input("/intent auto")
        self.assertEqual(parsed, {"action": "set_intent", "intent": None})

    def test_thinking_command_is_parsed(self) -> None:
        parsed = _parse_chat_input("/thinking off")
        self.assertEqual(parsed, {"action": "set_thinking", "enabled": False})

    def test_events_command_is_parsed(self) -> None:
        parsed = _parse_chat_input("/events full")
        self.assertEqual(parsed, {"action": "set_events", "events_mode": "full"})

    def test_stop_command_is_parsed(self) -> None:
        parsed = _parse_chat_input("/stop")
        self.assertEqual(parsed, {"action": "stop"})

    def test_commands_command_is_parsed(self) -> None:
        parsed = _parse_chat_input("/commands")
        self.assertEqual(parsed, {"action": "commands"})

    def test_pending_clarification_can_resume_plain_follow_up(self) -> None:
        state = ChatSessionState(
            pending_clarification_query="最近有什么股票值得投入",
            pending_clarification_intent=None,
        )
        self.assertTrue(
            _should_resume_pending_clarification(
                query="中线",
                intent=None,
                state=state,
            )
        )

    def test_pending_clarification_merges_follow_up_into_query(self) -> None:
        state = ChatSessionState(
            pending_clarification_query="最近有什么股票值得投入",
            pending_clarification_intent="stock_screen",
            pending_clarification_run_id="01CLARIFY",
        )
        query, intent, run_id = _merge_pending_clarification(
            query="中线",
            intent=None,
            run_id=None,
            state=state,
        )
        self.assertIn("最近有什么股票值得投入", query)
        self.assertIn("补充信息: 中线", query)
        self.assertEqual(intent, "stock_screen")
        self.assertEqual(run_id, "01CLARIFY")

    def test_completion_current_token_uses_last_segment(self) -> None:
        self.assertEqual(_chat_completion_current_token("/eve"), "/eve")
        self.assertEqual(_chat_completion_current_token("/events fu"), "fu")
        self.assertEqual(_chat_completion_current_token("/events "), "")

    def test_completion_candidates_for_intent_command(self) -> None:
        candidates = _chat_completion_candidates("/intent ", ChatSessionState())
        self.assertIn("stock_check", candidates)
        self.assertIn("review_thesis", candidates)

    def test_completion_candidates_include_recent_run_ids(self) -> None:
        state = ChatSessionState(last_run_id="01LAST", review_target_run_id="01TARGET")
        candidates = _chat_completion_candidates("/resume ", state)
        self.assertEqual(candidates, ["last", "01LAST", "01TARGET"])


if __name__ == "__main__":
    unittest.main()
