"""Fictional local corpus: full-text input wiring, not new moat scoring rules."""

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from time import monotonic
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from research_system.market_data import moat_derivation, native_moat
from research_system.market_data.contracts import EvidenceGrade, EvidenceInput
from research_system.market_data.ingestion import SnapshotIngestionService
from research_system.market_data.models import MarketDataCollectionTaskResult
from research_system.market_data.repository import MarketDataRepository
from research_system.market_data.research_archive import ResearchDocumentStore
from research_system.market_data.research_archive_text import archive_text
from research_system.market_data.uzi_adapter import UziSnapshotAdapter
from tests.market_data.test_historical_moat_collection import COMPANY, TEXT, harness, run, seed
from tests.market_data.test_pit_core import AS_OF, _input

SPEC = {"id": "offline-task", "job_id": "offline-job", "symbol": "600519.SH",
        "market": "CN_EQUITY", "exchange_timezone": "Asia/Shanghai"}


def _seed_full(database, tmp_path, *, bound=True, damage=None, future=False, shared_legacy=False):
    old = seed(database, tmp_path, document=False)
    store = ResearchDocumentStore(tmp_path / "documents")
    source_hash, source_path = store.put(b"%PDF-fictional-complete-original", suffix="pdf")
    full = f"{COMPANY}年度报告。\n" + "说明。\n" * 25_000 + TEXT
    inline, binding = archive_text(store, full, source_hash=source_hash,
        extraction={"extractor": "offline-test", "page_count": 166, "pages_processed": 166, "pages_without_text": []})
    assert TEXT not in inline
    expected = deepcopy(binding)
    if damage == "missing":
        Path(binding["text_content_ref"]).unlink()
    elif damage == "hash":
        original = full.encode()
        Path(binding["text_content_ref"]).write_bytes(original[:-1] + b"X")
    elif damage == "version":
        binding["version"] = "research-text-v999"
        expected = deepcopy(binding)
    elif damage == "source":
        binding["source_content_hash"] = "f" * 64
        expected = deepcopy(binding)
    elif damage == "binding":
        binding["text_characters"] += 1
    evidence = EvidenceInput(
        provider="offline-test", source_kind="research_document", title=f"{COMPANY}年度报告",
        source_url="https://example.invalid/fictional-full-report.pdf",
        content_ref=source_path, content_hash=source_hash, inline_content=inline,
        published_at=datetime(2026, 8, 21 if future else 19, tzinfo=timezone.utc),
        observed_at=datetime(2026, 9, 1, tzinfo=timezone.utc), grade=EvidenceGrade.B, immutable_proof=True,
        metadata={"evidence_mode": "public_archive_reconstruction", "text_extraction": binding},
    )
    query = {"research_archive": {"text_extractions": [{"evidence_key": evidence.evidence_key(), "binding": expected}]}} if bound else {}
    with database.session() as session:
        ingestion = SnapshotIngestionService(MarketDataRepository(session))
        if shared_legacy:
            ingestion.ingest(_input("6_research", evidence=[evidence], query_manifest={},
                observed_at=datetime(2026, 9, 1, tzinfo=timezone.utc), ingested_at=datetime(2026, 9, 1, tzinfo=timezone.utc)))
        snapshot = ingestion.ingest(_input(
            "7_industry", evidence=[evidence], query_manifest=query,
            observed_at=datetime(2026, 9, 1, tzinfo=timezone.utc), ingested_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        ))
        snapshot_id = snapshot.id
    return old, full, inline, binding, snapshot_id


def _prepare(database):
    return moat_derivation.prepare_moat_derivation(database, spec=SPEC, as_of_date=AS_OF,
        registry=UziSnapshotAdapter().registry, deadline_at=monotonic() + 30, cancellation=Event())


def test_explicit_new_binding_reaches_native_recording_beyond_inline(harness, monkeypatch):
    database, _, tmp_path, _ = harness
    _, full, inline, binding, _ = _seed_full(database, tmp_path)
    replay = Mock(return_value={"status": "completed"})
    monkeypatch.setattr(native_moat, "replay_moat", replay)
    packet = _prepare(database)
    assert packet["documents"][0]["inline_content"] == full
    assert packet["documents"][0]["text_hash"] == binding["text_content_hash"]
    assert packet["documents"][0]["text_extraction"] == binding
    for query in packet["recording"]["queries"]:
        assert query["trusted"]
        assert all(row["start"] >= len(inline) for row in query["trusted"])
        assert all(row["body"] == full[row["start"]:row["end"]] for row in query["trusted"])
    assert replay.call_count == 1


def test_selected_full_text_binding_is_not_shadowed_by_shared_legacy_evidence(harness, monkeypatch):
    database, _, tmp_path, _ = harness
    _, full, _, _, bound_snapshot = _seed_full(database, tmp_path, shared_legacy=True)
    monkeypatch.setattr(native_moat, "replay_moat", Mock(return_value={"status": "completed"}))
    packet = _prepare(database)
    assert len(packet["documents"]) == 1
    assert packet["documents"][0]["snapshot_id"] == bound_snapshot
    assert packet["documents"][0]["inline_content"] == full
    assert all(row["dimension"] != "6_research" for row in packet["inputs"])


