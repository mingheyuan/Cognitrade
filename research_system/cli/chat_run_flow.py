from __future__ import annotations

from research_system.cli.chat_commands import (
    clear_pending_clarification,
    merge_pending_clarification,
    store_pending_clarification,
    should_resume_pending_clarification,
)
from research_system.cli.chat_updates import SessionStatePatch, session_patch_update, warning_update
from research_system.services.task_router import ChatDispatchDecision


def run_chat_query_flow(
    *,
    parsed: dict,
    state,
    settings,
    llm_provider,
    task_router,
    emit_text,
    dispatch_fn,
    basic_answer_fn,
    run_research_fn,
    print_result_fn,
    output_writer=None,
) -> bool:
    action = parsed.get("action")
    if action != "run":
        return False

    intent = parsed.get("intent") or state.current_intent
    run_id = parsed.get("run_id")
    query = str(parsed.get("query") or "")
    if should_resume_pending_clarification(query=query, intent=intent, state=state):
        query, intent, run_id = merge_pending_clarification(query=query, intent=intent, run_id=run_id, state=state)

    if intent is None:
        dispatch = dispatch_fn(query=query, task_router=task_router)
        if dispatch.mode == "direct_answer":
            basic_answer_fn(query=query, llm_provider=llm_provider, output_writer=output_writer)
            return True
        intent = dispatch.intent
    else:
        dispatch = ChatDispatchDecision(mode="research", intent=intent, reason="explicit_or_session_intent")

    if intent == "review_thesis":
        run_id = run_id or state.review_target_run_id
        if not run_id:
            emit_text(output_writer, warning_update("review_thesis requires `/run-id <target_run_id>` or `/review <run_id> <query>`."))
            return True

    payload = run_research_fn(
        settings,
        query=query,
        intent=intent,
        run_id=run_id,
        thinking_enabled=state.thinking_enabled,
        events_mode=state.events_mode,
        dispatch=dispatch,
        output_writer=output_writer,
    )
    print_result_fn(
        payload,
        llm_provider=llm_provider,
        query=query,
        requested_intent=intent,
        output_writer=output_writer,
    )
    session_patch = SessionStatePatch()
    if isinstance(payload, dict) and payload.get("run_id"):
        session_patch = SessionStatePatch(
            last_run_id=str(payload.get("run_id")),
        )
    if isinstance(payload, dict) and payload.get("status") == "needs_clarification":
        clarification_patch = store_pending_clarification(
            query=query,
            intent=intent,
            run_id=(str(run_id) if run_id else None),
            payload=payload,
        )
        session_patch = SessionStatePatch(
            last_run_id=(str(payload.get("run_id")) if payload.get("run_id") else session_patch.last_run_id),
            pending_clarification_query=clarification_patch.pending_clarification_query,
            pending_clarification_intent=clarification_patch.pending_clarification_intent,
            pending_clarification_run_id=clarification_patch.pending_clarification_run_id,
            pending_clarification_question=clarification_patch.pending_clarification_question,
        )
    else:
        cleared = clear_pending_clarification()
        session_patch = SessionStatePatch(
            last_run_id=(str(payload.get("run_id")) if isinstance(payload, dict) and payload.get("run_id") else session_patch.last_run_id),
            pending_clarification_query=cleared.pending_clarification_query,
            pending_clarification_intent=cleared.pending_clarification_intent,
            pending_clarification_run_id=cleared.pending_clarification_run_id,
            pending_clarification_question=cleared.pending_clarification_question,
        )
    if parsed.get("intent") == "review_thesis" and run_id:
        session_patch = SessionStatePatch(
            current_intent=session_patch.current_intent,
            review_target_run_id=str(run_id),
            last_run_id=session_patch.last_run_id,
            thinking_enabled=session_patch.thinking_enabled,
            events_mode=session_patch.events_mode,
            pending_clarification_query=session_patch.pending_clarification_query,
            pending_clarification_intent=session_patch.pending_clarification_intent,
            pending_clarification_run_id=session_patch.pending_clarification_run_id,
            pending_clarification_question=session_patch.pending_clarification_question,
        )
    emit_text(output_writer, session_patch_update(session_patch))
    return True
