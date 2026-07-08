from __future__ import annotations

from pathlib import Path

from research_system.cli.runtime.app import build_chat_entry_runtime, run_chat_entry as runtime_run_chat_entry
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout


def build_app_settings(*, cwd: Path | None = None) -> Settings:
    settings = Settings.from_env(cwd or Path.cwd())
    settings.validate()
    ensure_workspace_layout(settings)
    return settings


def execute_app_main_command(
    settings: Settings,
    args,
    *,
    build_chat_entry_runtime_fn=build_chat_entry_runtime,
    run_chat_entry_fn=runtime_run_chat_entry,
    run_chat_tui_fn,
    run_chat_fallback_fn,
    handle_chat_line_fn,
    execute_cli_command_fn,
    print_payload_fn,
) -> int:
    if args.command == "chat":
        entry_runtime = build_chat_entry_runtime_fn(
            run_chat_tui_fn=run_chat_tui_fn,
            run_chat_fallback_fn=run_chat_fallback_fn,
            handle_chat_line=handle_chat_line_fn,
            execute_cli_command=execute_cli_command_fn,
        )
        return run_chat_entry_fn(
            settings,
            initial_intent=args.intent,
            initial_run_id=args.run_id,
            thinking=args.thinking,
            events=args.events,
            ui=args.ui,
            runtime=entry_runtime,
        )

    payload = execute_cli_command_fn(
        settings,
        command=args.command,
        query=getattr(
            args,
            "query",
            getattr(
                args,
                "decision",
                getattr(
                    args,
                    "delegate_subagent_type",
                    getattr(
                        args,
                        "call_id",
                        getattr(args, "thread_id", getattr(args, "node_id", None)),
                    ),
                ),
            ),
        ),
        intent=getattr(args, "intent", getattr(args, "reviewer", getattr(args, "activated_by", None))),
        run_id=getattr(args, "run_id", getattr(args, "proposal_id", None)),
        ticket_id=getattr(args, "ticket_id", None),
        limit=getattr(args, "limit", 20),
        status_filter=getattr(args, "status", getattr(args, "reason", None)),
        activated_by=getattr(args, "activated_by", None),
        delegate_subagent_type=getattr(args, "delegate_subagent_type", None),
        candidate_subagent_type=getattr(args, "candidate_subagent_type", None),
        registration_template_id=getattr(args, "registration_template_id", None),
        registration_type=getattr(args, "registration_type", None),
        skill_name=getattr(args, "skill_name", None),
        snapshot_type=getattr(args, "snapshot_type", None),
        source_id=getattr(args, "source_id", None),
        tool_name=getattr(args, "tool_name", None),
        endpoint_path=getattr(args, "endpoint_path", None),
        http_method=getattr(args, "http_method", None),
        openapi_spec_uri=getattr(args, "openapi_spec_uri", None),
        openapi_operation_id=getattr(args, "openapi_operation_id", None),
        timeout_seconds=getattr(args, "timeout_seconds", None),
        action_node_id=getattr(args, "action_node_id", None),
        dag_id=getattr(args, "dag_id", None),
        subagent_node_id=getattr(args, "subagent_node_id", None),
    )
    return print_payload_fn(payload, as_json=getattr(args, "as_json", False))
