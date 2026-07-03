from __future__ import annotations

import unittest

from research_system.cli.chat_keybinding_bindings import build_chat_keybindings_from_runtime
from research_system.cli.chat_keybinding_runtime import (
    ChatKeybindingRuntime,
    build_chat_keybinding_runtime,
    move_output_cursor,
    set_output_mode,
)
from research_system.cli.chat_keybindings import build_chat_keybindings


class ChatKeybindingsRuntimeTests(unittest.TestCase):
    def test_keybindings_facade_exports_runtime_and_binding_support(self) -> None:
        self.assertIsNotNone(build_chat_keybindings)
        self.assertIsNotNone(build_chat_keybindings_from_runtime)
        self.assertIsNotNone(build_chat_keybinding_runtime)
        self.assertIsNotNone(move_output_cursor)
        self.assertIsNotNone(set_output_mode)
        self.assertIsNotNone(ChatKeybindingRuntime)


if __name__ == "__main__":
    unittest.main()
