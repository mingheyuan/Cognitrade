from __future__ import annotations

from research_system.cli.chat_interactions import emit_meta_update
from research_system.cli.chat_session_bootstrap import build_chat_startup_context
from research_system.cli.chat_state import apply_session_patch_and_checkpoint
from research_system.runtime.settings import Settings


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
    runtime, banner_lines = build_chat_startup_context(
        settings,
        initial_intent=initial_intent,
        initial_run_id=initial_run_id,
        thinking=thinking,
        events=events,
        line_mode=True,
        fallback_reason=fallback_reason,
    )
    state = runtime.state

    for line in banner_lines:
        print(line)

    def _emit_line(update) -> None:
        apply_session_patch_and_checkpoint(settings=settings, state=state, patch=update.session_patch)
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
            llm_provider=runtime.llm_provider,
            task_router=runtime.task_router,
            output_writer=_emit_line,
        )
        if outcome == "exit":
            return 0
