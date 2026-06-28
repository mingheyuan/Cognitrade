from __future__ import annotations

import argparse
import io
import json
import shlex
import sys
import threading
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path

from prompt_toolkit.application import Application
from prompt_toolkit.filters import Condition
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Float, FloatContainer, HSplit, Layout, Window
from prompt_toolkit.layout.containers import ConditionalContainer
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea

from research_system.runtime.provider_registry import LLMProviderError, OpenAIProvider, ProviderRegistry
from research_system.runtime.chat_presenter import ChatStreamPresenter, DisplayChunk
from research_system.runtime.checks import run_checks
from research_system.runtime.doctor import build_status, run_doctor
from research_system.runtime.orchestrator import ResearchOrchestrator
from research_system.runtime.settings import Settings, ensure_workspace_layout
from research_system.schemas.runtime import RunEvent
from research_system.schemas.protocol import RequestEnvelope
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
class TuiSession:
    settings: Settings
    state: ChatSessionState
    llm_provider: OpenAIProvider
    task_router: TaskRouter
    output_lines: list[tuple[str, str]]
    current_run_id: str | None = None
    running: bool = False
    stop_requested: bool = False
    app: Application | None = None
    output_area: TextArea | None = None
    status_bar: Window | None = None
    input_area: TextArea | None = None
    worker: threading.Thread | None = None
    current_phase: str = "-"
    current_gate: str = "pending"
    run_status_label: str = "idle"
    commands_popup_visible: bool = False
    answer_stream_open: bool = False
    input_hint_bar: Window | None = None


CHAT_COMMANDS = [
    "/commands",
    "/help",
    "/exit",
    "/intent",
    "/run-id",
    "/thinking",
    "/events",
    "/run",
    "/review",
    "/history",
    "/status",
    "/doctor",
    "/check",
    "/replay",
    "/resume",
    "/cancel",
    "/stop",
    "/cleanup",
    "/reindex",
    "/reindex-qdrant",
    "/last",
    "/clear",
]


class ChatCommandCompleter(Completer):
    def __init__(self, session: TuiSession) -> None:
        self.session = session

    def get_completions(self, document: Document, complete_event):
        current_token = _chat_completion_current_token(document.text_before_cursor)
        for candidate in _chat_completion_candidates(document.text_before_cursor, self.session.state):
            if current_token and not candidate.lower().startswith(current_token.lower()):
                continue
            yield Completion(candidate, start_position=-len(current_token))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research-system")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("query")
    run_parser.add_argument("--intent")
    run_parser.add_argument("--json", action="store_true", dest="as_json")
    run_parser.add_argument("--run-id")

    history_parser = subparsers.add_parser("history")
    history_parser.add_argument("--limit", type=int, default=20)
    history_parser.add_argument("--status")
    history_parser.add_argument("--json", action="store_true", dest="as_json")

    replay_parser = subparsers.add_parser("replay")
    replay_parser.add_argument("run_id")
    replay_parser.add_argument("--json", action="store_true", dest="as_json")

    resume_parser = subparsers.add_parser("resume")
    resume_parser.add_argument("run_id")
    resume_parser.add_argument("--json", action="store_true", dest="as_json")

    cancel_parser = subparsers.add_parser("cancel")
    cancel_parser.add_argument("run_id")
    cancel_parser.add_argument("--json", action="store_true", dest="as_json")

    cleanup_parser = subparsers.add_parser("cleanup")
    cleanup_parser.add_argument("--json", action="store_true", dest="as_json")

    reindex_parser = subparsers.add_parser("reindex-qdrant")
    reindex_parser.add_argument("--json", action="store_true", dest="as_json")

    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--json", action="store_true", dest="as_json")

    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--json", action="store_true", dest="as_json")

    doctor_parser = subparsers.add_parser("doctor")
    doctor_parser.add_argument("--json", action="store_true", dest="as_json")

    chat_parser = subparsers.add_parser("chat")
    chat_parser.add_argument("--intent")
    chat_parser.add_argument("--run-id")
    chat_parser.add_argument("--thinking", choices=("on", "off"), default="on")
    chat_parser.add_argument("--events", choices=("off", "compact", "full"), default="compact")
    return parser


