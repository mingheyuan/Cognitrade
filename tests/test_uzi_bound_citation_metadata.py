"""Native citation metadata is joined from fixed archives before validation."""
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from domain_packs.finance_ashare.subagents import uzi_qualitative_deep_dive as q
from domain_packs.finance_ashare.subagents.uzi_evidence_bindings import (
    citation_validation_context,
)
from domain_packs.finance_ashare.subagents.uzi_llm_support import model_failure_record
from research_system.runtime.providers.codex_provider import CodexProvider
from tests.test_uzi_qualitative_company_document_context import (
    attach_shared_document,
    fixed_rows,
)
from tests.test_uzi_validation_diagnostic_contract import VALIDATOR


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail("fixed citation binding must not access live sources")
    monkeypatch.setattr("socket.socket.connect", denied)


def inputs():
    rows = fixed_rows(historical=True)
    doc = attach_shared_document(rows)
    scoped, _, documents = q._fixed_report_inputs(rows, {})
    bundle = {key: q._dimension_context(scoped, key, stock_documents=documents)
              for key in q._DIMENSION_TYPES}
    return rows, doc, bundle, citation_validation_context(bundle, documents)


def citation(doc, *, by_id=True):
    return {"source": "frozen annual report", "finding": "公司公告披露研发投入增加。",
            "url": None if by_id else doc["url"], "retrieved_at": None,
            "evidence_id": doc["id"] if by_id else None}


@pytest.mark.parametrize("by_id", [True, False])
def test_fixed_archive_completes_only_metadata_without_mutating_inputs(by_id):
    rows, doc, _, context = inputs()
    before = deepcopy((rows, context))
    result = q.QualitativeEvidence.model_validate(citation(doc, by_id=by_id), context=context)
    assert result.url == doc["url"] and result.evidence_id == doc["id"]
    assert result.retrieved_at == doc["observed_at"]
    assert result.retrieved_at != doc["published_at"]
    assert result.finding == citation(doc)["finding"]
    assert (rows, context) == before


def test_codex_group_parse_receives_binding_context_before_required_field_check(monkeypatch):
    _, doc, bundle, context = inputs()
    value = {"agent_reviewed": True, "group_label": "A · Macro-Policy", "dimensions": [
        {"dimension_id": key, "evidence": [
            {**citation(doc), "finding": f"冻结文档中的独立事实 {i}"} for i in (1, 2)],
         "conclusion": "仅根据本次冻结资料分析。"} for key in ("3_macro", "13_policy")]}
    provider = CodexProvider(SimpleNamespace(default_model="gpt-5.6-luna", codex_reasoning_effort="low",
                                             llm_request_timeout_seconds=2))
    async def execute(**kwargs):
        # Context is host-only and must not accidentally go into SDK options.
        assert "validation_context" not in kwargs
        return {"output_text": json.dumps(value), "metadata": {"status": "completed"}}
    monkeypatch.setattr(provider, "execute_async", execute)
    result = q._evaluate_group(provider, system_prompt="fixed", group_label="A · Macro-Policy",
        dimensions=("3_macro", "13_policy"), evidence_bundle=bundle, user_prompt="fixed",
        validation_context=context)
    q._validate_group(result, ("3_macro", "13_policy"), bundle)
    assert all(e.retrieved_at == doc["observed_at"] for dim in result.dimensions for e in dim.evidence)


@pytest.mark.parametrize("case,code", [
    ("unknown", "unknown"),
    ("duplicate_id", "conflict"), ("ambiguous_url", "ambiguous"),
])
def test_binding_cannot_guess_or_mix_versions(case, code):
    _, doc, _, context = inputs()
    value = citation(doc, by_id=case != "ambiguous_url")
    rows = context["frozen_citation_bindings"]
    selected = next(row for row in rows if row["evidence_id"] == doc["id"])
    if case == "unknown":
        value["evidence_id"] = "other-stock-or-unfrozen-evidence"
    else:
        rows.append({**selected, "observed_at": "2099-01-01",
                     "evidence_id": "newer" if case == "ambiguous_url" else selected["evidence_id"]})
    before = deepcopy(context)
    with pytest.raises(ValidationError) as caught:
        q.QualitativeEvidence.model_validate(value, context=context)
    record = model_failure_record(caught.value, stage="qualitative_A")
    assert record["validation_reasons"][0]["code"] == f"qualitative_archive_binding_{code}"
    VALIDATOR.validate(record)
    assert context == before


@pytest.mark.parametrize("field,value", [
    ("url", "https://unrelated.example/document"),
    ("retrieved_at", "2099-01-01"),
])
def test_unique_document_id_joins_metadata_instead_of_rejecting_transcription(field, value):
    rows, doc, _, context = inputs()
    original = deepcopy(rows)
    context["citation_binding_audit"] = []
    supplied = {**citation(doc), field: value}
    result = q.QualitativeEvidence.model_validate(supplied, context=context)
    assert result.url == doc["url"]
    assert result.retrieved_at == doc["observed_at"]
    assert result.source == supplied["source"] and result.finding == supplied["finding"]
    assert rows == original and supplied[field] == value
    audit = context["citation_binding_audit"][0]
    assert audit["corrected_fields"] == [field]
    assert value not in json.dumps(audit)


