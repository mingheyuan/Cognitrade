from __future__ import annotations

import sys

from research_system.cli.state.chat_update_models import TranscriptUpdate


def dispatch_transcript_update(output_writer, update: TranscriptUpdate) -> None:
    if output_writer is not None:
        try:
            output_writer(update)
            return
        except TypeError:
            if update.kind is not None:
                output_writer(update.kind, update.text)
                return
            raise

    if update.action == "stream_append":
        sys.stdout.write(update.text)
        sys.stdout.flush()
        return
    if update.action == "stream_done":
        print()
        return
    if update.kind in {"thinking", "status", "event", "answer"}:
        print(update.kind, flush=True)
        print(f"  {update.text}", flush=True)
        return
    if update.text:
        print(update.text, flush=True)


__all__ = ["dispatch_transcript_update"]
