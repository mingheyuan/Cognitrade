from __future__ import annotations

from prompt_toolkit.input.defaults import create_input
from prompt_toolkit.output.defaults import create_output
from prompt_toolkit.output.win32 import NoConsoleScreenBufferError

from research_system.cli.shell.windows_terminal import create_terminal_io as shell_create_terminal_io
from research_system.cli.shell.windows_terminal import _pick_interactive_stdio as shell_pick_interactive_stdio


def _pick_interactive_stdio():
    return shell_pick_interactive_stdio()


def create_terminal_io():
    # Keep the module-level patch surface stable for tests that replace
    # `_pick_interactive_stdio`, `create_output`, and `create_input`.
    stdout = _pick_interactive_stdio()
    return shell_create_terminal_io(
        pick_interactive_stdio_fn=lambda: stdout,
        create_output_fn=create_output,
        create_input_fn=create_input,
        no_console_error_cls=NoConsoleScreenBufferError,
    )
