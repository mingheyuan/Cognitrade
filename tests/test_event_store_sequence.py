from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from research_system.runtime.event_store import append_canonical_event
from research_system.runtime.storage.jsonl import append_jsonl, read_jsonl


class EventStoreSequenceTests(unittest.TestCase):
    def test_external_writer_and_sidecar_keep_sequence_monotonic(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "execution_events.jsonl"
            self.assertEqual(
                append_canonical_event(path, {"event_type": "first"})["sequence"],
                1,
            )
            append_jsonl(path, {"event_type": "external", "sequence": 17})

            self.assertEqual(
                append_canonical_event(path, {"event_type": "next"})["sequence"],
                18,
            )

    def test_concurrent_appends_allocate_unique_sequences(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "execution_events.jsonl"
            with ThreadPoolExecutor(max_workers=8) as pool:
                rows = list(
                    pool.map(
                        lambda index: append_canonical_event(
                            path,
                            {"event_type": "row", "index": index},
                        ),
                        range(64),
                    )
                )

            self.assertEqual(sorted(row["sequence"] for row in rows), list(range(1, 65)))
            self.assertEqual(
                [row["sequence"] for row in read_jsonl(path)],
                list(range(1, 65)),
            )


if __name__ == "__main__":
    unittest.main()
