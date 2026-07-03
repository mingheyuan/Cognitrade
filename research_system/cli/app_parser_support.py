from __future__ import annotations

import argparse


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

    replay_parser = subparsers.add_parser("replay")
    replay_parser.add_argument("run_id")
    replay_parser.add_argument("--json", action="store_true", dest="as_json")

    trace_parser = subparsers.add_parser("trace")
    trace_parser.add_argument("run_id")
    trace_parser.add_argument("--json", action="store_true", dest="as_json")

    events_parser = subparsers.add_parser("events")
    events_parser.add_argument("run_id")
    events_parser.add_argument("--json", action="store_true", dest="as_json")

    run_steps_parser = subparsers.add_parser("run-steps")
    run_steps_parser.add_argument("run_id")
    run_steps_parser.add_argument("--json", action="store_true", dest="as_json")

    subagent_parser = subparsers.add_parser("subagent-read")
    subagent_parser.add_argument("run_id")
    subagent_parser.add_argument("call_id")
    subagent_parser.add_argument("--json", action="store_true", dest="as_json")

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
