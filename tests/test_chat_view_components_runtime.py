from __future__ import annotations

import unittest

from research_system.cli.view.chat_output_area import TranscriptOutputArea
from research_system.cli.view.chat_view_components import (
    ChatViewComponents,
    TranscriptOutputArea as ComponentsTranscriptOutputArea,
    build_layout_children,
)
from research_system.cli.view.chat_view_layout_support import build_layout_children as support_build_layout_children
from research_system.cli.view.chat_view_models import ChatViewComponents as SupportChatViewComponents


class ChatViewComponentsRuntimeTests(unittest.TestCase):
    def test_view_components_package_exports_split_supports(self) -> None:
        self.assertIsNotNone(TranscriptOutputArea)
        self.assertIsNotNone(ComponentsTranscriptOutputArea)
        self.assertIsNotNone(ChatViewComponents)
        self.assertIsNotNone(SupportChatViewComponents)
        self.assertIsNotNone(build_layout_children)
        self.assertIsNotNone(support_build_layout_children)
        self.assertIs(TranscriptOutputArea, ComponentsTranscriptOutputArea)


if __name__ == "__main__":
    unittest.main()
