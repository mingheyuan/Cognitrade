from __future__ import annotations

import unittest

from research_system.services.task_router import TaskRouter
from research_system.services.task_router_chat_support import TaskRouterChatSupport
from research_system.services.task_router_identify_support import TaskRouterIdentifySupport
from research_system.services.task_router_llm_support import TaskRouterLlmSupport
from research_system.services.task_router_rule_support import TaskRouterRuleSupport


class TaskRouterRuntimeTests(unittest.TestCase):
    def test_router_wires_rule_llm_identify_and_chat_supports(self) -> None:
        router = TaskRouter()

        self.assertIsInstance(router.rule_support, TaskRouterRuleSupport)
        self.assertIsInstance(router.llm_support, TaskRouterLlmSupport)
        self.assertIsInstance(router.identify_support, TaskRouterIdentifySupport)
        self.assertIsInstance(router.chat_support, TaskRouterChatSupport)
        self.assertIs(router.identify_support.rule_support, router.rule_support)
        self.assertIs(router.identify_support.llm_support, router.llm_support)
        self.assertIs(router.chat_support.rule_support, router.rule_support)
        self.assertIs(router.chat_support.llm_support, router.llm_support)


if __name__ == "__main__":
    unittest.main()
