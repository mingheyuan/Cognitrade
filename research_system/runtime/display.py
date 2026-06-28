from __future__ import annotations

from typing import Any


def build_display_summary(*, result_payload: dict | None, summary_payload: dict | None) -> str | None:
    report_payload = _report_payload(result_payload)
    review_summary = report_payload.get("review_summary") if isinstance(report_payload, dict) else None
    if isinstance(review_summary, dict):
        summary_text = str(review_summary.get("summary_text") or "").strip()
        if summary_text:
            return summary_text

    if isinstance(report_payload, dict):
        conclusion = str(report_payload.get("conclusion") or "").strip()
        if conclusion:
            return _compact_text(conclusion)

    message = str((summary_payload or {}).get("message") or "").strip()
    status = str((summary_payload or {}).get("status") or (result_payload or {}).get("status") or "").strip()
    if message and status and message != status:
        return _compact_text(f"{status}: {message}")
    if message:
        return _compact_text(message)
    if status:
        return _compact_text(status)
    return None


def _report_payload(result_payload: dict | None) -> dict[str, Any]:
    if not isinstance(result_payload, dict):
        return {}
    report = result_payload.get("report")
    if isinstance(report, dict):
        return report
    return result_payload


def _compact_text(text: str, limit: int = 160) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."
