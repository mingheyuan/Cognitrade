from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from research_system.adapters.vendors import baostock_market_adapter
from research_system.adapters.vendors.baostock_socket_safety import baostock_session
from research_system.market_data.collection import MarketDataCollectionCoordinator
from research_system.market_data.ingestion import SnapshotIngestionService
from research_system.market_data.kline_price_evidence import (
    BaoStockPriceEvidenceCollector,
    enrich_kline_snapshot,
    has_complete_price_evidence,
)
from research_system.market_data.models import MarketDataSnapshot
from research_system.market_data.repository import MarketDataRepository
from research_system.market_data.uzi_adapter import UziSnapshotAdapter
from research_system.strategies.backtesting.instruments import InstrumentResolver
from research_system.strategies.backtesting.market_data_gateway import MarketDataGateway
from research_system.strategies.database import StrategyDatabase
from tests.market_data.test_pit_core import AS_OF, _input, _payload


class Result:
    def __init__(self, rows=(), *, error_code="0"):
        self.rows = list(rows)
        self.error_code = error_code
        self.fields = list(self.rows[0]) if self.rows else []
        self.index = 0

    def next(self):
        return self.index < len(self.rows)

    def get_row_data(self):
        row = self.rows[self.index]
        self.index += 1
        return [row[key] for key in self.fields]


def _client():
    return SimpleNamespace(
        login=Mock(return_value=SimpleNamespace(error_code="0")),
        logout=Mock(),
        query_dividend_data=Mock(side_effect=lambda **_: Result()),
        query_adjust_factor=Mock(side_effect=lambda **_: Result()),
    )


def _snapshot():
    raw = {
        **_payload("2_kline")["raw"],
        "ohlcv": _payload("2_kline")["ohlcv"],
        "ohlcv_adjustment": "none",
        "indicators": {"ma20": 123},
        "candles_60d": [{"close": 123}],
    }
    return {
        "symbol": "600519.SH",
        "snapshot_type": "kline",
        "quality_status": "available",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_date": AS_OF.isoformat(),
        "facts": {
            "availability": "available",
            "_uzi_raw_result": {"data": raw, "source": "fixture.native"},
        },
        "source": {"provider_id": "fixture.native"},
        "source_trace": [
            {"provider_id": "fixture.native", "status": "available", "count": 260}
        ],
        "historical_reconstruction": {
            "safe": True,
            "requested_as_of_date": AS_OF.isoformat(),
            "strategy": "dated_series",
            "window_start": "2025-01-01",
        },
    }


def test_exact_sdk_parameters_zero_is_proven_and_authenticated_client_reused():
    client = _client()
    collector = BaoStockPriceEvidenceCollector(client=client)
    result = collector.collect(
        symbol="600519.SH", start_date=date(2025, 12, 1), end_date=AS_OF
    )
    assert result["status"] == "available"
    assert result["corporate_actions"] == result["adjustment_factors"] == []
    assert [call.kwargs for call in client.query_dividend_data.call_args_list] == [
        {"code": "sh.600519", "year": "2025", "yearType": "operate"},
        {"code": "sh.600519", "year": "2026", "yearType": "operate"},
    ]
    client.query_adjust_factor.assert_called_once_with(
        code="sh.600519", start_date="2025-12-01", end_date="2026-08-20"
    )
    collector.collect(symbol="000001.SZ", start_date=AS_OF, end_date=AS_OF)
    client.login.assert_called_once()
    client.logout.assert_not_called()
    for proof in result["field_queries"].values():
        assert proof["query_scope"] == "field"
        assert proof["result_count"] == 0
        assert all(
            row["status"] == "success" and row["result_count"] == 0
            for row in proof["sources"]
        )


def test_historical_filter_uses_operation_day_not_year_or_future_announcement():
    client = _client()
    client.query_dividend_data.side_effect = lambda **_: Result(
        [
            {"dividOperateDate": "2026-08-19", "dividPlanAnnounceDate": "2026-04-01"},
            {"dividOperateDate": "2026-09-01", "dividPlanAnnounceDate": "2026-08-21"},
            {"dividOperateDate": "2026-01-01", "dividPlanAnnounceDate": "2025-10-01"},
        ]
    )
    client.query_adjust_factor.side_effect = lambda **_: Result(
        [
            {"dividOperateDate": "2026-08-20", "adjustFactor": "1.02"},
            {"dividOperateDate": "2026-09-01", "adjustFactor": "1.1"},
        ]
    )
    result = BaoStockPriceEvidenceCollector(client=client).collect(
        symbol="600519.SH", start_date=date(2026, 8, 1), end_date=AS_OF
    )
    assert [row["date"] for row in result["corporate_actions"]] == [
        "2026-08-19",
        "2026-08-20",
    ]
    assert result["adjustment_factors"][0]["factor"] == 1.02
    assert len(result["adjustment_factors"]) == 1
    assert "2026-09-01" not in str(result)
    assert result["corporate_actions"][-1]["type"] == "adjustment_event"


@pytest.mark.parametrize(
    "failure,status",
    [
        (TimeoutError("transport stalled"), "timeout"),
        (RuntimeError("source unavailable"), "provider_unavailable"),
    ],
)
def test_failed_queries_never_become_empty_success(failure, status):
    client = _client()
    client.query_dividend_data.side_effect = failure
    result = BaoStockPriceEvidenceCollector(client=client).collect(
        symbol="600519.SH", start_date=AS_OF, end_date=AS_OF
    )
    assert result["status"] == "partial" and result["retryable"]
    source = result["field_queries"]["corporate_actions"]["sources"][0]
    assert source["status"] == status and source["result_count"] is None
    assert source["error_reason"] == type(failure).__name__


