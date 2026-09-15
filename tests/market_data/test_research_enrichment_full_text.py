"""DC fact proposals consume the exact text bound by their selected snapshot."""

import json
from copy import deepcopy
from pathlib import Path
from time import monotonic
from types import SimpleNamespace

import pytest

from research_system.market_data import research_enrichment as enrichment
from research_system.market_data.dimension_contracts import DEFAULT_DIMENSION_REGISTRY
from research_system.market_data.ingestion import SnapshotIngestionService
from research_system.market_data.repository import MarketDataRepository
from research_system.market_data.research_archive import ResearchDocumentStore
from research_system.market_data.research_archive_text import archive_text
from tests.market_data.research_suggestion_fixtures import fact_suggestion
from tests.market_data.test_pit_core import _input
from tests.market_data.test_research_archive import LATER
from tests.market_data.test_research_enrichment import _job, _response
from tests.market_data.test_research_enrichment import (
    no_network as no_network,  # noqa: PLC0414 - exported pytest fixture
)
from tests.market_data.test_research_enrichment import (
    source as source,  # noqa: PLC0414 - exported pytest fixture
)


def seed(source, tmp_path, *, bound=True, damage=None, oversized=False):
    database, original_id, proposal = source
    quote = proposal["citations"][0]["quote"]
    text = "600519公司报告。\n" + "正文说明。\n" * (90_000 if oversized else 25_000) + quote
    store = ResearchDocumentStore(tmp_path / "full-original")
    digest, path = store.put(b"%PDF-offline-original-with-full-text", suffix="pdf")
    inline, binding = archive_text(store, text, source_hash=digest, extraction={
        "extractor": "offline-test", "page_count": 166, "pages_processed": 166,
        "pages_without_text": [],
    })
    assert quote not in inline
    expected = deepcopy(binding)
    if damage == "version":
        binding["version"] = expected["version"] = "research-text-v999"
    elif damage == "binding":
        binding["text_characters"] += 1
    elif damage == "source":
        binding["source_content_hash"] = expected["source_content_hash"] = "f" * 64
    elif damage == "nul_path":
        text_digest = binding["text_content_hash"]
        binding["text_content_ref"] = expected["text_content_ref"] = f"/invalid/\x00/{text_digest[:2]}/{text_digest}.txt"
    with database.session() as session:
        repo = MarketDataRepository(session)
        payload = deepcopy(repo.require_snapshot(original_id).payload_json)
        evidence = enrichment._evidence_inputs(repo, original_id)[0].model_copy(update={
            "content_hash": digest, "content_ref": path, "inline_content": inline,
            "metadata": {"evidence_mode": "public_archive_reconstruction", "text_extraction": binding},
        })
        bindings = [{"evidence_key": evidence.evidence_key(), "binding": expected}]
        if damage == "duplicate":
            bindings *= 2
        elif damage == "unbound":
            bindings[0]["evidence_key"] = "not-bound-to-this-snapshot"
        elif damage == "invalid_set":
            bindings = {"unexpected": True}
        query = {"research_archive": {"text_extractions": bindings}} if bound else {}
        snapshot = SnapshotIngestionService(repo).ingest(_input(
            "7_industry", payload=payload, evidence=[evidence], query_manifest=query,
            observed_at=LATER, ingested_at=LATER,
        ).model_copy(update={"source_priority": 0}))
        proof = repo.list_snapshot_evidence(snapshot.id)[0]
        proposal = {**deepcopy(proposal), "snapshot_id": snapshot.id, "snapshot_hash": snapshot.content_hash,
                    "citations": [{"evidence_id": proof.id, "document_hash": proof.content_hash,
                                   "start": text.index(quote), "end": len(text), "quote": quote}]}
        state = SimpleNamespace(database=database, id=snapshot.id, hash=snapshot.content_hash,
                                payload=payload, inline=inline, text=text, binding=binding,
                                proposal=proposal, url=proof.source_url, original_id=original_id)
    if damage == "missing":
        Path(binding["text_content_ref"]).unlink()
    elif damage == "hash":
        body = text.encode()
        Path(binding["text_content_ref"]).write_bytes(body[:-1] + b"X")
    return state


