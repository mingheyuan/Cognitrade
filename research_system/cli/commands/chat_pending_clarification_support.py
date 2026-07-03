from __future__ import annotations

from research_system.cli.state.chat_updates import SessionStatePatch


def resolve_chat_run_id(token: str, state) -> str | None:
    if token == "last":
        return state.last_run_id
    return token or None


def should_resume_pending_clarification(*, query: str, intent: object, state) -> bool:
    return (
        state.pending_clarification_query is not None
        and not str(query).strip().startswith("/")
        and intent is None
    )


def merge_pending_clarification(
    *,
    query: str,
    intent: object,
    run_id: object,
    state,
) -> tuple[str, str | None, str | None]:
    merged_query = f"{state.pending_clarification_query}\n补充信息: {query.strip()}"
    merged_intent = str(intent) if isinstance(intent, str) and intent else state.pending_clarification_intent
    merged_run_id = str(run_id) if isinstance(run_id, str) and run_id else state.pending_clarification_run_id
    return merged_query, merged_intent, merged_run_id


def store_pending_clarification(
    *,
    query: str,
    intent: str | None,
    run_id: str | None,
    payload: dict,
) -> SessionStatePatch:
    clarification = payload.get("clarification")
    if isinstance(clarification, dict):
        question = clarification.get("question")
        question_text = str(question) if question else None
    else:
        question_text = None
    return SessionStatePatch(
        pending_clarification_query=query,
        pending_clarification_intent=intent,
        pending_clarification_run_id=run_id,
        pending_clarification_question=question_text,
    )


def clear_pending_clarification() -> SessionStatePatch:
    return SessionStatePatch(
        pending_clarification_query=None,
        pending_clarification_intent=None,
        pending_clarification_run_id=None,
        pending_clarification_question=None,
    )
