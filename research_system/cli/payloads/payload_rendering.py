from __future__ import annotations

import json

from research_system.cli.state.chat_updates import meta_update
from research_system.cli.payloads.payload_rendering_common import (
    emit_run_context_feed as common_emit_run_context_feed,
    emit_run_detail_fields as common_emit_run_detail_fields,
    print_rows as common_print_rows,
)
from research_system.cli.payloads.payload_rendering_inspect import (
    print_events_payload as inspect_print_events_payload,
    print_metrics_payload as inspect_print_metrics_payload,
    print_steps_payload as inspect_print_steps_payload,
    print_subagent_payload as inspect_print_subagent_payload,
    print_thread_payload as inspect_print_thread_payload,
    print_trace_payload as inspect_print_trace_payload,
)
from research_system.cli.payloads.payload_rendering_specialized import (
    print_doctor_payload as specialized_print_doctor_payload,
    print_proposal_payload as specialized_print_proposal_payload,
    print_proposal_rows as specialized_print_proposal_rows,
    print_replay_payload as specialized_print_replay_payload,
    print_status_payload as specialized_print_status_payload,
    print_subagent_discovery_payload as specialized_print_subagent_discovery_payload,
    print_subagent_discovery_rows as specialized_print_subagent_discovery_rows,
)
from research_system.services.payloads.runs.run_payloads import run_payload_fields


def emit_run_context_feed(
    run_id: str | None,
    status: str | None,
    *,
    emit_text,
    output_writer=None,
) -> None:
    common_emit_run_context_feed(run_id, status, emit_text=emit_text, output_writer=output_writer)


def emit_run_detail_fields(
    fields: list[tuple[str, str]],
    *,
    emit_text,
    output_writer=None,
) -> None:
    common_emit_run_detail_fields(fields, emit_text=emit_text, output_writer=output_writer)


def print_payload(payload: object, *, as_json: bool, emit_text, output_writer=None) -> int:
    if as_json:
        emit_text(output_writer, meta_update(json.dumps(payload, ensure_ascii=True, indent=2)))
        return 0
    if isinstance(payload, list):
        if print_subagent_discovery_rows(payload, emit_text=emit_text, output_writer=output_writer):
            return 0
        if print_proposal_rows(payload, emit_text=emit_text, output_writer=output_writer):
            return 0
        print_rows(payload, emit_text=emit_text, output_writer=output_writer)
        return 0
    if isinstance(payload, dict):
        if print_subagent_discovery_payload(payload, emit_text=emit_text, output_writer=output_writer):
            return 0
        if print_proposal_payload(payload, emit_text=emit_text, output_writer=output_writer):
            return 0
        if print_status_payload(payload, emit_text=emit_text, output_writer=output_writer):
            return 0
        if print_doctor_payload(payload, emit_text=emit_text, output_writer=output_writer):
            return 0
        if print_replay_payload(payload, emit_text=emit_text, output_writer=output_writer):
            return 0
        if print_trace_payload(payload, emit_text=emit_text, output_writer=output_writer):
            return 0
        if print_metrics_payload(payload, emit_text=emit_text, output_writer=output_writer):
            return 0
        if print_steps_payload(payload, emit_text=emit_text, output_writer=output_writer):
            return 0
        if print_subagent_payload(payload, emit_text=emit_text, output_writer=output_writer):
            return 0
        if print_thread_payload(payload, emit_text=emit_text, output_writer=output_writer):
            return 0
        if print_events_payload(payload, emit_text=emit_text, output_writer=output_writer):
            return 0
        display_summary = payload.get("display_summary")
        if display_summary:
            emit_text(output_writer, meta_update(str(display_summary)))
        else:
            emit_text(output_writer, meta_update(str(payload.get("message", json.dumps(payload, ensure_ascii=True)))))
        emit_run_detail_fields(
            run_payload_fields(payload),
            emit_text=emit_text,
            output_writer=output_writer,
        )
        if payload.get("result"):
            emit_text(output_writer, meta_update(json.dumps(payload["result"], ensure_ascii=True, indent=2)))
        return 0
    emit_text(output_writer, meta_update(json.dumps(payload, ensure_ascii=True, indent=2)))
    return 0


def print_rows(rows: list[object], *, emit_text, output_writer=None) -> None:
    common_print_rows(rows, emit_text=emit_text, output_writer=output_writer)


def print_proposal_rows(rows: list[object], *, emit_text, output_writer=None) -> bool:
    return specialized_print_proposal_rows(rows, emit_text=emit_text, output_writer=output_writer)


def print_subagent_discovery_rows(rows: list[object], *, emit_text, output_writer=None) -> bool:
    return specialized_print_subagent_discovery_rows(rows, emit_text=emit_text, output_writer=output_writer)


def print_proposal_payload(payload: dict, *, emit_text, output_writer=None) -> bool:
    return specialized_print_proposal_payload(payload, emit_text=emit_text, output_writer=output_writer)


def print_subagent_discovery_payload(payload: dict, *, emit_text, output_writer=None) -> bool:
    return specialized_print_subagent_discovery_payload(payload, emit_text=emit_text, output_writer=output_writer)


def print_status_payload(payload: dict, *, emit_text, output_writer=None) -> bool:
    return specialized_print_status_payload(payload, emit_text=emit_text, output_writer=output_writer)


def print_replay_payload(payload: dict, *, emit_text, output_writer=None) -> bool:
    return specialized_print_replay_payload(payload, emit_text=emit_text, output_writer=output_writer)


def print_trace_payload(payload: dict, *, emit_text, output_writer=None) -> bool:
    return inspect_print_trace_payload(payload, emit_text=emit_text, output_writer=output_writer)


def print_events_payload(payload: dict, *, emit_text, output_writer=None) -> bool:
    return inspect_print_events_payload(payload, emit_text=emit_text, output_writer=output_writer)


def print_metrics_payload(payload: dict, *, emit_text, output_writer=None) -> bool:
    return inspect_print_metrics_payload(payload, emit_text=emit_text, output_writer=output_writer)


def print_steps_payload(payload: dict, *, emit_text, output_writer=None) -> bool:
    return inspect_print_steps_payload(payload, emit_text=emit_text, output_writer=output_writer)


def print_subagent_payload(payload: dict, *, emit_text, output_writer=None) -> bool:
    return inspect_print_subagent_payload(payload, emit_text=emit_text, output_writer=output_writer)


def print_thread_payload(payload: dict, *, emit_text, output_writer=None) -> bool:
    return inspect_print_thread_payload(payload, emit_text=emit_text, output_writer=output_writer)


def print_doctor_payload(payload: dict, *, emit_text, output_writer=None) -> bool:
    return specialized_print_doctor_payload(payload, emit_text=emit_text, output_writer=output_writer)
