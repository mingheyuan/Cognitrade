from __future__ import annotations


try:
    from prompt_toolkit.output.win32 import NoConsoleScreenBufferError
except (AssertionError, ImportError):
    class NoConsoleScreenBufferError(Exception):
        """Non-Windows fallback for prompt_toolkit's Windows-only exception."""


__all__ = ["NoConsoleScreenBufferError"]
