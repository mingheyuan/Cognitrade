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

    def _trace_task_count() -> int:
        payload = session.trace_payload
        if not isinstance(payload, dict):
            return 0
        keys: set[tuple[str, str, str]] = set()
        for section_name in ("subagent_trace", "dynamic_trace", "wait_trace"):
            rows = payload.get(section_name) or []
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                node_id = str(row.get("node_id") or "").strip() or "-"
                phase = str(row.get("phase") or "-")
                agent = str(row.get("agent") or "-")
                action_id = str(row.get("action_id") or node_id or "-")
                keys.add((action_id, phase, agent))
        return len(keys)

    def _set_output_mode(mode: str) -> None:
        session.output_mode = mode
        session.follow_output = mode == "live"

    def _toggle_output_panel() -> None:
        if session.output_panel == "trace":
            session.output_panel = "transcript"
            session.trace_focus_index = None
            session.follow_output = session.output_mode == "live"
            if session.output_mode == "live":
                _move_output_cursor(session, absolute="end")
            elif session.app is not None:
                session.app.invalidate()
            return
        run_id = session.current_run_id or session.state.last_run_id
        if isinstance(session.trace_payload, dict):
            cached_run_id = str(session.trace_payload.get("run_id") or "").strip()
            if cached_run_id and cached_run_id == str(run_id or cached_run_id):
                session.output_panel = "trace"
                session.follow_output = False
                if session.app is not None:
                    session.app.invalidate()
                return
        if run_id:
            try:
                payload = execute_cli_command(session.settings, command="trace", run_id=run_id)
            except Exception as exc:  # pragma: no cover - defensive UI fallback
                emit_warning_update(session, f"trace load failed: {exc}")
            else:
                if (
                    isinstance(payload, dict)
                    and "subagent_trace" in payload
                    and "dynamic_trace" in payload
                    and "wait_trace" in payload
                ):
                    session.trace_payload = payload
        session.output_panel = "trace"
        if _trace_task_count() <= 0:
            session.trace_focus_index = None
        session.follow_output = False
        if session.app is not None:
            session.app.invalidate()

    def _focus_next_trace_task() -> None:
        if session.output_panel != "trace":
            return
        task_count = _trace_task_count()
        if task_count <= 0:
            session.trace_focus_index = None
            if session.app is not None:
                session.app.invalidate()
            return
        current_index = session.trace_focus_index
        if current_index is None or current_index >= task_count - 1:
            session.trace_focus_index = 0
        else:
            session.trace_focus_index = current_index + 1
        session.follow_output = False
        _move_output_cursor(session, absolute="start")

    def _clear_trace_focus() -> None:
        if session.output_panel != "trace":
            return
        session.trace_focus_index = None
        session.follow_output = False
        _move_output_cursor(session, absolute="start")

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

    @kb.add("f2")
    def _toggle_output_mode(event) -> None:
        _toggle_output_panel()

    @kb.add("f3")
    def _trace_focus_next(event) -> None:
        _focus_next_trace_task()

    @kb.add("f4")
    def _trace_focus_clear(event) -> None:
        _clear_trace_focus()

    @kb.add(Keys.ScrollUp)
    def _scroll_up(event) -> None:
        if session.output_panel == "transcript":
            _set_output_mode("browse")
        _move_output_cursor(session, lines=-3)

    @kb.add(Keys.ScrollDown)
    def _scroll_down(event) -> None:
        if session.output_panel == "transcript":
            _set_output_mode("browse")
        _move_output_cursor(session, lines=3)

    @kb.add("pageup")
    def _page_up(event) -> None:
        if session.output_panel == "transcript":
            _set_output_mode("browse")
        _move_output_cursor(session, lines=-12)

    @kb.add("pagedown")
    def _page_down(event) -> None:
        if session.output_panel == "transcript":
            _set_output_mode("browse")
        _move_output_cursor(session, lines=12)

    @kb.add("home")
    def _home(event) -> None:
        if session.output_panel == "transcript":
            _set_output_mode("browse")
        _move_output_cursor(session, absolute="start")

    @kb.add("end")
    def _end(event) -> None:
        output_area = session.output_area
        if output_area is None:
            return
        if session.output_panel == "transcript":
            _set_output_mode("live")
        _move_output_cursor(session, absolute="end")

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
        session.output_panel = "transcript"
        _set_output_mode("live")
        event.app.create_background_task(
            submit_tui_input(
                session,
                handle_chat_line=handle_chat_line,
                execute_cli_command=execute_cli_command,
            )
        )

    return kb
