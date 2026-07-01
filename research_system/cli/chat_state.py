from __future__ import annotations

from typing import Any

from research_system.cli.chat_thread_store import ChatThreadStore
from research_system.cli.chat_updates import SESSION_PATCH_UNSET, SessionStatePatch
from research_system.runtime.settings import Settings


def apply_session_state_patch_to_state(state, patch: SessionStatePatch) -> None:
    if patch.thread_id is not SESSION_PATCH_UNSET:
        state.thread_id = patch.thread_id
    if getattr(patch, "thread_source", SESSION_PATCH_UNSET) is not SESSION_PATCH_UNSET:
        state.thread_source = patch.thread_source
    if getattr(patch, "parent_thread_id", SESSION_PATCH_UNSET) is not SESSION_PATCH_UNSET:
        state.parent_thread_id = patch.parent_thread_id
    if patch.current_intent is not SESSION_PATCH_UNSET:
        state.current_intent = patch.current_intent
    if patch.review_target_run_id is not SESSION_PATCH_UNSET:
        state.review_target_run_id = patch.review_target_run_id
    if patch.last_run_id is not SESSION_PATCH_UNSET:
        state.last_run_id = patch.last_run_id
    if patch.thinking_enabled is not SESSION_PATCH_UNSET:
        state.thinking_enabled = bool(patch.thinking_enabled)
    if patch.events_mode is not SESSION_PATCH_UNSET:
        state.events_mode = str(patch.events_mode)
    if patch.last_user_query is not SESSION_PATCH_UNSET:
        state.last_user_query = patch.last_user_query
    if patch.last_result_intent is not SESSION_PATCH_UNSET:
        state.last_result_intent = patch.last_result_intent
    if patch.last_result_summary is not SESSION_PATCH_UNSET:
        state.last_result_summary = patch.last_result_summary
    if patch.last_result_conclusion is not SESSION_PATCH_UNSET:
        state.last_result_conclusion = patch.last_result_conclusion
    if patch.last_result_follow_up is not SESSION_PATCH_UNSET:
        state.last_result_follow_up = patch.last_result_follow_up
    if patch.pending_clarification_query is not SESSION_PATCH_UNSET:
        state.pending_clarification_query = patch.pending_clarification_query
    if patch.pending_clarification_intent is not SESSION_PATCH_UNSET:
        state.pending_clarification_intent = patch.pending_clarification_intent
    if patch.pending_clarification_run_id is not SESSION_PATCH_UNSET:
        state.pending_clarification_run_id = patch.pending_clarification_run_id
    if patch.pending_clarification_question is not SESSION_PATCH_UNSET:
        state.pending_clarification_question = patch.pending_clarification_question


def build_research_result_session_patch(
    *,
    payload: object,
    query: str,
    effective_intent: str | None,
    review_target_run_id: str | None = None,
    pending_patch: SessionStatePatch | None = None,
) -> SessionStatePatch:
    payload_dict = payload if isinstance(payload, dict) else {}
    result = payload_dict.get("result") if isinstance(payload_dict.get("result"), dict) else {}
    report = result.get("report") if isinstance(result.get("report"), dict) else result
    resolved_intent = str(payload_dict.get("intent") or effective_intent or "")
    patch_kwargs: dict[str, object] = {
        "last_run_id": str(payload_dict.get("run_id")) if payload_dict.get("run_id") else None,
        "last_user_query": query,
        "last_result_intent": resolved_intent,
        "last_result_summary": str(payload_dict.get("display_summary") or "") or None,
        "last_result_conclusion": str((result or {}).get("conclusion") or "").strip() or None,
        "last_result_follow_up": str((report or {}).get("follow_up") or "").strip() or None,
    }
    if effective_intent == "review_thesis" and review_target_run_id:
        patch_kwargs["review_target_run_id"] = str(review_target_run_id)
    if pending_patch is not None:
        patch_kwargs.update(
            pending_clarification_query=pending_patch.pending_clarification_query,
            pending_clarification_intent=pending_patch.pending_clarification_intent,
            pending_clarification_run_id=pending_patch.pending_clarification_run_id,
            pending_clarification_question=pending_patch.pending_clarification_question,
        )
    return SessionStatePatch(**patch_kwargs)


def clear_recent_session_patch(*, include_defaults: bool = False) -> SessionStatePatch:
    patch_kwargs: dict[str, object] = {
        "last_run_id": None,
        "last_user_query": None,
        "last_result_intent": None,
        "last_result_summary": None,
        "last_result_conclusion": None,
        "last_result_follow_up": None,
        "pending_clarification_query": None,
        "pending_clarification_intent": None,
        "pending_clarification_run_id": None,
        "pending_clarification_question": None,
    }
    if include_defaults:
        patch_kwargs["current_intent"] = None
        patch_kwargs["review_target_run_id"] = None
    return SessionStatePatch(**patch_kwargs)


def build_new_thread_session_patch(*, thread_id: str) -> SessionStatePatch:
    return SessionStatePatch(
        thread_id=thread_id,
        thread_source="new",
        parent_thread_id=None,
        current_intent=None,
        review_target_run_id=None,
        last_run_id=None,
        last_user_query=None,
        last_result_intent=None,
        last_result_summary=None,
        last_result_conclusion=None,
        last_result_follow_up=None,
        pending_clarification_query=None,
        pending_clarification_intent=None,
        pending_clarification_run_id=None,
        pending_clarification_question=None,
    )


