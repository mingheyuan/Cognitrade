"""Explicit historical collection must call the real PIT macro derivation.

All corpus records are fictional, all IO belongs to the existing tmp SQLite
harness, and provider/model/auxiliary calls are forbidden.
"""
from copy import deepcopy
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from research_system.market_data.collection import MarketDataCollectionCoordinator
from research_system.market_data.models import MarketDataCollectionTaskResult
from research_system.market_data.repository import MarketDataRepository
from tests.market_data.test_macro_derivation import (  # noqa: F401
    AS_OF,
    SYMBOL,
    harness,
    seed,
)


def run(coordinator):
    job = coordinator.create_job(tenant_id="offline", user_id="offline", actor_id="offline",
        symbols=[SYMBOL], start_date=AS_OF, end_date=AS_OF, dimensions=["3_macro"],
        mode="missing_or_stale", publish_when_complete=False, submit=False)
    coordinator.run_job_now(job["id"])
    return coordinator.get_job(job["id"], tenant_id="offline", user_id="offline")


@pytest.fixture
def coordinator(harness, monkeypatch):  # noqa: F811
    forbidden = Mock(side_effect=AssertionError("macro derivation must not collect benchmark/model/aux"))
    monkeypatch.setattr("research_system.market_data.quant_auxiliary.collect_job_quant_auxiliary_inputs", forbidden)
    service = MarketDataCollectionCoordinator(harness.database, collector=forbidden, research_provider=forbidden)
    try:
        yield service
    finally:
        service.close()
        forbidden.assert_not_called()


def selected(context, result):
    with context.database.session() as session:
        row = session.scalar(select(MarketDataCollectionTaskResult).where(
            MarketDataCollectionTaskResult.task_id == result["tasks"][0]["id"]))
        snapshot = MarketDataRepository(session).require_snapshot(row.snapshot_id)
        return snapshot.id, deepcopy(snapshot.payload_json), deepcopy(snapshot.query_manifest_json)


def test_explicit_macro_derivation_runs_without_benchmark_and_reuses_success(harness, coordinator):  # noqa: F811
    old = seed(harness)
    result = run(coordinator)
    assert result["status"] == "succeeded", result["tasks"]
    sid, payload, manifest = selected(harness, result)
    assert sid != old["id"]
    assert payload["_uzi_collector_original_result"] == old["payload"]["_uzi_raw_result"]
    assert manifest["macro_derivation"]["status"] == "completed"
    assert not manifest["macro_derivation"]["unassessed_groups"]
    assert len(manifest["macro_derivation"]["calls"]) == 6
    assert manifest["macro_derivation"]["isolation_events"] == []
    again = run(coordinator)
    assert again["tasks"][0]["status"] == "succeeded"
    assert selected(harness, again)[0] == sid
    with harness.database.session() as session:
        original = MarketDataRepository(session).require_snapshot(old["id"])
        assert original.content_hash == old["hash"] and original.payload_json == old["payload"]


def test_partial_macro_corpus_does_not_remove_native_values_or_add_report_gate(harness, coordinator):  # noqa: F811
    seed(harness, public_text=None, generic_text="纯虚构：地缘政治风险与贸易摩擦。")
    result = run(coordinator)
    assert result["status"] == "partial", result["tasks"]
    _, payload, manifest = selected(harness, result)
    assert manifest["collection_outcome"] == "partial"
    assert len(manifest["macro_derivation"]["unassessed_groups"]) == 5
    assert payload["_uzi_raw_result"]["data"]["rate_cycle"] == f"中性（{AS_OF.year} 货币政策）"
    assert not manifest.get("degradation", {}).get("excluded_from_scoring")
    assert result["tasks"][0]["retryable"] is False


@pytest.mark.parametrize("future", [False, True])
def test_missing_or_future_corpus_reports_dependency_without_source_retry(harness, coordinator, future):  # noqa: F811
    seed(harness, documents=future, future=future)
    result = run(coordinator)
    assert result["tasks"][0]["error_code"] == "macro_archive_dependency_missing"
    assert result["tasks"][0]["retryable"] is False
    assert result["tasks"][0]["failure_kind"] == "missing_dependency"
    assert selected(harness, result)[2]["macro_derivation"]["missing_dependency"]
