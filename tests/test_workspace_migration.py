from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.migrate_workspace import migrate_workspace
from research_system.runtime.storage.jsonl import read_json, write_json
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.turn_runtime import TaskSnapshot, TurnSnapshot


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


if __name__ == "__main__":
    unittest.main()
