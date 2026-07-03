from __future__ import annotations

import io
import threading
import traceback
from contextlib import redirect_stderr, redirect_stdout

from prompt_toolkit.application import Application
from prompt_toolkit.document import Document

from research_system.cli.interactions.chat_interaction_output_support import (
    emit_input_update,
    emit_meta_update,
    emit_warning_update,
    iter_visible_captured_lines,
    write_tui_update,
)
from research_system.cli.rendering.chat_rendering import apply_tui_update, refresh_tui
from research_system.cli.state.chat_types import TuiSession
from research_system.cli.state.chat_updates import (
    ShellStatePatch,
    reset_turn_update,
    state_patch_update,
)


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
                for line in iter_visible_captured_lines(captured):
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


from research_system.cli.state.chat_updates import meta_update


__all__ = ["exit_tui_app", "request_stop", "submit_tui_input", "toggle_commands_popup"]
