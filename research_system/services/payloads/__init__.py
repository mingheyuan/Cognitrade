from __future__ import annotations

from importlib import import_module

__all__ = [
    "append_intent_query_summary_status",
    "append_run_id",
    "events_payload_fields",
    "group_doctor_checks",
    "latest_run_context",
    "render_doctor_check_line",
    "render_event_row_text",
    "render_labeled_field_line",
    "render_run_row_text",
    "render_status_counts_text",
    "render_trace_row_text",
    "replay_payload_fields",
    "request_summary",
    "run_payload_fields",
    "status_header_fields",
    "steps_payload_fields",
    "subagent_payload_fields",
    "trace_payload_fields",
]

_EXPORTS = {
    "append_intent_query_summary_status": (
        "research_system.services.payloads.runs.run_payload_common_fields",
        "append_intent_query_summary_status",
    ),
    "append_run_id": ("research_system.services.payloads.runs.run_payload_common_fields", "append_run_id"),
    "events_payload_fields": ("research_system.services.payloads.runs.run_payload_fields", "events_payload_fields"),
    "group_doctor_checks": ("research_system.services.payloads.status_payloads", "group_doctor_checks"),
    "latest_run_context": ("research_system.services.payloads.runs.run_payload_runtime", "latest_run_context"),
    "render_doctor_check_line": (
        "research_system.services.payloads.status_payloads",
        "render_doctor_check_line",
    ),
    "render_event_row_text": (
        "research_system.services.payloads.runs.run_payload_renderers",
        "render_event_row_text",
    ),
    "render_labeled_field_line": (
        "research_system.services.payloads.runs.run_payload_renderers",
        "render_labeled_field_line",
    ),
    "render_run_row_text": (
        "research_system.services.payloads.runs.run_payload_renderers",
        "render_run_row_text",
    ),
    "render_status_counts_text": (
        "research_system.services.payloads.runs.run_payload_renderers",
        "render_status_counts_text",
    ),
    "render_trace_row_text": (
        "research_system.services.payloads.runs.run_payload_renderers",
        "render_trace_row_text",
    ),
    "replay_payload_fields": ("research_system.services.payloads.runs.run_payload_fields", "replay_payload_fields"),
    "request_summary": ("research_system.services.payloads.runs.run_payload_common_fields", "request_summary"),
    "run_payload_fields": ("research_system.services.payloads.runs.run_payload_fields", "run_payload_fields"),
    "status_header_fields": ("research_system.services.payloads.status_payloads", "status_header_fields"),
    "steps_payload_fields": ("research_system.services.payloads.runs.run_payload_fields", "steps_payload_fields"),
    "subagent_payload_fields": (
        "research_system.services.payloads.runs.run_payload_fields",
        "subagent_payload_fields",
    ),
    "trace_payload_fields": ("research_system.services.payloads.runs.run_payload_fields", "trace_payload_fields"),
}


def __getattr__(name: str):
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
