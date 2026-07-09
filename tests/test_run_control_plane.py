from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from research_system.runtime.core.control_plane import RunControlPlane, RunControlState


class RunControlPlaneTests(unittest.TestCase):
    def test_initialize_persists_empty_runtime_control_stage_decisions(self) -> None:
        with TemporaryDirectory() as tmpdir:
            control_plane = RunControlPlane(Path(tmpdir) / "run_control.json")

            state = control_plane.initialize(run_id="run-1", run_timeout_seconds=30)
            loaded = control_plane.load()

        self.assertEqual(state.runtime_control_stage_decisions, [])
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.runtime_control_stage_decisions, [])

    def test_record_runtime_control_stage_decision_persists_recent_rows(self) -> None:
        with TemporaryDirectory() as tmpdir:
            control_plane = RunControlPlane(Path(tmpdir) / "run_control.json")
            control_plane.initialize(run_id="run-1", run_timeout_seconds=30)

            control_plane.record_runtime_control_stage_decision(
                row={"event": "runtime_control_transition_decided", "transition_type": "apply_plan_patch"},
                max_entries=2,
            )
            control_plane.record_runtime_control_stage_decision(
                row={"event": "runtime_control_protocol_decided", "transition_type": "request_action_replan"},
                max_entries=2,
            )
            state = control_plane.record_runtime_control_stage_decision(
                row={"event": "runtime_control_subagent_protocol_resolved", "decision_type": "selected"},
                max_entries=2,
            )

        self.assertIsNotNone(state)
        self.assertEqual(
            [row["event"] for row in state.runtime_control_stage_decisions],
            [
                "runtime_control_protocol_decided",
                "runtime_control_subagent_protocol_resolved",
            ],
        )

    def test_from_dict_backfills_missing_runtime_control_stage_decisions(self) -> None:
        state = RunControlState.from_dict(
            {
                "run_id": "run-1",
                "cancellation_requested": False,
                "cancellation_reason": None,
                "deadline_at": "2026-07-10T00:00:00+00:00",
                "external_wait_deadline_at": None,
                "updated_at": "2026-07-10T00:00:00+00:00",
            }
        )

        self.assertEqual(state.runtime_control_stage_decisions, [])


if __name__ == "__main__":
    unittest.main()
