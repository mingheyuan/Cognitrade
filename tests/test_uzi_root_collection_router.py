from __future__ import annotations

import importlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from domain_packs.finance_ashare.subagents.uzi_methodology import select_profile
from domain_packs.finance_ashare.subagents.uzi_392_native_methods import build_uzi_raw
from domain_packs.finance_ashare.subagents import runtime as subagent_runtime
from domain_packs.finance_ashare.tools import snapshot_collector
from domain_packs.finance_ashare.vendor.uzi_3_9_2 import Uzi392FetcherRuntime


class UziRootCollectionRouterTests(unittest.TestCase):
    def _runtime(self, workspace_root: Path) -> Uzi392FetcherRuntime:
        return Uzi392FetcherRuntime(workspace_root=workspace_root)

    def test_pipeline_partial_is_a_degraded_snapshot_with_explicit_gaps(self) -> None:
        raw_result = {
            "ticker": "600519.SH",
            "data": {"roe_history": [30.0, 31.0]},
            "source": "legacy:fetch_financials",
            "fallback": False,
            "_pipeline": {
                "dim_key": "1_financials",
                "quality": "partial",
                "data_gaps": ["gross_margin", "ocf"],
            },
        }
        normalized = snapshot_collector._normalize_snapshot_quality(
            {
                "snapshot_type": "fundamental",
                "symbol": "600519",
                "quality_status": "available",
                "degraded": False,
                "missing_fields": [],
                "facts": {
                    "availability": "available",
                    "_uzi_raw_result": raw_result,
                },
            }
        )

        self.assertEqual(normalized["quality_status"], "degraded")
        self.assertTrue(normalized["degraded"])
        self.assertEqual(normalized["missing_fields"], ["gross_margin", "ocf"])
        self.assertIn("uzi_pipeline_partial", normalized["degrade_reasons"])
        self.assertEqual(normalized["facts"]["availability"], "degraded")
        self.assertIs(normalized["facts"]["_uzi_raw_result"], raw_result)

    def test_capital_flow_partial_without_target_observation_is_data_gap(self) -> None:
        raw_result = {
            "ticker": "000858.SZ",
            "data": {
                "northbound": None,
                "margin_recent": None,
                "holder_count_history": [
                    {"代码": "688609", "名称": "九联科技", "股东户数-本次": 18680}
                ],
                "holders_trend": "基本持平",
                "main_fund_flow_20d": None,
                "institutional_history": {
                    "quarters": ["26Q1", "26Q2"],
                    "fund": [0.0, 0.0],
                    "qfii": [0.0, 0.0],
                    "shehui": [0.0, 0.0],
                },
            },
            "source": "legacy:fetch_capital_flow",
            "fallback": False,
            "_pipeline": {
                "dim_key": "12_capital_flow",
                "quality": "partial",
                "data_gaps": [
                    "northbound",
                    "margin_recent",
                    "main_fund_flow_20d",
                ],
            },
        }
        symbol_result = {
            "ticker": "000858",
            "market": "A",
            "code": "000858",
            "full": "000858.SZ",
            "dimensions": {"12_capital_flow": raw_result},
            "_uzi_execution": {"lane": "pipeline", "fallback_reason": None},
        }
        with patch.object(
            Uzi392FetcherRuntime,
            "collect_symbol",
            autospec=True,
            return_value=symbol_result,
        ):
            row = snapshot_collector.collect_snapshots(
                query="五粮液资金面",
                symbols=["000858"],
                snapshot_types=["capital_flow"],
                research_profile="medium",
                uzi_root_route=True,
                no_resume=True,
            )[0]

        self.assertEqual(row["quality_status"], "data_gap")
        self.assertTrue(row["degraded"])
        self.assertEqual(
            row["missing_fields"],
            [
                "northbound",
                "margin_recent",
                "main_fund_flow_20d",
                "capital_flow_target_observation",
            ],
        )
        self.assertIn(
            "capital_flow_target_observation_missing",
            row["degrade_reasons"],
        )
        self.assertEqual(row["facts"]["availability"], "data_gap")
        self.assertEqual(row["source_trace"][0]["status"], "data_gap")
        self.assertEqual(row["facts"]["_uzi_raw_result"], raw_result)

    def test_capital_flow_partial_with_target_observation_is_degraded(self) -> None:
        raw_result = {
            "ticker": "600519.SH",
            "data": {
                "main_fund_flow_20d": [
                    {"日期": "2026-08-11", "主力净流入-净额": -303316592.0}
                ],
                "northbound": None,
                "margin_recent": None,
            },
            "source": "legacy:fetch_capital_flow",
            "fallback": False,
            "_pipeline": {
                "dim_key": "12_capital_flow",
                "quality": "partial",
                "data_gaps": ["northbound", "margin_recent"],
            },
        }
        symbol_result = {
            "ticker": "600519",
            "market": "A",
            "code": "600519",
            "full": "600519.SH",
            "dimensions": {"12_capital_flow": raw_result},
            "_uzi_execution": {"lane": "pipeline", "fallback_reason": None},
        }
        with patch.object(
            Uzi392FetcherRuntime,
            "collect_symbol",
            autospec=True,
            return_value=symbol_result,
        ):
            row = snapshot_collector.collect_snapshots(
                query="茅台资金面",
                symbols=["600519"],
                snapshot_types=["capital_flow"],
                research_profile="medium",
                uzi_root_route=True,
                no_resume=True,
            )[0]

        self.assertEqual(row["quality_status"], "degraded")
        self.assertEqual(row["missing_fields"], ["northbound", "margin_recent"])
        self.assertIn("uzi_pipeline_partial", row["degrade_reasons"])
        self.assertEqual(row["source_trace"][0]["status"], "degraded")

    def test_request_scoped_no_resume_reaches_root_uzi_router(self) -> None:
        symbol_result = {
            "ticker": "600519",
            "market": "A",
            "code": "600519",
            "full": "600519.SH",
            "dimensions": {
                "12_capital_flow": {
                    "data": {"main_5d": "—"},
                    "source": "fixture",
                    "fallback": False,
                    "_pipeline": {"dim_key": "12_capital_flow", "quality": "full"},
                }
            },
            "_uzi_execution": {"lane": "pipeline", "fallback_reason": None},
        }
        with patch.object(
            Uzi392FetcherRuntime,
            "collect_symbol",
            autospec=True,
            return_value=symbol_result,
        ) as collect_symbol:
            rows = snapshot_collector.collect_snapshots(
                query="fresh capital flow",
                symbols=["600519"],
                snapshot_types=["capital_flow"],
                research_profile="lite",
                uzi_root_route=True,
                no_resume=True,
            )

        self.assertEqual(len(rows), 1)
        self.assertIs(collect_symbol.call_args.kwargs["resume"], False)
        self.assertEqual(
            collect_symbol.call_args.kwargs["options"][
                "cognitrade_pipeline_fetcher_timeout_overrides"
            ],
            {"similar_stocks": 30.0},
        )

    def test_pipeline_host_timeout_override_reaches_spawn_job(self) -> None:
        observed: dict[str, float] = {}

        with TemporaryDirectory() as temporary:
            runtime = self._runtime(Path(temporary))
            pipeline_run = importlib.import_module("lib.pipeline.run")
            pipeline_collect = importlib.import_module("lib.pipeline.collect")
            run_real_test = importlib.import_module("run_real_test")

            def collect_with_job(*_args, **_kwargs):
                job = pipeline_collect.ProcessJob(
                    "similar_stocks",
                    lambda: None,
                    timeout_sec=120.0,
                )
                observed["timeout_seconds"] = job.timeout_sec
                return {}

            with (
                patch.dict(os.environ, {"UZI_LEGACY": "0"}),
                patch.object(pipeline_run, "_preflight_guards"),
                patch.object(
                    pipeline_collect,
                    "collect",
                    side_effect=collect_with_job,
                ),
                patch.object(run_real_test, "collect_raw_data") as legacy,
                patch.object(runtime, "_write_native_raw"),
            ):
                runtime.collect_symbol(
                    ticker="600519",
                    options={
                        "cognitrade_pipeline_fetcher_timeout_overrides": {
                            "similar_stocks": 30.0,
                        }
                    },
                )

        self.assertEqual(observed, {"timeout_seconds": 30.0})
        self.assertEqual(pipeline_collect.ProcessJob.__name__, "ProcessJob")
        legacy.assert_not_called()

    def test_multi_runner_keeps_pinned_resume_true(self) -> None:
        symbol_result = {
            "ticker": "600519",
            "market": "A",
            "code": "600519",
            "full": "600519.SH",
            "dimensions": {
                "12_capital_flow": {
                    "data": {"main_5d": "—"},
                    "source": "fixture",
                    "fallback": False,
                    "_pipeline": {"dim_key": "12_capital_flow", "quality": "full"},
                }
            },
            "_uzi_execution": {"lane": "pipeline", "fallback_reason": None},
        }
        with patch.object(
            Uzi392FetcherRuntime,
            "collect_symbol",
            autospec=True,
            return_value=symbol_result,
        ) as collect_symbol:
            rows = snapshot_collector.collect_snapshots(
                query="compare capital flow",
                symbols=["600519"],
                snapshot_types=["capital_flow"],
                research_profile="lite",
                uzi_root_route=True,
                uzi_root_route_mode="multi",
                # Defensive internal-call coverage: the public API rejects
                # this field for versus/portfolio, and the pinned runners
                # always call their pipeline with resume=True.
                no_resume=True,
            )

        self.assertEqual(len(rows), 1)
        self.assertIs(collect_symbol.call_args.kwargs["resume"], True)

    @staticmethod
    def _legacy_raw(*, marker: str = "legacy") -> dict:
        return {
            "ticker": "600519",
            "market": "A",
            "dimensions": {
                "0_basic": {
                    "data": {"name": "贵州茅台", "marker": marker},
                    "source": f"fixture:{marker}:basic",
                    "fallback": False,
                },
                "12_capital_flow": {
                    "data": {"marker": marker},
                    "source": f"fixture:{marker}:capital",
                    "fallback": False,
                },
            },
            "fund_managers": [],
            "similar_stocks": [],
        }

    @staticmethod
    def _collect_with_actual_capital_flow_adapter(ticker: str, **_kwargs) -> dict:
        registry = importlib.import_module("lib.pipeline.fetchers.registry")
        fetcher = registry.get_fetcher("12_capital_flow")
        if fetcher is None:
            raise AssertionError("Pinned UZI registry is missing 12_capital_flow")
        return {"12_capital_flow": fetcher.fetch(ticker).to_dict()}

    def test_default_route_uses_v3_and_normalizes_capital_flow_dim_result(self) -> None:
        upstream = {
            "ticker": "600519.SH",
            "data": {
                "northbound": "",
                "margin_recent": [],
                "holder_count_history": [{"holder_count": 12345}],
                "main_fund_flow_20d": [],
                "institutional_history": None,
            },
            "source": "akshare:multi",
            "fallback": False,
        }

        with TemporaryDirectory() as temporary:
            runtime = self._runtime(Path(temporary))
            pipeline_run = importlib.import_module("lib.pipeline.run")
            pipeline_collect = importlib.import_module("lib.pipeline.collect")
            capital_flow = importlib.import_module("fetch_capital_flow")
            run_real_test = importlib.import_module("run_real_test")

            with (
                patch.dict(os.environ, {"UZI_LEGACY": "0"}),
                patch.object(pipeline_run, "_preflight_guards") as preflight,
                patch.object(
                    pipeline_collect,
                    "collect",
                    side_effect=self._collect_with_actual_capital_flow_adapter,
                ) as collect,
                patch.object(capital_flow, "main", return_value=upstream) as main,
                patch.object(run_real_test, "collect_raw_data") as legacy,
                patch.object(runtime, "_write_native_raw"),
            ):
                raw = runtime.collect_symbol(
                    ticker="600519",
                    raw_previous={},
                    max_workers=4,
                )

        result = raw["dimensions"]["12_capital_flow"]
        self.assertEqual(raw["_uzi_execution"], {
            "lane": "pipeline",
            "fallback_reason": None,
        })
        self.assertIsNone(result["data"]["northbound"])
        self.assertIsNone(result["data"]["margin_recent"])
        self.assertEqual(
            result["data"]["holder_count_history"],
            upstream["data"]["holder_count_history"],
        )
        self.assertIsNone(result["data"]["main_fund_flow_20d"])
        self.assertIsNone(result["data"]["institutional_history"])
        self.assertEqual(result["source"], "legacy:fetch_capital_flow")
        self.assertEqual(result["_pipeline"]["dim_key"], "12_capital_flow")
        self.assertEqual(result["_pipeline"]["quality"], "partial")
        preflight.assert_called_once_with("600519")
        collect.assert_called_once_with("600519", raw_previous={}, max_workers=4)
        main.assert_called_once_with("600519")
        legacy.assert_not_called()

    def test_capital_flow_provider_exception_is_v3_error_not_root_fallback(self) -> None:
        with TemporaryDirectory() as temporary:
            runtime = self._runtime(Path(temporary))
            pipeline_run = importlib.import_module("lib.pipeline.run")
            pipeline_collect = importlib.import_module("lib.pipeline.collect")
            capital_flow = importlib.import_module("fetch_capital_flow")
            run_real_test = importlib.import_module("run_real_test")

            with (
                patch.dict(os.environ, {"UZI_LEGACY": "0"}),
                patch.object(pipeline_run, "_preflight_guards"),
                patch.object(
                    pipeline_collect,
                    "collect",
                    side_effect=self._collect_with_actual_capital_flow_adapter,
                ),
                patch.object(
                    capital_flow,
                    "main",
                    side_effect=RuntimeError("provider fixture failed"),
                ) as main,
                patch.object(run_real_test, "collect_raw_data") as legacy,
                patch.object(runtime, "_write_native_raw"),
            ):
                raw = runtime.collect_symbol(ticker="600519", raw_previous={})

        result = raw["dimensions"]["12_capital_flow"]
        self.assertEqual(raw["_uzi_execution"]["lane"], "pipeline")
        self.assertIsNone(raw["_uzi_execution"]["fallback_reason"])
        self.assertEqual(result["data"], {})
        self.assertTrue(result["fallback"])
        self.assertEqual(result["_pipeline"]["quality"], "error")
        self.assertIn("RuntimeError: provider fixture failed", result["_pipeline"]["error"])
        main.assert_called_once_with("600519")
        legacy.assert_not_called()

    def test_corrupt_pipeline_resume_cache_is_ignored_and_v3_continues(self) -> None:
        with TemporaryDirectory() as temporary:
            runtime = self._runtime(Path(temporary))
            pipeline_run = importlib.import_module("lib.pipeline.run")
            pipeline_collect = importlib.import_module("lib.pipeline.collect")
            cache = importlib.import_module("lib.cache")
            run_real_test = importlib.import_module("run_real_test")
            collected = {
                "12_capital_flow": {
                    "data": {"marker": "fresh-v3"},
                    "source": "fixture:v3",
                    "fallback": False,
                    "_pipeline": {
                        "dim_key": "12_capital_flow",
                        "quality": "full",
                    },
                }
            }

            with (
                patch.dict(os.environ, {"UZI_LEGACY": "0"}),
                patch.object(pipeline_run, "_preflight_guards"),
                patch.object(
                    cache,
                    "read_task_output",
                    side_effect=ValueError("corrupt json fixture"),
                ),
                patch.object(pipeline_collect, "collect", return_value=collected) as collect,
                patch.object(run_real_test, "collect_raw_data") as legacy,
                patch.object(runtime, "_write_native_raw"),
            ):
                raw = runtime.collect_symbol(ticker="600519")

        self.assertEqual(raw["_uzi_execution"]["lane"], "pipeline")
        self.assertEqual(
            raw["dimensions"]["12_capital_flow"]["data"]["marker"],
            "fresh-v3",
        )
        collect.assert_called_once_with("600519", raw_previous={}, max_workers=6)
        legacy.assert_not_called()

    def test_preflight_rejection_falls_back_before_v3_collection(self) -> None:
        legacy_raw = self._legacy_raw(marker="preflight-legacy")
        with TemporaryDirectory() as temporary:
            runtime = self._runtime(Path(temporary))
            pipeline_run = importlib.import_module("lib.pipeline.run")
            pipeline_collect = importlib.import_module("lib.pipeline.collect")
            run_real_test = importlib.import_module("run_real_test")

            with (
                patch.dict(os.environ, {"UZI_LEGACY": "0"}),
                patch.object(
                    pipeline_run,
                    "_preflight_guards",
                    side_effect=ValueError("fixture rejected"),
                ) as preflight,
                patch.object(pipeline_collect, "collect") as collect,
                patch.object(
                    run_real_test,
                    "collect_raw_data",
                    return_value=legacy_raw,
                ) as legacy,
            ):
                raw = runtime.collect_symbol(ticker="600519")

        self.assertEqual(raw["_uzi_execution"]["lane"], "legacy_fallback")
        self.assertEqual(
            raw["_uzi_execution"]["fallback_reason"],
            "ValueError",
        )
        self.assertEqual(
            raw["dimensions"]["12_capital_flow"]["data"]["marker"],
            "preflight-legacy",
        )
        preflight.assert_called_once_with("600519")
        collect.assert_not_called()
        legacy.assert_called_once_with("600519.SH", max_workers=6, resume=True)

    def test_preflight_fallback_never_scores_an_etf_as_a_stock(self) -> None:
        with TemporaryDirectory() as temporary:
            runtime = self._runtime(Path(temporary))
            pipeline_run = importlib.import_module("lib.pipeline.run")
            pipeline_collect = importlib.import_module("lib.pipeline.collect")
            run_real_test = importlib.import_module("run_real_test")
            with (
                patch.dict(os.environ, {"UZI_LEGACY": "0"}),
                patch.object(
                    pipeline_run,
                    "_preflight_guards",
                    side_effect=ValueError("ETF requires legacy target handling"),
                ),
                patch.object(pipeline_collect, "collect") as collect,
                patch.object(run_real_test, "collect_raw_data") as legacy,
            ):
                with self.assertRaisesRegex(ValueError, "not an individual stock: etf"):
                    runtime.collect_symbol(ticker="510300")

        collect.assert_not_called()
        legacy.assert_not_called()

    def test_pipeline_infrastructure_exception_discards_v3_and_falls_back_whole_symbol(self) -> None:
        legacy_raw = self._legacy_raw(marker="infra-legacy")
        v3_candidate = {
            "0_basic": {"data": {"marker": "discarded-v3"}},
            "12_capital_flow": {"data": {"marker": "discarded-v3"}},
        }

        def crash_after_partial_candidate(*_args, **_kwargs):
            self.assertEqual(
                v3_candidate["12_capital_flow"]["data"]["marker"],
                "discarded-v3",
            )
            raise RuntimeError("pipeline infrastructure failed")

        with TemporaryDirectory() as temporary:
            runtime = self._runtime(Path(temporary))
            pipeline_run = importlib.import_module("lib.pipeline.run")
            pipeline_collect = importlib.import_module("lib.pipeline.collect")
            run_real_test = importlib.import_module("run_real_test")

            with (
                patch.dict(os.environ, {"UZI_LEGACY": "0"}),
                patch.object(pipeline_run, "_preflight_guards"),
                patch.object(
                    pipeline_collect,
                    "collect",
                    side_effect=crash_after_partial_candidate,
                ) as collect,
                patch.object(
                    run_real_test,
                    "collect_raw_data",
                    return_value=legacy_raw,
                ) as legacy,
                patch.object(runtime, "_write_native_raw") as write_raw,
            ):
                raw = runtime.collect_symbol(
                    ticker="600519",
                    raw_previous={},
                    resume=False,
                )

        self.assertEqual(raw["_uzi_execution"]["lane"], "legacy_fallback")
        self.assertEqual(
            raw["_uzi_execution"]["fallback_reason"],
            "RuntimeError",
        )
        projected_markers = {
            dimension["data"]["marker"]
            for dimension in raw["dimensions"].values()
        }
        self.assertEqual(projected_markers, {"infra-legacy"})
        self.assertNotIn("discarded-v3", str(raw))
        collect.assert_called_once_with("600519", raw_previous={}, max_workers=6)
        legacy.assert_called_once_with("600519.SH", max_workers=6, resume=False)
        write_raw.assert_not_called()

    def test_explicit_uzi_legacy_skips_preflight_and_v3(self) -> None:
        legacy_raw = self._legacy_raw(marker="forced-legacy")
        with TemporaryDirectory() as temporary:
            runtime = self._runtime(Path(temporary))
            pipeline_run = importlib.import_module("lib.pipeline.run")
            pipeline_collect = importlib.import_module("lib.pipeline.collect")
            run_real_test = importlib.import_module("run_real_test")

            with (
                patch.dict(os.environ, {"UZI_LEGACY": "1"}),
                patch.object(pipeline_run, "_preflight_guards") as preflight,
                patch.object(pipeline_collect, "collect") as collect,
                patch.object(
                    run_real_test,
                    "collect_raw_data",
                    return_value=legacy_raw,
                ) as legacy,
            ):
                raw = runtime.collect_symbol(ticker="600519", max_workers=3)

        self.assertEqual(raw["_uzi_execution"], {
            "lane": "legacy_forced",
            "fallback_reason": None,
        })
        self.assertEqual(
            raw["dimensions"]["12_capital_flow"]["data"]["marker"],
            "forced-legacy",
        )
        preflight.assert_not_called()
        collect.assert_not_called()
        legacy.assert_called_once_with("600519.SH", max_workers=3, resume=True)

    def test_multi_runner_ignores_root_legacy_switch_and_uses_v3(self) -> None:
        collected = {
            "12_capital_flow": {
                "data": {"marker": "multi-v3"},
                "source": "fixture:v3",
                "fallback": False,
                "_pipeline": {"dim_key": "12_capital_flow", "quality": "full"},
            }
        }
        with TemporaryDirectory() as temporary:
            runtime = self._runtime(Path(temporary))
            pipeline_run = importlib.import_module("lib.pipeline.run")
            pipeline_collect = importlib.import_module("lib.pipeline.collect")
            run_real_test = importlib.import_module("run_real_test")
            with (
                patch.dict(os.environ, {"UZI_LEGACY": "1"}),
                patch.object(pipeline_run, "_preflight_guards"),
                patch.object(pipeline_collect, "collect", return_value=collected) as collect,
                patch.object(run_real_test, "collect_raw_data") as legacy,
                patch.object(runtime, "_write_native_raw"),
            ):
                raw = runtime.collect_symbol(
                    ticker="600519",
                    raw_previous={},
                    options={"cognitrade_root_route_mode": "multi"},
                )

        self.assertEqual(raw["_uzi_execution"]["lane"], "pipeline")
        collect.assert_called_once()
        legacy.assert_not_called()

    def test_multi_runner_ignores_process_no_resume_for_v3_cache(self) -> None:
        cached = self._legacy_raw(marker="cached-multi")
        collected = {
            "12_capital_flow": {
                "data": {"marker": "multi-v3"},
                "source": "fixture:v3",
                "fallback": False,
                "_pipeline": {"dim_key": "12_capital_flow", "quality": "full"},
            }
        }
        with TemporaryDirectory() as temporary:
            runtime = self._runtime(Path(temporary))
            pipeline_run = importlib.import_module("lib.pipeline.run")
            pipeline_collect = importlib.import_module("lib.pipeline.collect")
            with (
                patch.dict(os.environ, {"UZI_NO_RESUME": "1"}),
                patch.object(pipeline_run, "_preflight_guards"),
                patch.object(pipeline_collect, "collect", return_value=collected) as collect,
                patch.object(runtime, "_write_native_raw"),
            ):
                runtime.collect_symbol(
                    ticker="600519",
                    raw_previous=cached,
                    options={"cognitrade_root_route_mode": "multi"},
                )

        collect.assert_called_once_with("600519", raw_previous=cached, max_workers=6)

    def test_multi_runner_skips_raw_only_cache_after_pipeline_error(self) -> None:
        cached = self._legacy_raw(marker="cached-before-error")
        with TemporaryDirectory() as temporary:
            runtime = self._runtime(Path(temporary))
            pipeline_run = importlib.import_module("lib.pipeline.run")
            pipeline_collect = importlib.import_module("lib.pipeline.collect")
            run_real_test = importlib.import_module("run_real_test")
            with (
                patch.dict(os.environ, {"UZI_LEGACY": "0"}),
                patch.object(pipeline_run, "_preflight_guards"),
                patch.object(
                    pipeline_collect,
                    "collect",
                    side_effect=RuntimeError("pipeline fixture failed"),
                ),
                patch.object(run_real_test, "collect_raw_data") as legacy,
            ):
                with self.assertRaisesRegex(RuntimeError, "pipeline fixture failed"):
                    runtime.collect_symbol(
                        ticker="600519",
                        raw_previous=cached,
                        options={"cognitrade_root_route_mode": "multi"},
                    )

        legacy.assert_not_called()

    def test_multi_route_failure_is_excluded_from_host_recovery(self) -> None:
        failed = snapshot_collector._fetcher_failure_snapshot(
            query="compare",
            symbol="000858",
            snapshot_type="capital_flow",
            exc=RuntimeError("pipeline failed"),
            symbol_route_failed=True,
        )
        healthy = {
            "snapshot_type": "capital_flow",
            "symbol": "600519",
            "facts": {
                "availability": "available",
                "_uzi_raw_result": {
                    "ticker": "600519",
                    "data": {"main_5d": "+1"},
                    "source": "fixture",
                    "fallback": False,
                },
            },
            "quality_status": "available",
            "source": {"provider_id": "fixture"},
        }
        stage = {
            "enabled": True,
            "attempted": 0,
            "succeeded": 0,
            "failed": 0,
            "attempts": [],
        }
        settings = snapshot_collector.Settings.from_env(
            Path(__file__).resolve().parents[1]
        )
        with (
            patch.object(
                snapshot_collector,
                "recover_suggested_fixes_until_exhausted",
                return_value=stage,
            ) as strict,
            patch.object(
                snapshot_collector,
                "recover_qualitative_snapshots",
                return_value=stage,
            ) as qualitative,
            patch.object(
                snapshot_collector,
                "recover_playwright_snapshots",
                return_value=stage,
            ) as playwright,
            patch.object(
                snapshot_collector,
                "collect_qualitative_agent_evidence",
                return_value=stage,
            ) as agent_evidence,
        ):
            result = snapshot_collector._bundle_result(
                snapshots=[healthy, failed],
                symbols=["600519", "000858"],
                requested=["capital_flow"],
                universe_rows=[],
                universe_source="structured_symbols",
                enrichment_targets=[],
                query="compare",
                research_profile="lite",
                settings=settings,
                intent="stock_compare",
            )

        for recovery in (strict, qualitative, playwright, agent_evidence):
            self.assertEqual(
                [row.get("symbol") for row in recovery.call_args.args[0]],
                ["600519"],
            )
            self.assertEqual(recovery.call_args.kwargs["symbols"], ["600519"])
        failed_result = next(
            row for row in result["snapshots"] if row.get("symbol") == "000858"
        )
        self.assertTrue(failed_result["facts"]["_uzi_symbol_route_failed"])
        self.assertNotIn("_uzi_fetcher_id", failed_result["facts"])
        self.assertEqual(
            failed_result["source"]["provider_id"],
            "uzi_3_9_2.root_collection",
        )
        self.assertEqual(failed_result["source"]["source_id"], "root_symbol_route")
        self.assertEqual(
            failed_result["facts"]["_uzi_raw_result"]["source"],
            "lib.pipeline.collect",
        )
        self.assertEqual(failed_result["quality_status"], "data_gap")

    def test_provider_error_keeps_every_dimension_on_the_v3_lane(self) -> None:
        with TemporaryDirectory() as temporary:
            runtime = self._runtime(Path(temporary))
            pipeline_run = importlib.import_module("lib.pipeline.run")
            pipeline_collect = importlib.import_module("lib.pipeline.collect")
            pipeline_schema = importlib.import_module("lib.pipeline.schema")
            pipeline_registry = importlib.import_module("lib.pipeline.fetchers.registry")
            capital_flow = importlib.import_module("fetch_capital_flow")
            run_real_test = importlib.import_module("run_real_test")

            def collect_v3(ticker: str, **_kwargs) -> dict:
                capital_fetcher = pipeline_registry.get_fetcher("12_capital_flow")
                if capital_fetcher is None:
                    raise AssertionError("Pinned UZI registry is missing 12_capital_flow")
                basic = pipeline_schema.DimResult(
                    dim_key="0_basic",
                    data={"name": "贵州茅台", "price": 1400.0},
                    source="legacy:fetch_basic",
                    quality=pipeline_schema.Quality.FULL,
                )
                return {
                    "0_basic": basic.to_dict(),
                    "12_capital_flow": capital_fetcher.fetch(ticker).to_dict(),
                }

            with (
                patch.dict(os.environ, {"UZI_LEGACY": "0"}),
                patch.object(pipeline_run, "_preflight_guards"),
                patch.object(pipeline_collect, "collect", side_effect=collect_v3),
                patch.object(
                    capital_flow,
                    "main",
                    side_effect=RuntimeError("capital provider down"),
                ),
                patch.object(run_real_test, "collect_raw_data") as legacy,
                patch.object(runtime, "_write_native_raw"),
            ):
                raw = runtime.collect_symbol(ticker="600519", raw_previous={})

        self.assertEqual(raw["_uzi_execution"]["lane"], "pipeline")
        self.assertEqual(
            raw["dimensions"]["0_basic"]["_pipeline"]["quality"],
            "full",
        )
        self.assertEqual(
            raw["dimensions"]["12_capital_flow"]["_pipeline"]["quality"],
            "error",
        )
        self.assertNotIn("fixture:legacy", str(raw["dimensions"]))
        legacy.assert_not_called()

    def test_symbol_bonus_top_level_fields_round_trip_without_becoming_dimensions(self) -> None:
        pipeline_schema = None
        cases = (
            (
                "pipeline-non-empty",
                "pipeline",
                [{"name": "基金A"}],
                [{"ticker": "000858.SZ"}],
            ),
            ("pipeline-empty", "pipeline", [], None),
            (
                "legacy-non-empty",
                "legacy_forced",
                [{"name": "基金B"}],
                [{"ticker": "601318.SH"}],
            ),
            ("legacy-empty", "legacy_forced", None, []),
        )

        for label, lane, managers, similar in cases:
            with self.subTest(label=label), TemporaryDirectory() as temporary:
                runtime = self._runtime(Path(temporary))
                pipeline_run = importlib.import_module("lib.pipeline.run")
                pipeline_collect = importlib.import_module("lib.pipeline.collect")
                run_real_test = importlib.import_module("run_real_test")
                if pipeline_schema is None:
                    pipeline_schema = importlib.import_module("lib.pipeline.schema")
                fund_dimension = pipeline_schema.DimResult(
                    dim_key="6_fund_holders",
                    data={"active_funds_count": 1},
                    source="legacy:fetch_fund_holders",
                    quality=pipeline_schema.Quality.FULL,
                ).to_dict()

                if lane == "pipeline":
                    collected = {
                        "6_fund_holders": fund_dimension,
                        "fund_managers": managers,
                        "similar_stocks": similar,
                    }
                    legacy_raw = self._legacy_raw()
                    environment = {"UZI_LEGACY": "0"}
                else:
                    collected = {}
                    legacy_raw = {
                        "ticker": "600519",
                        "market": "A",
                        "dimensions": {},
                        "fund_managers": managers,
                        "similar_stocks": similar,
                    }
                    environment = {"UZI_LEGACY": "1"}

                with (
                    patch.dict(os.environ, environment),
                    patch.object(pipeline_run, "_preflight_guards"),
                    patch.object(
                        pipeline_collect,
                        "collect",
                        return_value=collected,
                    ),
                    patch.object(
                        run_real_test,
                        "collect_raw_data",
                        return_value=legacy_raw,
                    ),
                    patch.object(runtime, "_write_native_raw"),
                ):
                    raw = runtime.collect_symbol(ticker="600519", raw_previous={})

                self.assertEqual(raw["_uzi_execution"]["lane"], lane)
                self.assertIs(raw.get("fund_managers"), managers)
                self.assertIs(raw.get("similar_stocks"), similar)
                self.assertNotIn("fund_managers", raw["dimensions"])
                self.assertNotIn("similar_stocks", raw["dimensions"])
                if lane == "pipeline":
                    self.assertEqual(
                        raw["dimensions"]["6_fund_holders"],
                        fund_dimension,
                    )
                else:
                    self.assertNotIn("6_fund_holders", raw["dimensions"])

    def test_requested_capital_flow_only_still_executes_complete_v3_registry(self) -> None:
        executed: list[str] = []
        with TemporaryDirectory() as temporary:
            runtime = self._runtime(Path(temporary))
            pipeline_run = importlib.import_module("lib.pipeline.run")
            pipeline_collect = importlib.import_module("lib.pipeline.collect")
            pipeline_registry = importlib.import_module("lib.pipeline.fetchers.registry")
            pipeline_schema = importlib.import_module("lib.pipeline.schema")

            class FixtureFetcher:
                def __init__(self, dim_key: str) -> None:
                    self.dim_key = dim_key
                    self._legacy_module = f"fixture_{dim_key}"

                def fetch(self, ticker: str):
                    executed.append(self.dim_key)
                    top_level = {}
                    if self.dim_key == "6_fund_holders":
                        top_level = {"fund_managers": []}
                    elif self.dim_key == "similar_stocks":
                        top_level = {"similar_stocks": []}
                    return pipeline_schema.DimResult(
                        dim_key=self.dim_key,
                        data={"marker": self.dim_key, "ticker": ticker},
                        source=f"fixture:{self.dim_key}",
                        quality=pipeline_schema.Quality.FULL,
                        top_level_fields=top_level,
                    )

            def get_fetcher(dim_key: str):
                self.assertIn(dim_key, pipeline_registry.FETCHER_REGISTRY)
                return FixtureFetcher(dim_key)

            def run_jobs(jobs, *, max_workers, overall_timeout):
                return [
                    pipeline_collect.ProcessOutcome(
                        key=job.key,
                        value=(
                            pipeline_collect._run_fetcher_job
                            if job.key == "19_contests"
                            else job.target
                        )(*job.args[:3], **job.kwargs),
                    )
                    for job in jobs
                ]

            with (
                patch.dict(os.environ, {"UZI_LEGACY": "0"}),
                patch.object(pipeline_run, "_preflight_guards"),
                patch.object(pipeline_collect, "DEPENDENT_DIMS", set()),
                patch.object(pipeline_collect, "get_fetcher", side_effect=get_fetcher),
                patch.object(pipeline_collect, "run_process_jobs", side_effect=run_jobs),
                patch.object(Uzi392FetcherRuntime, "_write_native_raw"),
            ):
                rows = snapshot_collector.collect_snapshots(
                    query="只看资金流",
                    symbols=["600519"],
                    snapshot_types=["capital_flow"],
                    research_profile="lite",
                    cache_root=Path(temporary) / "snapshots",
                    uzi_root_route=True,
                    no_resume=True,
                )

        self.assertEqual(set(executed), set(pipeline_registry.FETCHER_REGISTRY))
        self.assertEqual(len(executed), len(pipeline_registry.FETCHER_REGISTRY))
        self.assertEqual(len(pipeline_registry.FETCHER_REGISTRY), 22)
        self.assertEqual(len(select_profile({"research_profile": "lite"})["fetchers_enabled"]), 7)
        self.assertEqual([row["snapshot_type"] for row in rows], ["capital_flow"])
        self.assertEqual(rows[0]["facts"]["marker"], "12_capital_flow")
        self.assertEqual(rows[0]["facts"]["_uzi_execution"]["lane"], "pipeline")

    def test_duplicate_requested_types_do_not_repeat_symbol_pipeline(self) -> None:
        symbol_result = {
            "ticker": "600519",
            "market": "A",
            "code": "600519",
            "full": "600519.SH",
            "dimensions": {
                "12_capital_flow": {
                    "data": {"marker": "one-symbol-run"},
                    "source": "fixture:capital",
                    "fallback": False,
                    "_pipeline": {
                        "dim_key": "12_capital_flow",
                        "quality": "partial",
                    },
                },
            },
            "_uzi_execution": {"lane": "pipeline", "fallback_reason": None},
        }
        with TemporaryDirectory() as temporary, patch.object(
            Uzi392FetcherRuntime,
            "collect_symbol",
            autospec=True,
            return_value=symbol_result,
        ) as collect_symbol:
            rows = snapshot_collector.collect_snapshots(
                query="重复请求",
                symbols=["600519"],
                snapshot_types=["capital_flow", "capital_flow", "capital_flow"],
                research_profile="medium",
                cache_root=Path(temporary),
                uzi_root_route=True,
            )

        self.assertEqual([row["snapshot_type"] for row in rows], ["capital_flow"])
        self.assertEqual(rows[0]["facts"]["marker"], "one-symbol-run")
        collect_symbol.assert_called_once()

    def test_snapshot_projection_round_trips_pipeline_and_legacy_bonus_spills(self) -> None:
        fund_dimension = {
            "data": {"active_funds_count": 1},
            "source": "legacy:fetch_fund_holders",
            "fallback": False,
            "_pipeline": {
                "dim_key": "6_fund_holders",
                "quality": "full",
            },
        }
        cases = (
            (
                "pipeline-non-empty",
                {
                    "ticker": "600519",
                    "market": "A",
                    "dimensions": {"6_fund_holders": fund_dimension},
                    "fund_managers": [{"name": "基金A"}],
                    "similar_stocks": [{"ticker": "000858.SZ"}],
                    "_uzi_execution": {"lane": "pipeline", "fallback_reason": None},
                },
            ),
            (
                "pipeline-empty-none",
                {
                    "ticker": "600519",
                    "market": "A",
                    "dimensions": {"6_fund_holders": fund_dimension},
                    "fund_managers": [],
                    "similar_stocks": None,
                    "_uzi_execution": {"lane": "pipeline", "fallback_reason": None},
                },
            ),
            (
                "legacy-non-empty",
                {
                    "ticker": "600519",
                    "market": "A",
                    "dimensions": {},
                    "fund_managers": [{"name": "基金B"}],
                    "similar_stocks": [{"ticker": "601318.SH"}],
                    "_uzi_execution": {
                        "lane": "legacy_forced",
                        "fallback_reason": None,
                    },
                },
            ),
            (
                "legacy-none-empty",
                {
                    "ticker": "600519",
                    "market": "A",
                    "dimensions": {},
                    "fund_managers": None,
                    "similar_stocks": [],
                    "_uzi_execution": {
                        "lane": "legacy_fallback",
                        "fallback_reason": "ValueError",
                    },
                },
            ),
        )

        for label, symbol_result in cases:
            with self.subTest(label=label), TemporaryDirectory() as temporary, patch.object(
                Uzi392FetcherRuntime,
                "collect_symbol",
                autospec=True,
                return_value=symbol_result,
            ):
                rows = snapshot_collector.collect_snapshots(
                    query="bonus spill",
                    symbols=["600519"],
                    snapshot_types=["fund_holders", "similar_stocks"],
                    research_profile="medium",
                    cache_root=Path(temporary),
                    uzi_root_route=True,
                )

            by_type = {row["snapshot_type"]: row for row in rows}
            self.assertEqual(
                by_type["fund_holders"]["facts"]["fund_managers"],
                symbol_result["fund_managers"],
            )
            self.assertEqual(
                by_type["similar_stocks"]["facts"]["similar_stocks"],
                symbol_result["similar_stocks"],
            )
            for snapshot_type, top_level_key in (
                ("fund_holders", "fund_managers"),
                ("similar_stocks", "similar_stocks"),
            ):
                spill = by_type[snapshot_type]["facts"]["_uzi_top_level_fields"]
                self.assertTrue(spill[top_level_key]["present"])
                self.assertEqual(
                    spill[top_level_key]["value"],
                    symbol_result[top_level_key],
                )
                self.assertEqual(
                    by_type[snapshot_type]["facts"]["_uzi_execution"]["lane"],
                    symbol_result["_uzi_execution"]["lane"],
                )

            rebuilt = build_uzi_raw(rows, "600519")
            self.assertEqual(
                rebuilt.get("fund_managers"),
                symbol_result["fund_managers"],
            )
            self.assertEqual(
                rebuilt.get("similar_stocks"),
                symbol_result["similar_stocks"],
            )
            if symbol_result["_uzi_execution"]["lane"] == "pipeline":
                self.assertEqual(
                    rebuilt["dimensions"]["6_fund_holders"],
                    fund_dimension,
                )
            else:
                self.assertNotIn("6_fund_holders", rebuilt["dimensions"])

    def test_project_then_build_preserves_lane_specific_raw_shape(self) -> None:
        dimensions = {
            "0_basic": {
                "data": {"name": "贵州茅台", "ticker": "600519.SH", "price": 1400.0},
                "source": "fixture:basic",
                "fallback": False,
            },
            "1_financials": {
                "data": {"revenue": 100.0},
                "source": "fixture:financials",
                "fallback": False,
            },
            "2_kline": {
                "data": {"history": [{"date": "2026-08-10", "close": 1400.0}]},
                "source": "fixture:kline",
                "fallback": False,
            },
        }
        cases = (
            {
                "ticker": "600519",
                "market": "A",
                "code": "600519",
                "full": "600519.SH",
                "dimensions": dimensions,
                "_uzi_execution": {"lane": "pipeline", "fallback_reason": None},
            },
            {
                "ticker": "600519.SH",
                "market": "A",
                "fetched_at": "2026-08-11T12:34:56",
                "dimensions": dimensions,
                "_uzi_execution": {"lane": "legacy_forced", "fallback_reason": None},
            },
        )

        for symbol_result in cases:
            with self.subTest(lane=symbol_result["_uzi_execution"]["lane"]), patch.object(
                Uzi392FetcherRuntime,
                "collect_symbol",
                autospec=True,
                return_value=symbol_result,
            ):
                rows = snapshot_collector.collect_snapshots(
                    query="raw round trip",
                    symbols=["600519"],
                    snapshot_types=["market", "fundamental", "kline"],
                    research_profile="medium",
                    uzi_root_route=True,
                )

            rebuilt = build_uzi_raw(rows, "600519")
            expected = {
                key: value
                for key, value in symbol_result.items()
                if key != "_uzi_execution"
            }
            self.assertEqual(rebuilt, expected)

    def test_symbol_macro_and_policy_are_not_replaced_by_sector_gaps(self) -> None:
        rows = [
            {
                "snapshot_type": snapshot_type,
                "symbol": "600519",
                "sector": "白酒",
                "facts": {"availability": "available"},
                "quality_status": "available",
            }
            for snapshot_type in ("macro", "policy")
        ]
        completed = subagent_runtime._ensure_requested_snapshot_records(
            rows,
            requested_types=["macro", "policy"],
            symbols=["600519"],
            sector=None,
            intent="stock_check",
        )

        self.assertEqual(completed, rows)
        normalized = [
            snapshot_collector._normalize_snapshot_quality(row)
            for row in completed
        ]
        self.assertTrue(all(row["scope"] == "symbol" for row in normalized))
        self.assertTrue(
            all(
                subagent_runtime._snapshot_artifact_content(row)["scope"] == "symbol"
                for row in completed
            )
        )

    def test_native_source_trace_survives_independent_snapshot_artifacts(self) -> None:
        symbol_result = {
            "ticker": "600519",
            "market": "A",
            "code": "600519",
            "full": "600519.SH",
            "dimensions": {
                "1_financials": {
                    "data": {},
                    "source": "fixture:financials",
                    "fallback": False,
                    "_pipeline": {
                        "dim_key": "1_financials",
                        "quality": "partial",
                    },
                }
            },
            "_uzi_execution": {"lane": "pipeline", "fallback_reason": None},
        }
        with patch.object(
            Uzi392FetcherRuntime,
            "collect_symbol",
            autospec=True,
            return_value=symbol_result,
        ):
            row = snapshot_collector.collect_snapshots(
                query="source trace",
                symbols=["600519"],
                snapshot_types=["fundamental"],
                research_profile="medium",
                uzi_root_route=True,
            )[0]

        # Simulate a recovery stage followed by the independent Snapshot
        # artifact serializer.  Recovery provenance must outrank the original
        # provider trace when the reconstructed native field came from it.
        row["facts"]["revenue"] = 100.0
        row["source_trace"].append(
            {
                "source_id": "ddgs-recovery",
                "provider_id": "uzi_recovery.ddgs",
                "status": "available",
            }
        )
        artifact_row = subagent_runtime._snapshot_artifact_content(row)
        rebuilt = build_uzi_raw([artifact_row], "600519")

        recovery = rebuilt["dimensions"]["1_financials"]["recovery"]
        self.assertEqual(recovery["fields"], ["revenue"])
        self.assertEqual(
            recovery["source_trace"][-1]["provider_id"],
            "uzi_recovery.ddgs",
        )
        source_refs = subagent_runtime._snapshot_artifact_source_refs(row)
        self.assertTrue(
            any(ref["id"] == "ddgs-recovery" for ref in source_refs),
            source_refs,
        )


if __name__ == "__main__":
    unittest.main()
