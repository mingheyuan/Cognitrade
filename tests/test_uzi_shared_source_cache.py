from __future__ import annotations

import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock

import pandas as pd

from research_system.adapters.vendors.uzi_shared_source_cache import (
    UziSharedSourceCache,
)


class UziSharedSourceCacheTests(unittest.TestCase):
    def test_concurrent_identical_requests_call_real_source_once(self) -> None:
        with TemporaryDirectory() as tmp:
            calls = 0
            calls_lock = Lock()

            def source(*, date: str) -> pd.DataFrame:
                nonlocal calls
                with calls_lock:
                    calls += 1
                time.sleep(0.08)
                return pd.DataFrame([{"date": date, "value": 7.5}])

            cached = UziSharedSourceCache(Path(tmp)).wrap(
                "quarterly_universe",
                source,
                ttl_seconds=3600,
            )
            with ThreadPoolExecutor(max_workers=5) as pool:
                results = list(pool.map(lambda _: cached(date="20260630"), range(5)))

            self.assertEqual(calls, 1)
            self.assertTrue(all(frame.to_dict("records") == [{"date": "20260630", "value": 7.5}] for frame in results))
            results[0].loc[0, "value"] = 99
            self.assertEqual(float(results[1].loc[0, "value"]), 7.5)

    def test_new_manager_reuses_persisted_real_dataframe(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = UziSharedSourceCache(root).wrap(
                "industry_universe",
                lambda *, date: pd.DataFrame([{"date": date, "pe": 31.2}]),
                ttl_seconds=3600,
            )
            self.assertEqual(float(first(date="20260808").loc[0, "pe"]), 31.2)

            def must_not_run(*, date: str) -> pd.DataFrame:
                raise AssertionError(f"unexpected provider call for {date}")

            second = UziSharedSourceCache(root).wrap(
                "industry_universe",
                must_not_run,
                ttl_seconds=3600,
            )
            restored = second(date="20260808")
            self.assertEqual(restored.to_dict("records"), [{"date": "20260808", "pe": 31.2}])

    def test_provider_exception_is_not_cached(self) -> None:
        with TemporaryDirectory() as tmp:
            calls = 0

            def source(symbol: str) -> pd.DataFrame:
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise ConnectionError("temporary provider failure")
                return pd.DataFrame([{"symbol": symbol}])

            cached = UziSharedSourceCache(Path(tmp)).wrap(
                "real_source",
                source,
                ttl_seconds=3600,
            )
            with self.assertRaises(ConnectionError):
                cached("CU0")
            self.assertEqual(cached("CU0").to_dict("records"), [{"symbol": "CU0"}])
            self.assertEqual(calls, 2)


if __name__ == "__main__":
    unittest.main()
