from __future__ import annotations

import re
from typing import Any

from research_system.adapters.storage.mem0_adapter import Mem0Adapter
from research_system.cli.chat_state import (
    apply_session_checkpoint_to_state,
    apply_session_state_patch_to_state,
    build_new_thread_session_patch,
    clear_recent_session_patch,
)
from research_system.cli.chat_thread_store import ChatThreadStore
from research_system.runtime.display import build_display_summary
from research_system.runtime.jsonl import read_json
from research_system.runtime.run_store import RunStore
from research_system.runtime.settings import Settings
from research_system.services.memory_service import MemoryService, build_memory_recorded_at

_TERMINAL_RESEARCH_STATUSES = {"completed", "failed", "timed_out", "cancelled"}
_THREAD_CONTEXT_LIMIT = 6
_AUTO_COMPACT_TRIGGER_TURNS = 18
_AUTO_COMPACT_RETAIN_TURNS = 8
_RELEVANT_MEMORY_LIMIT = 4
_RELEVANT_SUMMARY_SCAN_LIMIT = 8
_RELEVANT_ARCHIVE_SCAN_LIMIT = 40
_RELEVANT_CHAT_MEMORY_SCAN_LIMIT = 12
_RELEVANT_METHODOLOGY_SCAN_LIMIT = 8
_ENGLISH_QUERY_STOPWORDS = {
    "a",
    "about",
    "and",
    "are",
    "better",
    "compare",
    "continue",
    "for",
    "from",
    "how",
    "into",
    "just",
    "last",
    "more",
    "previous",
    "review",
    "than",
    "that",
    "the",
    "they",
    "this",
    "those",
    "what",
    "which",
    "who",
    "with",
}


def ensure_session_thread(*, settings: Settings, state) -> str:
    store = ChatThreadStore(settings)
    resolved = store.ensure_thread(getattr(state, "thread_id", None))
    state.thread_id = resolved
    return resolved


def start_new_session_thread(*, settings: Settings, state) -> str:
    store = ChatThreadStore(settings)
    resolved = store.ensure_thread(None)
    apply_session_state_patch_to_state(state, build_new_thread_session_patch(thread_id=resolved))
    return resolved


def switch_session_thread(*, settings: Settings, state, thread_id: str) -> bool:
    resolved = str(thread_id or "").strip()
    if not resolved:
        return False
    store = ChatThreadStore(settings)
    state.thread_id = store.ensure_thread(resolved)
    _clear_recent_session_memory(state)
    hydrate_session_from_persistent_memory(settings=settings, state=state)
    return True


def list_chat_threads(*, settings: Settings, limit: int = 10) -> list[dict[str, Any]]:
    rows = ChatThreadStore(settings).list_threads(limit=limit)
    enriched: list[dict[str, Any]] = []
    for row in rows:
        summary = ChatThreadStore(settings).load_thread_summary(thread_id=str(row.get("thread_id") or ""))
        metadata = summary.get("metadata") if isinstance(summary, dict) and isinstance(summary.get("metadata"), dict) else {}
        enriched.append(
            {
                **row,
                "topic_title": _thread_topic_title(
                    last_user_query=metadata.get("last_user_query"),
                    last_result_summary=metadata.get("last_result_summary"),
                    topic_title=metadata.get("topic_title"),
                ),
                "topic_intent": str(metadata.get("last_result_intent") or "").strip() or None,
            }
        )
    return enriched


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
    summary_text = _build_thread_summary_text(
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
            "topic_title": _thread_topic_title(
                last_user_query=getattr(state, "last_user_query", None),
                last_result_summary=getattr(state, "last_result_summary", None),
                topic_title=((latest_summary or {}).get("metadata") or {}).get("topic_title") if isinstance((latest_summary or {}).get("metadata"), dict) else None,
            ),
        },
    )
    if len(turns) > _AUTO_COMPACT_RETAIN_TURNS:
        archived = turns[:-_AUTO_COMPACT_RETAIN_TURNS]
        store.archive_turns(thread_id=thread_id, rows=archived)
        store.replace_turns(thread_id=thread_id, rows=turns[-_AUTO_COMPACT_RETAIN_TURNS:])
    return payload


