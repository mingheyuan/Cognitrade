from __future__ import annotations

import unittest

from research_system.cli.chat_controller import chat_event_listener
from research_system.cli.chat_event_adapter import build_chat_presenter, display_chunk_to_update, event_to_display_chunks
from research_system.cli.chat_updates import TranscriptUpdate, default_update_for_text
from research_system.schemas.runtime import RunEvent


class ChatEventAdapterTests(unittest.TestCase):
    def test_chat_event_listener_emits_transcript_updates(self) -> None:
        presenter = build_chat_presenter(thinking_enabled=True, events_mode="compact")
        captured: list[TranscriptUpdate] = []
        event = RunEvent(
            request_id="01REQ",
            run_id="01RUN",
            intent="stock_check",
            event_type="phase_started",
            phase="collect",
            agent="collector",
            message="Phase started: collect",
        )
        chat_event_listener(event, presenter=presenter, output_writer=captured.append)
        self.assertTrue(captured)
        self.assertTrue(all(isinstance(update, TranscriptUpdate) for update in captured))
        self.assertEqual(captured[0].kind, "status")

    def test_event_to_display_chunks_returns_presenter_output(self) -> None:
        presenter = build_chat_presenter(thinking_enabled=True, events_mode="compact")
        event = RunEvent(
            request_id="01REQ",
            run_id="01RUN",
            intent="stock_check",
            event_type="phase_started",
            phase="collect",
            agent="collector",
            message="Phase started: collect",
        )
        chunks = event_to_display_chunks(event, presenter=presenter)
        self.assertTrue(chunks)
        self.assertEqual(chunks[0].kind, "status")

    def test_display_chunk_to_update_uses_replace_for_status(self) -> None:
        update = display_chunk_to_update(type("Chunk", (), {"kind": "status", "text": "collect"})())
        self.assertEqual(update.action, "replace")
        self.assertEqual(update.kind, "status")

    def test_display_chunk_to_update_preserves_state_patch(self) -> None:
        update = display_chunk_to_update(
            type(
                "Chunk",
                (),
                {
                    "kind": "status",
                    "text": "collect",
                    "state_patch": {"current_phase": "collect"},
                },
            )()
        )
        self.assertEqual(update.state_patch.current_phase, "collect")

    def test_event_to_display_chunks_emits_structured_phase_patch(self) -> None:
        presenter = build_chat_presenter(thinking_enabled=True, events_mode="compact")
        event = RunEvent(
            request_id="01REQ",
            run_id="01RUN",
            intent="stock_check",
            event_type="phase_started",
            phase="collect",
            agent="collector",
            message="Phase started: collect",
        )
        chunks = event_to_display_chunks(event, presenter=presenter)
        status_chunk = next(chunk for chunk in chunks if chunk.kind == "status")
        self.assertEqual(status_chunk.state_patch, {"current_phase": "collect"})

    def test_default_update_for_text_uses_message_metadata(self) -> None:
        thinking = default_update_for_text("thinking", "route request")
        answer_stream = default_update_for_text("answer_delta", "hello")
        meta = default_update_for_text("meta", "run_id: 01ABC")
        self.assertEqual(thinking.action, "append")
        self.assertEqual(answer_stream.action, "stream_append")
        self.assertEqual(meta.action, "append")
        self.assertEqual(meta.state_patch.current_run_id, "01ABC")

    def test_default_update_for_text_infers_phase_and_gate_state_patch(self) -> None:
        phase = default_update_for_text("status", "collect")
        gate = default_update_for_text("status", "gate: EvidenceGate pass")
        warning = default_update_for_text("warning", "stop failed")
        self.assertEqual(phase.state_patch.current_phase, "collect")
        self.assertEqual(gate.state_patch.current_gate, "EvidenceGate pass")
        self.assertEqual(warning.state_patch.run_status_label, "warning")


if __name__ == "__main__":
    unittest.main()
