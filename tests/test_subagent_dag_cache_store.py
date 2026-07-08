from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_system.runtime.storage.runs.subagent_dag_cache_store import SubagentDagCacheStore


class SubagentDagCacheStoreTests(unittest.TestCase):
    def test_save_snapshot_handles_long_dynamic_dag_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir) / "workspace" / "runs" / "01RUN"
            run_root.mkdir(parents=True, exist_ok=True)
            store = SubagentDagCacheStore(run_root=run_root)

            snapshot_path = store.save_snapshot(
                dag_id="collect_replenish_sector_policy_signals_regulatory_signals_benchmark_relative_strength:single_node:query_normalizer",
                node_id="review_sector_snapshot_collector",
                cache_scope="run",
                cache_key="review_sector_snapshot_collector",
                run_id="01RUN",
                thread_id="01THREAD",
                request_id="req-1",
                status="completed",
                attempts=1,
                outputs={"symbol": None},
                record_payload={"status": "completed"},
                metadata={"phase": "collect"},
            )

            self.assertIsNotNone(snapshot_path)
            assert snapshot_path is not None
            self.assertTrue(snapshot_path.exists())
            self.assertLess(len(str(snapshot_path)), 260)


if __name__ == "__main__":
    unittest.main()