def _commands_popup_text() -> str:
    return "\n".join(
        [
            "Commands",
            "",
            "/commands                 open or close this popup",
            "/help                     print command help into the transcript",
            "/stop                     stop the current run",
            "/intent <intent|auto>     set default intent",
            "/run-id <run_id|clear>    set review target run",
            "/thinking <on|off>        toggle thinking stream",
            "/events <off|compact|full> set event visibility",
            "/run <query>              run a query explicitly",
            "/review <run_id> <query>  run review_thesis",
            "/history [limit] [status] show run history",
            "/replay <run_id|last>     replay a run",
            "/resume <run_id|last>     inspect resumability",
            "/cancel <run_id|last>     cancel a run",
            "/status | /doctor | /check",
            "/cleanup | /reindex | /last | /clear | /exit",
            "",
            "Keys",
            "",
            "Enter   submit",
            "Esc     clear input / close popup",
            "F1      toggle popup help",
            "Ctrl+C  stop current run or clear input",
            "Ctrl+D  exit shell",
            "Up/Down history",
            "Tab     complete command or parameter",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    settings = Settings.from_env(Path.cwd())
    settings.validate()
    ensure_workspace_layout(settings)

    if args.command == "chat":
        return _run_chat_tui(
            settings,
            initial_intent=args.intent,
            initial_run_id=args.run_id,
            thinking=args.thinking,
            events=args.events,
        )

    payload = _execute_cli_command(
        settings,
        command=args.command,
        query=getattr(args, "query", None),
        intent=getattr(args, "intent", None),
        run_id=getattr(args, "run_id", None),
        limit=getattr(args, "limit", 20),
        status_filter=getattr(args, "status", None),
    )
    return _print_payload(payload, as_json=getattr(args, "as_json", False))

    parser.error("unsupported command")
    return 2


def _execute_cli_command(
    settings: Settings,
    *,
    command: str,
    query: str | None = None,
    intent: str | None = None,
    run_id: str | None = None,
    limit: int = 20,
    status_filter: str | None = None,
) -> object:
    if command == "run":
        orchestrator = ResearchOrchestrator(settings)
        request = RequestEnvelope(
            intent=intent,
            user_query=query or "",
            constraints={"review_target_run_id": run_id} if run_id else {},
        )
        response = orchestrator.run(request)
        return response.model_dump(mode="json")

    if command == "history":
        orchestrator = ResearchOrchestrator(settings)
        rows = orchestrator.history()
        if status_filter:
            rows = [row for row in rows if row.get("status") == status_filter]
        return rows[:limit]

    if command == "replay":
        orchestrator = ResearchOrchestrator(settings)
        return orchestrator.replay(run_id or "")

    if command == "resume":
        orchestrator = ResearchOrchestrator(settings)
        return orchestrator.resume(run_id or "")

    if command == "cancel":
        orchestrator = ResearchOrchestrator(settings)
        return orchestrator.cancel(run_id or "")

    if command == "cleanup":
        orchestrator = ResearchOrchestrator(settings)
        return orchestrator.cleanup()

    if command == "reindex-qdrant":
        orchestrator = ResearchOrchestrator(settings)
        return orchestrator.rebuild_qdrant()

    if command == "check":
        return run_checks(Path.cwd())

    if command == "status":
        return build_status(settings)

    if command == "doctor":
        return run_doctor(settings)

    raise ValueError(f"unsupported command: {command}")


def _run_chat_tui(
    settings: Settings,
    *,
    initial_intent: str | None,
    initial_run_id: str | None,
    thinking: str,
    events: str,
) -> int:
    llm_provider = ProviderRegistry(settings).default()
    task_router = TaskRouter(llm_provider)
    session = TuiSession(
        settings=settings,
        state=ChatSessionState(
            current_intent=initial_intent,
            review_target_run_id=initial_run_id,
            thinking_enabled=thinking == "on",
            events_mode=events,
        ),
        llm_provider=llm_provider,
        task_router=task_router,
        output_lines=[],
    )
    _append_tui_line(session, "meta", "Cognitrade terminal shell")
    _append_tui_line(session, "meta", "Enter to submit, Esc to clear, Ctrl+C to stop current run, Ctrl+D or /exit to quit.")
    _append_tui_line(session, "meta", "Up/Down recalls input history. Tab completes slash commands. Use /commands or F1 for popup help.")

    output_area = TextArea(
        text="",
        read_only=True,
        scrollbar=True,
        focusable=False,
        wrap_lines=True,
    )
    commands_popup = TextArea(
        text=_commands_popup_text(),
        read_only=True,
        focusable=False,
        scrollbar=True,
        wrap_lines=False,
        style="class:popup",
    )
    input_area = TextArea(
        height=3,
        prompt="> ",
        multiline=True,
        wrap_lines=True,
        history=InMemoryHistory(),
        completer=ChatCommandCompleter(session),
        complete_while_typing=True,
    )
    status_control = FormattedTextControl(lambda: _build_tui_status_text(session))
    status_bar = Window(content=status_control, height=1)
    hint_control = FormattedTextControl(lambda: _build_tui_hint_text(session))
    hint_bar = Window(content=hint_control, height=1)

    session.output_area = output_area
    session.input_area = input_area
    session.status_bar = status_bar
    session.input_hint_bar = hint_bar

    kb = KeyBindings()

    @kb.add("c-d")
    def _exit(event) -> None:
        if session.running:
            _append_tui_line(session, "warning", "当前有任务在运行，请先 Ctrl+C 或 /stop。")
            return
        event.app.exit(result=0)

    @kb.add("c-c")
    def _stop_or_clear(event) -> None:
        if session.running:
            _request_stop(session)
            return
        input_area.buffer.document = Document(text="")

    @kb.add("escape")
    def _clear_input(event) -> None:
        if session.commands_popup_visible:
            _toggle_commands_popup(session, visible=False)
            return
        input_area.buffer.document = Document(text="")

    @kb.add("f1")
    def _toggle_commands(event) -> None:
        _toggle_commands_popup(session)

    @kb.add("enter")
    def _submit(event) -> None:
        if input_area.buffer.document.text.strip() == "":
            return
        if input_area.buffer.complete_state:
            input_area.buffer.complete_state = None
        event.app.create_background_task(_submit_tui_input(session))

    root_container = FloatContainer(
        content=HSplit([output_area, status_bar, hint_bar, input_area]),
        floats=[
            Float(
                top=2,
                left=4,
                right=4,
                bottom=4,
                content=ConditionalContainer(
                    content=commands_popup,
                    filter=Condition(lambda: session.commands_popup_visible),
                ),
            )
        ],
    )
    app = Application(
        layout=Layout(root_container, focused_element=input_area),
        key_bindings=kb,
        full_screen=True,
        mouse_support=True,
        style=Style.from_dict(
            {
                "status": "reverse",
                "popup": "bg:#20252b #f4f4f4",
                "hint": "bg:#101317 #c2c8d0",
            }
        ),
    )
    session.app = app
    return app.run()


async def _submit_tui_input(session: TuiSession) -> None:
    if session.input_area is None:
        return
    text = session.input_area.text.strip()
    if not text:
        return
    if session.running:
        if text == "/stop":
            session.input_area.buffer.document = Document(text="")
            _append_tui_line(session, "input", f"> {text}")
            _request_stop(session)
            return
        _append_tui_line(session, "warning", "当前任务仍在运行。等待完成，或用 Ctrl+C / /stop 停止。")
        return
    session.input_area.buffer.document = Document(text="")
    _append_tui_line(session, "input", f"> {text}")
    session.running = True
    session.stop_requested = False
    session.run_status_label = "running"
    session.current_phase = "routing"
    session.current_gate = "pending"
    _refresh_tui(session)

    def _work() -> None:
        buffer = io.StringIO()
        try:
            with redirect_stdout(buffer):
                outcome = _handle_chat_line(
                    session.settings,
                    session.state,
                    text,
                    llm_provider=session.llm_provider,
                    task_router=session.task_router,
                    output_writer=lambda kind, message: _append_tui_line(session, kind, message),
                    stop_callback=lambda: _request_stop(session),
                )
            session.current_run_id = session.state.last_run_id
            captured = buffer.getvalue().strip()
            if captured:
                for line in captured.splitlines():
                    _append_tui_line(session, "meta", line)
            if outcome == "commands_popup":
                _toggle_commands_popup(session)
            if outcome == "exit" and session.app is not None:
                session.app.create_background_task(_exit_tui_app(session.app))
        finally:
            session.running = False
            session.stop_requested = False
            _refresh_tui(session)

    session.worker = threading.Thread(target=_work, daemon=True)
    session.worker.start()


def _append_tui_line(session: TuiSession, kind: str, text: str) -> None:
    if not text:
        return
    if kind == "answer_delta":
        if session.answer_stream_open and session.output_lines and session.output_lines[-1][0] == "answer_stream":
            last_kind, last_text = session.output_lines[-1]
            session.output_lines[-1] = (last_kind, f"{last_text}{text}")
        else:
            session.output_lines.append(("answer_stream", text))
            session.answer_stream_open = True
        session.output_lines = session.output_lines[-400:]
        _refresh_tui(session)
        return
    if kind == "answer_done":
        session.answer_stream_open = False
        _refresh_tui(session)
        return
    session.answer_stream_open = False
    _update_tui_status_state(session, kind, text)
    session.output_lines.append((kind, text))
    session.output_lines = session.output_lines[-400:]
    _refresh_tui(session)


def _refresh_tui(session: TuiSession) -> None:
    if session.output_area is not None:
        session.output_area.text = _render_tui_output(session.output_lines)
        session.output_area.buffer.cursor_position = len(session.output_area.text)
    if session.app is not None:
        session.app.invalidate()


def _render_tui_output(lines: list[tuple[str, str]]) -> str:
    rendered: list[str] = []
    for kind, text in lines:
        if kind == "thinking":
            _append_tui_block(rendered, "thinking", text)
        elif kind == "status":
            _append_tui_block(rendered, "status", text)
        elif kind == "answer":
            _append_tui_block(rendered, "answer", text)
        elif kind == "answer_stream":
            _append_tui_block(rendered, "answer", text)
        elif kind == "warning":
            _append_tui_block(rendered, "warning", text)
        elif kind == "event":
            _append_tui_block(rendered, "event", text)
        elif kind == "input":
            if rendered and rendered[-1] != "":
                rendered.append("")
            rendered.append(text)
        else:
            rendered.append(text)
    return "\n".join(rendered)


def _append_tui_block(rendered: list[str], title: str, text: str) -> None:
    if rendered and rendered[-1] != "":
        rendered.append("")
    rendered.append(f"┌ {title}")
    for line in str(text).splitlines() or [""]:
        rendered.append(f"│ {line}")
    rendered.append(f"└ {'─' * max(8, len(title))}")


def _build_tui_status_text(session: TuiSession) -> list[tuple[str, str]]:
    intent = session.state.current_intent or "auto"
    run_id = session.current_run_id or session.state.last_run_id or "-"
    mode = "running" if session.running else session.run_status_label
    clarify = "pending" if session.state.pending_clarification_question else "clear"
    return [
        ("class:status", f" mode: codex "),
        ("class:status", f" intent: {intent} "),
        ("class:status", f" run: {run_id} "),
        ("class:status", f" phase: {session.current_phase} "),
        ("class:status", f" gate: {session.current_gate} "),
        ("class:status", f" clarify: {clarify} "),
        ("class:status", f" thinking: {'on' if session.state.thinking_enabled else 'off'} "),
        ("class:status", f" events: {session.state.events_mode} "),
        ("class:status", f" state: {mode} "),
    ]


def _build_tui_hint_text(session: TuiSession) -> list[tuple[str, str]]:
    if session.commands_popup_visible:
        message = " popup: F1 or Esc close | Enter submit | Tab complete "
    elif session.running:
        message = " running: Ctrl+C or /stop | Tab complete | F1 commands "
    elif session.state.pending_clarification_question:
        message = " clarification pending: next plain message will continue the same request | F1 commands "
    else:
        message = " ready: Enter submit | Tab complete | Up/Down history | F1 commands "
    return [("class:hint", message)]


def _request_stop(session: TuiSession) -> None:
    if not session.running:
        return
    session.stop_requested = True
    session.run_status_label = "cancelling"
    session.current_phase = "cancelling"
    run_id = session.current_run_id or session.state.last_run_id
    if run_id:
        try:
            payload = _execute_cli_command(session.settings, command="cancel", run_id=run_id)
            if isinstance(payload, dict):
                _append_tui_line(session, "status", f"stop requested: {payload.get('message') or 'cancelling'}")
        except Exception as exc:  # pragma: no cover - UI fallback
            _append_tui_line(session, "warning", f"stop failed: {exc}")
    else:
        _append_tui_line(session, "warning", "当前没有可取消的 run_id。")


async def _exit_tui_app(app: Application) -> None:
    app.exit(result=0)


def _toggle_commands_popup(session: TuiSession, *, visible: bool | None = None) -> None:
    session.commands_popup_visible = (not session.commands_popup_visible) if visible is None else visible
    _refresh_tui(session)


def _update_tui_status_state(session: TuiSession, kind: str, text: str) -> None:
    if kind == "status":
        if text.startswith("gate: "):
            session.current_gate = text.removeprefix("gate: ").strip() or "pending"
            return
        session.current_phase = text.strip() or session.current_phase
        return
    if kind == "meta":
        if text.startswith("status: "):
            session.run_status_label = text.split(": ", 1)[1].strip() or session.run_status_label
            return
        if text.startswith("run_id: "):
            session.current_run_id = text.split(": ", 1)[1].strip() or session.current_run_id
            return
    if kind == "warning":
        session.run_status_label = "warning"


def _chat_completion_current_token(text_before_cursor: str) -> str:
    stripped = text_before_cursor.rstrip()
    if not stripped:
        return ""
    if text_before_cursor.endswith(" "):
        return ""
    return stripped.split()[-1]


def _chat_completion_candidates(text_before_cursor: str, state: ChatSessionState) -> list[str]:
    stripped = text_before_cursor.lstrip()
    if not stripped.startswith("/"):
        return []
    try:
        parts = shlex.split(stripped)
    except ValueError:
        parts = stripped.split()
    if not parts:
        return CHAT_COMMANDS
    command = parts[0].lower()
    if len(parts) == 1 and not text_before_cursor.endswith(" "):
        return CHAT_COMMANDS

    recent_run_ids = [value for value in [state.last_run_id, state.review_target_run_id] if value]
    unique_run_ids: list[str] = []
    for value in ["last", *recent_run_ids]:
        if value and value not in unique_run_ids:
            unique_run_ids.append(value)

    if command == "/intent":
        return ["auto", "stock_check", "sector_research", "distill_kol_logic", "review_thesis"]
    if command == "/thinking":
        return ["on", "off"]
    if command == "/events":
        return ["off", "compact", "full"]
    if command == "/run-id":
        return ["clear", *recent_run_ids]
    if command in {"/replay", "/resume", "/cancel"}:
        return unique_run_ids
    if command == "/history":
        return ["10", "20", "50", "completed", "failed", "cancelled", "timed_out", "needs_clarification"]
    if command == "/review":
        if len(parts) <= 2:
            return unique_run_ids
        return []
    return []


def _handle_chat_line(
    settings: Settings,
    state: ChatSessionState,
    line: str,
    *,
    llm_provider: OpenAIProvider,
    task_router: TaskRouter,
    output_writer=None,
    stop_callback=None,
) -> str | None:
    parsed = _parse_chat_input(line)
    action = parsed.get("action")

    if action == "noop":
        return None
    if action == "error":
        _emit_chat_text(output_writer, "meta", str(parsed.get("message") or "Invalid command."))
        return None
    if action == "commands":
        if output_writer is not None:
            _emit_chat_text(output_writer, "status", "commands popup toggled")
            return "commands_popup"
        _print_chat_help(output_writer=output_writer)
        return None
    if action == "help":
        _print_chat_help(output_writer=output_writer)
        return None
    if action == "exit":
        return "exit"
    if action == "stop":
        if stop_callback is not None:
            stop_callback()
            _emit_chat_text(output_writer, "status", "stop requested")
            return None
        resolved_run_id = state.last_run_id
        if not resolved_run_id:
            _emit_chat_text(output_writer, "warning", "no active or recent run to stop")
            return None
        payload = _execute_cli_command(settings, command="cancel", run_id=resolved_run_id)
        _print_payload(payload, as_json=False, output_writer=output_writer)
        return None
    if action == "set_intent":
        state.current_intent = parsed.get("intent")
        _emit_chat_text(output_writer, "status", f"intent: {state.current_intent or 'auto'}")
        return None
    if action == "set_run_id":
        state.review_target_run_id = parsed.get("run_id")
        _emit_chat_text(output_writer, "status", f"review_target_run_id: {state.review_target_run_id or 'unset'}")
        return None
    if action == "clear":
        state.current_intent = None
        state.review_target_run_id = None
        _clear_pending_clarification(state)
        _emit_chat_text(output_writer, "status", "session defaults cleared")
        return None
    if action == "show_last":
        _emit_chat_text(output_writer, "meta", f"last_run_id: {state.last_run_id or 'unset'}")
        _emit_chat_text(output_writer, "meta", f"intent: {state.current_intent or 'auto'}")
        _emit_chat_text(output_writer, "meta", f"review_target_run_id: {state.review_target_run_id or 'unset'}")
        _emit_chat_text(output_writer, "meta", f"thinking: {'on' if state.thinking_enabled else 'off'}")
        _emit_chat_text(output_writer, "meta", f"events: {state.events_mode}")
        return None
    if action == "set_thinking":
        state.thinking_enabled = bool(parsed.get("enabled"))
        _emit_chat_text(output_writer, "status", f"thinking: {'on' if state.thinking_enabled else 'off'}")
        return None
    if action == "set_events":
        state.events_mode = str(parsed.get("events_mode") or "compact")
        _emit_chat_text(output_writer, "status", f"events: {state.events_mode}")
        return None

    if action in {"replay", "resume", "cancel"}:
        resolved_run_id = _resolve_chat_run_id(str(parsed.get("run_id") or ""), state)
        if not resolved_run_id:
            _emit_chat_text(output_writer, "warning", "run_id is required. Use an explicit run_id or `last` after a successful run.")
            return None
        payload = _execute_cli_command(settings, command=action, run_id=resolved_run_id)
        _print_payload(payload, as_json=False, output_writer=output_writer)
        state.last_run_id = resolved_run_id
        return None

    if action in {"history", "status", "doctor", "check", "cleanup", "reindex-qdrant"}:
        payload = _execute_cli_command(
            settings,
            command=action,
            limit=int(parsed.get("limit") or 20),
            status_filter=parsed.get("status"),
        )
        _print_payload(payload, as_json=False, output_writer=output_writer)
        return None

    if action == "run":
        intent = parsed.get("intent") or state.current_intent
        run_id = parsed.get("run_id")
        query = str(parsed.get("query") or "")
        if _should_resume_pending_clarification(query=query, intent=intent, state=state):
            query, intent, run_id = _merge_pending_clarification(query=query, intent=intent, run_id=run_id, state=state)
        if intent is None and _should_answer_basic_chat(query=query, task_router=task_router):
            _print_streamed_basic_chat_answer(query=query, llm_provider=llm_provider, output_writer=output_writer)
            return None
        if intent == "review_thesis":
            run_id = run_id or state.review_target_run_id
            if not run_id:
                _emit_chat_text(output_writer, "warning", "review_thesis requires `/run-id <target_run_id>` or `/review <run_id> <query>`.")
                return None
        payload = _run_chat_research(
            settings,
            query=query,
            intent=intent,
            run_id=run_id,
            thinking_enabled=state.thinking_enabled,
            events_mode=state.events_mode,
            output_writer=output_writer,
        )
        _print_chat_result_payload(
            payload,
            llm_provider=llm_provider,
            query=query,
            requested_intent=intent,
            output_writer=output_writer,
        )
        if isinstance(payload, dict) and payload.get("run_id"):
            state.last_run_id = str(payload.get("run_id"))
        if isinstance(payload, dict) and payload.get("status") == "needs_clarification":
            _store_pending_clarification(
                state,
                query=query,
                intent=intent,
                run_id=str(run_id) if run_id else None,
                payload=payload,
            )
        else:
            _clear_pending_clarification(state)
        if parsed.get("intent") == "review_thesis" and run_id:
            state.review_target_run_id = str(run_id)
        return None

    _emit_chat_text(output_writer, "warning", "Unsupported command.")
    return None


def _parse_chat_input(line: str) -> dict[str, object]:
    stripped = line.strip()
    if not stripped:
        return {"action": "noop"}
    if not stripped.startswith("/"):
        return {"action": "run", "query": stripped}
    try:
        parts = shlex.split(stripped)
    except ValueError as exc:
        return {"action": "error", "message": f"command parse error: {exc}"}
    if not parts:
        return {"action": "noop"}

    command = parts[0].lower()
    if command in {"/exit", "/quit"}:
        return {"action": "exit"}
    if command == "/commands":
        return {"action": "commands"}
    if command == "/help":
        return {"action": "help"}
    if command == "/last":
        return {"action": "show_last"}
    if command == "/clear":
        return {"action": "clear"}
    if command == "/stop":
        return {"action": "stop"}
    if command == "/intent":
        if len(parts) != 2:
            return {"action": "error", "message": "usage: /intent <intent|auto>"}
        value = parts[1].strip()
        return {"action": "set_intent", "intent": None if value == "auto" else value}
    if command == "/run-id":
        if len(parts) != 2:
            return {"action": "error", "message": "usage: /run-id <run_id|clear>"}
        value = parts[1].strip()
        return {"action": "set_run_id", "run_id": None if value == "clear" else value}
    if command == "/thinking":
        if len(parts) != 2 or parts[1] not in {"on", "off"}:
            return {"action": "error", "message": "usage: /thinking <on|off>"}
        return {"action": "set_thinking", "enabled": parts[1] == "on"}
    if command == "/events":
        if len(parts) != 2 or parts[1] not in {"off", "compact", "full"}:
            return {"action": "error", "message": "usage: /events <off|compact|full>"}
        return {"action": "set_events", "events_mode": parts[1]}
    if command == "/history":
        limit = 20
        status = None
        for token in parts[1:]:
            if token.isdigit() and limit == 20:
                limit = int(token)
                continue
            if status is None:
                status = token
                continue
            return {"action": "error", "message": "usage: /history [limit] [status]"}
        return {"action": "history", "limit": limit, "status": status}
    if command in {"/status", "/doctor", "/check", "/cleanup"}:
        return {"action": command.removeprefix("/")}
    if command in {"/reindex", "/reindex-qdrant"}:
        return {"action": "reindex-qdrant"}
    if command in {"/replay", "/resume", "/cancel"}:
        if len(parts) != 2:
            return {"action": "error", "message": f"usage: {command} <run_id|last>"}
        return {"action": command.removeprefix("/"), "run_id": parts[1]}
    if command == "/run":
        query = stripped[len("/run") :].strip()
        if not query:
            return {"action": "error", "message": "usage: /run <query>"}
        return {"action": "run", "query": query}
    if command == "/review":
        if len(parts) < 3:
            return {"action": "error", "message": "usage: /review <run_id> <query>"}
        return {
            "action": "run",
            "intent": "review_thesis",
            "run_id": parts[1],
            "query": " ".join(parts[2:]).strip(),
        }
    return {"action": "error", "message": f"unknown command: {command}"}


def _resolve_chat_run_id(token: str, state: ChatSessionState) -> str | None:
    if token == "last":
        return state.last_run_id
    return token or None


def _should_resume_pending_clarification(*, query: str, intent: object, state: ChatSessionState) -> bool:
    return (
        state.pending_clarification_query is not None
        and not str(query).strip().startswith("/")
        and intent is None
    )


def _merge_pending_clarification(
    *,
    query: str,
    intent: object,
    run_id: object,
    state: ChatSessionState,
) -> tuple[str, str | None, str | None]:
    merged_query = f"{state.pending_clarification_query}\n补充信息: {query.strip()}"
    merged_intent = str(intent) if isinstance(intent, str) and intent else state.pending_clarification_intent
    merged_run_id = str(run_id) if isinstance(run_id, str) and run_id else state.pending_clarification_run_id
    return merged_query, merged_intent, merged_run_id


def _store_pending_clarification(
    state: ChatSessionState,
    *,
    query: str,
    intent: str | None,
    run_id: str | None,
    payload: dict,
) -> None:
    state.pending_clarification_query = query
    state.pending_clarification_intent = intent
    state.pending_clarification_run_id = run_id
    clarification = payload.get("clarification")
    if isinstance(clarification, dict):
        question = clarification.get("question")
        state.pending_clarification_question = str(question) if question else None
    else:
        state.pending_clarification_question = None


def _clear_pending_clarification(state: ChatSessionState) -> None:
    state.pending_clarification_query = None
    state.pending_clarification_intent = None
    state.pending_clarification_run_id = None
    state.pending_clarification_question = None


def _should_answer_basic_chat(*, query: str, task_router: TaskRouter) -> bool:
    if _is_basic_chat_query(query):
        return True
    if _looks_research_like(query):
        return False
    decision = task_router.identify(RequestEnvelope(user_query=query))
    return decision.intent is None


def _is_basic_chat_query(query: str) -> bool:
    lowered = query.strip().lower()
    basic_markers = (
        "你是谁",
        "你能做什么",
        "怎么用",
        "help",
        "who are you",
        "what can you do",
        "how do i use this",
        "hello",
        "hi",
        "你好",
        "嗨",
        "谢谢",
        "thanks",
    )
    return any(marker in lowered for marker in basic_markers)


def _looks_research_like(query: str) -> bool:
    lowered = query.lower()
    research_markers = (
        "股票",
        "个股",
        "板块",
        "行业",
        "主题",
        "复核",
        "后验",
        "蒸馏",
        "雪球",
        "公告",
        "财报",
        "sector",
        "stock",
        "ticker",
        "industry",
        "theme",
        "review",
        "distill",
        "xueqiu",
        "jsonl",
    )
    if any(marker in query for marker in research_markers[:9]):
        return True
    if any(marker in lowered for marker in research_markers[9:]):
        return True
    return any(char.isdigit() for char in query) and len("".join(ch for ch in query if ch.isdigit())) >= 6


def _answer_basic_chat(*, query: str, llm_provider: OpenAIProvider) -> str:
    if "你是谁" in query or "who are you" in query.lower():
        return (
            "我是 Cognitrade 的命令行研究助手。当前主要支持 A 股个股研究、板块研究、"
            "KOL/雪球蒸馏，以及对历史结论做后验复核。输入研究问题直接运行，`/help` 看交互指令。"
        )
    if llm_provider.available:
        try:
            return llm_provider.complete(
                system_prompt=(
                    "You are the lightweight chat shell assistant for Cognitrade.\n"
                    "Answer short general questions about identity, usage, and capabilities.\n"
                    "Keep replies concise and practical."
                ),
                user_prompt=query,
                temperature=0.2,
                reasoning_effort="low",
            ).strip()
        except LLMProviderError:
            pass
    return "当前交互壳主要支持研究任务和命令控制。你可以直接输入研究问题，或用 `/help` 查看支持的斜杠指令。"


def _print_streamed_basic_chat_answer(*, query: str, llm_provider: OpenAIProvider, output_writer=None) -> None:
    if "你是谁" in query or "who are you" in query.lower():
        _emit_chat_text(output_writer, "answer", _answer_basic_chat(query=query, llm_provider=llm_provider))
        return
    if llm_provider.available:
        try:
            text_started = False

            def _on_text_delta(delta: str) -> None:
                nonlocal text_started
                text_started = True
                _emit_chat_text(output_writer, "answer_delta", delta)

            llm_provider.stream_complete(
                system_prompt=(
                    "You are the lightweight chat shell assistant for Cognitrade.\n"
                    "Answer short general questions about identity, usage, and capabilities.\n"
                    "Keep replies concise and practical."
                ),
                user_prompt=query,
                on_text_delta=_on_text_delta,
                temperature=0.2,
                reasoning_effort="low",
            )
            if text_started:
                _emit_chat_text(output_writer, "answer_done", "")
                return
        except LLMProviderError:
            pass
    _emit_chat_text(output_writer, "answer", _answer_basic_chat(query=query, llm_provider=llm_provider))


def _run_chat_research(
    settings: Settings,
    *,
    query: str,
    intent: str | None,
    run_id: str | None,
    thinking_enabled: bool,
    events_mode: str,
    output_writer=None,
) -> object:
    presenter = ChatStreamPresenter(thinking_enabled=thinking_enabled, events_mode=events_mode)

    def _listener(event: RunEvent) -> None:
        _chat_event_listener(event, presenter=presenter, output_writer=output_writer)

    orchestrator = ResearchOrchestrator(settings, event_listener=_listener)
    request = RequestEnvelope(
        intent=intent,
        user_query=query,
        constraints={"review_target_run_id": run_id} if run_id else {},
    )
    response = orchestrator.run(request)
    return response.model_dump(mode="json")


def _chat_event_listener(event: RunEvent, *, presenter: ChatStreamPresenter, output_writer=None) -> None:
    for chunk in presenter.on_event(event):
        _print_display_chunk(chunk, output_writer=output_writer)


def _print_chat_help(*, output_writer=None) -> None:
    for line in [
        "Commands:",
        "  /help                         show this help",
        "  /exit                         exit interactive mode",
        "  /stop                         stop the current run from chat",
        "  /intent <intent|auto>        set default intent for plain text input",
        "  /run-id <run_id|clear>       set review target run id for review_thesis",
        "  /thinking <on|off>           toggle visible thinking stream",
        "  /events <off|compact|full>   control event visibility",
        "  /run <query>                 run a query explicitly",
        "  /review <run_id> <query>     run review_thesis against a prior run",
        "  /history [limit] [status]    show recent runs",
        "  /status                      show project status summary",
        "  /doctor                      show environment diagnostics",
        "  /check                       run local checks",
        "  /replay <run_id|last>        replay a run",
        "  /resume <run_id|last>        inspect resumability",
        "  /cancel <run_id|last>        cancel a run",
        "  /cleanup                     run retention cleanup",
        "  /reindex                     rebuild local Qdrant index",
        "  /last                        show last run id and current session defaults",
        "  /clear                       clear current intent and review target run id",
    ]:
        _emit_chat_text(output_writer, "meta", line)


def _print_chat_result_payload(
    payload: object,
    *,
    llm_provider: OpenAIProvider,
    query: str,
    requested_intent: str | None,
    output_writer=None,
) -> None:
    if not isinstance(payload, dict):
        _print_payload(payload, as_json=False, output_writer=output_writer)
        return
    status = str(payload.get("status") or "")
    if status == "needs_clarification":
        clarification = payload.get("clarification")
        if isinstance(clarification, dict) and clarification.get("question"):
            _emit_chat_text(output_writer, "thinking", "当前信息还不足以安全继续，我先停在澄清点，等你补一条关键约束。")
        if isinstance(clarification, dict) and clarification.get("question"):
            _emit_chat_text(output_writer, "answer", str(clarification["question"]))
        else:
            _emit_chat_text(output_writer, "answer", str(payload.get("message") or "Clarification required."))
        if payload.get("run_id"):
            _emit_chat_text(output_writer, "meta", f"run_id: {payload['run_id']}")
        _emit_chat_text(output_writer, "meta", f"status: {status}")
        _emit_chat_text(output_writer, "meta", f"intent: {payload.get('intent') or requested_intent or 'auto'}")
        return
    display_summary = payload.get("display_summary")
    result = payload.get("result")
    conclusion = None
    if isinstance(result, dict):
        maybe_conclusion = result.get("conclusion")
        if isinstance(maybe_conclusion, str) and maybe_conclusion.strip():
            conclusion = maybe_conclusion.strip()
    summary_text = display_summary.strip() if isinstance(display_summary, str) and display_summary.strip() else None
    if isinstance(result, dict) and llm_provider.available and status in {"completed", "failed", "timed_out", "cancelled"}:
        if _stream_chat_rendered_result(
            llm_provider=llm_provider,
            payload=payload,
            query=query,
            requested_intent=requested_intent,
            output_writer=output_writer,
        ):
            if payload.get("run_id"):
                _emit_chat_text(output_writer, "meta", f"run_id: {payload['run_id']}")
            if payload.get("status"):
                _emit_chat_text(output_writer, "meta", f"status: {payload['status']}")
            if payload.get("intent"):
                _emit_chat_text(output_writer, "meta", f"intent: {payload['intent']}")
            return
    if summary_text:
        _emit_chat_text(output_writer, "answer", summary_text)
    elif conclusion:
        _emit_chat_text(output_writer, "answer", conclusion)
    else:
        _print_payload(payload, as_json=False, output_writer=output_writer)
        return
    if payload.get("run_id"):
        _emit_chat_text(output_writer, "meta", f"run_id: {payload['run_id']}")
    if payload.get("status"):
        _emit_chat_text(output_writer, "meta", f"status: {payload['status']}")
    if payload.get("intent"):
        _emit_chat_text(output_writer, "meta", f"intent: {payload['intent']}")


def _print_display_chunk(chunk: DisplayChunk, *, output_writer=None) -> None:
    _emit_chat_text(output_writer, chunk.kind, chunk.text)


def _stream_chat_rendered_result(
    *,
    llm_provider: OpenAIProvider,
    payload: dict,
    query: str,
    requested_intent: str | None,
    output_writer=None,
) -> bool:
    result = payload.get("result")
    if not isinstance(result, dict):
        return False
    effective_intent = str(payload.get("intent") or requested_intent or "auto")
    status = str(payload.get("status") or "unknown")
    display_summary = payload.get("display_summary")
    try:
        text_started = False

        def _on_text_delta(delta: str) -> None:
            nonlocal text_started
            text_started = True
            _emit_chat_text(output_writer, "answer_delta", delta)

        llm_provider.stream_complete(
            system_prompt=(
                "You are the terminal chat presenter for Cognitrade.\n"
                "Turn the structured research result into a concise direct reply for a CLI user.\n"
                "Do not invent facts. Preserve caveats, review verdicts, and exact uncertainty.\n"
                "Prefer one tight paragraph or a couple of short lines.\n"
                "Start directly with the takeaway; do not mention JSON or internal payload structure."
            ),
            user_prompt=(
                f"Intent: {effective_intent}\n"
                f"User query: {query}\n"
                f"Run status: {status}\n"
                f"Display summary: {display_summary or 'N/A'}\n"
                f"Structured result: {json.dumps(result, ensure_ascii=False)}"
            ),
            on_text_delta=_on_text_delta,
            temperature=0.2,
            reasoning_effort="low",
        )
        if text_started:
            _emit_chat_text(output_writer, "answer_done", "")
            return True
    except LLMProviderError:
        return False
    return False


def _print_payload(payload: object, *, as_json: bool, output_writer=None) -> int:
    if as_json:
        _emit_chat_text(output_writer, "meta", json.dumps(payload, ensure_ascii=True, indent=2))
        return 0
    if isinstance(payload, list):
        _print_rows(payload, output_writer=output_writer)
        return 0
    if isinstance(payload, dict):
        if _print_status_payload(payload, output_writer=output_writer):
            return 0
        if _print_doctor_payload(payload, output_writer=output_writer):
            return 0
        if _print_replay_payload(payload, output_writer=output_writer):
            return 0
        display_summary = payload.get("display_summary")
        if display_summary:
            _emit_chat_text(output_writer, "meta", str(display_summary))
        else:
            _emit_chat_text(output_writer, "meta", str(payload.get("message", json.dumps(payload, ensure_ascii=True))))
        if "run_id" in payload:
            _emit_chat_text(output_writer, "meta", f"run_id: {payload['run_id']}")
        if payload.get("status"):
            _emit_chat_text(output_writer, "meta", f"status: {payload['status']}")
        if payload.get("intent"):
            _emit_chat_text(output_writer, "meta", f"intent: {payload['intent']}")
        if payload.get("result"):
            _emit_chat_text(output_writer, "meta", json.dumps(payload["result"], ensure_ascii=True, indent=2))
        return 0
    _emit_chat_text(output_writer, "meta", json.dumps(payload, ensure_ascii=True, indent=2))
    return 0


def _print_rows(rows: list[object], *, output_writer=None) -> None:
    if not rows:
        _emit_chat_text(output_writer, "meta", "No runs.")
        return
    for row in rows:
        if not isinstance(row, dict):
            _emit_chat_text(output_writer, "meta", json.dumps(row, ensure_ascii=True))
            continue
        run_id = str(row.get("run_id") or "unknown")
        status = str(row.get("status") or "unknown")
        intent = str(row.get("intent") or "unknown")
        display_summary = str(row.get("display_summary") or row.get("message") or "").strip()
        line = f"{run_id}  [{status}]  {intent}"
        if display_summary:
            line += f"  {display_summary}"
        _emit_chat_text(output_writer, "meta", line)


def _print_status_payload(payload: dict, *, output_writer=None) -> bool:
    if "recent_runs" not in payload or "status_counts" not in payload:
        return False
    _emit_chat_text(output_writer, "meta", f"project_root: {payload.get('project_root')}")
    _emit_chat_text(output_writer, "meta", f"provider: {payload.get('provider_name')} / model: {payload.get('default_model')}")
    _emit_chat_text(output_writer, "meta", f"qdrant_mode: {payload.get('qdrant_mode')} / collection: {payload.get('qdrant_collection_name')}")
    _emit_chat_text(output_writer, "meta", f"total_runs: {payload.get('total_runs')}")
    status_counts = payload.get("status_counts") or {}
    if status_counts:
        counts_line = ", ".join(f"{key}={value}" for key, value in sorted(status_counts.items()))
        _emit_chat_text(output_writer, "meta", f"status_counts: {counts_line}")
    recent_runs = payload.get("recent_runs") or []
    if recent_runs:
        _emit_chat_text(output_writer, "meta", "recent_runs:")
        for row in recent_runs:
            run_id = str(row.get("run_id") or "unknown")
            status = str(row.get("status") or "unknown")
            intent = str(row.get("intent") or "unknown")
            display_summary = str(row.get("display_summary") or row.get("message") or "").strip()
            line = f"  {run_id}  [{status}]  {intent}"
            if display_summary:
                line += f"  {display_summary}"
            _emit_chat_text(output_writer, "meta", line)
    return True


def _print_replay_payload(payload: dict, *, output_writer=None) -> bool:
    if "request" not in payload or "events" not in payload or "blackboard" not in payload:
        return False
    request = payload.get("request") or {}
    summary = payload.get("summary") or {}
    _emit_chat_text(output_writer, "meta", f"run_id: {payload.get('run_id') or 'unknown_run'}")
    _emit_chat_text(output_writer, "meta", f"intent: {request.get('intent') or 'unknown'}")
    _emit_chat_text(output_writer, "meta", f"query: {request.get('user_query') or ''}")
    if payload.get("display_summary"):
        _emit_chat_text(output_writer, "meta", f"summary: {payload['display_summary']}")
    if summary.get("status"):
        _emit_chat_text(output_writer, "meta", f"status: {summary.get('status')}")
    _emit_chat_text(output_writer, "meta", f"events: {len(payload.get('events') or [])}")
    _emit_chat_text(output_writer, "meta", f"blackboard_entries: {len(payload.get('blackboard') or [])}")
    result = payload.get("result")
    if result:
        _emit_chat_text(output_writer, "meta", json.dumps(result, ensure_ascii=True, indent=2))
    return True


def _print_doctor_payload(payload: dict, *, output_writer=None) -> bool:
    checks = payload.get("checks")
    status = payload.get("status")
    if not isinstance(checks, list) or not isinstance(status, str):
        return False
    _emit_chat_text(output_writer, "meta", f"doctor_status: {status}")
    grouped: dict[str, list[dict]] = {"failed": [], "passed": [], "skipped": []}
    for item in checks:
        if not isinstance(item, dict):
            continue
        key = str(item.get("status") or "skipped")
        grouped.setdefault(key, []).append(item)
    for group_name in ("failed", "passed", "skipped"):
        rows = grouped.get(group_name) or []
        if not rows:
            continue
        _emit_chat_text(output_writer, "meta", f"{group_name}:")
        for row in rows:
            name = str(row.get("name") or "unknown_check")
            detail = row.get("detail") or row.get("path") or ""
            suffix = f"  {detail}" if detail else ""
            _emit_chat_text(output_writer, "meta", f"  {name}{suffix}")
    return True


def _emit_chat_text(output_writer, kind: str, text: str) -> None:
    if output_writer is not None:
        output_writer(kind, text)
        return
    if kind == "answer_delta":
        sys.stdout.write(text)
        sys.stdout.flush()
        return
    if kind == "answer_done":
        print()
        return
    if kind in {"thinking", "status", "event", "answer"}:
        print(kind, flush=True)
        print(f"  {text}", flush=True)
        return
    print(text, flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
