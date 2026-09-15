from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from domain_packs.finance_ashare.tools.snapshot_collector import (
    _annotate_point_in_time_snapshot,
)
from research_system.market_data.collection import MarketDataCollectionCoordinator
from research_system.market_data.contracts import cutoff_at_for_date
from research_system.market_data.historical import capability_for_dimension
from research_system.market_data.job_diagnostics import classify_collection_failure
from research_system.market_data.models import MarketDataCollectionTaskResult
from research_system.market_data.uzi_adapter import UziSnapshotAdapter
from research_system.strategies.database import StrategyDatabase

AS_OF = date(2026, 9, 2)


def _snapshot(snapshot_type: str, source_status: str, *, as_of: date = AS_OF) -> dict:
    """Match the historical skills' actual input-only result shapes."""
    facts = (
        {
            "company_info": {},
            "financial_abstract": {"报告期": "2025", "营业收入": "100亿"},
            "banking_metrics": {},
            "web_search": [],
            "browser_evidence": {},
        }
        if snapshot_type == "moat"
        else {
            "news": [],
            "news_multi_source": {},
            "web_search": [],
            "hot_trend_mentions": {},
        }
    )
    available = source_status == "available"
    facts["availability"] = "available" if available else "data_gap"
    return _annotate_point_in_time_snapshot(
        {
            "snapshot_type": snapshot_type,
            "symbol": "688012.SH",
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "quality_status": "available" if available else "data_gap",
            "facts": facts,
            "source": {
                "provider_id": "test.pit",
                "source_id": "test",
                "chain_position": 0,
            },
            "source_trace": [{"source_id": "test", "status": source_status}],
        },
        snapshot_type=snapshot_type,
        as_of_date=as_of,
        cutoff_at=cutoff_at_for_date(as_of, "Asia/Shanghai"),
    )


def _adapt(snapshot: dict):
    return UziSnapshotAdapter().adapt(
        snapshot,
        symbol="688012.SH",
        market="CN_EQUITY",
        exchange_timezone="Asia/Shanghai",
        as_of_date=AS_OF,
    )


@pytest.mark.parametrize(
    ("snapshot_type", "source_status", "reason"),
    [
        ("moat", "available", "normalization_or_derivation_missing"),
        ("trap", "available", "normalization_or_derivation_missing"),
        ("trap", "no_data", "bounded_archive_gap"),
    ],
)
def test_input_only_success_is_not_fabricated_provider_failure(
    snapshot_type, source_status, reason
):
    snapshot = _snapshot(snapshot_type, source_status)
    unchanged = deepcopy(snapshot)

    ingest = _adapt(snapshot)
    manifest = ingest.query_manifest

    assert snapshot == unchanged
    assert manifest["collection_outcome"] == "unavailable"
    assert manifest["historical_capability"]["implementation_status"] == "inputs_only"
    assert manifest["degradation"]["reason_code"] == reason
    assert manifest["degradation"]["retryable"] is False
    assert "provider_failure" not in manifest
    assert manifest["field_coverage"]["present_fields"] == []
    assert manifest["source_trace"][0]["status"] == source_status
    if snapshot_type == "moat":
        assert ingest.payload["raw"]["financial_abstract"]["营业收入"] == "100亿"
        assert not ingest.payload["raw"].get("scores")
    else:
        assert all(row["status"] == "insufficient" for row in ingest.payload["signals"])
        assert "trap_level" not in ingest.payload["raw"]
    diagnostic = classify_collection_failure(
        error_code=reason,
        error_message=manifest["degradation"]["message"],
        provider_status="historical_unavailable",
        metrics={"raw_data_present": True},
    )
    assert diagnostic["failure_kind"] == reason
    assert "UZI 可用" not in diagnostic["failure_label"]


@pytest.mark.parametrize("snapshot_type", ["moat", "trap"])
@pytest.mark.parametrize(
    "source_status", ["timeout", "provider_unavailable", "rate_limited", "parse_failed"]
)
def test_real_source_failure_stays_retryable_for_input_collection(
    snapshot_type, source_status
):
    ingest = _adapt(_snapshot(snapshot_type, source_status))
    assert capability_for_dimension(ingest.dimension).can_reconstruct
    assert ingest.query_manifest["collection_outcome"] == "retryable_failure"
    assert ingest.query_manifest["provider_failure"]["retryable"] is True
    assert source_status in ingest.query_manifest["provider_failure"]["message"]
    assert "degradation" not in ingest.query_manifest


