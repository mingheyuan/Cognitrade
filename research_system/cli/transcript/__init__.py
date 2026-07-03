from __future__ import annotations

from importlib import import_module

__all__ = [
    "TranscriptMessage",
    "append_stream_transcript_message",
    "append_transcript_message",
    "clear_stream_slot",
    "display_block_title_for_message",
    "make_transcript_message",
    "record_transcript_event",
    "replace_transcript_message",
    "reset_turn_scoped_slots",
]

_EXPORTS = {
    "TranscriptMessage": ("research_system.cli.transcript.chat_transcript", "TranscriptMessage"),
    "append_stream_transcript_message": (
        "research_system.cli.transcript.chat_transcript_store",
        "append_stream_transcript_message",
    ),
    "append_transcript_message": ("research_system.cli.transcript.chat_transcript_store", "append_transcript_message"),
    "clear_stream_slot": ("research_system.cli.transcript.chat_transcript_store", "clear_stream_slot"),
    "display_block_title_for_message": ("research_system.cli.transcript.chat_transcript", "display_block_title_for_message"),
    "make_transcript_message": ("research_system.cli.transcript.chat_transcript", "make_transcript_message"),
    "record_transcript_event": ("research_system.cli.transcript.chat_transcript_store", "record_transcript_event"),
    "replace_transcript_message": ("research_system.cli.transcript.chat_transcript_store", "replace_transcript_message"),
    "reset_turn_scoped_slots": ("research_system.cli.transcript.chat_transcript_store", "reset_turn_scoped_slots"),
}


def __getattr__(name: str):
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
