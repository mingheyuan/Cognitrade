from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import patch

import pandas as pd

from domain_packs.finance_ashare.tools.snapshot_collector import (
    _point_in_time_uzi_projection,
)
from research_system.adapters.vendors.akshare_fundamental_adapter import (
    AkShareFundamentalAdapter,
)
from research_system.adapters.vendors.akshare_lhb_adapter import AkShareLHBAdapter
from research_system.adapters.vendors.akshare_market_adapter import AkShareMarketAdapter
from research_system.adapters.vendors.efinance_capital_flow_adapter import (
    EFinanceCapitalFlowAdapter,
)
from research_system.market_data.uzi_adapter import UziSnapshotAdapter


def test_market_valuation_uses_last_row_on_or_before_cutoff() -> None:
    frame = pd.DataFrame(
        [
            {"数据日期": "2026-09-01", "PE(TTM)": 79.36, "市净率": 11.18},
            {"数据日期": "2026-09-02", "PE(TTM)": 77.85, "市净率": 10.90},
        ]
    )
    with patch(
        "research_system.adapters.vendors.akshare_market_adapter.call_with_retry",
        return_value=frame,
    ):
        latest, history = AkShareMarketAdapter()._get_valuation_data(
            "688012", as_of_date=date(2026, 9, 1)
        )

    assert latest["数据日期"] == "2026-09-01"
    assert latest["PE(TTM)"] == 79.36
    assert [row["date"] for row in history] == ["2026-09-01"]


def test_financial_period_is_kept_only_after_conservative_disclosure_deadline() -> None:
    frame = pd.DataFrame(
        [
            {"报告期": "2025", "净利润": 10},
            {"报告期": "2026", "净利润": 20},
        ]
    )

    september = AkShareFundamentalAdapter._filter_frame_as_of(frame, date(2026, 9, 1))
    next_may = AkShareFundamentalAdapter._filter_frame_as_of(frame, date(2027, 5, 1))

    assert september["报告期"].tolist() == ["2025"]
    assert next_may["报告期"].tolist() == ["2025", "2026"]


def test_efinance_history_drops_rows_after_cutoff() -> None:
    rows = [
        {"日期": "2026-09-01", "主力净流入": "100"},
        {"日期": "2026-09-02", "主力净流入": "200"},
    ]
    with patch.object(
        EFinanceCapitalFlowAdapter,
        "_bounded_history_rows",
        return_value=rows,
    ):
        result = EFinanceCapitalFlowAdapter().get_capital_flow_snapshot(
            symbol="688012.SH", as_of_date=date(2026, 9, 1)
        )

    assert result["latest_flow"]["date"] == "2026-09-01"
    assert len(result["main_fund_flow_20d"]) == 1


def test_lhb_successful_empty_window_is_verified_empty() -> None:
    date_frame = pd.DataFrame([{"交易日": "2026-07-01"}])
    with patch(
        "research_system.adapters.vendors.akshare_lhb_adapter.call_with_retry",
        return_value=date_frame,
    ):
        result = AkShareLHBAdapter().get_lhb_snapshot(
            symbol="688012", as_of_date=date(2026, 9, 1)
        )

    assert result["observation_status"] == "verified_empty"
    assert result["lhb_count_30d"] == 0


def test_historical_valuation_projection_preserves_target_multiples() -> None:
    projected = _point_in_time_uzi_projection(
        "valuation",
        {
            "valuation": {
                "PE(TTM)": 79.36,
                "市净率": 11.18,
                "history": [
                    {"date": "2026-08-29", "pe_ttm": 70, "pb": 10},
                    {"date": "2026-09-01", "pe_ttm": 79.36, "pb": 11.18},
                ],
            }
        },
    )

    assert projected["pe"] == 79.36
    assert projected["pb"] == 11.18
    assert projected["pe_quantile"] == 100
    assert projected["pb_quantile"] == 100


