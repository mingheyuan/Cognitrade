from __future__ import annotations

import unittest

from research_system.runtime.storage.jsonl import append_jsonl, read_json, read_jsonl, write_json, write_json_array
from research_system.runtime.storage import runs as storage_runs_package
from research_system.runtime.storage.runs.checkpoint_store import (
    CheckpointStore as StorageCheckpointStore,
)
from research_system.runtime.storage.jsonl import (
    append_jsonl as storage_append_jsonl,
    read_json as storage_read_json,
    read_jsonl as storage_read_jsonl,
    write_json as storage_write_json,
    write_json_array as storage_write_json_array,
)
from research_system.runtime.storage.runs.retention import (
    RunRetentionService as StorageRunRetentionService,
)
from research_system.runtime.storage.runs.run_store import (
    RunPaths as StorageRunPaths,
    RunStore as StorageRunStore,
)


class StorageRuntimeTests(unittest.TestCase):
    def test_top_level_storage_exports_remain_compatible_with_storage_package(self) -> None:
        self.assertIs(append_jsonl, storage_append_jsonl)
        self.assertIs(read_json, storage_read_json)
        self.assertIs(read_jsonl, storage_read_jsonl)
        self.assertIs(write_json, storage_write_json)
        self.assertIs(write_json_array, storage_write_json_array)
        self.assertTrue(StorageCheckpointStore)
        self.assertTrue(StorageRunRetentionService)
        self.assertTrue(StorageRunPaths)
        self.assertTrue(StorageRunStore)

    def test_storage_runs_package_exports_remain_compatible(self) -> None:
        self.assertIs(storage_runs_package.CheckpointStore, StorageCheckpointStore)
        self.assertIs(storage_runs_package.RunPaths, StorageRunPaths)
        self.assertIs(storage_runs_package.RunRetentionService, StorageRunRetentionService)
        self.assertIs(storage_runs_package.RunStore, StorageRunStore)


if __name__ == "__main__":
    unittest.main()
