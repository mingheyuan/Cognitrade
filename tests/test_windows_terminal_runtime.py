from __future__ import annotations

import unittest

from research_system.cli.shell.windows_terminal import create_terminal_io as shell_create_terminal_io
from research_system.cli.windows_terminal import create_terminal_io


class WindowsTerminalRuntimeTests(unittest.TestCase):
    def test_top_level_windows_terminal_exposes_shell_runtime_entrypoint(self) -> None:
        self.assertTrue(callable(create_terminal_io))
        self.assertTrue(callable(shell_create_terminal_io))


if __name__ == "__main__":
    unittest.main()
