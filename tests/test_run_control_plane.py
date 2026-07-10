from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from research_system.runtime.core.control_plane import RunControlPlane, RunControlState


class RunControlPlaneTests(unittest.TestCase):
    def test_initialize_persists_canonical_control_state(self) -> None:
        with TemporaryDirectory() as tmpdir:
            control_plane = RunControlPlane(Path(tmpdir) / "run_control.json")
            state = control_plane.initialize(run_id="run-1", run_timeout_seconds=30)
            loaded = control_plane.load()

        self.assertEqual(state.run_id, "run-1")
        self.assertFalse(state.cancellation_requested)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.run_id, "run-1")
        self.assertFalse(hasattr(loaded, "runtime_control_stage_decisions"))

    def test_request_cancellation_is_persisted(self) -> None:
        with TemporaryDirectory() as tmpdir:
            control_plane = RunControlPlane(Path(tmpdir) / "run_control.json")
            control_plane.initialize(run_id="run-1", run_timeout_seconds=30)
            state = control_plane.request_cancellation(reason="manual_test")

        self.assertIsNotNone(state)
        self.assertTrue(state.cancellation_requested)
        self.assertEqual(state.cancellation_reason, "manual_test")

    def test_from_dict_accepts_current_control_shape(self) -> None:
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
        self.assertEqual(state.run_id, "run-1")


if __name__ == "__main__":
    unittest.main()
