"""Explicit original-text refresh; local SQLite and local immutable bytes only."""

from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import update

from research_system.market_data.collection import MarketDataCollectionCoordinator
from research_system.market_data.contracts import EvidenceGrade
from research_system.market_data.dimension_contracts import DEFAULT_DIMENSION_REGISTRY
from research_system.market_data.ingestion import SnapshotIngestionService
from research_system.market_data.models import MarketDataEvidence, MarketDataSnapshot
from research_system.market_data.repository import MarketDataRepository
from research_system.market_data.research_collection import recover_research_documents
from research_system.market_data.resolver import SnapshotResolver, _database_utc
from research_system.strategies.database import StrategyDatabase
from tests.market_data.test_pit_core import _evidence, _input
from tests.market_data.test_research_archive import CUTOFF, LATER
from tests.market_data.test_research_archive_full_text import _archive


@pytest.fixture
def stage(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail(
            "an existing archived original must not fetch, search, or run a model"
        )

    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("requests.Session.request", forbidden)
    new, full, source = _archive(tmp_path / "original", monkeypatch)
    old = new.model_copy(deep=True)
    old.metadata.pop("text_extraction")
    old.metadata["archive_version"] = "research-archive-v2"
    database = StrategyDatabase(f"sqlite:///{tmp_path / 'refresh.sqlite'}")
    database.create_schema()
    settings = SimpleNamespace(workspace_root=tmp_path)
    coordinator = MarketDataCollectionCoordinator(
        database,
        collector=forbidden,
        archive_collector=SimpleNamespace(collect=forbidden),
        settings=settings,
    )
    with database.session() as session:
        repo = MarketDataRepository(session)
        ingest = SnapshotIngestionService(repo)
        ingest.ingest(_input("0_basic"))
        item = ingest.ingest(
            _input(
                "8_materials", evidence=[old], observed_at=CUTOFF, ingested_at=CUTOFF
            )
        )
        original_id, original_hash = item.id, item.content_hash
        original_evidence = repo.list_snapshot_evidence(item.id)[0]
        old_evidence_id = original_evidence.id
        old_metadata = dict(original_evidence.metadata_json)

    def run(mode="force_all", *, cancellation=None, max_duration_seconds=600, archive_collector=None):
        job = coordinator.create_job(
            tenant_id="tenant",
            user_id="user",
            actor_id="user",
            symbols=["600519.SH"],
            start_date=date(2026, 8, 20),
            end_date=date(2026, 8, 20),
            dimensions=["8_materials"],
            mode=mode,
            include_research_archive=True,
            submit=False,
        )
        result = recover_research_documents(
            database,
            job_id=job["id"],
            settings=settings,
            registry=DEFAULT_DIMENSION_REGISTRY,
            archive_collector=archive_collector or SimpleNamespace(collect=forbidden),
            cancellation=cancellation,
            max_duration_seconds=max_duration_seconds,
        )
        return result

    yield SimpleNamespace(
        database=database,
        run=run,
        old=old,
        new=new,
        full=full,
        source=source,
        original_id=original_id,
        original_hash=original_hash,
        old_evidence_id=old_evidence_id,
        old_metadata=old_metadata,
    )
    coordinator.close()
    database.close()


def test_force_all_reextracts_local_original_into_new_revision_without_rewriting_history(
    stage,
):
    result = stage.run()
    assert result["persisted_snapshot_count"] == 1
    assert result["attempts"][0]["acquisition_mode"] == "archived_original_reextraction"
    assert result["research_completion"] == "not_assessed"
    assert result["raw_enrichment"] == "not_run"
    with stage.database.session() as session:
        repo = MarketDataRepository(session)
        old = repo.require_snapshot(stage.original_id)
        revised = repo.require_snapshot(result["persisted_snapshots"][0]["snapshot_id"])
        assert old.content_hash == stage.original_hash
        assert revised.id != old.id and revised.payload_json == old.payload_json
        assert _database_utc(revised.observed_at) == CUTOFF
        assert revised.ingested_at > old.ingested_at
        assert (
            repo.get_evidence(stage.old_evidence_id).metadata_json == stage.old_metadata
        )
        proof = next(
            row
            for row in repo.list_snapshot_evidence(revised.id)
            if row.id != stage.old_evidence_id
        )
        binding = proof.metadata_json["text_extraction"]
        assert binding["page_count"] == binding["pages_processed"] == 166
        assert Path(binding["text_content_ref"]).read_text() == stage.full
        assert proof.content_hash == stage.old.content_hash
        assert _database_utc(proof.published_at) == stage.old.published_at
        assert _database_utc(proof.observed_at) == stage.old.observed_at
        assert proof.metadata_json["publication_time_basis"] == "observed_upper_bound"
        assert (
            proof.metadata_json["text_reextraction"]["source_evidence_id"]
            == stage.old_evidence_id
        )
        chosen = next(
            row
            for row in SnapshotResolver(repo)
            .coverage("600519.SH", "CN_EQUITY", date(2026, 8, 20))
            .dimensions
            if row.dimension == "8_materials"
        )
        assert chosen.snapshot_id == revised.id


def test_missing_or_stale_keeps_existing_qualified_archive_untouched(stage):
    result = stage.run("missing_or_stale")
    assert result["persisted_snapshot_count"] == 0
    assert result["attempts"][0]["reused"] is True
    with stage.database.session() as session:
        assert (
            session.get(MarketDataSnapshot, stage.original_id).content_hash
            == stage.original_hash
        )


def test_repeating_same_text_extraction_does_not_create_another_revision(stage):
    first = stage.run()
    assert first["persisted_snapshot_count"] == 1
    second = stage.run()
    assert second["persisted_snapshot_count"] == 0
    assert second["attempts"][0]["reused"] is True


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_force_all_discloses_broken_original_without_network_fallback_or_partial_revision(
    stage, damage
):
    path = Path(stage.old.content_ref)
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(b"wrong bytes")
    result = stage.run()
    assert result["persisted_snapshot_count"] == 0
    assert result["attempts"][0]["status"] == "failed"
    assert result["attempts"][0]["reason"] in {
        "archive_original_unavailable",
        "archive_content_corrupt",
    }


@pytest.mark.parametrize("boundary", ["cancelled", "deadline"])
def test_cancel_or_expired_budget_does_not_publish_refresh(stage, boundary):
    result = stage.run(
        cancellation=SimpleNamespace(is_set=lambda: boundary == "cancelled"),
        max_duration_seconds=0 if boundary == "deadline" else 600,
    )
    assert result["persisted_snapshot_count"] == 0
    assert result["attempts"][0]["status"] == "cancelled"


@pytest.mark.parametrize("mode", ["force_all", "missing_or_stale"])
def test_recovers_original_from_prior_same_day_revision_when_structured_refresh_has_none(
    stage, mode,
):
    with stage.database.session() as session:
        repo = MarketDataRepository(session)
        value = _input(
            "8_materials",
            published_at=CUTOFF - timedelta(minutes=1),
            observed_at=CUTOFF,
            ingested_at=CUTOFF,
        )
        fresh = SnapshotIngestionService(repo).ingest(value)
        fresh_id, fresh_hash = fresh.id, fresh.content_hash
    result = stage.run(mode)
    assert result["persisted_snapshot_count"] == 1
    with stage.database.session() as session:
        repo = MarketDataRepository(session)
        revised = repo.require_snapshot(result["persisted_snapshots"][0]["snapshot_id"])
        assert revised.payload_json == repo.require_snapshot(fresh_id).payload_json
        assert repo.require_snapshot(fresh_id).content_hash == fresh_hash
        assert repo.require_snapshot(stage.original_id).content_hash == stage.original_hash
        proof = next(
            row
            for row in repo.list_snapshot_evidence(revised.id)
            if row.source_kind == "research_document"
        )
        assert (
            proof.metadata_json["text_reextraction"]["source_snapshot_id"]
            == stage.original_id
        )
        assert proof.metadata_json["text_reextraction"]["network_requests"] == 0
        assert _database_utc(proof.published_at) == stage.old.published_at
        assert _database_utc(proof.observed_at) == stage.old.observed_at
        chosen = next(row for row in SnapshotResolver(repo).coverage(
            "600519.SH", "CN_EQUITY", date(2026, 8, 20)).dimensions
            if row.dimension == "8_materials")
        assert chosen.snapshot_id == revised.id


@pytest.mark.parametrize("damage", ["revoked", "future", "other_day", "other_symbol", "other_contract", "other_zone"])
def test_gap_fill_never_borrows_out_of_scope_or_invalid_prior_archive(stage, damage):
    changes = {
        "revoked": {"revoked_at": LATER},
        "future": {"published_at": CUTOFF + timedelta(days=1)},
        "other_day": {"as_of_date": date(2026, 8, 19)},
        "other_symbol": {"symbol": "000001.SZ"},
        "other_contract": {"contract_version": "unrelated-contract"},
        "other_zone": {"exchange_timezone": "UTC"},
    }
    with stage.database.session() as session:
        # Fault-inject a nonqualifying prior candidate; normal ingestion is
        # immutable. A failed cache match must not change the current raw.
        session.execute(update(MarketDataSnapshot).where(
            MarketDataSnapshot.id == stage.original_id).values(**changes[damage]))
        fresh = SnapshotIngestionService(MarketDataRepository(session)).ingest(_input(
            "8_materials", published_at=CUTOFF - timedelta(minutes=1),
            observed_at=CUTOFF, ingested_at=CUTOFF))
        fresh_id, fresh_hash = fresh.id, fresh.content_hash
    lookups = []

    def empty_collector(**scope):
        lookups.append(scope)
        return {"status": "missing", "evidence": [], "attempts": []}

    result = stage.run("missing_or_stale", archive_collector=SimpleNamespace(collect=empty_collector))
    assert result["persisted_snapshot_count"] == 0
    assert result["attempts"][0]["status"] == "missing"
    assert "acquisition_mode" not in result["attempts"][0]
    assert len(lookups) == 1 and lookups[0]["symbol"] == "600519.SH"
    with stage.database.session() as session:
        assert session.get(MarketDataSnapshot, fresh_id).content_hash == fresh_hash


@pytest.mark.parametrize(
    "mutation", ["raw_after_cutoff", "evidence_after_cutoff", "revoked", "source_bytes"]
)
def test_refresh_rechecks_inputs_before_persist_and_never_backdates_changed_data(
    stage, monkeypatch, mutation
):
    from research_system.market_data import research_collection

    reextract = research_collection._reextract_archived_originals

    def mutate_after_read(*args, **kwargs):
        result = reextract(*args, **kwargs)
        with stage.database.session() as session:
            repo = MarketDataRepository(session)
            row = repo.require_snapshot(stage.original_id)
            if mutation == "raw_after_cutoff":
                # Fault injection deliberately bypasses the ORM's ordinary
                # immutable-field guard to exercise commit-time validation.
                session.execute(
                    update(MarketDataSnapshot)
                    .where(MarketDataSnapshot.id == row.id)
                    .values(observed_at=CUTOFF + timedelta(days=1))
                )
            elif mutation == "evidence_after_cutoff":
                session.execute(
                    update(MarketDataEvidence)
                    .where(MarketDataEvidence.id == stage.old_evidence_id)
                    .values(
                        published_at=CUTOFF + timedelta(days=1),
                        observed_at=CUTOFF + timedelta(days=1),
                    )
                )
            elif mutation == "revoked":
                row.revoked_at = CUTOFF + timedelta(days=1)
            else:
                Path(stage.old.content_ref).write_bytes(b"changed after extraction")
        return result

    monkeypatch.setattr(
        research_collection, "_reextract_archived_originals", mutate_after_read
    )
    result = stage.run()
    assert result["persisted_snapshot_count"] == 0
    assert result["attempts"][0]["reason"] in {
        "archive_refresh_input_changed",
        "archive_content_corrupt",
    }


def test_cancellation_after_text_extraction_cannot_publish_revision(stage, monkeypatch):
    from threading import Event

    from research_system.market_data import research_collection

    event = Event()
    reextract = research_collection._reextract_archived_originals

    def cancel_after_read(*args, **kwargs):
        result = reextract(*args, **kwargs)
        event.set()
        return result

    monkeypatch.setattr(
        research_collection, "_reextract_archived_originals", cancel_after_read
    )
    result = stage.run(cancellation=event)
    assert result["persisted_snapshot_count"] == 0
    assert result["attempts"][0]["reason"] == "archive_cancelled_or_deadline"


def test_old_original_does_not_backdate_current_historical_raw_observation(stage):
    published = CUTOFF - timedelta(minutes=1)
    with stage.database.session() as session:
        repo = MarketDataRepository(session)
        value = _input(
            "8_materials",
            published_at=published,
            observed_at=LATER,
            ingested_at=LATER,
            evidence=_evidence(
                "8_materials",
                grade=EvidenceGrade.B,
                published_at=published,
                observed_at=LATER,
            ),
        )
        value.query_manifest["pit_mode"] = "historical_reconstruction"
        raw = SnapshotIngestionService(repo).ingest(value)
        raw_id = raw.id
    result = stage.run()
    assert result["persisted_snapshot_count"] == 1
    with stage.database.session() as session:
        repo = MarketDataRepository(session)
        revised = repo.require_snapshot(result["persisted_snapshots"][0]["snapshot_id"])
        assert revised.payload_json == repo.require_snapshot(raw_id).payload_json
        assert _database_utc(revised.observed_at) == LATER > stage.old.observed_at
        assert revised.evidence_grade == "B"
        proof = next(
            row
            for row in repo.list_snapshot_evidence(revised.id)
            if row.source_kind == "research_document"
        )
        assert proof.grade == stage.old.grade.value == "A"
        assert _database_utc(proof.observed_at) == stage.old.observed_at


def test_old_bundle_does_not_absorb_explicit_new_text_version(stage):
    from research_system.market_data.bundles import BundleMaterializer, BundlePublisher

    with stage.database.session() as session:
        repo = MarketDataRepository(session)
        for contract in DEFAULT_DIMENSION_REGISTRY:
            if contract.dimension not in {"0_basic", "8_materials"}:
                SnapshotIngestionService(repo).ingest(_input(contract.dimension))
        bundle = BundlePublisher(repo).publish(
            symbol="600519.SH",
            market="CN_EQUITY",
            as_of_date=date(2026, 8, 20),
            now=LATER,
        )
        bundle_id, bundle_hash = bundle.id, bundle.manifest_hash
        before = BundleMaterializer(repo).materialize(bundle_id)
    assert stage.run()["persisted_snapshot_count"] == 1
    with stage.database.session() as session:
        repo = MarketDataRepository(session)
        after = BundleMaterializer(repo).materialize(bundle_id)
        assert before.source_refs == after.source_refs
        assert before.snapshots == after.snapshots
        assert repo.get_bundle(bundle_id).manifest_hash == bundle_hash
