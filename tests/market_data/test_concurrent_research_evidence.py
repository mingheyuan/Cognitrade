"""Real file-SQLite writer races; no business providers or model requests."""

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from threading import Barrier, Lock, get_ident

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError

from research_system.market_data.contracts import EvidenceGrade, EvidenceInput
from research_system.market_data.dimension_contracts import DEFAULT_DIMENSION_REGISTRY
from research_system.market_data.ingestion import (
    SnapshotIngestionError,
    SnapshotIngestionService,
    _evidence_insert_if_absent,
)
from research_system.market_data.models import (
    MarketDataEvidence,
    MarketDataSnapshotEvidence,
)
from research_system.market_data.repository import MarketDataRepository
from research_system.market_data.research_collection import persist_research_documents
from research_system.strategies.database import StrategyDatabase
from tests.market_data.test_pit_core import _input
from tests.market_data.test_research_archive import CUTOFF


@pytest.fixture
def database(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("evidence race regression must stay offline")

    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("requests.Session.request", forbidden)
    db = StrategyDatabase(f"sqlite:///{tmp_path / 'evidence-race.sqlite'}")
    db.create_schema()
    yield db
    db.close()


def _document(**changes):
    body = "同一个不可变原件的离线正文，两个显式维度并发引用。"
    return EvidenceInput(
        provider="static.cninfo.com.cn",
        source_kind="research_document",
        source_url="https://static.cninfo.com.cn/finalpage/2026-08-19/shared.PDF",
        title="相同原件",
        mime_type="application/pdf",
        content_hash=sha256(body.encode()).hexdigest(),
        content_ref="/offline/original.pdf",
        inline_content=body,
        published_at=CUTOFF,
        observed_at=CUTOFF,
        grade=EvidenceGrade.A,
        immutable_proof=True,
        metadata={"field_paths": ["$research"], "purpose": "research_context"},
    ).model_copy(update=changes, deep=True)


def _synchronize_misses(monkeypatch, evidence_key, workers):
    lookup = MarketDataRepository.find_evidence_by_key
    barrier, lock, seen = Barrier(workers), Lock(), set()

    def find(self, key):
        result = lookup(self, key)
        if key == evidence_key and result is None:
            with lock:
                first = get_ident() not in seen
                seen.add(get_ident())
            if first:
                barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(MarketDataRepository, "find_evidence_by_key", find)


@pytest.mark.parametrize("workers", [2, 5])
def test_concurrent_sessions_same_evidence_insert_is_idempotent(
    database, monkeypatch, workers
):
    document = _document()
    _synchronize_misses(monkeypatch, document.evidence_key(), workers)

    def ingest(index):
        with database.session() as session:
            row = SnapshotIngestionService(
                MarketDataRepository(session)
            )._get_or_create_evidence(
                document.model_copy(
                    update={"metadata": {"dimension": f"dimension-{index}"}}
                )
            )
            return row.id

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(ingest, i) for i in range(workers)]
        ids = [future.result(timeout=15) for future in futures]
    assert len(set(ids)) == 1
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(MarketDataEvidence)) == 1
        proof = session.get(MarketDataEvidence, ids[0])
        assert proof.content_ref == document.content_ref
        assert proof.content_hash == document.content_hash


@pytest.mark.parametrize("versioned_text", [False, True])
def test_three_research_dimensions_share_one_proof_and_keep_all_snapshot_links(
    database,
    monkeypatch,
    tmp_path,
    versioned_text,
):
    dimensions = ("7_industry", "8_materials", "9_futures")
    original_ids, original_hashes = {}, {}
    with database.session() as session:
        ingestion = SnapshotIngestionService(MarketDataRepository(session))
        for dimension in dimensions:
            row = ingestion.ingest(_input(dimension))
            original_ids[dimension], original_hashes[dimension] = (
                row.id,
                row.content_hash,
            )
    document = _document()
    if versioned_text:
        from research_system.market_data.research_archive import ResearchDocumentStore
        from research_system.market_data.research_archive_text import archive_text

        _, binding = archive_text(
            ResearchDocumentStore(tmp_path / "text-versions"),
            document.inline_content,
            source_hash=document.content_hash,
            extraction={"extractor": "offline-fixture"},
        )
        document.metadata["text_extraction"] = binding
    _synchronize_misses(monkeypatch, document.evidence_key(), 3)

    def persist(dimension):
        with database.session() as session:
            row = persist_research_documents(
                MarketDataRepository(session),
                registry=DEFAULT_DIMENSION_REGISTRY,
                snapshot_id=original_ids[dimension],
                documents=[
                    document.model_copy(
                        update={
                            "metadata": {**document.metadata, "dimension": dimension}
                        },
                        deep=True,
                    )
                ],
                collection_task_id=None,
                job_id=f"archive-{dimension}",
                now=CUTOFF,
            )
            return row.id

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(persist, dimension) for dimension in dimensions]
        ids = [future.result(timeout=15) for future in futures]
    assert len(set(ids)) == 3
    with database.session() as session:
        repo = MarketDataRepository(session)
        proof = repo.find_evidence_by_key(document.evidence_key())
        assert proof is not None
        if versioned_text:
            for snapshot_id in ids:
                records = repo.require_snapshot(snapshot_id).query_manifest_json[
                    "research_archive"
                ]["text_extractions"]
                assert records == [
                    {
                        "evidence_key": proof.evidence_key,
                        "binding": proof.metadata_json["text_extraction"],
                    }
                ]
        assert (
            session.scalar(
                select(func.count())
                .select_from(MarketDataSnapshotEvidence)
                .where(
                    MarketDataSnapshotEvidence.evidence_id == proof.id,
                )
            )
            == 3
        )
        for dimension in dimensions:
            assert (
                repo.require_snapshot(original_ids[dimension]).content_hash
                == original_hashes[dimension]
            )
            assert proof.id not in {
                row.id for row in repo.list_snapshot_evidence(original_ids[dimension])
            }