def test_unbound_metadata_does_not_discover_or_read_sidecar(harness, monkeypatch):
    database, _, tmp_path, _ = harness
    _, _, inline, _, _ = _seed_full(database, tmp_path, bound=False, damage="missing")
    monkeypatch.setattr(native_moat, "replay_moat", Mock(return_value={"status": "completed"}))
    packet = _prepare(database)
    assert packet["documents"][0]["inline_content"] == inline
    assert "text_extraction" not in packet["documents"][0]
    assert all(not query["trusted"] for query in packet["recording"]["queries"])


def test_legacy_inline_recording_is_byte_compatible(harness, monkeypatch):
    database, _, tmp_path, _ = harness
    seed(database, tmp_path)
    monkeypatch.setattr(native_moat, "replay_moat", Mock(return_value={"status": "completed"}))
    packet = _prepare(database)
    assert packet["documents"][0]["inline_content"] == TEXT
    assert "text_extraction" not in packet["documents"][0]
    expected = native_moat.archive_recording(symbol=SPEC["symbol"], basic=packet["basic"], documents=packet["documents"])
    assert packet["recording"] == expected


@pytest.mark.parametrize("damage", ["missing", "hash", "version", "source", "binding"])
def test_bad_explicit_binding_is_rejected_before_native_without_inline_fallback(harness, monkeypatch, damage):
    database, _, tmp_path, _ = harness
    _seed_full(database, tmp_path, damage=damage)
    replay = Mock(side_effect=AssertionError("invalid binding reached native"))
    monkeypatch.setattr(native_moat, "replay_moat", replay)
    with pytest.raises(moat_derivation.MoatDependencyError, match="archive_text"):
        _prepare(database)
    replay.assert_not_called()


def test_future_document_never_reaches_native_even_with_full_text_binding(harness, monkeypatch):
    database, _, tmp_path, _ = harness
    _seed_full(database, tmp_path, future=True)
    replay = Mock(side_effect=AssertionError("future document reached native"))
    monkeypatch.setattr(native_moat, "replay_moat", replay)
    packet = _prepare(database)
    assert packet["status"] == "missing_dependency"
    assert not packet["documents"]
    replay.assert_not_called()


@pytest.mark.parametrize("damage", ["file", "packet"])
def test_full_text_is_rechecked_inside_ingest_fence(harness, damage):
    database, _, tmp_path, _ = harness
    _, full, _, binding, _ = _seed_full(database, tmp_path)
    packet = _prepare(database)
    assert packet["status"] == "completed"
    if damage == "file":
        Path(binding["text_content_ref"]).write_bytes(full.encode()[:-1] + b"X")
    else:
        packet["documents"][0]["text_extraction"]["version"] = "research-text-v999"
    with database.session() as session, pytest.raises(moat_derivation.MoatDependencyError):
        moat_derivation.moat_derivation_ingest(MarketDataRepository(session), packet=packet,
            task=SimpleNamespace(**SPEC, dimension="14_moat", attempt_count=1), as_of_date=AS_OF,
            registry=UziSnapshotAdapter().registry)


def test_actual_native_output_is_preserved_and_new_derivation_retains_binding(harness, monkeypatch):
    database, coordinator, tmp_path, _ = harness
    old, _, inline, binding, _ = _seed_full(database, tmp_path)
    actual, replay = {}, native_moat.replay_moat
    def capture(recording, **kwargs):
        result = replay(recording, **kwargs)
        actual.update(deepcopy(result))
        return result
    monkeypatch.setattr(native_moat, "replay_moat", capture)
    result = run(coordinator)
    assert result["status"] == "succeeded", result["tasks"]
    assert actual["source_sha256"] == native_moat.NATIVE_SOURCE_SHA256
    assert actual["isolation_events"] == []
    assert actual["native_result"]["data"]["scores_available"] is True
    with database.session() as session:
        repo = MarketDataRepository(session)
        item = session.scalar(select(MarketDataCollectionTaskResult).where(MarketDataCollectionTaskResult.task_id == result["tasks"][0]["id"]))
        revision = repo.require_snapshot(item.snapshot_id)
        assert revision.payload_json["_uzi_raw_result"] == actual["native_result"]
        assert repo.require_snapshot(old["id"]).content_hash == old["hash"]
        assert repo.require_snapshot(old["id"]).payload_json == old["payload"]
        fixed = revision.query_manifest_json["research_archive"]["text_extractions"]
        assert len(fixed) == 1 and fixed[0]["binding"] == binding
        proofs = repo.list_snapshot_evidence(revision.id)
        retained = next(row for row in proofs if row.evidence_key == fixed[0]["evidence_key"])
        assert retained.inline_content == inline  # No expanding/mutating persisted evidence.


def test_unbound_sidecar_stays_unbound_in_new_derivation(harness):
    database, coordinator, tmp_path, _ = harness
    _seed_full(database, tmp_path, bound=False, damage="missing")
    result = run(coordinator)
    with database.session() as session:
        repo = MarketDataRepository(session)
        item = session.scalar(select(MarketDataCollectionTaskResult).where(
            MarketDataCollectionTaskResult.task_id == result["tasks"][0]["id"]))
        revision = repo.require_snapshot(item.snapshot_id)
        assert "research_archive" not in revision.query_manifest_json
        assert revision.payload_json["_uzi_raw_result"]["data"]["scores_available"] is False
        assert all(not row["trusted"] for row in revision.query_manifest_json["moat_derivation"]["recording"]["queries"])
