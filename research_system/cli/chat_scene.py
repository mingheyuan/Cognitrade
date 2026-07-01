from __future__ import annotations

from dataclasses import dataclass

from research_system.cli.chat_command_catalog import CHAT_COMMAND_SUMMARY_LINES
from research_system.cli.chat_transcript import (
    TranscriptMessage,
    display_block_title_for_message,
    renders_as_inline_message,
)
from research_system.cli.chat_types import TuiSession


@dataclass(frozen=True)
class TranscriptRenderBlock:
    title: str | None
    lines: tuple[str, ...]
    inline: bool = False


@dataclass(frozen=True)
class StatusBarItem:
    label: str
    value: str


@dataclass(frozen=True)
class HeaderBarView:
    brand: str
    title: str
    subtitle: str
    mode_tag: str


@dataclass(frozen=True)
class HintBarView:
    text: str
    visible: bool = True


@dataclass(frozen=True)
class CommandsPopupView:
    lines: tuple[str, ...]


@dataclass(frozen=True)
class InputBarView:
    prompt: str
    mode_label: str
    focus_target: str
    height: int
    multiline: bool


@dataclass(frozen=True)
class PopupLayoutView:
    top: int
    left: int
    right: int
    bottom: int


@dataclass(frozen=True)
class ShellSceneView:
    header: HeaderBarView
    transcript_blocks: tuple[TranscriptRenderBlock, ...]
    status_items: tuple[StatusBarItem, ...]
    hint: HintBarView
    commands_popup: CommandsPopupView
    commands_popup_visible: bool
    input_bar: InputBarView
    status_bar_height: int
    hint_bar_height: int
    popup_layout: PopupLayoutView
    layout_mode: str


def commands_popup_text() -> str:
    return "\n".join(build_commands_popup_view().lines)


def build_commands_popup_view() -> CommandsPopupView:
    return CommandsPopupView(
        lines=(
            "Commands",
            "",
            *CHAT_COMMAND_SUMMARY_LINES,
            "",
            "Keys",
            "",
            "Enter   submit",
            "Esc     clear input / close popup",
            "F1      toggle popup help",
            "F2      toggle transcript live/browse mode",
            "Ctrl+C  stop current run or clear input",
            "Ctrl+D  exit shell",
            "Up/Down history",
            "Tab     complete command or parameter",
        )
    )


def build_tui_blocks(lines: list[TranscriptMessage]) -> list[TranscriptRenderBlock]:
    blocks: list[TranscriptRenderBlock] = []
    for item in lines:
        text = item.text
        if item.scope == "turn" and is_message_closed_for_render(item, lines):
            continue
        title = display_block_title_for_message(item)
        split_lines = tuple(str(text).splitlines() or [""])
        if title is not None:
            blocks.append(TranscriptRenderBlock(title=title, lines=split_lines))
        elif renders_as_inline_message(item):
            blocks.append(TranscriptRenderBlock(title=None, lines=split_lines, inline=True))
        else:
            blocks.append(TranscriptRenderBlock(title=None, lines=split_lines))
    return blocks


def is_message_closed_for_render(message: TranscriptMessage, lines: list[TranscriptMessage]) -> bool:
    if message.slot_key is None:
        return False
    latest_in_slot = None
    for candidate in reversed(lines):
        if candidate.slot_key == message.slot_key:
            latest_in_slot = candidate
            break
    if latest_in_slot is None:
        return False
    return latest_in_slot.message_id != message.message_id


def build_status_bar_items(session: TuiSession) -> list[StatusBarItem]:
    intent = session.state.current_intent or "auto"
    run_id = session.current_run_id or session.state.last_run_id or "-"
    thread_short = (session.state.thread_id or "-")[-8:]
    mode = "running" if session.running else session.run_status_label
    clarify = "pending" if session.state.pending_clarification_question else "clear"
    return [
        StatusBarItem("mode", "codex"),
        StatusBarItem("thread", thread_short),
        StatusBarItem("intent", intent),
        StatusBarItem("run", run_id),
        StatusBarItem("phase", session.current_phase),
        StatusBarItem("gate", session.current_gate),
        StatusBarItem("output", session.output_mode),
        StatusBarItem("clarify", clarify),
        StatusBarItem("thinking", "on" if session.state.thinking_enabled else "off"),
        StatusBarItem("events", session.state.events_mode),
        StatusBarItem("state", mode),
    ]


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


def build_hint_bar_view(session: TuiSession) -> HintBarView:
    if session.help_mode == "commands":
        message = "popup | Esc close | Tab complete | Enter submit"
    elif session.running:
        message = f"running | output {session.output_mode} | Ctrl+C or /stop | F1 commands | F2 transcript mode"
    elif session.state.pending_clarification_question:
        message = f"clarification pending | output {session.output_mode} | next plain message continues the current request | F1 commands | F2 transcript mode"
    else:
        message = f"ready | output {session.output_mode} | PageUp/PageDown scroll transcript | End jump live | F1 commands | F2 transcript mode"
    return HintBarView(text=message, visible=bool(message))


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


def build_shell_scene_view(session: TuiSession) -> ShellSceneView:
    if session.help_mode == "commands":
        layout_mode = "help"
    elif session.running:
        layout_mode = "running"
    elif session.input_mode == "command":
        layout_mode = "command"
    else:
        layout_mode = "normal"
    hint = build_hint_bar_view(session)
    return ShellSceneView(
        header=build_header_bar_view(session, layout_mode),
        transcript_blocks=tuple(build_tui_blocks(session.output_lines)),
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
