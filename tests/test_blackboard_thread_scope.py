from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.runtime.core.blackboard import Blackboard
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


if __name__ == "__main__":
    unittest.main()
