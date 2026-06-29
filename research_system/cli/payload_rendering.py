from __future__ import annotations

import json

from research_system.cli.chat_updates import (
    SessionStatePatch,
    ShellStatePatch,
    meta_update,
    session_patch_update,
    state_patch_update,
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


def print_payload(payload: object, *, as_json: bool, emit_text, output_writer=None) -> int:
    if as_json:
        emit_text(output_writer, meta_update(json.dumps(payload, ensure_ascii=True, indent=2)))
        return 0
    if isinstance(payload, list):
        print_rows(payload, emit_text=emit_text, output_writer=output_writer)
        return 0
    if isinstance(payload, dict):
        if print_status_payload(payload, emit_text=emit_text, output_writer=output_writer):
            return 0
        if print_doctor_payload(payload, emit_text=emit_text, output_writer=output_writer):
            return 0
        if print_replay_payload(payload, emit_text=emit_text, output_writer=output_writer):
            return 0
        display_summary = payload.get("display_summary")
        if display_summary:
            emit_text(output_writer, meta_update(str(display_summary)))
        else:
            emit_text(output_writer, meta_update(str(payload.get("message", json.dumps(payload, ensure_ascii=True)))))
        if "run_id" in payload:
            emit_text(
                output_writer,
                meta_update(
                    f"run_id: {payload['run_id']}",
                    state_patch=ShellStatePatch(current_run_id=str(payload["run_id"])),
                    session_patch=SessionStatePatch(last_run_id=str(payload["run_id"])),
                ),
            )
        if payload.get("status"):
            emit_text(
                output_writer,
                meta_update(
                    f"status: {payload['status']}",
                    state_patch=ShellStatePatch(run_status_label=str(payload["status"])),
                ),
            )
        if payload.get("intent"):
            emit_text(output_writer, meta_update(f"intent: {payload['intent']}"))
        if payload.get("result"):
            emit_text(output_writer, meta_update(json.dumps(payload["result"], ensure_ascii=True, indent=2)))
        return 0
    emit_text(output_writer, meta_update(json.dumps(payload, ensure_ascii=True, indent=2)))
    return 0


def print_rows(rows: list[object], *, emit_text, output_writer=None) -> None:
    if not rows:
        emit_text(output_writer, meta_update("No runs."))
        return
    latest_run_id = None
    latest_status = None
    for row in rows:
        if not isinstance(row, dict):
            emit_text(output_writer, meta_update(json.dumps(row, ensure_ascii=True)))
            continue
        run_id = str(row.get("run_id") or "unknown")
        status = str(row.get("status") or "unknown")
        if latest_run_id is None and row.get("run_id"):
            latest_run_id = run_id
        if latest_status is None and row.get("status"):
            latest_status = status
        intent = str(row.get("intent") or "unknown")
        display_summary = str(row.get("display_summary") or row.get("message") or "").strip()
        line = f"{run_id}  [{status}]  {intent}"
        if display_summary:
            line += f"  {display_summary}"
        emit_text(output_writer, meta_update(line))
    if latest_run_id is not None or latest_status is not None:
        emit_run_context_feed(
            latest_run_id,
            latest_status,
            emit_text=emit_text,
            output_writer=output_writer,
        )


def print_status_payload(payload: dict, *, emit_text, output_writer=None) -> bool:
    if "recent_runs" not in payload or "status_counts" not in payload:
        return False
    emit_text(output_writer, meta_update(f"project_root: {payload.get('project_root')}"))
    emit_text(output_writer, meta_update(f"provider: {payload.get('provider_name')} / model: {payload.get('default_model')}"))
    emit_text(output_writer, meta_update(f"qdrant_mode: {payload.get('qdrant_mode')} / collection: {payload.get('qdrant_collection_name')}"))
    emit_text(output_writer, meta_update(f"total_runs: {payload.get('total_runs')}"))
    status_counts = payload.get("status_counts") or {}
    if status_counts:
        counts_line = ", ".join(f"{key}={value}" for key, value in sorted(status_counts.items()))
        emit_text(output_writer, meta_update(f"status_counts: {counts_line}"))
    recent_runs = payload.get("recent_runs") or []
    if recent_runs:
        emit_text(output_writer, meta_update("recent_runs:"))
        for row in recent_runs:
            run_id = str(row.get("run_id") or "unknown")
            status = str(row.get("status") or "unknown")
            intent = str(row.get("intent") or "unknown")
            display_summary = str(row.get("display_summary") or row.get("message") or "").strip()
            line = f"  {run_id}  [{status}]  {intent}"
            if display_summary:
                line += f"  {display_summary}"
            emit_text(output_writer, meta_update(line))
    latest_run = recent_runs[0] if recent_runs else None
    latest_status = None
    latest_run_id = None
    if isinstance(latest_run, dict):
        if latest_run.get("run_id"):
            latest_run_id = str(latest_run["run_id"])
        if latest_run.get("status"):
            latest_status = str(latest_run["status"])
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
    request = payload.get("request") or {}
    summary = payload.get("summary") or {}
    replay_run_id = str(payload.get("run_id") or "unknown_run")
    emit_text(
        output_writer,
        meta_update(
            f"run_id: {replay_run_id}",
            state_patch=ShellStatePatch(current_run_id=replay_run_id),
            session_patch=SessionStatePatch(
                last_run_id=replay_run_id,
            ),
        ),
    )
    emit_text(output_writer, meta_update(f"intent: {request.get('intent') or 'unknown'}"))
    emit_text(output_writer, meta_update(f"query: {request.get('user_query') or ''}"))
    if payload.get("display_summary"):
        emit_text(output_writer, meta_update(f"summary: {payload['display_summary']}"))
    if summary.get("status"):
        emit_text(
            output_writer,
            meta_update(
                f"status: {summary.get('status')}",
                state_patch=ShellStatePatch(run_status_label=str(summary.get("status"))),
            ),
        )
    emit_text(output_writer, meta_update(f"events: {len(payload.get('events') or [])}"))
    emit_text(output_writer, meta_update(f"blackboard_entries: {len(payload.get('blackboard') or [])}"))
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
        emit_text(output_writer, meta_update(f"{group_name}:"))
        for row in rows:
            name = str(row.get("name") or "unknown_check")
            detail = row.get("detail") or row.get("path") or ""
            suffix = f"  {detail}" if detail else ""
            emit_text(output_writer, meta_update(f"  {name}{suffix}"))
    return True