def has_recent_chat_memory(state) -> bool:
    return bool(
        getattr(state, "last_user_query", None)
        or getattr(state, "last_result_summary", None)
        or getattr(state, "last_result_conclusion", None)
        or getattr(state, "last_result_follow_up", None)
    )


def hydrate_session_from_persistent_memory(*, settings: Settings, state) -> bool:
    if has_recent_chat_memory(state):
        return False
    thread_id = ensure_session_thread(settings=settings, state=state)
    store = ChatThreadStore(settings)
    checkpoint = store.load_thread_checkpoint(thread_id=thread_id)
    if checkpoint is not None:
        apply_session_checkpoint_to_state(state=state, checkpoint=checkpoint)
        return True
    turns = store.read_recent_turns(thread_id=thread_id, limit=_THREAD_CONTEXT_LIMIT)
    if turns:
        _apply_turns_to_state(state=state, turns=turns)
        return True
    record = MemoryService(Mem0Adapter(settings)).load_latest_chat_research_memory(thread_id=thread_id)
    if record is None:
        record = _load_latest_run_memory(settings)
    if record is None:
        return False
    _apply_record_to_state(state=state, record=record)
    return True


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
    conclusion = _extract_conclusion(payload)
    follow_up = _extract_follow_up(payload)
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
    _auto_compact_thread_if_needed(settings=settings, state=state, store=store, thread_id=thread_id)
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
    _auto_compact_thread_if_needed(settings=settings, state=state, store=store, thread_id=thread_id)


def build_recent_research_context(*, settings: Settings, state, limit: int = 1) -> list[dict[str, Any]]:
    if has_recent_chat_memory(state):
        context = _context_from_record(_state_memory_record(state))
        return [context] if context is not None else []

    service = MemoryService(Mem0Adapter(settings))
    rows = service.read_recent_chat_memory(limit=limit)
    contexts = []
    for row in rows:
        content = row.get("content")
        if not isinstance(content, dict):
            continue
        context = _context_from_record(content)
        if context is not None:
            contexts.append(context)
    if contexts:
        return contexts

    fallback = _load_latest_run_memory(settings)
    if fallback is None:
        return []
    context = _context_from_record(fallback)
    return [context] if context is not None else []


def build_thread_context(*, settings: Settings, state, limit: int = _THREAD_CONTEXT_LIMIT) -> list[dict[str, Any]]:
    return [
        *build_thread_profile_context(settings=settings, state=state),
        *build_thread_summary_context(settings=settings, state=state),
        *_build_thread_turn_context(settings=settings, state=state, limit=limit),
    ]


def build_chat_conversation_context(*, settings: Settings, state, query: str | None = None) -> list[dict[str, Any]]:
    return [
        *build_thread_context(settings=settings, state=state),
        *build_recent_research_context(settings=settings, state=state),
        *build_relevant_memory_context(settings=settings, state=state, query=query),
    ]


