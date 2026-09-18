from __future__ import annotations

import unittest

from research_system.market_data.job_diagnostics import (
    classify_collection_failure,
    fund_gap_dimensions,
    fund_retryable_scope,
    project_fund_parent_status,
    summarize_fund_portfolio,
    summarize_tasks,
    task_is_retryable,
)


class MarketDataJobDiagnosticsTests(unittest.TestCase):
    def test_candidate_only_gaps_are_in_every_breakdown_with_identical_retryability(self):
        symbol = "688012.SH"
        dimensions = ["19_contests", "13_policy"]
        summary = summarize_fund_portfolio(
            checkpoint={"holdings": [{"symbol": symbol}], "publication_candidate": {
                "bundles": [{"symbol": symbol, "items": [{"dimension": d, "status": "data_gap"} for d in dimensions]}],
            }}, child_tasks=[], dimension_ids=dimensions, dimensions_per_holding=2, top_n=1,
        )
        self.assertEqual(summary["missing_slots"], 2)
        self.assertEqual(summary["retryable_slots"], 1)
        self.assertEqual(summary["blocked_slots"], 1)
        for key in ("by_dimension", "by_error_code"):
            self.assertEqual(sum(row["count"] for row in summary[key]), 2)
            self.assertEqual(sum(row["retryable_count"] for row in summary[key]), 1)
            self.assertEqual(sum(row["blocked_count"] for row in summary[key]), 1)

    def test_fund_breakdown_does_not_truncate_at_twelve_dimensions(self):
        symbol = "688012.SH"
        dimensions = [f"dimension_{n}" for n in range(22)]
        tasks = [{"symbol": symbol, "dimension": d, "status": "failed", "last_error_code": f"error-{d}",
                  "retryable": False} for d in dimensions]
        summary = summarize_fund_portfolio(
            checkpoint={"holdings": [{"symbol": symbol}]}, child_tasks=tasks,
            dimension_ids=dimensions, dimensions_per_holding=22, top_n=1,
        )
        for key in ("by_dimension", "by_error_code"):
            self.assertEqual(len(summary[key]), 22)
            self.assertEqual(sum(row["count"] for row in summary[key]), summary["missing_slots"])

    def test_unscheduled_portfolio_slots_are_unassessed_not_ready_or_blocked(self):
        summary = summarize_fund_portfolio(
            checkpoint={
                "holdings": [{"symbol": f"60000{index}.SH"} for index in range(10)]
            },
            child_tasks=[],
            dimensions_per_holding=22,
        )
        self.assertEqual(summary["ready_slots"], 0)
        self.assertEqual(summary["ready_holdings"], 0)
        self.assertEqual(summary["missing_slots"], 0)
        self.assertEqual(summary["blocked_slots"], 0)
        self.assertEqual(summary["unassessed_slots"], 220)

    def test_planned_gap_without_dispatched_task_is_not_a_blocked_result(self):
        summary = summarize_fund_portfolio(
            checkpoint={
                "holdings": [{"symbol": "688012.SH", "missing_dimensions": ["2_kline"]}]
            },
            child_tasks=[],
            dimensions_per_holding=22,
        )
        self.assertEqual(summary["unassessed_slots"], 220)
        self.assertEqual(summary["blocked_slots"], 0)
        self.assertEqual(summary["missing_slots"], 0)

    def test_previous_result_cannot_settle_a_new_attempt(self):
        task = {
            "id": "task",
            "symbol": "688012.SH",
            "dimension": "2_kline",
            "status": "succeeded",
            "attempt_count": 2,
        }
        result = {
            "task_id": "task",
            "status": "succeeded",
            "snapshot_id": "old",
            "attempt": 1,
        }
        summary = summarize_fund_portfolio(
            checkpoint={"holdings": [{"symbol": "688012.SH"}]},
            child_tasks=[task],
            child_results=[result],
            dimensions_per_holding=22,
        )
        self.assertEqual(summary["ready_slots"], 0)
        self.assertEqual(summary["unassessed_slots"], 220)

    def test_persisted_retry_success_supersedes_pre_retry_gap_list(self):
        summary = summarize_fund_portfolio(
            checkpoint={
                "holdings": [{"symbol": "688012.SH"}],
                "child_missing_by_symbol": {"688012.SH": ["2_kline"]},
            },
            child_tasks=[
                {
                    "id": "t",
                    "symbol": "688012.SH",
                    "dimension": "2_kline",
                    "status": "succeeded",
                    "attempt_count": 1,
                }
            ],
            child_results=[
                {
                    "task_id": "t",
                    "attempt": 1,
                    "status": "succeeded",
                    "snapshot_id": "new",
                }
            ],
            dimensions_per_holding=22,
        )
        self.assertEqual(summary["ready_slots"], 1)
        self.assertEqual(summary["missing_slots"], 0)
        self.assertEqual(summary["unassessed_slots"], 219)

    def test_successful_task_can_reuse_a_degraded_result_without_becoming_ready(self):
        symbol = "688012.SH"
        tasks = [
            {
                "id": "t1",
                "job_id": "child",
                "symbol": symbol,
                "dimension": "19_contests",
                "status": "succeeded",
                "attempt_count": 1,
            }
        ]
        results = [
            {
                "task_id": "t1",
                "attempt": 1,
                "status": "skipped",
                "snapshot_id": "gap",
                "provider_status": "existing_degraded_resolution",
                "metrics_json": {"degraded": True, "collection_outcome": "unavailable"},
            }
        ]
        summary = summarize_fund_portfolio(
            checkpoint={"holdings": [{"symbol": symbol}], "child_total_count": 1},
            child_tasks=tasks,
            child_results=results,
            dimensions_per_holding=22,
        )
        self.assertEqual(summary["ready_slots"], 0)
        self.assertEqual(summary["missing_slots"], 1)
        self.assertEqual(summary["degraded_slots"], 1)
        self.assertEqual(summary["blocked_slots"], 1)
        self.assertEqual(summary["unassessed_slots"], 219)
        self.assertEqual(summary["by_holding"][0]["unassessed_slots"], 21)
        self.assertEqual(summary["by_dimension"][0]["count"], 1)
        self.assertEqual(summary["by_dimension"][0]["blocked_count"], 1)
        self.assertEqual(summary["by_error_code"][0]["count"], 1)

    def test_only_persisted_positive_results_count_as_ready_before_candidate(self):
        holdings = [{"symbol": f"60000{index}.SH"} for index in range(10)]
        tasks = [
            {
                "id": "t1",
                "symbol": holdings[0]["symbol"],
                "dimension": "0_basic",
                "status": "succeeded",
                "attempt_count": 1,
            },
            {
                "id": "t2",
                "symbol": holdings[0]["symbol"],
                "dimension": "2_kline",
                "status": "running",
                "attempt_count": 1,
            },
        ]
        results = [
            {
                "task_id": "t1",
                "attempt": 1,
                "status": "skipped",
                "snapshot_id": "ready",
                "provider_status": "existing_report_ready",
                "metrics_json": {"degraded": False, "collection_outcome": "available"},
            }
        ]
        summary = summarize_fund_portfolio(
            checkpoint={"holdings": holdings},
            child_tasks=tasks,
            child_results=results,
            dimensions_per_holding=22,
        )
        self.assertEqual(summary["ready_slots"], 1)
        self.assertEqual(summary["active_slots"], 1)
        self.assertEqual(summary["missing_slots"], 0)
        self.assertEqual(summary["unassessed_slots"], 218)
        self.assertEqual(summary["ready_holdings"], 0)

    def test_scoped_retry_does_not_hide_non_retryable_gaps(self):
        self.assertEqual(
            fund_gap_dimensions(
                {
                    "retry_scope_by_symbol": {"688012.SH": ["12_capital_flow"]},
                    "child_missing_by_symbol": {
                        "688012.SH": ["12_capital_flow", "19_contests"]
                    },
                }
            ),
            {"688012.SH": ["12_capital_flow", "19_contests"]},
        )

    def test_legacy_chain_and_industry_gaps_become_retryable_once_after_upgrade(
        self,
    ) -> None:
        legacy = {
            "status": "partial",
            "dimension": "5_chain",
            "retryable": False,
            "attempt_count": 3,
            "max_attempts": 3,
            "last_error_code": "provider_data_gap",
            "checkpoint_json": {},
        }
        after_upgrade_attempt = {
            **legacy,
            "dimension": "7_industry",
            "checkpoint_json": {
                "historical_provider_revision": "cninfo-industry-pit-v1"
            },
        }

        self.assertTrue(task_is_retryable(legacy))
        self.assertTrue(task_is_retryable({**legacy, "dimension": "7_industry"}))
        self.assertFalse(task_is_retryable(after_upgrade_attempt))

    def test_failure_classifier_separates_raw_contract_gap_from_source_outage(
        self,
    ) -> None:
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

    def test_failed_historical_request_is_not_an_audit_hint_when_raw_is_saved(self):
        cases = [
            ("historical_provider_failure", "provider_unavailable", {}),
            ("strict_contract_gap", "provider_unavailable", {}),
            (
                "strict_contract_gap",
                "available",
                {"collection_outcome": "retryable_failure"},
            ),
            (None, None, {"reason_code": "historical_provider_failure"}),
            (None, "rate_limited", {}),
            (None, "parse_failed", {}),
        ]
        for code, provider, metrics in cases:
            with self.subTest(code=code, provider=provider, metrics=metrics):
                classified = classify_collection_failure(
                    error_code=code,
                    error_message="em_push2: provider_unavailable (ConnectionError)",
                    provider_status=provider,
                    metrics={"raw_data_present": True, **metrics},
                )

                self.assertEqual(classified["failure_kind"], "source_unavailable")
                self.assertEqual(classified["failure_stage"], "acquisition")
                self.assertNotIn("UZI 可用", classified["failure_label"])
                self.assertTrue(classified["raw_data_present"])

    def test_optional_source_failure_does_not_override_available_aggregate(self):
        classified = classify_collection_failure(
            error_code="historical_partial",
            error_message="核心数据可用；optional provider timeout",
            provider_status="degraded",
            metrics={
                "raw_data_present": True,
                "collection_outcome": "partial",
                "source_trace": [{"source_id": "optional", "status": "timeout"}],
            },
        )

        self.assertEqual(classified["failure_kind"], "partial_available")

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

    def test_historical_partial_is_usable_and_neither_retryable_nor_blocked(
        self,
    ) -> None:
        task = {
            "status": "partial",
            "dimension": "10_valuation",
            "retryable": False,
            "attempt_count": 1,
            "max_attempts": 3,
            "last_error_code": "historical_partial",
            "last_error_message": "核心 UZI 数据可用；历史分位样本不足",
        }

        summary = summarize_tasks([task])
        classified = classify_collection_failure(
            error_code="historical_partial",
            error_message=task["last_error_message"],
            provider_status="degraded",
            metrics={"raw_data_present": True},
        )

        self.assertEqual(summary["succeeded"], 1)
        self.assertEqual(summary["audit_only"], 1)
        self.assertEqual(summary["retryable_failed"], 0)
        self.assertEqual(summary["blocked_failed"], 0)
        self.assertEqual(classified["failure_kind"], "partial_available")

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
        # No frozen candidate or positive results prove the other 23 slots.
        self.assertEqual(summary["ready_slots"], 0)
        self.assertEqual(summary["unassessed_slots"], 23)
        self.assertEqual(summary["missing_slots"], 197)
        self.assertEqual(summary["retryable_slots"], 134)
        self.assertEqual(summary["blocked_slots"], 63)
        self.assertEqual(summary["attempt"], 2)

    def test_fund_portfolio_counts_running_child_slots_before_parent_settles(
        self,
    ) -> None:
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
        self.assertEqual(summary["missing_slots"], 0)
        self.assertEqual(summary["unassessed_slots"], 0)
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

    def test_publication_candidate_separates_report_readiness_from_retryable_gaps(
        self,
    ) -> None:
        checkpoint = {
            "holdings": [{"symbol": "600001.SH"}, {"symbol": "600002.SH"}],
            "child_total_count": 2,
            "child_job_ids": ["child-1"],
            "publication_candidate": {
                "bundles": [
                    {
                        "asset_role": "holding",
                        "symbol": "600001.SH",
                        "missing_dimensions": ["12_capital_flow", "19_contests"],
                    },
                    {
                        "asset_role": "holding",
                        "symbol": "600002.SH",
                        "missing_dimensions": [],
                    },
                ],
                "missing_dimensions": [
                    "600001.SH:12_capital_flow",
                    "600001.SH:19_contests",
                ],
            },
        }
        tasks = [
            {
                "job_id": "child-1",
                "symbol": "600001.SH",
                "dimension": "12_capital_flow",
                "status": "failed",
                "retryable": True,
                "attempt_count": 1,
                "max_attempts": 3,
                "last_error_code": "historical_provider_failure",
            },
            {
                "job_id": "child-1",
                "symbol": "600001.SH",
                "dimension": "19_contests",
                "status": "partial",
                "retryable": False,
                "attempt_count": 1,
                "max_attempts": 3,
                "last_error_code": "historical_unavailable",
            },
            {
                "job_id": "child-1",
                "symbol": "600001.SH",
                "dimension": "11_governance",
                "status": "partial",
                "retryable": True,
                "attempt_count": 1,
                "max_attempts": 3,
                "last_error_code": "historical_partial",
            },
        ]

        summary = summarize_fund_portfolio(
            checkpoint=checkpoint,
            child_tasks=tasks,
            dimensions_per_holding=22,
            top_n=2,
        )
        retry_scope = fund_retryable_scope(
            checkpoint=checkpoint,
            child_tasks=tasks,
        )

        self.assertEqual(summary["ready_slots"], 42)
        self.assertEqual(summary["missing_slots"], 2)
        self.assertEqual(summary["retryable_slots"], 1)
        self.assertEqual(summary["blocked_slots"], 1)
        self.assertEqual(
            retry_scope,
            {"600001.SH": ["12_capital_flow"]},
        )
        self.assertEqual(
            project_fund_parent_status(
                status="succeeded",
                checkpoint=checkpoint,
            ),
            "partial",
        )


if __name__ == "__main__":
    unittest.main()
