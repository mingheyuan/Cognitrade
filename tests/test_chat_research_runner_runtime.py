from __future__ import annotations

import unittest

from research_system.cli.chat_research_runner import run_chat_research
from research_system.cli.flow.chat_research_runner import run_chat_research as flow_run_chat_research


class ChatResearchRunnerRuntimeTests(unittest.TestCase):
    def test_top_level_chat_research_runner_exports_flow_entrypoint(self) -> None:
        self.assertTrue(callable(run_chat_research))
        self.assertTrue(callable(flow_run_chat_research))


if __name__ == "__main__":
    unittest.main()
