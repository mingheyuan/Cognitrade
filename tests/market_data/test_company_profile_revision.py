"""Offline profile repair: no financial period, no implicit write authority."""

import importlib
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from threading import Event
from time import monotonic
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, update

from research_system.market_data.contracts import EvidenceInput, SeriesPointInput, canonical_hash, cutoff_at_for_date
from research_system.market_data.dimension_contracts import DEFAULT_DIMENSION_REGISTRY
from research_system.market_data.ingestion import SnapshotIngestionService
from research_system.market_data.models import MarketDataEvidence, MarketDataSnapshot, MarketDataSnapshotEvidence
from research_system.market_data.native_projection import project_native_result
from research_system.market_data.repository import MarketDataRepository
from research_system.market_data.research_archive import ResearchDocumentStore
from research_system.market_data.research_archive_text import archive_text
from tests.market_data.test_pit_core import AS_OF, NOW, OBSERVED, PUBLISHED, _input, _payload
from tests.market_data.test_pit_core import session as session  # noqa: PLC0414


TEXT = (
    "600519公司披露：公司名称为离线测试公司。公司所处的集成电路设计行业。"
    "公司主营业务为集成电路芯片设计及销售。"
    "独立分类说明：申银万国行业分类标准将600519归入数字芯片设计。"
)
VALUES = {"name": "离线测试公司", "industry": "集成电路设计", "main_business": "集成电路芯片设计及销售"}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("company profile tests must not access sources or models")
    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)


def module():
    return importlib.import_module("research_system.market_data.company_profile_revision")


def seed(session, tmp_path, *, full=False, bound=True, symbol="600519.SH", as_of=AS_OF):
    repo = MarketDataRepository(session)
    ingest = SnapshotIngestionService(repo)
    payload = _payload("0_basic")
    payload["raw"].update(name="旧名称", industry=None)
    payload["_uzi_raw_result"] = {"data": deepcopy(payload["raw"]), "error": "original source unavailable"}
    basic = ingest.ingest(_input("0_basic", payload=payload))
    store = ResearchDocumentStore(tmp_path / "documents")
    text = ("旧版裁切区。" * 50_000 if full else "") + TEXT
    digest, ref = store.put(b"offline original company document", suffix="pdf")
    if full:
        inline, binding = archive_text(store, text, source_hash=digest, extraction={
            "extractor": "offline-test", "page_count": 166, "pages_processed": 166,
            "pages_without_text": [],
        })
    else:
        inline, binding = text, None
    proof = EvidenceInput(
        provider="offline-issuer", source_url="https://archive.example/600519/company.pdf",
        source_kind="research_document", title="固定公司原文", content_hash=digest,
        content_ref=ref, inline_content=inline, published_at=PUBLISHED,
        observed_at=OBSERVED, grade="B", immutable_proof=True,
        metadata={"evidence_mode": "contemporaneous_archive", "security_code": "600519",
                  **({"text_extraction": binding} if binding else {})},
    )
    query = {"research_archive": {"text_extractions": [
        {"evidence_key": proof.evidence_key(), "binding": binding},
    ]}} if binding and bound else {}
    source = ingest.ingest(_input("7_industry", evidence=[proof], query_manifest=query).model_copy(
        update={"symbol": symbol, "as_of_date": as_of},
    ))
    return SimpleNamespace(repo=repo, basic=basic, source=source, text=text, binding=binding,
                           proof=repo.list_snapshot_evidence(source.id)[0])


def scope_for(state):
    return module().build_company_profile_scope(
        state.repo, basic_snapshot=state.basic, source_snapshots=[state.source],
    )


def proposal_for(state, scope, field="industry", value=None):
    document = next(row for row in scope["documents"] if row["evidence_id"] == state.proof.id)
    text = document["inline_content"]
    quote = TEXT
    start = text.index(quote)
    return {
        "snapshot_id": state.basic.id, "snapshot_hash": state.basic.content_hash,
        "field": field, "old_exists": field in state.basic.payload_json["raw"],
        "old_value": state.basic.payload_json["raw"].get(field),
        "new_value": value or VALUES[field],
        "industry_basis": "issuer_disclosure" if field == "industry" else None,
        "citations": [{
            "source_snapshot_id": state.source.id, "source_snapshot_hash": state.source.content_hash,
            "evidence_id": state.proof.id, "document_hash": state.proof.content_hash,
            "text_content_hash": document["text_content_hash"],
            "start": start, "end": start + len(quote), "quote": quote,
        }],
    }


