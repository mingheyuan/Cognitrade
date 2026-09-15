"""Different evidence IDs for one PDF are input versions, not extra sources.

Fictional PDF/text bytes, actual archive persistence, temporary DB only.
"""

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from time import monotonic
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from research_system.market_data import (
    macro_derivation,
    moat_derivation,
    native_macro,
    native_moat,
)
from research_system.market_data.contracts import EvidenceGrade, EvidenceInput
from research_system.market_data.ingestion import SnapshotIngestionService
from research_system.market_data.repository import MarketDataRepository
from research_system.market_data.research_archive import ResearchDocumentStore
from research_system.market_data.research_archive_text import archive_text
from research_system.market_data.research_collection import persist_research_documents
from research_system.market_data.resolver import SnapshotResolver
from tests.market_data.test_macro_derivation import (
    AS_OF,
    COMPANY,
    GENERIC_TEXT,
    PUBLIC_TEXT,
    seed,
)
from tests.market_data.test_macro_derivation import (
    harness as harness,  # noqa: PLC0414 - shared pytest fixture
)
from tests.market_data.test_pit_core import _input


@pytest.fixture(params=["moat", "macro"])
def kind(request):
    return request.param


def _module(kind):
    return moat_derivation if kind == "moat" else macro_derivation


def _replay_mock(monkeypatch, kind):
    replay = Mock(return_value={"status": "completed"})
    monkeypatch.setattr(native_moat if kind == "moat" else native_macro, f"replay_{kind}", replay)
    return replay


def _prepare(context, kind):
    return getattr(_module(kind), f"prepare_{kind}_derivation")(
        context.database, spec={key: getattr(context.task, key) for key in
            ("id", "job_id", "symbol", "market", "exchange_timezone")},
        as_of_date=AS_OF, registry=context.registry, deadline_at=monotonic() + 30,
        cancellation=Event())


def _seed_versions(context, *, pairs=1, tail=False, new_text=True, second_full=None, alternate_url=False):
    seed(context, documents=False)
    store = ResearchDocumentStore(context.root / "representation-documents")
    originals, full_versions = [], []
    for index in range(pairs + int(tail)):
        digest, path = store.put(f"%PDF-fictional-original-{index}".encode(), suffix="pdf")
        prefix = f"{COMPANY} 专利 核心技术 客户粘性 转换成本。\n"
        full = prefix + f"{COMPANY} 平台效应 市场份额 规模优势 研发投入。\n{PUBLIC_TEXT}\n{GENERIC_TEXT}"
        old = EvidenceInput(provider="offline-original", source_kind="research_document", title=f"{COMPANY}原件{index}",
            source_url=f"https://www.pbc.gov.cn/offline-fictional-{index}.pdf", content_hash=digest,
            content_ref=path, inline_content=prefix, published_at=datetime(2026, 8, 19, tzinfo=timezone.utc),
            observed_at=datetime(2026, 9, 1, tzinfo=timezone.utc), grade=EvidenceGrade.B, immutable_proof=True,
            metadata={"evidence_mode": "public_archive_reconstruction"})
        originals.append(old)
        if index < pairs and new_text:
            inline, binding = archive_text(store, full, source_hash=digest,
                extraction={"extractor": "offline-fixture", "page_count": 166, "pages_processed": 166})
            full_versions.append(old.model_copy(update={"inline_content": inline,
                "metadata": {**old.metadata, "text_extraction": binding}}))
            if second_full:
                alternate = full if second_full == "same" else full + "\n不同的已冻结提取表示。"
                other_inline, other_binding = archive_text(store, alternate, source_hash=digest,
                    extraction={"extractor": "offline-fixture-other", "page_count": 166, "pages_processed": 166})
                alternate_version = old.model_copy(update={"inline_content": other_inline,
                    "metadata": {**old.metadata, "text_extraction": other_binding},
                    **({"source_url": "https://example.invalid/offline-republication.pdf"} if alternate_url else {})})
                # Make the non-authority alias's evidence ID sort first to
                # expose source loss in a hash-only representative selector.
                full_versions.insert(0, alternate_version) if alternate_url else full_versions.append(alternate_version)
    with context.database.session() as session:
        repo = MarketDataRepository(session)
        ingestion = SnapshotIngestionService(repo)
        old_snapshot = ingestion.ingest(_input("7_industry", evidence=originals[:pairs],
            # Native availability survives a stricter audit-only gap, as in
            # actual adapter snapshots; do not inherit fixture status drift.
            query_manifest={"native_quality_status": "available"},
            observed_at=datetime(2026, 9, 1, tzinfo=timezone.utc), ingested_at=datetime(2026, 9, 1, tzinfo=timezone.utc)))
        before = {"id": old_snapshot.id, "hash": old_snapshot.content_hash,
                  "payload": deepcopy(old_snapshot.payload_json), "manifest": deepcopy(old_snapshot.query_manifest_json)}
        revision = persist_research_documents(repo, registry=context.registry, snapshot_id=old_snapshot.id,
            documents=full_versions, collection_task_id=context.task.id, job_id=context.task.job_id)
        proofs = repo.list_snapshot_evidence(revision.id)
        old_ids = {row.id for row in proofs if not (row.metadata_json or {}).get("text_extraction")}
        full_ids = {row.id for row in proofs if (row.metadata_json or {}).get("text_extraction")}
        if new_text:
            assert old_ids and full_ids and old_ids.isdisjoint(full_ids)  # Actual persisted two-version shape.
        if tail:
            ingestion.ingest(_input("8_materials", evidence=[originals[-1]],
                observed_at=datetime(2026, 9, 1, tzinfo=timezone.utc), ingested_at=datetime(2026, 9, 1, tzinfo=timezone.utc)))
        selection = SnapshotResolver(repo, registry=context.registry).coverage(
            context.task.symbol, context.task.market, AS_OF)
        assert next(row.snapshot_id for row in selection.dimensions if row.dimension == "7_industry") == revision.id
    return SimpleNamespace(before=before, originals=originals, full_versions=full_versions,
        old_ids=old_ids, full_ids=full_ids)