def test_evidence_insert_is_not_committed_before_outer_transaction(database):
    document = _document()
    with (
        pytest.raises(RuntimeError, match="abort enclosing snapshot"),
        database.session() as session,
    ):
        SnapshotIngestionService(MarketDataRepository(session))._get_or_create_evidence(
            document
        )
        raise RuntimeError("abort enclosing snapshot")
    with database.session() as session:
        assert (
            MarketDataRepository(session).find_evidence_by_key(document.evidence_key())
            is None
        )


def test_same_claimed_content_hash_with_different_body_is_not_silently_reused(database):
    document = _document()
    with database.session() as session:
        stored = SnapshotIngestionService(
            MarketDataRepository(session)
        )._get_or_create_evidence(document)
        original_id, original_body = stored.id, stored.inline_content
    conflicting = document.model_copy(
        update={
            "inline_content": "different source bytes were claimed under the old hash"
        }
    )
    with (
        pytest.raises(
            SnapshotIngestionError, match="evidence content identity conflict"
        ),
        database.session() as session,
    ):
        SnapshotIngestionService(MarketDataRepository(session))._get_or_create_evidence(
            conflicting
        )
    with database.session() as session:
        assert (
            session.get(MarketDataEvidence, original_id).inline_content == original_body
        )


def test_reusing_existing_proof_never_updates_its_original_metadata_or_reference(
    database,
):
    document = _document()
    with database.session() as session:
        stored = SnapshotIngestionService(
            MarketDataRepository(session)
        )._get_or_create_evidence(document)
        original_id = stored.id
    candidate = document.model_copy(
        update={
            "content_ref": "/another/cache/location.pdf",
            "metadata": {"dimension": "other"},
        }
    )
    with database.session() as session:
        row = SnapshotIngestionService(
            MarketDataRepository(session)
        )._get_or_create_evidence(candidate)
        assert row.id == original_id
        assert row.content_ref == document.content_ref
        assert row.metadata_json == document.metadata


@pytest.mark.parametrize(
    "name,dialect", [("sqlite", sqlite.dialect), ("postgresql", postgresql.dialect)]
)
def test_atomic_insert_sql_targets_only_evidence_key_and_never_updates(name, dialect):
    statement = _evidence_insert_if_absent(
        name, {"evidence_key": "a" * 64, "provider": "fixture"}
    )
    sql = str(statement.compile(dialect=dialect()))
    assert "ON CONFLICT (evidence_key) DO NOTHING" in sql
    assert "RETURNING" in sql
    assert "UPDATE" not in sql
    assert "SAVEPOINT" not in sql


def test_unrelated_not_null_violation_is_not_swallowed(database):
    invalid = _document(provider=None)
    with pytest.raises(IntegrityError, match="NOT NULL"), database.session() as session:
        SnapshotIngestionService(MarketDataRepository(session))._get_or_create_evidence(
            invalid
        )
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(MarketDataEvidence)) == 0


def test_racing_different_bodies_cannot_both_succeed_under_the_same_claimed_hash(
    database, monkeypatch
):
    document = _document()
    _synchronize_misses(monkeypatch, document.evidence_key(), 2)

    def ingest(index):
        candidate = document.model_copy(
            update={"inline_content": f"different body {index}"}
        )
        try:
            with database.session() as session:
                SnapshotIngestionService(
                    MarketDataRepository(session)
                )._get_or_create_evidence(candidate)
            return "inserted"
        except SnapshotIngestionError as error:
            assert str(error) == "evidence content identity conflict"
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(ingest, i) for i in range(2)]
        statuses = [future.result(timeout=15) for future in futures]
    assert sorted(statuses) == ["conflict", "inserted"]
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(MarketDataEvidence)) == 1
