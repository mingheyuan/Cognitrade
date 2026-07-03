from __future__ import annotations


def append_run_id(fields: list[tuple[str, str]], payload: dict) -> None:
    if payload.get("run_id") not in (None, ""):
        fields.append(("run_id", str(payload["run_id"])))


def append_intent_query_summary_status(
    fields: list[tuple[str, str]],
    *,
    payload: dict,
    intent: str,
    query: str,
    status: str | None,
    status_label: str = "status",
) -> None:
    fields.append(("intent", intent))
    fields.append(("query", query))
    if payload.get("display_summary") not in (None, ""):
        fields.append(("summary", str(payload["display_summary"])))
    if status not in (None, ""):
        fields.append((status_label, str(status)))


def request_summary(payload: dict) -> tuple[dict, dict]:
    request = payload.get("request") or {}
    summary = payload.get("summary") or {}
    return request, summary


__all__ = [
    "append_run_id",
    "append_intent_query_summary_status",
    "request_summary",
]