class Provider:
    available = True
    settings = SimpleNamespace(uzi_llm_transient_retries=0)

    def __init__(self, state, *, after_parse=None, proposals=True):
        self.state, self.calls = state, []
        self.after_parse, self.proposals = after_parse, proposals

    def parse(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        if self.after_parse:
            self.after_parse()
        response = _response(self.state.proposal, self.state.url)
        if not self.proposals:
            response["proposals"] = []
        return response


def run(state, provider):
    return enrichment.run_research_enrichment(
        state.database, job_id=_job(state.database), registry=DEFAULT_DIMENSION_REGISTRY,
        provider=provider, deadline_at=monotonic() + 30,
        native_recompute=lambda raw, **kwargs: {"raw_after_derivation": raw},
    )


def documents(provider):
    prompt = provider.calls[0]["user_prompt"]
    encoded = prompt.split("\n本组证据=", 1)[1].split("\nREAD_ONLY_RESEARCH_CONTEXT=", 1)[0]
    return json.loads(encoded)["7_industry"]["documents"]


def test_bound_full_text_reaches_real_provider_and_revision_validator(source, tmp_path):
    state = seed(source, tmp_path)
    provider = Provider(state)
    result = run(state, provider)
    assert documents(provider)[0]["inline_content"] == state.text
    assert documents(provider)[0]["text_extraction"] == state.binding
    assert documents(provider)[0]["document_view_version"] == "uzi-research-document-view-v1"
    assert documents(provider)[0]["observed_at"] == LATER.isoformat()
    assert documents(provider)[0]["retrieved_at"] == LATER.date().isoformat()
    assert "不是发布日期" in provider.calls[0]["system_prompt"]
    assert result["rejected"] == [], result
    assert len(result["revisions"]) == len(result["recomputations"]) == 1
    with state.database.session() as session:
        repo = MarketDataRepository(session)
        original = repo.require_snapshot(state.id)
        assert original.content_hash == state.hash and original.payload_json == state.payload
        revision = repo.require_snapshot(result["revisions"][0]["snapshot_id"])
        assert revision.payload_json["raw"]["growth"] == "25%"
        assert revision.payload_json["_uzi_raw_result"] == state.payload["_uzi_raw_result"]
        assert revision.query_manifest_json["research_archive"] == original.query_manifest_json["research_archive"]
        assert repo.list_snapshot_evidence(revision.id)[0].inline_content == state.inline
        assert revision.query_manifest_json["research_enrichment"]["document_view_version"] == "uzi-research-document-view-v1"


@pytest.mark.parametrize("damage", ["missing", "hash", "version", "source", "binding", "duplicate", "unbound", "invalid_set", "nul_path"])
def test_invalid_explicit_binding_blocks_model_and_revision_without_prefix_fallback(source, tmp_path, damage):
    state = seed(source, tmp_path, damage=damage)
    provider = Provider(state)
    result = run(state, provider)
    assert provider.calls == []
    assert result["status"] == "partial"
    assert result["groups"][0]["status"] == "input_invalid"
    assert result["groups"][0]["input_errors"][0]["reason"].startswith("archive_text_")
    assert result["revisions"] == result["recomputations"] == []
    with state.database.session() as session:
        direct = enrichment.persist_fact_revisions(
            MarketDataRepository(session), proposals=[state.proposal], registry=DEFAULT_DIMENSION_REGISTRY,
            job_id="direct-validation", now=LATER,
        )
    assert not direct["revisions"]
    assert direct["rejected"][0]["reason"].startswith("archive_text_")


def test_unbound_proof_metadata_does_not_upgrade_legacy_input(source, tmp_path):
    state = seed(source, tmp_path, bound=False, damage="missing")
    provider = Provider(state)
    result = run(state, provider)
    document = documents(provider)[0]
    assert document["inline_content"] == state.inline
    assert "text_extraction" not in document and "document_view_version" not in document
    assert not result["revisions"]
    assert result["rejected"][0]["reason"] == "quote_not_found"


def test_text_corrupted_after_model_read_is_rejected_again_before_revision(source, tmp_path):
    state = seed(source, tmp_path)
    def damage():
        body = state.text.encode()
        Path(state.binding["text_content_ref"]).write_bytes(body[:-1] + b"X")
    provider = Provider(state, after_parse=damage)
    result = run(state, provider)
    assert documents(provider)[0]["inline_content"] == state.text
    assert not result["revisions"] and not result["recomputations"]
    assert result["rejected"][0]["reason"] == "archive_text_hash_mismatch"


def test_full_text_obeys_existing_request_budget_without_truncation(source, tmp_path):
    state = seed(source, tmp_path, oversized=True)
    provider = Provider(state)
    result = run(state, provider)
    assert provider.calls == []
    assert result["groups"][0]["failure"]["error_type"] == "UziInputBudgetExceeded"
    assert result["revisions"] == []


def test_legacy_prompt_and_proposal_remain_compatible(source):
    database, snapshot_id, proposal = source
    with database.session() as session:
        row = MarketDataRepository(session).list_snapshot_evidence(snapshot_id)[0]
        state = SimpleNamespace(database=database, proposal=proposal, url=row.source_url)
        inline = row.inline_content
    provider = Provider(state)
    result = run(state, provider)
    assert result["rejected"] == [] and len(result["revisions"]) == 1
    assert documents(provider)[0]["inline_content"] == inline
    assert "text_extraction" not in documents(provider)[0]


@pytest.mark.parametrize("damage", ["missing", "nul_path"])
def test_invalid_full_text_does_not_prevent_an_independent_research_group(source, tmp_path, damage):
    state = seed(source, tmp_path, damage=damage)
    with state.database.session() as session:
        repo = MarketDataRepository(session)
        SnapshotIngestionService(repo).ingest(_input(
            "3_macro", evidence=enrichment._evidence_inputs(repo, state.original_id),
            observed_at=LATER, ingested_at=LATER,
        ))
    job_id = _job(state.database, dimensions=("7_industry", "3_macro"))
    class MacroProvider(Provider):
        def parse(self, **kwargs):
            response = super().parse(**kwargs)
            response["research"]["group_label"] = "A · Macro-Policy"
            for row, dimension in zip(response["research"]["dimensions"], ("3_macro", "13_policy"), strict=True):
                row["dimension_id"] = dimension
            return response
    provider = MacroProvider(state, proposals=False)
    result = enrichment.run_research_enrichment(
        state.database, job_id=job_id, registry=DEFAULT_DIMENSION_REGISTRY,
        provider=provider, deadline_at=monotonic() + 30,
    )
    assert len(provider.calls) == 1
    assert {row["group"]: row["status"] for row in result["groups"]} == {
        "B · Industry-Events": "input_invalid", "A · Macro-Policy": "completed",
    }
    assert result["status"] == "partial" and result["revisions"] == []


def test_single_dimension_recovery_reads_group_partner_without_authorizing_edits(source, tmp_path):
    state = seed(source, tmp_path)
    with state.database.session() as session:
        repo = MarketDataRepository(session)
        partner = SnapshotIngestionService(repo).ingest(_input(
            "15_events", evidence=enrichment._evidence_inputs(repo, state.original_id),
            observed_at=LATER, ingested_at=LATER,
        ))
        partner_id, partner_hash = partner.id, partner.content_hash
        foreign_proposal = {**deepcopy(state.proposal), "snapshot_id": partner_id,
                            "snapshot_hash": partner_hash, "dimension": "15_events", "field": "catalyst"}
    class PartnerProvider(Provider):
        def parse(self, **kwargs):
            response = super().parse(**kwargs)
            response["proposals"].append(fact_suggestion(foreign_proposal))
            return response
    provider = PartnerProvider(state)
    result = run(state, provider)
    assert len(provider.calls) == 1
    encoded = provider.calls[0]["user_prompt"].split("\n本组证据=", 1)[1].split("\nREAD_ONLY_RESEARCH_CONTEXT=", 1)[0]
    inputs = json.loads(encoded)
    assert inputs["15_events"]["snapshot_id"] == partner_id
    assert inputs["15_events"]["documents"]
    assert inputs["15_events"]["revision_allowed"] is False
    assert inputs["7_industry"]["revision_allowed"] is True
    assert len(result["revisions"]) == 1 and result["revisions"][0]["dimension"] == "7_industry"
    assert result["rejected"][0]["reason"] == "snapshot_outside_requested_scope"
    with state.database.session() as session:
        assert MarketDataRepository(session).require_snapshot(partner_id).content_hash == partner_hash