@pytest.mark.parametrize(
    ("snapshot_type", "facts"),
    [
        (
            "moat",
            {
                "factors": {"scale": "2025年报产能与规模数据"},
                "research_and_development": {"expense": 30},
            },
        ),
        (
            "trap",
            {"signals": [{"id": 1, "status": "hit", "evidence": "历史公开处罚记录"}]},
        ),
    ],
)
def test_existing_real_derived_fields_are_not_blocked_by_capability_label(
    snapshot_type, facts
):
    snapshot = _snapshot(snapshot_type, "available")
    snapshot["facts"]["_uzi_raw_result"]["data"] = facts
    ingest = _adapt(snapshot)
    assert ingest.query_manifest["collection_outcome"] == "available"
    assert ingest.query_manifest["field_coverage"]["missing_fields"] == []


def test_historical_peers_missing_group_is_not_source_failure_but_real_peers_remain_usable():
    snapshot = _snapshot("moat", "available")
    snapshot["snapshot_type"] = "peers"
    snapshot["historical_reconstruction"].pop("recoverable_fields", None)
    snapshot["facts"] = {
        "availability": "degraded",
        "_uzi_raw_result": {
            "data": {
                "comparison": {"peers": [], "self_only": [{"symbol": "688012.SH"}]},
                "global_peer_comparison": {"peer_count": 0, "peers": []},
            }
        },
    }
    missing = _adapt(snapshot)
    assert missing.query_manifest["collection_outcome"] == "unavailable"
    assert (
        missing.query_manifest["historical_capability"]["implementation_status"]
        == "inputs_only"
    )
    assert (
        missing.query_manifest["degradation"]["reason_code"]
        == "normalization_or_derivation_missing"
    )
    assert "历史可比公司集合" in missing.query_manifest["degradation"]["message"]
    assert missing.query_manifest["degradation"]["retryable"] is False
    assert "provider_failure" not in missing.query_manifest
    snapshot["facts"]["_uzi_raw_result"]["data"]["peers"] = [
        {"symbol": "002371.SZ", "name": "北方华创", "roe": 12.0}
    ]
    complete = _adapt(snapshot)
    assert complete.query_manifest["collection_outcome"] == "available"
    assert complete.query_manifest["field_coverage"]["missing_fields"] == []


def test_new_collection_exposes_specific_non_retryable_derivation_gaps():
    database = StrategyDatabase("sqlite://")
    database.create_schema()
    historical = datetime.now(timezone.utc).date() - timedelta(days=30)
    calls = []

    def collect(**kwargs):
        calls.append(kwargs)
        return [
            _snapshot(
                snapshot_type,
                "available" if snapshot_type == "moat" else "no_data",
                as_of=historical,
            )
            for snapshot_type in kwargs["snapshot_types"]
        ]

    coordinator = MarketDataCollectionCoordinator(database, collector=collect)
    try:
        job = coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="actor",
            symbols=["688012.SH"],
            start_date=historical,
            end_date=historical,
            dimensions=["14_moat", "18_trap"],
            mode="force_all",
            publish_when_complete=False,
            submit=False,
        )
        coordinator.run_job_now(job["id"])
        result = coordinator.get_job(job["id"], tenant_id="tenant", user_id="user")
        assert len(calls) == 1
        assert calls[0]["snapshot_types"] == ["trap"]
        assert len(result["tasks"]) == 2
        assert {task["error_code"] for task in result["tasks"]} == {
            "moat_basic_dependency_missing",
            "bounded_archive_gap",
        }
        for task in result["tasks"]:
            assert task["retryable"] is False
            assert task["provider_status"] == "historical_unavailable"
            if task["error_code"] == "bounded_archive_gap":
                assert task["failure_kind"] == task["error_code"]
            else:
                assert task["failure_kind"] == "missing_dependency"
            assert task["reason_code"] == task["error_code"]
        with database.session() as session:
            rows = session.scalars(select(MarketDataCollectionTaskResult)).all()
            assert len(rows) == 2
            assert all(row.retryable is False for row in rows)
            assert all(row.provider_status != "provider_unavailable" for row in rows)
    finally:
        coordinator.close()
        database.close()
