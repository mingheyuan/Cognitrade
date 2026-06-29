from __future__ import annotations

import threading
from dataclasses import dataclass, field

from prompt_toolkit.application import Application
from prompt_toolkit.layout import Float, Window
from prompt_toolkit.layout.containers import HSplit
from prompt_toolkit.widgets import TextArea

from research_system.cli.chat_transcript import TranscriptMessage
from research_system.runtime.provider_registry import OpenAIProvider
from research_system.runtime.settings import Settings
from research_system.services.task_router import TaskRouter


@dataclass
class ChatSessionState:
    current_intent: str | None = None
    review_target_run_id: str | None = None
    last_run_id: str | None = None
    thinking_enabled: bool = True
    events_mode: str = "compact"
    pending_clarification_query: str | None = None
    pending_clarification_intent: str | None = None
    pending_clarification_run_id: str | None = None
    pending_clarification_question: str | None = None


@dataclass
class ShellViewState:
    current_run_id: str | None = None
    running: bool = False
    stop_requested: bool = False
    current_phase: str = "-"
    current_gate: str = "pending"
    run_status_label: str = "idle"
    commands_popup_visible: bool = False
    focus_target: str = "input"
    help_mode: str = "hidden"
    input_mode: str = "insert"
    answer_stream_open: bool = False
    follow_output: bool = True
    active_message_ids: dict[str, str] = field(default_factory=dict)
    closed_message_ids: set[str] = field(default_factory=set)


@dataclass
class ShellUiState:
    app: Application | None = None
    output_area: TextArea | None = None
    main_stack: HSplit | None = None
    input_area: TextArea | None = None
    commands_popup: TextArea | None = None
    commands_popup_float: Float | None = None
    status_bar: Window | None = None
    hint_bar: Window | None = None
    worker: threading.Thread | None = None
    last_view_sync_signature: tuple | None = None
    last_rendered_output_text: str = ""
    last_focus_target: str | None = None
    pending_stream_update: object | None = None


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