def test_source_error_and_undated_records_remain_unavailable():
    client = _client()
    client.query_dividend_data.side_effect = lambda **_: Result(
        [{"dividOperateDate": ""}]
    )
    client.query_adjust_factor.side_effect = lambda **_: Result(error_code="10001001")
    result = BaoStockPriceEvidenceCollector(client=client).collect(
        symbol="600519.SH", start_date=AS_OF, end_date=AS_OF
    )
    assert result["status"] == "partial"
    assert "ValueError" in result["error_reason"]
    assert "baostock_error:10001001" in result["error_reason"]


def test_price_queries_reuse_the_market_adapter_authenticated_session(monkeypatch):
    client = _client()
    client.query_history_k_data_plus = Mock(
        return_value=Result(
            [
                {
                    "date": "2026-08-20",
                    "open": "100",
                    "close": "101",
                    "high": "102",
                    "low": "99",
                },
            ]
        )
    )
    monkeypatch.setattr(
        baostock_market_adapter,
        "baostock_session",
        lambda: baostock_session(client=client),
    )
    market = baostock_market_adapter.BaoStockMarketAdapter().get_market_snapshot(
        symbol="600519", start_date="20260819", end_date="20260820"
    )
    assert market["adjust"] == ""
    BaoStockPriceEvidenceCollector(client=client).collect(
        symbol="600519.SH", start_date=AS_OF, end_date=AS_OF
    )
    client.login.assert_called_once()
    client.logout.assert_not_called()


def test_expired_collection_deadline_is_not_successful_empty():
    client = _client()
    result = BaoStockPriceEvidenceCollector(client=client, deadline_seconds=0).collect(
        symbol="600519.SH", start_date=AS_OF, end_date=AS_OF
    )
    assert result["status"] == "partial" and "TimeoutError" in result["error_reason"]
    client.query_dividend_data.assert_not_called()
    client.query_adjust_factor.assert_not_called()


def test_price_evidence_does_not_manufacture_native_kline_availability():
    snapshot = _snapshot()
    snapshot["facts"]["_uzi_raw_result"]["data"] = {}
    enriched = enrich_kline_snapshot(snapshot, as_of_date=AS_OF, collector=BaoStockPriceEvidenceCollector(client=_client()))
    ingest = UziSnapshotAdapter().adapt(enriched, symbol="600519.SH", market="CN_EQUITY", exchange_timezone="Asia/Shanghai", as_of_date=AS_OF)
    assert ingest.validation["native_available"] is False


def test_enrichment_preserves_native_indicators_and_failure_is_only_replay_gap():
    original = _snapshot()
    before = deepcopy(original)
    client = _client()
    client.query_adjust_factor.side_effect = TimeoutError()
    enriched = enrich_kline_snapshot(
        original,
        as_of_date=AS_OF,
        collector=BaoStockPriceEvidenceCollector(client=client),
    )
    ingest = UziSnapshotAdapter().adapt(
        enriched,
        symbol="600519.SH",
        market="CN_EQUITY",
        exchange_timezone="Asia/Shanghai",
        as_of_date=AS_OF,
    )
    assert original == before
    assert enriched["facts"]["_uzi_raw_result"] == original["facts"]["_uzi_raw_result"]
    assert ingest.payload["raw"]["indicators"] == {"ma20": 123}
    assert ingest.validation["native_available"] is True
    assert ingest.query_manifest["native_quality_status"] == "available"
    assert (
        ingest.query_manifest["partial_disclosure"]["reason_code"]
        == "price_evidence_unavailable"
    )
    assert ingest.query_manifest["collection_outcome"] == "partial"
    assert ingest.field_sources["adjustment_factors"] == "baostock.query_adjust_factor"


def test_explicit_missing_or_stale_collection_repairs_old_missing_proof_and_then_reuses():
    database = StrategyDatabase("sqlite:///:memory:")
    database.create_schema()
    client = _client()
    collector = Mock(return_value=[_snapshot()])
    coordinator = MarketDataCollectionCoordinator(
        database,
        collector=collector,
        price_evidence_collector=BaoStockPriceEvidenceCollector(client=client),
    )
    with database.session() as session:
        old = SnapshotIngestionService(MarketDataRepository(session)).ingest(
            _input("2_kline")
        )
        old_id, old_hash = old.id, old.content_hash
    try:
        job = coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.SH"],
            start_date=AS_OF,
            end_date=AS_OF,
            dimensions=["2_kline"],
            submit=False,
        )
        coordinator.run_job_now(job["id"])
        assert collector.call_count == 1
        assert client.query_adjust_factor.call_count == 1
        with database.session() as session:
            snapshots = list(
                session.scalars(
                    select(MarketDataSnapshot).where(
                        MarketDataSnapshot.dimension == "2_kline"
                    )
                )
            )
            assert len(snapshots) == 2
            assert session.get(MarketDataSnapshot, old_id).content_hash == old_hash
            assert any(has_complete_price_evidence(row, AS_OF) for row in snapshots)
        prices = MarketDataGateway(database=database).get_frozen_series(
            InstrumentResolver.resolve_stock("600519"),
            start_date=date(2026, 8, 19),
            end_date=AS_OF,
            as_of_date=AS_OF,
        )
        assert prices.source["corporate_action_coverage"]["verified"] is True
        assert len(prices.bars) == 2
        next_job = coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["600519.SH"],
            start_date=AS_OF,
            end_date=AS_OF,
            dimensions=["2_kline"],
            submit=False,
        )
        coordinator.run_job_now(next_job["id"])
        assert collector.call_count == 1
        assert client.query_adjust_factor.call_count == 1
    finally:
        coordinator.close()
        database.engine.dispose()