def current_thread_summary(*, settings: Settings, state) -> dict[str, Any]:
    thread_id = ensure_session_thread(settings=settings, state=state)
    store = ChatThreadStore(settings)
    turns = store.read_recent_turns(thread_id=thread_id, limit=_THREAD_CONTEXT_LIMIT)
    persisted_summary = store.load_thread_summary(thread_id=thread_id)
    persisted_checkpoint = store.load_thread_checkpoint(thread_id=thread_id)
    summary_metadata = (persisted_summary or {}).get("metadata") if isinstance((persisted_summary or {}).get("metadata"), dict) else {}
    return {
        "thread_id": thread_id,
        "turn_count": len(turns),
        "lifetime_turn_count": int(next((row.get("lifetime_turn_count") or 0 for row in store.list_threads(limit=1000) if str(row.get("thread_id") or "") == thread_id), 0)),
        "last_user_query": getattr(state, "last_user_query", None),
        "last_run_id": getattr(state, "last_run_id", None),
        "last_result_summary": summary_metadata.get("last_result_summary") or getattr(state, "last_result_summary", None),
        "last_result_follow_up": summary_metadata.get("last_result_follow_up") or getattr(state, "last_result_follow_up", None),
        "has_compact_summary": bool(isinstance(persisted_summary, dict) and str(persisted_summary.get("summary_text") or "").strip()),
        "has_checkpoint": bool(isinstance(persisted_checkpoint, dict) and isinstance(persisted_checkpoint.get("session_state"), dict)),
        "topic_title": _thread_topic_title(
            last_user_query=summary_metadata.get("last_user_query") or getattr(state, "last_user_query", None),
            last_result_summary=summary_metadata.get("last_result_summary") or getattr(state, "last_result_summary", None),
            topic_title=summary_metadata.get("topic_title"),
        ),
        "topic_intent": str(summary_metadata.get("last_result_intent") or getattr(state, "last_result_intent", None) or "").strip() or None,
        "open_question": str(getattr(state, "pending_clarification_question", None) or "").strip() or None,
        "recommended_next_step": summary_metadata.get("last_result_follow_up") or getattr(state, "last_result_follow_up", None),
        "thread_status": "clarification_pending" if str(getattr(state, "pending_clarification_question", None) or "").strip() else "active",
    }


def build_thread_profile_context(*, settings: Settings, state) -> list[dict[str, Any]]:
    summary = current_thread_summary(settings=settings, state=state)
    if not any(
        summary.get(key)
        for key in (
            "topic_title",
            "topic_intent",
            "last_user_query",
            "last_result_summary",
            "last_result_follow_up",
            "last_run_id",
            "open_question",
        )
    ):
        return []
    return [
        {
            "type": "chat_thread_profile",
            "thread_id": summary.get("thread_id"),
            "topic_title": summary.get("topic_title"),
            "topic_intent": summary.get("topic_intent"),
            "last_user_query": summary.get("last_user_query"),
            "last_result_summary": summary.get("last_result_summary"),
            "last_result_follow_up": summary.get("last_result_follow_up"),
            "last_run_id": summary.get("last_run_id"),
            "open_question": summary.get("open_question"),
            "recommended_next_step": summary.get("recommended_next_step"),
            "thread_status": summary.get("thread_status"),
            "turn_count": summary.get("turn_count"),
            "lifetime_turn_count": summary.get("lifetime_turn_count"),
            "has_compact_summary": summary.get("has_compact_summary"),
            "has_checkpoint": summary.get("has_checkpoint"),
        }
    ]


def build_thread_summary_context(*, settings: Settings, state) -> list[dict[str, Any]]:
    thread_id = ensure_session_thread(settings=settings, state=state)
    rows = ChatThreadStore(settings).read_recent_summaries(thread_id=thread_id, limit=2)
    contexts: list[dict[str, Any]] = []
    for index, payload in enumerate(rows):
        if not isinstance(payload, dict):
            continue
        summary_text = str(payload.get("summary_text") or "").strip()
        if not summary_text:
            continue
        contexts.append(
            {
                "type": "chat_thread_summary",
                "thread_id": thread_id,
                "summary": summary_text,
                "summary_index": index,
                "last_turn_at": str(payload.get("last_turn_at") or "").strip() or None,
                "source_turn_count": payload.get("source_turn_count"),
                "covered_turn_count": payload.get("covered_turn_count"),
                "delta_turn_count": payload.get("delta_turn_count"),
                "metadata": payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
            }
        )
    return contexts


