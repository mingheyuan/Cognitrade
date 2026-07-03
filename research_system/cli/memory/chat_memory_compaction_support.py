from __future__ import annotations

from typing import Any

from research_system.cli.memory.chat_thread_queries import (
    ensure_session_thread,
    thread_topic_title,
)
from research_system.cli.memory.chat_thread_store import ChatThreadStore
from research_system.runtime.platform.settings import Settings

_AUTO_COMPACT_TRIGGER_TURNS = 18
_AUTO_COMPACT_RETAIN_TURNS = 8


def compact_session_thread(*, settings: Settings, state) -> dict[str, Any]:
    thread_id = ensure_session_thread(settings=settings, state=state)
    store = ChatThreadStore(settings)
    turns = store.read_all_turns(thread_id=thread_id)
    previous_summaries = store.read_recent_summaries(thread_id=thread_id, limit=2)
    latest_summary = previous_summaries[-1] if previous_summaries else {}
    previous_covered_turn_count = int((latest_summary or {}).get("covered_turn_count") or 0)
    delta_turns = [turn for turn in turns if int(turn.get("turn_seq") or 0) > previous_covered_turn_count]
    if not delta_turns and turns:
        delta_turns = turns[-min(len(turns), _AUTO_COMPACT_RETAIN_TURNS):]
    summary_text = build_thread_summary_text(
        state=state,
        turns=delta_turns,
        previous_summaries=previous_summaries,
        covered_turn_count_before=previous_covered_turn_count,
        total_turn_count=len(turns),
    )
    last_turn_at = None
    if turns:
        last_turn_at = str(turns[-1].get("recorded_at") or "").strip() or None
    covered_turn_count = max((int(turn.get("turn_seq") or 0) for turn in turns), default=previous_covered_turn_count)
    payload = store.write_thread_summary(
        thread_id=thread_id,
        summary_text=summary_text,
        last_turn_at=last_turn_at,
        source_turn_count=len(turns),
        covered_turn_count=covered_turn_count,
        delta_turn_count=len(delta_turns),
        metadata={
            "last_user_query": getattr(state, "last_user_query", None),
            "last_result_intent": getattr(state, "last_result_intent", None),
            "last_result_summary": getattr(state, "last_result_summary", None),
            "last_result_follow_up": getattr(state, "last_result_follow_up", None),
            "last_run_id": getattr(state, "last_run_id", None),
            "previous_summary_count": len(previous_summaries),
            "delta_turn_seq_start": min((int(turn.get("turn_seq") or 0) for turn in delta_turns), default=None),
            "delta_turn_seq_end": max((int(turn.get("turn_seq") or 0) for turn in delta_turns), default=None),
            "topic_title": _summary_topic_title(state=state, latest_summary=latest_summary),
        },
    )
    if len(turns) > _AUTO_COMPACT_RETAIN_TURNS:
        archived = turns[:-_AUTO_COMPACT_RETAIN_TURNS]
        store.archive_turns(thread_id=thread_id, rows=archived)
        store.replace_turns(thread_id=thread_id, rows=turns[-_AUTO_COMPACT_RETAIN_TURNS:])
    return payload


def build_thread_summary_text(
    *,
    state,
    turns: list[dict[str, Any]],
    previous_summaries: list[dict[str, Any]] | None = None,
    covered_turn_count_before: int = 0,
    total_turn_count: int | None = None,
) -> str:
    lines: list[str] = []
    last_user_query = str(getattr(state, "last_user_query", None) or "").strip()
    last_intent = str(getattr(state, "last_result_intent", None) or "").strip()
    last_summary = str(getattr(state, "last_result_summary", None) or "").strip()
    last_conclusion = str(getattr(state, "last_result_conclusion", None) or "").strip()
    last_follow_up = str(getattr(state, "last_result_follow_up", None) or "").strip()
    last_run_id = str(getattr(state, "last_run_id", None) or "").strip()
    pending_question = str(getattr(state, "pending_clarification_question", None) or "").strip()

    if last_user_query:
        lines.append(f"recent_query: {last_user_query}")
    if last_intent:
        lines.append(f"recent_intent: {last_intent}")
    if last_summary:
        lines.append(f"recent_summary: {last_summary}")
    if last_conclusion and last_conclusion != last_summary:
        lines.append(f"recent_conclusion: {last_conclusion}")
    if last_follow_up:
        lines.append(f"recent_follow_up: {last_follow_up}")
    if last_run_id:
        lines.append(f"recent_run_id: {last_run_id}")
    if pending_question:
        lines.append(f"open_question: {pending_question}")
    if covered_turn_count_before > 0:
        lines.append(f"covered_turns_before: {covered_turn_count_before}")
    if total_turn_count is not None:
        lines.append(f"covered_turns_after: {total_turn_count}")
    lines.append(f"delta_turn_count: {len(turns)}")
    if previous_summaries:
        previous_text = str((previous_summaries[-1] or {}).get("summary_text") or "").strip()
        if previous_text:
            compact_previous = previous_text.replace("\n", " ")
            if len(compact_previous) > 180:
                compact_previous = f"{compact_previous[:177]}..."
            lines.append(f"previous_summary: {compact_previous}")

    turn_descriptions: list[str] = []
    for turn in turns[-8:]:
        role = str(turn.get("role") or "").strip() or "unknown"
        turn_type = str(turn.get("turn_type") or "").strip() or "turn"
        content = str(turn.get("content") or "").strip()
        if not content:
            continue
        compact = content.replace("\n", " ")
        if len(compact) > 120:
            compact = f"{compact[:117]}..."
        turn_descriptions.append(f"{role}/{turn_type}: {compact}")
    if turn_descriptions:
        lines.append("recent_turns:")
        lines.extend(turn_descriptions)
    return "\n".join(lines).strip() or "recent_turns: empty"


def auto_compact_thread_if_needed(
    *,
    settings: Settings,
    state,
    store: ChatThreadStore | None = None,
    thread_id: str | None = None,
) -> None:
    resolved_thread_id = thread_id or ensure_session_thread(settings=settings, state=state)
    resolved_store = store or ChatThreadStore(settings)
    turns = resolved_store.read_all_turns(thread_id=resolved_thread_id)
    if len(turns) < _AUTO_COMPACT_TRIGGER_TURNS:
        return
    compact_session_thread(settings=settings, state=state)


def _summary_topic_title(*, state, latest_summary: dict[str, Any]) -> str | None:
    latest_metadata = (latest_summary or {}).get("metadata") or {}
    return thread_topic_title(
        last_user_query=getattr(state, "last_user_query", None),
        last_result_summary=getattr(state, "last_result_summary", None),
        topic_title=latest_metadata.get("topic_title") if isinstance(latest_metadata, dict) else None,
    )
