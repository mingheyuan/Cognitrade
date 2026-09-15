"""Bounded diagnostics explain compiler refusals without echoing model text."""

import json
from copy import deepcopy
from hashlib import sha256

import pytest

from research_system.market_data.research_suggestions import (
    resolve_fact_suggestions,
    resolve_profile_suggestions,
)
from tests.market_data.test_company_profile_workflow import (
    Provider as ProfileProvider,
)
from tests.market_data.test_company_profile_workflow import (
    company as company,  # noqa: PLC0414
)
from tests.market_data.test_company_profile_workflow import (
    run as run_profile,
)
from tests.market_data.test_research_enrichment import source as source  # noqa: PLC0414
from tests.market_data.test_research_suggestion_pipeline import response_for, run
from tests.market_data.test_research_suggestions import (
    fact,
    inputs,
    profile,
    profile_scope,
)

SECRET = "PRIVATE_MODEL_PAYLOAD_MUST_NOT_BE_LOGGED"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("diagnostics must not call a model or data source")
    monkeypatch.setattr("socket.socket.connect", deny)
    monkeypatch.setattr("socket.create_connection", deny)


def test_missing_quote_retains_input_identity_and_literal_value_support_without_adoption():
    frozen = inputs()
    original = deepcopy(frozen)
    suggestion = fact(citations=[{"evidence_id": "proof", "quote": SECRET}])
    expected = resolve_fact_suggestions([suggestion], inputs=frozen)
    diagnostics = []
    result = resolve_fact_suggestions([suggestion], inputs=frozen, diagnostics=diagnostics)
    assert result == expected and frozen == original
    assert len(diagnostics) == 1
    row = diagnostics[0]
    assert row["reason"] == "quote_not_found" and row["stage"] == "citation_resolution"
    assert row["field"] == "core_material" and row["dimension"] == "8_materials"
    assert row["input"]["snapshot_hash"] == "a" * 64
    citation = row["citation"]
    assert citation["collection"] == "citations" and citation["index"] == 0
    assert citation["quote"]["sha256"] == sha256(SECRET.encode()).hexdigest()
    assert citation["bound_document_count"] == 1
    assert citation["documents"][0]["document_hash"] == "b" * 64
    assert citation["documents"][0]["literal_value_present"] is True
    assert SECRET not in json.dumps(row)
    assert row["replayability"] == "fingerprints_only_not_original_wire"


def test_compound_old_value_has_specific_schema_paths_not_just_invalid_proposal():
    frozen = inputs()
    frozen["8_materials"]["raw"]["core_material"] = [SECRET]
    diagnostics = []
    result = resolve_fact_suggestions([fact()], inputs=frozen, diagnostics=diagnostics)
    row = diagnostics[0]
    assert result["rejected"][0]["reason"] == "resolved_proposal_invalid"
    assert row["stage"] == "proposal_schema"
    assert row["old_value"]["type"] == "list"
    assert row["validation"] and all(item["path"][0] == "old_value" for item in row["validation"])
    assert {item["code"] for item in row["validation"]} >= {"string_type", "int_type"}
    assert SECRET not in json.dumps(row)


def test_unknown_field_and_invalid_dto_never_echo_untrusted_keys_or_values():
    diagnostics = []
    result = resolve_fact_suggestions([fact(field=SECRET), fact(**{SECRET: SECRET}), fact()],
        inputs=inputs(), diagnostics=diagnostics)
    assert len(result["proposals"]) == 1 and len(diagnostics) == 2
    assert diagnostics[0]["field"] is None
    assert diagnostics[0]["requested_field"]["sha256"] == sha256(SECRET.encode()).hexdigest()
    assert diagnostics[1]["stage"] == "suggestion_schema"
    assert diagnostics[1]["validation"][0]["path"] == ["<unknown>"]
    assert SECRET not in json.dumps(diagnostics)


def test_profile_classification_failure_keeps_collection_and_bound_source_separate():
    scope = profile_scope()
    suggestion = profile(industry_basis="classification", classification_system=SECRET,
        classification_citations=[{"source_snapshot_id": "source", "evidence_id": "proof", "quote": SECRET}])
    expected = resolve_profile_suggestions([suggestion], scope=scope)
    diagnostics = []
    assert resolve_profile_suggestions([suggestion], scope=scope, diagnostics=diagnostics) == expected
    row = diagnostics[0]
    assert row["proposal_kind"] == "company_profile" and row["dimension"] == "0_basic"
    assert row["citation"]["collection"] == "classification_citations"
    assert row["citation"]["documents"][0]["source_snapshot_hash"] == "d" * 64
    assert SECRET not in json.dumps(row)


def test_no_cross_snapshot_binding_is_created_for_missing_evidence():
    diagnostics = []
    resolve_fact_suggestions([fact(citations=[{"evidence_id": SECRET, "quote": SECRET}])],
        inputs=inputs(), diagnostics=diagnostics)
    row = diagnostics[0]
    assert row["reason"] == "evidence_not_bound"
    assert row["citation"]["bound_document_count"] == 0
    assert row["citation"]["documents"] == []
    assert SECRET not in json.dumps(row)


