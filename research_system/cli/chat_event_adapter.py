from __future__ import annotations

from research_system.runtime.chat_presenter import ChatStreamPresenter, DisplayChunk
from research_system.schemas.runtime import RunEvent
from research_system.cli.chat_updates import (
    ShellStatePatch,
    TranscriptUpdate,
    default_update_for_text,
    dispatch_transcript_update,
    state_patch_update,
)


def build_chat_presenter(*, thinking_enabled: bool, events_mode: str) -> ChatStreamPresenter:
    return ChatStreamPresenter(thinking_enabled=thinking_enabled, events_mode=events_mode)


def event_to_display_chunks(
    event: RunEvent,
    *,
    presenter: ChatStreamPresenter,
) -> list[DisplayChunk]:
    return list(presenter.on_event(event))


def display_chunk_to_update(chunk: DisplayChunk) -> TranscriptUpdate:
    update = default_update_for_text(chunk.kind, chunk.text)
    state_patch = getattr(chunk, "state_patch", None)
    if not state_patch:
        return update
    return TranscriptUpdate(
        action=update.action,
        kind=update.kind,
        text=update.text,
        slot_key=update.slot_key,
        state_patch=ShellStatePatch(
            current_phase=state_patch.get("current_phase"),
            current_gate=state_patch.get("current_gate"),
        ),
    )


def chat_event_listener(event: RunEvent, *, presenter: ChatStreamPresenter, output_writer=None) -> None:
    for chunk in event_to_display_chunks(event, presenter=presenter):
        dispatch_transcript_update(output_writer, display_chunk_to_update(chunk))
    trace_payload = presenter.current_trace_payload()
    if trace_payload is not None:
        dispatch_transcript_update(
            output_writer,
            state_patch_update(
                ShellStatePatch(
                    current_run_id=event.run_id,
                    trace_payload=trace_payload,
                )
            ),
        )
