from __future__ import annotations

import re
import threading
import time

from research_system.cli.chat_rendering import apply_tui_update
from research_system.cli.chat_types import TuiSession
from research_system.cli.chat_updates import (
    TranscriptUpdate,
    input_update,
    meta_update,
    warning_update,
)


_PROGRESS_LINE_PATTERNS = (
    re.compile(r"^\s*\d+%\s*\|\s*\d+/\d+"),
    re.compile(r"^\s*\d+%\|"),
)
_STREAM_FLUSH_INTERVAL_SECONDS = 0.04


def is_transient_progress_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    lowered = stripped.lower()
    if "please wait for a moment" in lowered:
        return True
    if any(pattern.match(stripped) for pattern in _PROGRESS_LINE_PATTERNS):
        return True
    if "it/s" in lowered and "|" in stripped:
        return True
    return False


def iter_visible_captured_lines(captured: str):
    for line in captured.splitlines():
        if not line.strip():
            continue
        if is_transient_progress_line(line):
            continue
        yield line.rstrip()


def write_tui_update(session: TuiSession, update: TranscriptUpdate) -> None:
    if update.action == "stream_append":
        pending = session.ui_state.pending_stream_update
        if isinstance(pending, TranscriptUpdate):
            session.ui_state.pending_stream_update = TranscriptUpdate(
                action="stream_append",
                kind=update.kind or pending.kind,
                text=pending.text + update.text,
                slot_key=update.slot_key or pending.slot_key,
                state_patch=update.state_patch,
                session_patch=update.session_patch,
            )
            return
        session.ui_state.pending_stream_update = update

        def _flush_pending() -> None:
            time.sleep(_STREAM_FLUSH_INTERVAL_SECONDS)
            pending_update = session.ui_state.pending_stream_update
            session.ui_state.pending_stream_update = None
            if isinstance(pending_update, TranscriptUpdate):
                apply_tui_update(session, pending_update)

        threading.Thread(target=_flush_pending, daemon=True).start()
        return
    pending_update = session.ui_state.pending_stream_update
    if isinstance(pending_update, TranscriptUpdate):
        session.ui_state.pending_stream_update = None
        apply_tui_update(session, pending_update)
    apply_tui_update(session, update)


def emit_meta_update(session: TuiSession, message: str) -> None:
    apply_tui_update(session, meta_update(message))


def emit_input_update(session: TuiSession, message: str) -> None:
    apply_tui_update(session, input_update(message))


def emit_warning_update(session: TuiSession, message: str) -> None:
    apply_tui_update(session, warning_update(message))


__all__ = [
    "emit_input_update",
    "emit_meta_update",
    "emit_warning_update",
    "is_transient_progress_line",
    "iter_visible_captured_lines",
    "write_tui_update",
]