def persist(state, scope, proposals, **kwargs):
    return module().persist_company_profile_revisions(
        state.repo, proposals=proposals, scope=scope,
        authorized_basic_snapshot_ids=kwargs.pop("authorized_basic_snapshot_ids", {state.basic.id}),
        registry=DEFAULT_DIMENSION_REGISTRY, job_id="offline-profile-repair", now=NOW,
        **kwargs,
    )


def test_profile_schema_is_available_without_financial_period_fields():
    assert "period_start" not in module().CompanyProfileProposal.model_json_schema()["properties"]
    assert "period_end" not in module().CompanyProfileProposal.model_json_schema()["properties"]


@pytest.mark.parametrize("full", [False, True], ids=["legacy-inline", "explicit-full-text"])
def test_new_profile_revision_keeps_quote_observation_and_old_inputs(session, tmp_path, full):
    state = seed(session, tmp_path, full=full)
    before = deepcopy(state.basic.payload_json)
    hashes = state.basic.content_hash, state.source.content_hash
    scope = scope_for(state)
    frozen_scope = deepcopy(scope)
    proposals = [proposal_for(state, scope, field) for field in VALUES]
    result = persist(state, scope, proposals)
    assert result["rejected"] == []
    assert len(result["revisions"]) == 1
    assert result["ready_to_freeze"] is False and result["rebuild_required"]
    revision = state.repo.require_snapshot(result["revisions"][0]["snapshot_id"])
    assert revision.id != state.basic.id and revision.dimension == "0_basic"
    assert revision.observed_at == state.basic.observed_at
    assert revision.payload_json["observed_at"] == before["observed_at"]
    assert revision.payload_json["raw"]["price"] == before["raw"]["price"]
    assert revision.payload_json["raw"]["market_cap"] == before["raw"]["market_cap"]
    assert revision.payload_json["_uzi_raw_result"] == before["_uzi_raw_result"]
    assert project_native_result(revision)["data"]["industry"] == VALUES["industry"]
    assert state.basic.payload_json == before
    assert (state.basic.content_hash, state.source.content_hash) == hashes
    assert scope == frozen_scope
    receipt = revision.query_manifest_json["company_profile_revision"]
    assert receipt["derived_at"] == NOW.isoformat()
    assert receipt["dependency_fingerprint"] == result["dependency_fingerprint"]
    assert receipt["facts"][1]["industry_basis"] == "issuer_disclosure"
    assert state.proof.id in revision.field_sources_json["raw.industry"]["evidence_ids"]
    assert revision.field_sources_json["industry"] == revision.field_sources_json["raw.industry"]
    assert state.proof.id in {row.id for row in state.repo.list_snapshot_evidence(revision.id)}
    if full:
        assert scope["documents"][0]["inline_content"] == state.text
        bindings = revision.query_manifest_json["research_archive"]["text_extractions"]
        assert {row["evidence_key"] for row in bindings} == {state.proof.evidence_key}
        assert bindings[0]["binding"] == state.binding


def test_scope_is_read_only_and_does_not_authorize_basic_writes(session, tmp_path):
    state = seed(session, tmp_path)
    scope = scope_for(state)
    result = persist(state, scope, [proposal_for(state, scope)], authorized_basic_snapshot_ids={state.source.id})
    assert result["revisions"] == []
    assert result["rejected"][0]["reason"] == "profile_target_not_authorized"


@pytest.mark.parametrize("change,reason", [
    ({"industry_basis": None}, "industry_basis_required"),
    ({"industry_basis": "classification", "classification_system": "申银万国行业分类标准"}, "classification_evidence_required"),
    ({"new_value": "半导体"}, "value_not_grounded"),
    ({"snapshot_hash": "f" * 64}, "input_hash_mismatch"),
    ({"old_value": "不存在的旧行业"}, "old_value_mismatch"),
    ({"field": "price", "new_value": "120"}, "proposal_invalid"),
    ({"new_value": " \n\t "}, "proposal_invalid"),
])
def test_invalid_fact_or_basis_cannot_write(session, tmp_path, change, reason):
    state = seed(session, tmp_path)
    scope = scope_for(state)
    count = session.scalar(select(func.count()).select_from(MarketDataSnapshot))
    result = persist(state, scope, [{**proposal_for(state, scope), **change}])
    assert result["revisions"] == [] and result["rejected"][0]["reason"] == reason
    assert session.scalar(select(func.count()).select_from(MarketDataSnapshot)) == count


