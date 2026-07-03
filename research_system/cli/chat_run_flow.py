from __future__ import annotations

from research_system.cli.chat_commands import clear_pending_clarification, store_pending_clarification
from research_system.cli.chat_flow_runtime import ChatQueryFlowRuntime
from research_system.cli.chat_memory import (
    remember_chat_research_result,
)
from research_system.cli.chat_preprocessor import prepare_chat_run_request
from research_system.cli.chat_state import build_research_result_session_patch
from research_system.cli.chat_updates import SessionStatePatch, answer_update, session_patch_update, warning_update
from research_system.services.task_router import ChatDispatchDecision


def _resolve_chat_dispatch(
    *,
    query: str,
    intent: str | None,
    explicit_run_request: bool,
    task_router,
    dispatch_fn,
) -> tuple[ChatDispatchDecision, str | None]:
    if intent is None and not explicit_run_request:
        dispatch = dispatch_fn(query=query, task_router=task_router)
        return dispatch, dispatch.intent
    return ChatDispatchDecision(mode="research", intent=intent, reason="explicit_or_session_intent"), intent


def _handle_direct_answer_dispatch(
    *,
    dispatch: ChatDispatchDecision,
    query: str,
    llm_provider,
    resolved_context: list[dict],
    runtime: ChatQueryFlowRuntime,
    output_writer=None,
) -> bool:
    if dispatch.mode != "direct_answer":
        return False
    answer = runtime.basic_answer_fn(
        query=query,
        llm_provider=llm_provider,
        output_writer=output_writer,
        conversation_context=resolved_context,
    )
    if isinstance(answer, str) and answer.strip():
        runtime.remember_basic_chat_turn_fn(query=query, answer=answer)
    return True


def _resolve_review_target_run_id(
    *,
    intent: str | None,
    run_id: str | None,
    review_target_run_id: str | None,
    emit_text,
    output_writer=None,
) -> tuple[str | None, bool]:
    if intent != "review_thesis":
        return run_id, True
    resolved_run_id = run_id or review_target_run_id
    if resolved_run_id:
        return resolved_run_id, True
    emit_text(
        output_writer,
        warning_update("review_thesis requires `/run-id <target_run_id>` or `/review <run_id> <query>`."),
    )
    return None, False


def _build_research_result_patch(
    *,
    payload,
    query: str,
    intent: str | None,
    run_id: str | None,
) -> SessionStatePatch:
    if isinstance(payload, dict) and payload.get("status") == "needs_clarification":
        clarification_patch = store_pending_clarification(
            query=query,
            intent=intent,
            run_id=(str(run_id) if run_id else None),
            payload=payload,
        )
        return build_research_result_session_patch(
            payload=payload,
            query=query,
            effective_intent=intent,
            review_target_run_id=str(run_id) if run_id else None,
            pending_patch=clarification_patch,
        )
    return build_research_result_session_patch(
        payload=payload,
        query=query,
        effective_intent=intent,
        review_target_run_id=str(run_id) if run_id else None,
        pending_patch=clear_pending_clarification(),
    )


def _finalize_research_result(
    *,
    settings,
    state,
    query: str,
    intent: str | None,
    run_id: str | None,
    payload,
    emit_text,
    output_writer=None,
) -> None:
    session_patch = _build_research_result_patch(
        payload=payload,
        query=query,
        intent=intent,
        run_id=run_id,
    )
    remember_chat_research_result(settings=settings, state=state, query=query, intent=intent, payload=payload)
    emit_text(output_writer, session_patch_update(session_patch))


def run_chat_query_flow(
    *,
    parsed: dict,
    state,
    settings,
    llm_provider,
    task_router,
    emit_text,
    dispatch_fn,
    conversation_context,
    runtime: ChatQueryFlowRuntime,
    output_writer=None,
) -> bool:
    action = parsed.get("action")
    if action != "run":
        return False

    prepared = prepare_chat_run_request(
        parsed=parsed,
        state=state,
        settings=settings,
        conversation_context=conversation_context,
    )
    resolved_context = list(prepared.conversation_context)
    intent = prepared.intent
    run_id = prepared.run_id
    query = prepared.query

    if prepared.memory_reply is not None:
        emit_text(output_writer, answer_update(prepared.memory_reply))
        return True
    dispatch, intent = _resolve_chat_dispatch(
        query=query,
        intent=intent,
        explicit_run_request=prepared.explicit_run_request,
        task_router=task_router,
        dispatch_fn=dispatch_fn,
    )
    if _handle_direct_answer_dispatch(
        dispatch=dispatch,
        query=query,
        llm_provider=llm_provider,
        resolved_context=resolved_context,
        runtime=runtime,
        output_writer=output_writer,
    ):
        return True
    run_id, can_run = _resolve_review_target_run_id(
        intent=intent,
        run_id=run_id,
        review_target_run_id=state.review_target_run_id,
        emit_text=emit_text,
        output_writer=output_writer,
    )
    if not can_run:
        return True

    payload = runtime.run_research_fn(
        settings,
        query=query,
        intent=intent,
        run_id=run_id,
        thinking_enabled=state.thinking_enabled,
        events_mode=state.events_mode,
        dispatch=dispatch,
        conversation_context=resolved_context,
        output_writer=output_writer,
    )
    runtime.print_result_fn(
        payload,
        llm_provider=llm_provider,
        query=query,
        requested_intent=intent,
        output_writer=output_writer,
    )

    _finalize_research_result(
        settings=settings,
        state=state,
        query=query,
        intent=intent,
        run_id=str(run_id) if run_id else None,
        payload=payload,
        emit_text=emit_text,
        output_writer=output_writer,
    )
    return True
