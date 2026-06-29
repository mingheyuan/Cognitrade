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
    def test_intent_event_generates_no_thinking_chunks(self) -> None:
        presenter = ChatStreamPresenter()
        chunks = presenter.on_event(_event(event_type="intent_identified", intent="stock_check", message="Intent identified: stock_check"))
        self.assertEqual(chunks, [])

    def test_stock_screen_intent_generates_no_thinking_text(self) -> None:
        presenter = ChatStreamPresenter()
        chunks = presenter.on_event(_event(event_type="intent_identified", intent="stock_screen", message="Intent identified: stock_screen"))
        self.assertEqual(chunks, [])

    def test_phase_started_generates_status_only(self) -> None:
        presenter = ChatStreamPresenter()
        chunks = presenter.on_event(_event(event_type="phase_started", intent="sector_research", phase="collect", agent="collector", message="Phase started: collect"))
        self.assertEqual(chunks[0].kind, "status")
        self.assertEqual(chunks[0].text, "collect")
        self.assertEqual(len(chunks), 1)

    def test_gate_needs_review_generates_summary_only(self) -> None:
        presenter = ChatStreamPresenter()
        chunks = presenter.on_event(_event(event_type="phase_completed", phase="gate", message="VerifiedFactGate: needs_review"))
        self.assertEqual(chunks[0].kind, "status")
        self.assertIn("VerifiedFactGate needs_review", chunks[0].text)
        self.assertEqual(len(chunks), 1)

    def test_full_event_mode_keeps_raw_event_line(self) -> None:
        presenter = ChatStreamPresenter(events_mode="full")
        chunks = presenter.on_event(_event(event_type="run_completed", message="Run completed"))
        self.assertEqual(chunks[0].kind, "event")
        self.assertIn("[run_completed]", chunks[0].text)

    def test_agent_progress_event_is_exposed_as_event_text(self) -> None:
        presenter = ChatStreamPresenter()
        chunks = presenter.on_event(
            _event(
                event_type="agent_progress",
                phase="screen",
                agent="screener",
                message="Enriching top 3 stock_screen candidates",
            )
        )
        self.assertEqual(chunks[0].kind, "event")
        self.assertIn("Enriching top 3 stock_screen candidates", chunks[0].text)

    def test_dynamic_action_inserted_event_is_exposed_as_event_text(self) -> None:
        presenter = ChatStreamPresenter()
        chunks = presenter.on_event(
            _event(
                event_type="dynamic_action_inserted",
                phase="collect",
                agent="collector",
                message="Dynamically inserted action: collect_replenish_notice",
            )
        )
        self.assertEqual(chunks[0].kind, "event")
        self.assertIn("collect_replenish_notice", chunks[0].text)

    def test_supervisor_decision_event_summarizes_next_action(self) -> None:
        presenter = ChatStreamPresenter()
        chunks = presenter.on_event(
            _event(
                event_type="supervisor_decision",
                message="Supervisor evaluated next actions",
                payload={
                    "decision_type": "continue",
                    "next_actions": [
                        {"node_id": "retrieve", "phase": "retrieve", "agent": "retriever"}
                    ],
                    "notes": [],
                },
            )
        )
        self.assertEqual(chunks[0].kind, "event")
        self.assertIn("next: retrieve / retriever / retrieve", chunks[0].text)

    def test_subagent_trace_event_is_exposed_as_event_text(self) -> None:
        presenter = ChatStreamPresenter()
        chunks = presenter.on_event(
            _event(
                event_type="subagent_trace",
                phase="retrieve",
                agent="retriever",
                message="blackboard_evidence_indexer [completed]: Index blackboard entries and expose evidence candidates.",
            )
        )
        self.assertEqual(chunks[0].kind, "event")
        self.assertIn("blackboard_evidence_indexer", chunks[0].text)


if __name__ == "__main__":
    unittest.main()
