from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Lock
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from domain_packs.finance_ashare.subagents.uzi_methodology import select_profile
from domain_packs.finance_ashare.subagents import uzi_392_analysis
from domain_packs.finance_ashare.tools import snapshot_collector
from domain_packs.finance_ashare.vendor.uzi_3_9_2 import Uzi392FetcherRuntime
from research_system.adapters.vendors import uzi_request_timeout


class UziSnapshotRuntimeParityTests(unittest.TestCase):
    def test_uzi_resume_reuses_numbered_dimensions_but_refreshes_basic_and_bonus(self) -> None:
        calls: dict[str, int] = {}

        def collect(_runtime, fetcher_id: str, **kwargs):
            calls[fetcher_id] = calls.get(fetcher_id, 0) + 1
            data = {
                "0_basic": {"industry": "白酒", "price": 1400.0},
                "1_financials": {"roe": 30.0},
                "6_fund_holders": {"top_holders": [{"name": "测试基金"}]},
            }[fetcher_id]
            return {
                "ticker": "600519.SH",
                "market": "A",
                "data": data,
                "source": f"fixture:{fetcher_id}",
                "fallback": False,
            }

        with TemporaryDirectory() as temporary, patch.object(
            Uzi392FetcherRuntime,
            "collect",
            autospec=True,
            side_effect=collect,
        ):
            for profile in ("lite", "medium"):
                rows = snapshot_collector.collect_snapshots(
                    query="resume",
                    symbols=["600519"],
                    snapshot_types=["market", "fundamental", "fund_holders"],
                    research_profile=profile,
                    cache_root=Path(temporary),
                )

        self.assertEqual(len(rows), 3)
        self.assertEqual(
            calls,
            {"0_basic": 2, "1_financials": 1, "6_fund_holders": 2},
        )

    def test_uzi_http_guard_does_not_change_peer_request_timeouts(self) -> None:
        import requests

        observed: list[dict] = []

        def fake_request(_session, _method, _url, **kwargs):
            observed.append(dict(kwargs))
            return object()

        original_submit = ThreadPoolExecutor.submit
        with (
            patch.object(requests.sessions.Session, "request", fake_request),
            patch.object(ThreadPoolExecutor, "submit", original_submit),
            patch.object(uzi_request_timeout, "_INSTALLED", False),
            patch.dict(os.environ, {"UZI_HTTP_TIMEOUT": "23"}),
        ):
            uzi_request_timeout._install_scoped_guard()
            guarded_request = requests.sessions.Session.request
            guarded_request(object(), "GET", "https://peer.test")
            with uzi_request_timeout.uzi_request_timeout_scope():
                guarded_request(object(), "GET", "https://uzi.test")
                with ThreadPoolExecutor(max_workers=1) as executor:
                    executor.submit(
                        guarded_request,
                        object(),
                        "GET",
                        "https://uzi-child.test",
                    ).result()
            with uzi_request_timeout.uzi_request_timeout_scope(timeout_seconds=5):
                guarded_request(object(), "GET", "https://uzi-short.test")
            with patch.object(
                uzi_request_timeout,
                "monotonic",
                side_effect=(100.0, 131.0),
            ):
                with uzi_request_timeout.uzi_request_timeout_scope(
                    timeout_seconds=5,
                    total_timeout_seconds=30,
                ):
                    with self.assertRaisesRegex(
                        requests.Timeout,
                        "scope deadline exceeded",
                    ):
                        guarded_request(object(), "GET", "https://uzi-expired.test")
            guarded_request(object(), "GET", "https://peer.test/after")

        self.assertEqual(
            observed,
            [{}, {"timeout": 23.0}, {"timeout": 23.0}, {"timeout": 5.0}, {}],
        )

    def test_basic_canonical_ticker_drives_fetchers_but_not_artifact_entity(self) -> None:
        calls: list[tuple[str, str]] = []

        def collect(_runtime, fetcher_id: str, **kwargs):
            calls.append((fetcher_id, kwargs["ticker"]))
            return {
                "ticker": "600519.SH",
                "market": "A",
                "data": {"industry": "白酒"} if fetcher_id == "0_basic" else {},
                "source": f"fixture:{fetcher_id}",
                "fallback": False,
            }

        with patch.object(
            Uzi392FetcherRuntime,
            "collect",
            autospec=True,
            side_effect=collect,
        ):
            rows = snapshot_collector.collect_snapshots(
                query="分析贵州茅台",
                symbols=["贵州茅台"],
                snapshot_types=["market", "fundamental"],
                research_profile="lite",
                max_workers=1,
            )

        self.assertEqual(calls, [("0_basic", "贵州茅台"), ("1_financials", "600519.SH")])
        self.assertEqual({row["symbol"] for row in rows}, {"贵州茅台"})
        fundamental = next(row for row in rows if row["snapshot_type"] == "fundamental")
        self.assertEqual(
            fundamental["facts"]["_uzi_raw_result"]["ticker"],
            "600519.SH",
        )

    def test_explicit_symbols_receive_independent_native_wave_windows(self) -> None:
        progress: list[dict] = []

        def collect(_runtime, _fetcher_id: str, **kwargs):
            time.sleep(0.04)
            return {
                "ticker": kwargs["ticker"],
                "data": {"roe": 12.0},
                "source": "fixture:fundamental",
                "fallback": False,
            }

        with (
            patch.object(
                Uzi392FetcherRuntime,
                "collect",
                autospec=True,
                side_effect=collect,
            ),
            patch.object(snapshot_collector, "_UZI_FETCHER_WAVE_TIMEOUT_SECONDS", 0.06),
        ):
            rows = snapshot_collector.collect_snapshots(
                query="组合分析",
                symbols=["600519", "000858"],
                snapshot_types=["fundamental"],
                research_profile="lite",
                max_workers=1,
                progress_callback=progress.append,
            )

        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["quality_status"] == "available" for row in rows))
        completed = [int(row["completed"]) for row in progress]
        self.assertEqual(completed, sorted(completed))
        self.assertEqual({int(row["total"]) for row in progress}, {2})

    def test_sector_policy_exception_is_dimension_local(self) -> None:
        with patch.object(
            Uzi392FetcherRuntime,
            "collect",
            autospec=True,
            side_effect=RuntimeError("fixture policy crash"),
        ):
            rows = snapshot_collector.collect_snapshots(
                query="白酒政策",
                symbols=[],
                snapshot_types=["policy"],
                sector="白酒",
                research_profile="medium",
            )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["snapshot_type"], "policy")
        self.assertEqual(rows[0]["quality_status"], "data_gap")
        self.assertNotIn("fixture policy crash", str(rows[0]))

    def test_each_collection_resets_the_pinned_web_search_budget(self) -> None:
        Uzi392FetcherRuntime(workspace_root=Path(__file__).resolve().parents[1] / "workspace")
        web_search = __import__("lib.web_search", fromlist=["get_budget_state"])
        with web_search._BUDGET_LOCK:
            web_search._BUDGET_STATE.update({"used": 99, "skipped": 7})
        observed: list[dict] = []

        def collect(_runtime, _fetcher_id: str, **kwargs):
            observed.append(web_search.get_budget_state())
            return {
                "ticker": kwargs["ticker"],
                "data": {},
                "source": "fixture:industry",
                "fallback": False,
            }

        with patch.object(
            Uzi392FetcherRuntime,
            "collect",
            autospec=True,
            side_effect=collect,
        ):
            snapshot_collector.collect_snapshots(
                query="行业",
                symbols=["600519"],
                snapshot_types=["industry"],
                research_profile="medium",
            )

        self.assertEqual(observed, [{"used": 0, "skipped": 0}])

    def test_snapshot_and_analysis_share_one_complete_profile_environment_lock(self) -> None:
        self.assertIs(
            snapshot_collector.UZI_ENVIRONMENT_LOCK,
            uzi_392_analysis.UZI_ENVIRONMENT_LOCK,
        )
        outer = {
            "UZI_DEPTH": "lite",
            "UZI_LITE": "1",
            "UZI_DDG_BUDGET": "7",
            "UZI_FUND_STATS_TOP": "9",
            "UZI_SCHOOL": "A",
        }
        with patch.dict(os.environ, outer, clear=False):
            with uzi_392_analysis._uzi_native_environment(
                profile="medium",
                school="I",
            ):
                self.assertEqual(os.environ.get("UZI_DEPTH"), "medium")
                self.assertEqual(os.environ.get("UZI_LITE"), "0")
                self.assertEqual(os.environ.get("UZI_DDG_BUDGET"), "30")
                self.assertEqual(os.environ.get("UZI_FUND_STATS_TOP"), "20")
                self.assertEqual(os.environ.get("UZI_SCHOOL"), "I")
            self.assertEqual(
                {key: os.environ.get(key) for key in outer},
                outer,
            )

    def test_every_analyze_profile_collects_the_full_v3_registry(self) -> None:
        expected_counts = {"lite": 22, "medium": 22, "deep": 22}
        for profile_name, expected_count in expected_counts.items():
            with self.subTest(profile=profile_name):
                snapshot_types = select_profile(
                    {"research_profile": profile_name}
                )["snapshot_types"]
                self.assertEqual(len(snapshot_types), expected_count)
                self.assertEqual(
                    snapshot_types[-2:],
                    ["fund_holders", "similar_stocks"],
                )

    def test_each_of_22_fetcher_exceptions_degrades_only_its_dimension(self) -> None:
        snapshot_types = list(snapshot_collector._UZI_FETCHER_BY_SNAPSHOT_TYPE)
        snapshot_type_by_fetcher = {
            fetcher_id: snapshot_type
            for snapshot_type, fetcher_id in snapshot_collector._UZI_FETCHER_BY_SNAPSHOT_TYPE.items()
        }

        for failing_fetcher in snapshot_type_by_fetcher:
            with self.subTest(fetcher=failing_fetcher):
                def collect(_runtime, fetcher_id: str, **_kwargs):
                    if fetcher_id == failing_fetcher:
                        raise RuntimeError(f"fixture crash: {fetcher_id}")
                    data = {"observed": fetcher_id}
                    if fetcher_id == "0_basic":
                        data["industry"] = "半导体"
                    if fetcher_id == "8_materials":
                        data["materials_detail"] = [
                            {"name": "硅", "futures_code": "SI0"}
                        ]
                    return {
                        "ticker": "688041.SH",
                        "data": data,
                        "source": f"fixture:{fetcher_id}",
                        "fallback": False,
                    }

                with patch.object(
                    snapshot_collector,
                    "_UZI_MINI_RACER_FETCHERS",
                    frozenset({"7_industry", "10_valuation"}),
                ), patch.object(
                    Uzi392FetcherRuntime,
                    "collect",
                    autospec=True,
                    side_effect=collect,
                ):
                    rows = snapshot_collector.collect_snapshots(
                        query="完整分析 688041",
                        symbols=["688041"],
                        snapshot_types=snapshot_types,
                        research_profile="medium",
                        max_workers=6,
                    )

                self.assertEqual(len(rows), 22)
                by_type = {row["snapshot_type"]: row for row in rows}
                failed = by_type[snapshot_type_by_fetcher[failing_fetcher]]
                self.assertEqual(failed["quality_status"], "data_gap")
                self.assertTrue(failed["degraded"])
                self.assertTrue(failed["fallback_applied"])
                self.assertTrue(
                    failed["facts"]["_uzi_raw_result"]["fallback"]
                )
                self.assertEqual(
                    failed["degrade_reason"],
                    "RuntimeError: provider_unavailable",
                )
                self.assertNotIn("fixture crash", str(failed))
                self.assertEqual(
                    sum(row.get("quality_status") == "data_gap" for row in rows),
                    1,
                )

    def test_futures_receives_materials_result_in_wave2(self) -> None:
        observed_dependencies: list[dict] = []

        def collect(_runtime, fetcher_id: str, **kwargs):
            if fetcher_id == "9_futures":
                observed_dependencies.append(dict(kwargs.get("dependencies") or {}))
            data = (
                {"materials_detail": [{"name": "铜", "futures_code": "CU0"}]}
                if fetcher_id == "8_materials"
                else {"observed": fetcher_id}
            )
            return {
                "ticker": "600000.SH",
                "data": data,
                "source": f"fixture:{fetcher_id}",
                "fallback": False,
            }

        with patch.object(
            Uzi392FetcherRuntime,
            "collect",
            autospec=True,
            side_effect=collect,
        ):
            snapshot_collector.collect_snapshots(
                query="期货分析",
                symbols=["600000"],
                snapshot_types=["materials", "futures"],
                research_profile="medium",
                max_workers=1,
            )

        self.assertEqual(len(observed_dependencies), 1)
        self.assertEqual(
            observed_dependencies[0]["8_materials"]["data"]["materials_detail"][0][
                "futures_code"
            ],
            "CU0",
        )

    def test_concurrent_profiles_apply_and_restore_native_environment_atomically(self) -> None:
        observed: dict[str, tuple[str | None, ...]] = {}
        observed_lock = Lock()
        start = Barrier(2)

        def collect(_runtime, _fetcher_id: str, **kwargs):
            values = tuple(
                os.environ.get(key)
                for key in snapshot_collector._UZI_PROFILE_ENV_KEYS
            )
            with observed_lock:
                observed[kwargs["ticker"]] = values
            return {
                "ticker": kwargs["ticker"],
                "data": {"industry": "半导体"},
                "source": "fixture:industry",
                "fallback": False,
            }

        def run(profile: str, ticker: str) -> None:
            start.wait()
            snapshot_collector.collect_snapshots(
                query=f"{profile} {ticker}",
                symbols=[ticker],
                snapshot_types=["industry"],
                research_profile=profile,
                max_workers=1,
            )

        outer = {
            "UZI_DEPTH": "medium",
            "UZI_LITE": "0",
            "UZI_DDG_BUDGET": "17",
            "UZI_FUND_STATS_TOP": "33",
        }
        with (
            patch.dict(os.environ, outer, clear=False),
            patch.object(
                Uzi392FetcherRuntime,
                "collect",
                autospec=True,
                side_effect=collect,
            ),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            futures = [
                executor.submit(run, "lite", "600001"),
                executor.submit(run, "deep", "600002"),
            ]
            for future in futures:
                future.result()
            self.assertEqual(
                {key: os.environ.get(key) for key in outer},
                outer,
            )

        self.assertEqual(observed["600001"], ("lite", "1", "0", "5"))
        self.assertEqual(observed["600002"], ("deep", "0", "60", "100"))


if __name__ == "__main__":
    unittest.main()
