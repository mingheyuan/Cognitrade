"""Direct DB macro derivation tests over explicitly fictional local documents.

No coordinator, business API, real source, or model is invoked. Official-looking
URLs below are inert transport fixtures, not historical claims or downloaded
documents. Every SQLite database and archive belongs to pytest's tmp_path.
"""

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from time import monotonic
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import update

from research_system.market_data import macro_derivation, native_macro
from research_system.market_data.contracts import EvidenceGrade, EvidenceInput
from research_system.market_data.ingestion import SnapshotIngestionService
from research_system.market_data.models import (
    MarketDataCollectionJob,
    MarketDataCollectionTask,
    MarketDataEvidence,
    MarketDataSnapshot,
)
from research_system.market_data.native_projection import project_native_result
from research_system.market_data.repository import MarketDataRepository
from research_system.market_data.research_archive import ResearchDocumentStore
from research_system.market_data.research_archive_text import archive_text, verified_text_projection
from research_system.market_data.uzi_adapter import UziSnapshotAdapter
from research_system.strategies.database import StrategyDatabase
from tests.market_data.test_pit_core import AS_OF, _input, _payload

SYMBOL = "600519.SH"
COMPANY = "纯虚构宏观测试甲公司"
INDUSTRY = "虚构测试行业"
GROUPS = ("rate_cycle", "us_rate", "fx_trend", "geo_risk", "commodity", "industry_macro")
PUBLIC_TEXT = "\n".join([
    "纯虚构离线材料：2026 中国利率货币政策，降息宽松。",
    "纯虚构离线材料：2026 美国美联储利率周期，政策讨论。",
    "纯虚构离线材料：2026 人民币汇率走势，相关说明。",
])
GENERIC_TEXT = "\n".join([
    f"纯虚构离线材料：2026 中美关系贸易制裁，涉及{INDUSTRY}。",
    "纯虚构离线材料：2026 大宗商品周期与 CRB指数。",
    f"纯虚构离线材料：2026 {INDUSTRY}宏观政策利好利空讨论。",
])


@pytest.fixture
def harness(tmp_path, monkeypatch):
    guards = []
    for target in (
        "socket.socket.connect", "socket.create_connection", "socket.getaddrinfo",
        "requests.sessions.Session.request",
        "research_system.market_data.research_archive.ResearchArchiveCollector.collect",
        "research_system.market_data.research_enrichment.run_research_enrichment",
    ):
        guard = Mock(side_effect=AssertionError("macro DB test attempted external acquisition"))
        monkeypatch.setattr(target, guard)
        guards.append(guard)
    database = StrategyDatabase(f"sqlite:///{tmp_path / 'macro.sqlite'}")
    database.create_schema()
    with database.session() as session:
        job = MarketDataCollectionJob(
            tenant_id="offline", user_id="offline", requested_by="offline",
            idempotency_key="fictional-macro", start_date=AS_OF, end_date=AS_OF,
            status="running", symbols_json=[SYMBOL], dimensions_json=["3_macro"],
        )
        session.add(job)
        session.flush()
        task = MarketDataCollectionTask(
            job_id=job.id, symbol=SYMBOL, market="CN_EQUITY",
            exchange_timezone="Asia/Shanghai", dimension="3_macro",
            start_date=AS_OF, end_date=AS_OF, status="running", attempt_count=1,
        )
        session.add(task)
        session.flush()
        task_copy = SimpleNamespace(**{key: getattr(task, key) for key in (
            "id", "job_id", "symbol", "market", "exchange_timezone", "dimension",
            "start_date", "end_date", "status", "attempt_count",
        )})
    try:
        yield SimpleNamespace(database=database, root=tmp_path, task=task_copy,
                              registry=UziSnapshotAdapter().registry)
    finally:
        database.close()
        for guard in guards:
            guard.assert_not_called()


