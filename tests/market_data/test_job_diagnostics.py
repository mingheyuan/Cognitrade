from __future__ import annotations

import unittest

from research_system.market_data.job_diagnostics import (
    classify_collection_failure,
    summarize_fund_portfolio,
    summarize_tasks,
)


class MarketDataJobDiagnosticsTests(unittest.TestCase):
    def test_failure_classifier_separates_raw_contract_gap_from_source_outage(self) -> None:
        normalized = classify_collection_failure(
            error_code="strict_contract_gap",
            error_message="field ohlcv has 60 items; requires at least 260",
            provider_status="available",
            metrics={"raw_data_present": True},
        )
        timeout = classify_collection_failure(
            error_code="provider_error",
            error_message="fetcher timeout > 150s",
            provider_status="provider_error",
        )
        empty_contract_gap = classify_collection_failure(
            error_code="strict_contract_gap",
            error_message="missing normalized field: prices",
            provider_status="data_gap",
            metrics={"raw_data_present": False},
        )

        self.assertEqual(normalized["failure_kind"], "audit_only")
        self.assertEqual(normalized["failure_label"], "UZI 可用 · 扩展审计提示")
        self.assertEqual(timeout["failure_kind"], "source_timeout")
        self.assertEqual(empty_contract_gap["failure_kind"], "source_unavailable")

    def test_task_summary_separates_retryable_and_contract_blocked_gaps(self) -> None:
        tasks = [
            {
                "status": "failed",
                "dimension": "12_capital_flow",
                "retryable": True,
                "attempt_count": 1,
                "max_attempts": 3,
                "last_error_code": "provider_error",
            },
            {
                "status": "failed",
                "dimension": "3_financials",
                "retryable": False,
                "attempt_count": 1,
                "max_attempts": 3,
                "last_error_code": "strict_contract_gap",
            },
            {"status": "succeeded", "dimension": "0_basic"},
        ]

        summary = summarize_tasks(tasks)

        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["retryable_failed"], 1)
        self.assertEqual(summary["blocked_failed"], 0)
        self.assertEqual(summary["audit_only"], 1)
        self.assertEqual(summary["succeeded"], 2)
        self.assertEqual(summary["by_error_code"][0]["count"], 1)

    def test_fund_portfolio_uses_latest_child_wave_for_strict_slot_counts(self) -> None:
        dimensions = [f"dimension_{index}" for index in range(22)]
        holdings = [{"symbol": f"60000{index}.SH"} for index in range(10)]
        all_slots = [
            (holding["symbol"], dimension)
            for holding in holdings
            for dimension in dimensions
        ]
        missing_slots = all_slots[23:]
        missing_by_symbol: dict[str, list[str]] = {}
        for symbol, dimension in missing_slots:
            missing_by_symbol.setdefault(symbol, []).append(dimension)
        tasks = [
            {
                "job_id": "latest-child",
                "symbol": symbol,
                "dimension": dimension,
                "status": "failed",
                "retryable": index < 134,
                "attempt_count": 1,
                "max_attempts": 3,
                "last_error_code": (
                    "provider_degraded" if index < 134 else "strict_contract_gap"
                ),
            }
            for index, (symbol, dimension) in enumerate(missing_slots)
        ]
        tasks.append(
            {
                "job_id": "old-child",
                "symbol": missing_slots[0][0],
                "dimension": missing_slots[0][1],
                "status": "failed",
                "retryable": False,
                "attempt_count": 2,
                "max_attempts": 3,
            }
        )

        summary = summarize_fund_portfolio(
            checkpoint={
                "holdings": holdings,
                "child_total_count": 10,
                "child_complete_count": 0,
                "child_missing_by_symbol": missing_by_symbol,
                "last_child_runs": [{"job_id": "latest-child", "attempt": 2}],
            },
            child_tasks=tasks,
            dimensions_per_holding=22,
        )

        self.assertEqual(summary["total_slots"], 220)
        self.assertEqual(summary["ready_slots"], 23)
        self.assertEqual(summary["missing_slots"], 197)
        self.assertEqual(summary["retryable_slots"], 134)
        self.assertEqual(summary["blocked_slots"], 63)
        self.assertEqual(summary["attempt"], 2)

    def test_fund_portfolio_counts_running_child_slots_before_parent_settles(self) -> None:
        dimensions = [f"dimension_{index}" for index in range(22)]
        holdings = [{"symbol": f"60000{index}.SH"} for index in range(10)]
        tasks = [
            {
                "job_id": "running-child",
                "symbol": holding["symbol"],
                "dimension": dimension,
                "status": "running",
            }
            for holding in holdings
            for dimension in dimensions
        ]

        summary = summarize_fund_portfolio(
            checkpoint={
                "holdings": holdings,
                "child_total_count": 10,
                "child_complete_count": 0,
                "child_job_ids": ["running-child"],
                "child_runs": [
                    {
                        "job_id": "running-child",
                        "status": "running",
                        "attempt": 1,
                    }
                ],
            },
            child_tasks=tasks,
            dimensions_per_holding=22,
        )

        self.assertEqual(summary["ready_slots"], 0)
        self.assertEqual(summary["missing_slots"], 220)
        self.assertEqual(summary["active_slots"], 220)
        self.assertEqual(summary["retryable_slots"], 0)
        self.assertEqual(summary["blocked_slots"], 0)
        self.assertEqual(summary["current_child_job_ids"], ["running-child"])

    def test_scoped_child_wave_preserves_other_holdings_retryability(self) -> None:
        holdings = [{"symbol": f"60000{index}.SH"} for index in range(10)]
        untouched_symbol = holdings[1]["symbol"]
        summary = summarize_fund_portfolio(
            checkpoint={
                "holdings": holdings,
                "child_total_count": 10,
                "child_complete_count": 9,
                "child_missing_by_symbol": {untouched_symbol: ["0_basic"]},
                "last_child_runs": [{"job_id": "scoped-child", "attempt": 2}],
            },
            child_tasks=[
                {
                    "job_id": "initial-child",
                    "symbol": untouched_symbol,
                    "dimension": "0_basic",
                    "status": "failed",
                    "retryable": True,
                    "attempt_count": 1,
                    "max_attempts": 3,
                },
                {
                    "job_id": "scoped-child",
                    "symbol": holdings[0]["symbol"],
                    "dimension": "0_basic",
                    "status": "succeeded",
                },
            ],
            dimensions_per_holding=22,
        )

        self.assertEqual(summary["missing_slots"], 1)
        self.assertEqual(summary["retryable_slots"], 1)
        self.assertEqual(summary["blocked_slots"], 0)


if __name__ == "__main__":
    unittest.main()
