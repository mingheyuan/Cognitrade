from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from domain_packs.finance_ashare.vendor.uzi_3_9_2 import (
    UZI_COMMAND_IDS,
    UZI_FETCHER_IDS,
    Uzi392FetcherRuntime,
    verify_vendored_uzi_integrity,
)
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
        self.assertEqual(report["python_file_count"], 134)
        self.assertEqual(report["command_count"], 20)
        self.assertTrue(report["python_tree_matches"])
        self.assertTrue(report["command_tree_matches"])
        self.assertTrue(report["report_template_matches"])
        self.assertEqual(report["analyst_asset_file_count"], 138)
        self.assertTrue(report["analyst_asset_tree_matches"])
        self.assertIn("quick-scan", UZI_COMMAND_IDS)
        self.assertEqual(len(UZI_FETCHER_IDS), 22)

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
                elif fetcher_id == "fund_holders":
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

    def test_unknown_fetcher_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported UZI 3.9.2 fetcher"):
            self.runtime.collect("missing", ticker="600519")

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

        with patch.object(Uzi392FetcherRuntime, "collect", autospec=True) as mocked:
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
        self.assertTrue(
            all(
                isinstance((row.get("facts") or {}).get("_uzi_raw_result"), dict)
                for row in rows
            )
        )


if __name__ == "__main__":
    unittest.main()
