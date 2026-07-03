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


def render_event_row_text(row: dict, *, indent: str = "") -> str:
    event_type = str(row.get("event_type") or "").strip() or "event"
    phase = str(row.get("phase") or "").strip()
    agent = str(row.get("agent") or "").strip()
    message = str(row.get("message") or "").strip()
    task_name = str(row.get("task_name") or "").strip()
    agent_path = str(row.get("agent_path") or "").strip()
    agent_label = str(row.get("agent_label") or "").strip()
    status = str(row.get("status") or "").strip()
    objective = str(row.get("objective") or "").strip()
    parts: list[str] = [f"{indent}[{event_type}]"]
    if phase or agent:
        parts.append(f"phase={phase or '-'} agent={agent or '-'}")
    if task_name:
        parts.append(task_name)
    elif agent_label:
        parts.append(agent_label)
    if agent_path:
        parts.append(f"path={agent_path}")
    if status:
        parts.append(f"[{status}]")
    if message:
        parts.append(message)
    if objective:
        parts.append(objective)
    if row.get("node_id"):
        parts.append(f"node_id={row['node_id']}")
    if row.get("invocation_id"):
        parts.append(f"call_id={row['invocation_id']}")
    if row.get("wait_timeout_seconds") not in (None, ""):
        parts.append(f"timeout={row['wait_timeout_seconds']}s")
    notes = row.get("notes") or []
    if isinstance(notes, list) and notes:
        parts.append(f"notes={'; '.join(str(item) for item in notes if str(item).strip())}")
    return "  ".join(part for part in parts if part)


def render_trace_row_text(row: dict, *, indent: str = "") -> str:
    event = str(row.get("event") or row.get("event_type") or "").strip()
    subagent_type = str(row.get("subagent_type") or "").strip()
    task_name = str(row.get("task_name") or "").strip()
    agent_path = str(row.get("agent_path") or "").strip()
    agent_label = str(row.get("agent_label") or "").strip()
    status = str(row.get("status") or "").strip()
    phase = str(row.get("phase") or "").strip()
    agent = str(row.get("agent") or "").strip()
    objective = str(row.get("objective") or "").strip()
    parts: list[str] = []
    label = task_name or agent_label or subagent_type or event or "trace"
    parts.append(f"{indent}{label}")
    if agent_label and agent_label != label:
        parts.append(f"label={agent_label}")
    if agent_path:
        parts.append(f"path={agent_path}")
    if status:
        parts.append(f"[{status}]")
    if phase or agent:
        parts.append(f"phase={phase or '-'} agent={agent or '-'}")
    if objective:
        parts.append(objective)
    if row.get("node_id"):
        parts.append(f"node_id={row['node_id']}")
    if row.get("invocation_id"):
        parts.append(f"call_id={row['invocation_id']}")
    if row.get("wait_timeout_seconds") not in (None, ""):
        parts.append(f"timeout={row['wait_timeout_seconds']}s")
    notes = row.get("notes") or []
    if isinstance(notes, list) and notes:
        parts.append(f"notes={'; '.join(str(item) for item in notes if str(item).strip())}")
    return "  ".join(part for part in parts if part)
