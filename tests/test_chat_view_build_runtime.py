from __future__ import annotations

import unittest

from research_system.cli.chat_view_application_support import (
    build_chat_application_layout,
    build_chat_application_style,
)
from research_system.cli.chat_view_build_support import build_chat_application, build_chat_view
from research_system.cli.chat_view_widget_factory import (
    build_commands_popup,
    build_header_bar,
    build_hint_bar,
    build_input_area,
    build_status_bar,
)


class ChatViewBuildRuntimeTests(unittest.TestCase):
    def test_view_build_facade_exports_split_supports(self) -> None:
        self.assertIsNotNone(build_chat_view)
        self.assertIsNotNone(build_chat_application)
        self.assertIsNotNone(build_header_bar)
        self.assertIsNotNone(build_commands_popup)
        self.assertIsNotNone(build_input_area)
        self.assertIsNotNone(build_status_bar)
        self.assertIsNotNone(build_hint_bar)
        self.assertIsNotNone(build_chat_application_layout)
        self.assertIsNotNone(build_chat_application_style)


if __name__ == "__main__":
    unittest.main()
