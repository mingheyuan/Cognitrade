from __future__ import annotations

import unittest
from threading import Event

from research_system.services.memory.maintenance import MemoryExpirationScheduler


class _MemoryStore:
    def __init__(self) -> None:
        self.calls = 0
        self.called = Event()

    def expire_records(self):
        self.calls += 1
        self.called.set()
        return ["expired"]


class MemoryMaintenanceTests(unittest.TestCase):
    def test_run_once_delegates_to_the_audited_memory_store(self) -> None:
        store = _MemoryStore()
        scheduler = MemoryExpirationScheduler(store, interval_seconds=60)

        self.assertEqual(scheduler.run_once(), 1)
        self.assertEqual(store.calls, 1)

    def test_scheduler_can_start_and_stop_without_owning_memory_state(self) -> None:
        store = _MemoryStore()
        scheduler = MemoryExpirationScheduler(store, interval_seconds=0.01)

        scheduler.start()
        self.assertTrue(store.called.wait(0.5))
        scheduler.stop()
        calls_after_stop = store.calls
        self.assertGreaterEqual(calls_after_stop, 1)

        scheduler.stop()
        self.assertEqual(store.calls, calls_after_stop)

    def test_interval_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            MemoryExpirationScheduler(_MemoryStore(), interval_seconds=0)


if __name__ == "__main__":
    unittest.main()
