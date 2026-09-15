from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from research_system.runtime.storage.jsonl import read_json, write_json
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.turn_runtime import (
    ChildTurnSnapshot,
    TaskSnapshot,
    TurnSnapshot,
)
from scripts.migrate_workspace import migrate_workspace


class WorkspaceMigrationTests(unittest.TestCase):
    def test_aliases_are_migrated_and_removed(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            root = workspace / "runs" / "run_test"
            root.mkdir(parents=True)
            request = RequestEnvelope(
                request_id="request_test",
                thread_id="thread_test",
                user_query="test",
            )
            write_json(root / "request.json", request.model_dump(mode="json"))
            turn = TurnSnapshot(
                run_id="run_test",
                thread_id="thread_test",
                request_id="request_test",
            )
            task = TaskSnapshot(active_turn_id=turn.turn_id)
            write_json(root / "turn.json", turn.model_dump(mode="json"))
            write_json(root / "task.json", task.model_dump(mode="json"))

            report = migrate_workspace(workspace)

            self.assertEqual(report["created_turn_states"], 1)
            self.assertEqual(report["created_task_states"], 1)
            self.assertEqual(report["removed_aliases"], 2)
            self.assertFalse((root / "turn.json").exists())
            self.assertFalse((root / "task.json").exists())
            TurnSnapshot.model_validate(read_json(root / "turn_state.json"))
            TaskSnapshot.model_validate(read_json(root / "task_state.json"))

    def test_pre_turn_run_is_synthesized_without_losing_request(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            root = workspace / "runs" / "run_old"
            root.mkdir(parents=True)
            request = RequestEnvelope(
                request_id="request_old",
                thread_id="thread_old",
                user_query="hello",
            )
            write_json(root / "request.json", request.model_dump(mode="json"))
            write_json(
                root / "main_agent_state.json",
                {
                    "run_id": "run_old",
                    "thread_id": "thread_old",
                    "request_id": "request_old",
                    "status": "completed",
                    "mode": "core",
                    "intent": "core.conversation.answer",
                    "request_kind": "conversation",
                    "assistant_message": "hello",
                },
            )

            migrate_workspace(workspace)

            turn = TurnSnapshot.model_validate(read_json(root / "turn_state.json"))
            task = TaskSnapshot.model_validate(read_json(root / "task_state.json"))
            self.assertEqual(turn.status, "completed")
            self.assertEqual(turn.execution_mode, "core")
            self.assertEqual(task.last_assistant_message, "hello")
            self.assertEqual(read_json(root / "request.json")["request_id"], "request_old")

    def test_duplicate_task_usage_is_normalized_to_child_turn_audit_state(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            root = workspace / "runs" / "run_with_usage"
            root.mkdir(parents=True)
            usage = {
                "tokens": 17,
                "cost": 0.125,
                "duration_seconds": 3.5,
            }
            task = TaskSnapshot(
                execution_mode="graph",
                active_turn_id="turn_with_usage",
            )
            legacy_task = task.model_dump(mode="json")
            legacy_task["usage"] = usage
            write_json(root / "task_state.json", legacy_task)
            child_turn = ChildTurnSnapshot(
                child_turn_id="turn_with_usage",
                child_thread_id="thread_with_usage",
                parent_turn_id="parent_turn",
                parent_run_id="run_with_usage",
                role_id="test_role",
                usage=usage,
            )
            write_json(root / "child_turn.json", child_turn.model_dump(mode="json"))

            dry_run_report = migrate_workspace(workspace, dry_run=True)

            self.assertEqual(dry_run_report["normalized_task_usage"], 1)
            self.assertEqual(read_json(root / "task_state.json")["usage"], usage)

            report = migrate_workspace(workspace)

            self.assertEqual(report["normalized_task_usage"], 1)
            persisted_task = read_json(root / "task_state.json")
            self.assertNotIn("usage", persisted_task)
            TaskSnapshot.model_validate(persisted_task)
            persisted_child = ChildTurnSnapshot.model_validate(
                read_json(root / "child_turn.json")
            )
            self.assertEqual(persisted_child.usage, usage)

    def test_mismatched_task_usage_is_not_discarded(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            root = workspace / "runs" / "run_with_mismatched_usage"
            root.mkdir(parents=True)
            task = TaskSnapshot(execution_mode="graph").model_dump(mode="json")
            task["usage"] = {"tokens": 17}
            write_json(root / "task_state.json", task)
            write_json(root / "child_turn.json", {"usage": {"tokens": 18}})

            with self.assertRaisesRegex(
                ValueError,
                "child_turn.json is missing an identical audit copy",
            ):
                migrate_workspace(workspace)

            self.assertEqual(
                read_json(root / "task_state.json")["usage"],
                {"tokens": 17},
            )


if __name__ == "__main__":
    unittest.main()
