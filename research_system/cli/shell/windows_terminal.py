from __future__ import annotations

import sys

from prompt_toolkit.input.defaults import create_input
from prompt_toolkit.output.defaults import create_output

from research_system.cli.prompt_toolkit_compat import NoConsoleScreenBufferError


def _pick_interactive_stdio():
    for candidate in (sys.__stdout__, sys.__stderr__, sys.stdout, sys.stderr):
        if candidate is not None and getattr(candidate, "isatty", lambda: False)():
            return candidate
    return None


def create_terminal_io(
    *,
    pick_interactive_stdio_fn=_pick_interactive_stdio,
    create_output_fn=create_output,
    create_input_fn=create_input,
    no_console_error_cls=NoConsoleScreenBufferError,
):
    stdout = pick_interactive_stdio_fn()
    stdin = sys.__stdin__ if sys.__stdin__ is not None else sys.stdin

    try:
        output = create_output_fn(stdout=stdout, always_prefer_tty=True)
        input_obj = create_input_fn(stdin=stdin, always_prefer_tty=True)
        return input_obj, output
    except no_console_error_cls:
        if stdout is None:
            raise
        output = create_output_fn(stdout=stdout, always_prefer_tty=False)
        input_obj = create_input_fn(stdin=stdin, always_prefer_tty=False)
        return input_obj, output
