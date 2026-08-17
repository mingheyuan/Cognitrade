from __future__ import annotations

import os
import operator
from pathlib import Path
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from domain_packs.finance_ashare.vendor.uzi_3_9_2 import (
    UZI_COMMAND_IDS,
    UZI_FETCHER_IDS,
    UZI_REPORT_SOURCE_COMMIT,
    UZI_REPORT_VERSION,
    Uzi392FetcherRuntime,
    verify_vendored_uzi_integrity,
)
from domain_packs.finance_ashare.vendor.uzi_3_9_2 import runtime as uzi_runtime
from domain_packs.finance_ashare.tools import snapshot_collector


class Uzi392VendorRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = Uzi392FetcherRuntime(
            workspace_root=Path(__file__).resolve().parents[1] / "workspace"
        )

    def test_pinned_upstream_capability_tree_is_complete_and_unmodified(self) -> None:
        report = verify_vendored_uzi_integrity()
        self.assertTrue(report["valid"], report)
        self.assertEqual(report["fetcher_count"], 22)
        self.assertEqual(report["python_file_count"], 135)
        self.assertEqual(report["command_count"], 20)
        self.assertTrue(report["python_tree_matches"])
        self.assertTrue(report["command_tree_matches"])
        self.assertTrue(report["report_template_matches"])
        self.assertTrue(report["report_renderer_matches"])
        self.assertEqual(report["report_source_commit"], UZI_REPORT_SOURCE_COMMIT)
        self.assertEqual(report["report_version"], UZI_REPORT_VERSION)
        self.assertEqual(report["analyst_asset_file_count"], 138)
        self.assertTrue(report["analyst_asset_tree_matches"])
        self.assertEqual(report["standalone_skill_file_count"], 17)
        self.assertTrue(report["standalone_skill_tree_matches"])
        self.assertIn("quick-scan", UZI_COMMAND_IDS)
        self.assertEqual(len(UZI_FETCHER_IDS), 22)

    def test_all_referenced_standalone_skill_resources_are_vendored(self) -> None:
        root = (
            Path(__file__).resolve().parents[1]
            / "domain_packs"
            / "finance_ashare"
            / "vendor"
            / "uzi_3_9_2"
        )
        required = {
            "skills/investor-panel/SKILL.md",
            "skills/investor-panel/assets/investor-cards.json",
            "skills/investor-panel/references/group-a-classic-value.md",
            "skills/investor-panel/references/group-b-growth.md",
            "skills/investor-panel/references/group-c-macro-hedge.md",
            "skills/investor-panel/references/group-d-technical.md",
            "skills/investor-panel/references/group-e-china-value.md",
            "skills/investor-panel/references/group-f-china-youzi.md",
            "skills/investor-panel/references/group-g-quant.md",
            "skills/investor-panel/references/group-i-serenity.md",
            "skills/investor-panel/references/quotes-knowledge-base.md",
            "skills/investor-panel/references/serenity-voice.md",
            "skills/lhb-analyzer/SKILL.md",
            "skills/lhb-analyzer/assets/seats-2026.json",
            "skills/lhb-analyzer/references/seat-encyclopedia.md",
            "skills/trap-detector/SKILL.md",
            "skills/trap-detector/references/eight-signals.md",
        }
        actual = {
            path.relative_to(root).as_posix()
            for path in (root / "skills").rglob("*")
            if path.is_file()
        }
        self.assertEqual(actual, required)

    def test_fetcher_result_is_returned_without_shape_adaptation(self) -> None:
        upstream_result = {
            "ticker": "600519.SH",
            "market": "A",
            "data": {"price": 1418.2, "industry": "白酒"},
            "source": "akshare:A",
            "fallback": False,
        }
        with patch("fetch_basic.main", return_value=upstream_result) as main:
            result = self.runtime.collect("0_basic", ticker="600519")
        self.assertIs(result, upstream_result)
        main.assert_called_once_with("600519")

    def test_all_22_fetcher_dispatches_preserve_upstream_results_and_call_contracts(self) -> None:
        dependencies = {
            "8_materials": {"data": {"materials_detail": [{"name": "铜", "code": "CU0"}]}}
        }
        for fetcher_id in UZI_FETCHER_IDS:
            with self.subTest(fetcher_id=fetcher_id):
                upstream = {
                    "ticker": "600000",
                    "data": {"fetcher_id": fetcher_id, "nested": [None, 1, {"ok": True}]},
                    "source": f"fixture:{fetcher_id}",
                    "fallback": False,
                }
                main = Mock(return_value=upstream)
                with patch(
                    "domain_packs.finance_ashare.vendor.uzi_3_9_2.runtime.importlib.import_module",
                    return_value=SimpleNamespace(main=main),
                ):
                    result = self.runtime.collect(
                        fetcher_id,
                        ticker="600000",
                        industry="银行",
                        dependencies=dependencies,
                        options={"limit": 7, "top_n": 6},
                    )

                self.assertIs(result, upstream)
                if fetcher_id in {"3_macro", "7_industry", "13_policy"}:
                    main.assert_called_once_with("银行")
                elif fetcher_id == "9_futures":
                    main.assert_called_once_with("银行", dependencies["8_materials"]["data"]["materials_detail"])
                elif fetcher_id == "6_fund_holders":
                    main.assert_called_once_with("600000", limit=7)
                elif fetcher_id == "similar_stocks":
                    main.assert_called_once_with("600000", top_n=6)
                else:
                    main.assert_called_once_with("600000")

    def test_futures_receives_upstream_materials_dependency(self) -> None:
        dependencies = {
            "8_materials": {"data": {"materials_detail": [{"name": "铜", "futures_code": "CU0"}]}}
        }
        with patch("fetch_futures.main", return_value={"data": {}}) as main:
            self.runtime.collect(
                "9_futures",
                ticker="600519",
                industry="白酒",
                dependencies=dependencies,
            )
        main.assert_called_once_with("白酒", dependencies["8_materials"]["data"]["materials_detail"])

    def test_fund_holder_profile_env_is_scoped_to_the_native_call(self) -> None:
        observed: list[str | None] = []

        def main(_ticker, *, limit=None):
            observed.append(os.environ.get("UZI_FUND_STATS_TOP"))
            return {"data": {"fund_managers": []}}

        with (
            patch.dict(os.environ, {"UZI_FUND_STATS_TOP": "77"}),
            patch(
                "domain_packs.finance_ashare.vendor.uzi_3_9_2.runtime.importlib.import_module",
                return_value=SimpleNamespace(main=main),
            ),
        ):
            self.runtime.collect(
                "6_fund_holders",
                ticker="600000",
                options={"fund_stats_top_n": 5},
            )
            self.assertEqual(os.environ.get("UZI_FUND_STATS_TOP"), "77")

        self.assertEqual(observed, ["5"])

    def test_unknown_fetcher_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported UZI 3.9.4 fetcher"):
            self.runtime.collect("missing", ticker="600519")

    def test_contests_runtime_blocks_xueqiu_at_the_native_boundary(self) -> None:
        calls = {"xueqiu": 0}
        module = SimpleNamespace()

        def original_fetch(_ticker_info, limit=50):
            calls["xueqiu"] += 1
            return [{"name": "unexpected"}], {"source": "unguarded"}

        def cached(_ticker, _key, fetch_fn, ttl=0):
            return fetch_fn()

        def main(ticker):
            cubes, meta = module.fetch_xueqiu_cubes(object())
            return {
                "ticker": ticker,
                "data": {"xueqiu_cubes": cubes, "xueqiu_meta": meta},
                "source": "xueqiu + public fallbacks",
                "fallback": not bool(cubes),
            }

        module.main = main
        module.fetch_xueqiu_cubes = original_fetch
        module.cached = cached
        with patch(
            "domain_packs.finance_ashare.vendor.uzi_3_9_2.runtime.importlib.import_module",
            return_value=module,
        ):
            result = self.runtime.collect(
                "19_contests",
                ticker="600519",
                options={
                    "cognitrade_xueqiu_policy": {"enabled": False, "cookie": None}
                },
            )

        self.assertEqual(calls["xueqiu"], 0)
        self.assertEqual(result["data"]["xueqiu_cubes"], [])
        self.assertEqual(
            result["data"]["xueqiu_meta"]["policy_status"],
            "sensitive_source_disabled",
        )
        self.assertIs(module.fetch_xueqiu_cubes, original_fetch)
        self.assertIs(module.cached, cached)

    def test_pipeline_spawn_worker_preserves_xueqiu_policy_gate(self) -> None:
        contests = SimpleNamespace()

        def original_fetch(_ticker_info, limit=50):
            return [{"name": "unexpected"}], {"source": "unguarded"}

        def cached(_ticker, _key, fetch_fn, ttl=0):
            return fetch_fn()

        contests.fetch_xueqiu_cubes = original_fetch
        contests.cached = cached

        def native_job(dim_key, ticker, raw_context):
            cubes, meta = contests.fetch_xueqiu_cubes(object())
            return dim_key, {"data": {"xueqiu_cubes": cubes, "xueqiu_meta": meta}}, {}

        pipeline_collect = SimpleNamespace(_run_fetcher_job=native_job)

        def import_module(name):
            return pipeline_collect if name == "lib.pipeline.collect" else contests

        with patch.object(
            uzi_runtime.importlib,
            "import_module",
            side_effect=import_module,
        ):
            result = uzi_runtime._run_pipeline_contest_job(
                "19_contests",
                "600519",
                None,
                {"enabled": False, "cookie": None},
            )

        self.assertEqual(result[1]["data"]["xueqiu_cubes"], [])
        self.assertEqual(
            result[1]["data"]["xueqiu_meta"]["policy_status"],
            "sensitive_source_disabled",
        )
        self.assertIs(contests.fetch_xueqiu_cubes, original_fetch)
        self.assertIs(contests.cached, cached)

    def test_pipeline_policy_is_attached_only_to_the_contest_process_job(self) -> None:
        runner = __import__("lib.pipeline.process_runner", fromlist=["ProcessJob"])
        captured = []

        def run_process_jobs(jobs, *, max_workers, overall_timeout):
            captured.extend(jobs)
            return []

        pipeline_collect = SimpleNamespace(run_process_jobs=run_process_jobs)
        normal = runner.ProcessJob(
            key="0_basic",
            target=operator.add,
            args=(1, 2),
        )
        contest = runner.ProcessJob(
            key="19_contests",
            target=operator.add,
            args=("19_contests", "600519"),
        )

        with uzi_runtime._pipeline_contests_policy_scope(
            pipeline_collect,
            policy={"enabled": True, "cookie": "secret-cookie"},
        ):
            pipeline_collect.run_process_jobs(
                [normal, contest],
                max_workers=2,
                overall_timeout=30,
            )

        self.assertIs(captured[0], normal)
        self.assertIs(captured[1].target, uzi_runtime._run_pipeline_contest_job)
        self.assertEqual(captured[1].args[-1]["cookie"], "secret-cookie")
        self.assertNotIn("secret-cookie", repr(captured[0]))
        self.assertIs(pipeline_collect.run_process_jobs, run_process_jobs)

    def test_process_runner_enforces_hard_timeout_with_platform_startup_budget(self) -> None:
        runner = __import__(
            "lib.pipeline.process_runner",
            fromlist=["ProcessJob", "run_process_jobs"],
        )
        jobs = [
            runner.ProcessJob(
                key="fast",
                target=operator.add,
                args=(1, 2),
                timeout_sec=5.0,
            ),
            runner.ProcessJob(
                key="slow",
                target=time.sleep,
                args=(10.0,),
                timeout_sec=0.05,
            ),
        ]

        started = time.monotonic()
        outcomes = {
            row.key: row
            for row in runner.run_process_jobs(
                jobs,
                max_workers=2,
                overall_timeout=5.0,
            )
        }

        self.assertLess(time.monotonic() - started, 5.0)
        self.assertEqual(outcomes["fast"].value, 3)
        self.assertIsNone(outcomes["fast"].error)
        self.assertTrue(outcomes["slow"].timed_out)

    def test_production_snapshot_path_preserves_raw_results_and_dependency_waves(self) -> None:
        calls: list[tuple[str, str | None, dict]] = []

        def collect(fetcher_id: str, **kwargs):
            calls.append((fetcher_id, kwargs.get("industry"), dict(kwargs.get("dependencies") or {})))
            if fetcher_id == "0_basic":
                return {
                    "ticker": "600519.SH",
                    "market": "A",
                    "data": {"name": "贵州茅台", "industry": "白酒", "price": 1418.2},
                    "source": "akshare:A",
                    "fallback": False,
                }
            if fetcher_id == "8_materials":
                return {
                    "ticker": "600519.SH",
                    "data": {"materials_detail": [{"name": "玉米", "code": "C0"}]},
                    "source": "akshare:futures_main_sina",
                    "fallback": False,
                }
            return {
                "data": {"observed": fetcher_id},
                "source": f"uzi:{fetcher_id}",
                "fallback": False,
            }

        with patch.object(Uzi392FetcherRuntime, "collect", autospec=True) as mocked:
            mocked.side_effect = lambda _self, fetcher_id, **kwargs: collect(fetcher_id, **kwargs)
            rows = snapshot_collector.collect_snapshots(
                query="分析贵州茅台",
                symbols=["600519"],
                snapshot_types=["futures", "macro", "materials", "market"],
                max_workers=4,
            )

        by_type = {row["snapshot_type"]: row for row in rows}
        self.assertEqual(
            by_type["market"]["facts"]["_uzi_raw_result"]["data"]["industry"],
            "白酒",
        )
        macro_call = next(row for row in calls if row[0] == "3_macro")
        futures_call = next(row for row in calls if row[0] == "9_futures")
        self.assertEqual(macro_call[1], "白酒")
        self.assertEqual(futures_call[1], "白酒")
        self.assertEqual(
            futures_call[2]["8_materials"]["data"]["materials_detail"][0]["code"],
            "C0",
        )

    def test_all_22_fetchers_are_reachable_from_the_production_snapshot_path(self) -> None:
        called: list[str] = []

        def collect(fetcher_id: str, **_kwargs):
            called.append(fetcher_id)
            if fetcher_id == "19_contests":
                policy = (_kwargs.get("options") or {}).get("cognitrade_xueqiu_policy")
                self.assertEqual(policy, {"enabled": False, "cookie": None})
            data = {"observed": fetcher_id}
            if fetcher_id == "0_basic":
                data.update({"name": "贵州茅台", "industry": "白酒", "price": 1418.2})
            if fetcher_id == "8_materials":
                data["materials_detail"] = [{"name": "玉米", "code": "C0"}]
            return {
                "ticker": "600519.SH",
                "data": data,
                "source": f"uzi:{fetcher_id}",
                "fallback": False,
            }

        with patch.dict(
            os.environ,
            {
                "COGNITRADE_SENSITIVE_SOURCES_ENABLED": "false",
                "COGNITRADE_SENSITIVE_SOURCE_ALLOWLIST": "",
                "COGNITRADE_XUEQIU_COOKIE": "",
            },
        ), patch.object(Uzi392FetcherRuntime, "collect", autospec=True) as mocked:
            mocked.side_effect = lambda _self, fetcher_id, **kwargs: collect(fetcher_id, **kwargs)
            rows = snapshot_collector.collect_snapshots(
                query="完整分析贵州茅台",
                symbols=["600519"],
                snapshot_types=list(snapshot_collector._UZI_FETCHER_BY_SNAPSHOT_TYPE),
                max_workers=8,
            )

        self.assertEqual(set(called), set(UZI_FETCHER_IDS))
        self.assertEqual(len(called), 22)
        self.assertEqual(len(rows), 22)
        self.assertEqual({row.get("symbol") for row in rows}, {"600519"})
        self.assertTrue(
            all(
                isinstance((row.get("facts") or {}).get("_uzi_raw_result"), dict)
                for row in rows
            )
        )

    def test_native_snapshot_cache_version_rejects_pre_raw_payload_entries(self) -> None:
        for snapshot_type in snapshot_collector._UZI_FETCHER_BY_SNAPSHOT_TYPE:
            with self.subTest(snapshot_type=snapshot_type):
                context = snapshot_collector._snapshot_cache_context(
                    snapshot_type=snapshot_type,
                    target="600519",
                    profile="medium",
                )
                self.assertIn("uzi-3.9.4-native-raw-v1", context["schema_version"])


if __name__ == "__main__":
    unittest.main()
