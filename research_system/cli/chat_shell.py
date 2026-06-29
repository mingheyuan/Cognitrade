from __future__ import annotations

from research_system.cli.chat_interactions import (
    emit_meta_update,
    emit_warning_update,
    request_stop,
    submit_tui_input,
    toggle_commands_popup,
)
from research_system.cli.chat_keybindings import build_chat_keybindings
from research_system.cli.chat_types import ChatSessionState, TuiSession
from research_system.cli.chat_view import build_chat_application, build_chat_view
from research_system.runtime.provider_registry import ProviderRegistry
from research_system.runtime.settings import Settings
from research_system.services.task_router import TaskRouter


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
    llm_provider = ProviderRegistry(settings).default()
    task_router = TaskRouter(llm_provider)
    session = TuiSession(
        settings=settings,
        state=ChatSessionState(
            current_intent=initial_intent,
            review_target_run_id=initial_run_id,
            thinking_enabled=thinking == "on",
            events_mode=events,
        ),
        llm_provider=llm_provider,
        task_router=task_router,
        output_lines=[],
    )
    emit_meta_update(session, "Cognitrade terminal shell")
    emit_meta_update(session, "Enter to submit, Esc to clear, Ctrl+C to stop current run, Ctrl+D or /exit to quit.")
    emit_meta_update(session, "Up/Down recalls input history. Tab completes slash commands. Use /commands or F1 for popup help.")
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
        submit_tui_input=submit_tui_input,
        request_stop=request_stop,
        toggle_commands_popup=toggle_commands_popup,
        emit_warning_update=emit_warning_update,
    )
    app = build_chat_application(session=session, view=view, key_bindings=key_bindings)
    session.app = app
    return app.run()