def mirror_session_state_patch(state, *, thread_id: str | None = None) -> SessionStatePatch:
    patch_kwargs: dict[str, object] = {
        "last_run_id": state.last_run_id,
        "last_user_query": state.last_user_query,
        "last_result_intent": state.last_result_intent,
        "last_result_summary": state.last_result_summary,
        "last_result_conclusion": state.last_result_conclusion,
        "last_result_follow_up": state.last_result_follow_up,
        "pending_clarification_query": state.pending_clarification_query,
        "pending_clarification_intent": state.pending_clarification_intent,
        "pending_clarification_run_id": state.pending_clarification_run_id,
        "pending_clarification_question": state.pending_clarification_question,
        "thread_source": getattr(state, "thread_source", None),
        "parent_thread_id": getattr(state, "parent_thread_id", None),
    }
    if thread_id is not None:
        patch_kwargs["thread_id"] = thread_id
    return SessionStatePatch(**patch_kwargs)


def build_show_last_session_patch(state) -> SessionStatePatch:
    return SessionStatePatch(
        last_run_id=state.last_run_id,
        thread_source=getattr(state, "thread_source", None),
        parent_thread_id=getattr(state, "parent_thread_id", None),
        current_intent=state.current_intent,
        review_target_run_id=state.review_target_run_id,
        thinking_enabled=state.thinking_enabled,
        events_mode=state.events_mode,
        last_user_query=state.last_user_query,
        last_result_intent=state.last_result_intent,
        last_result_summary=state.last_result_summary,
        last_result_conclusion=state.last_result_conclusion,
        last_result_follow_up=state.last_result_follow_up,
    )


def session_state_snapshot(state) -> dict[str, Any]:
    return {
        "thread_id": state.thread_id,
        "thread_source": getattr(state, "thread_source", None),
        "parent_thread_id": getattr(state, "parent_thread_id", None),
        "current_intent": state.current_intent,
        "review_target_run_id": state.review_target_run_id,
        "last_run_id": state.last_run_id,
        "thinking_enabled": state.thinking_enabled,
        "events_mode": state.events_mode,
        "last_user_query": state.last_user_query,
        "last_result_intent": state.last_result_intent,
        "last_result_summary": state.last_result_summary,
        "last_result_conclusion": state.last_result_conclusion,
        "last_result_follow_up": state.last_result_follow_up,
        "pending_clarification_query": state.pending_clarification_query,
        "pending_clarification_intent": state.pending_clarification_intent,
        "pending_clarification_run_id": state.pending_clarification_run_id,
        "pending_clarification_question": state.pending_clarification_question,
    }


def apply_session_checkpoint_to_state(*, state, checkpoint: dict[str, Any]) -> None:
    session_state = checkpoint.get("session_state") if isinstance(checkpoint, dict) else None
    if not isinstance(session_state, dict):
        return
    apply_session_state_patch_to_state(
        state,
        SessionStatePatch(
            thread_id=session_state.get("thread_id"),
            thread_source=session_state.get("thread_source"),
            parent_thread_id=session_state.get("parent_thread_id"),
            current_intent=session_state.get("current_intent"),
            review_target_run_id=session_state.get("review_target_run_id"),
            last_run_id=session_state.get("last_run_id"),
            thinking_enabled=session_state.get("thinking_enabled"),
            events_mode=session_state.get("events_mode"),
            last_user_query=session_state.get("last_user_query"),
            last_result_intent=session_state.get("last_result_intent"),
            last_result_summary=session_state.get("last_result_summary"),
            last_result_conclusion=session_state.get("last_result_conclusion"),
            last_result_follow_up=session_state.get("last_result_follow_up"),
            pending_clarification_query=session_state.get("pending_clarification_query"),
            pending_clarification_intent=session_state.get("pending_clarification_intent"),
            pending_clarification_run_id=session_state.get("pending_clarification_run_id"),
            pending_clarification_question=session_state.get("pending_clarification_question"),
        ),
    )


def persist_thread_checkpoint(*, settings: Settings, state) -> dict[str, Any]:
    thread_id = str(getattr(state, "thread_id", None) or "").strip()
    if not thread_id:
        raise ValueError("thread_id is required to persist a thread checkpoint")
    return ChatThreadStore(settings).write_thread_checkpoint(
        thread_id=thread_id,
        session_state=session_state_snapshot(state),
    )


def apply_session_patch_and_checkpoint(
    *,
    settings: Settings,
    state,
    patch: SessionStatePatch,
) -> None:
    apply_session_state_patch_to_state(state, patch)
    if has_session_state_patch(patch):
        if not getattr(state, "thread_id", None) and patch.thread_id is not SESSION_PATCH_UNSET:
            state.thread_id = str(patch.thread_id or "").strip() or None
        if getattr(state, "thread_id", None):
            persist_thread_checkpoint(settings=settings, state=state)


def has_session_state_patch(patch: SessionStatePatch) -> bool:
    return any(
        value is not SESSION_PATCH_UNSET
        for value in (
            patch.thread_id,
            getattr(patch, "thread_source", SESSION_PATCH_UNSET),
            getattr(patch, "parent_thread_id", SESSION_PATCH_UNSET),
            patch.current_intent,
            patch.review_target_run_id,
            patch.last_run_id,
            patch.thinking_enabled,
            patch.events_mode,
            patch.last_user_query,
            patch.last_result_intent,
            patch.last_result_summary,
            patch.last_result_conclusion,
            patch.last_result_follow_up,
            patch.pending_clarification_query,
            patch.pending_clarification_intent,
            patch.pending_clarification_run_id,
            patch.pending_clarification_question,
        )
    )
