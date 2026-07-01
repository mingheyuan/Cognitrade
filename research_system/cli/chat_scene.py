from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from research_system.services.run_payloads import render_labeled_field_line, render_trace_row_text, trace_payload_fields
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
            "F2      toggle transcript/trace panel",
            "F3      next trace task focus",
            "F4      clear trace task focus",
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


def build_trace_blocks(payload: dict | None) -> list[TranscriptRenderBlock]:
    if not payload:
        return [
            TranscriptRenderBlock(
                title="trace",
                lines=(
                    "No trace loaded for the current or last run.",
                    "Run /trace <run_id|last>, or press F2 after a completed run.",
                ),
            )
        ]
    blocks = [
        TranscriptRenderBlock(
            title="trace",
            lines=tuple(render_labeled_field_line(label, value) for label, value in trace_payload_fields(payload)),
        )
    ]
    task_blocks = build_trace_task_blocks(payload)
    if task_blocks:
        blocks.extend(task_blocks)
    for section_name in ("subagent_trace", "dynamic_trace", "wait_trace"):
        rows = payload.get(section_name) or []
        if not isinstance(rows, list):
            continue
        lines = tuple(
            render_trace_row_text(row, indent="  ")
            for row in rows
            if isinstance(row, dict)
        ) or ("  -",)
        blocks.append(TranscriptRenderBlock(title=section_name, lines=lines))
    return blocks


def build_trace_task_blocks(payload: dict | None, *, focus_index: int | None = None) -> list[TranscriptRenderBlock]:
    if not isinstance(payload, dict):
        return []
    groups = _group_trace_runtime_cards(payload)
    if not groups:
        return []
    if focus_index is not None:
        if 0 <= focus_index < len(groups):
            groups = [groups[focus_index]]
        else:
            groups = []
    blocks: list[TranscriptRenderBlock] = []
    for group in groups:
        header = group["title"]
        lines: list[str] = []
        status_counts = group.get("status_counts") or {}
        lines.append(f"task: {group['task_label']}")
        if status_counts:
            lines.append(
                "status_counts: "
                + ", ".join(f"{status}={count}" for status, count in sorted(status_counts.items()))
            )
        if group.get("objective"):
            lines.append(f"objective: {group['objective']}")
        if group.get("waiting"):
            lines.append("waits:")
            lines.extend(f"  {line}" for line in group["waiting"])
        if group.get("dynamic"):
            lines.append("dynamic_inserts:")
            lines.extend(f"  {line}" for line in group["dynamic"])
        if group.get("subagents"):
            lines.append("spawned_subagents:")
            lines.extend(f"  {line}" for line in group["subagents"])
        blocks.append(TranscriptRenderBlock(title=header, lines=tuple(lines or ("-",))))
    return blocks


def build_output_blocks(session: TuiSession) -> list[TranscriptRenderBlock]:
    if session.output_panel == "trace":
        blocks = build_trace_blocks(session.trace_payload)
        if not isinstance(session.trace_payload, dict):
            return blocks
        focus_index = session.trace_focus_index
        if focus_index is None:
            return blocks
        focused_task_blocks = build_trace_task_blocks(session.trace_payload, focus_index=focus_index)
        if not focused_task_blocks:
            return blocks
        focused_titles = {block.title for block in focused_task_blocks}
        filtered_blocks = [blocks[0], *focused_task_blocks]
        for block in blocks[1:]:
            if block.title in focused_titles:
                continue
            if block.title in {"subagent_trace", "dynamic_trace", "wait_trace"}:
                filtered_blocks.append(block)
        return filtered_blocks
    return build_tui_blocks(session.output_lines)


def output_mode_label(session: TuiSession) -> str:
    if session.output_panel == "trace":
        return "trace"
    return f"transcript:{session.output_mode}"


def _group_trace_runtime_cards(payload: dict) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    order: list[tuple[str, str, str]] = []

    def _ensure_group(
        phase: str | None,
        agent: str | None,
        node_id: str | None,
        objective: str | None,
        action_id: str | None = None,
    ) -> dict[str, Any]:
        resolved_node = str(node_id or "").strip() or "-"
        resolved_phase = str(phase or "-")
        resolved_agent = str(agent or "-")
        key = (
            str(action_id or resolved_node or "-"),
            resolved_phase,
            resolved_agent,
        )
        if key not in grouped:
            title = f"{resolved_node} | {resolved_phase} / {resolved_agent}" if resolved_node != "-" else f"{resolved_phase} / {resolved_agent}"
            task_label = resolved_node if resolved_node != "-" else f"{resolved_phase}:{resolved_agent}"
            grouped[key] = {
                "phase": resolved_phase,
                "agent": resolved_agent,
                "node_id": None if resolved_node == "-" else resolved_node,
                "action_id": str(action_id or "").strip() or None,
                "objective": str(objective or "").strip() or None,
                "title": title,
                "task_label": task_label,
                "status_counts": {},
                "subagents": [],
                "dynamic": [],
                "waiting": [],
            }
            order.append(key)
        elif not grouped[key].get("objective") and str(objective or "").strip():
            grouped[key]["objective"] = str(objective).strip()
        return grouped[key]

    for row in payload.get("subagent_trace") or []:
        if not isinstance(row, dict):
            continue
        group = _ensure_group(
            row.get("phase"),
            row.get("agent"),
            row.get("node_id"),
            row.get("action_objective") or row.get("objective"),
            row.get("action_id"),
        )
        status = str(row.get("status") or "unknown").strip() or "unknown"
        group["status_counts"][status] = int(group["status_counts"].get(status) or 0) + 1
        group["subagents"].append(render_trace_row_text(row).strip())

    for row in payload.get("dynamic_trace") or []:
        if not isinstance(row, dict):
            continue
        group = _ensure_group(
            row.get("phase"),
            row.get("agent"),
            row.get("node_id"),
            row.get("objective"),
            row.get("action_id"),
        )
        group["dynamic"].append(render_trace_row_text(row).strip())

    for row in payload.get("wait_trace") or []:
        if not isinstance(row, dict):
            continue
        group = _ensure_group(
            row.get("phase"),
            row.get("agent"),
            row.get("node_id"),
            row.get("objective"),
            row.get("action_id"),
        )
        group["waiting"].append(render_trace_row_text(row).strip())

    return [grouped[key] for key in order]


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
        StatusBarItem("output", output_mode_label(session)),
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
    elif session.output_panel == "trace":
        run_id = session.current_run_id or session.state.last_run_id or "-"
        focus_suffix = " | F3 next task | F4 all tasks"
        if session.trace_focus_index is not None:
            focus_suffix = f" | focus task {session.trace_focus_index + 1} | F3 next | F4 all tasks"
        message = f"trace panel | run {run_id} | PageUp/PageDown scroll trace | F2 transcript{focus_suffix}"
    elif session.running:
        message = f"running | output transcript:{session.output_mode} | Ctrl+C or /stop | F1 commands | F2 trace"
    elif session.state.pending_clarification_question:
        message = f"clarification pending | output transcript:{session.output_mode} | next plain message continues the current request | F1 commands | F2 trace"
    else:
        message = f"ready | output transcript:{session.output_mode} | PageUp/PageDown scroll transcript | End jump live | F1 commands | F2 trace"
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
