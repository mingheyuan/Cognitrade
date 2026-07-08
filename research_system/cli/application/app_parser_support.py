from __future__ import annotations

import argparse

from research_system.runtime.orchestration.subagent_runtime_packaging import SUPPORTED_RUNTIME_REGISTRATION_TYPES


def build_app_parser() -> argparse.ArgumentParser:
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

    discoveries_parser = subparsers.add_parser("subagent-discoveries")
    discoveries_parser.add_argument("--status", choices=("pending", "approved", "rejected"))
    discoveries_parser.add_argument("--limit", type=int, default=50)
    discoveries_parser.add_argument("--json", action="store_true", dest="as_json")

    discovery_parser = subparsers.add_parser("subagent-discovery")
    discovery_parser.add_argument("ticket_id")
    discovery_parser.add_argument("--json", action="store_true", dest="as_json")

    review_discovery_parser = subparsers.add_parser("review-subagent-discovery")
    review_discovery_parser.add_argument("ticket_id")
    review_discovery_parser.add_argument("decision", choices=("approve", "reject"))
    review_discovery_parser.add_argument("--reason")
    review_discovery_parser.add_argument("--reviewer", default="manual_cli_review")
    review_discovery_parser.add_argument("--json", action="store_true", dest="as_json")

    activate_discovery_parser = subparsers.add_parser("activate-subagent-discovery")
    activate_discovery_parser.add_argument("ticket_id")
    activate_discovery_parser.add_argument("delegate_subagent_type", nargs="?")
    activate_discovery_parser.add_argument("--candidate-subagent-type")
    activate_discovery_parser.add_argument("--registration-template-id")
    activate_discovery_parser.add_argument("--registration-type", choices=SUPPORTED_RUNTIME_REGISTRATION_TYPES)
    activate_discovery_parser.add_argument("--skill-name")
    activate_discovery_parser.add_argument("--snapshot-type")
    activate_discovery_parser.add_argument("--source-id")
    activate_discovery_parser.add_argument("--endpoint-path")
    activate_discovery_parser.add_argument("--http-method", choices=("GET", "POST"))
    activate_discovery_parser.add_argument("--openapi-spec-uri")
    activate_discovery_parser.add_argument("--openapi-operation-id")
    activate_discovery_parser.add_argument("--timeout-seconds", type=float)
    activate_discovery_parser.add_argument("--activated-by", default="manual_cli_activation")
    activate_discovery_parser.add_argument("--reason")
    activate_discovery_parser.add_argument("--json", action="store_true", dest="as_json")

    replay_parser = subparsers.add_parser("replay")
    replay_parser.add_argument("run_id")
    replay_parser.add_argument("--json", action="store_true", dest="as_json")

    trace_parser = subparsers.add_parser("trace")
    trace_parser.add_argument("run_id")
    trace_parser.add_argument("--json", action="store_true", dest="as_json")

    events_parser = subparsers.add_parser("events")
    events_parser.add_argument("run_id")
    events_parser.add_argument("--json", action="store_true", dest="as_json")

    metrics_parser = subparsers.add_parser("metrics")
    metrics_parser.add_argument("run_id")
    metrics_parser.add_argument("--json", action="store_true", dest="as_json")

    run_steps_parser = subparsers.add_parser("run-steps")
    run_steps_parser.add_argument("run_id")
    run_steps_parser.add_argument("--json", action="store_true", dest="as_json")

    run_thread_parser = subparsers.add_parser("run-thread")
    run_thread_parser.add_argument("run_id")
    run_thread_parser.add_argument("thread_id")
    run_thread_parser.add_argument("--json", action="store_true", dest="as_json")

    subagent_parser = subparsers.add_parser("subagent-read")
    subagent_parser.add_argument("run_id")
    subagent_parser.add_argument("call_id")
    subagent_parser.add_argument("--json", action="store_true", dest="as_json")

    rerun_from_parser = subparsers.add_parser("rerun-from")
    rerun_from_parser.add_argument("run_id")
    rerun_from_parser.add_argument("node_id")
    rerun_from_parser.add_argument("--json", action="store_true", dest="as_json")

    rerun_subagent_parser = subparsers.add_parser("rerun-subagent")
    rerun_subagent_parser.add_argument("run_id")
    rerun_subagent_parser.add_argument("action_node_id")
    rerun_subagent_parser.add_argument("dag_id")
    rerun_subagent_parser.add_argument("subagent_node_id")
    rerun_subagent_parser.add_argument("--json", action="store_true", dest="as_json")

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
