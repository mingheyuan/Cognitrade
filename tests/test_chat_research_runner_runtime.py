from __future__ import annotations

import unittest

from research_system.cli.flow.chat_research_runner import run_chat_research as flow_run_chat_research


class ChatResearchRunnerRuntimeTests(unittest.TestCase):
    def test_flow_chat_research_runner_is_callable(self) -> None:
        self.assertTrue(callable(flow_run_chat_research))


if __name__ == "__main__":
    unittest.main()
