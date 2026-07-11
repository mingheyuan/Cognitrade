from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from research_system.cli.commands.research_request_payloads import build_research_request
from research_system.cli.runtime.app.cli_runtime import build_cli_command_runtime
from research_system.runtime.platform.settings import Settings


class CliAgentOSEntryTests(unittest.TestCase):
    def test_run_command_uses_agent_os_without_constructing_legacy_orchestrator(self) -> None:
        class UnexpectedOrchestrator:
            def __init__(self, *_args, **_kwargs):
                raise AssertionError("CLI research must not construct ResearchOrchestrator")

        repository_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            settings = Settings.from_env(repository_root).model_copy(
                update={
                    "runs_root": root / "runs",
                    "memory_root": root / "memory",
                    "domain_packs_root": root / "empty_packs",
                }
            )
            runtime = build_cli_command_runtime(
                orchestrator_cls=UnexpectedOrchestrator,
                build_research_request_fn=build_research_request,
                execute_cli_payload_command_fn=lambda *_args, **_kwargs: None,
                run_checks_fn=lambda *_args, **_kwargs: {},
                build_status_fn=lambda *_args, **_kwargs: {},
                run_doctor_fn=lambda *_args, **_kwargs: {},
            )

            payload = runtime.execute(
                settings,
                command="run",
                query="你是谁？",
            )

        self.assertEqual(payload["mode"], "core")
        self.assertEqual(payload["status"], "completed")


if __name__ == "__main__":
    unittest.main()