def test_formal_classification_needs_its_explicit_standard_and_value_quote(session, tmp_path):
    state = seed(session, tmp_path)
    scope = scope_for(state)
    proposal = proposal_for(state, scope, value="数字芯片设计")
    proposal.update(industry_basis="classification", classification_system="申银万国行业分类标准",
                    classification_citations=deepcopy(proposal["citations"]))
    result = persist(state, scope, [proposal])
    assert result["rejected"] == [] and len(result["revisions"]) == 1
    bad = deepcopy(proposal)
    bad["classification_system"] = "不存在的行业标准"
    result = persist(state, scope, [bad])
    assert result["rejected"][0]["reason"] == "classification_not_grounded"


@pytest.mark.parametrize("changes", [{"symbol": "000001.SZ"}, {"as_of": AS_OF - timedelta(days=1)}])
def test_scope_rejects_foreign_stock_or_different_day(session, tmp_path, changes):
    state = seed(session, tmp_path, **changes)
    with pytest.raises(module().CompanyProfileRefusal, match="source_scope_mismatch"):
        scope_for(state)


@pytest.mark.parametrize("field,value,reason", [
    ("evidence_id", "unbound-proof", "evidence_not_bound"),
    ("source_snapshot_id", "unbound-snapshot", "evidence_not_bound"),
    ("text_content_hash", "f" * 64, "text_hash_mismatch"),
    ("document_hash", "f" * 64, "document_hash_mismatch"),
    ("start", 1, "excerpt_mismatch"),
    ("end", 10_000_000, "excerpt_bounds_invalid"),
])
def test_exact_citation_catalog_is_enforced(session, tmp_path, field, value, reason):
    state = seed(session, tmp_path)
    scope = scope_for(state)
    proposal = proposal_for(state, scope)
    proposal["citations"][0][field] = value
    result = persist(state, scope, [proposal])
    assert not result["revisions"] and result["rejected"][0]["reason"] == reason


def test_scope_tampering_is_not_new_authority(session, tmp_path):
    state = seed(session, tmp_path)
    scope = scope_for(state)
    proposal = proposal_for(state, scope)
    scope["documents"][0]["inline_content"] = "伪造公司原文"
    result = persist(state, scope, [proposal])
    assert not result["revisions"] and result["rejected"][0]["reason"] == "scope_hash_mismatch"


def test_full_text_corruption_after_scope_is_checked_before_writing(session, tmp_path):
    state = seed(session, tmp_path, full=True)
    scope = scope_for(state)
    proposal = proposal_for(state, scope)
    path = Path(state.binding["text_content_ref"])
    path.write_bytes(path.read_bytes()[:-1] + b"X")
    result = persist(state, scope, [proposal])
    assert not result["revisions"] and result["rejected"][0]["reason"] == "archive_text_hash_mismatch"


def test_legacy_snapshot_does_not_use_unbound_proof_full_text(session, tmp_path):
    state = seed(session, tmp_path, full=True, bound=False)
    scope = scope_for(state)
    assert scope["documents"][0]["inline_content"] == state.proof.inline_content
    assert "text_extraction" not in scope["documents"][0]
    assert VALUES["industry"] not in scope["documents"][0]["inline_content"]


@pytest.mark.parametrize("stop", ["cancelled", "deadline_exceeded", "worker_lease_lost"])
def test_cancellation_deadline_and_write_fence_prevent_new_revision(session, tmp_path, stop):
    state = seed(session, tmp_path)
    scope = scope_for(state)
    kwargs = {}
    if stop == "cancelled":
        event = Event()
        event.set()
        kwargs["cancellation"] = event
    elif stop == "deadline_exceeded":
        kwargs["deadline_at"] = monotonic() - 1
    else:
        kwargs["write_fence"] = lambda _session: False
    result = persist(state, scope, [proposal_for(state, scope)], **kwargs)
    assert not result["revisions"] and result["rejected"][0]["reason"] == stop


