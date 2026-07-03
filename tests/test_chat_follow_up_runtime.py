from __future__ import annotations

import unittest

from research_system.cli.chat_follow_up import FollowUpResolution
from research_system.cli.chat_follow_up_markers import looks_like_follow_up_research_query
from research_system.cli.chat_follow_up_memory_support import short_term_memory_reply
from research_system.cli.chat_types import ChatSessionState


class ChatFollowUpRuntimeTests(unittest.TestCase):
    def test_follow_up_runtime_exports_remain_usable(self) -> None:
        state = ChatSessionState(last_user_query="compare banks", last_result_intent="stock_compare")

        self.assertTrue(looks_like_follow_up_research_query("who is more resilient?"))
        self.assertIn("compare banks", short_term_memory_reply(state))
        self.assertEqual(
            FollowUpResolution(query="q", intent=None, run_id=None, resolved=False).resolved,
            False,
        )


if __name__ == "__main__":
    unittest.main()
