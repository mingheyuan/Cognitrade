from __future__ import annotations

from research_system.cli.state.chat_updates import SessionStatePatch


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
