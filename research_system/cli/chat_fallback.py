from __future__ import annotations

from research_system.cli.chat_interactions import emit_meta_update
from research_system.cli.chat_types import ChatSessionState
from research_system.runtime.provider_registry import ProviderRegistry
from research_system.runtime.settings import Settings
from research_system.services.task_router import TaskRouter


def run_chat_fallback(
    settings: Settings,
    *,
    initial_intent: str | None,
    initial_run_id: str | None,
    thinking: str,
    events: str,
    handle_chat_line,
    fallback_reason: str,
) -> int:
    llm_provider = ProviderRegistry(settings).default()
    task_router = TaskRouter(llm_provider)
    state = ChatSessionState(
        current_intent=initial_intent,
        review_target_run_id=initial_run_id,
        thinking_enabled=thinking == "on",
        events_mode=events,
    )

    print("Cognitrade chat fallback")
    print(f"reason: {fallback_reason}")
    print("TUI is unavailable in this terminal, switched to line mode.")
    print("type /exit to quit")

    def _emit_line(update) -> None:
        if update.action == "stream_append":
            print(update.text, end="", flush=True)
            return
        if update.action == "stream_done":
            print()
            return
        if update.text:
            print(update.text, flush=True)

    while True:
        try:
            line = input("> ").strip()
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print()
            continue
        if not line:
            continue
        outcome = handle_chat_line(
            settings,
            state,
            line,
            llm_provider=llm_provider,
            task_router=task_router,
            output_writer=_emit_line,
        )
        if outcome == "exit":
            return 0
