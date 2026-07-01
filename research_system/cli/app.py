from __future__ import annotations

import argparse
from pathlib import Path

from prompt_toolkit.output.win32 import NoConsoleScreenBufferError

from research_system.cli.chat_controller import (
    handle_chat_line as controller_handle_chat_line,
    run_chat_research as controller_run_chat_research,
)
from research_system.cli.chat_fallback import run_chat_fallback
from research_system.cli.chat_rendering import build_tui_hint_text, build_tui_status_text, render_tui_output
from research_system.cli.chat_shell import run_chat_tui
from research_system.cli.chat_types import ChatSessionState, TuiSession
from research_system.cli.chat_dispatch import decide_chat_dispatch, print_streamed_basic_chat_answer
from research_system.cli.chat_result import print_chat_result_payload
from research_system.cli.research_request_payloads import build_research_request
from research_system.cli.chat_updates import dispatch_transcript_update
from research_system.cli.chat_updates import TranscriptUpdate
from research_system.cli.cli_command_dispatch import execute_cli_payload_command
from research_system.cli.payload_rendering import (
    print_doctor_payload,
    print_payload,
)
from research_system.runtime.checks import run_checks
from research_system.runtime.doctor import build_status, run_doctor
from research_system.runtime.orchestrator import ResearchOrchestrator
from research_system.runtime.provider_registry import OpenAIProvider
from research_system.runtime.settings import Settings, ensure_workspace_layout
from research_system.services.task_router import ChatDispatchDecision, TaskRouter


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

    proposals_parser = subparsers.add_parser("proposals")
    proposals_parser.add_argument("--status", choices=("pending", "approved", "rejected"))
    proposals_parser.add_argument("--limit", type=int, default=50)
    proposals_parser.add_argument("--json", action="store_true", dest="as_json")

    proposal_parser = subparsers.add_parser("proposal")
    proposal_parser.add_argument("proposal_id")
    proposal_parser.add_argument("--json", action="store_true", dest="as_json")

    review_proposal_parser = subparsers.add_parser("review-proposal")
    review_proposal_parser.add_argument("proposal_id")
    review_proposal_parser.add_argument("decision", choices=("approve", "reject"))
    review_proposal_parser.add_argument("--reason")
    review_proposal_parser.add_argument("--reviewer", default="manual_cli_review")
    review_proposal_parser.add_argument("--json", action="store_true", dest="as_json")

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
    chat_parser.add_argument("--ui", choices=("line", "tui"), default="line")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    settings = Settings.from_env(Path.cwd())
    settings.validate()
    ensure_workspace_layout(settings)

    if args.command == "chat":
        if args.ui == "tui":
            try:
                return run_chat_tui(
                    settings,
                    initial_intent=args.intent,
                    initial_run_id=args.run_id,
                    thinking=args.thinking,
                    events=args.events,
                    handle_chat_line=_handle_chat_line,
                    execute_cli_command=_execute_cli_command,
                )
            except NoConsoleScreenBufferError as exc:
                return run_chat_fallback(
                    settings,
                    initial_intent=args.intent,
                    initial_run_id=args.run_id,
                    thinking=args.thinking,
                    events=args.events,
                    handle_chat_line=_handle_chat_line,
                    fallback_reason=str(exc),
                )
        return run_chat_fallback(
            settings,
            initial_intent=args.intent,
            initial_run_id=args.run_id,
            thinking=args.thinking,
            events=args.events,
            handle_chat_line=_handle_chat_line,
            fallback_reason="line_mode_selected",
        )

    payload = _execute_cli_command(
        settings,
        command=args.command,
        query=getattr(args, "query", None),
        intent=getattr(args, "intent", getattr(args, "reviewer", None)),
        run_id=getattr(args, "run_id", getattr(args, "proposal_id", None)),
        limit=getattr(args, "limit", 20),
        status_filter=getattr(args, "status", getattr(args, "reason", None)),
    )
    return _print_payload(payload, as_json=getattr(args, "as_json", False))


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
        with ResearchOrchestrator(settings) as orchestrator:
            request = build_research_request(
                query=query or "",
                intent=intent,
                review_target_run_id=run_id,
            )
            response = orchestrator.run(request)
            return response.model_dump(mode="json")
    return execute_cli_payload_command(
        settings,
        command=command,
        query=query,
        intent=intent,
        run_id=run_id,
        limit=limit,
        status_filter=status_filter,
        orchestrator_cls=ResearchOrchestrator,
        run_checks_fn=run_checks,
        build_status_fn=build_status,
        run_doctor_fn=run_doctor,
    )


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
    return controller_handle_chat_line(
        settings,
        state,
        line,
        llm_provider=llm_provider,
        task_router=task_router,
        cli_command_runner=_execute_cli_command,
        payload_printer=_print_payload,
        output_writer=output_writer,
        stop_callback=stop_callback,
        chat_dispatch_fn=decide_chat_dispatch,
        basic_answer_stream_fn=lambda **kwargs: print_streamed_basic_chat_answer(emit_text=dispatch_transcript_update, **kwargs),
        run_chat_research_fn=controller_run_chat_research,
        print_chat_result_payload_fn=lambda payload, *, llm_provider, query, requested_intent, output_writer=None: print_chat_result_payload(
            payload,
            llm_provider=llm_provider,
            query=query,
            requested_intent=requested_intent,
            payload_printer=_print_payload,
            output_writer=output_writer,
        ),
    )


def _print_payload(payload: object, *, as_json: bool, output_writer=None) -> int:
    return print_payload(payload, as_json=as_json, emit_text=dispatch_transcript_update, output_writer=output_writer)


if __name__ == "__main__":
    raise SystemExit(main())
