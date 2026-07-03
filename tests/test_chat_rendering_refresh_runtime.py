from __future__ import annotations

import unittest

from research_system.cli.chat_rendering_output_refresh import refresh_output_area
from research_system.cli.chat_rendering_refresh_support import (
    build_tui_hint_text,
    build_tui_status_text,
    commands_popup_text,
    refresh_tui,
    render_tui_output,
)
from research_system.cli.chat_rendering_status_support import (
    build_tui_hint_text as support_build_tui_hint_text,
    build_tui_status_text as support_build_tui_status_text,
)
from research_system.cli.chat_rendering_text_support import (
    commands_popup_text as support_commands_popup_text,
    render_tui_output as support_render_tui_output,
)


class ChatRenderingRefreshRuntimeTests(unittest.TestCase):
    def test_refresh_facade_exports_split_supports(self) -> None:
        self.assertIsNotNone(commands_popup_text)
        self.assertIsNotNone(render_tui_output)
        self.assertIsNotNone(build_tui_status_text)
        self.assertIsNotNone(build_tui_hint_text)
        self.assertIsNotNone(refresh_tui)
        self.assertIsNotNone(support_commands_popup_text)
        self.assertIsNotNone(support_render_tui_output)
        self.assertIsNotNone(support_build_tui_status_text)
        self.assertIsNotNone(support_build_tui_hint_text)
        self.assertIsNotNone(refresh_output_area)


if __name__ == "__main__":
    unittest.main()
