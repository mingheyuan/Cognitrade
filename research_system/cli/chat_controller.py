from __future__ import annotations

from research_system.cli.chat_actions import handle_chat_cli_action, handle_chat_meta_action
from research_system.cli.chat_commands import parse_chat_input
from research_system.cli.chat_dispatch import (
    answer_basic_chat,
    decide_chat_dispatch,
    print_streamed_basic_chat_answer,
    should_answer_basic_chat,
)
from research_system.cli.chat_event_adapter import build_chat_presenter, display_chunk_to_update, event_to_display_chunks
from research_system.cli.chat_run_flow import run_chat_query_flow
from research_system.cli.chat_result import (
    dispatch_chat_update,
    print_chat_result_payload,
    print_display_chunk,
    stream_chat_rendered_result,
)
from research_system.cli.chat_updates import meta_update, warning_update
from research_system.runtime.chat_presenter import DisplayChunk
from research_system.runtime.orchestrator import ResearchOrchestrator
from research_system.runtime.provider_registry import OpenAIProvider
from research_system.runtime.settings import Settings
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.runtime import RunEvent
from research_system.services.task_router import ChatDispatchDecision, TaskRouter


def run_chat_research(
    settings: Settings,
    *,
    query: str,
    intent: str | None,
    run_id: str | None,
    thinking_enabled: bool,
    events_mode: str,
    dispatch: ChatDispatchDecision | None = None,
    output_writer=None,
) -> object:
    presenter = build_chat_presenter(thinking_enabled=thinking_enabled, events_mode=events_mode)

    def _listener(event: RunEvent) -> None:
        chat_event_listener(event, presenter=presenter, output_writer=output_writer)

    orchestrator = ResearchOrchestrator(settings, event_listener=_listener)
    constraints: dict[str, object] = {}
    if run_id:
        constraints["review_target_run_id"] = run_id
    if dispatch is not None:
        constraints["chat_dispatch"] = {
            "mode": dispatch.mode,
            "intent": dispatch.intent,
            "reason": dispatch.reason,
            "source": "chat_dispatch",
        }
    request = RequestEnvelope(intent=intent, user_query=query, constraints=constraints)
    response = orchestrator.run(request)
    return response.model_dump(mode="json")


def chat_event_listener(event: RunEvent, *, presenter, output_writer=None) -> None:
    for chunk in event_to_display_chunks(event, presenter=presenter):
        dispatch_chat_update(output_writer, display_chunk_to_update(chunk))


def print_chat_help(*, output_writer=None) -> None:
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
        dispatch_chat_update(output_writer, meta_update(line))


def handle_chat_line(
    settings: Settings,
    state,
    line: str,
    *,
    llm_provider: OpenAIProvider,
    task_router: TaskRouter,
    cli_command_runner=None,
    payload_printer=None,
    output_writer=None,
    stop_callback=None,
    chat_dispatch_fn=None,
    basic_answer_stream_fn=None,
    run_chat_research_fn=None,
    print_chat_result_payload_fn=None,
) -> str | None:
    emit_update = dispatch_chat_update
    dispatch_fn = chat_dispatch_fn or decide_chat_dispatch
    basic_answer_fn = basic_answer_stream_fn or (
        lambda **kwargs: print_streamed_basic_chat_answer(emit_text=emit_update, **kwargs)
    )
    run_research_fn = run_chat_research_fn or run_chat_research
    if print_chat_result_payload_fn is not None:
        print_result_fn = print_chat_result_payload_fn
    else:
        def print_result_fn(payload, *, llm_provider, query, requested_intent, output_writer=None):
            return print_chat_result_payload(
                payload,
                llm_provider=llm_provider,
                query=query,
                requested_intent=requested_intent,
                payload_printer=payload_printer,
                output_writer=output_writer,
            )

    parsed = parse_chat_input(line)
    action = parsed.get("action")

    outcome = handle_chat_meta_action(
        action=action,
        parsed=parsed,
        state=state,
        settings=settings,
        emit_text=emit_update,
        cli_command_runner=cli_command_runner,
        payload_printer=payload_printer,
        output_writer=output_writer,
        stop_callback=stop_callback,
        print_chat_help_fn=print_chat_help,
    )
    if outcome != "continue":
        return outcome

    if cli_command_runner is None:
        raise RuntimeError("cli_command_runner is required for chat command execution")
    if payload_printer is None:
        raise RuntimeError("payload_printer is required for chat command execution")

    if handle_chat_cli_action(
        action=action,
        parsed=parsed,
        state=state,
        settings=settings,
        emit_text=emit_update,
        cli_command_runner=cli_command_runner,
        payload_printer=payload_printer,
        output_writer=output_writer,
    ):
        return None

    if run_chat_query_flow(
        parsed=parsed,
        state=state,
        settings=settings,
        llm_provider=llm_provider,
        task_router=task_router,
        emit_text=emit_update,
        dispatch_fn=dispatch_fn,
        basic_answer_fn=basic_answer_fn,
        run_research_fn=run_research_fn,
        print_result_fn=print_result_fn,
        output_writer=output_writer,
    ):
        return None

    dispatch_chat_update(output_writer, warning_update("Unsupported command."))
    return None
