from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from domain_packs.finance_ashare.tools.uzi_data_recovery import (
    _apply_qualitative_action,
    recover_suggested_fixes_until_exhausted,
)
from research_system.runtime.platform.settings import Settings


class UziStrictRecoveryTests(unittest.TestCase):
    def test_suggested_fix_loop_continues_beyond_three_actions_without_round_limit(self) -> None:
        progress = {"executed": 0}
        actions = [f"ws: '真实来源 {index}'" for index in range(5)]

        def integrity(_snapshots, _symbols):
            done = progress["executed"] >= len(actions)
            task = {
                "dim": "0_basic",
                "field": "price",
                "severity": "critical",
                "suggested_actions": actions,
            }
            return {
                "symbols": [{
                    "symbol": "600519",
                    "critical_missing": not done,
                    "coverage_pct": 100 if done else 20,
                    "recovery_tasks": [] if done else [task],
                }],
                "critical_missing": not done,
                "coverage_pct": 100 if done else 20,
            }

        def execute(*_args, **_kwargs):
            progress["executed"] += 1
            return {"stage": "official_web_ddgs", "status": "available", "source_id": "test"}

        settings = Settings.from_env(Path(__file__).resolve().parents[1])
        with patch(
            "domain_packs.finance_ashare.tools.uzi_data_recovery._exact_integrity",
            side_effect=integrity,
        ), patch(
            "domain_packs.finance_ashare.tools.uzi_data_recovery._execute_recovery_action",
            side_effect=execute,
        ):
            result = recover_suggested_fixes_until_exhausted(
                [], query="测试", symbols=["600519"], profile="deep", settings=settings,
            )

        self.assertEqual(result["attempted"], 5)
        self.assertEqual(len(result["cycles"]), 5)
        self.assertTrue(result["critical_cleared"])
        self.assertIsNone(result["round_limit"])

    def test_unstructured_search_never_fills_numeric_or_identity_fields(self) -> None:
        snapshot = {
            "snapshot_type": "market",
            "symbol": "600519",
            "facts": {
                "availability": "data_gap",
                "_uzi_raw_result": {"ticker": "600519", "data": {}, "source": "test"},
            },
            "degraded": True,
        }

        applied = _apply_qualitative_action(
            snapshot,
            "market",
            "网页声称股价为 9999 元",
            "ddgs",
            "600519 最新价",
        )

        self.assertFalse(applied)
        self.assertNotIn("price", snapshot["facts"])
        self.assertEqual(snapshot["facts"]["_uzi_raw_result"]["data"], {})

    def test_qualitative_recovery_is_explicitly_disclosed(self) -> None:
        snapshot = {
            "snapshot_type": "macro",
            "symbol": "600519",
            "facts": {
                "availability": "data_gap",
                "_uzi_raw_result": {"ticker": "600519", "data": {}, "source": "test"},
            },
            "degraded": True,
        }

        applied = _apply_qualitative_action(
            snapshot,
            "macro",
            "公开信息显示利率环境处于观察期",
            "ddgs",
            "利率周期",
            results=[{"title": "权威来源", "url": "https://example.test"}],
        )

        self.assertTrue(applied)
        evidence = snapshot["facts"]["recovery_evidence"][0]
        self.assertEqual(evidence["disclosure"], "基于公开信息推断，非一手数据")
        self.assertEqual(evidence["source_id"], "ddgs")


if __name__ == "__main__":
    unittest.main()
