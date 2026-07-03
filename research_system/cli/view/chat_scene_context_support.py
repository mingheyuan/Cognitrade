from __future__ import annotations

from typing import Any, Callable

from research_system.cli.state.chat_types import TuiSession


ThreadSummaryLoader = Callable[..., dict[str, Any]]


def idle_transcript_context_text(
    session: TuiSession,
    *,
    current_thread_summary: ThreadSummaryLoader,
) -> str:
    parts: list[str] = [f"output transcript:{session.output_mode}"]
    try:
        summary = current_thread_summary(settings=session.settings, state=session.state)
    except AttributeError:
        return " | ".join(parts)
    active_context_parts: list[str] = []
    current_intent = str(session.state.current_intent or "").strip()
    current_phase = str(session.current_phase or "").strip()
    run_state = str(session.run_status_label or "").strip()
    if current_intent and current_intent != "auto":
        active_context_parts.append(current_intent)
    if current_phase and current_phase != "-":
        active_context_parts.append(current_phase)
    if run_state and run_state not in {"idle", "-"}:
        active_context_parts.append(run_state)
    if active_context_parts:
        parts.append("context " + " / ".join(active_context_parts))
    thread_label = str(summary.get("thread_label") or "").strip()
    topic_title = str(summary.get("topic_title") or "").strip()
    thread_status = str(summary.get("thread_status") or "").strip()
    recommended_next_step = str(summary.get("recommended_next_step") or "").strip()
    if thread_label:
        parts.append(f"thread {thread_label}")
    if topic_title:
        parts.append(f"topic {topic_title}")
    if thread_status and thread_status != "active":
        parts.append(f"state {thread_status}")
    if recommended_next_step:
        parts.append(f"next {recommended_next_step}")
    return " | ".join(parts)

