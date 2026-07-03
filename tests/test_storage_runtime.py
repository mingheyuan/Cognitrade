from __future__ import annotations

import unittest

from research_system.runtime.checkpoint_store import CheckpointStore
from research_system.runtime.jsonl import append_jsonl, read_json, read_jsonl, write_json, write_json_array
from research_system.runtime.retention import RunRetentionService
from research_system.runtime.run_store import RunPaths, RunStore
from research_system.runtime.storage import runs as storage_runs_package
from research_system.runtime.storage.checkpoint_store import (
    CheckpointStore as StorageCheckpointStore,
)
from research_system.runtime.storage.jsonl import (
    append_jsonl as storage_append_jsonl,
    read_json as storage_read_json,
    read_jsonl as storage_read_jsonl,
    write_json as storage_write_json,
    write_json_array as storage_write_json_array,
)
from research_system.runtime.storage.retention import (
    RunRetentionService as StorageRunRetentionService,
)
from research_system.runtime.storage.run_store import (
    RunPaths as StorageRunPaths,
    RunStore as StorageRunStore,
)


class StorageRuntimeTests(unittest.TestCase):
    def test_top_level_storage_exports_remain_compatible_with_storage_package(self) -> None:
        self.assertIs(CheckpointStore, StorageCheckpointStore)
        self.assertIs(RunRetentionService, StorageRunRetentionService)
        self.assertIs(RunPaths, StorageRunPaths)
        self.assertIs(RunStore, StorageRunStore)
        self.assertIs(append_jsonl, storage_append_jsonl)
        self.assertIs(read_json, storage_read_json)
        self.assertIs(read_jsonl, storage_read_jsonl)
        self.assertIs(write_json, storage_write_json)
        self.assertIs(write_json_array, storage_write_json_array)

    def test_storage_runs_package_exports_remain_compatible(self) -> None:
        self.assertIs(storage_runs_package.CheckpointStore, CheckpointStore)
        self.assertIs(storage_runs_package.RunPaths, RunPaths)
        self.assertIs(storage_runs_package.RunRetentionService, RunRetentionService)
        self.assertIs(storage_runs_package.RunStore, RunStore)


if __name__ == "__main__":
    unittest.main()