def test_long_text_is_fingerprinted_but_never_retained_and_diagnostics_are_bounded():
    diagnostics = []
    large = SECRET * 10000
    suggestions = [fact(citations=[{"evidence_id": "proof", "quote": large}]) for _ in range(24)]
    resolve_fact_suggestions(suggestions, inputs=inputs(), diagnostics=diagnostics)
    assert len(diagnostics) == 24
    assert len(json.dumps(diagnostics).encode()) < 100_000
    assert all(row["citation"]["quote"]["length"] == len(large) for row in diagnostics)
    assert SECRET not in json.dumps(diagnostics)


def test_diagnostic_count_and_schema_error_paths_are_bounded_for_direct_callers():
    diagnostics = []
    bad = fact(**{SECRET + str(index): SECRET for index in range(100)})
    result = resolve_fact_suggestions([bad] * 40, inputs=inputs(), diagnostics=diagnostics)
    assert len(result["rejected"]) == 40 and len(diagnostics) == 32
    assert diagnostics[-1]["diagnostics_omitted"] == 8
    assert all(row["validation_truncated"] and len(row["validation"]) == 16 for row in diagnostics)
    assert SECRET not in json.dumps(diagnostics)


def test_diagnostic_failure_cannot_change_a_real_refusal_or_valid_sibling(monkeypatch):
    from research_system.market_data import (
        research_suggestion_diagnostics as diagnostic,
    )
    suggestions = [fact(field=SECRET), fact()]
    expected = resolve_fact_suggestions(suggestions, inputs=inputs())
    def failure(*args, **kwargs):
        raise RuntimeError(SECRET)
    monkeypatch.setattr(diagnostic, "_shape", failure)
    diagnostics = []
    result = resolve_fact_suggestions(suggestions, inputs=inputs(), diagnostics=diagnostics)
    assert result == expected
    assert diagnostics[0]["diagnostic_status"] == "details_unavailable"
    assert SECRET not in json.dumps(diagnostics)


@pytest.mark.parametrize("scope", [None, {}, {"target": None}, {"target": []}])
def test_invalid_profile_scope_has_same_refusal_with_or_without_diagnostics(scope):
    expected = resolve_profile_suggestions([profile()], scope=scope)
    diagnostics = []
    assert resolve_profile_suggestions([profile()], scope=scope, diagnostics=diagnostics) == expected
    assert len(diagnostics) == 1


def test_profile_diagnostics_reach_job_without_granting_identity_write_authority(company):
    class RejectedProvider(ProfileProvider):
        def parse(self, **kwargs):
            response = super().parse(**kwargs)
            response["company_profile_proposals"][0]["citations"][0]["quote"] = SECRET
            return response
    result = run_profile(company, RejectedProvider(company), ("0_basic",))
    assert not result["revisions"]
    row = result["groups"][0]["suggestion_rejection_diagnostics"][0]
    assert row["proposal_kind"] == "company_profile" and row["input"]["snapshot_hash"] == company.basic_hash
    assert row["reason"] == "quote_not_found" and SECRET not in json.dumps(result)


def test_diagnostics_reach_real_job_checkpoint_without_changing_sibling_revision(source):
    response = response_for(source)
    bad = deepcopy(response["proposals"][0])
    bad["citations"][0]["quote"] = SECRET
    response["proposals"].append(bad)
    result, native = run(source[0], response)
    assert result["status"] == "partial" and len(result["revisions"]) == len(native) == 1
    group = result["groups"][0]
    diagnostics = group["suggestion_rejection_diagnostics"]
    assert diagnostics[0]["reason"] == "quote_not_found"
    assert diagnostics[0]["input"]["snapshot_hash"] == source[2]["snapshot_hash"]
    assert SECRET not in json.dumps(result)
    from sqlalchemy import select

    from research_system.market_data.models import MarketDataCollectionJob
    with source[0].session() as session:
        job = session.scalar(select(MarketDataCollectionJob))
        job_id = job.id
        persisted = job.checkpoint_json["research_enrichment"]["groups"][0]
        assert persisted["suggestion_rejection_diagnostics"] == diagnostics
    from fastapi.encoders import jsonable_encoder

    from research_system.market_data.collection import MarketDataCollectionCoordinator
    coordinator = MarketDataCollectionCoordinator(source[0], collector=lambda **kw: pytest.fail("read only"))
    try:
        public = jsonable_encoder(coordinator.get_job(job_id, tenant_id="offline", user_id="offline"))
        assert public["checkpoint"]["research_enrichment"]["groups"][0]["suggestion_rejection_diagnostics"] == diagnostics
        assert SECRET not in json.dumps(public)
    finally:
        coordinator.close()