def seed(harness, *, basic=True, documents=True, public_text=PUBLIC_TEXT,
         generic_text=GENERIC_TEXT, future=False, version_proven=True,
         archive_mode="contemporaneous_archive", outside_window=False,
         document_symbol=SYMBOL, trusted_url="https://www.pbc.gov.cn/offline-fictional.txt"):
    """Seed immutable original gap and documents; none is real market evidence."""
    with harness.database.session() as session:
        repo = MarketDataRepository(session)
        ingestion = SnapshotIngestionService(repo)
        if basic:
            payload = _payload("0_basic")
            payload["raw"] = {"name": COMPANY, "code": SYMBOL, "industry": INDUSTRY}
            ingestion.ingest(_input("0_basic", payload=payload))
        if documents:
            evidence = []
            for index, (text, url) in enumerate((
                (public_text, trusted_url),
                (generic_text, "https://example.invalid/offline-fictional-industry.txt"),
            )):
                if text is None:
                    continue
                digest, path = ResearchDocumentStore(harness.root / "documents").put(
                    text.encode(), suffix="txt",
                )
                evidence.append(EvidenceInput(
                    provider="fictional-offline-fixture", source_kind="research_document",
                    title=f"纯虚构离线公共宏观材料 {index}", source_url=url,
                    content_ref=path, content_hash=digest, inline_content=text,
                    published_at=(datetime(2025, 8, 18, tzinfo=timezone.utc) if outside_window
                                  else datetime(2026, 8, 21 if future else 19, tzinfo=timezone.utc)),
                    observed_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
                    grade=EvidenceGrade.B, immutable_proof=True,
                    metadata={"evidence_mode": archive_mode},
                ))
            item = _input("7_industry", evidence=evidence,
                observed_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
                ingested_at=datetime(2026, 9, 1, tzinfo=timezone.utc))
            item.symbol = document_symbol
            document_snapshot = ingestion.ingest(item)
            if not version_proven:
                # Valid B evidence is admitted first; private-DB fault injection
                # then models loss/corruption of its historical version proof.
                for row in repo.list_snapshot_evidence(document_snapshot.id):
                    session.execute(update(MarketDataEvidence).where(MarketDataEvidence.id == row.id)
                                    .values(immutable_proof=False, metadata_json={}))
        gap = UziSnapshotAdapter().disclosed_gap(
            symbol=SYMBOL, market="CN_EQUITY", exchange_timezone="Asia/Shanghai",
            dimension="3_macro", as_of_date=AS_OF,
            reason_code="fictional_original_gap", message="fictional original gap",
        )
        gap.payload["_uzi_raw_result"] = {
            "data": {}, "error": "fictional original source timeout", "_timeout": True,
        }
        original = ingestion.ingest(gap)
        return {"id": original.id, "hash": original.content_hash,
                "payload": deepcopy(original.payload_json)}


def prepare(harness, *, cancellation=None, deadline_at=None):
    return macro_derivation.prepare_macro_derivation(
        harness.database,
        spec={key: getattr(harness.task, key) for key in
              ("id", "job_id", "symbol", "market", "exchange_timezone")},
        as_of_date=AS_OF, registry=harness.registry,
        deadline_at=monotonic() + 30 if deadline_at is None else deadline_at,
        cancellation=Event() if cancellation is None else cancellation,
    )


def persist(harness, packet, *, task=None):
    with harness.database.session() as session:
        repo = MarketDataRepository(session)
        ingest = macro_derivation.macro_derivation_ingest(
            repo, packet=packet, task=task or harness.task,
            as_of_date=AS_OF, registry=harness.registry,
        )
        snapshot = SnapshotIngestionService(repo).ingest(ingest)
        macro_derivation.validate_persisted_macro(repo, snapshot, packet)
        return snapshot.id


def assert_only_original(harness, original):
    with harness.database.session() as session:
        rows = MarketDataRepository(session).list_snapshots(dimension="3_macro")
        assert [row.id for row in rows] == [original["id"]]
        assert rows[0].content_hash == original["hash"]
        assert rows[0].payload_json == original["payload"]


def seed_full_text(harness, *, snapshot_binds_text):
    """Store a fictional source and full text without invoking any parser."""
    old = seed(harness, documents=False)
    store = ResearchDocumentStore(harness.root / "full-text-documents")
    source_hash, source_ref = store.put(b"fictional offline original document bytes", suffix="bin")
    full = "纯虚构办公地址。" * 12_000 + "\n\n纯虚构末尾材料：中美关系与出口管制。"
    inline, binding = archive_text(store, full, source_hash=source_hash, extraction={
        "extractor": "fictional-offline-text", "extractor_version": "fixture-v1",
        "page_count": 1, "pages_processed": 1, "pages_without_text": [],
    })
    assert binding["inline_truncated"] is True
    assert "出口管制" in full and "出口管制" not in inline
    evidence = EvidenceInput(
        provider="fictional-full-text-fixture", source_kind="research_document",
        title="纯虚构全文归档", source_url="https://example.invalid/fictional-full-text",
        content_ref=source_ref, content_hash=source_hash, inline_content=inline,
        published_at=datetime(2026, 8, 19, tzinfo=timezone.utc),
        observed_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        grade=EvidenceGrade.B, immutable_proof=True,
        metadata={"evidence_mode": "contemporaneous_archive", "text_extraction": binding},
    )
    with harness.database.session() as session:
        repo = MarketDataRepository(session)
        item = _input("7_industry", evidence=[evidence],
                      observed_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
                      ingested_at=datetime(2026, 9, 1, tzinfo=timezone.utc))
        if snapshot_binds_text:
            item.query_manifest["research_archive"] = {"text_extractions": [{
                "evidence_key": evidence.evidence_key(), "binding": deepcopy(binding),
            }]}
        source_snapshot = SnapshotIngestionService(repo).ingest(item)
        return {"old": old, "binding": binding, "inline": inline, "full": full,
                "source_snapshot_id": source_snapshot.id, "source_snapshot_hash": source_snapshot.content_hash}


