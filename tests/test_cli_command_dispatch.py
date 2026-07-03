from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from research_system.cli.cli_command_dispatch import execute_cli_payload_command
from research_system.runtime.settings import Settings


class _DispatchOrchestratorStub:
    instances: list["_DispatchOrchestratorStub"] = []

    def __init__(self, settings) -> None:
        self.settings = settings
        self.closed = False
        self.__class__.instances.append(self)

    def __enter__(self) -> "_DispatchOrchestratorStub":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        self.closed = True

    def history(self) -> list[dict]:
        return [
            {"run_id": "01NEW", "status": "completed", "intent": "stock_check"},
            {"run_id": "01OLD", "status": "failed", "intent": "review_thesis"},
        ]

    def list_proposals(self, *, status=None, limit=50) -> list[dict]:
        return [{"proposal_id": "01PROP", "status": status or "pending", "proposal_type": "MethodologyTemplatePatch"}]

    def show_proposal(self, proposal_id: str) -> dict:
        return {"proposal_id": proposal_id, "status": "pending", "proposal_type": "MethodologyTemplatePatch"}

    def review_proposal(self, proposal_id: str, *, decision: str, reviewer: str, reason: str | None = None) -> dict:
        return {"proposal_id": proposal_id, "status": "approved", "reviewer": reviewer, "review_reason": reason or "", "decision": decision}

    def replay(self, run_id: str) -> dict:
        return {"run_id": run_id, "status": "completed"}

    def trace(self, run_id: str) -> dict:
        return {"run_id": run_id, "status": "completed", "subagent_trace": [], "dynamic_trace": [], "wait_trace": []}

    def subagent(self, run_id: str, invocation_id: str) -> dict:
        return {"run_id": run_id, "invocation_id": invocation_id, "trace_rows": [], "events": [], "event_counts": {"trace_rows": 0, "events": 0}}

    def steps(self, run_id: str) -> dict:
        return {"run_id": run_id, "steps": [], "step_counts": {"total": 0, "active": 0, "completed": 0, "pending": 0}}

    def resume(self, run_id: str) -> dict:
        return {"run_id": run_id, "status": "resumed"}

    def cancel(self, run_id: str) -> dict:
        return {"run_id": run_id, "status": "cancelled"}

    def cleanup(self) -> dict:
        return {"status": "completed", "message": "cleanup done"}

    def rebuild_qdrant(self) -> dict:
        return {"status": "completed", "message": "reindex done"}


class CliCommandDispatchTests(unittest.TestCase):
    def test_execute_cli_payload_command_filters_history_rows(self) -> None:
        rows = execute_cli_payload_command(
            Settings.from_env(Path(".")),
            command="history",
            limit=10,
            status_filter="completed",
            orchestrator_cls=_DispatchOrchestratorStub,
        )

        self.assertEqual(rows, [{"run_id": "01NEW", "status": "completed", "intent": "stock_check"}])

    def test_execute_cli_payload_command_dispatches_proposal_commands(self) -> None:
        proposals = execute_cli_payload_command(
            Settings.from_env(Path(".")),
            command="proposals",
            limit=5,
            status_filter="pending",
            orchestrator_cls=_DispatchOrchestratorStub,
        )
        detail = execute_cli_payload_command(
            Settings.from_env(Path(".")),
            command="proposal",
            run_id="01PROP",
            orchestrator_cls=_DispatchOrchestratorStub,
        )
        reviewed = execute_cli_payload_command(
            Settings.from_env(Path(".")),
            command="review-proposal",
            run_id="01PROP",
            query="approve",
            intent="reviewer_1",
            status_filter="looks good",
            orchestrator_cls=_DispatchOrchestratorStub,
        )

        self.assertEqual(proposals[0]["proposal_id"], "01PROP")
        self.assertEqual(detail["proposal_id"], "01PROP")
        self.assertEqual(reviewed["reviewer"], "reviewer_1")
        self.assertEqual(reviewed["review_reason"], "looks good")

    def test_execute_cli_payload_command_dispatches_run_id_commands(self) -> None:
        replayed = execute_cli_payload_command(
            Settings.from_env(Path(".")),
            command="replay",
            run_id="01RUN",
            orchestrator_cls=_DispatchOrchestratorStub,
        )
        traced = execute_cli_payload_command(
            Settings.from_env(Path(".")),
            command="trace",
            run_id="01RUN",
            orchestrator_cls=_DispatchOrchestratorStub,
        )
        steps = execute_cli_payload_command(
            Settings.from_env(Path(".")),
            command="run-steps",
            run_id="01RUN",
            orchestrator_cls=_DispatchOrchestratorStub,
        )
        subagent = execute_cli_payload_command(
            Settings.from_env(Path(".")),
            command="subagent-read",
            run_id="01RUN",
            query="01INV",
            orchestrator_cls=_DispatchOrchestratorStub,
        )
        resumed = execute_cli_payload_command(
            Settings.from_env(Path(".")),
            command="resume",
            run_id="01RUN",
            orchestrator_cls=_DispatchOrchestratorStub,
        )
        cancelled = execute_cli_payload_command(
            Settings.from_env(Path(".")),
            command="cancel",
            run_id="01RUN",
            orchestrator_cls=_DispatchOrchestratorStub,
        )

        self.assertEqual(replayed["status"], "completed")
        self.assertIn("subagent_trace", traced)
        self.assertIn("step_counts", steps)
        self.assertEqual(subagent["invocation_id"], "01INV")
        self.assertEqual(resumed["status"], "resumed")
        self.assertEqual(cancelled["status"], "cancelled")

    def test_execute_cli_payload_command_dispatches_status_commands(self) -> None:
        cleanup = execute_cli_payload_command(
            Settings.from_env(Path(".")),
            command="cleanup",
            orchestrator_cls=_DispatchOrchestratorStub,
        )
        reindex = execute_cli_payload_command(
            Settings.from_env(Path(".")),
            command="reindex-qdrant",
            orchestrator_cls=_DispatchOrchestratorStub,
        )
        checks = execute_cli_payload_command(
            Settings.from_env(Path(".")),
            command="check",
            run_checks_fn=lambda repo_root: {"status": "completed"},
        )
        status = execute_cli_payload_command(
            Settings.from_env(Path(".")),
            command="status",
            build_status_fn=lambda settings: {"status_counts": {}, "recent_runs": []},
        )
        doctor = execute_cli_payload_command(
            Settings.from_env(Path(".")),
            command="doctor",
            run_doctor_fn=lambda settings: {"status": "completed", "checks": []},
        )

        self.assertEqual(cleanup["message"], "cleanup done")
        self.assertEqual(reindex["message"], "reindex done")
        self.assertEqual(checks["status"], "completed")
        self.assertIn("recent_runs", status)
        self.assertEqual(doctor["status"], "completed")


if __name__ == "__main__":
    unittest.main()