def build_relevant_memory_context(
    *,
    settings: Settings,
    state,
    query: str | None,
    limit: int = _RELEVANT_MEMORY_LIMIT,
) -> list[dict[str, Any]]:
    normalized_query = str(query or "").strip()
    if not normalized_query or limit <= 0:
        return []
    query_terms = _extract_query_terms(normalized_query)
    if not query_terms:
        return []

    thread_id = ensure_session_thread(settings=settings, state=state)
    store = ChatThreadStore(settings)
    candidates: list[tuple[float, dict[str, Any]]] = []
    thread_profile = current_thread_summary(settings=settings, state=state)
    profile_blob = "\n".join(
        str(thread_profile.get(key) or "")
        for key in (
            "topic_title",
            "topic_intent",
            "last_user_query",
            "last_result_summary",
            "last_result_follow_up",
            "open_question",
            "last_run_id",
        )
    ).strip()
    if profile_blob:
        matched_terms, score = _score_query_match(query_terms=query_terms, blob=profile_blob)
        if score > 0:
            candidates.append(
                (
                    score,
                    {
                        "type": "relevant_thread_profile",
                        "thread_id": thread_profile.get("thread_id"),
                        "topic_title": thread_profile.get("topic_title"),
                        "topic_intent": thread_profile.get("topic_intent"),
                        "last_user_query": thread_profile.get("last_user_query"),
                        "last_result_summary": _trim_context_text(str(thread_profile.get("last_result_summary") or "").strip()) or None,
                        "last_result_follow_up": _trim_context_text(str(thread_profile.get("last_result_follow_up") or "").strip()) or None,
                        "last_run_id": thread_profile.get("last_run_id"),
                        "open_question": _trim_context_text(str(thread_profile.get("open_question") or "").strip()) or None,
                        "recommended_next_step": _trim_context_text(str(thread_profile.get("recommended_next_step") or "").strip()) or None,
                        "thread_status": thread_profile.get("thread_status"),
                        "matched_terms": matched_terms,
                        "match_score": round(score, 3),
                    },
                )
            )

    for payload in store.read_recent_summaries(thread_id=thread_id, limit=_RELEVANT_SUMMARY_SCAN_LIMIT):
        if not isinstance(payload, dict):
            continue
        summary_text = str(payload.get("summary_text") or "").strip()
        if not summary_text:
            continue
        matched_terms, score = _score_query_match(query_terms=query_terms, blob=summary_text)
        if score <= 0:
            continue
        candidates.append(
            (
                score,
                {
                    "type": "relevant_thread_summary",
                    "thread_id": thread_id,
                    "summary": summary_text,
                    "last_turn_at": str(payload.get("last_turn_at") or "").strip() or None,
                    "source_turn_count": payload.get("source_turn_count"),
                    "matched_terms": matched_terms,
                    "match_score": round(score, 3),
                },
            )
        )

    for turn in store.read_archived_turns(thread_id=thread_id, limit=_RELEVANT_ARCHIVE_SCAN_LIMIT):
        if not isinstance(turn, dict):
            continue
        content = str(turn.get("content") or "").strip()
        if not content:
            continue
        matched_terms, score = _score_query_match(query_terms=query_terms, blob=content)
        if score <= 0:
            continue
        candidates.append(
            (
                score,
                {
                    "type": "relevant_thread_archive",
                    "thread_id": thread_id,
                    "role": str(turn.get("role") or "").strip() or None,
                    "turn_type": str(turn.get("turn_type") or "").strip() or None,
                    "content": _trim_context_text(content),
                    "intent": str(turn.get("intent") or "").strip() or None,
                    "run_id": str(turn.get("run_id") or "").strip() or None,
                    "recorded_at": str(turn.get("recorded_at") or "").strip() or None,
                    "matched_terms": matched_terms,
                    "match_score": round(score, 3),
                },
            )
        )

    memory_service = MemoryService(Mem0Adapter(settings))
    for row in memory_service.search_chat_research_memory(
        query=normalized_query,
        limit=_RELEVANT_CHAT_MEMORY_SCAN_LIMIT,
        exclude_thread_id=thread_id,
    ):
        content = row.get("content") if isinstance(row.get("content"), dict) else {}
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        if not isinstance(content, dict):
            continue
        candidates.append(
            (
                float(row.get("match_score") or 0.0),
                {
                    "type": "relevant_chat_memory",
                    "thread_id": str(content.get("thread_id") or "").strip() or None,
                    "query": str(content.get("query") or "").strip() or None,
                    "intent": str(content.get("intent") or "").strip() or None,
                    "summary": _trim_context_text(str(content.get("summary") or "").strip()) or None,
                    "conclusion": _trim_context_text(str(content.get("conclusion") or "").strip()) or None,
                    "run_id": str(content.get("run_id") or "").strip() or None,
                    "memory_scope": str(metadata.get("memory_scope") or content.get("memory_scope") or "").strip() or None,
                    "snapshot_id": str(metadata.get("snapshot_id") or content.get("snapshot_id") or "").strip() or None,
                    "schema_version": str(metadata.get("schema_version") or content.get("schema_version") or "").strip() or None,
                    "matched_terms": list(row.get("matched_terms") or []),
                    "match_score": float(row.get("match_score") or 0.0),
                },
            )
        )

    for row in memory_service.search_methodology_memory(
        query=normalized_query,
        limit=_RELEVANT_METHODOLOGY_SCAN_LIMIT,
    ):
        content = row.get("content") if isinstance(row.get("content"), dict) else {}
        if not isinstance(content, dict):
            continue
        candidates.append(
            (
                float(row.get("match_score") or 0.0),
                {
                    "type": "relevant_methodology_memory",
                    "memory_type": str(row.get("memory_type") or "").strip() or None,
                    "title": str(content.get("title") or "").strip() or None,
                    "summary": _trim_context_text(str(content.get("summary") or "").strip()) or None,
                    "benchmark_scope": str(content.get("benchmark_scope") or "").strip() or None,
                    "proposal_id": str(content.get("proposal_id") or "").strip() or None,
                    "matched_terms": list(row.get("matched_terms") or []),
                    "match_score": float(row.get("match_score") or 0.0),
                },
            )
        )

    if not candidates:
        return []

    ordered: list[dict[str, Any]] = []
    seen_signatures: set[tuple[Any, ...]] = set()
    for _, context in sorted(candidates, key=lambda item: item[0], reverse=True):
        signature = _context_signature(context)
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        ordered.append(context)
        if len(ordered) >= limit:
            break
    return ordered


