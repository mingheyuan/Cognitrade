from __future__ import annotations

import unittest

from research_system.cli.chat_shell_scene import (
    build_header_bar_view,
    build_input_bar_view,
    build_popup_layout_view,
    build_status_bar_items,
    output_mode_label,
)
from research_system.cli.state.chat_types import ChatSessionState, TuiSession


class ChatShellSceneRuntimeTests(unittest.TestCase):
    def test_shell_scene_facade_exports_remain_available(self) -> None:
        session = TuiSession(
            settings=None,  # type: ignore[arg-type]
            state=ChatSessionState(),
            llm_provider=None,  # type: ignore[arg-type]
            task_router=None,  # type: ignore[arg-type]
            output_lines=[],
        )

        self.assertEqual(output_mode_label(session), "transcript:live")
        self.assertTrue(build_status_bar_items(session))
        self.assertEqual(build_header_bar_view(session, "normal").brand, "COGNITRADE")
        self.assertEqual(build_input_bar_view(session).prompt, "> ")
        self.assertIsNotNone(build_popup_layout_view(session))


if __name__ == "__main__":
    unittest.main()
