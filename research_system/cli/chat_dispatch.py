from __future__ import annotations

import json

from research_system.cli.chat_updates import answer_update, stream_append_update, stream_done_update, thinking_update
from research_system.runtime.provider_registry import LLMProviderError, OpenAIProvider
from research_system.services.task_router import ChatDispatchDecision, TaskRouter


def should_answer_basic_chat(*, query: str, task_router: TaskRouter) -> bool:
    return decide_chat_dispatch(query=query, task_router=task_router).mode == "direct_answer"


def decide_chat_dispatch(*, query: str, task_router: TaskRouter) -> ChatDispatchDecision:
    stripped = query.strip()
    if stripped and set(stripped) == {"?"}:
        return ChatDispatchDecision(mode="direct_answer", intent=None, reason="punctuation_only")
    dispatch = task_router.decide_chat_dispatch(query)
    if dispatch.mode == "direct_answer":
        lowered = stripped.lower()
        time_sensitive_cn = ("最近", "今天", "今日", "现在", "最新", "近期", "走势", "表现", "涨跌", "多少点", "怎么看")
        time_sensitive_en = ("latest", "today", "recent", "current", "trend", "performance", "price", "level", "move")
        if any(marker in stripped for marker in time_sensitive_cn) or any(marker in lowered for marker in time_sensitive_en):
            return ChatDispatchDecision(mode="research", intent="sector_research", reason="time_sensitive_market_fallback")
    return dispatch


def is_identity_query(query: str) -> bool:
    stripped = query.strip()
    lowered = stripped.lower()
    return stripped.startswith("你是谁") or "who are you" in lowered


def answer_basic_chat(
    *,
    query: str,
    llm_provider: OpenAIProvider,
    conversation_context: list[dict] | None = None,
) -> str:
    if is_identity_query(query):
        return (
            "我是 Cognitrade，一个面向 A 股研究、候选筛选、观点蒸馏和后验复核的命令行研究助手。"
            "你可以直接问我个股、板块、候选标的、蒸馏文本或复核上次结论，也可以用 `/help` 查看命令。"
        )
    if llm_provider.available:
        try:
            return llm_provider.complete(
                system_prompt=(
                    "You are the lightweight chat shell assistant for Cognitrade.\n"
                    "Answer short general questions about identity, usage, capabilities, and basic finance or index concepts.\n"
                    "Keep replies concise and practical.\n"
                    "If the user asks for a definition, explanation, or overview, answer directly instead of asking for research inputs.\n"
                    "If recent research context is provided, use it only when it is directly relevant to the user's follow-up."
                ),
                user_prompt=_build_basic_chat_prompt(query=query, conversation_context=conversation_context),
                temperature=0.2,
                reasoning_effort="low",
            ).strip()
        except LLMProviderError:
            pass
    return "我可以直接回答基础问题，也可以在需要时启动研究子代理。你可以直接提问，或者用 `/help` 查看命令。"


def print_streamed_basic_chat_answer(
    *,
    query: str,
    llm_provider: OpenAIProvider,
    emit_text,
    output_writer=None,
    conversation_context: list[dict] | None = None,
) -> str:
    if is_identity_query(query):
        answer = answer_basic_chat(
            query=query,
            llm_provider=llm_provider,
            conversation_context=conversation_context,
        )
        emit_text(
            output_writer,
            answer_update(answer),
        )
        return answer
    if llm_provider.available:
        try:
            text_started = False
            chunks: list[str] = []

            def _on_text_delta(delta: str) -> None:
                nonlocal text_started
                text_started = True
                chunks.append(delta)
                emit_text(output_writer, stream_append_update(delta))

            def _on_reasoning_summary(summary: str) -> None:
                emit_text(output_writer, thinking_update(summary))

            llm_provider.stream_complete(
                system_prompt=(
                    "You are the lightweight chat shell assistant for Cognitrade.\n"
                    "Answer short general questions about identity, usage, capabilities, and basic finance or index concepts.\n"
                    "Keep replies concise and practical.\n"
                    "If the user asks for a definition, explanation, or overview, answer directly instead of redirecting them into a research workflow.\n"
                    "If recent research context is provided, use it only when it is directly relevant to the user's follow-up."
                ),
                user_prompt=_build_basic_chat_prompt(query=query, conversation_context=conversation_context),
                on_text_delta=_on_text_delta,
                on_reasoning_summary=_on_reasoning_summary,
                temperature=0.2,
                reasoning_effort="low",
            )
            if text_started:
                emit_text(output_writer, stream_done_update())
                return "".join(chunks).strip()
        except LLMProviderError:
            pass
    answer = answer_basic_chat(
        query=query,
        llm_provider=llm_provider,
        conversation_context=conversation_context,
    )
    emit_text(
        output_writer,
        answer_update(answer),
    )
    return answer


def _build_basic_chat_prompt(*, query: str, conversation_context: list[dict] | None) -> str:
    if not conversation_context:
        return query
    return (
        f"User query:\n{query}\n\n"
        "Recent conversation context:\n"
        f"{json.dumps(conversation_context, ensure_ascii=False)}"
    )
