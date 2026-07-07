from __future__ import annotations

import unittest

from research_system.cli.view.chat_view_focus_sync import sync_focus_target
from research_system.cli.view.chat_view_layout_sync import sync_main_layout
from research_system.cli.view.chat_view_sync_signature import build_scene_sync_signature
from research_system.cli.view.chat_view_sync_support import sync_chat_view_from_scene
from research_system.cli.view.chat_view_widget_sync import sync_auxiliary_widgets, sync_input_widget


class ChatViewSyncRuntimeTests(unittest.TestCase):
    def test_view_sync_facade_exports_split_supports(self) -> None:
        self.assertIsNotNone(sync_chat_view_from_scene)
        self.assertIsNotNone(build_scene_sync_signature)
        self.assertIsNotNone(sync_main_layout)
        self.assertIsNotNone(sync_input_widget)
        self.assertIsNotNone(sync_auxiliary_widgets)
        self.assertIsNotNone(sync_focus_target)


if __name__ == "__main__":
    unittest.main()
