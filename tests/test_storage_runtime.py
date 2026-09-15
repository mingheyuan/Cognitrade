from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from research_system.runtime.event_store import append_canonical_event
from research_system.runtime.storage.jsonl import append_jsonl, read_jsonl


class StorageRuntimeTests(unittest.TestCase):
    def test_canonical_event_sequence_re_reads_events_written_by_another_writer(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "execution_events.jsonl"
            first = append_canonical_event(path, {"event_type": "first"})
            self.assertEqual(first["sequence"], 1)

            append_jsonl(path, {"event_type": "external", "sequence": 17})

            next_event = append_canonical_event(path, {"event_type": "next"})
            self.assertEqual(next_event["sequence"], 18)
            self.assertEqual([row["sequence"] for row in read_jsonl(path)], [1, 17, 18])


if __name__ == "__main__":
    unittest.main()
