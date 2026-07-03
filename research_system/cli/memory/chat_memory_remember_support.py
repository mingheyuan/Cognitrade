from __future__ import annotations

from typing import Any

from research_system.adapters.storage.mem0_adapter import Mem0Adapter
from research_system.cli.memory.chat_memory_compaction_support import auto_compact_thread_if_needed
from research_system.cli.memory.chat_thread_queries import ensure_session_thread
from research_system.cli.memory.chat_thread_store import ChatThreadStore
from research_system.runtime.platform.settings import Settings
from research_system.services.memory import MemoryService, build_memory_recorded_at

_TERMINAL_RESEARCH_STATUSES = {"completed", "failed", "timed_out", "cancelled"}


def remember_chat_research_result(
    *,
    settings: Settings,
    state,
    query: str,
    intent: str | None,
    payload: object,
) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    status = str(payload.get("status") or "").strip()
    if status not in _TERMINAL_RESEARCH_STATUSES:
        return None
    run_id = str(payload.get("run_id") or "").strip()
    resolved_intent = str(payload.get("intent") or intent or "").strip()
    if not query.strip() or not run_id or not resolved_intent:
        return None
    summary = str(payload.get("display_summary") or "").strip() or None
    conclusion = extract_conclusion(payload)
    follow_up = extract_follow_up(payload)
    recorded_at = build_memory_recorded_at()
    thread_id = ensure_session_thread(settings=settings, state=state)
    store = ChatThreadStore(settings)
    store.append_turn(
        thread_id=thread_id,
        role="user",
        content=query,
        turn_type="research_query",
        intent=resolved_intent,
        run_id=run_id,
        metadata={"status": status},
        recorded_at=recorded_at,
    )
    store.append_turn(
        thread_id=thread_id,
        role="assistant",
        content=conclusion or summary or "",
        turn_type="research_result",
        intent=resolved_intent,
        run_id=run_id,
        metadata={"status": status, "summary": summary, "conclusion": conclusion, "follow_up": follow_up},
        recorded_at=recorded_at,
    )
    auto_compact_thread_if_needed(settings=settings, state=state, store=store, thread_id=thread_id)
    return MemoryService(Mem0Adapter(settings)).write_chat_research_memory(
        query=query,
        intent=resolved_intent,
        run_id=run_id,
        summary=summary,
        conclusion=conclusion,
        follow_up=follow_up,
        status=status,
        thread_id=thread_id,
    )


def remember_basic_chat_turn(
    *,
    settings: Settings,
    state,
    query: str,
    answer: str,
) -> None:
    if not query.strip() or not answer.strip():
        return
    thread_id = ensure_session_thread(settings=settings, state=state)
    store = ChatThreadStore(settings)
    recorded_at = build_memory_recorded_at()
    store.append_turn(
        thread_id=thread_id,
        role="user",
        content=query,
        turn_type="chat_query",
        recorded_at=recorded_at,
    )
    store.append_turn(
        thread_id=thread_id,
        role="assistant",
        content=answer,
        turn_type="chat_answer",
        recorded_at=recorded_at,
    )
    auto_compact_thread_if_needed(settings=settings, state=state, store=store, thread_id=thread_id)


def extract_conclusion(payload: dict[str, Any]) -> str | None:
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


def extract_follow_up(payload: dict[str, Any]) -> str | None:
    result = payload.get("result")
    if not isinstance(result, dict):
        return None
    report = result.get("report")
    if isinstance(report, dict):
        follow_up = str(report.get("follow_up") or "").strip()
        if follow_up:
            return follow_up
    follow_up = str(result.get("follow_up") or "").strip()
    return follow_up or None
