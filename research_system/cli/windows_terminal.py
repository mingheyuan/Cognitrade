from __future__ import annotations

import sys

from prompt_toolkit.input.defaults import create_input
from prompt_toolkit.output.defaults import create_output
from prompt_toolkit.output.win32 import NoConsoleScreenBufferError


def _pick_interactive_stdio():
    for candidate in (sys.__stdout__, sys.__stderr__, sys.stdout, sys.stderr):
        if candidate is not None and getattr(candidate, "isatty", lambda: False)():
            return candidate
    return None


def create_terminal_io():
    stdout = _pick_interactive_stdio()
    stdin = sys.__stdin__ if sys.__stdin__ is not None else sys.stdin

    try:
        output = create_output(stdout=stdout, always_prefer_tty=True)
        input_obj = create_input(stdin=stdin, always_prefer_tty=True)
        return input_obj, output
    except NoConsoleScreenBufferError:
        if stdout is None:
            raise
        output = create_output(stdout=stdout, always_prefer_tty=False)
        input_obj = create_input(stdin=stdin, always_prefer_tty=False)
        return input_obj, output
