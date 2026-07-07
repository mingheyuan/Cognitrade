from __future__ import annotations

import unittest

from research_system.cli.shell.chat_fallback import run_chat_fallback
from research_system.cli.shell.chat_shell import run_chat_tui


class ChatShellRuntimeTests(unittest.TestCase):
    def test_shell_package_exports_tui_and_fallback_runners(self) -> None:
        self.assertIsNotNone(run_chat_fallback)
        self.assertIsNotNone(run_chat_tui)


if __name__ == "__main__":
    unittest.main()
