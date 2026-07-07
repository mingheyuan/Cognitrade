from __future__ import annotations

import unittest

from research_system.cli.flow.chat_controller import handle_chat_line
from research_system.cli.flow.chat_dispatch import answer_basic_chat, decide_chat_dispatch, print_streamed_basic_chat_answer
from research_system.cli.flow.chat_result import print_chat_result_payload
from research_system.cli.flow.chat_controller import handle_chat_line as flow_handle_chat_line
from research_system.cli.flow.chat_dispatch import (
    answer_basic_chat as flow_answer_basic_chat,
    decide_chat_dispatch as flow_decide_chat_dispatch,
    print_streamed_basic_chat_answer as flow_print_streamed_basic_chat_answer,
)
from research_system.cli.flow.chat_result import (
    print_chat_result_payload as flow_print_chat_result_payload,
)


class ChatFlowRuntimeTests(unittest.TestCase):
    def test_chat_flow_package_exports_remain_consistent(self) -> None:
        self.assertIs(handle_chat_line, flow_handle_chat_line)
        self.assertIs(answer_basic_chat, flow_answer_basic_chat)
        self.assertIs(decide_chat_dispatch, flow_decide_chat_dispatch)
        self.assertIs(print_streamed_basic_chat_answer, flow_print_streamed_basic_chat_answer)
        self.assertIs(print_chat_result_payload, flow_print_chat_result_payload)


if __name__ == "__main__":
    unittest.main()
