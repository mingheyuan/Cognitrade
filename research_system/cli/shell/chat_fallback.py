from __future__ import annotations

from research_system.cli.runtime.chat_session_bootstrap import build_chat_startup_context
from research_system.cli.runtime.chat_ui_runtime import build_chat_fallback_runtime
from research_system.runtime.platform.settings import Settings


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
    fallback_runtime = build_chat_fallback_runtime(
        settings=settings,
        state=runtime.state,
        llm_provider=runtime.llm_provider,
        task_router=runtime.task_router,
        handle_chat_line=handle_chat_line,
        banner_lines=banner_lines,
    )
    fallback_runtime.print_banner()
    return fallback_runtime.run_loop()