def test_wrong_optional_id_does_not_block_a_unique_frozen_native_url():
    rows, doc, _, context = inputs()
    before = deepcopy(rows)
    context["citation_binding_audit"] = []
    value = {**citation(doc, by_id=False), "evidence_id": "mistyped-internal-hint"}
    result = q.QualitativeEvidence.model_validate(value, context=context)
    assert result.evidence_id == doc["id"]
    assert result.url == doc["url"] and result.retrieved_at == doc["observed_at"]
    assert result.finding == value["finding"] and rows == before
    assert context["citation_binding_audit"][0]["corrected_fields"] == ["evidence_id"]


def test_archive_without_observation_never_invents_retrieval_date():
    _, doc, _, context = inputs()
    selected = next(row for row in context["frozen_citation_bindings"]
                    if row["evidence_id"] == doc["id"])
    selected["observed_at"] = None
    original = deepcopy(context)
    result = q.QualitativeEvidence.model_validate(
        {**citation(doc), "retrieved_at": "2099-01-01"}, context=context)
    assert result.url == doc["url"] and result.retrieved_at is None
    assert context == original


def test_native_report_citations_exclude_internal_ids_and_keep_binding_audit(monkeypatch, tmp_path):
    from tests.test_uzi_qualitative_deep_dive import _deep_base, _Provider, _rows

    class BoundProvider(_Provider):
        def parse(self, **kwargs):
            result = super().parse(**kwargs)
            for dim in result.dimensions:
                for evidence in dim.evidence:
                    evidence.evidence_id = "internal-archive-id"
            return result

    # _evaluate_group would replace the audit with its validation context;
    # inject the joined result at that boundary to test publication + caching.
    provider = BoundProvider()

    def evaluate(*args, **kwargs):
        result = provider.parse(system_prompt=kwargs["system_prompt"],
                                user_prompt=kwargs["user_prompt"], schema=q.QualitativeGroupResult)
        result._citation_binding_audit = [{"evidence_id": "internal-archive-id",
                                           "filled_fields": ["retrieved_at"], "corrected_fields": []}]
        cache_path = tmp_path / f"{result.group_label}.json"
        q._write_qualitative_stage_cache(cache_path, result)
        restored = q._load_qualitative_stage_cache(cache_path)
        assert restored is not None and restored._citation_binding_audit == result._citation_binding_audit
        return restored

    monkeypatch.setattr(q, "_evaluate_group", evaluate)
    result = q.apply_qualitative_deep_dive(_deep_base(), snapshot_rows=_rows(), llm_provider=provider)
    assert result["qualitative_agent_execution"]["status"] == "completed"
    for dimension in result["qualitative_deep_dive"].values():
        for evidence in dimension["evidence"]:
            assert set(evidence) == {"source", "url", "finding", "retrieved_at"}
    assert len(result["qualitative_agent_execution"]["citation_binding_audits"]) == 3


def test_unknown_disclosure_stays_unknown_and_bare_url_has_no_invented_date():
    _, _, _, context = inputs()
    result = q.QualitativeEvidence.model_validate({"source": "unknown", "finding": "该事实没有公开依据。"}, context=context)
    assert result.url is None and result.retrieved_at is None
    # Native parsing may retain a bare URL without inventing an observation
    # date. The separate group check still rejects URLs outside frozen input.
    unbound = q.QualitativeEvidence.model_validate(
        {"source": "source", "url": "https://not-archived.example/fact",
         "finding": "缺少实际归档时间。"}, context=context)
    assert unbound.retrieved_at is None and unbound.evidence_id is None


def test_legacy_citation_can_identify_its_exact_archive_time():
    _, doc, _, context = inputs()
    rows = context["frozen_citation_bindings"]
    selected = next(row for row in rows if row["evidence_id"] == doc["id"])
    rows.append({**selected, "evidence_id": "newer", "observed_at": "2099-01-01T00:00:00Z"})
    result = q.QualitativeEvidence.model_validate(
        {**citation(doc, by_id=False), "retrieved_at": doc["observed_at"].replace("+00:00", "Z")}, context=context)
    assert result.evidence_id == doc["id"] and result.retrieved_at == doc["observed_at"]


def test_duplicate_bindings_with_identical_metadata_do_not_pick_arbitrary_id():
    _, doc, _, context = inputs()
    rows = context["frozen_citation_bindings"]
    selected = next(row for row in rows if row["evidence_id"] == doc["id"])
    rows.append({**selected, "evidence_id": "same-document-another-link"})
    result = q.QualitativeEvidence.model_validate(citation(doc, by_id=False), context=context)
    assert result.evidence_id is None and result.retrieved_at == doc["observed_at"]
