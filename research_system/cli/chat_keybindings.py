from __future__ import annotations

from prompt_toolkit.document import Document
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys

from research_system.cli.chat_types import TuiSession


def build_chat_keybindings(
    session: TuiSession,
    *,
    handle_chat_line,
    execute_cli_command,
    submit_tui_input,
    request_stop,
    toggle_commands_popup,
    emit_warning_update,
) -> KeyBindings:
    kb = KeyBindings()

    def _move_output_cursor(session: TuiSession, *, lines: int | None = None, absolute: str | None = None) -> None:
        output_area = session.output_area
        if output_area is None:
            return
        buffer = getattr(output_area, "buffer", None)
        if buffer is None:
            return
        text = getattr(output_area, "text", "")
        if absolute == "start":
            buffer.cursor_position = 0
        elif absolute == "end":
            buffer.cursor_position = len(text)
        elif lines is not None:
            if lines < 0:
                buffer.cursor_up(count=abs(lines))
            elif lines > 0:
                buffer.cursor_down(count=lines)
        if session.app is not None:
            session.app.invalidate()

    @kb.add("c-d")
    def _exit(event) -> None:
        if session.running:
            emit_warning_update(session, "A run is still active. Stop it with Ctrl+C or /stop first.")
            return
        event.app.exit(result=0)

    @kb.add("c-c")
    def _stop_or_clear(event) -> None:
        if session.running:
            request_stop(session, execute_cli_command=execute_cli_command)
            return
        if session.input_area is not None:
            session.input_area.buffer.document = Document(text="")

    @kb.add("escape")
    def _clear_input(event) -> None:
        if session.commands_popup_visible:
            toggle_commands_popup(session, visible=False)
            return
        if session.input_area is not None:
            session.input_area.buffer.document = Document(text="")

    @kb.add("f1")
    def _toggle_commands(event) -> None:
        toggle_commands_popup(session)

    @kb.add(Keys.ScrollUp)
    def _scroll_up(event) -> None:
        session.follow_output = False
        _move_output_cursor(session, lines=-3)
        try:
            if session.input_area is not None:
                event.app.layout.focus(session.input_area)
        except Exception:
            pass

    @kb.add(Keys.ScrollDown)
    def _scroll_down(event) -> None:
        session.follow_output = False
        _move_output_cursor(session, lines=3)
        try:
            if session.input_area is not None:
                event.app.layout.focus(session.input_area)
        except Exception:
            pass

    @kb.add("pageup")
    def _page_up(event) -> None:
        session.follow_output = False
        _move_output_cursor(session, lines=-12)
        try:
            if session.input_area is not None:
                event.app.layout.focus(session.input_area)
        except Exception:
            pass

    @kb.add("pagedown")
    def _page_down(event) -> None:
        session.follow_output = False
        _move_output_cursor(session, lines=12)
        try:
            if session.input_area is not None:
                event.app.layout.focus(session.input_area)
        except Exception:
            pass

    @kb.add("home")
    def _home(event) -> None:
        session.follow_output = False
        _move_output_cursor(session, absolute="start")
        try:
            if session.input_area is not None:
                event.app.layout.focus(session.input_area)
        except Exception:
            pass

    @kb.add("end")
    def _end(event) -> None:
        output_area = session.output_area
        if output_area is None:
            return
        session.follow_output = True
        _move_output_cursor(session, absolute="end")
        try:
            if session.input_area is not None:
                event.app.layout.focus(session.input_area)
        except Exception:
            pass

    @kb.add("enter", eager=True)
    @kb.add("c-j", eager=True)
    def _submit(event) -> None:
        input_area = session.input_area
        if input_area is None:
            return
        if input_area.buffer.document.text.strip() == "":
            return
        if input_area.buffer.complete_state:
            input_area.buffer.complete_state = None
        session.follow_output = True
        event.app.create_background_task(
            submit_tui_input(
                session,
                handle_chat_line=handle_chat_line,
                execute_cli_command=execute_cli_command,
            )
        )

    return kb
