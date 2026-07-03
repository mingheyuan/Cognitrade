from __future__ import annotations


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
