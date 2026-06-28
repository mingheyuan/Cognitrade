from __future__ import annotations

import unittest

from research_system.runtime.chat_presenter import ChatStreamPresenter
from research_system.schemas.runtime import RunEvent


def _event(*, event_type: str, intent: str = "sector_research", phase: str | None = None, agent: str | None = None, message: str = "", payload: dict | None = None) -> RunEvent:
    return RunEvent(
        request_id="req",
        run_id="run",
        intent=intent,
        event_type=event_type,
        phase=phase,
        agent=agent,
        message=message or event_type,
        payload=payload,
    )


class ChatPresenterTests(unittest.TestCase):
    def test_intent_event_generates_thinking_text(self) -> None:
        presenter = ChatStreamPresenter()
        chunks = presenter.on_event(_event(event_type="intent_identified", intent="stock_check", message="Intent identified: stock_check"))
        self.assertEqual(chunks[0].kind, "thinking")
        self.assertIn("单票研究请求", chunks[0].text)

    def test_phase_started_generates_status_and_thinking(self) -> None:
        presenter = ChatStreamPresenter()
        chunks = presenter.on_event(_event(event_type="phase_started", intent="sector_research", phase="collect", agent="collector", message="Phase started: collect"))
        self.assertEqual(chunks[0].kind, "status")
        self.assertEqual(chunks[0].text, "collect")
        self.assertEqual(chunks[1].kind, "thinking")
        self.assertIn("板块快照", chunks[1].text)

    def test_gate_needs_review_generates_summary_and_thinking(self) -> None:
        presenter = ChatStreamPresenter()
        chunks = presenter.on_event(_event(event_type="phase_completed", phase="gate", message="VerifiedFactGate: needs_review"))
        self.assertEqual(chunks[0].kind, "status")
        self.assertIn("VerifiedFactGate needs_review", chunks[0].text)
        self.assertEqual(chunks[1].kind, "thinking")
        self.assertIn("还需要人工复核", chunks[1].text)

    def test_full_event_mode_keeps_raw_event_line(self) -> None:
        presenter = ChatStreamPresenter(events_mode="full")
        chunks = presenter.on_event(_event(event_type="run_completed", message="Run completed"))
        self.assertEqual(chunks[0].kind, "event")
        self.assertIn("[run_completed]", chunks[0].text)


if __name__ == "__main__":
    unittest.main()
