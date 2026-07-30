from __future__ import annotations

import unittest
from unittest.mock import patch

from research_system.cli.prompt_toolkit_compat import NoConsoleScreenBufferError
from research_system.cli.shell.windows_terminal import create_terminal_io


class _TTYStub:
    def isatty(self) -> bool:
        return True


class WindowsTerminalTests(unittest.TestCase):
    def test_create_terminal_io_prefers_interactive_tty_mode(self) -> None:
        stdout = _TTYStub()
        with patch("research_system.cli.shell.windows_terminal._pick_interactive_stdio", return_value=stdout), patch(
            "research_system.cli.shell.windows_terminal.create_output", return_value="OUT"
        ) as create_output_mock, patch(
            "research_system.cli.shell.windows_terminal.create_input", return_value="IN"
        ) as create_input_mock:
            input_obj, output_obj = create_terminal_io()

        self.assertEqual((input_obj, output_obj), ("IN", "OUT"))
        create_output_mock.assert_called_once_with(stdout=stdout, always_prefer_tty=True)
        self.assertTrue(create_input_mock.call_args.kwargs["always_prefer_tty"])

    def test_create_terminal_io_falls_back_when_console_screen_buffer_is_unavailable(self) -> None:
        stdout = _TTYStub()
        with patch("research_system.cli.shell.windows_terminal._pick_interactive_stdio", return_value=stdout), patch(
            "research_system.cli.shell.windows_terminal.create_output",
            side_effect=[NoConsoleScreenBufferError(), "OUT_FALLBACK"],
        ) as create_output_mock, patch(
            "research_system.cli.shell.windows_terminal.create_input",
            return_value="IN_FALLBACK",
        ) as create_input_mock:
            input_obj, output_obj = create_terminal_io()

        self.assertEqual((input_obj, output_obj), ("IN_FALLBACK", "OUT_FALLBACK"))
        self.assertEqual(create_output_mock.call_args_list[0].kwargs["always_prefer_tty"], True)
        self.assertEqual(create_output_mock.call_args_list[1].kwargs["always_prefer_tty"], False)
        self.assertEqual(create_input_mock.call_args_list[0].kwargs["always_prefer_tty"], False)


if __name__ == "__main__":
    unittest.main()
