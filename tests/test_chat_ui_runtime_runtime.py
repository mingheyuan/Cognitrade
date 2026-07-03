from __future__ import annotations

import unittest

from research_system.cli.chat_ui_runtime import (
    ChatFallbackRuntime,
    ChatShellRuntime,
    ChatUiRuntime,
    build_chat_fallback_runtime,
    build_chat_shell_runtime,
    build_chat_ui_runtime,
)


class ChatUiRuntimeRuntimeTests(unittest.TestCase):
    def test_chat_ui_runtime_facade_exports_runtime_types_and_builders(self) -> None:
        self.assertIsNotNone(ChatUiRuntime)
        self.assertIsNotNone(ChatShellRuntime)
        self.assertIsNotNone(ChatFallbackRuntime)
        self.assertIsNotNone(build_chat_ui_runtime)
        self.assertIsNotNone(build_chat_shell_runtime)
        self.assertIsNotNone(build_chat_fallback_runtime)


if __name__ == "__main__":
    unittest.main()
