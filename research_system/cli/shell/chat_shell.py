from __future__ import annotations

from research_system.cli.keybindings.chat_keybindings import build_chat_keybindings
from research_system.cli.runtime.chat_session_bootstrap import build_chat_startup_context
from research_system.cli.runtime.chat_ui_runtime import build_chat_shell_runtime
from research_system.cli.state.chat_types import TuiSession
from research_system.cli.view.chat_view import build_chat_application, build_chat_view
from research_system.runtime.platform.settings import Settings


def run_chat_tui(
    settings: Settings,
    *,
    initial_intent: str | None,
    initial_run_id: str | None,
    thinking: str,
    events: str,
    handle_chat_line,
    execute_cli_command,
) -> int:
    runtime, banner_lines = build_chat_startup_context(
        settings,
        initial_intent=initial_intent,
        initial_run_id=initial_run_id,
        thinking=thinking,
        events=events,
        line_mode=False,
    )
    session = TuiSession(
        settings=settings,
        state=runtime.state,
        llm_provider=runtime.llm_provider,
        task_router=runtime.task_router,
        output_lines=[],
    )
    shell_runtime = build_chat_shell_runtime(
        session=session,
        handle_chat_line=handle_chat_line,
        execute_cli_command=execute_cli_command,
        banner_lines=banner_lines,
    )
    shell_runtime.emit_banner()
    view = build_chat_view(session)
    session.output_area = view.output_area
    session.main_stack = view.main_stack
    session.commands_popup = view.commands_popup
    session.commands_popup_float = view.commands_popup_float
    session.input_area = view.input_area
    session.status_bar = view.status_bar
    session.input_hint_bar = view.hint_bar
    key_bindings = build_chat_keybindings(
        session,
        handle_chat_line=handle_chat_line,
        execute_cli_command=execute_cli_command,
        submit_tui_input=shell_runtime.ui_runtime.submit_tui_input,
        request_stop=shell_runtime.ui_runtime.request_stop,
        toggle_commands_popup=shell_runtime.ui_runtime.toggle_commands_popup,
        emit_warning_update=shell_runtime.ui_runtime.emit_warning_update,
    )
    app = build_chat_application(session=session, view=view, key_bindings=key_bindings)
    session.app = app
    return app.run()