def test_historical_adapter_distinguishes_partial_and_retryable_failure() -> None:
    adapter = UziSnapshotAdapter()
    common = {
        "snapshot_type": "market",
        "symbol": "688012.SH",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "historical_reconstruction": {
            "safe": True,
            "strategy": "dated_series",
            "requested_as_of_date": "2026-09-01",
            "window_start": "2025-03-01",
            "window_end": "2026-09-01",
        },
        "source": {"provider_id": "test.market", "chain_position": 0},
    }
    partial = adapter.adapt(
        {
            **common,
            "quality_status": "degraded",
            "facts": {
                "availability": "degraded",
                "_uzi_raw_result": {
                    "data": {
                        "code": "688012.SH",
                        "name": "中微公司",
                        "price": 350.52,
                        "market_cap_raw": 335_742_899_820.96,
                    }
                },
            },
            "source_trace": [{"source_id": "akshare", "status": "available"}],
        },
        symbol="688012.SH",
        market="CN_EQUITY",
        exchange_timezone="Asia/Shanghai",
        as_of_date=date(2026, 9, 1),
    )
    failed = adapter.adapt(
        {
            **common,
            "quality_status": "data_gap",
            "facts": {"availability": "data_gap"},
            "source_trace": [
                {"source_id": "efinance", "status": "provider_unavailable"}
            ],
        },
        symbol="688012.SH",
        market="CN_EQUITY",
        exchange_timezone="Asia/Shanghai",
        as_of_date=date(2026, 9, 1),
    )

    assert partial.query_manifest["collection_outcome"] == "partial"
    assert (
        partial.query_manifest["partial_disclosure"]["excluded_from_scoring"] is False
    )
    assert "degradation" not in partial.query_manifest
    assert failed.query_manifest["collection_outcome"] == "retryable_failure"
    assert "degradation" not in failed.query_manifest


def test_historical_outcome_uses_normalized_fields_instead_of_native_quality_label() -> (
    None
):
    result = UziSnapshotAdapter().adapt(
        {
            "snapshot_type": "fundamental",
            "symbol": "688012.SH",
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "quality_status": "degraded",
            "facts": {
                "availability": "degraded",
                "_uzi_raw_result": {
                    "data": {
                        "financial_years": ["2024", "2025"],
                        "roe_history": [20.0, 21.0],
                        "revenue_history": [100.0, 120.0],
                        "net_profit_history": [10.0, 12.0],
                        "financial_health": {"debt_ratio": 0.3},
                    }
                },
            },
            "source_trace": [{"source_id": "akshare", "status": "available"}],
            "historical_reconstruction": {
                "safe": True,
                "strategy": "published_archive",
                "requested_as_of_date": "2026-09-01",
                "window_start": "2016-09-03",
                "window_end": "2026-09-01",
            },
        },
        symbol="688012.SH",
        market="CN_EQUITY",
        exchange_timezone="Asia/Shanghai",
        as_of_date=date(2026, 9, 1),
    )

    assert result.query_manifest["collection_outcome"] == "available"
    assert result.query_manifest["field_coverage"]["missing_fields"] == []


def test_historical_verified_empty_is_a_real_empty_snapshot_with_bounded_proof() -> (
    None
):
    result = UziSnapshotAdapter().adapt(
        {
            "snapshot_type": "lhb",
            "symbol": "688012.SH",
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "quality_status": "available",
            "facts": {
                "availability": "available",
                "_uzi_raw_result": {
                    "data": {
                        "observation_status": "verified_empty",
                        "lhb_count_30d": 0,
                        "lhb_records": [],
                    }
                },
            },
            "source_trace": [{"source_id": "akshare_lhb", "status": "available"}],
            "historical_reconstruction": {
                "safe": True,
                "strategy": "dated_series",
                "requested_as_of_date": "2026-09-01",
                "window_start": "2026-08-02",
                "window_end": "2026-09-01",
            },
        },
        symbol="688012.SH",
        market="CN_EQUITY",
        exchange_timezone="Asia/Shanghai",
        as_of_date=date(2026, 9, 1),
    )

    assert result.status.value == "verified_empty"
    assert result.payload == {}
    assert result.query_manifest["required_sources"] == ["akshare_lhb"]
    assert result.query_manifest["sources"][0]["status"] == "success"
