from __future__ import annotations

import unittest
from types import SimpleNamespace

from research_system.runtime.orchestration._runtime_control_stage_recorder import (
    RuntimeControlStageRecorder,
)


class _ControlPlaneStub:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def record_runtime_control_stage_decision(self, *, row: dict):
        self.rows.append(dict(row))


class RuntimeControlStageRecorderTests(unittest.TestCase):
    def test_record_persists_runtime_control_row_to_context_and_control_plane(self) -> None:
        control_plane = _ControlPlaneStub()
        context = SimpleNamespace(
            runtime_control_stage_decisions=[],
            dynamic_trace=[],
            control_plane=control_plane,
        )
        recorder = RuntimeControlStageRecorder(
            runtime_binding_getter=lambda: {
                "context": context,
                "request_id": "req-runtime-control",
                "intent": "review_thesis",
            }
        )

        recorder.record(
            event_type="runtime_control_transition_decided",
            message="runtime transition decided",
            payload={
                "intent": "review_thesis",
                "transition_type": "request_action_replan",
            },
            phase="supervision",
            agent="runtime_control",
        )

        expected_row = {
            "event": "runtime_control_transition_decided",
            "intent": "review_thesis",
            "transition_type": "request_action_replan",
        }
        self.assertEqual(context.runtime_control_stage_decisions, [expected_row])
        self.assertEqual(context.dynamic_trace, [expected_row])
        self.assertEqual(control_plane.rows, [expected_row])


if __name__ == "__main__":
    unittest.main()