def _build_thread_turn_context(*, settings: Settings, state, limit: int) -> list[dict[str, Any]]:
    thread_id = ensure_session_thread(settings=settings, state=state)
    turns = ChatThreadStore(settings).read_recent_turns(thread_id=thread_id, limit=limit)
    contexts: list[dict[str, Any]] = []
    for turn in turns:
        content = str(turn.get("content") or "").strip()
        if not content:
            continue
        contexts.append(
            {
                "type": "chat_thread_turn",
                "thread_id": thread_id,
                "role": str(turn.get("role") or "").strip() or None,
                "turn_type": str(turn.get("turn_type") or "").strip() or None,
                "content": content,
                "intent": str(turn.get("intent") or "").strip() or None,
                "run_id": str(turn.get("run_id") or "").strip() or None,
                "recorded_at": str(turn.get("recorded_at") or "").strip() or None,
                "metadata": turn.get("metadata") if isinstance(turn.get("metadata"), dict) else {},
            }
        )
    return contexts


def _extract_query_terms(query: str) -> set[str]:
    terms: set[str] = set()
    lowered = query.lower()
    for token in re.findall(r"[a-z0-9]{2,}", lowered):
        if token not in _ENGLISH_QUERY_STOPWORDS:
            terms.add(token)
    for token in re.findall(r"[\u4e00-\u9fff]{2,}", query):
        terms.add(token)
        if len(token) <= 8:
            for index in range(0, len(token) - 1):
                terms.add(token[index:index + 2])
    return {term for term in terms if term.strip()}


def _score_query_match(*, query_terms: set[str], blob: str) -> tuple[list[str], float]:
    if not query_terms:
        return [], 0.0
    lowered_blob = blob.lower()
    matched_terms: list[str] = []
    score = 0.0
    for term in sorted(query_terms):
        probe = term.lower()
        if probe not in lowered_blob:
            continue
        matched_terms.append(term)
        if len(term) >= 8:
            score += 3.0
        elif len(term) >= 4:
            score += 2.0
        else:
            score += 1.0
    return matched_terms, score


