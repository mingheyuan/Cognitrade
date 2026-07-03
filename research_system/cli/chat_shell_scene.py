from __future__ import annotations

from typing import Any, Callable

from research_system.cli.chat_scene_models import (
    CommandsPopupView,
    HeaderBarView,
    HintBarView,
    InputBarView,
    PopupLayoutView,
    ShellSceneView,
    StatusBarItem,
    TranscriptRenderBlock,
)
from research_system.cli.chat_shell_hint_support import (
    build_hint_bar_view as hint_build_hint_bar_view,
    idle_transcript_context_text as hint_idle_transcript_context_text,
)
from research_system.cli.chat_shell_layout_support import (
    build_header_bar_view as layout_build_header_bar_view,
    build_input_bar_view as layout_build_input_bar_view,
    build_popup_layout_view as layout_build_popup_layout_view,
    build_shell_scene_view as layout_build_shell_scene_view,
)
from research_system.cli.chat_shell_status_support import (
    build_status_bar_items as status_build_status_bar_items,
    output_mode_label as status_output_mode_label,
)
from research_system.cli.chat_types import TuiSession


OutputBlocksBuilder = Callable[[TuiSession], list[TranscriptRenderBlock]]
CommandsPopupBuilder = Callable[[], CommandsPopupView]
ThreadSummaryLoader = Callable[..., dict[str, Any]]


def output_mode_label(session: TuiSession) -> str:
    return status_output_mode_label(session)


def build_status_bar_items(session: TuiSession) -> list[StatusBarItem]:
    return status_build_status_bar_items(session)


def build_header_bar_view(session: TuiSession, layout_mode: str) -> HeaderBarView:
    return layout_build_header_bar_view(session, layout_mode)


def build_hint_bar_view(
    session: TuiSession,
    *,
    current_thread_summary: ThreadSummaryLoader,
) -> HintBarView:
    return hint_build_hint_bar_view(
        session,
        current_thread_summary=current_thread_summary,
    )


def build_input_bar_view(session: TuiSession) -> InputBarView:
    return layout_build_input_bar_view(session)


def build_popup_layout_view(session: TuiSession) -> PopupLayoutView:
    return layout_build_popup_layout_view(session)


def build_shell_scene_view(
    session: TuiSession,
    *,
    build_output_blocks: OutputBlocksBuilder,
    build_commands_popup_view: CommandsPopupBuilder,
    current_thread_summary: ThreadSummaryLoader,
) -> ShellSceneView:
    return layout_build_shell_scene_view(
        session,
        build_output_blocks=build_output_blocks,
        build_commands_popup_view=build_commands_popup_view,
        current_thread_summary=current_thread_summary,
    )


def _idle_transcript_context_text(
    session: TuiSession,
    *,
    current_thread_summary: ThreadSummaryLoader,
) -> str:
    return hint_idle_transcript_context_text(
        session,
        current_thread_summary=current_thread_summary,
    )