def test_explicit_snapshot_full_text_binding_reaches_macro_and_survives_revision(harness):
    archive = seed_full_text(harness, snapshot_binds_text=True)
    packet = prepare(harness)
    assert packet["status"] == "completed", packet
    snippets = packet["replay"]["native_result"]["data"]["web_search_snippets"]
    assert snippets["geo_risk"] and "出口管制" in snippets["geo_risk"][0]["body"]
    snapshot_id = persist(harness, packet)
    with harness.database.session() as session:
        repo = MarketDataRepository(session)
        revision = repo.require_snapshot(snapshot_id)
        bindings = revision.query_manifest_json["research_archive"]["text_extractions"]
        proofs = [row for row in repo.list_snapshot_evidence(snapshot_id)
                  if row.source_kind == "research_document"]
        assert len(proofs) == 1
        assert proofs[0].inline_content == archive["inline"]
        assert {"evidence_key": proofs[0].evidence_key, "binding": archive["binding"]} in bindings
        assert verified_text_projection(proofs[0], archive["binding"])["content"] == archive["full"]
        assert repo.require_snapshot(archive["source_snapshot_id"]).content_hash == archive["source_snapshot_hash"]


def test_evidence_metadata_alone_cannot_upgrade_legacy_snapshot_to_full_text(harness):
    archive = seed_full_text(harness, snapshot_binds_text=False)
    # An unbound adjacent full-text file must not even be consulted by legacy
    # projection; its corruption cannot upgrade or break the old inline input.
    Path(archive["binding"]["text_content_ref"]).write_bytes(b"corrupt unbound fictional text")
    packet = prepare(harness)
    assert packet["status"] == "completed", packet
    snippets = packet["replay"]["native_result"]["data"]["web_search_snippets"]
    assert all(rows == [] for rows in snippets.values())
    assert packet["documents"][0]["inline_content"] == archive["inline"]
    assert_only_original(harness, archive["old"])


def test_same_evidence_explicit_full_text_binding_wins_without_upgrading_old_macro(harness):
    archive = seed_full_text(harness, snapshot_binds_text=True)
    with harness.database.session() as session:
        repo = MarketDataRepository(session)
        proof = next(row for row in repo.list_snapshot_evidence(archive["source_snapshot_id"])
                     if row.source_kind == "research_document")
        # Both selected snapshots reference exactly the same evidence row.
        # Only the industry snapshot explicitly froze its full-text binding.
        repo.add_snapshot_evidence(snapshot_id=archive["old"]["id"], evidence_id=proof.id)
        old_manifest = deepcopy(repo.require_snapshot(archive["old"]["id"]).query_manifest_json)
    packet = prepare(harness)
    assert packet["status"] == "completed", packet
    snippets = packet["replay"]["native_result"]["data"]["web_search_snippets"]
    assert snippets["geo_risk"] and "出口管制" in snippets["geo_risk"][0]["body"]
    assert len(packet["documents"]) == 1
    assert packet["documents"][0]["snapshot_id"] == archive["source_snapshot_id"]
    assert packet["documents"][0]["text_binding"] == archive["binding"]
    with harness.database.session() as session:
        repo = MarketDataRepository(session)
        old_snapshot = repo.require_snapshot(archive["old"]["id"])
        old_proof = next(row for row in repo.list_snapshot_evidence(old_snapshot.id)
                         if row.source_kind == "research_document")
        assert macro_derivation._bound_text(old_snapshot, old_proof) == (archive["inline"], None)
        assert old_snapshot.query_manifest_json == old_manifest
    assert_only_original(harness, archive["old"])


