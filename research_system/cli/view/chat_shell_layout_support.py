from __future__ import annotations

from typing import Any, Callable

from research_system.cli.view.chat_scene_models import (
    CommandsPopupView,
    HeaderBarView,
    InputBarView,
    PopupLayoutView,
    ShellSceneView,
    TranscriptRenderBlock,
)
from research_system.cli.view.chat_shell_hint_support import build_hint_bar_view
from research_system.cli.view.chat_shell_status_support import build_status_bar_items
from research_system.cli.state.chat_types import TuiSession


OutputBlocksBuilder = Callable[[TuiSession], list[TranscriptRenderBlock]]
CommandsPopupBuilder = Callable[[], CommandsPopupView]
ThreadSummaryLoader = Callable[..., dict[str, Any]]


def build_header_bar_view(session: TuiSession, layout_mode: str) -> HeaderBarView:
    title_by_mode = {
        "normal": "Research Shell",
        "running": "Live Run",
        "command": "Command Line",
        "help": "Command Reference",
    }
    short_run = (session.current_run_id or session.state.last_run_id or "-")[-8:]
    subtitle = f"{(session.state.current_intent or 'auto')} | {session.run_status_label} | {short_run}"
    return HeaderBarView(
        brand="COGNITRADE",
        title=title_by_mode.get(layout_mode, "Research Shell"),
        subtitle=subtitle,
        mode_tag=layout_mode.upper(),
    )


def build_input_bar_view(session: TuiSession) -> InputBarView:
    if session.help_mode == "commands":
        prompt = "help> "
        multiline = False
        height = 1
    elif session.running:
        prompt = "wait> "
        multiline = False
        height = 1
    elif session.input_mode == "command":
        prompt = "/ "
        multiline = False
        height = 1
    else:
        prompt = "> "
        multiline = True
        height = 3
    return InputBarView(
        prompt=prompt,
        mode_label=session.input_mode,
        focus_target=session.focus_target,
        height=height,
        multiline=multiline,
    )


def build_popup_layout_view(session: TuiSession) -> PopupLayoutView:
    if session.help_mode == "commands":
        if session.input_mode == "command":
            return PopupLayoutView(top=1, left=2, right=2, bottom=3)
        return PopupLayoutView(top=2, left=4, right=4, bottom=4)
    return PopupLayoutView(top=3, left=6, right=6, bottom=6)


def build_shell_scene_view(
    session: TuiSession,
    *,
    build_output_blocks: OutputBlocksBuilder,
    build_commands_popup_view: CommandsPopupBuilder,
    current_thread_summary: ThreadSummaryLoader,
) -> ShellSceneView:
    if session.help_mode == "commands":
        layout_mode = "help"
    elif session.running:
        layout_mode = "running"
    elif session.input_mode == "command":
        layout_mode = "command"
    else:
        layout_mode = "normal"
    hint = build_hint_bar_view(
        session,
        current_thread_summary=current_thread_summary,
    )
    return ShellSceneView(
        header=build_header_bar_view(session, layout_mode),
        transcript_blocks=tuple(build_output_blocks(session)),
        status_items=tuple(build_status_bar_items(session)),
        hint=hint,
        commands_popup=build_commands_popup_view(),
        commands_popup_visible=session.help_mode == "commands",
        input_bar=build_input_bar_view(session),
        status_bar_height=1,
        hint_bar_height=0 if session.help_mode == "commands" else (1 if hint.visible else 0),
        popup_layout=build_popup_layout_view(session),
        layout_mode=layout_mode,
    )

