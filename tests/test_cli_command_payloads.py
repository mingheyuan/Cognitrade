from __future__ import annotations

import unittest

from research_system.cli.commands.cli_command_payloads import (
    PROPOSAL_COMMANDS,
    RUN_ID_COMMANDS,
    RUN_ID_QUERY_COMMANDS,
    RUN_ID_SELECTOR_COMMANDS,
    STATUS_FILTER_COMMANDS,
    SUBAGENT_DISCOVERY_COMMANDS,
    SUBAGENT_COMMANDS,
    build_cli_command_payload,
)


class CliCommandPayloadsTests(unittest.TestCase):
    def test_command_groups_cover_expected_protocol_sets(self) -> None:
        self.assertEqual(STATUS_FILTER_COMMANDS, frozenset({"history", "status", "doctor", "check", "cleanup", "reindex-qdrant"}))
        self.assertEqual(RUN_ID_COMMANDS, frozenset({"replay", "resume", "cancel", "trace", "events", "run-steps", "metrics"}))
        self.assertEqual(RUN_ID_QUERY_COMMANDS, frozenset({"subagent-read", "run-thread", "rerun-from"}))
        self.assertEqual(RUN_ID_SELECTOR_COMMANDS, frozenset({"rerun-subagent"}))
        self.assertEqual(SUBAGENT_COMMANDS, frozenset({"subagent-read"}))
        self.assertEqual(PROPOSAL_COMMANDS, frozenset({"proposals", "proposal", "review-proposal"}))
        self.assertEqual(
            SUBAGENT_DISCOVERY_COMMANDS,
            frozenset(
                {
                    "subagent-discoveries",
                    "subagent-discovery",
                    "review-subagent-discovery",
                    "activate-subagent-discovery",
                }
            ),
        )

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

    def test_build_rerun_subagent_command_payload(self) -> None:
        payload = build_cli_command_payload(
            command="rerun-subagent",
            run_id="01RUN",
            action_node_id="retrieve",
            dag_id="phase_two",
            subagent_node_id="retrieval_decider",
        )
        self.assertEqual(
            payload,
            {
                "command": "rerun-subagent",
                "run_id": "01RUN",
                "action_node_id": "retrieve",
                "dag_id": "phase_two",
                "subagent_node_id": "retrieval_decider",
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

    def test_build_subagent_discovery_commands_payloads(self) -> None:
        listed = build_cli_command_payload(command="subagent-discoveries", limit=10, status="approved")
        detail = build_cli_command_payload(command="subagent-discovery", ticket_id="01DISC")
        reviewed = build_cli_command_payload(
            command="review-subagent-discovery",
            ticket_id="01DISC",
            decision="approve",
            reviewer="manual_chat_review",
            reason="validated",
        )
        activated = build_cli_command_payload(
            command="activate-subagent-discovery",
            ticket_id="01DISC",
            delegate_subagent_type="stock_snapshot_collector",
            activated_by="manual_chat_activation",
            reason="delegate approved",
            candidate_subagent_type="candidate_collect_snapshot",
        )
        runtime_activated = build_cli_command_payload(
            command="activate-subagent-discovery",
            ticket_id="01DISC",
            activated_by="manual_cli_activation",
            registration_type="snapshot_skill_runner",
            skill_name="market_snapshot_skill",
            snapshot_type="market",
            candidate_subagent_type="candidate_collect_snapshot",
        )
        generic_runtime_activated = build_cli_command_payload(
            command="activate-subagent-discovery",
            ticket_id="01DISC",
            activated_by="manual_cli_activation",
            registration_type="skill_runner",
            skill_name="analysis_skill",
            candidate_subagent_type="candidate_report_analysis",
        )
        http_runtime_activated = build_cli_command_payload(
            command="activate-subagent-discovery",
            ticket_id="01DISC",
            activated_by="manual_cli_activation",
            registration_type="http_json_runner",
            source_id="eastmoney",
            endpoint_path="/api/market-signal",
            http_method="GET",
            timeout_seconds=9.5,
            candidate_subagent_type="candidate_collect_http_extension",
        )

        self.assertEqual(
            listed,
            {"command": "subagent-discoveries", "limit": 10, "status_filter": "approved"},
        )
        self.assertEqual(
            detail,
            {"command": "subagent-discovery", "ticket_id": "01DISC"},
        )
        self.assertEqual(
            reviewed,
            {
                "command": "review-subagent-discovery",
                "ticket_id": "01DISC",
                "query": "approve",
                "intent": "manual_chat_review",
                "status_filter": "validated",
            },
        )
        self.assertEqual(
            activated,
            {
                "command": "activate-subagent-discovery",
                "ticket_id": "01DISC",
                "query": "stock_snapshot_collector",
                "intent": "manual_chat_activation",
                "status_filter": "delegate approved",
                "subagent_node_id": "candidate_collect_snapshot",
                "registration_type": "",
                "skill_name": "",
                "snapshot_type": "",
                "source_id": "",
                "endpoint_path": "",
                "http_method": "",
                "timeout_seconds": None,
            },
        )
        self.assertEqual(
            runtime_activated,
            {
                "command": "activate-subagent-discovery",
                "ticket_id": "01DISC",
                "query": "",
                "intent": "manual_cli_activation",
                "status_filter": None,
                "subagent_node_id": "candidate_collect_snapshot",
                "registration_type": "snapshot_skill_runner",
                "skill_name": "market_snapshot_skill",
                "snapshot_type": "market",
                "source_id": "",
                "endpoint_path": "",
                "http_method": "",
                "timeout_seconds": None,
            },
        )
        self.assertEqual(
            generic_runtime_activated,
            {
                "command": "activate-subagent-discovery",
                "ticket_id": "01DISC",
                "query": "",
                "intent": "manual_cli_activation",
                "status_filter": None,
                "subagent_node_id": "candidate_report_analysis",
                "registration_type": "skill_runner",
                "skill_name": "analysis_skill",
                "snapshot_type": "",
                "source_id": "",
                "endpoint_path": "",
                "http_method": "",
                "timeout_seconds": None,
            },
        )
        self.assertEqual(
            http_runtime_activated,
            {
                "command": "activate-subagent-discovery",
                "ticket_id": "01DISC",
                "query": "",
                "intent": "manual_cli_activation",
                "status_filter": None,
                "subagent_node_id": "candidate_collect_http_extension",
                "registration_type": "http_json_runner",
                "skill_name": "",
                "snapshot_type": "",
                "source_id": "eastmoney",
                "endpoint_path": "/api/market-signal",
                "http_method": "GET",
                "timeout_seconds": 9.5,
            },
        )


if __name__ == "__main__":
    unittest.main()
