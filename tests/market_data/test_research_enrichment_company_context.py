"""Offline prompt capture and PIT/write-scope checks for DC company context."""

import json
from copy import deepcopy
from datetime import timedelta
from time import monotonic
from types import SimpleNamespace

import pytest

from research_system.market_data import research_enrichment as enrichment
from research_system.market_data.contracts import cutoff_at_for_date
from research_system.market_data.dimension_contracts import DEFAULT_DIMENSION_REGISTRY
from research_system.market_data.ingestion import SnapshotIngestionService
from research_system.market_data.repository import MarketDataRepository
from tests.market_data.research_suggestion_fixtures import fact_suggestion
from tests.market_data.research_prompt_fixtures import restore_background
from tests.market_data.test_pit_core import AS_OF, _evidence, _input, _payload
from tests.market_data.test_research_archive import LATER
from tests.market_data.test_research_enrichment import _job, _response, source


CONTEXT_MARKER = "\nREAD_ONLY_RESEARCH_CONTEXT="


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("company-context tests must not call live sources or models")

    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)


@pytest.fixture
def company(source):
    database, industry_id, proposal = source
    with database.session() as session:
        repo = MarketDataRepository(session)
        ingestion = SnapshotIngestionService(repo)
        evidence = enrichment._evidence_inputs(repo, industry_id)
        macro = ingestion.ingest(_input(
            "3_macro", evidence=evidence, observed_at=LATER, ingested_at=LATER,
        ))
        basic = ingestion.ingest(_input("0_basic"))
        financial = ingestion.ingest(_input("1_financials"))
        state = SimpleNamespace(
            database=database, proposal=proposal, industry_id=industry_id,
            basic_id=basic.id, basic_hash=basic.content_hash,
            financial_id=financial.id, macro_id=macro.id,
            url=evidence[0].source_url,
        )
    state.job_id = _job(database, dimensions=("3_macro",))
    return state


class CapturingProvider:
    available = True
    settings = SimpleNamespace(uzi_llm_transient_retries=0)

    def __init__(self, company, proposals=()):
        self.company = company
        self.proposals = list(proposals)
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        response = _response(None, self.company.url)
        response["research"]["group_label"] = "A · Macro-Policy"
        for row, dimension in zip(
            response["research"]["dimensions"], ("3_macro", "13_policy"), strict=True
        ):
            row["dimension_id"] = dimension
        response["proposals"] = [fact_suggestion(proposal) for proposal in self.proposals]
        return response


def run(company, provider):
    return enrichment.run_research_enrichment(
        company.database, job_id=company.job_id, registry=DEFAULT_DIMENSION_REGISTRY,
        provider=provider, deadline_at=monotonic() + 30,
        native_recompute=lambda *args, **kwargs: pytest.fail("no accepted revision to recompute"),
    )


def context(provider):
    assert len(provider.calls) == 1
    return json.loads(provider.calls[0]["user_prompt"].split(CONTEXT_MARKER, 1)[1])


def test_macro_model_receives_existing_company_and_frozen_scope(company):
    provider = CapturingProvider(company)
    result = run(company, provider)

    assert result["status"] == "completed", result
    assert "贵州茅台" in provider.calls[0]["user_prompt"], (
        "DC Macro-Policy dropped the stored 0_basic company name before the provider call"
    )
    view = context(provider)
    assert view["symbol"] == "600519.SH"
    assert view["market"] == "CN_EQUITY"
    assert view["as_of_date"] == AS_OF.isoformat()
    assert view["exchange_timezone"] == "Asia/Shanghai"
    assert view["cutoff_at"] == cutoff_at_for_date(AS_OF, "Asia/Shanghai").isoformat()
    assert view["dimensions"]["0_basic"]["data"]["industry"] == "白酒"
    assert view["dimensions"]["1_financials"]["data"]["revenue_history"] == [100, 110, 120]
    assert view["dimensions"]["7_industry"]["data"]["growth"] == "10%"
    assert view["requested_dimensions"] == ["3_macro"]
    assert {row["dimension"] for row in view["snapshot_inputs"]} == set(view["dimensions"])
    basic = next(row for row in view["snapshot_inputs"] if row["dimension"] == "0_basic")
    assert basic["snapshot_id"] == company.basic_id
    assert basic["content_hash"] == company.basic_hash
    assert view["prompt_view"]["read_only"] is True
    assert view["prompt_view"]["enrichment_version"] == result["version"] == "uzi-research-facts-v2"
    # Company/reference facts do not add URLs. The already-supplied macro
    # original is shared for paired research only, not for policy fact writes.
    group_prompt = provider.calls[0]["user_prompt"].split(CONTEXT_MARKER, 1)[0]
    group_evidence = json.loads(group_prompt.split("\n本组证据=", 1)[1])
    assert set(group_evidence) == {"3_macro", "13_policy"}
    assert group_evidence["3_macro"]["allowed_urls"] == [company.url]
    assert group_evidence["13_policy"]["allowed_urls"] == [company.url]
    assert group_evidence["13_policy"]["documents"] == []


