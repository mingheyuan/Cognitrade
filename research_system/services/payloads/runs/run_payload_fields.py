from __future__ import annotations

from research_system.services.payloads.runs.run_payload_common_fields import (
    append_intent_query_summary_status,
    append_run_id,
    request_summary,
)


def run_payload_fields(payload: dict) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    append_run_id(fields, payload)
    if payload.get("status") not in (None, ""):
        fields.append(("status", str(payload["status"])))
    if payload.get("intent") not in (None, ""):
        fields.append(("intent", str(payload["intent"])))
    return fields


def replay_payload_fields(payload: dict) -> list[tuple[str, str]]:
    request, summary = request_summary(payload)
    fields: list[tuple[str, str]] = []
    append_run_id(fields, payload)
    append_intent_query_summary_status(
        fields,
        payload=payload,
        intent=str(request.get("intent") or "unknown"),
        query=str(request.get("user_query") or ""),
        status=summary.get("status"),
    )
    fields.append(("events", str(len(payload.get("events") or []))))
    fields.append(("blackboard_entries", str(len(payload.get("blackboard") or []))))
    return fields


def trace_payload_fields(payload: dict) -> list[tuple[str, str]]:
    request, summary = request_summary(payload)
    event_counts = payload.get("event_counts") or {}
    fields: list[tuple[str, str]] = []
    append_run_id(fields, payload)
    append_intent_query_summary_status(
        fields,
        payload=payload,
        intent=str(request.get("intent") or "unknown"),
        query=str(request.get("user_query") or ""),
        status=summary.get("status"),
    )
    fields.append(("subagent_trace", str(event_counts.get("subagent_trace") or 0)))
    fields.append(("dynamic_trace", str(event_counts.get("dynamic_trace") or 0)))
    fields.append(("wait_trace", str(event_counts.get("wait_trace") or 0)))
    return fields


def events_payload_fields(payload: dict) -> list[tuple[str, str]]:
    request, summary = request_summary(payload)
    event_counts = payload.get("event_counts") or {}
    fields: list[tuple[str, str]] = []
    append_run_id(fields, payload)
    append_intent_query_summary_status(
        fields,
        payload=payload,
        intent=str(request.get("intent") or "unknown"),
        query=str(request.get("user_query") or ""),
        status=summary.get("status"),
    )
    fields.append(("events", str(event_counts.get("events") or 0)))
    fields.append(("event_types", str(event_counts.get("distinct_event_types") or 0)))
    return fields


def metrics_payload_fields(payload: dict) -> list[tuple[str, str]]:
    request, summary = request_summary(payload)
    counts = payload.get("counts") or {}
    fields: list[tuple[str, str]] = []
    append_run_id(fields, payload)
    append_intent_query_summary_status(
        fields,
        payload=payload,
        intent=str(request.get("intent") or "unknown"),
        query=str(request.get("user_query") or ""),
        status=summary.get("status"),
    )
    fields.append(("events", str(counts.get("events") or 0)))
    fields.append(("items", str(counts.get("items") or 0)))
    fields.append(("threads", str(counts.get("threads") or 0)))
    fields.append(("subagents", str(counts.get("subagents") or 0)))
    fields.append(("llm_calls", str(counts.get("llm_calls") or 0)))
    return fields


def steps_payload_fields(payload: dict) -> list[tuple[str, str]]:
    request, summary = request_summary(payload)
    step_counts = payload.get("step_counts") or {}
    fields: list[tuple[str, str]] = []
    append_run_id(fields, payload)
    append_intent_query_summary_status(
        fields,
        payload=payload,
        intent=str(payload.get("intent") or request.get("intent") or "unknown"),
        query=str(request.get("user_query") or ""),
        status=summary.get("status"),
    )
    if payload.get("strategy") not in (None, ""):
        fields.append(("strategy", str(payload.get("strategy"))))
    if payload.get("entry_strategy") not in (None, ""):
        fields.append(("entry_strategy", str(payload.get("entry_strategy"))))
    fields.append(("steps", str(step_counts.get("total") or 0)))
    fields.append(("active_steps", str(step_counts.get("active") or 0)))
    fields.append(("completed_steps", str(step_counts.get("completed") or 0)))
    fields.append(("pending_steps", str(step_counts.get("pending") or 0)))
    return fields


def subagent_payload_fields(payload: dict) -> list[tuple[str, str]]:
    request, summary = request_summary(payload)
    event_counts = payload.get("event_counts") or {}
    fields: list[tuple[str, str]] = []
    append_run_id(fields, payload)
    if payload.get("invocation_id") not in (None, ""):
        fields.append(("call_id", str(payload["invocation_id"])))
    append_intent_query_summary_status(
        fields,
        payload=payload,
        intent=str(request.get("intent") or "unknown"),
        query=str(request.get("user_query") or ""),
        status=summary.get("status"),
        status_label="run_status",
    )
    if payload.get("status") not in (None, ""):
        fields.append(("subagent_status", str(payload.get("status"))))
    if payload.get("task_name") not in (None, ""):
        fields.append(("task_name", str(payload.get("task_name"))))
    if payload.get("agent_path") not in (None, ""):
        fields.append(("agent_path", str(payload.get("agent_path"))))
    if payload.get("subagent_type") not in (None, ""):
        fields.append(("subagent_type", str(payload.get("subagent_type"))))
    fields.append(("trace_rows", str(event_counts.get("trace_rows") or 0)))
    fields.append(("events", str(event_counts.get("events") or 0)))
    return fields


def thread_payload_fields(payload: dict) -> list[tuple[str, str]]:
    request, summary = request_summary(payload)
    thread_payload = payload.get("thread") or {}
    event_counts = payload.get("event_counts") or {}
    fields: list[tuple[str, str]] = []
    append_run_id(fields, payload)
    if payload.get("thread_id") not in (None, ""):
        fields.append(("thread_id", str(payload.get("thread_id"))))
    append_intent_query_summary_status(
        fields,
        payload=payload,
        intent=str(request.get("intent") or "unknown"),
        query=str(request.get("user_query") or ""),
        status=summary.get("status"),
    )
    if thread_payload.get("status") not in (None, ""):
        fields.append(("thread_status", str(thread_payload.get("status"))))
    fields.append(("events", str(event_counts.get("events") or 0)))
    fields.append(("subagent_trace", str(event_counts.get("subagent_trace") or 0)))
    fields.append(("items", str(event_counts.get("items") or 0)))
    return fields
