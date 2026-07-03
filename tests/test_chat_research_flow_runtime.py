from __future__ import annotations

import unittest

from research_system.cli.chat_event_adapter import (
    build_chat_presenter,
    chat_event_listener,
    display_chunk_to_update,
    event_to_display_chunks,
)
from research_system.cli.chat_research_runner import run_chat_research
from research_system.cli.flow.chat_event_adapter import (
    build_chat_presenter as flow_build_chat_presenter,
    chat_event_listener as flow_chat_event_listener,
    display_chunk_to_update as flow_display_chunk_to_update,
    event_to_display_chunks as flow_event_to_display_chunks,
)
from research_system.cli.flow.chat_research_runner import (
    run_chat_research as flow_run_chat_research,
)
from research_system.cli.runtime.chat_research_runner import (
    run_chat_research as runtime_run_chat_research,
)


class ChatResearchFlowRuntimeTests(unittest.TestCase):
    def test_top_level_chat_research_exports_remain_compatible_with_flow_package(self) -> None:
        self.assertIs(build_chat_presenter, flow_build_chat_presenter)
        self.assertIs(chat_event_listener, flow_chat_event_listener)
        self.assertIs(display_chunk_to_update, flow_display_chunk_to_update)
        self.assertIs(event_to_display_chunks, flow_event_to_display_chunks)
        self.assertIs(runtime_run_chat_research, flow_run_chat_research)
        self.assertEqual(run_chat_research.__name__, flow_run_chat_research.__name__)


if __name__ == "__main__":
    unittest.main()
