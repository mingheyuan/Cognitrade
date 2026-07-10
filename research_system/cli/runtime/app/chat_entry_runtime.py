from __future__ import annotations

from dataclasses import dataclass

from research_system.cli.prompt_toolkit_compat import NoConsoleScreenBufferError
from research_system.runtime.platform.settings import Settings


@dataclass(frozen=True)
class ChatEntryRuntime:
    run_chat_tui_fn: object
    run_chat_fallback_fn: object
    handle_chat_line: object
    execute_cli_command: object

    def run_chat_entry(
        self,
        settings: Settings,
        *,
        initial_intent: str | None,
        initial_run_id: str | None,
        thinking: str,
        events: str,
        ui: str,
    ) -> int:
        if ui == "tui":
            try:
                return self.run_chat_tui_fn(
                    settings,
                    initial_intent=initial_intent,
                    initial_run_id=initial_run_id,
                    thinking=thinking,
                    events=events,
                    handle_chat_line=self.handle_chat_line,
                    execute_cli_command=self.execute_cli_command,
                )
            except NoConsoleScreenBufferError as exc:
                return self.run_chat_fallback_fn(
                    settings,
                    initial_intent=initial_intent,
                    initial_run_id=initial_run_id,
                    thinking=thinking,
                    events=events,
                    handle_chat_line=self.handle_chat_line,
                    fallback_reason=str(exc),
                )
        return self.run_chat_fallback_fn(
            settings,
            initial_intent=initial_intent,
            initial_run_id=initial_run_id,
            thinking=thinking,
            events=events,
            handle_chat_line=self.handle_chat_line,
            fallback_reason="line_mode_selected",
        )


def build_chat_entry_runtime(
    *,
    run_chat_tui_fn,
    run_chat_fallback_fn,
    handle_chat_line,
    execute_cli_command,
) -> ChatEntryRuntime:
    return ChatEntryRuntime(
        run_chat_tui_fn=run_chat_tui_fn,
        run_chat_fallback_fn=run_chat_fallback_fn,
        handle_chat_line=handle_chat_line,
        execute_cli_command=execute_cli_command,
    )


def run_chat_entry(
    settings: Settings,
    *,
    initial_intent: str | None,
    initial_run_id: str | None,
    thinking: str,
    events: str,
    ui: str,
    runtime: ChatEntryRuntime,
) -> int:
    return runtime.run_chat_entry(
        settings,
        initial_intent=initial_intent,
        initial_run_id=initial_run_id,
        thinking=thinking,
        events=events,
        ui=ui,
    )