def _trim_context_text(text: str, limit: int = 220) -> str:
    compact = text.replace("\n", " ").strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3]}..."


def _thread_topic_title(*, last_user_query: Any, last_result_summary: Any, topic_title: Any = None) -> str | None:
    explicit = str(topic_title or "").strip()
    if explicit:
        return explicit
    query = _trim_context_text(str(last_user_query or "").strip(), limit=72)
    if query:
        return query
    summary = _trim_context_text(str(last_result_summary or "").strip(), limit=72)
    return summary or None


def _context_signature(context: dict[str, Any]) -> tuple[Any, ...]:
    return (
        context.get("type"),
        context.get("thread_id"),
        context.get("run_id"),
        context.get("memory_type"),
        context.get("title"),
        context.get("summary"),
        context.get("content"),
        tuple(context.get("matched_terms") or []),
    )


def _apply_turns_to_state(*, state, turns: list[dict[str, Any]]) -> None:
    for turn in turns:
        role = str(turn.get("role") or "").strip()
        turn_type = str(turn.get("turn_type") or "").strip()
        content = str(turn.get("content") or "").strip()
        if role == "user" and content:
            state.last_user_query = content
        if role == "assistant" and content:
            metadata = turn.get("metadata") if isinstance(turn.get("metadata"), dict) else {}
            summary = str(metadata.get("summary") or "").strip()
            conclusion = str(metadata.get("conclusion") or "").strip()
            follow_up = str(metadata.get("follow_up") or "").strip()
            if summary:
                state.last_result_summary = summary
            if conclusion or content:
                state.last_result_conclusion = conclusion or content
            if follow_up:
                state.last_result_follow_up = follow_up
        if turn_type.startswith("research_"):
            state.last_result_intent = str(turn.get("intent") or "").strip() or state.last_result_intent
            state.last_run_id = str(turn.get("run_id") or "").strip() or state.last_run_id


def _apply_record_to_state(*, state, record: dict[str, Any]) -> None:
    state.last_user_query = record.get("query") or state.last_user_query
    state.last_result_intent = record.get("intent") or state.last_result_intent
    state.last_result_summary = record.get("summary") or state.last_result_summary
    state.last_result_conclusion = record.get("conclusion") or state.last_result_conclusion
    state.last_result_follow_up = record.get("follow_up") or state.last_result_follow_up
    state.last_run_id = record.get("run_id") or state.last_run_id
    state.thread_id = record.get("thread_id") or getattr(state, "thread_id", None)


def _clear_recent_session_memory(state) -> None:
    apply_session_state_patch_to_state(state, clear_recent_session_patch())


def _load_latest_run_memory(settings: Settings) -> dict[str, Any] | None:
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


def _extract_follow_up(payload: dict[str, Any]) -> str | None:
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


def _state_memory_record(state) -> dict[str, Any]:
    return {
        "query": getattr(state, "last_user_query", None),
        "intent": getattr(state, "last_result_intent", None),
        "summary": getattr(state, "last_result_summary", None),
        "conclusion": getattr(state, "last_result_conclusion", None),
        "follow_up": getattr(state, "last_result_follow_up", None),
        "run_id": getattr(state, "last_run_id", None),
        "thread_id": getattr(state, "thread_id", None),
    }


def _context_from_record(record: dict[str, Any]) -> dict[str, Any] | None:
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


def _build_thread_summary_text(
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


def _auto_compact_thread_if_needed(*, settings: Settings, state, store: ChatThreadStore | None = None, thread_id: str | None = None) -> None:
    resolved_thread_id = thread_id or ensure_session_thread(settings=settings, state=state)
    resolved_store = store or ChatThreadStore(settings)
    turns = resolved_store.read_all_turns(thread_id=resolved_thread_id)
    if len(turns) < _AUTO_COMPACT_TRIGGER_TURNS:
        return
    compact_session_thread(settings=settings, state=state)
