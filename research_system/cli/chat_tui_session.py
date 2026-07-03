from __future__ import annotations

import threading
from dataclasses import dataclass, field

from prompt_toolkit.application import Application
from prompt_toolkit.layout import Float, Window
from prompt_toolkit.layout.containers import HSplit
from prompt_toolkit.widgets import TextArea

from research_system.cli.chat_state_models import (
    ChatSessionState,
    ShellUiState,
    ShellViewState,
)
from research_system.cli.chat_transcript import TranscriptMessage
from research_system.runtime.provider_registry import OpenAIProvider
from research_system.runtime.settings import Settings
from research_system.services.task_router import TaskRouter


@dataclass
class TuiSession:
    settings: Settings
    state: ChatSessionState
    llm_provider: OpenAIProvider
    task_router: TaskRouter
    output_lines: list[TranscriptMessage]
    view_state: ShellViewState = field(default_factory=ShellViewState)
    ui_state: ShellUiState = field(default_factory=ShellUiState)

    @property
    def current_run_id(self) -> str | None:
        return self.view_state.current_run_id

    @current_run_id.setter
    def current_run_id(self, value: str | None) -> None:
        self.view_state.current_run_id = value

    @property
    def running(self) -> bool:
        return self.view_state.running

    @running.setter
    def running(self, value: bool) -> None:
        self.view_state.running = value

    @property
    def stop_requested(self) -> bool:
        return self.view_state.stop_requested

    @stop_requested.setter
    def stop_requested(self, value: bool) -> None:
        self.view_state.stop_requested = value

    @property
    def current_phase(self) -> str:
        return self.view_state.current_phase

    @current_phase.setter
    def current_phase(self, value: str) -> None:
        self.view_state.current_phase = value

    @property
    def current_gate(self) -> str:
        return self.view_state.current_gate

    @current_gate.setter
    def current_gate(self, value: str) -> None:
        self.view_state.current_gate = value

    @property
    def run_status_label(self) -> str:
        return self.view_state.run_status_label

    @run_status_label.setter
    def run_status_label(self, value: str) -> None:
        self.view_state.run_status_label = value

    @property
    def commands_popup_visible(self) -> bool:
        return self.view_state.commands_popup_visible

    @commands_popup_visible.setter
    def commands_popup_visible(self, value: bool) -> None:
        self.view_state.commands_popup_visible = value

    @property
    def focus_target(self) -> str:
        return self.view_state.focus_target

    @focus_target.setter
    def focus_target(self, value: str) -> None:
        self.view_state.focus_target = value

    @property
    def help_mode(self) -> str:
        return self.view_state.help_mode

    @help_mode.setter
    def help_mode(self, value: str) -> None:
        self.view_state.help_mode = value

    @property
    def input_mode(self) -> str:
        return self.view_state.input_mode

    @input_mode.setter
    def input_mode(self, value: str) -> None:
        self.view_state.input_mode = value

    @property
    def output_mode(self) -> str:
        return self.view_state.output_mode

    @output_mode.setter
    def output_mode(self, value: str) -> None:
        self.view_state.output_mode = value

    @property
    def output_panel(self) -> str:
        return self.view_state.output_panel

    @output_panel.setter
    def output_panel(self, value: str) -> None:
        self.view_state.output_panel = value

    @property
    def thread_panel_scope(self) -> str:
        return self.view_state.thread_panel_scope

    @thread_panel_scope.setter
    def thread_panel_scope(self, value: str) -> None:
        self.view_state.thread_panel_scope = value

    @property
    def thread_focus_index(self) -> int | None:
        return self.view_state.thread_focus_index

    @thread_focus_index.setter
    def thread_focus_index(self, value: int | None) -> None:
        self.view_state.thread_focus_index = value

    @property
    def thread_tree_focus_index(self) -> int | None:
        return self.view_state.thread_tree_focus_index

    @thread_tree_focus_index.setter
    def thread_tree_focus_index(self, value: int | None) -> None:
        self.view_state.thread_tree_focus_index = value

    @property
    def trace_payload(self) -> dict | None:
        return self.view_state.trace_payload

    @trace_payload.setter
    def trace_payload(self, value: dict | None) -> None:
        prior_run_id = ""
        if isinstance(self.view_state.trace_payload, dict):
            prior_run_id = str(self.view_state.trace_payload.get("run_id") or "").strip()
        next_run_id = ""
        if isinstance(value, dict):
            next_run_id = str(value.get("run_id") or "").strip()
        self.view_state.trace_payload = value

    @property
    def events_payload(self) -> dict | None:
        return self.view_state.events_payload

    @events_payload.setter
    def events_payload(self, value: dict | None) -> None:
        self.view_state.events_payload = value
        if value is None:
            self.view_state.events_focus_index = None

    @property
    def subagent_payload(self) -> dict | None:
        return self.view_state.subagent_payload

    @subagent_payload.setter
    def subagent_payload(self, value: dict | None) -> None:
        self.view_state.subagent_payload = value

    @property
    def events_focus_index(self) -> int | None:
        return self.view_state.events_focus_index

    @events_focus_index.setter
    def events_focus_index(self, value: int | None) -> None:
        self.view_state.events_focus_index = value

    @property
    def trace_focus_index(self) -> int | None:
        return self.view_state.trace_focus_index

    @trace_focus_index.setter
    def trace_focus_index(self, value: int | None) -> None:
        self.view_state.trace_focus_index = value

    @property
    def trace_child_focus_index(self) -> int | None:
        return self.view_state.trace_child_focus_index

    @trace_child_focus_index.setter
    def trace_child_focus_index(self, value: int | None) -> None:
        self.view_state.trace_child_focus_index = value

    @property
    def trace_branch_focus_path(self) -> tuple[str, ...] | None:
        return self.view_state.trace_branch_focus_path

    @trace_branch_focus_path.setter
    def trace_branch_focus_path(self, value: tuple[str, ...] | None) -> None:
        self.view_state.trace_branch_focus_path = value

    @property
    def trace_show_raw_sections(self) -> bool:
        return self.view_state.trace_show_raw_sections

    @trace_show_raw_sections.setter
    def trace_show_raw_sections(self, value: bool) -> None:
        self.view_state.trace_show_raw_sections = value

    @property
    def trace_collapsed_branches(self) -> set[tuple[str, ...]]:
        return set(self.view_state.trace_collapsed_branches)

    @trace_collapsed_branches.setter
    def trace_collapsed_branches(self, value: set[tuple[str, ...]]) -> None:
        self.view_state.trace_collapsed_branches = {tuple(item) for item in value}

    @property
    def trace_expand_child_details(self) -> bool:
        return self.view_state.trace_expand_child_details

    @trace_expand_child_details.setter
    def trace_expand_child_details(self, value: bool) -> None:
        self.view_state.trace_expand_child_details = value

    @property
    def trace_collapsed_tasks(self) -> set[str]:
        return self.view_state.trace_collapsed_tasks

    @trace_collapsed_tasks.setter
    def trace_collapsed_tasks(self, value: set[str]) -> None:
        self.view_state.trace_collapsed_tasks = set(value)

    @property
    def answer_stream_open(self) -> bool:
        return self.view_state.answer_stream_open

    @answer_stream_open.setter
    def answer_stream_open(self, value: bool) -> None:
        self.view_state.answer_stream_open = value

    @property
    def follow_output(self) -> bool:
        return self.view_state.follow_output

    @follow_output.setter
    def follow_output(self, value: bool) -> None:
        self.view_state.follow_output = value

    @property
    def active_message_ids(self) -> dict[str, str]:
        return self.view_state.active_message_ids

    @property
    def app(self) -> Application | None:
        return self.ui_state.app

    @app.setter
    def app(self, value: Application | None) -> None:
        self.ui_state.app = value

    @property
    def output_area(self) -> TextArea | None:
        return self.ui_state.output_area

    @output_area.setter
    def output_area(self, value: TextArea | None) -> None:
        self.ui_state.output_area = value

    @property
    def input_area(self) -> TextArea | None:
        return self.ui_state.input_area

    @input_area.setter
    def input_area(self, value: TextArea | None) -> None:
        self.ui_state.input_area = value

    @property
    def main_stack(self) -> HSplit | None:
        return self.ui_state.main_stack

    @main_stack.setter
    def main_stack(self, value: HSplit | None) -> None:
        self.ui_state.main_stack = value

    @property
    def commands_popup(self) -> TextArea | None:
        return self.ui_state.commands_popup

    @commands_popup.setter
    def commands_popup(self, value: TextArea | None) -> None:
        self.ui_state.commands_popup = value

    @property
    def commands_popup_float(self) -> Float | None:
        return self.ui_state.commands_popup_float

    @commands_popup_float.setter
    def commands_popup_float(self, value: Float | None) -> None:
        self.ui_state.commands_popup_float = value

    @property
    def status_bar(self) -> Window | None:
        return self.ui_state.status_bar

    @status_bar.setter
    def status_bar(self, value: Window | None) -> None:
        self.ui_state.status_bar = value

    @property
    def input_hint_bar(self) -> Window | None:
        return self.ui_state.hint_bar

    @input_hint_bar.setter
    def input_hint_bar(self, value: Window | None) -> None:
        self.ui_state.hint_bar = value

    @property
    def worker(self) -> threading.Thread | None:
        return self.ui_state.worker

    @worker.setter
    def worker(self, value: threading.Thread | None) -> None:
        self.ui_state.worker = value


__all__ = ["TuiSession"]
