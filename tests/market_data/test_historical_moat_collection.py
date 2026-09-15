"""Real coordinator + SQLite + native spawn, entirely fictional offline corpus.

These test documents and basic fixtures are not historical investment evidence.
"""

from copy import deepcopy
from datetime import datetime, timezone
from threading import Event
from time import monotonic
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from research_system.market_data import moat_derivation, native_moat
from research_system.market_data.collection import MarketDataCollectionCoordinator
from research_system.market_data.contracts import EvidenceGrade, EvidenceInput
from research_system.market_data.ingestion import SnapshotIngestionService
from research_system.market_data.models import (
    MarketDataCollectionJob,
    MarketDataCollectionTaskResult,
    MarketDataSnapshot,
)
from research_system.market_data.native_projection import project_native_result
from research_system.market_data.repository import MarketDataRepository
from research_system.market_data.research_archive import ResearchDocumentStore
from research_system.market_data.resolver import SnapshotResolver
from research_system.market_data.uzi_adapter import UziSnapshotAdapter
from research_system.strategies.database import StrategyDatabase
from tests.market_data.test_pit_core import AS_OF, _input, _payload

COMPANY = "离线甲公司"
TEXT = "\n".join([
    f"{COMPANY} 专利 核心技术 自主。",
    f"{COMPANY} 客户粘性 转换成本 认证 长期合作。",
    f"{COMPANY} 平台效应 网络效应 用户生态。",
    f"{COMPANY} 市场份额 行业地位 规模优势 龙头。",
    f"{COMPANY} 研发投入 技术实力。",
])


@pytest.fixture
def harness(tmp_path, monkeypatch):
    guard = Mock(side_effect=AssertionError("offline coordinator attempted network"))
    monkeypatch.setattr("socket.socket.connect", guard)
    monkeypatch.setattr("socket.create_connection", guard)
    database = StrategyDatabase(f"sqlite:///{tmp_path / 'moat.sqlite'}")
    database.create_schema()
    forbidden = Mock(side_effect=AssertionError("historical moat must not fetch old material/model/aux"))
    coordinator = MarketDataCollectionCoordinator(database, collector=forbidden, research_provider=forbidden)
    monkeypatch.setattr("research_system.market_data.quant_auxiliary.collect_job_quant_auxiliary_inputs", forbidden)
    monkeypatch.setattr("research_system.market_data.research_enrichment.run_research_enrichment", forbidden)
    try:
        yield database, coordinator, tmp_path, forbidden
    finally:
        forbidden.assert_not_called()
        guard.assert_not_called()
        coordinator.close()
        database.close()


def seed(database, tmp_path, *, document=True, future=False, text=TEXT, document_dimension="7_industry", create_gap=True):
    with database.session() as session:
        repo = MarketDataRepository(session)
        ingestion = SnapshotIngestionService(repo)
        basic = _payload("0_basic")
        basic["raw"] = {"name": COMPANY, "code": "600519.SH", "industry": "测试行业"}
        ingestion.ingest(_input("0_basic", payload=basic))
        if document:
            digest, path = ResearchDocumentStore(tmp_path / "documents").put(text.encode(), suffix="txt")
            evidence = EvidenceInput(
                provider="offline-test", source_kind="research_document", title=f"{COMPANY}测试正文",
                source_url="https://example.invalid/test-document", content_ref=path,
                content_hash=digest, inline_content=text,
                published_at=datetime(2026, 8, 21 if future else 19, tzinfo=timezone.utc),
                observed_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
                grade=EvidenceGrade.B, immutable_proof=True,
                metadata={"evidence_mode": "contemporaneous_archive"},
            )
            ingestion.ingest(_input(document_dimension, evidence=[evidence],
                observed_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
                ingested_at=datetime(2026, 9, 1, tzinfo=timezone.utc)))
        if not create_gap:
            return None
        gap = UziSnapshotAdapter().disclosed_gap(
            symbol="600519.SH", market="CN_EQUITY", exchange_timezone="Asia/Shanghai",
            dimension="14_moat", as_of_date=AS_OF, reason_code="old_material_gap", message="old gap",
        )
        gap.payload["_uzi_raw_result"] = {"data": {}, "error": "original failure", "_timeout": True}
        original = ingestion.ingest(gap)
        return {"id": original.id, "hash": original.content_hash, "payload": deepcopy(original.payload_json)}


