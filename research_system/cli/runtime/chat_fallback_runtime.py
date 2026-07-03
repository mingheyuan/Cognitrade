from __future__ import annotations

from dataclasses import dataclass

from research_system.cli.state.chat_state import apply_session_patch_and_checkpoint
from research_system.runtime.platform.settings import Settings


@dataclass(frozen=True)
class ChatFallbackRuntime:
    settings: Settings
    state: object
    llm_provider: object
    task_router: object
    handle_chat_line: object
    banner_lines: tuple[str, ...]

    def print_banner(self) -> None:
        for line in self.banner_lines:
            print(line)

    def emit_line(self, update) -> None:
        apply_session_patch_and_checkpoint(settings=self.settings, state=self.state, patch=update.session_patch)
        if update.action == "stream_append":
            print(update.text, end="", flush=True)
            return
        if update.action == "stream_done":
            print()
            return
        if update.text:
            print(update.text, flush=True)

    def run_loop(self) -> int:
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
            outcome = self.handle_chat_line(
                self.settings,
                self.state,
                line,
                llm_provider=self.llm_provider,
                task_router=self.task_router,
                output_writer=self.emit_line,
            )
            if outcome == "exit":
                return 0


def build_chat_fallback_runtime(
    *,
    settings: Settings,
    state,
    llm_provider,
    task_router,
    handle_chat_line,
    banner_lines,
) -> ChatFallbackRuntime:
    return ChatFallbackRuntime(
        settings=settings,
        state=state,
        llm_provider=llm_provider,
        task_router=task_router,
        handle_chat_line=handle_chat_line,
        banner_lines=tuple(banner_lines),
    )


__all__ = ["ChatFallbackRuntime", "build_chat_fallback_runtime"]
