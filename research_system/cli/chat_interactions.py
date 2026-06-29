from __future__ import annotations

import io
import re
import threading
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout

from prompt_toolkit.application import Application
from prompt_toolkit.document import Document

from research_system.cli.chat_rendering import apply_tui_update, refresh_tui
from research_system.cli.chat_types import TuiSession
from research_system.cli.chat_updates import (
    ShellStatePatch,
    TranscriptUpdate,
    input_update,
    meta_update,
    reset_turn_update,
    state_patch_update,
    warning_update,
)


_PROGRESS_LINE_PATTERNS = (
    re.compile(r"^\s*\d+%\s*\|\s*\d+/\d+"),
    re.compile(r"^\s*\d+%\|"),
)
_STREAM_FLUSH_INTERVAL_SECONDS = 0.04


def _is_transient_progress_line(line: str) -> bool:
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


def _iter_visible_captured_lines(captured: str):
    for line in captured.splitlines():
        if not line.strip():
            continue
        if _is_transient_progress_line(line):
            continue
        yield line.rstrip()


async def submit_tui_input(session: TuiSession, *, handle_chat_line, execute_cli_command) -> None:
    if session.input_area is None:
        return
    text = session.input_area.text.strip()
    if not text:
        return
    if session.running:
        if text == "/stop":
            session.input_area.buffer.document = Document(text="")
            emit_input_update(session, f"> {text}")
            request_stop(session, execute_cli_command=execute_cli_command)
            return
        emit_warning_update(session, "A run is still in progress. Wait for it to finish, or use Ctrl+C or /stop.")
        return
    session.input_area.buffer.document = Document(text="")
    emit_input_update(session, f"> {text}")
    apply_tui_update(
        session,
        reset_turn_update(
            state_patch=ShellStatePatch(
                run_status_label="running",
                current_phase="routing",
                current_gate="pending",
                running=True,
                stop_requested=False,
                focus_target="input",
                input_mode="insert",
            )
        ),
    )
    session.follow_output = True
    refresh_tui(session)

    def _work() -> None:
        buffer = io.StringIO()
        outcome = None
        try:
            with redirect_stdout(buffer), redirect_stderr(buffer):
                outcome = handle_chat_line(
                    session.settings,
                    session.state,
                    text,
                    llm_provider=session.llm_provider,
                    task_router=session.task_router,
                    output_writer=lambda update: write_tui_update(session, update),
                    stop_callback=lambda: request_stop(session, execute_cli_command=execute_cli_command),
                )
        except Exception as exc:  # pragma: no cover - defensive UI fallback
            traceback.print_exc(file=buffer)
            emit_warning_update(session, f"run failed: {exc}")
            outcome = None
        try:
            if session.state.last_run_id:
                apply_tui_update(
                    session,
                    state_patch_update(
                        ShellStatePatch(
                            current_run_id=session.state.last_run_id,
                        )
                    ),
                )
            captured = buffer.getvalue().strip()
            if captured:
                for line in _iter_visible_captured_lines(captured):
                    emit_meta_update(session, line)
            if outcome == "commands_popup":
                toggle_commands_popup(session)
            if outcome == "exit" and session.app is not None:
                session.app.create_background_task(exit_tui_app(session.app))
        finally:
            apply_tui_update(
                session,
                state_patch_update(
                    ShellStatePatch(
                        run_status_label="idle",
                        current_phase="-",
                        running=False,
                        stop_requested=False,
                        focus_target="input",
                    )
                ),
            )
            refresh_tui(session)

    session.worker = threading.Thread(target=_work, daemon=True)
    session.worker.start()


def request_stop(session: TuiSession, *, execute_cli_command) -> None:
    if not session.running:
        return
    apply_tui_update(
        session,
        state_patch_update(
            ShellStatePatch(
                stop_requested=True,
                run_status_label="cancelling",
                current_phase="cancelling",
                input_mode="command",
            )
        ),
    )
    run_id = session.current_run_id or session.state.last_run_id
    if run_id:
        try:
            payload = execute_cli_command(session.settings, command="cancel", run_id=run_id)
            if isinstance(payload, dict):
                apply_tui_update(
                    session,
                    meta_update(
                        f"stop requested: {payload.get('message') or 'cancelling'}",
                        state_patch=ShellStatePatch(
                            run_status_label="cancelling",
                            current_phase="cancelling",
                            input_mode="command",
                        ),
                    ),
                )
        except Exception as exc:  # pragma: no cover - UI fallback
            emit_warning_update(session, f"stop failed: {exc}")
    else:
        emit_warning_update(session, "No active run_id is available to stop.")


async def exit_tui_app(app: Application) -> None:
    app.exit(result=0)


def toggle_commands_popup(session: TuiSession, *, visible: bool | None = None) -> None:
    next_visible = (not session.commands_popup_visible) if visible is None else visible
    apply_tui_update(
        session,
        state_patch_update(
            ShellStatePatch(
                commands_popup_visible=next_visible,
                help_mode="commands" if next_visible else "hidden",
                focus_target="commands_popup" if next_visible else "input",
            )
        ),
    )
    refresh_tui(session)


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
