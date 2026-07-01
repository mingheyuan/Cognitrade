from __future__ import annotations


def render_labeled_field_line(label: str, value: object, *, indent: str = "") -> str:
    return f"{indent}{label}: {value}"


def render_run_row_text(row: dict, *, indent: str = "") -> str:
    run_id = str(row.get("run_id") or "unknown")
    status = str(row.get("status") or "unknown")
    intent = str(row.get("intent") or "unknown")
    display_summary = str(row.get("display_summary") or row.get("message") or "").strip()
    line = f"{indent}{run_id}  [{status}]  {intent}"
    if display_summary:
        line += f"  {display_summary}"
    return line


def render_status_counts_text(status_counts: dict) -> str:
    return ", ".join(f"{key}={value}" for key, value in sorted(status_counts.items()))


def run_payload_fields(payload: dict) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    if payload.get("run_id") not in (None, ""):
        fields.append(("run_id", str(payload["run_id"])))
    if payload.get("status") not in (None, ""):
        fields.append(("status", str(payload["status"])))
    if payload.get("intent") not in (None, ""):
        fields.append(("intent", str(payload["intent"])))
    return fields


def replay_payload_fields(payload: dict) -> list[tuple[str, str]]:
    request = payload.get("request") or {}
    summary = payload.get("summary") or {}
    fields: list[tuple[str, str]] = []
    if payload.get("run_id") not in (None, ""):
        fields.append(("run_id", str(payload["run_id"])))
    fields.append(("intent", str(request.get("intent") or "unknown")))
    fields.append(("query", str(request.get("user_query") or "")))
    if payload.get("display_summary") not in (None, ""):
        fields.append(("summary", str(payload["display_summary"])))
    if summary.get("status") not in (None, ""):
        fields.append(("status", str(summary.get("status"))))
    fields.append(("events", str(len(payload.get("events") or []))))
    fields.append(("blackboard_entries", str(len(payload.get("blackboard") or []))))
    return fields


def latest_run_context(rows: list[object]) -> tuple[str | None, str | None]:
    latest_run_id = None
    latest_status = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        if latest_run_id is None and row.get("run_id"):
            latest_run_id = str(row["run_id"])
        if latest_status is None and row.get("status"):
            latest_status = str(row["status"])
        if latest_run_id is not None and latest_status is not None:
            break
    return latest_run_id, latest_status
