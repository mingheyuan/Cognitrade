from __future__ import annotations

from dataclasses import dataclass


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

