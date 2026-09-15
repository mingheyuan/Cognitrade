"""Real request/compiler/persistence integration with a deterministic model wire."""
# Imported pytest fixtures are intentionally referenced as test arguments.
# ruff: noqa: F401, F811

from copy import deepcopy
from time import monotonic
from types import SimpleNamespace

import pytest

from research_system.market_data import research_enrichment as enrichment
from research_system.market_data.dimension_contracts import DEFAULT_DIMENSION_REGISTRY
from research_system.market_data.ingestion import SnapshotIngestionService
from research_system.market_data.repository import MarketDataRepository
from tests.market_data.test_pit_core import _input, _payload
from tests.market_data.test_research_archive import LATER
from tests.market_data.test_research_enrichment import _job, _response, source


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr("socket.socket.connect", lambda *a, **kw: pytest.fail("offline test"))
    monkeypatch.setattr("socket.create_connection", lambda *a, **kw: pytest.fail("offline test"))


class Provider:
    available = True
    settings = SimpleNamespace(uzi_llm_transient_retries=0)

    def __init__(self, response):
        self.response = response
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return deepcopy(self.response)


def run(database, response):
    provider = Provider(response)
    native = []
    result = enrichment.run_research_enrichment(
        database, job_id=_job(database), registry=DEFAULT_DIMENSION_REGISTRY,
        provider=provider, deadline_at=monotonic() + 30,
        native_recompute=lambda raw, **kw: native.append(deepcopy(raw)) or {"raw": raw},
    )
    assert len(provider.calls) == 1
    assert provider.calls[0]["schema"] is enrichment.ResearchSuggestionGroup
    return result, native


def response_for(source):
    database, snapshot_id, proposal = source
    with database.session() as session:
        url = MarketDataRepository(session).list_snapshot_evidence(snapshot_id)[0].source_url
    return _response(proposal, url)


def test_compiler_rejection_keeps_job_partial_but_preserves_valid_sibling(source):
    response = response_for(source)
    bad = deepcopy(response["proposals"][0])
    bad["citations"][0]["quote"] = "MODEL_ONLY_TEXT_MUST_NOT_APPEAR_IN_SAFE_REJECTION"
    response["proposals"].append(bad)
    result, native = run(source[0], response)
    assert result["status"] == "partial"
    assert len(result["revisions"]) == len(native) == 1
    group = result["groups"][0]
    assert group["status"] == "completed"  # Model structure and adoption are separate.
    assert group["proposal_protocol"] == "uzi-research-suggestions-v1"
    assert group["proposal_rejections"] == [{
        "proposal_kind": "fact", "suggestion_index": 1, "reason": "quote_not_found",
    }]
    assert result["rejected"][0]["reason"] == "quote_not_found"
    assert "MODEL_ONLY_TEXT" not in str(result["rejected"])
    assert native[0]["dimensions"]["7_industry"]["data"]["growth"] == "25%"


def test_existing_null_field_is_bound_by_server_and_old_snapshot_is_immutable(source):
    database, original_id, proposal = source
    with database.session() as session:
        repo = MarketDataRepository(session)
        payload = _payload("7_industry")
        payload["raw"] = {"growth": None}
        payload["_uzi_raw_result"] = {"data": {"growth": None}, "error": "original source failure"}
        null_snapshot = SnapshotIngestionService(repo).ingest(_input(
            "7_industry", payload=payload, evidence=enrichment._evidence_inputs(repo, original_id),
            observed_at=LATER, ingested_at=LATER,
        ).model_copy(update={"source_priority": 0}))
        null_id, null_hash = null_snapshot.id, null_snapshot.content_hash
        null_payload = deepcopy(null_snapshot.payload_json)
    response = response_for(source)
    response["proposals"][0]["snapshot_id"] = null_id
    # The model never submits old_exists/old_value. The actual DB has the key.
    assert "old_exists" not in response["proposals"][0]
    result, native = run(database, response)
    assert result["status"] == "completed", result
    compiled = result["groups"][0]["proposals"][0]
    assert compiled["old_exists"] is True and compiled["old_value"] is None
    assert compiled["snapshot_hash"] == null_hash
    assert compiled["citations"] == proposal["citations"]
    assert len(result["revisions"]) == len(native) == 1
    with database.session() as session:
        repo = MarketDataRepository(session)
        old = repo.require_snapshot(null_id)
        assert old.content_hash == null_hash and old.payload_json == null_payload
        revised = repo.require_snapshot(result["revisions"][0]["snapshot_id"])
        assert revised.payload_json["raw"]["growth"] == "25%"
        assert revised.payload_json["_uzi_raw_result"] == null_payload["_uzi_raw_result"]


def test_uniquely_located_but_unsupported_value_still_fails_old_persistence(source):
    response = response_for(source)
    response["proposals"][0]["new_value"] = "95%"
    result, native = run(source[0], response)
    assert result["groups"][0]["proposal_rejections"] == []
    assert result["status"] == "partial"
    assert result["rejected"][0]["reason"] == "value_not_grounded"
    assert result["revisions"] == [] and native == []
