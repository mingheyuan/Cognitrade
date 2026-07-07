from __future__ import annotations

import unittest

from research_system.cli.commands.cli_command_dispatch import (
    execute_cli_payload_command as commands_execute_cli_payload_command,
)
from research_system.cli.commands.cli_command_dispatch import execute_cli_payload_command
from research_system.cli.commands.cli_command_dispatch_registry import (
    CLI_COMMAND_EXECUTORS,
    resolve_cli_command_executor,
)
from research_system.cli.commands.cli_command_history_support import execute_history_command
from research_system.cli.commands.cli_command_proposal_support import (
    execute_proposal_detail_command,
    execute_proposals_command,
    execute_review_proposal_command,
)
from research_system.cli.commands.cli_command_run_support import execute_run_id_command, execute_subagent_command
from research_system.cli.commands.cli_command_system_support import (
    execute_check_command,
    execute_cleanup_command,
    execute_doctor_command,
    execute_reindex_command,
    execute_status_command,
)


class CliCommandDispatchRuntimeTests(unittest.TestCase):
    def test_top_level_cli_command_dispatch_remains_compatible_with_commands_package(self) -> None:
        self.assertIs(execute_cli_payload_command, commands_execute_cli_payload_command)

    def test_registry_exposes_domain_specific_command_supports(self) -> None:
        self.assertIsNotNone(execute_cli_payload_command)
        self.assertIs(resolve_cli_command_executor("history"), CLI_COMMAND_EXECUTORS["history"])
        self.assertIs(CLI_COMMAND_EXECUTORS["history"], execute_history_command)
        self.assertIs(CLI_COMMAND_EXECUTORS["proposals"], execute_proposals_command)
        self.assertIs(CLI_COMMAND_EXECUTORS["proposal"], execute_proposal_detail_command)
        self.assertIs(CLI_COMMAND_EXECUTORS["review-proposal"], execute_review_proposal_command)
        self.assertIs(CLI_COMMAND_EXECUTORS["trace"], execute_run_id_command)
        self.assertIs(CLI_COMMAND_EXECUTORS["subagent-read"], execute_subagent_command)
        self.assertIs(CLI_COMMAND_EXECUTORS["cleanup"], execute_cleanup_command)
        self.assertIs(CLI_COMMAND_EXECUTORS["reindex-qdrant"], execute_reindex_command)
        self.assertIs(CLI_COMMAND_EXECUTORS["check"], execute_check_command)
        self.assertIs(CLI_COMMAND_EXECUTORS["status"], execute_status_command)
        self.assertIs(CLI_COMMAND_EXECUTORS["doctor"], execute_doctor_command)


if __name__ == "__main__":
    unittest.main()