@pytest.mark.parametrize("invalid", [
    "future_published", "future_observed", "future_evidence", "pit_unsafe",
    "other_symbol", "other_date", "revoked",
])
def test_read_only_context_excludes_future_or_out_of_scope_company_rows(company, invalid):
    payload = _payload("0_basic")
    payload["raw"]["name"] = "FUTURE_OR_OTHER_COMPANY_MUST_NOT_LEAK"
    kwargs = {"payload": payload}
    if invalid == "future_published":
        kwargs.update(published_at=LATER, observed_at=LATER, ingested_at=LATER)
    elif invalid == "future_observed":
        kwargs.update(observed_at=LATER, ingested_at=LATER)
    elif invalid == "future_evidence":
        kwargs["evidence"] = _evidence("0_basic", published_at=LATER, observed_at=LATER)
        kwargs["ingested_at"] = LATER
    elif invalid == "pit_unsafe":
        kwargs["query_manifest"] = {"pit_safe": False}
    value = _input("0_basic", **kwargs).model_copy(update={"source_priority": 0})
    if invalid == "other_symbol":
        value = value.model_copy(update={"symbol": "000001.SZ"})
    elif invalid == "other_date":
        value = value.model_copy(update={"as_of_date": AS_OF + timedelta(days=1)})
    with company.database.session() as session:
        candidate = SnapshotIngestionService(MarketDataRepository(session)).ingest(value)
        if invalid == "revoked":
            candidate.revoked_at = LATER
        candidate_id = candidate.id

    provider = CapturingProvider(company)
    assert run(company, provider)["status"] == "completed"
    view = context(provider)
    assert view["dimensions"]["0_basic"]["data"]["name"] == "贵州茅台"
    assert "FUTURE_OR_OTHER_COMPANY_MUST_NOT_LEAK" not in provider.calls[0]["user_prompt"]
    assert candidate_id not in {row["snapshot_id"] for row in view["snapshot_inputs"]}


def test_read_only_research_fact_does_not_authorize_revision(company):
    provider = CapturingProvider(company, proposals=[company.proposal])
    result = run(company, provider)

    assert "7_industry" in context(provider)["dimensions"]
    assert result["revisions"] == []
    assert result["rejected"][0]["reason"] == "snapshot_outside_requested_scope"
    with company.database.session() as session:
        original = MarketDataRepository(session).require_snapshot(company.industry_id)
        assert original.content_hash == company.proposal["snapshot_hash"]
        assert original.payload_json["raw"]["growth"] == "10%"


def test_background_url_is_not_admitted_as_group_citation(company):
    background_url = "https://example.test/company-background-only"
    payload = _payload("0_basic")
    payload["raw"]["company_url"] = background_url
    with company.database.session() as session:
        SnapshotIngestionService(MarketDataRepository(session)).ingest(
            _input("0_basic", payload=payload).model_copy(update={"source_priority": 0})
        )

    class InvalidCitationProvider(CapturingProvider):
        def parse(self, **kwargs):
            response = super().parse(**kwargs)
            response["research"]["dimensions"][0]["evidence"][0]["url"] = background_url
            return response

    provider = InvalidCitationProvider(company)
    result = run(company, provider)

    assert context(provider)["dimensions"]["0_basic"]["data"]["company_url"] == background_url
    assert result["status"] == "partial"
    assert result["groups"][0]["status"] == "model_failed"
    assert result["revisions"] == []


def test_missing_basic_remains_a_gap_with_explicit_scope_identity(company):
    with company.database.session() as session:
        MarketDataRepository(session).require_snapshot(company.basic_id).revoked_at = LATER
    provider = CapturingProvider(company)

    assert run(company, provider)["status"] == "completed"

    view = context(provider)
    assert view["symbol"] == "600519.SH"
    assert view["as_of_date"] == AS_OF.isoformat()
    assert "0_basic" not in view["dimensions"]
    assert "贵州茅台" not in provider.calls[0]["user_prompt"]


def test_context_uses_index_preserving_bounded_view_without_changing_snapshots(company):
    payload = _payload("1_financials")
    payload["raw"]["deep_detail"] = {"segments": [
        {"index": index, "description": "公司历史分部事实" * 200} for index in range(60)
    ]}
    with company.database.session() as session:
        snapshot = SnapshotIngestionService(MarketDataRepository(session)).ingest(
            _input("1_financials", payload=payload).model_copy(update={"source_priority": 0})
        )
        snapshot_id, original_hash = snapshot.id, snapshot.content_hash
        original_payload = deepcopy(snapshot.payload_json)

    provider = CapturingProvider(company)
    assert run(company, provider)["status"] == "completed"
    view = context(provider)
    # The sparse/table transport must restore exactly the old sampled view,
    # including original indexes, omission markers and literal string prefix.
    segments = restore_background(view)["dimensions"]["1_financials"]["data"]["deep_detail"]["segments"]
    assert len(segments) == 60
    assert segments[0]["index"] == 0 and segments[59]["index"] == 59
    assert segments[30] == {"__uzi_prompt_omitted__": True}
    assert len(segments[0]["description"]) == 800
    assert {row["reason"] for row in view["prompt_view"]["truncations"]} == {"sequence_sample", "text_prefix"}
    with company.database.session() as session:
        original = MarketDataRepository(session).require_snapshot(snapshot_id)
        assert original.content_hash == original_hash
        assert original.payload_json == original_payload


def test_oversized_context_fails_before_any_model_call(company):
    payload = _payload("1_financials")
    payload["raw"]["oversized_mapping_key_" + "x" * 500_000] = 1
    with company.database.session() as session:
        SnapshotIngestionService(MarketDataRepository(session)).ingest(
            _input("1_financials", payload=payload).model_copy(update={"source_priority": 0})
        )
    provider = CapturingProvider(company)

    result = run(company, provider)

    assert provider.calls == []
    assert result["status"] == "partial"
    assert result["revisions"] == []
    assert result["groups"][0]["status"] == "input_invalid"
    assert result["groups"][0]["model_request_started"] is False
    assert result["groups"][0]["failure"]["error_type"] == "UziInputBudgetExceeded"
