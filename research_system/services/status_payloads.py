from __future__ import annotations


def status_header_fields(payload: dict) -> list[tuple[str, str]]:
    return [
        ("project_root", str(payload.get("project_root"))),
        ("provider", f"{payload.get('provider_name')} / model: {payload.get('default_model')}"),
        ("qdrant_mode", f"{payload.get('qdrant_mode')} / collection: {payload.get('qdrant_collection_name')}"),
        ("total_runs", str(payload.get("total_runs"))),
    ]


def group_doctor_checks(checks: list[object]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {"failed": [], "passed": [], "skipped": []}
    for item in checks:
        if not isinstance(item, dict):
            continue
        key = str(item.get("status") or "skipped")
        grouped.setdefault(key, []).append(item)
    return grouped


def render_doctor_check_line(row: dict) -> str:
    name = str(row.get("name") or "unknown_check")
    detail = row.get("detail") or row.get("path") or ""
    suffix = f"  {detail}" if detail else ""
    return f"  {name}{suffix}"
