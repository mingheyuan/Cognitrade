from __future__ import annotations

from research_system.cli.chat_command_catalog import CHAT_COMMAND_SUMMARY_LINES
from research_system.cli.chat_scene_models import CommandsPopupView


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
            "F5      toggle trace raw sections",
            "F12     toggle child detail expansion",
            "Ctrl+F12 toggle focused task collapse",
            "Ctrl+F3  next child focus",
            "Ctrl+F4  clear child focus",
            "Ctrl+F9  next trace drill target",
            "Ctrl+F10 move up trace branch / clear call",
            "Ctrl+F11 collapse trace branch",
            "F6      cycle trace/events/threads panel",
            "F7      cycle threads panel scope",
            "F8      focus next thread",
            "Ctrl+F8  focus next thread tree node",
            "F9      resume focused thread",
            "F10     fork focused thread",
            "F11     archive/unarchive focused thread",
            "Ctrl+C  stop current run or clear input",
            "Ctrl+D  exit shell",
            "Up/Down history",
            "Tab     complete command or parameter",
        )
    )


def commands_popup_text() -> str:
    return "\n".join(build_commands_popup_view().lines)
