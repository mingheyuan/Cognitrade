from __future__ import annotations

import unittest

from research_system.cli.chat_fallback import run_chat_fallback
from research_system.cli.chat_shell import run_chat_tui
from research_system.cli.shell.chat_fallback import run_chat_fallback as shell_run_chat_fallback
from research_system.cli.shell.chat_shell import run_chat_tui as shell_run_chat_tui


class ChatShellRuntimeTests(unittest.TestCase):
    def test_top_level_shell_exports_remain_compatible_with_shell_package(self) -> None:
        self.assertIs(run_chat_fallback, shell_run_chat_fallback)
        self.assertIs(run_chat_tui, shell_run_chat_tui)


if __name__ == "__main__":
    unittest.main()
