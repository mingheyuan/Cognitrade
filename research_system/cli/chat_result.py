from __future__ import annotations

import json

from research_system.cli.chat_event_adapter import display_chunk_to_update
from research_system.cli.chat_updates import (
    SessionStatePatch,
    ShellStatePatch,
    answer_update,
    default_update_for_text,
    dispatch_transcript_update,
    meta_update,
    thinking_update,
)
from research_system.runtime.provider_registry import LLMProviderError, OpenAIProvider

def stream_chat_rendered_result(
    *,
    llm_provider: OpenAIProvider,
    payload: dict,
    query: str,
    requested_intent: str | None,
    output_writer=None,
) -> bool:
    result = payload.get("result")
    if not isinstance(result, dict):
        return False
    effective_intent = str(payload.get("intent") or requested_intent or "auto")
    status = str(payload.get("status") or "unknown")
    display_summary = payload.get("display_summary")
    try:
        text_started = False

        def _on_text_delta(delta: str) -> None:
            nonlocal text_started
            text_started = True
            dispatch_transcript_update(output_writer, default_update_for_text("answer_delta", delta))

        def _on_reasoning_summary(summary: str) -> None:
            dispatch_transcript_update(output_writer, thinking_update(summary))

        llm_provider.stream_complete(
            system_prompt=(
                "You are the terminal chat presenter for Cognitrade.\n"
                "Turn the structured research result into a direct reply for a CLI user.\n"
                "Do not invent facts. Preserve caveats, review verdicts, and exact uncertainty.\n"
                "Match the response length to the evidence and the user's question.\n"
                "If the result needs detail, give detail. If it is simple, keep it short.\n"
                "If one paragraph is not enough, continue with additional short paragraphs and labeled bullets.\n"
                "Start directly with the takeaway; do not mention JSON or internal payload structure."
            ),
            user_prompt=(
                f"Intent: {effective_intent}\n"
                f"User query: {query}\n"
                f"Run status: {status}\n"
                f"Display summary: {display_summary or 'N/A'}\n"
                f"Structured result: {json.dumps(result, ensure_ascii=False)}"
            ),
            on_text_delta=_on_text_delta,
            on_reasoning_summary=_on_reasoning_summary,
            temperature=0.2,
            reasoning_effort="low",
        )
        if text_started:
            dispatch_transcript_update(output_writer, default_update_for_text("answer_done", ""))
            return True
    except LLMProviderError:
        return False
    return False


def print_chat_result_payload(
    payload: object,
    *,
    llm_provider: OpenAIProvider,
    query: str,
    requested_intent: str | None,
    payload_printer,
    output_writer=None,
) -> None:
    if not isinstance(payload, dict):
        payload_printer(payload, as_json=False, output_writer=output_writer)
        return
    status = str(payload.get("status") or "")
    if status == "needs_clarification":
        clarification = payload.get("clarification")
        if isinstance(clarification, dict) and clarification.get("question"):
            dispatch_transcript_update(output_writer, answer_update(str(clarification["question"])))
        else:
            dispatch_transcript_update(output_writer, answer_update(str(payload.get("message") or "Clarification required.")))
        if payload.get("run_id"):
            dispatch_transcript_update(
                output_writer,
                meta_update(
                    f"run_id: {payload['run_id']}",
                    state_patch=ShellStatePatch(current_run_id=str(payload["run_id"])),
                    session_patch=SessionStatePatch(last_run_id=str(payload["run_id"])),
                ),
            )
        dispatch_transcript_update(
            output_writer,
            meta_update(f"status: {status}", state_patch=ShellStatePatch(run_status_label=status)),
        )
        dispatch_transcript_update(output_writer, meta_update(f"intent: {payload.get('intent') or requested_intent or 'auto'}"))
        return
    display_summary = payload.get("display_summary")
    result = payload.get("result")
    conclusion = None
    if isinstance(result, dict):
        maybe_conclusion = result.get("conclusion")
        if isinstance(maybe_conclusion, str) and maybe_conclusion.strip():
            conclusion = maybe_conclusion.strip()
    summary_text = display_summary.strip() if isinstance(display_summary, str) and display_summary.strip() else None
    if isinstance(result, dict) and llm_provider.available and status in {"completed", "failed", "timed_out", "cancelled"}:
        if stream_chat_rendered_result(
            llm_provider=llm_provider,
            payload=payload,
            query=query,
            requested_intent=requested_intent,
            output_writer=output_writer,
        ):
            if payload.get("run_id"):
                dispatch_transcript_update(
                    output_writer,
                    meta_update(
                        f"run_id: {payload['run_id']}",
                        state_patch=ShellStatePatch(current_run_id=str(payload["run_id"])),
                        session_patch=SessionStatePatch(last_run_id=str(payload["run_id"])),
                    ),
                )
            if payload.get("status"):
                dispatch_transcript_update(
                    output_writer,
                    meta_update(
                        f"status: {payload['status']}",
                        state_patch=ShellStatePatch(run_status_label=str(payload["status"])),
                    ),
                )
            if payload.get("intent"):
                dispatch_transcript_update(output_writer, meta_update(f"intent: {payload['intent']}"))
            return
    if summary_text and conclusion and conclusion != summary_text:
        dispatch_transcript_update(output_writer, answer_update(_merge_answer_sections(summary_text, conclusion)))
    elif conclusion:
        dispatch_transcript_update(output_writer, answer_update(conclusion))
    elif summary_text:
        dispatch_transcript_update(output_writer, answer_update(summary_text))
    else:
        payload_printer(payload, as_json=False, output_writer=output_writer)
        return
    if payload.get("run_id"):
        dispatch_transcript_update(
            output_writer,
            meta_update(
                f"run_id: {payload['run_id']}",
                state_patch=ShellStatePatch(current_run_id=str(payload["run_id"])),
                session_patch=SessionStatePatch(last_run_id=str(payload["run_id"])),
            ),
        )
    if payload.get("status"):
        dispatch_transcript_update(
            output_writer,
            meta_update(
                f"status: {payload['status']}",
                state_patch=ShellStatePatch(run_status_label=str(payload["status"])),
            ),
        )
    if payload.get("intent"):
        dispatch_transcript_update(output_writer, meta_update(f"intent: {payload['intent']}"))


def _merge_answer_sections(summary_text: str, conclusion: str) -> str:
    summary = summary_text.strip()
    conclusion_text = conclusion.strip()
    if not summary:
        return conclusion_text
    if not conclusion_text:
        return summary
    if summary in conclusion_text:
        return conclusion_text
    if conclusion_text in summary:
        return summary
    return f"{summary}\n\n{conclusion_text}"
