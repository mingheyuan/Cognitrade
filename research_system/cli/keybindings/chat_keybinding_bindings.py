from __future__ import annotations

from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys

from research_system.cli.inspect.chat_inspect_keybindings import (
    clear_trace_branch_focus,
    clear_trace_child_focus,
    clear_trace_focus,
    focus_next_trace_branch,
    focus_next_trace_child,
    focus_next_trace_task,
    switch_to_focused_inspect_item,
    toggle_focused_trace_task_collapse,
    toggle_output_panel,
    toggle_trace_branch_collapse,
    toggle_trace_child_details,
    toggle_trace_raw_sections,
    toggle_trace_threads_panel,
)
from research_system.cli.keybindings.chat_keybinding_runtime import ChatKeybindingRuntime, move_output_cursor, set_output_mode
from research_system.cli.keybindings.chat_thread_keybindings import (
    cycle_thread_panel_scope,
    focus_next_thread,
    focus_next_thread_tree_node,
    fork_focused_thread,
    switch_to_focused_thread as switch_to_focused_thread_panel,
    toggle_focused_thread_archive_state,
)


def build_chat_keybindings_from_runtime(runtime: ChatKeybindingRuntime) -> KeyBindings:
    kb = KeyBindings()
    session = runtime.session
    inspect_runtime = runtime.inspect_runtime
    thread_runtime = runtime.thread_runtime

    @kb.add("c-d")
    def _exit(event) -> None:
        if session.running:
            runtime.emit_warning_update(session, "A run is still active. Stop it with Ctrl+C or /stop first.")
            return
        event.app.exit(result=0)

    @kb.add("c-c")
    def _stop_or_clear(event) -> None:
        if session.running:
            runtime.request_stop(session, execute_cli_command=runtime.execute_cli_command)
            return
        runtime.clear_input()

    @kb.add("escape")
    def _clear_input(event) -> None:
        if session.commands_popup_visible:
            runtime.toggle_commands_popup(session, visible=False)
            return
        runtime.clear_input()

    @kb.add("f1")
    def _toggle_commands(event) -> None:
        runtime.toggle_commands_popup(session)

    @kb.add("f2")
    def _toggle_output_mode(event) -> None:
        toggle_output_panel(inspect_runtime)

    @kb.add("f3")
    def _trace_focus_next(event) -> None:
        focus_next_trace_task(inspect_runtime)

    @kb.add("f4")
    def _trace_focus_clear(event) -> None:
        clear_trace_focus(inspect_runtime)

    @kb.add(Keys.ControlF3)
    def _trace_child_focus_next(event) -> None:
        focus_next_trace_child(inspect_runtime)

    @kb.add(Keys.ControlF4)
    def _trace_child_focus_clear(event) -> None:
        clear_trace_child_focus(inspect_runtime)

    @kb.add(Keys.ControlF9)
    def _trace_branch_focus_next(event) -> None:
        focus_next_trace_branch(inspect_runtime)

    @kb.add(Keys.ControlF10)
    def _trace_branch_focus_clear(event) -> None:
        clear_trace_branch_focus(inspect_runtime)

    @kb.add(Keys.ControlF11)
    def _trace_branch_toggle_collapse(event) -> None:
        toggle_trace_branch_collapse(inspect_runtime)

    @kb.add("f5")
    def _trace_toggle_raw(event) -> None:
        toggle_trace_raw_sections(inspect_runtime)

    @kb.add("f12")
    def _trace_toggle_children(event) -> None:
        toggle_trace_child_details(inspect_runtime)

    @kb.add(Keys.ControlF12)
    def _trace_toggle_task_collapse(event) -> None:
        toggle_focused_trace_task_collapse(inspect_runtime)

    @kb.add("f6")
    def _trace_toggle_threads(event) -> None:
        toggle_trace_threads_panel(inspect_runtime)

    @kb.add("f7")
    def _cycle_thread_scope(event) -> None:
        cycle_thread_panel_scope(thread_runtime)

    @kb.add("f8")
    def _focus_thread(event) -> None:
        focus_next_thread(thread_runtime)

    @kb.add(Keys.ControlF8)
    def _focus_thread_tree(event) -> None:
        focus_next_thread_tree_node(thread_runtime)

    @kb.add("f9")
    def _switch_thread(event) -> None:
        if switch_to_focused_inspect_item(inspect_runtime):
            return
        switch_to_focused_thread_panel(thread_runtime)

    @kb.add("f10")
    def _fork_thread(event) -> None:
        fork_focused_thread(thread_runtime)

    @kb.add("f11")
    def _toggle_thread_archive(event) -> None:
        toggle_focused_thread_archive_state(thread_runtime)

    @kb.add(Keys.ScrollUp)
    def _scroll_up(event) -> None:
        if session.output_panel == "transcript":
            set_output_mode(session, "browse")
        move_output_cursor(session, lines=-3)

    @kb.add(Keys.ScrollDown)
    def _scroll_down(event) -> None:
        if session.output_panel == "transcript":
            set_output_mode(session, "browse")
        move_output_cursor(session, lines=3)

    @kb.add("pageup")
    def _page_up(event) -> None:
        if session.output_panel == "transcript":
            set_output_mode(session, "browse")
        move_output_cursor(session, lines=-12)

    @kb.add("pagedown")
    def _page_down(event) -> None:
        if session.output_panel == "transcript":
            set_output_mode(session, "browse")
        move_output_cursor(session, lines=12)

    @kb.add("home")
    def _home(event) -> None:
        if session.output_panel == "transcript":
            set_output_mode(session, "browse")
        move_output_cursor(session, absolute="start")

    @kb.add("end")
    def _end(event) -> None:
        output_area = session.output_area
        if output_area is None:
            return
        if session.output_panel == "transcript":
            set_output_mode(session, "live")
        move_output_cursor(session, absolute="end")

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
        set_output_mode(session, "live")
        event.app.create_background_task(
            runtime.submit_tui_input(
                session,
                handle_chat_line=runtime.handle_chat_line,
                execute_cli_command=runtime.execute_cli_command,
            )
        )

    return kb
