from __future__ import annotations

import json

from research_system.cli.state.chat_updates import (
    ShellStatePatch,
    meta_update,
    state_patch_update,
)
from research_system.services.payloads.runs.run_payloads import (
    events_payload_fields,
    metrics_payload_fields,
    render_event_row_text,
    render_labeled_field_line,
    render_status_counts_text,
    render_trace_row_text,
    steps_payload_fields,
    subagent_payload_fields,
    thread_payload_fields,
    trace_payload_fields,
)

from research_system.cli.payloads.payload_rendering_common import emit_run_detail_fields


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


def print_events_payload(payload: dict, *, emit_text, output_writer=None) -> bool:
    if "events" not in payload or "event_counts" not in payload:
        return False
    emit_text(
        output_writer,
        state_patch_update(
            ShellStatePatch(
                events_payload=payload,
            )
        ),
    )
    emit_run_detail_fields(
        events_payload_fields(payload),
        emit_text=emit_text,
        output_writer=output_writer,
    )
    emit_text(output_writer, meta_update("events:"))
    rows = payload.get("events") or []
    if not isinstance(rows, list) or not rows:
        emit_text(output_writer, meta_update("  -"))
        return True
    for row in rows:
        if not isinstance(row, dict):
            continue
        emit_text(output_writer, meta_update(render_event_row_text(row, indent="  ")))
    return True


def print_metrics_payload(payload: dict, *, emit_text, output_writer=None) -> bool:
    if "counts" not in payload or "rates" not in payload or "latency_ms" not in payload:
        return False
    emit_run_detail_fields(
        metrics_payload_fields(payload),
        emit_text=emit_text,
        output_writer=output_writer,
    )
    counts = payload.get("counts") or {}
    rates = payload.get("rates") or {}
    fanout = payload.get("fanout") or {}
    degrade_reason_counts = payload.get("degrade_reason_counts") or {}
    event_type_counts = payload.get("event_type_counts") or {}
    turn_usage = payload.get("turn_usage") or {}
    latency_ms = payload.get("latency_ms") or {}
    emit_text(output_writer, meta_update(f"counts: {json.dumps(counts, ensure_ascii=True, sort_keys=True)}"))
    emit_text(output_writer, meta_update(f"rates: {json.dumps(rates, ensure_ascii=True, sort_keys=True)}"))
    emit_text(output_writer, meta_update(f"fanout: {json.dumps(fanout, ensure_ascii=True, sort_keys=True)}"))
    if degrade_reason_counts:
        emit_text(
            output_writer,
            meta_update(f"degrade_reason_counts: {json.dumps(degrade_reason_counts, ensure_ascii=True, sort_keys=True)}"),
        )
    if event_type_counts:
        emit_text(
            output_writer,
            meta_update(f"event_type_counts: {json.dumps(event_type_counts, ensure_ascii=True, sort_keys=True)}"),
        )
    emit_text(output_writer, meta_update("latency_ms:"))
    for key in ("subagents", "actions", "per_subagent", "per_action"):
        value = latency_ms.get(key)
        emit_text(output_writer, meta_update(f"  {key}: {json.dumps(value, ensure_ascii=True, sort_keys=True)}"))
    emit_text(output_writer, meta_update("turn_usage:"))
    if not isinstance(turn_usage, dict) or not turn_usage:
        emit_text(output_writer, meta_update("  -"))
        return True
    for turn_id in sorted(turn_usage):
        emit_text(output_writer, meta_update(f"  {turn_id}: {json.dumps(turn_usage[turn_id], ensure_ascii=True, sort_keys=True)}"))
    return True


def print_thread_payload(payload: dict, *, emit_text, output_writer=None) -> bool:
    if "thread" not in payload or "thread_id" not in payload or "items" not in payload:
        return False
    emit_run_detail_fields(
        thread_payload_fields(payload),
        emit_text=emit_text,
        output_writer=output_writer,
    )
    thread_payload = payload.get("thread") or {}
    emit_text(output_writer, meta_update(f"thread: {json.dumps(thread_payload, ensure_ascii=True, sort_keys=True)}"))
    emit_text(output_writer, meta_update("subagent_trace:"))
    trace_rows = payload.get("subagent_trace") or []
    if not isinstance(trace_rows, list) or not trace_rows:
        emit_text(output_writer, meta_update("  -"))
    else:
        for row in trace_rows:
            if isinstance(row, dict):
                emit_text(output_writer, meta_update(render_trace_row_text(row, indent="  ")))
    emit_text(output_writer, meta_update("events:"))
    event_rows = payload.get("events") or []
    if not isinstance(event_rows, list) or not event_rows:
        emit_text(output_writer, meta_update("  -"))
    else:
        for row in event_rows:
            if isinstance(row, dict):
                emit_text(output_writer, meta_update(render_event_row_text(row, indent="  ")))
    emit_text(output_writer, meta_update("items:"))
    items = payload.get("items") or []
    if not isinstance(items, list) or not items:
        emit_text(output_writer, meta_update("  -"))
        return True
    for row in items:
        if not isinstance(row, dict):
            continue
        item_id = str(row.get("item_id") or row.get("title") or "unknown")
        item_type = str(row.get("item_type") or "item")
        status = str(row.get("status") or "")
        line = f"  {item_id} [{item_type}]"
        if status:
            line += f"  status={status}"
        emit_text(output_writer, meta_update(line))
    return True