def test_corrupt_explicit_full_text_binding_is_rejected_before_native(harness, monkeypatch):
    archive = seed_full_text(harness, snapshot_binds_text=True)
    Path(archive["binding"]["text_content_ref"]).write_bytes(b"corrupt bound fictional text")
    replay = Mock(side_effect=AssertionError("corrupt explicit full text reached native"))
    monkeypatch.setattr(native_macro, "replay_macro", replay)
    try:
        packet = prepare(harness)
    except ValueError:
        pass
    else:
        assert packet["status"] == "missing_dependency"
    replay.assert_not_called()
    assert_only_original(harness, archive["old"])


def test_explicit_full_text_corruption_after_prepare_is_rejected_before_write(harness):
    archive = seed_full_text(harness, snapshot_binds_text=True)
    packet = prepare(harness)
    assert packet["status"] == "completed", packet
    Path(archive["binding"]["text_content_ref"]).write_bytes(b"corrupt after replay")
    with pytest.raises(ValueError):
        persist(harness, packet)
    assert_only_original(harness, archive["old"])


def test_public_macro_archive_replays_native_and_creates_new_immutable_revision(harness):
    old = seed(harness)
    assert COMPANY not in PUBLIC_TEXT + GENERIC_TEXT
    packet = prepare(harness)
    assert packet["status"] == "completed", packet
    expected = deepcopy(packet["replay"]["native_result"])
    assert expected["data"]["year"] == AS_OF.year
    assert expected["data"]["industry"] == INDUSTRY
    assert set(expected["data"]["web_search_snippets"]) == set(GROUPS)
    assert all(expected["data"]["web_search_snippets"].values())
    new_id = persist(harness, packet)
    assert new_id != old["id"]
    with harness.database.session() as session:
        repo = MarketDataRepository(session)
        original, revision = repo.require_snapshot(old["id"]), repo.require_snapshot(new_id)
        assert original.content_hash == old["hash"] and original.payload_json == old["payload"]
        assert revision.origin == "derived"
        assert revision.payload_json["_uzi_raw_result"] == expected
        assert revision.payload_json["_uzi_collector_original_result"] == old["payload"]["_uzi_raw_result"]
        projected = project_native_result(revision)
        assert {key: projected[key] for key in expected} == expected
        assert set(projected) - set(expected) <= {"_pipeline", "_market_data_gap"}
        assert "error" not in projected and "_timeout" not in projected
        receipt = revision.query_manifest_json["macro_derivation"]
        assert receipt["status"] == "completed"
        assert receipt["actual_source"] == native_macro.ARCHIVE_BASIS
        assert receipt["isolation_events"] == []
        assert receipt["document_selection"]["complete_archive_coverage_claimed"] is False
        assert any(row["dimension"] == "7_industry" for row in receipt["inputs"])


def test_only_consumed_archive_documents_count_as_macro_supporting_evidence(harness):
    seed(harness, public_text="纯虚构离线材料只描述办公地址。",
         generic_text=f"纯虚构离线材料：中美关系与出口管制涉及{INDUSTRY}。")
    packet = prepare(harness)
    assert packet["status"] == "completed", packet
    assert len(packet["documents"]) == len(packet["recording"]["corpus"]) == 2
    snippets = packet["replay"]["native_result"]["data"]["web_search_snippets"]
    assert {key for key, rows in snippets.items() if rows} == {"geo_risk"}
    consumed_urls = {row["url"] for row in snippets["geo_risk"]}
    snapshot_id = persist(harness, packet)
    with harness.database.session() as session:
        repo = MarketDataRepository(session)
        snapshot = repo.require_snapshot(snapshot_id)
        supporting = [row for row in repo.list_snapshot_evidence(snapshot_id)
                      if row.source_kind == "research_document"]
        assert {row.source_url for row in supporting} == consumed_urls
        assert len(supporting) == 1
        receipt = snapshot.query_manifest_json["macro_derivation"]
        assert receipt["supporting_document_count"] == 1
        assert len(receipt["recording"]["corpus"]) == 2
        assert {key for key, value in receipt["group_coverage"].items()
                if value["evidence_count"]} == {"geo_risk"}
        assert snapshot.payload_json["_uzi_raw_result"] == packet["replay"]["native_result"]


