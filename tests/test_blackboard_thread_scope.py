from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.runtime.core.blackboard import Blackboard
from research_system.runtime.orchestration.orchestrator_context import build_runtime_context
from research_system.runtime.storage.runs.run_store import RunStore
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.runtime import BlackboardEntry


class BlackboardThreadScopeTests(unittest.TestCase):
    def test_bound_thread_filters_reads_and_backfills_missing_thread_id_on_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "blackboard.jsonl"
            blackboard = Blackboard(path)
            blackboard.bind_thread("thread-A")

            blackboard.add(
                BlackboardEntry(
                    run_id="01RUN",
                    thread_id="",
                    request_id="req-1",
                    kind="source_record",
                    agent="collector",
                    phase="collect",
                    payload={"symbol": "000001"},
                )
            )
            blackboard.add(
                BlackboardEntry(
                    run_id="01RUN",
                    thread_id="thread-B",
                    request_id="req-1",
                    kind="source_record",
                    agent="collector",
                    phase="collect",
                    payload={"symbol": "600519"},
                )
            )

            default_rows = blackboard.list_entries()
            explicit_a_rows = blackboard.list_entries(thread_id="thread-A")
            explicit_b_rows = blackboard.list_entries(thread_id="thread-B")

            self.assertEqual(len(default_rows), 1)
            self.assertEqual(default_rows[0].thread_id, "thread-A")
            self.assertEqual(default_rows[0].payload["symbol"], "000001")

            self.assertEqual(len(explicit_a_rows), 1)
            self.assertEqual(explicit_a_rows[0].thread_id, "thread-A")

            self.assertEqual(len(explicit_b_rows), 1)
            self.assertEqual(explicit_b_rows[0].thread_id, "thread-B")
            self.assertEqual(explicit_b_rows[0].payload["symbol"], "600519")

    def test_runtime_context_blackboards_are_isolated_per_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_store = RunStore(Path(temp_dir) / "runs")

            request_a = RequestEnvelope(user_query="run a")
            run_id_a, paths_a, snapshot_a = run_store.create_run(request_a, "stock_check")
            context_a = build_runtime_context(
                run_id=run_id_a,
                paths=paths_a,
                state=snapshot_a,
                run_timeout_seconds=60,
            )

            request_b = RequestEnvelope(user_query="run b")
            run_id_b, paths_b, snapshot_b = run_store.create_run(request_b, "stock_check")
            context_b = build_runtime_context(
                run_id=run_id_b,
                paths=paths_b,
                state=snapshot_b,
                run_timeout_seconds=60,
            )

            context_a.blackboard.add(
                BlackboardEntry(
                    run_id=run_id_a,
                    thread_id="",
                    request_id=request_a.request_id,
                    kind="verified_snapshot",
                    agent="collector",
                    phase="collect",
                    payload={"symbol": "000001"},
                )
            )
            context_b.blackboard.add(
                BlackboardEntry(
                    run_id=run_id_b,
                    thread_id="",
                    request_id=request_b.request_id,
                    kind="verified_snapshot",
                    agent="collector",
                    phase="collect",
                    payload={"symbol": "600519"},
                )
            )

            rows_a = context_a.blackboard.list_entries()
            rows_b = context_b.blackboard.list_entries()

            self.assertEqual(len(rows_a), 1)
            self.assertEqual(len(rows_b), 1)
            self.assertEqual(rows_a[0].run_id, run_id_a)
            self.assertEqual(rows_b[0].run_id, run_id_b)
            self.assertEqual(rows_a[0].payload["symbol"], "000001")
            self.assertEqual(rows_b[0].payload["symbol"], "600519")
            self.assertNotEqual(context_a.paths.blackboard, context_b.paths.blackboard)


if __name__ == "__main__":
    unittest.main()
