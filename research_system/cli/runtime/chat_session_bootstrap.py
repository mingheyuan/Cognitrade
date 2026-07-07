from __future__ import annotations

from dataclasses import dataclass

from research_system.cli.memory.chat_memory import ensure_session_thread, hydrate_session_from_persistent_memory
from research_system.cli.state.chat_types import ChatSessionState
from research_system.runtime.platform.settings import Settings
from research_system.runtime.providers.provider_registry import OpenAIProvider, ProviderRegistry
from research_system.services.routing.router.task_router import TaskRouter


@dataclass(frozen=True)
class ChatRuntimeContext:
    state: ChatSessionState
    llm_provider: OpenAIProvider
    task_router: TaskRouter


def build_chat_banner_lines(
    *,
    thread_id: str | None,
    resumed_query: str | None,
    line_mode: bool,
    fallback_reason: str | None = None,
) -> list[str]:
    lines: list[str] = []
    if line_mode:
        lines.append("Cognitrade chat")
        if fallback_reason and fallback_reason != "line_mode_selected":
            lines.append(f"mode: line ({fallback_reason})")
        else:
            lines.append("mode: line")
    else:
        lines.append("Cognitrade terminal shell")
    lines.append(f"thread: {thread_id or 'unset'}")
    if resumed_query:
        lines.append(f"resumed: {resumed_query}")
    if line_mode:
        lines.append("type /exit to quit")
    else:
        lines.append("Enter to submit, Esc to clear, Ctrl+C to stop current run, Ctrl+D or /exit to quit.")
        lines.append("Up/Down recalls input history. PageUp/PageDown scroll output. F2 toggles transcript/trace, F6 cycles trace/events/threads, F7 cycles thread scopes, F8 focuses a thread, Ctrl+F8 focuses the next thread tree node, F9 resumes it, F10 forks it, F11 archives or unarchives it. F3 cycles trace task focus, Ctrl+F3 cycles child focus, Ctrl+F9 drills into the next trace target, F4 restores all tasks, Ctrl+F4 restores all children, Ctrl+F10 moves up one trace branch or clears the focused call, Ctrl+F11 collapses the focused trace branch, F5 toggles raw trace sections, F12 toggles child detail expansion, Ctrl+F12 collapses the focused trace task. Use /commands or F1 for popup help.")
    return lines


def build_chat_session_state(
    settings: Settings,
    *,
    initial_intent: str | None,
    initial_run_id: str | None,
    thinking: str,
    events: str,
) -> ChatSessionState:
    state = ChatSessionState(
        current_intent=initial_intent,
        review_target_run_id=initial_run_id,
        thinking_enabled=thinking == "on",
        events_mode=events,
    )
    state.thread_id = ensure_session_thread(settings=settings, state=state)
    hydrate_session_from_persistent_memory(settings=settings, state=state)
    return state


def build_chat_runtime_context(
    settings: Settings,
    *,
    initial_intent: str | None,
    initial_run_id: str | None,
    thinking: str,
    events: str,
) -> ChatRuntimeContext:
    llm_provider = ProviderRegistry(settings).default()
    return ChatRuntimeContext(
        state=build_chat_session_state(
            settings,
            initial_intent=initial_intent,
            initial_run_id=initial_run_id,
            thinking=thinking,
            events=events,
        ),
        llm_provider=llm_provider,
        task_router=TaskRouter(llm_provider, settings=settings),
    )


def build_chat_startup_context(
    settings: Settings,
    *,
    initial_intent: str | None,
    initial_run_id: str | None,
    thinking: str,
    events: str,
    line_mode: bool,
    fallback_reason: str | None = None,
) -> tuple[ChatRuntimeContext, list[str]]:
    runtime = build_chat_runtime_context(
        settings,
        initial_intent=initial_intent,
        initial_run_id=initial_run_id,
        thinking=thinking,
        events=events,
    )
    banner_lines = build_chat_banner_lines(
        thread_id=runtime.state.thread_id,
        resumed_query=runtime.state.last_user_query,
        line_mode=line_mode,
        fallback_reason=fallback_reason,
    )
    return runtime, banner_lines
