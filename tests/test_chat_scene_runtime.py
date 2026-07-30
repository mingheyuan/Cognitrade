from __future__ import annotations

import unittest

from research_system.cli.view.chat_scene import (
    _build_focused_thread_family_lines,
    _build_thread_lineage_tree_entries,
    _build_thread_lineage_tree_lines,
    _idle_transcript_context_text,
    build_commands_popup_view,
    build_output_blocks,
    build_threads_blocks,
    commands_popup_text,
)
from research_system.cli.view.chat_scene_context_support import idle_transcript_context_text
from research_system.cli.view.chat_scene_output_support import build_output_blocks as support_build_output_blocks
from research_system.cli.view.chat_scene_popup_support import (
    build_commands_popup_view as support_build_commands_popup_view,
    commands_popup_text as support_commands_popup_text,
)
from research_system.cli.view.chat_scene_thread_support import (
    build_focused_thread_family_lines,
    build_thread_lineage_tree_entries,
    build_thread_lineage_tree_lines,
    build_threads_blocks as support_build_threads_blocks,
)


class ChatSceneRuntimeTests(unittest.TestCase):
    def test_scene_package_exports_split_supports(self) -> None:
        self.assertIsNotNone(commands_popup_text)
        self.assertIsNotNone(build_commands_popup_view)
        self.assertIsNotNone(build_threads_blocks)
        self.assertIsNotNone(_build_thread_lineage_tree_entries)
        self.assertIsNotNone(_build_focused_thread_family_lines)
        self.assertIsNotNone(_build_thread_lineage_tree_lines)
        self.assertIsNotNone(build_output_blocks)
        self.assertIsNotNone(_idle_transcript_context_text)
        self.assertIsNotNone(support_commands_popup_text)
        self.assertIsNotNone(support_build_commands_popup_view)
        self.assertIsNotNone(support_build_threads_blocks)
        self.assertIsNotNone(build_thread_lineage_tree_entries)
        self.assertIsNotNone(build_focused_thread_family_lines)
        self.assertIsNotNone(build_thread_lineage_tree_lines)
        self.assertIsNotNone(support_build_output_blocks)
        self.assertIsNotNone(idle_transcript_context_text)


if __name__ == "__main__":
    unittest.main()
