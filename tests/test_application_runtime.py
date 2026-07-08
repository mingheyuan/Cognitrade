from __future__ import annotations

import argparse
import unittest
from pathlib import Path

from research_system.cli.app import build_parser, main
from research_system.cli.application.app_entry_support import (
    build_app_settings as application_build_app_settings,
    execute_app_main_command as application_execute_app_main_command,
)
from research_system.cli.application.app_parser_support import (
    build_app_parser as application_build_app_parser,
)
from research_system.runtime.platform.settings import Settings


class ApplicationRuntimeTests(unittest.TestCase):
    def test_application_entry_and_parser_exports_remain_available(self) -> None:
        self.assertIsNotNone(build_parser)
        self.assertIsNotNone(main)
        self.assertIsNotNone(application_build_app_settings)
        self.assertIsNotNone(application_execute_app_main_command)
        self.assertIsNotNone(application_build_app_parser)

    def test_application_parser_supports_runtime_devtool_commands(self) -> None:
        parser = application_build_app_parser()

        metrics_args = parser.parse_args(["metrics", "01RUN"])
        run_thread_args = parser.parse_args(["run-thread", "01RUN", "thread-child"])
        rerun_args = parser.parse_args(["rerun-from", "01RUN", "retrieve"])
        rerun_subagent_args = parser.parse_args(
            ["rerun-subagent", "01RUN", "retrieve", "phase_two", "retrieval_decider"]
        )
        discovery_args = parser.parse_args(["subagent-discovery", "01DISC"])
        activate_args = parser.parse_args(
            [
                "activate-subagent-discovery",
                "01DISC",
                "stock_snapshot_collector",
                "--candidate-subagent-type",
                "candidate_screening_strategy",
            ]
        )

        self.assertEqual(metrics_args.command, "metrics")
        self.assertEqual(metrics_args.run_id, "01RUN")
        self.assertEqual(run_thread_args.command, "run-thread")
        self.assertEqual(run_thread_args.thread_id, "thread-child")
        self.assertEqual(rerun_args.command, "rerun-from")
        self.assertEqual(rerun_args.node_id, "retrieve")
        self.assertEqual(rerun_subagent_args.command, "rerun-subagent")
        self.assertEqual(rerun_subagent_args.action_node_id, "retrieve")
        self.assertEqual(rerun_subagent_args.dag_id, "phase_two")
        self.assertEqual(rerun_subagent_args.subagent_node_id, "retrieval_decider")
        self.assertEqual(discovery_args.ticket_id, "01DISC")
        self.assertEqual(activate_args.delegate_subagent_type, "stock_snapshot_collector")
        self.assertEqual(activate_args.candidate_subagent_type, "candidate_screening_strategy")
        runtime_activate_args = parser.parse_args(
            [
                "activate-subagent-discovery",
                "01DISC",
                "--registration-type",
                "snapshot_skill_runner",
                "--skill-name",
                "market_snapshot_skill",
                "--snapshot-type",
                "market",
            ]
        )
        self.assertIsNone(runtime_activate_args.delegate_subagent_type)
        self.assertEqual(runtime_activate_args.registration_type, "snapshot_skill_runner")
        self.assertEqual(runtime_activate_args.skill_name, "market_snapshot_skill")
        self.assertEqual(runtime_activate_args.snapshot_type, "market")
        generic_runtime_activate_args = parser.parse_args(
            [
                "activate-subagent-discovery",
                "01DISC",
                "--registration-type",
                "skill_runner",
                "--skill-name",
                "analysis_skill",
            ]
        )
        self.assertIsNone(generic_runtime_activate_args.delegate_subagent_type)
        self.assertEqual(generic_runtime_activate_args.registration_type, "skill_runner")
        self.assertEqual(generic_runtime_activate_args.skill_name, "analysis_skill")
        self.assertIsNone(generic_runtime_activate_args.snapshot_type)

    def test_execute_app_main_command_routes_query_from_thread_and_node_arguments(self) -> None:
        calls: list[dict[str, object]] = []

        def fake_execute_cli_command_fn(settings, **kwargs):
            calls.append(kwargs)
            return {"run_id": "01RUN", "status": "completed"}

        settings = Settings.from_env(Path("."))

        thread_args = argparse.Namespace(
            command="run-thread",
            run_id="01RUN",
            thread_id="thread-child",
            as_json=False,
        )
        rerun_args = argparse.Namespace(
            command="rerun-from",
            run_id="01RUN",
            node_id="retrieve",
            as_json=False,
        )
        rerun_subagent_args = argparse.Namespace(
            command="rerun-subagent",
            run_id="01RUN",
            action_node_id="retrieve",
            dag_id="phase_two",
            subagent_node_id="retrieval_decider",
            as_json=False,
        )
        review_discovery_args = argparse.Namespace(
            command="review-subagent-discovery",
            ticket_id="01DISC",
            decision="approve",
            reviewer="manual_cli_review",
            reason="validated",
            as_json=False,
        )
        activate_discovery_args = argparse.Namespace(
            command="activate-subagent-discovery",
            ticket_id="01DISC",
            delegate_subagent_type="stock_snapshot_collector",
            candidate_subagent_type="candidate_screening_strategy",
            activated_by="ops_user",
            reason="delegate approved",
            registration_type=None,
            skill_name=None,
            snapshot_type=None,
            as_json=False,
        )
        runtime_activate_discovery_args = argparse.Namespace(
            command="activate-subagent-discovery",
            ticket_id="01DISC",
            delegate_subagent_type=None,
            candidate_subagent_type="candidate_screening_strategy",
            activated_by="ops_user",
            reason="activate runtime skill",
            registration_type="snapshot_skill_runner",
            skill_name="market_snapshot_skill",
            snapshot_type="market",
            as_json=False,
        )
        generic_runtime_activate_discovery_args = argparse.Namespace(
            command="activate-subagent-discovery",
            ticket_id="01DISC",
            delegate_subagent_type=None,
            candidate_subagent_type="candidate_report_analysis",
            activated_by="ops_user",
            reason="activate generic runtime skill",
            registration_type="skill_runner",
            skill_name="analysis_skill",
            snapshot_type=None,
            as_json=False,
        )

        self.assertEqual(
            application_execute_app_main_command(
                settings,
                thread_args,
                build_chat_entry_runtime_fn=None,
                run_chat_entry_fn=None,
                run_chat_tui_fn=None,
                run_chat_fallback_fn=None,
                handle_chat_line_fn=None,
                execute_cli_command_fn=fake_execute_cli_command_fn,
                print_payload_fn=lambda payload, as_json: 0,
            ),
            0,
        )
        self.assertEqual(
            application_execute_app_main_command(
                settings,
                rerun_args,
                build_chat_entry_runtime_fn=None,
                run_chat_entry_fn=None,
                run_chat_tui_fn=None,
                run_chat_fallback_fn=None,
                handle_chat_line_fn=None,
                execute_cli_command_fn=fake_execute_cli_command_fn,
                print_payload_fn=lambda payload, as_json: 0,
            ),
            0,
        )
        self.assertEqual(
            application_execute_app_main_command(
                settings,
                rerun_subagent_args,
                build_chat_entry_runtime_fn=None,
                run_chat_entry_fn=None,
                run_chat_tui_fn=None,
                run_chat_fallback_fn=None,
                handle_chat_line_fn=None,
                execute_cli_command_fn=fake_execute_cli_command_fn,
                print_payload_fn=lambda payload, as_json: 0,
            ),
            0,
        )
        self.assertEqual(
            application_execute_app_main_command(
                settings,
                review_discovery_args,
                build_chat_entry_runtime_fn=None,
                run_chat_entry_fn=None,
                run_chat_tui_fn=None,
                run_chat_fallback_fn=None,
                handle_chat_line_fn=None,
                execute_cli_command_fn=fake_execute_cli_command_fn,
                print_payload_fn=lambda payload, as_json: 0,
            ),
            0,
        )
        self.assertEqual(
            application_execute_app_main_command(
                settings,
                activate_discovery_args,
                build_chat_entry_runtime_fn=None,
                run_chat_entry_fn=None,
                run_chat_tui_fn=None,
                run_chat_fallback_fn=None,
                handle_chat_line_fn=None,
                execute_cli_command_fn=fake_execute_cli_command_fn,
                print_payload_fn=lambda payload, as_json: 0,
            ),
            0,
        )
        self.assertEqual(
            application_execute_app_main_command(
                settings,
                runtime_activate_discovery_args,
                build_chat_entry_runtime_fn=None,
                run_chat_entry_fn=None,
                run_chat_tui_fn=None,
                run_chat_fallback_fn=None,
                handle_chat_line_fn=None,
                execute_cli_command_fn=fake_execute_cli_command_fn,
                print_payload_fn=lambda payload, as_json: 0,
            ),
            0,
        )
        self.assertEqual(
            application_execute_app_main_command(
                settings,
                generic_runtime_activate_discovery_args,
                build_chat_entry_runtime_fn=None,
                run_chat_entry_fn=None,
                run_chat_tui_fn=None,
                run_chat_fallback_fn=None,
                handle_chat_line_fn=None,
                execute_cli_command_fn=fake_execute_cli_command_fn,
                print_payload_fn=lambda payload, as_json: 0,
            ),
            0,
        )

        self.assertEqual(calls[0]["command"], "run-thread")
        self.assertEqual(calls[0]["query"], "thread-child")
        self.assertEqual(calls[1]["command"], "rerun-from")
        self.assertEqual(calls[1]["query"], "retrieve")
        self.assertEqual(calls[2]["command"], "rerun-subagent")
        self.assertEqual(calls[2]["action_node_id"], "retrieve")
        self.assertEqual(calls[2]["dag_id"], "phase_two")
        self.assertEqual(calls[2]["subagent_node_id"], "retrieval_decider")
        self.assertEqual(calls[3]["command"], "review-subagent-discovery")
        self.assertEqual(calls[3]["query"], "approve")
        self.assertEqual(calls[3]["ticket_id"], "01DISC")
        self.assertEqual(calls[4]["command"], "activate-subagent-discovery")
        self.assertEqual(calls[4]["delegate_subagent_type"], "stock_snapshot_collector")
        self.assertEqual(calls[4]["candidate_subagent_type"], "candidate_screening_strategy")
        self.assertEqual(calls[5]["command"], "activate-subagent-discovery")
        self.assertEqual(calls[5]["registration_type"], "snapshot_skill_runner")
        self.assertEqual(calls[5]["skill_name"], "market_snapshot_skill")
        self.assertEqual(calls[5]["snapshot_type"], "market")
        self.assertEqual(calls[6]["command"], "activate-subagent-discovery")
        self.assertEqual(calls[6]["registration_type"], "skill_runner")
        self.assertEqual(calls[6]["skill_name"], "analysis_skill")
        self.assertIsNone(calls[6]["snapshot_type"])


if __name__ == "__main__":
    unittest.main()
