from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from research_system.runtime.core.control_plane import RunControlPlane
from research_system.runtime.orchestration.orchestrator_run_admin_support import (
    OrchestratorRunAdminSupport,
)
from research_system.runtime.storage.jsonl import write_json
from research_system.runtime.storage.runs.checkpoint_store import CheckpointStore
from research_system.runtime.storage.runs.run_store import RunPaths
from research_system.schemas.runtime import RunStateSnapshot


class _RunStoreStub:
    def __init__(self, root: Path) -> None:
        self.root = root

    def load_run_paths(self, run_id: str) -> RunPaths:
        path = self.root / run_id
        if not path.exists():
            raise FileNotFoundError(run_id)
        return RunPaths(path)


class _HostStub:
    def __init__(self, runs_root: Path) -> None:
        self.run_store = _RunStoreStub(runs_root)

    @staticmethod
    def load_json_payload(path: Path):
        from research_system.runtime.storage.jsonl import read_json

        return read_json(path)


class OrchestratorRunAdminSupportTests(unittest.TestCase):
    def test_resume_includes_control_state_and_latest_runtime_control_stage(self) -> None:
        with TemporaryDirectory() as tmpdir:
            runs_root = Path(tmpdir)
            run_root = runs_root / "01RESUME"
            run_root.mkdir(parents=True)
            paths = RunPaths(run_root)
            write_json(
                paths.run_state,
                RunStateSnapshot(
                    run_id="01RESUME",
                    request_id="req-resume",
                    intent="review_thesis",
                    status="failed",
                    current_phase="review",
                    active_agent="reviewer",
                    pending_nodes=["review"],
                    completed_nodes=["collect", "retrieve"],
                ).model_dump(mode="json"),
            )
            CheckpointStore(paths.checkpoint).save(
                RunStateSnapshot(
                    run_id="01RESUME",
                    request_id="req-resume",
                    intent="review_thesis",
                    status="failed",
                    current_phase="review",
                    active_agent="reviewer",
                    pending_nodes=["review"],
                    completed_nodes=["collect", "retrieve"],
                )
            )
            control_plane = RunControlPlane(paths.run_control)
            control_plane.initialize(run_id="01RESUME", run_timeout_seconds=30)
            control_plane.record_runtime_control_stage_decision(
                row={
                    "event": "runtime_control_subagent_transition_decided",
                    "transition_source": "capability_gap_escalation",
                    "transition_type": "request_action_replan",
                }
            )

            payload = OrchestratorRunAdminSupport(_HostStub(runs_root)).resume("01RESUME")

        self.assertTrue(payload["resumable"])
        self.assertEqual(payload["runtime_control_stage_count"], 1)
        self.assertEqual(
            payload["latest_runtime_control_stage_decision"],
            {
                "event": "runtime_control_subagent_transition_decided",
                "transition_source": "capability_gap_escalation",
                "transition_type": "request_action_replan",
            },
        )
        self.assertEqual(
            payload["control_state"]["runtime_control_stage_decisions"],
            [
                {
                    "event": "runtime_control_subagent_transition_decided",
                    "transition_source": "capability_gap_escalation",
                    "transition_type": "request_action_replan",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
