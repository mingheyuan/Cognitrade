from __future__ import annotations

import json

from research_system.cli.runtime.chat_llm_runtime import (
    complete_text_with_runtime,
    decide_chat_dispatch_with_runtime,
    stream_text_with_runtime,
)
from research_system.cli.state.chat_updates import answer_update, stream_append_update, stream_done_update, thinking_update
from research_system.runtime.agent_os import AgentOS
from research_system.runtime.providers.provider_registry import OpenAIProvider
from research_system.schemas.protocol import RequestEnvelope
from research_system.services.routing.router.task_router import ChatDispatchDecision, TaskRouter

_BASIC_CHAT_SYSTEM_PROMPT = (
    "You are the lightweight chat shell assistant for Cognitrade.\n"
    "Answer short general questions about identity, usage, capabilities, and basic finance or index concepts.\n"
    "Keep replies concise and practical.\n"
    "If the user asks for a definition, explanation, or overview, answer directly instead of redirecting them into a research workflow.\n"
    "If recent research context is provided, use it only when it is directly relevant to the user's follow-up."
)

_IDENTITY_PREFIXES = ("\u4f60\u662f\u8c01",)
_IDENTITY_FALLBACK = (
    "\u6211\u662f Cognitrade\uff0c\u9762\u5411 A \u80a1\u7814\u7a76\u3001\u5019\u9009\u7b5b\u9009\u3001\u89c2\u70b9\u84b8\u998f\u548c\u590d\u6838\u7684\u547d\u4ee4\u884c\u7814\u7a76\u52a9\u624b\u3002"
    "\u4f60\u53ef\u4ee5\u76f4\u63a5\u95ee\u4e2a\u80a1\u3001\u677f\u5757\u3001\u5019\u9009\u6807\u7684\u3001\u84b8\u998f\u6587\u672c\uff0c\u6216\u7528 `/help` \u67e5\u770b\u547d\u4ee4\u3002"
)

_BASIC_CHAT_FALLBACK = (
    "\u6211\u53ef\u4ee5\u76f4\u63a5\u56de\u7b54\u57fa\u7840\u95ee\u9898\uff0c\u4e5f\u53ef\u4ee5\u5728\u9700\u8981\u65f6\u542f\u52a8\u7814\u7a76\u6d41\u7a0b\u3002"
    "\u4f60\u53ef\u4ee5\u76f4\u63a5\u63d0\u95ee\uff0c\u6216\u7528 `/help` \u67e5\u770b\u547d\u4ee4\u3002"
)

_TIME_SENSITIVE_CN = (
    "\u6700\u65b0",
    "\u4eca\u5929",
    "\u4eca\u65e5",
    "\u73b0\u5728",
    "\u8fd1\u671f",
    "\u8d70\u52bf",
    "\u8868\u73b0",
    "\u6da8\u8dcc",
    "\u591a\u5c11\u70b9",
    "\u600e\u4e48\u770b",
)


def should_answer_basic_chat(*, settings, state, query: str, task_router: TaskRouter) -> bool:
    return decide_chat_dispatch(settings=settings, state=state, query=query, task_router=task_router).mode == "direct_answer"


def decide_chat_dispatch(*, settings, state, query: str, task_router: TaskRouter) -> ChatDispatchDecision:
    stripped = query.strip()
    if stripped and set(stripped) == {"?"}:
        return ChatDispatchDecision(mode="direct_answer", intent=None, reason="punctuation_only")
    dispatch = decide_chat_dispatch_with_runtime(
        settings=settings,
        state=state,
        task_router=task_router,
        query=query,
    )
    if dispatch.mode == "direct_answer":
        lowered = stripped.lower()
        time_sensitive_en = ("latest", "today", "recent", "current", "trend", "performance", "price", "level", "move")
        if any(marker in stripped for marker in _TIME_SENSITIVE_CN) or any(marker in lowered for marker in time_sensitive_en):
            return ChatDispatchDecision(mode="research", intent="sector_research", reason="time_sensitive_market_fallback")
    return dispatch


