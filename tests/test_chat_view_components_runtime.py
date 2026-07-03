from __future__ import annotations

import unittest

from research_system.cli.chat_output_area import TranscriptOutputArea
from research_system.cli.chat_view_components import (
    ChatViewComponents,
    TranscriptOutputArea as FacadeTranscriptOutputArea,
    build_layout_children,
)
from research_system.cli.chat_view_layout_support import build_layout_children as support_build_layout_children
from research_system.cli.chat_view_models import ChatViewComponents as SupportChatViewComponents


class ChatViewComponentsRuntimeTests(unittest.TestCase):
    def test_view_components_facade_exports_split_supports(self) -> None:
        self.assertIsNotNone(TranscriptOutputArea)
        self.assertIsNotNone(FacadeTranscriptOutputArea)
        self.assertIsNotNone(ChatViewComponents)
        self.assertIsNotNone(SupportChatViewComponents)
        self.assertIsNotNone(build_layout_children)
        self.assertIsNotNone(support_build_layout_children)


if __name__ == "__main__":
    unittest.main()