@pytest.mark.parametrize("cause", [
    "no_basic", "no_document", "future", "unproven", "unsupported_version",
    "outside_window", "other_symbol",
])
def test_missing_or_invalid_dependencies_do_not_call_native(harness, monkeypatch, cause):
    seed(harness, basic=cause != "no_basic", documents=cause != "no_document",
         future=cause == "future", version_proven=cause != "unproven",
         archive_mode="unproven_live_download" if cause == "unsupported_version" else "contemporaneous_archive",
         outside_window=cause == "outside_window",
         document_symbol="000001.SZ" if cause == "other_symbol" else SYMBOL)
    replay = Mock(side_effect=AssertionError("invalid archive reached native replay"))
    monkeypatch.setattr(native_macro, "replay_macro", replay)
    packet = prepare(harness)
    assert packet["status"] == "missing_dependency"
    replay.assert_not_called()
    snapshot_id = persist(harness, packet)
    with harness.database.session() as session:
        snapshot = MarketDataRepository(session).require_snapshot(snapshot_id)
        receipt = snapshot.query_manifest_json["macro_derivation"]
        assert receipt["missing_dependency"] is True
        assert receipt["refresh_on_dependency_change"] is True
        assert snapshot.query_manifest_json["collection_outcome"] == "unavailable"
        assert snapshot.query_manifest_json["degradation"]["retryable"] is False


@pytest.mark.parametrize("public_text", ["纯虚构材料只描述办公地址。", PUBLIC_TEXT.split("\n")[1]])
def test_native_neutral_defaults_remain_unassessed_not_observed_macro_facts(harness, public_text):
    seed(harness, public_text=public_text, generic_text="纯虚构材料只描述办公地址。")
    packet = prepare(harness)
    assert packet["status"] == "completed", packet
    native = packet["replay"]["native_result"]
    data = native["data"]
    assert data["rate_cycle"] == f"中性（{AS_OF.year} 货币政策）"
    assert data["fx_trend"] == "中性（人民币走势）"
    assert data["geo_risk"] == "中性（地缘风险）"
    assert data["commodity"] == "中性（大宗周期）"
    assert data["industry_macro_impact"] == "中性"
    snapshot_id = persist(harness, packet)
    with harness.database.session() as session:
        snapshot = MarketDataRepository(session).require_snapshot(snapshot_id)
        assert snapshot.payload_json["_uzi_raw_result"] == native
        coverage = snapshot.query_manifest_json["macro_derivation"]["group_coverage"]
        assert set(coverage) == set(GROUPS)
        for group in GROUPS:
            count = len(data["web_search_snippets"][group])
            assert coverage[group]["evidence_count"] == count
            assert coverage[group]["status"] == ("archive_evidence_present" if count else "unassessed")
            assert coverage[group]["verified_neutral_fact"] is False
            assert coverage[group]["factual_sentiment_verified"] is False
        assert all(coverage[group]["status"] == "unassessed" for group in GROUPS if group != "us_rate")


@pytest.mark.parametrize("url", [
    "https://pbc.gov.cn.evil.invalid/fictional", "https://evil.invalid/pbc.gov.cn/fictional",
    "https://cs.com.cn/fictional-outside-native-first-six-domains",
])
def test_non_authority_archive_cannot_populate_trusted_macro_groups(harness, url):
    seed(harness, trusted_url=url)
    packet = prepare(harness)
    assert packet["status"] == "completed"
    snippets = packet["replay"]["native_result"]["data"]["web_search_snippets"]
    assert all(snippets[group] == [] for group in GROUPS[:3])
    assert all(snippets[group] for group in GROUPS[3:])


@pytest.mark.parametrize("mutation", [
    "file_bytes", "inline_text", "document_hash", "source_url", "future_evidence",
    "unproven_version", "revoked_snapshot", "future_snapshot", "snapshot_hash",
])
def test_dependency_mutation_after_prepare_is_rejected_before_write(harness, mutation):
    original = seed(harness)
    packet = prepare(harness)
    assert packet["status"] == "completed", packet
    document = packet["documents"][0]
    with harness.database.session() as session:
        repo = MarketDataRepository(session)
        evidence = next(row for row in repo.list_snapshot_evidence(document["snapshot_id"])
                        if row.id == document["evidence_id"])
        if mutation == "file_bytes":
            # Deliberate corruption of this test's private temporary archive.
            Path(evidence.content_ref).write_bytes(b"fictional corrupted bytes")
        elif mutation == "revoked_snapshot":
            repo.require_snapshot(document["snapshot_id"]).revoked_at = datetime.now(timezone.utc)
        else:
            # Fault injection bypasses ORM immutability only in this private DB,
            # modeling corruption/races that the write-time validator must catch.
            evidence_changes = {
                "inline_text": {"inline_content": "changed fictional text"},
                "document_hash": {"content_hash": "0" * 64},
                "source_url": {"source_url": "https://example.invalid/changed"},
                "future_evidence": {"published_at": datetime(2026, 8, 21, tzinfo=timezone.utc)},
                "unproven_version": {"immutable_proof": False, "metadata_json": {}},
            }
            if mutation in evidence_changes:
                session.execute(update(MarketDataEvidence).where(MarketDataEvidence.id == evidence.id)
                                .values(**evidence_changes[mutation]))
            else:
                changes = {"published_at": datetime(2026, 8, 21, tzinfo=timezone.utc)} \
                    if mutation == "future_snapshot" else {"content_hash": "1" * 64}
                session.execute(update(MarketDataSnapshot)
                                .where(MarketDataSnapshot.id == document["snapshot_id"]).values(**changes))
    with pytest.raises(ValueError):
        persist(harness, packet)
    assert_only_original(harness, original)


