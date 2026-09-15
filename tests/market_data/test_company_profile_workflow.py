"""Explicit DC profile repair is separate from six-dimension financial facts."""

import json
from copy import deepcopy
from pathlib import Path
from time import monotonic
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from research_system.market_data import research_enrichment as enrichment
from research_system.market_data.company_profile_revision import (
    build_company_profile_scope,
    persist_company_profile_revisions,
)
from research_system.market_data.dimension_contracts import DEFAULT_DIMENSION_REGISTRY
from research_system.market_data.repository import MarketDataRepository
from research_system.strategies.database import StrategyDatabase
from tests.market_data.research_suggestion_fixtures import (
    profile_assessments,
    profile_suggestion,
)
from tests.market_data.test_company_profile_revision import proposal_for, seed
from tests.market_data.test_research_enrichment import _job, _response


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("profile workflow tests must not call real sources or models")
    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)


@pytest.fixture
def company(tmp_path):
    database = StrategyDatabase(f"sqlite:///{tmp_path / 'profile-workflow.sqlite'}")
    database.create_schema()
    with database.session() as session:
        state = seed(session, tmp_path, full=True)
        scope = build_company_profile_scope(state.repo, basic_snapshot=state.basic,
                                           source_snapshots=[state.source])
        data = SimpleNamespace(database=database, scope=scope,
                               proposal=proposal_for(state, scope), basic_id=state.basic.id,
                               source_id=state.source.id, url=state.proof.source_url,
                               basic_hash=state.basic.content_hash, source_hash=state.source.content_hash)
    yield data
    database.close()