def is_identity_query(query: str) -> bool:
    stripped = query.strip()
    lowered = stripped.lower()
    return any(stripped.startswith(prefix) for prefix in _IDENTITY_PREFIXES) or "who are you" in lowered


def answer_basic_chat(
    *,
    settings,
    state,
    query: str,
    llm_provider: OpenAIProvider,
    conversation_context: list[dict] | None = None,
) -> str:
    if is_identity_query(query):
        return _IDENTITY_FALLBACK
    answer = complete_text_with_runtime(
        settings=settings,
        state=state,
        llm_provider=llm_provider,
        query=query,
        conversation_context=conversation_context,
        intent="basic_chat",
        phase="basic_chat",
        operation="basic_chat_answer",
        strategy="direct_answer",
        title="basic_chat_answerer",
        objective=f"Answer basic chat query: {query[:80]}",
        system_prompt=_BASIC_CHAT_SYSTEM_PROMPT,
        user_prompt=_build_basic_chat_prompt(query=query, conversation_context=conversation_context),
    )
    if isinstance(answer, str) and answer.strip():
        return answer
    return _BASIC_CHAT_FALLBACK


def print_streamed_basic_chat_answer(
    *,
    settings,
    state,
    query: str,
    llm_provider: OpenAIProvider,
    emit_text,
    output_writer=None,
    conversation_context: list[dict] | None = None,
) -> str:
    if is_identity_query(query):
        answer = answer_basic_chat(
            settings=settings,
            state=state,
            query=query,
            llm_provider=llm_provider,
            conversation_context=conversation_context,
        )
        emit_text(output_writer, answer_update(answer))
        return answer
    streamed = stream_text_with_runtime(
        settings=settings,
        state=state,
        llm_provider=llm_provider,
        query=query,
        conversation_context=conversation_context,
        intent="basic_chat",
        phase="basic_chat",
        operation="basic_chat_answer_stream",
        strategy="direct_answer",
        title="basic_chat_answerer",
        objective=f"Stream basic chat answer: {query[:80]}",
        system_prompt=_BASIC_CHAT_SYSTEM_PROMPT,
        user_prompt=_build_basic_chat_prompt(query=query, conversation_context=conversation_context),
        on_text_delta=lambda delta: emit_text(output_writer, stream_append_update(delta)),
        on_reasoning_summary=lambda summary: emit_text(output_writer, thinking_update(summary)),
    )
    if isinstance(streamed, str) and streamed.strip():
        emit_text(output_writer, stream_done_update())
        return streamed
    answer = answer_basic_chat(
        settings=settings,
        state=state,
        query=query,
        llm_provider=llm_provider,
        conversation_context=conversation_context,
    )
    emit_text(output_writer, answer_update(answer))
    return answer


def print_streamed_agent_os_chat_answer(
    *,
    settings,
    state,
    query: str,
    llm_provider: OpenAIProvider,
    emit_text,
    output_writer=None,
    conversation_context: list[dict] | None = None,
) -> str:
    """Route the production CLI basic-chat path through Main Agent Core Loop."""
    del llm_provider

    def on_event(event: dict) -> None:
        event_type = str(event.get("event_type") or "")
        if event_type in {"main_agent_completed", "main_agent_failed", "main_agent_cancelled"}:
            return
        message = str(event.get("message") or "").strip()
        if message:
            emit_text(output_writer, thinking_update(message))

    request = RequestEnvelope(
        thread_id=str(getattr(state, "thread_id", "") or "") or None,
        user_query=query,
        conversation_context=list(conversation_context or []),
    )
    response = AgentOS(settings).handle_request(request, event_callback=on_event)
    emit_text(output_writer, answer_update(response.message))
    return response.message


def _build_basic_chat_prompt(*, query: str, conversation_context: list[dict] | None) -> str:
    if not conversation_context:
        return query
    return (
        f"User query:\n{query}\n\n"
        "Recent conversation context:\n"
        f"{json.dumps(conversation_context, ensure_ascii=False)}"
    )