def test_scope_hash_binds_provenance_not_only_profile_label(session, tmp_path):
    state = seed(session, tmp_path)
    scope = scope_for(state)
    assert scope["scope_hash"] == canonical_hash({key: value for key, value in scope.items() if key != "scope_hash"})
    assert scope["target"]["snapshot_id"] == state.basic.id
    assert scope["source_snapshots"] == [{"snapshot_id": state.source.id, "snapshot_hash": state.source.content_hash}]


@pytest.mark.parametrize("field,required", [
    ("name", {"4_peers", "14_moat", "15_events", "17_sentiment", "18_trap"}),
    ("main_business", set()),
])
def test_name_and_business_changes_disclose_their_dependents(session, tmp_path, field, required):
    state = seed(session, tmp_path)
    scope = scope_for(state)
    result = persist(state, scope, [proposal_for(state, scope, field)])
    assert required == set(result["rebuild_required"]["direct_dimensions"])
    assert "3_macro" not in result["rebuild_required"]["direct_dimensions"]
    assert result["revisions"][0]["native_projection"]["data"][field] == VALUES[field]


def test_exact_cutoff_cannot_be_extended_past_target_day(session, tmp_path):
    state = seed(session, tmp_path)
    with pytest.raises(module().CompanyProfileRefusal, match="cutoff_outside_target_day"):
        module().build_company_profile_scope(
            state.repo, basic_snapshot=state.basic, source_snapshots=[state.source],
            cutoff_at=cutoff_at_for_date(AS_OF, "Asia/Shanghai") + timedelta(seconds=1),
        )


@pytest.mark.parametrize("mutation,reason", [
    ("revoked", "input_revoked"),
    ("payload", "input_content_hash_mismatch"),
    ("future_publication", "input_pit_invalid"),
    ("unproven_late_observation", "historical_version_unproven"),
])
def test_rechecks_inputs_and_pit_at_persist_not_only_scope_build(session, tmp_path, mutation, reason):
    state = seed(session, tmp_path)
    scope = scope_for(state)
    proposal = proposal_for(state, scope)
    source_id, proof_id = state.source.id, state.proof.id
    if mutation == "revoked":
        state.repo.revoke_snapshot(source_id, reason="offline test", revoked_by="offline", revoked_at=NOW)
    elif mutation == "payload":
        session.execute(update(MarketDataSnapshot).where(MarketDataSnapshot.id == source_id).values(payload_json={"raw": {"tampered": True}}))
    else:
        after_cutoff = cutoff_at_for_date(AS_OF, "Asia/Shanghai") + timedelta(hours=1)
        values = {"published_at": after_cutoff} if mutation == "future_publication" else {
            "observed_at": after_cutoff, "metadata_json": {"evidence_mode": "unknown"},
        }
        session.execute(update(MarketDataEvidence).where(MarketDataEvidence.id == proof_id).values(**values))
    session.expire_all()
    result = persist(state, scope, [proposal])
    assert not result["revisions"] and result["rejected"][0]["reason"] == reason


def test_binding_changes_after_scope_never_fall_back_to_inline(session, tmp_path):
    state = seed(session, tmp_path, full=True)
    scope = scope_for(state)
    proposal = proposal_for(state, scope)
    metadata = deepcopy(state.proof.metadata_json)
    metadata["text_extraction"]["version"] = "unsupported-new-version"
    session.execute(update(MarketDataEvidence).where(MarketDataEvidence.id == state.proof.id).values(metadata_json=metadata))
    session.expire_all()
    result = persist(state, scope, [proposal])
    assert not result["revisions"] and result["rejected"][0]["reason"] == "archive_text_binding_mismatch"


def test_later_profile_publication_is_refused_without_refreshing_quote_time(session, tmp_path):
    state = seed(session, tmp_path)
    later = OBSERVED + timedelta(minutes=1)
    original = state.proof
    evidence = EvidenceInput(
        provider="offline-later-issuer", source_url=original.source_url,
        source_kind="research_document", content_hash=original.content_hash,
        content_ref=original.content_ref, inline_content=original.inline_content,
        published_at=later, observed_at=later, grade="B", immutable_proof=True,
        metadata=deepcopy(original.metadata_json),
    )
    state.source = SnapshotIngestionService(state.repo).ingest(_input(
        "15_events", evidence=[evidence], published_at=later, observed_at=later,
    ))
    state.proof = state.repo.list_snapshot_evidence(state.source.id)[0]
    scope = scope_for(state)
    result = persist(state, scope, [proposal_for(state, scope)])
    assert not result["revisions"]
    assert result["rejected"][0]["reason"] == "profile_publication_after_quote_observation"
    assert state.basic.observed_at.replace(tzinfo=OBSERVED.tzinfo) == OBSERVED


