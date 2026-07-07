from __future__ import annotations

from typing import Any

from research_system.adapters.storage.local_memory_adapter import LocalMemoryAdapter
from research_system.runtime.platform.settings import Settings
from research_system.runtime.presentation.display import build_display_summary
from research_system.runtime.storage.jsonl import read_json
from research_system.runtime.storage.runs.run_store import RunStore
from research_system.services.memory import MemoryService


def build_recent_research_context(*, settings: Settings, state, limit: int = 1) -> list[dict[str, Any]]:
    if has_recent_chat_memory(state):
        context = context_from_record(state_memory_record(state))
        return [context] if context is not None else []

    service = MemoryService(LocalMemoryAdapter(settings))
    rows = service.read_recent_chat_memory(limit=limit)
    contexts = []
    for row in rows:
        content = row.get("content")
        if not isinstance(content, dict):
            continue
        context = context_from_record(content)
        if context is not None:
            contexts.append(context)
    if contexts:
        return contexts

    fallback = load_latest_run_memory(settings)
    if fallback is None:
        return []
    context = context_from_record(fallback)
    return [context] if context is not None else []


def has_recent_chat_memory(state) -> bool:
    return bool(
        getattr(state, "last_user_query", None)
        or getattr(state, "last_result_summary", None)
        or getattr(state, "last_result_conclusion", None)
        or getattr(state, "last_result_follow_up", None)
    )


def load_latest_run_memory(settings: Settings) -> dict[str, Any] | None:
    rows = RunStore(settings.runs_root).list_runs()
    if not rows:
        return None
    latest = rows[0]
    run_id = str(latest.get("run_id") or "").strip()
    if not run_id:
        return None
    run_root = settings.runs_root / run_id
    request_payload = read_json(run_root / "request.json") or {}
    result_payload = read_json(run_root / "result.json") or {}
    summary_payload = read_json(run_root / "summary.json") or {}
    query = str(request_payload.get("user_query") or "").strip()
    intent = str(latest.get("intent") or request_payload.get("intent") or "").strip()
    summary = build_display_summary(result_payload=result_payload, summary_payload=summary_payload)
    conclusion = _extract_conclusion({"result": result_payload})
    if not any([query, intent, summary, conclusion]):
        return None
    return {
        "query": query or None,
        "intent": intent or None,
        "summary": summary,
        "conclusion": conclusion,
        "run_id": run_id,
    }


def state_memory_record(state) -> dict[str, Any]:
    return {
        "query": getattr(state, "last_user_query", None),
        "intent": getattr(state, "last_result_intent", None),
        "summary": getattr(state, "last_result_summary", None),
        "conclusion": getattr(state, "last_result_conclusion", None),
        "follow_up": getattr(state, "last_result_follow_up", None),
        "run_id": getattr(state, "last_run_id", None),
        "thread_id": getattr(state, "thread_id", None),
    }


def context_from_record(record: dict[str, Any]) -> dict[str, Any] | None:
    query = str(record.get("query") or "").strip()
    intent = str(record.get("intent") or "").strip()
    summary = str(record.get("summary") or "").strip()
    conclusion = str(record.get("conclusion") or "").strip()
    follow_up = str(record.get("follow_up") or "").strip()
    run_id = str(record.get("run_id") or "").strip()
    thread_id = str(record.get("thread_id") or "").strip()
    if not any([query, summary, conclusion, follow_up, run_id]):
        return None
    return {
        "type": "recent_research_memory",
        "thread_id": thread_id or None,
        "query": query or None,
        "intent": intent or None,
        "summary": summary or None,
        "conclusion": conclusion or None,
        "follow_up": follow_up or None,
        "run_id": run_id or None,
    }


def _extract_conclusion(payload: dict[str, Any]) -> str | None:
    result = payload.get("result")
    if not isinstance(result, dict):
        return None
    report = result.get("report")
    if isinstance(report, dict):
        conclusion = str(report.get("conclusion") or "").strip()
        if conclusion:
            return conclusion
    conclusion = str(result.get("conclusion") or "").strip()
    return conclusion or None