class Provider:
    available = True
    settings = SimpleNamespace(uzi_llm_transient_retries=0)

    def __init__(self, company, *, propose=True):
        self.company, self.propose, self.calls = company, propose, []

    def parse(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        response = _response(None, self.company.url)
        response["proposals"] = []
        if self.propose or "company_profile_assessments" in kwargs["schema"].model_fields:
            suggestions = [profile_suggestion(self.company.proposal)] if self.propose else []
            response["company_profile_proposals"] = suggestions
            response["company_profile_assessments"] = profile_assessments(suggestions)
        return response


def run(company, provider, dimensions):
    return enrichment.run_research_enrichment(
        company.database, job_id=_job(company.database, dimensions=dimensions),
        registry=DEFAULT_DIMENSION_REGISTRY, provider=provider, deadline_at=monotonic() + 30,
        native_recompute=lambda *_args, **_kwargs: pytest.fail("must not score mixed old/new company dependencies"),
    )


@pytest.mark.parametrize("propose", [True, False])
def test_profile_fixture_wire_logs_all_fields_without_mutating_complete_proposal(company, propose):
    before = deepcopy(company.proposal)
    response = Provider(company, propose=propose).parse(schema=enrichment.CompanyResearchSuggestionGroup)
    suggestions = response["company_profile_proposals"]
    assert len(suggestions) == int(propose)
    if propose:
        assert set(suggestions[0]) == {"field", "new_value", "industry_basis", "classification_system", "citations", "classification_citations"}
        assert all(set(row) == {"source_snapshot_id", "evidence_id", "quote"} for row in suggestions[0]["citations"])
    assessments = {row["field"]: row for row in response["company_profile_assessments"]}
    assert len(response["company_profile_assessments"]) == len(assessments) == 3
    assert set(assessments) == {"name", "industry", "main_business"}
    assert assessments["industry"]["status"] == ("proposed" if propose else "unresolved")
    assert all(assessments[field]["status"] == "unresolved" for field in ("name", "main_business"))
    assert company.proposal == before


@pytest.mark.parametrize("damage", ["missing", "duplicate", "extra", "unknown_field"])
def test_company_wire_assessments_require_exactly_three_unique_profile_fields(company, damage):
    schema = enrichment.CompanyResearchSuggestionGroup
    response = Provider(company, propose=False).parse(schema=schema)
    assessments = response["company_profile_assessments"]
    if damage == "missing":
        assessments.pop()
    elif damage == "duplicate":
        assessments[2] = deepcopy(assessments[0])
    elif damage == "extra":
        assessments.append(deepcopy(assessments[0]))
    else:
        assessments[0]["field"] = "exchange"
    with pytest.raises(ValidationError):
        schema.model_validate(response)


@pytest.mark.parametrize("damage", ["proposed_without_candidate", "candidate_unresolved", "candidate_unchanged"])
def test_company_wire_proposed_assessment_matches_actual_candidate_fields(company, damage):
    schema = enrichment.CompanyResearchSuggestionGroup
    response = Provider(company).parse(schema=schema)
    if damage == "proposed_without_candidate":
        response["company_profile_proposals"] = []
    else:
        industry = next(row for row in response["company_profile_assessments"] if row["field"] == "industry")
        industry["status"] = "unresolved" if damage == "candidate_unresolved" else "unchanged"
    with pytest.raises(ValidationError):
        schema.model_validate(response)


def test_company_wire_all_unresolved_without_candidates_is_valid_not_forced_repair(company):
    schema = enrichment.CompanyResearchSuggestionGroup
    response = Provider(company, propose=False).parse(schema=schema)
    parsed = schema.model_validate(response)
    assert parsed.company_profile_proposals == []
    assert {row.field for row in parsed.company_profile_assessments} == {"name", "industry", "main_business"}
    assert all(row.status == "unresolved" for row in parsed.company_profile_assessments)


@pytest.mark.parametrize("dimensions", [("0_basic",), ("0_basic", "7_industry")])
def test_explicit_basic_scope_can_repair_identity_without_rewriting_industry_metrics(company, dimensions):
    provider = Provider(company)
    result = run(company, provider, dimensions)

    assert len(provider.calls) == 1, "basic-only explicit research must run native B using existing documents"
    assert "company_profile_proposals" in provider.calls[0]["schema"].model_fields
    assert result["status"] == "partial", "identity repair alone is not complete native recomputation"
    assert result["rejected"] == []
    revisions = [row for row in result["revisions"] if row["dimension"] == "0_basic"]
    assert len(revisions) == 1
    assert result["company_profile_rebuilds"][0]["status"] == "missing_dependency"
    assert result["recomputations"][0]["status"] == "missing_dependency"
    with company.database.session() as session:
        repo = MarketDataRepository(session)
        revised = repo.require_snapshot(revisions[0]["snapshot_id"])
        assert revised.payload_json["raw"]["industry"] == "集成电路设计"
        assert revised.observed_at == repo.require_snapshot(company.basic_id).observed_at
        assert repo.require_snapshot(company.basic_id).content_hash == company.basic_hash
        assert repo.require_snapshot(company.source_id).content_hash == company.source_hash


def test_industry_only_scope_cannot_gain_profile_write_authority(company):
    provider = Provider(company)
    result = run(company, provider, ("7_industry",))

    assert len(provider.calls) == 1
    assert "company_profile_proposals" not in provider.calls[0]["schema"].model_fields
    assert "COMPANY_PROFILE_SCOPE=" not in provider.calls[0]["user_prompt"]
    assert result["status"] == "partial" and result["revisions"] == []
    assert result["groups"][0]["status"] == "model_failed"


def test_company_scope_references_existing_full_text_instead_of_duplicating_it(company):
    provider = Provider(company, propose=False)
    result = run(company, provider, ("0_basic", "7_industry"))

    assert result["status"] == "completed"
    prompt = provider.calls[0]["user_prompt"]
    assert "\nCOMPANY_PROFILE_SCOPE=" in prompt
    view = json.loads(prompt.split("\nCOMPANY_PROFILE_SCOPE=", 1)[1])
    assert view["target"]["snapshot_id"] == company.basic_id
    assert view["target"]["raw"]["industry"] is None
    assert view["allowed_fields"] == ["name", "industry", "main_business"]
    document = next(row for row in view["documents"] if row["source_snapshot_id"] == company.source_id)
    assert "inline_content" not in document
    assert document["text_reference"]["dimension"] == "7_industry"
    # Full extraction is presented once in the native group input. Its
    # already-fixed identity and character offsets authorize the profile quote.
    assert prompt.count("旧版裁切区。" * 50_000) == 1
    assert "不等于申万" in provider.calls[0]["system_prompt"]


@pytest.mark.parametrize("dimensions", [("0_basic",), ("0_basic", "7_industry"), ("7_industry",)])
def test_data_center_prompt_separates_research_from_explicit_revision_targets(company, dimensions):
    provider = Provider(company, propose=False)
    run(company, provider, dimensions)
    call = provider.calls[0]
    prompt = call["user_prompt"]
    assert prompt.startswith("DATA_CENTER_TASK_SCOPE=")
    scope = json.loads(prompt.splitlines()[0].split("=", 1)[1])
    assert scope["research"]["dimension_keys"] == ["7_industry", "15_events"]
    assert "research.dimensions 必须且只能返回维度 ['7_industry', '15_events']" in prompt
    assert "\n必须且只能返回维度" not in prompt
    assert [r["dimension"] for r in scope["proposals"]] == (["7_industry"] if "7_industry" in dimensions else [])
    if "0_basic" in dimensions:
        target = scope["company_profile_proposals"]
        assert target["snapshot_id"] == company.basic_id
        assert target["field_states"]["industry"] == {"exists": True, "value": None}
        assert target["field_states"]["main_business"] == {"exists": False, "value": None}
        assert "只与目标快照的 field_states 比较" in call["system_prompt"]
    else:
        assert "company_profile_proposals" not in scope
    # The native manual is still the complete, unchanged research specification.
    protocol = Path("domain_packs/finance_ashare/vendor/uzi_3_9_2/skill/references/task2.5-qualitative-deep-dive.md").read_text()
    assert prompt.split("NATIVE_TASK_2_5_PROTOCOL_BEGIN\n", 1)[1].split("\nNATIVE_TASK_2_5_PROTOCOL_END", 1)[0] == protocol


def test_shared_native_prompt_keeps_report_contract_without_revision_permission():
    kwargs = {"group_label": "B · Industry-Events", "dimensions": ("7_industry", "15_events"),
              "evidence_bundle": {"7_industry": {}, "15_events": {}}}
    report = enrichment._group_user_prompt(**kwargs)
    assert report == enrichment._group_user_prompt(**kwargs, workflow="report")
    assert enrichment._group_user_prompt(**kwargs, workflow="data_center") == report.replace(
        "\n必须且只能返回维度", "\nresearch.dimensions 必须且只能返回维度", 1,
    )
    assert "company_profile_proposals" not in report


def test_profile_permission_does_not_widen_financial_fact_fields(company):
    assert "0_basic" not in enrichment.FACT_FIELDS
    provider = Provider(company, propose=False)
    result = run(company, provider, ("7_industry",))
    assert result["status"] == "completed"
    assert provider.calls[0]["schema"] is enrichment.ResearchSuggestionGroup


def test_business_only_revision_recomputes_without_recollecting_unaffected_dimensions(company):
    proposal = company.proposal
    proposal.update(field="main_business", old_exists=False, old_value=None,
                    new_value="集成电路芯片设计及销售", industry_basis=None)
    calls = []

    def recompute(raw, **kwargs):
        calls.append((deepcopy(raw), kwargs))
        return {"offline_native_result": True}

    result = enrichment.run_research_enrichment(
        company.database, job_id=_job(company.database, dimensions=("0_basic",)),
        registry=DEFAULT_DIMENSION_REGISTRY, provider=Provider(company),
        deadline_at=monotonic() + 30, native_recompute=recompute,
    )

    assert result["status"] == "completed", result
    assert len(calls) == 1
    assert calls[0][0]["dimensions"]["0_basic"]["data"]["main_business"] == proposal["new_value"]
    assert calls[0][0]["dimensions"]["0_basic"]["error"] == "original source unavailable"
    assert result["company_profile_rebuilds"][0]["required"]["required_dimensions"] == []
    assert result["company_profile_rebuilds"][0]["status"] == "completed"
    assert result["recomputations"][0]["status"] == "completed"
    assert result["report_delivery_assessed"] is False
    original_inputs = result["recomputations"][0]["snapshot_inputs"]
    assert next(row for row in original_inputs if row["dimension"] == "7_industry")["snapshot_id"] == company.source_id
    with company.database.session() as session:
        repo = MarketDataRepository(session)
        assert repo.require_snapshot(company.basic_id).content_hash == company.basic_hash
        assert repo.require_snapshot(company.source_id).content_hash == company.source_hash


@pytest.mark.parametrize("accepted_fact", [False, True])
def test_pending_profile_from_previous_job_is_not_bypassed_by_later_research(company, monkeypatch, accepted_fact):
    with company.database.session() as session:
        applied = persist_company_profile_revisions(
            MarketDataRepository(session), proposals=[company.proposal], scope=company.scope,
            authorized_basic_snapshot_ids={company.basic_id},
            registry=DEFAULT_DIMENSION_REGISTRY, job_id="earlier-explicit-basic-repair",
        )
        assert applied["rejected"] == [] and len(applied["revisions"]) == 1
    if accepted_fact:
        # The acceptance validator has its own real-write regressions. Here
        # simulate its accepted-result signal to isolate recomputation routing.
        monkeypatch.setattr(enrichment, "persist_fact_revisions", lambda *_args, **_kwargs: {
            "revisions": [{"dimension": "7_industry", "snapshot_id": company.source_id}],
            "rejected": [],
        })
    result = run(company, Provider(company, propose=False), ("7_industry",))

    assert result["status"] == "partial", result
    pending = result["company_profile_rebuilds"][0]
    assert pending["basic_snapshot_id"] == applied["revisions"][0]["snapshot_id"]
    assert pending["status"] == "missing_dependency"
    assert "7_industry" in pending["pending_dimensions"]
    if accepted_fact:
        assert result["recomputations"][0]["status"] == "missing_dependency"