def test_late_cancellation_rolls_back_new_snapshot_and_evidence_links(session, tmp_path, monkeypatch):
    state = seed(session, tmp_path)
    scope = scope_for(state)
    snapshot_count = session.scalar(select(func.count()).select_from(MarketDataSnapshot))
    link_count = session.scalar(select(func.count()).select_from(MarketDataSnapshotEvidence))
    event = Event()
    original = SnapshotIngestionService.ingest
    def cancelled_after_ingest(self, value):
        result = original(self, value)
        event.set()
        return result
    monkeypatch.setattr(SnapshotIngestionService, "ingest", cancelled_after_ingest)
    result = persist(state, scope, [proposal_for(state, scope)], cancellation=event)
    assert not result["revisions"] and result["rejected"][0]["reason"] == "cancelled"
    assert session.scalar(select(func.count()).select_from(MarketDataSnapshot)) == snapshot_count
    assert session.scalar(select(func.count()).select_from(MarketDataSnapshotEvidence)) == link_count


def test_repeated_same_job_and_frozen_proposals_reuse_revision(session, tmp_path):
    state = seed(session, tmp_path)
    scope = scope_for(state)
    proposals = [proposal_for(state, scope)]
    first = persist(state, scope, proposals)
    later = module().persist_company_profile_revisions(
        state.repo, proposals=proposals, scope=scope, authorized_basic_snapshot_ids={state.basic.id},
        registry=DEFAULT_DIMENSION_REGISTRY, job_id="offline-profile-repair", now=NOW + timedelta(minutes=1),
    )
    assert first["revisions"][0]["snapshot_id"] == later["revisions"][0]["snapshot_id"]
    assert first["dependency_fingerprint"] == later["dependency_fingerprint"]


def test_existing_generic_fact_schema_demonstrates_missing_identity_protocol(session, tmp_path):
    from pydantic import ValidationError
    from research_system.market_data.research_enrichment import FactProposal

    state = seed(session, tmp_path)
    with pytest.raises(ValidationError) as caught:
        FactProposal.model_validate({
            "snapshot_id": state.basic.id, "snapshot_hash": state.basic.content_hash,
            "dimension": "0_basic", "field": "industry", "old_exists": True,
            "old_value": None, "new_value": VALUES["industry"], "unit": "文本",
            "finding": "公司自述行业身份，没有另造财务报告期间。",
            "citations": [{"evidence_id": state.proof.id, "document_hash": state.proof.content_hash,
                           "start": 0, "end": len(TEXT), "quote": TEXT}],
        })
    missing = {error["loc"] for error in caught.value.errors() if error["type"] == "missing"}
    assert {("period_start",), ("period_end",), ("period_text",)} <= missing


def test_quote_series_and_source_timestamps_survive_profile_revision(session, tmp_path):
    state = seed(session, tmp_path)
    state.basic = SnapshotIngestionService(state.repo).ingest(_input(
        "0_basic", payload=deepcopy(state.basic.payload_json), series_points=[SeriesPointInput(
            series_key="price.close", point_at=PUBLISHED, effective_at=PUBLISHED,
            published_at=PUBLISHED, observed_at=OBSERVED, value={"close": 100.0},
            source_version="offline-quote-v1", metadata={"currency": "CNY"},
        )],
    ))
    scope = scope_for(state)
    document = scope["documents"][0]
    assert document["published_at"] == PUBLISHED.isoformat()
    assert document["observed_at"] == OBSERVED.isoformat()
    result = persist(state, scope, [proposal_for(state, scope)])
    assert not result["rejected"]
    before = state.repo.list_snapshot_series_points(state.basic.id)
    after = state.repo.list_snapshot_series_points(result["revisions"][0]["snapshot_id"])
    assert [(row.content_hash, row.value_json, row.observed_at) for row in before] == [
        (row.content_hash, row.value_json, row.observed_at) for row in after
    ]
