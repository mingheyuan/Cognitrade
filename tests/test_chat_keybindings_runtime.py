from __future__ import annotations

import unittest

from research_system.cli.keybindings.chat_keybinding_bindings import (
    build_chat_keybindings_from_runtime as keybindings_build_chat_keybindings_from_runtime,
)
from research_system.cli.keybindings.chat_keybinding_runtime import (
    ChatKeybindingRuntime as KeybindingsChatKeybindingRuntime,
)
from research_system.cli.keybindings.chat_keybinding_bindings import build_chat_keybindings_from_runtime
from research_system.cli.keybindings.chat_keybinding_runtime import (
    ChatKeybindingRuntime,
    build_chat_keybinding_runtime,
    move_output_cursor,
    set_output_mode,
)
from research_system.cli.keybindings.chat_keybindings import build_chat_keybindings


class ChatKeybindingsRuntimeTests(unittest.TestCase):
    def test_keybinding_supports_remain_available(self) -> None:
        self.assertIs(build_chat_keybindings_from_runtime, keybindings_build_chat_keybindings_from_runtime)
        self.assertIs(ChatKeybindingRuntime, KeybindingsChatKeybindingRuntime)

    def test_keybindings_facade_exports_runtime_and_binding_support(self) -> None:
        self.assertIsNotNone(build_chat_keybindings)
        self.assertIsNotNone(build_chat_keybindings_from_runtime)
        self.assertIsNotNone(build_chat_keybinding_runtime)
        self.assertIsNotNone(move_output_cursor)
        self.assertIsNotNone(set_output_mode)
        self.assertIsNotNone(ChatKeybindingRuntime)


if __name__ == "__main__":
    unittest.main()
