from __future__ import annotations

import json

from research_system.cli.chat_updates import (
    SessionStatePatch,
    ShellStatePatch,
    meta_update,
    session_patch_update,
    state_patch_update,
)
from research_system.services.proposal_payloads import (
    proposal_detail_fields,
    proposal_result_sections,
    render_proposal_review_history_line,
    render_proposal_row_text,
)
from research_system.services.status_payloads import (
    group_doctor_checks,
    render_doctor_check_line,
    status_header_fields,
)
from research_system.services.run_payloads import (
    latest_run_context,
    render_labeled_field_line,
    render_run_row_text,
    render_trace_row_text,
    replay_payload_fields,
    render_status_counts_text,
    trace_payload_fields,
    run_payload_fields,
)


def emit_run_context_feed(
    run_id: str | None,
    status: str | None,
    *,
    emit_text,
    output_writer=None,
) -> None:
    if run_id is not None or status is not None:
        emit_text(
            output_writer,
            state_patch_update(
                ShellStatePatch(
                    current_run_id=run_id,
                    run_status_label=status,
                )
            ),
        )
    if run_id is not None:
        emit_text(
            output_writer,
            session_patch_update(
                SessionStatePatch(
                    last_run_id=run_id,
                )
            ),
        )


def emit_run_detail_fields(
    fields: list[tuple[str, str]],
    *,
    emit_text,
    output_writer=None,
) -> None:
    run_id = None
    status = None
    for label, value in fields:
        line = render_labeled_field_line(label, value)
        if label == "run_id":
            run_id = value
            emit_text(
                output_writer,
                meta_update(
                    line,
                    state_patch=ShellStatePatch(current_run_id=value),
                    session_patch=SessionStatePatch(last_run_id=value),
                ),
            )
            continue
        if label == "status":
            status = value
            emit_text(
                output_writer,
                meta_update(
                    line,
                    state_patch=ShellStatePatch(run_status_label=value),
                ),
            )
            continue
        emit_text(output_writer, meta_update(line))
    if run_id is None or status is None:
        emit_run_context_feed(run_id, status, emit_text=emit_text, output_writer=output_writer)


def print_payload(payload: object, *, as_json: bool, emit_text, output_writer=None) -> int:
    if as_json:
        emit_text(output_writer, meta_update(json.dumps(payload, ensure_ascii=True, indent=2)))
        return 0
    if isinstance(payload, list):
        if print_proposal_rows(payload, emit_text=emit_text, output_writer=output_writer):
            return 0
        print_rows(payload, emit_text=emit_text, output_writer=output_writer)
        return 0
    if isinstance(payload, dict):
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
    if not rows:
        emit_text(output_writer, meta_update("No runs."))
        return
    for row in rows:
        if not isinstance(row, dict):
            emit_text(output_writer, meta_update(json.dumps(row, ensure_ascii=True)))
            continue
        emit_text(output_writer, meta_update(render_run_row_text(row)))
    latest_run_id, latest_status = latest_run_context(rows)
    if latest_run_id is not None or latest_status is not None:
        emit_run_context_feed(
            latest_run_id,
            latest_status,
            emit_text=emit_text,
            output_writer=output_writer,
        )


def print_proposal_rows(rows: list[object], *, emit_text, output_writer=None) -> bool:
    if not rows:
        return False
    normalized_rows = [row for row in rows if isinstance(row, dict)]
    if len(normalized_rows) != len(rows):
        return False
    if not all("proposal_id" in row and "proposal_type" in row for row in normalized_rows):
        return False
    for row in normalized_rows:
        emit_text(output_writer, meta_update(render_proposal_row_text(row)))
    return True


def print_proposal_payload(payload: dict, *, emit_text, output_writer=None) -> bool:
    if "proposal_id" not in payload or "proposal_type" not in payload:
        return False
    for label, value in proposal_detail_fields(payload):
        emit_text(output_writer, meta_update(render_labeled_field_line(label, value)))
    review_history = payload.get("review_history")
    if isinstance(review_history, list) and review_history:
        emit_text(output_writer, meta_update("review_history:"))
        for row in review_history:
            if not isinstance(row, dict):
                continue
            emit_text(output_writer, meta_update(render_proposal_review_history_line(row)))
    for section in proposal_result_sections(payload):
        emit_text(output_writer, meta_update(json.dumps(section, ensure_ascii=True, indent=2)))
    return True


def print_status_payload(payload: dict, *, emit_text, output_writer=None) -> bool:
    if "recent_runs" not in payload or "status_counts" not in payload:
        return False
    for label, value in status_header_fields(payload):
        emit_text(output_writer, meta_update(render_labeled_field_line(label, value)))
    status_counts = payload.get("status_counts") or {}
    if status_counts:
        counts_line = render_status_counts_text(status_counts)
        emit_text(output_writer, meta_update(f"status_counts: {counts_line}"))
    recent_runs = payload.get("recent_runs") or []
    if recent_runs:
        emit_text(output_writer, meta_update("recent_runs:"))
        for row in recent_runs:
            if not isinstance(row, dict):
                continue
            emit_text(output_writer, meta_update(render_run_row_text(row, indent="  ")))
    latest_run_id, latest_status = latest_run_context(recent_runs)
    emit_run_context_feed(
        latest_run_id,
        latest_status,
        emit_text=emit_text,
        output_writer=output_writer,
    )
    return True


def print_replay_payload(payload: dict, *, emit_text, output_writer=None) -> bool:
    if "request" not in payload or "events" not in payload or "blackboard" not in payload:
        return False
    emit_run_detail_fields(
        replay_payload_fields(payload),
        emit_text=emit_text,
        output_writer=output_writer,
    )
    result = payload.get("result")
    if result:
        emit_text(output_writer, meta_update(json.dumps(result, ensure_ascii=True, indent=2)))
    return True


def print_trace_payload(payload: dict, *, emit_text, output_writer=None) -> bool:
    if "subagent_trace" not in payload or "dynamic_trace" not in payload or "wait_trace" not in payload:
        return False
    emit_text(
        output_writer,
        state_patch_update(
            ShellStatePatch(
                trace_payload=payload,
            )
        ),
    )
    emit_run_detail_fields(
        trace_payload_fields(payload),
        emit_text=emit_text,
        output_writer=output_writer,
    )
    for section_name in ("subagent_trace", "dynamic_trace", "wait_trace"):
        rows = payload.get(section_name) or []
        if not isinstance(rows, list):
            continue
        emit_text(output_writer, meta_update(f"{section_name}:"))
        if not rows:
            emit_text(output_writer, meta_update("  -"))
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            emit_text(output_writer, meta_update(render_trace_row_text(row, indent="  ")))
    return True


def print_doctor_payload(payload: dict, *, emit_text, output_writer=None) -> bool:
    checks = payload.get("checks")
    status = payload.get("status")
    if not isinstance(checks, list) or not isinstance(status, str):
        return False
    emit_text(
        output_writer,
        meta_update(
            f"doctor_status: {status}",
            state_patch=ShellStatePatch(run_status_label=f"doctor:{status}"),
        ),
    )
    grouped = group_doctor_checks(checks)
    for group_name in ("failed", "passed", "skipped"):
        rows = grouped.get(group_name) or []
        if not rows:
            continue
        emit_text(output_writer, meta_update(f"{group_name}:"))
        for row in rows:
            emit_text(output_writer, meta_update(render_doctor_check_line(row)))
    return True