def run(coordinator):
    job = coordinator.create_job(tenant_id="tenant", user_id="user", actor_id="actor",
        symbols=["600519.SH"], start_date=AS_OF, end_date=AS_OF,
        dimensions=["14_moat"], mode="missing_or_stale", submit=False,
    )
    coordinator.run_job_now(job["id"])
    return coordinator.get_job(job["id"], tenant_id="tenant", user_id="user")


@pytest.mark.parametrize("document_dimension", ["7_industry", "8_materials"])
def test_explicit_historical_moat_uses_existing_pit_documents_and_new_revision(harness, document_dimension):
    database, coordinator, tmp_path, _ = harness
    old = seed(database, tmp_path, document_dimension=document_dimension)
    result = run(coordinator)
    assert result["status"] == "succeeded", result["tasks"]
    assert "include_research_archive" not in result.get("request", {})
    with database.session() as session:
        repo = MarketDataRepository(session)
        original = repo.require_snapshot(old["id"])
        assert original.content_hash == old["hash"] and original.payload_json == old["payload"]
        row = session.scalar(select(MarketDataCollectionTaskResult).where(
            MarketDataCollectionTaskResult.task_id == result["tasks"][0]["id"]))
        revision = repo.require_snapshot(row.snapshot_id)
        assert revision.id != old["id"] and revision.origin == "derived"
        assert revision.payload_json["_uzi_collector_original_result"] == old["payload"]["_uzi_raw_result"]
        assert not revision.payload_json["_uzi_raw_result"].get("error")
        derivation = revision.query_manifest_json["moat_derivation"]
        assert derivation["status"] == "completed"
        assert derivation["actual_source"] == native_moat.ARCHIVE_BASIS
        assert derivation["isolation_events"] == []
        assert len(derivation["recording"]["queries"]) == 5
        assert derivation["recording"]["corpus"]
        assert any(item["dimension"] == document_dimension for item in derivation["inputs"])
        assert derivation["document_selection"]["complete_archive_coverage_claimed"] is False
        coverage = SnapshotResolver(repo).coverage("600519.SH", "CN_EQUITY", AS_OF)
        selected = next(item for item in coverage.dimensions if item.dimension == "14_moat")
        assert selected.snapshot_id == revision.id
        assert selected.eligible
        count = len(repo.list_snapshots(dimension="14_moat"))
    # A qualified new revision is reused without another replay/provider call.
    again = run(coordinator)
    assert again["status"] == "succeeded"
    with database.session() as session:
        assert len(MarketDataRepository(session).list_snapshots(dimension="14_moat")) == count


@pytest.mark.parametrize("future", [False, True])
def test_absent_or_future_archive_stays_dependency_gap(harness, monkeypatch, future):
    database, coordinator, tmp_path, _ = harness
    seed(database, tmp_path, document=future, future=future)
    replay = Mock(side_effect=AssertionError("invalid corpus must not reach native"))
    monkeypatch.setattr(native_moat, "replay_moat", replay)
    result = run(coordinator)
    replay.assert_not_called()
    assert result["tasks"][0]["error_code"] == "moat_archive_dependency_missing"
    assert result["tasks"][0]["retryable"] is False
    assert result["tasks"][0]["failure_kind"] == "missing_dependency"
    with database.session() as session:
        row = session.scalar(select(MarketDataCollectionTaskResult).where(
            MarketDataCollectionTaskResult.task_id == result["tasks"][0]["id"]))
        snapshot = MarketDataRepository(session).require_snapshot(row.snapshot_id)
        receipt = snapshot.query_manifest_json["moat_derivation"]
        assert receipt["missing_dependency"] is True
        assert receipt["refresh_on_dependency_change"] is True


def test_partial_native_factor_evidence_is_not_a_new_host_gate(harness):
    database, coordinator, tmp_path, _ = harness
    seed(database, tmp_path, text=f"{COMPANY} 专利 核心技术。\n{COMPANY} 研发投入。")
    result = run(coordinator)
    assert result["status"] == "succeeded", result["tasks"]
    with database.session() as session:
        row = session.scalar(select(MarketDataCollectionTaskResult).where(
            MarketDataCollectionTaskResult.task_id == result["tasks"][0]["id"]))
        snapshot = MarketDataRepository(session).require_snapshot(row.snapshot_id)
        data = snapshot.payload_json["_uzi_raw_result"]["data"]
        assert data["scores_available"] is True
        assert data["scores_evidence"]["switching"] is False
        assert data["scores"]["switching"] == 5  # Preserved native semantics.
        assert not snapshot.query_manifest_json.get("degradation", {}).get("excluded_from_scoring")


