from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta

import pytest
from sqlalchemy import update

from research_system.market_data.ingestion import SnapshotIngestionService
from research_system.market_data.models import MarketDataSnapshot
from research_system.market_data.repository import MarketDataRepository
from research_system.strategies.backtesting.instruments import InstrumentResolver
from research_system.strategies.backtesting.market_data_gateway import MarketDataGateway
from research_system.strategies.database import StrategyDatabase
from research_system.strategies.errors import StrategyDomainError
from tests.market_data.test_pit_core import (
    AS_OF,
    OBSERVED,
    _evidence,
    _input,
    _payload,
    _query_manifest,
)


@pytest.fixture
def database():
    database = StrategyDatabase("sqlite:///:memory:")
    database.create_schema()
    yield database
    database.engine.dispose()


def _seed(
    database,
    *,
    missing_proof=False,
    factor_change=False,
    missing_day=None,
    observed_late=False,
):
    payload = _payload("2_kline")
    payload["corporate_actions"] = []
    if missing_day:
        payload["ohlcv"] = [
            row for row in payload["ohlcv"] if row["date"] != missing_day
        ]
    if factor_change:
        payload["adjustment_factors"][-1]["factor"] = 1.1
    query = _query_manifest("2_kline", payload)
    for proof in query["field_queries"].values():
        proof["query_window"]["start"] = (AS_OF - timedelta(days=30)).isoformat()
    for field, proof in query["field_queries"].items():
        proof.update(query_scope="field", field=field)
    if missing_proof:
        query.pop("field_queries")
    observed_at = OBSERVED + timedelta(days=1) if observed_late else OBSERVED
    ingest = _input(
        "2_kline",
        payload=payload,
        query_manifest=query,
        observed_at=observed_at,
        ingested_at=observed_at + timedelta(minutes=5),
        evidence=_evidence("2_kline", observed_at=observed_at),
    )
    # The native report gate deliberately admits auditable partial fields;
    # outcome-price verification is a stricter, separate boundary.
    ingest.validation["adapter"] = "cognitrade_uzi_native_gate_fixture"
    with database.session() as session:
        snapshot = SnapshotIngestionService(MarketDataRepository(session)).ingest(
            ingest
        )
        return snapshot.id


def _read(database, *, start=AS_OF - timedelta(days=1), end=AS_OF):
    return MarketDataGateway(database=database).get_frozen_series(
        InstrumentResolver.resolve_stock("600519"),
        start_date=start,
        end_date=end,
        as_of_date=AS_OF,
    )


def test_empty_corporate_actions_without_successful_query_are_not_proof(database):
    _seed(database, missing_proof=True)
    with pytest.raises(StrategyDomainError) as caught:
        _read(database)
    assert caught.value.code == "market_history_missing_in_data_center"
    assert any(
        row["reason"] == "corporate_action_coverage_unverified"
        for row in caught.value.details["rejected_snapshots"]
    )


def test_action_query_must_cover_full_price_basis_interval(database):
    _seed(database)
    with pytest.raises(StrategyDomainError) as caught:
        _read(database, start=AS_OF - timedelta(days=40))
    assert any(
        row["reason"] == "corporate_action_coverage_unverified"
        for row in caught.value.details["rejected_snapshots"]
    )


def test_changing_adjustment_factor_cannot_be_hidden_by_empty_action_list(database):
    _seed(database, factor_change=True)
    with pytest.raises(StrategyDomainError) as caught:
        _read(database)
    assert caught.value.code == "report_replay_corporate_actions_unsupported"
    assert (
        caught.value.details["corporate_actions"][0]["type"]
        == "adjustment_factor_changed"
    )


def test_missing_session_is_not_silently_treated_as_a_suspension(database):
    _seed(database, missing_day="2026-08-19")
    with pytest.raises(StrategyDomainError) as caught:
        _read(database, start=date(2026, 8, 18))
    assert caught.value.details["rejected_snapshots"][0] == {
        "snapshot_id": caught.value.details["rejected_snapshots"][0]["snapshot_id"],
        "reason": "requested_sessions_missing",
        "dates": ["2026-08-19"],
    }


def test_weekend_end_does_not_require_a_fabricated_weekend_bar(database):
    _seed(database)
    result = _read(database, start=date(2026, 8, 13), end=date(2026, 8, 16))
    assert [row["date"] for row in result.bars] == ["2026-08-13", "2026-08-14"]
    assert result.source["corporate_action_coverage"] == {
        "verified": True,
        "start": "2026-08-13",
        "end": "2026-08-14",
    }


def test_grade_a_observed_after_outcome_cutoff_is_not_historical_evidence(database):
    _seed(database, observed_late=True)
    with pytest.raises(StrategyDomainError) as caught:
        _read(database)
    assert (
        caught.value.details["rejected_snapshots"][0]["reason"]
        == "outcome_cutoff_unverified"
    )


def test_tampered_price_payload_fails_snapshot_hash(database):
    snapshot_id = _seed(database)
    with database.session() as session:
        snapshot = session.get(MarketDataSnapshot, snapshot_id)
        payload = deepcopy(snapshot.payload_json)
        payload["ohlcv"][-1]["close"] = 777
        # Simulate storage corruption, bypassing append-only ORM guards only
        # in this isolated database, to exercise read-time hash verification.
        session.execute(
            update(MarketDataSnapshot)
            .where(MarketDataSnapshot.id == snapshot_id)
            .values(payload_json=payload)
        )
    with pytest.raises(StrategyDomainError) as caught:
        _read(database)
    assert caught.value.code == "market_snapshot_hash_mismatch"


@pytest.mark.parametrize("opening", [None, 0, -1, float("nan"), float("inf")])
def test_replay_does_not_manufacture_an_open_or_reuse_a_nonfinite_price(opening):
    rows = [{"datetime": AS_OF.isoformat(), "open": opening, "close": 100}]
    result = MarketDataGateway(series_loader=lambda *_: rows).get_frozen_series(
        InstrumentResolver.resolve_stock("600519"),
        start_date=AS_OF,
        end_date=AS_OF,
        as_of_date=AS_OF,
    )
    assert result.bars[0]["open"] is None
    assert result.bars[0]["high"] is None
    assert result.degraded_reasons == (f"real_open_missing:{AS_OF}",)
