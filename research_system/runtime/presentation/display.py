from __future__ import annotations

import re
from typing import Any

_CHINESE_TEXT = re.compile(r"[\u3400-\u9fff]")
_TECHNICAL_ERROR_TEXT = re.compile(
    r"(?:traceback|stack trace|[A-Z][A-Za-z0-9_]*(?:Error|Exception)\b|"
    r"RemoteDisconnected|connection aborted|socket|econn|errno|httpx|"
    r"requests\.|urllib|ssl|tcp)",
    re.IGNORECASE,
)
_MACHINE_FIELDS = frozenset(
    {
        "action",
        "next_action",
        "kind",
        "code",
        "error_code",
        "reason_code",
        "reason_codes",
    }
)


def build_display_summary(
    *, result_payload: dict | None, summary_payload: dict | None
) -> str | None:
    report_payload = _report_payload(result_payload)
    review_summary = (
        report_payload.get("review_summary")
        if isinstance(report_payload, dict)
        else None
    )
    if isinstance(review_summary, dict):
        summary_text = str(review_summary.get("summary_text") or "").strip()
        if summary_text:
            return summary_text

    if isinstance(report_payload, dict):
        conclusion = str(report_payload.get("conclusion") or "").strip()
        if conclusion:
            return _compact_text(conclusion)

    message = str((summary_payload or {}).get("message") or "").strip()
    status = str(
        (summary_payload or {}).get("status")
        or (result_payload or {}).get("status")
        or ""
    ).strip()
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


def runtime_status_message(status: str, *, subject: str = "Agent OS 运行") -> str:
    label = {
        "received": "已接收",
        "identifying_intent": "正在识别意图",
        "needs_clarification": "需要补充信息",
        "ready": "已就绪",
        "running": "运行中",
        "paused": "已暂停",
        "waiting_approval": "等待审批",
        "waiting_external": "等待外部服务",
        "gated": "被质量门禁或审批阻止",
        "completed": "已完成",
        "completed_partial": "已部分完成",
        "cancelled": "已取消",
        "failed": "执行失败",
        "timed_out": "执行超时",
    }.get(str(status or ""), str(status or "未知状态"))
    return f"{subject}{label}。"


def localize_runtime_error(
    value: Any, *, fallback: str = "运行失败，请稍后重试。"
) -> str:
    message = str(value or "").strip()
    if (
        message
        and _CHINESE_TEXT.search(message)
        and not _TECHNICAL_ERROR_TEXT.search(message)
    ):
        return message
    lowered = message.lower()
    status = re.search(r"(?:error code|status(?: code)?)\D{0,8}(\d{3})", message, re.IGNORECASE)
    suffix = f"（HTTP {status.group(1)}）" if status else ""
    if "timeout" in lowered or "timed out" in lowered:
        return f"运行超时{suffix}，请稍后重试。"
    if "approval" in lowered and any(
        word in lowered for word in ("reject", "revoke", "expire", "denied")
    ):
        return "审批已被拒绝、撤销或过期。"
    if "approval" in lowered and any(
        word in lowered for word in ("required", "waiting")
    ):
        return "当前操作需要审批后才能继续。"
    if "cancel" in lowered:
        return "运行已取消。"
    if any(
        word in lowered
        for word in ("unavailable", "connection", "disconnected", "econn")
    ):
        return f"外部服务暂不可用{suffix}，请稍后重试。"
    if "not found" in lowered:
        return "未找到运行所需的资源。"
    if any(
        word in lowered for word in ("invalid", "missing", "required", "unsupported")
    ):
        return "运行参数或依赖不符合要求。"
    return str(fallback or "运行失败，请稍后重试。").strip() or "运行失败，请稍后重试。"


def localize_runtime_event(event: dict[str, Any]) -> dict[str, Any]:
    public = localize_public_error_fields(dict(event))
    event_type = str(public.get("event_type") or "").lower()
    event_status = str(public.get("status") or "").lower()
    is_failure = any(
        marker in event_type
        for marker in (
            "failed",
            "failure",
            "rejected",
            "timed_out",
            "cancelled",
            "gated",
            "error",
        )
    ) or event_status in {
        "failed",
        "timed_out",
        "cancelled",
        "gated",
        "rejected",
        "needs_review",
    }
    for key in ("error", "error_summary"):
        if public.get(key):
            public[key] = localize_runtime_error(public[key])
    if is_failure:
        for key in (
            "message",
            "reason",
            "current_action",
            "output_summary",
        ):
            if public.get(key):
                public[key] = localize_runtime_error(public[key])
    return public


def localize_public_error_fields(
    value: Any, *, field_name: str = "", failed_context: bool = False
) -> Any:
    """Localize display text without rewriting machine-readable recovery contracts."""
    if isinstance(value, dict):
        metadata = (
            value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
        )
        raw_status = (
            value.get("status")
            or value.get("verdict")
            or value.get("availability")
            or value.get("conclusion_status")
            or value.get("delivery_status")
            or metadata.get("status")
        )
        status = str(raw_status or "").lower()
        failure_statuses = {
            "failed",
            "timed_out",
            "gated",
            "rejected",
            "cancelled",
            "revoked",
            "expired",
            "fail",
            "needs_review",
            "data_gap",
            "degraded",
            "provider_unavailable",
            "unavailable",
            "parse_failed",
            "rate_limited",
            "error",
            "blocked",
            "unhealthy",
            "synthesis_unavailable",
        }
        current_failed = (
            status in failure_statuses if raw_status is not None else failed_context
        )
        return {
            key: localize_public_error_fields(
                item, field_name=str(key), failed_context=current_failed
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            localize_public_error_fields(
                item, field_name=field_name, failed_context=failed_context
            )
            for item in value
        ]
    normalized_key = field_name.lower()
    if normalized_key in _MACHINE_FIELDS:
        return value
    is_error_field = normalized_key in {
        "error",
        "errors",
        "error_message",
        "error_summary",
        "last_error",
        "load_error",
        "degrade_reason",
        "failure_reason",
        "diagnostic",
        "diagnostics",
        "stderr",
        "stdout",
    } or normalized_key.endswith(("_error", "_errors"))
    is_failed_copy = failed_context and normalized_key in {
        "message",
        "reason",
        "status_message",
        "details",
        "detail",
        "display_summary",
        "current_action",
        "output_summary",
        "output",
        "content",
        "note",
        "summary",
    }
    if isinstance(value, str) and (is_error_field or is_failed_copy):
        return localize_runtime_error(value)
    return value


__all__ = [
    "build_display_summary",
    "localize_public_error_fields",
    "localize_runtime_error",
    "localize_runtime_event",
    "runtime_status_message",
]