def test_new_scoped_request_recovers_after_missing_archive_is_added(harness):
    database, coordinator, tmp_path, _ = harness
    seed(database, tmp_path, document=False)
    first = run(coordinator)
    assert first["tasks"][0]["failure_kind"] == "missing_dependency"
    with database.session() as session:
        repo = MarketDataRepository(session)
        before = {row.id: row.content_hash for row in repo.list_snapshots(dimension="14_moat")}
    seed(database, tmp_path, create_gap=False, document_dimension="8_materials")
    second = run(coordinator)
    assert second["status"] == "succeeded", second["tasks"]
    with database.session() as session:
        repo = MarketDataRepository(session)
        assert {key: repo.require_snapshot(key).content_hash for key in before} == before
        assert len(repo.list_snapshots(dimension="14_moat")) == len(before) + 1


@pytest.mark.parametrize("rd_only", [False, True])
def test_unassessed_native_scores_and_envelope_are_preserved(harness, monkeypatch, rd_only):
    database, coordinator, tmp_path, _ = harness
    seed(database, tmp_path, text=f"{COMPANY} 研发投入。" if rd_only else f"{COMPANY} 本测试文本只描述办公地址。")
    actual, replay = {}, native_moat.replay_moat

    def capture(recording, **kwargs):
        result = replay(recording, **kwargs)
        actual.update(deepcopy(result))
        return result

    monkeypatch.setattr(native_moat, "replay_moat", capture)
    result = run(coordinator)
    assert actual["status"] == "completed"
    expected = actual["native_result"]
    assert expected["data"]["scores"] == {}
    assert expected["data"]["scores_available"] is False
    with database.session() as session:
        row = session.scalar(select(MarketDataCollectionTaskResult).where(
            MarketDataCollectionTaskResult.task_id == result["tasks"][0]["id"]))
        snapshot = MarketDataRepository(session).require_snapshot(row.snapshot_id)
        assert snapshot.payload_json["_uzi_raw_result"] == expected
        projected = project_native_result(snapshot)
        assert {key: projected[key] for key in expected} == expected
        assert set(projected) - set(expected) <= {"_pipeline", "_market_data_gap"}
        assert "error" not in projected and "_timeout" not in projected
        manifest = snapshot.query_manifest_json
        assert manifest["collection_outcome"] == ("partial" if rd_only else "unavailable")
        assert manifest["moat_derivation"]["factor_evidence"] == dict.fromkeys(
            ("intangible", "switching", "network", "scale"), False)
        assert manifest["moat_derivation"]["rd_evidence_present"] is rd_only


@pytest.mark.parametrize("interruption", ["cancel", "lease_loss", "revoke"])
def test_replay_result_cannot_write_after_cancel_fence_loss_or_input_revocation(harness, monkeypatch, interruption):
    database, coordinator, tmp_path, _ = harness
    old = seed(database, tmp_path)
    original_replay = native_moat.replay_moat

    def interrupted(recording, **kwargs):
        result = original_replay(recording, **kwargs)
        with database.session() as session:
            job = session.scalar(select(MarketDataCollectionJob).where(MarketDataCollectionJob.status == "running"))
            if interruption == "cancel":
                job.cancel_requested = True
            elif interruption == "lease_loss":
                job.lease_owner = "different-worker"
            else:
                snapshot = session.scalar(select(MarketDataSnapshot).where(MarketDataSnapshot.dimension == "0_basic"))
                snapshot.revoked_at = datetime.now(timezone.utc)
        return result

    monkeypatch.setattr(native_moat, "replay_moat", interrupted)
    run(coordinator)
    with database.session() as session:
        rows = MarketDataRepository(session).list_snapshots(dimension="14_moat")
        assert [row.id for row in rows] == [old["id"]]


def test_cancelled_before_preparation_never_calls_native(harness, monkeypatch):
    database, _, tmp_path, _ = harness
    seed(database, tmp_path)
    replay = Mock(side_effect=AssertionError("cancelled derivation must not start"))
    monkeypatch.setattr(native_moat, "replay_moat", replay)
    cancellation = Event()
    cancellation.set()
    packet = moat_derivation.prepare_moat_derivation(
        database, spec={"id": "test", "job_id": "test", "symbol": "600519.SH",
                       "market": "CN_EQUITY", "exchange_timezone": "Asia/Shanghai"},
        as_of_date=AS_OF, registry=UziSnapshotAdapter().registry,
        deadline_at=monotonic() + 20, cancellation=cancellation,
    )
    assert packet["status"] == "cancelled"
    replay.assert_not_called()