def test_new_bound_version_replaces_distinct_old_id_before_native(harness, monkeypatch, kind):
    versions = _seed_versions(harness)
    _replay_mock(monkeypatch, kind)
    packet = _prepare(harness, kind)
    assert len(packet["documents"]) == 1
    assert packet["documents"][0]["evidence_id"] in versions.full_ids
    assert len(packet["recording"]["corpus"]) == 1
    assert "研发投入" in packet["documents"][0]["inline_content"]


def test_four_pdfs_with_two_ids_do_not_exhaust_eight_document_budget(harness, monkeypatch, kind):
    versions = _seed_versions(harness, pairs=4, tail=True)
    _replay_mock(monkeypatch, kind)
    packet = _prepare(harness, kind)
    assert len(packet["documents"]) == 5
    assert {row["document_hash"] for row in packet["documents"]} == {row.content_hash for row in versions.originals}
    assert packet["document_selection"]["selection_limit_reached"] is False
    assert not versions.old_ids.intersection(row["evidence_id"] for row in packet["documents"])


def test_broken_new_bound_version_cannot_fall_back_to_retained_inline(harness, monkeypatch, kind):
    versions = _seed_versions(harness)
    Path(versions.full_versions[0].metadata["text_extraction"]["text_content_ref"]).write_bytes(b"broken local fixture")
    replay = _replay_mock(monkeypatch, kind)
    try:
        packet = _prepare(harness, kind)
    except ValueError:
        pass
    else:
        assert packet["status"] == "missing_dependency"
        assert not packet["documents"]
    replay.assert_not_called()


@pytest.mark.parametrize("alternate_url", [False, True])
def test_two_complete_bodies_for_one_original_refuse_ambiguous_selection(harness, monkeypatch, kind, alternate_url):
    _seed_versions(harness, second_full="different", alternate_url=alternate_url)
    replay = _replay_mock(monkeypatch, kind)
    with pytest.raises(ValueError, match="archive_text_representation_conflict"):
        _prepare(harness, kind)
    replay.assert_not_called()


def test_identical_complete_bodies_collapse_with_candidate_lineage(harness, monkeypatch, kind):
    versions = _seed_versions(harness, second_full="same")
    _replay_mock(monkeypatch, kind)
    packet = _prepare(harness, kind)
    again = _prepare(harness, kind)
    assert len(packet["documents"]) == 1
    assert packet["documents"] == again["documents"]
    selection = packet["document_representation_selection"]
    assert len(selection) == 1
    selected = selection[0]["selected"]
    full_lineage = [selected, *selection[0]["equivalent_full_text_inputs"]]
    assert {row["evidence_id"] for row in full_lineage} == versions.full_ids
    assert {row["evidence_id"] for row in selection[0]["superseded_inline_inputs"]} == versions.old_ids
    assert {row["snapshot_id"] for row in full_lineage}.issubset({row["snapshot_id"] for row in packet["inputs"]})


def test_inline_only_path_is_unchanged(harness, monkeypatch, kind):
    versions = _seed_versions(harness, pairs=2, new_text=False)
    _replay_mock(monkeypatch, kind)
    packet = _prepare(harness, kind)
    assert {row["evidence_id"] for row in packet["documents"]} == versions.old_ids
    assert [row["inline_content"] for row in packet["documents"]] == [row.inline_content for row in versions.originals]
    assert not packet.get("document_representation_selection")


def test_same_pdf_different_urls_retain_native_macro_authority_relationship(harness, monkeypatch):
    versions = _seed_versions(harness, second_full="same", alternate_url=True)
    _replay_mock(monkeypatch, "macro")
    packet = _prepare(harness, "macro")
    assert len(packet["documents"]) == 2
    assert {row["url"] for row in packet["documents"]} == {row.source_url for row in versions.full_versions}
    assert not versions.old_ids.intersection(row["evidence_id"] for row in packet["documents"])
    for row in packet["recording"]["queries"]:
        if row["key"] in native_macro.TRUSTED_KEYS:
            assert row["trusted"]
            assert all(item["url"].startswith("https://www.pbc.gov.cn/") for item in row["trusted"])


def test_real_native_envelope_and_old_snapshot_remain_unchanged(harness, kind):
    versions = _seed_versions(harness)
    packet = _prepare(harness, kind)
    assert packet["status"] == "completed"
    assert len(packet["recording"]["corpus"]) == 1
    with harness.database.session() as session:
        repo = MarketDataRepository(session)
        task = SimpleNamespace(**{**vars(harness.task), "dimension": "14_moat" if kind == "moat" else "3_macro"})
        ingest = getattr(_module(kind), f"{kind}_derivation_ingest")(repo, packet=packet, task=task,
            as_of_date=AS_OF, registry=harness.registry)
        assert ingest.payload["_uzi_raw_result"] == packet["replay"]["native_result"]
        assert ingest.query_manifest[f"{kind}_derivation"]["document_representation_selection"] == packet["document_representation_selection"]
        old = repo.require_snapshot(versions.before["id"])
        assert old.content_hash == versions.before["hash"]
        assert old.payload_json == versions.before["payload"]
        assert old.query_manifest_json == versions.before["manifest"]
