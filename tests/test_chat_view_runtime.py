from __future__ import annotations

import unittest

from research_system.cli.chat_view import (
    build_chat_application,
    build_chat_view,
    build_layout_children,
    sync_chat_view_from_scene,
)
from research_system.cli.view.chat_view import (
    build_chat_application as package_build_chat_application,
    build_chat_view as package_build_chat_view,
    build_layout_children as package_build_layout_children,
    sync_chat_view_from_scene as package_sync_chat_view_from_scene,
)


class ChatViewRuntimeTests(unittest.TestCase):
    def test_top_level_chat_view_exports_match_view_package_exports(self) -> None:
        self.assertTrue(callable(build_chat_application))
        self.assertTrue(callable(build_chat_view))
        self.assertTrue(callable(build_layout_children))
        self.assertTrue(callable(sync_chat_view_from_scene))
        self.assertTrue(callable(package_build_chat_application))
        self.assertTrue(callable(package_build_chat_view))
        self.assertTrue(callable(package_build_layout_children))
        self.assertTrue(callable(package_sync_chat_view_from_scene))


if __name__ == "__main__":
    unittest.main()
