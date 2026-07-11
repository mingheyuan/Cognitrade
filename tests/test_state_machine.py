from __future__ import annotations

import unittest

from research_system.schemas.state_machine import (
    InvalidStatusTransition,
    RUN_TERMINAL_STATUSES,
    transition_status,
)


class StateMachineTests(unittest.TestCase):
    def test_run_lifecycle_allows_pause_resume_and_terminal_states(self) -> None:
        self.assertEqual(
            transition_status(scope="run", current="ready", target="running"),
            "running",
        )
        self.assertEqual(
            transition_status(scope="run", current="running", target="paused"),
            "paused",
        )
        self.assertEqual(
            transition_status(scope="run", current="paused", target="running"),
            "running",
        )
        self.assertIn("gated", RUN_TERMINAL_STATUSES)

    def test_node_rerun_is_explicitly_allowed_from_terminal_state(self) -> None:
        self.assertEqual(
            transition_status(scope="node", current="failed", target="pending"),
            "pending",
        )
        self.assertEqual(
            transition_status(scope="node", current="pending", target="running"),
            "running",
        )

    def test_invalid_terminal_run_transition_is_rejected(self) -> None:
        with self.assertRaises(InvalidStatusTransition):
            transition_status(scope="run", current="completed", target="running")

    def test_tool_approval_wait_is_distinct_from_tool_failure(self) -> None:
        self.assertEqual(
            transition_status(scope="tool", current="pending", target="waiting_approval"),
            "waiting_approval",
        )
        with self.assertRaises(InvalidStatusTransition):
            transition_status(scope="tool", current="failed", target="completed")


if __name__ == "__main__":
    unittest.main()