def print_steps_payload(payload: dict, *, emit_text, output_writer=None) -> bool:
    if "steps" not in payload or "step_counts" not in payload:
        return False
    emit_run_detail_fields(
        steps_payload_fields(payload),
        emit_text=emit_text,
        output_writer=output_writer,
    )
    dispatch_trace = payload.get("dispatch_trace") or {}
    if dispatch_trace:
        emit_text(output_writer, meta_update("dispatch_trace:"))
        for key in sorted(dispatch_trace):
            emit_text(output_writer, meta_update(render_labeled_field_line(str(key), dispatch_trace[key], indent="  ")))
    notes = payload.get("notes") or []
    if isinstance(notes, list) and notes:
        emit_text(output_writer, meta_update("notes:"))
        for item in notes:
            text = str(item).strip()
            if text:
                emit_text(output_writer, meta_update(f"  {text}"))
    status_counts = payload.get("status_counts") or {}
    if status_counts:
        emit_text(output_writer, meta_update(f"status_counts: {render_status_counts_text(status_counts)}"))
    emit_text(output_writer, meta_update("steps:"))
    rows = payload.get("steps") or []
    if not isinstance(rows, list) or not rows:
        emit_text(output_writer, meta_update("  -"))
        return True
    for row in rows:
        if not isinstance(row, dict):
            continue
        step_index = row.get("step_index")
        node_id = str(row.get("node_id") or "unknown")
        phase = str(row.get("phase") or "-")
        agent = str(row.get("agent") or "-")
        status = str(row.get("status") or "pending")
        objective = str(row.get("objective") or "").strip()
        line = f"  {step_index}. {node_id} [{status}]  phase={phase} agent={agent}"
        if row.get("active_from_supervisor"):
            line += "  next=active"
        emit_text(output_writer, meta_update(line))
        if objective:
            emit_text(output_writer, meta_update(f"    objective: {objective}"))
        tags = row.get("tags") or []
        if isinstance(tags, list) and tags:
            emit_text(output_writer, meta_update(f"    tags: {', '.join(str(tag) for tag in tags if str(tag).strip())}"))
        inputs = row.get("inputs") or {}
        if isinstance(inputs, dict) and inputs:
            emit_text(output_writer, meta_update("    inputs:"))
            for key in sorted(inputs):
                emit_text(output_writer, meta_update(render_labeled_field_line(str(key), inputs[key], indent="      ")))
        supervisor_note = str(row.get("supervisor_note") or "").strip()
        if supervisor_note:
            emit_text(output_writer, meta_update(f"    supervisor_note: {supervisor_note}"))
    return True


def print_subagent_payload(payload: dict, *, emit_text, output_writer=None) -> bool:
    if "invocation_id" not in payload or "trace_rows" not in payload or "events" not in payload:
        return False
    emit_run_detail_fields(
        subagent_payload_fields(payload),
        emit_text=emit_text,
        output_writer=output_writer,
    )
    emit_text(
        output_writer,
        state_patch_update(
            ShellStatePatch(
                subagent_payload=payload,
            )
        ),
    )
    objective = str(payload.get("objective") or "").strip()
    if objective:
        emit_text(output_writer, meta_update(f"objective: {objective}"))
    notes = payload.get("notes") or []
    if isinstance(notes, list) and notes:
        emit_text(output_writer, meta_update("notes:"))
        for item in notes:
            text = str(item).strip()
            if text:
                emit_text(output_writer, meta_update(f"  {text}"))
    if isinstance(payload.get("item"), dict):
        item_payload = payload["item"]
        emit_text(
            output_writer,
            meta_update(
                "item: "
                + json.dumps(
                    {
                        "item_id": item_payload.get("item_id"),
                        "thread_id": item_payload.get("thread_id"),
                        "turn_id": item_payload.get("turn_id"),
                        "status": item_payload.get("status"),
                        "degrade_reason": item_payload.get("degrade_reason"),
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                )
            ),
        )
    emit_text(output_writer, meta_update("trace_rows:"))
    trace_rows = payload.get("trace_rows") or []
    if not isinstance(trace_rows, list) or not trace_rows:
        emit_text(output_writer, meta_update("  -"))
    else:
        for row in trace_rows:
            if isinstance(row, dict):
                emit_text(output_writer, meta_update(render_trace_row_text(row, indent="  ")))
    emit_text(output_writer, meta_update("events:"))
    event_rows = payload.get("events") or []
    if not isinstance(event_rows, list) or not event_rows:
        emit_text(output_writer, meta_update("  -"))
    else:
        for row in event_rows:
            if isinstance(row, dict):
                emit_text(output_writer, meta_update(render_event_row_text(row, indent="  ")))
    return True
