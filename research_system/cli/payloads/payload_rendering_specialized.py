from __future__ import annotations

import json

from research_system.cli.state.chat_updates import (
    ShellStatePatch,
    meta_update,
)
from research_system.services.proposals.payload.payloads import (
    proposal_detail_fields,
    proposal_result_sections,
    render_proposal_review_history_line,
    render_proposal_row_text,
)
from research_system.services.payloads.status_payloads import (
    group_doctor_checks,
    render_doctor_check_line,
    status_header_fields,
)
from research_system.services.payloads.runs.run_payloads import (
    latest_run_context,
    render_run_row_text,
    replay_payload_fields,
    render_status_counts_text,
)

from research_system.cli.payloads.payload_rendering_common import emit_run_context_feed, emit_run_detail_fields


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


def print_subagent_discovery_rows(rows: list[object], *, emit_text, output_writer=None) -> bool:
    if not rows:
        return False
    normalized_rows = [row for row in rows if isinstance(row, dict)]
    if len(normalized_rows) != len(rows):
        return False
    if not all("ticket_id" in row and "search_query" in row for row in normalized_rows):
        return False
    for row in normalized_rows:
        emit_text(output_writer, meta_update(render_subagent_discovery_row_text(row)))
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


def print_subagent_discovery_payload(payload: dict, *, emit_text, output_writer=None) -> bool:
    if "ticket_id" not in payload or "capability_gap" not in payload or "tool_discovery_request" not in payload:
        return False
    for label, value in subagent_discovery_detail_fields(payload):
        emit_text(output_writer, meta_update(render_labeled_field_line(label, value)))
    candidate_entries = payload.get("candidate_catalog_entries") or []
    if isinstance(candidate_entries, list) and candidate_entries:
        emit_text(output_writer, meta_update("candidate_catalog_entries:"))
        for row in candidate_entries:
            if isinstance(row, dict):
                emit_text(output_writer, meta_update(render_subagent_catalog_entry_line(row)))
    activated_entries = payload.get("activated_entries") or []
    if isinstance(activated_entries, list) and activated_entries:
        emit_text(output_writer, meta_update("activated_entries:"))
        for row in activated_entries:
            if isinstance(row, dict):
                emit_text(output_writer, meta_update(render_subagent_activation_entry_line(row)))
    notes = payload.get("notes") or []
    if isinstance(notes, list) and notes:
        emit_text(output_writer, meta_update("notes:"))
        for note in notes:
            emit_text(output_writer, meta_update(f"  - {note}"))
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


def render_labeled_field_line(label: str, value, indent: str = "") -> str:
    return f"{indent}{label}: {value}"


def render_subagent_discovery_row_text(row: dict) -> str:
    ticket_id = str(row.get("ticket_id") or "unknown")
    status = str(row.get("status") or "pending")
    intent = str(row.get("intent") or "-")
    stage = str(row.get("stage") or "-")
    capability = str(row.get("capability") or "-")
    query = str(row.get("search_query") or "-")
    counts = (
        f"candidates={int(row.get('candidate_entry_count') or 0)} "
        f"activations={int(row.get('activation_entry_count') or 0)}"
    )
    return (
        f"{ticket_id}  [{status}]  intent={intent}  stage={stage}  "
        f"capability={capability}  query={query}  {counts}"
    )


def subagent_discovery_detail_fields(payload: dict) -> list[tuple[str, str]]:
    gap = payload.get("capability_gap") or {}
    request = payload.get("tool_discovery_request") or {}
    preferred_protocols = request.get("preferred_protocols") or []
    return [
        ("ticket_id", str(payload.get("ticket_id") or "")),
        ("status", str(payload.get("status") or "")),
        ("stored_status", str(payload.get("stored_status") or "")),
        ("intent", str(gap.get("intent") or "")),
        ("stage", str(gap.get("stage") or "")),
        ("capability", str(gap.get("capability") or "")),
        ("preferred_subagent_type", str(gap.get("preferred_subagent_type") or "")),
        ("requires_llm", str(gap.get("requires_llm"))),
        ("gap_reason", str(gap.get("reason") or "")),
        ("search_query", str(request.get("search_query") or "")),
        ("preferred_protocols", ", ".join(str(item) for item in preferred_protocols) or "-"),
        ("request_reason", str(request.get("reason") or "")),
        ("reviewer", str(payload.get("reviewer") or "")),
        ("review_reason", str(payload.get("review_reason") or "")),
    ]


def render_subagent_catalog_entry_line(row: dict) -> str:
    capabilities = ", ".join(str(item) for item in row.get("capabilities") or []) or "-"
    tags = ", ".join(str(item) for item in row.get("tags") or []) or "-"
    stage = str(row.get("stage") or "-")
    summary = str(row.get("summary") or "").strip()
    summary_part = f"  summary={summary}" if summary else ""
    templates = row.get("runtime_registration_templates") or []
    template_ids = ", ".join(
        str(item.get("template_id") or "").strip()
        for item in templates
        if isinstance(item, dict) and str(item.get("template_id") or "").strip()
    )
    template_part = f"  templates={template_ids}" if template_ids else ""
    return (
        f"  - {row.get('subagent_type')}  stage={stage}  capabilities={capabilities}  tags={tags}{summary_part}{template_part}"
    )


def render_subagent_activation_entry_line(row: dict) -> str:
    runtime_registration = row.get("runtime_registration") or {}
    registration_type = str(runtime_registration.get("registration_type") or "").strip()
    if registration_type:
        registration_bits = [registration_type]
        skill_name = str(runtime_registration.get("skill_name") or "").strip()
        snapshot_type = str(runtime_registration.get("snapshot_type") or "").strip()
        source_id = str(runtime_registration.get("source_id") or "").strip()
        endpoint_path = str(runtime_registration.get("endpoint_path") or "").strip()
        http_method = str(runtime_registration.get("http_method") or "").strip()
        openapi_spec_uri = str(runtime_registration.get("openapi_spec_uri") or "").strip()
        openapi_operation_id = str(runtime_registration.get("openapi_operation_id") or "").strip()
        registration_template_id = str(runtime_registration.get("registration_template_id") or "").strip()
        if registration_template_id:
            registration_bits.append(f"template={registration_template_id}")
        if skill_name:
            registration_bits.append(f"skill={skill_name}")
        if snapshot_type:
            registration_bits.append(f"snapshot={snapshot_type}")
        if source_id:
            registration_bits.append(f"source={source_id}")
        if http_method:
            registration_bits.append(f"method={http_method}")
        if endpoint_path:
            registration_bits.append(f"path={endpoint_path}")
        if openapi_operation_id:
            registration_bits.append(f"op={openapi_operation_id}")
        if openapi_spec_uri:
            registration_bits.append(f"spec={openapi_spec_uri}")
        registration_text = " ".join(registration_bits)
        return (
            f"  - {row.get('subagent_type')} -> runtime:{registration_text}  "
            f"status={row.get('status')}  activated_by={row.get('activated_by') or '-'}"
        )
    return (
        f"  - {row.get('subagent_type')} -> {row.get('delegate_subagent_type')}  "
        f"status={row.get('status')}  activated_by={row.get('activated_by') or '-'}"
    )