@pytest.mark.parametrize("field,value", [
    ("symbol", "000001.SZ"), ("market", "HK_EQUITY"), ("as_of_date", "2026-08-19"),
    ("task_id", "other-task"), ("job_id", "other-job"), ("exchange_timezone", "UTC"),
])
def test_changed_task_scope_is_rejected_before_write(harness, field, value):
    original = seed(harness)
    packet = prepare(harness)
    packet["scope"] = {**packet["scope"], field: value}
    with pytest.raises(ValueError):
        persist(harness, packet)
    assert_only_original(harness, original)


@pytest.mark.parametrize("mutation", ["recording_hash", "native_source", "isolation_event", "recording_text"])
def test_replay_receipt_and_recording_must_still_bind_before_write(harness, mutation):
    original = seed(harness)
    packet = prepare(harness)
    assert packet["status"] == "completed"
    if mutation == "recording_hash":
        packet["replay"]["recording_hash"] = "0" * 64
    elif mutation == "native_source":
        packet["replay"]["source_sha256"] = "0" * 64
    elif mutation == "isolation_event":
        packet["replay"]["isolation_events"] = ["socket.connect"]
    else:
        packet["recording"]["queries"][0]["query"] += " changed"
    with pytest.raises(ValueError):
        persist(harness, packet)
    assert_only_original(harness, original)


def test_persisted_validator_rejects_native_envelope_mutation(harness):
    seed(harness)
    packet = prepare(harness)
    snapshot_id = persist(harness, packet)
    with harness.database.session() as session:
        repo = MarketDataRepository(session)
        snapshot = repo.require_snapshot(snapshot_id)
        broken = SimpleNamespace(**{
            column.name: deepcopy(getattr(snapshot, column.name))
            for column in MarketDataSnapshot.__table__.columns
        })
        broken.payload_json["_uzi_raw_result"]["data"]["rate_cycle"] = "fabricated verified neutral fact"
        with pytest.raises(ValueError):
            macro_derivation.validate_persisted_macro(repo, broken, packet)
        # The invalid detached copy is never written to the database.
        assert snapshot.payload_json["_uzi_raw_result"] == packet["replay"]["native_result"]


@pytest.mark.parametrize("cause", ["event", "job_cancel", "deadline"])
def test_finished_replay_cannot_write_after_cancellation_or_deadline(harness, cause):
    original = seed(harness)
    cancellation = Event()
    packet = prepare(harness, cancellation=cancellation)
    assert packet["status"] == "completed"
    if cause == "event":
        cancellation.set()
    elif cause == "deadline":
        packet["deadline_at"] = monotonic() - 1
    else:
        with harness.database.session() as session:
            session.get(MarketDataCollectionJob, harness.task.job_id).cancel_requested = True
    with pytest.raises(ValueError):
        persist(harness, packet)
    assert_only_original(harness, original)


@pytest.mark.parametrize("expired", [False, True])
def test_cancelled_or_expired_preparation_never_runs_native(harness, monkeypatch, expired):
    seed(harness)
    cancellation = Event()
    if not expired:
        cancellation.set()
    replay = Mock(side_effect=AssertionError("stopped preparation reached native"))
    monkeypatch.setattr(native_macro, "replay_macro", replay)
    packet = prepare(harness, cancellation=cancellation,
                     deadline_at=monotonic() - 1 if expired else monotonic() + 30)
    assert packet["status"] == ("timed_out" if expired else "cancelled")
    replay.assert_not_called()
