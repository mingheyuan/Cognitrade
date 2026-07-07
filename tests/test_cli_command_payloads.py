from __future__ import annotations

import unittest

from research_system.cli.commands.cli_command_payloads import (
    PROPOSAL_COMMANDS,
    RUN_ID_COMMANDS,
    RUN_ID_QUERY_COMMANDS,
    STATUS_FILTER_COMMANDS,
    SUBAGENT_COMMANDS,
    build_cli_command_payload,
)


class CliCommandPayloadsTests(unittest.TestCase):
    def test_command_groups_cover_expected_protocol_sets(self) -> None:
        self.assertEqual(STATUS_FILTER_COMMANDS, frozenset({"history", "status", "doctor", "check", "cleanup", "reindex-qdrant"}))
        self.assertEqual(RUN_ID_COMMANDS, frozenset({"replay", "resume", "cancel", "trace", "events", "run-steps", "metrics"}))
        self.assertEqual(RUN_ID_QUERY_COMMANDS, frozenset({"subagent-read", "run-thread", "rerun-from"}))
        self.assertEqual(SUBAGENT_COMMANDS, frozenset({"subagent-read"}))
        self.assertEqual(PROPOSAL_COMMANDS, frozenset({"proposals", "proposal", "review-proposal"}))

    def test_build_history_command_payload(self) -> None:
        payload = build_cli_command_payload(command="history", limit=10, status_filter="completed")
        self.assertEqual(
            payload,
            {
                "command": "history",
                "limit": 10,
                "status_filter": "completed",
            },
        )

    def test_build_replay_command_payload(self) -> None:
        payload = build_cli_command_payload(command="replay", run_id="01RUN")
        self.assertEqual(
            payload,
            {
                "command": "replay",
                "run_id": "01RUN",
            },
        )

    def test_build_trace_command_payload(self) -> None:
        payload = build_cli_command_payload(command="trace", run_id="01RUN")
        self.assertEqual(
            payload,
            {
                "command": "trace",
                "run_id": "01RUN",
            },
        )

    def test_build_run_steps_command_payload(self) -> None:
        payload = build_cli_command_payload(command="run-steps", run_id="01RUN")
        self.assertEqual(
            payload,
            {
                "command": "run-steps",
                "run_id": "01RUN",
            },
        )

    def test_build_metrics_command_payload(self) -> None:
        payload = build_cli_command_payload(command="metrics", run_id="01RUN")
        self.assertEqual(
            payload,
            {
                "command": "metrics",
                "run_id": "01RUN",
            },
        )

    def test_build_subagent_read_command_payload(self) -> None:
        payload = build_cli_command_payload(command="subagent-read", run_id="01RUN", decision="01INV")
        self.assertEqual(
            payload,
            {
                "command": "subagent-read",
                "run_id": "01RUN",
                "query": "01INV",
            },
        )

    def test_build_run_thread_command_payload(self) -> None:
        payload = build_cli_command_payload(command="run-thread", run_id="01RUN", query="thread-child")
        self.assertEqual(
            payload,
            {
                "command": "run-thread",
                "run_id": "01RUN",
                "query": "thread-child",
            },
        )

    def test_build_rerun_from_command_payload(self) -> None:
        payload = build_cli_command_payload(command="rerun-from", run_id="01RUN", query="retrieve")
        self.assertEqual(
            payload,
            {
                "command": "rerun-from",
                "run_id": "01RUN",
                "query": "retrieve",
            },
        )

    def test_build_status_like_command_payload(self) -> None:
        payload = build_cli_command_payload(command="status", limit=20, status_filter=None)
        self.assertEqual(
            payload,
            {
                "command": "status",
                "limit": 20,
                "status_filter": None,
            },
        )

    def test_build_proposals_command_payload(self) -> None:
        payload = build_cli_command_payload(command="proposals", limit=10, status="pending")
        self.assertEqual(
            payload,
            {
                "command": "proposals",
                "limit": 10,
                "status_filter": "pending",
            },
        )

    def test_build_proposal_command_payload(self) -> None:
        payload = build_cli_command_payload(command="proposal", proposal_id="01PROP")
        self.assertEqual(
            payload,
            {
                "command": "proposal",
                "run_id": "01PROP",
            },
        )

    def test_build_review_proposal_command_payload(self) -> None:
        payload = build_cli_command_payload(
            command="review-proposal",
            proposal_id="01PROP",
            decision="approve",
            reviewer="manual_chat_review",
            reason="looks good",
        )
        self.assertEqual(
            payload,
            {
                "command": "review-proposal",
                "run_id": "01PROP",
                "query": "approve",
                "intent": "manual_chat_review",
                "status_filter": "looks good",
            },
        )


if __name__ == "__main__":
    unittest.main()
