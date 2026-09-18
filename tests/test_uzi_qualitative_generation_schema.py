"""Expose the existing URL / bound-ID / unknown choices before generation."""
from copy import deepcopy
import json
from types import SimpleNamespace

from jsonschema import Draft202012Validator
import pytest
from pydantic import ValidationError

from domain_packs.finance_ashare.subagents import uzi_qualitative_deep_dive as q
from research_system.runtime.providers.codex_provider import CodexProvider
from research_system.runtime.providers.structured_output import strict_schema
from tests.test_uzi_qualitative_native_text_schema import group


def wire_payload(**changes):
    payload = group(short=True)
    for dimension in payload["dimensions"]:
        for evidence in dimension["evidence"]:
            evidence.setdefault("evidence_id", None)
            evidence.update(changes)
    return payload


def validator():
    schema = strict_schema(q._qualitative_group_json_schema())
    assert schema is not None and schema["type"] == "object"
    assert "anyOf" not in schema  # Codex requires an object, not a union, at root.
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@pytest.mark.parametrize("changes", [{"url": None}, {"url": None, "evidence_id": ""}])
def test_decoder_rejects_factual_null_url_without_binding_hint(changes):
    assert not validator().is_valid(wire_payload(**changes))
    # The unchanged Pydantic/native boundary already rejected these.
    with pytest.raises(ValidationError):
        q.QualitativeGroupResult.model_validate(wire_payload(**changes))


@pytest.mark.parametrize("changes", [
    {},
    {"retrieved_at": None},  # No invented date or mandatory host ID.
    {"source": "unknown", "url": None, "retrieved_at": None},
    {"url": None, "evidence_id": "fixed-document"},
])
def test_existing_url_disclosure_and_bound_id_alternatives_are_kept(changes):
    validator().validate(wire_payload(**changes))


def test_bound_id_is_still_resolved_and_not_trusted_without_context():
    payload = wire_payload(url=None, evidence_id="fixed-document", retrieved_at=None)
    validator().validate(payload)
    with pytest.raises(ValidationError):
        q.QualitativeGroupResult.model_validate(payload)
    context = {"frozen_citation_bindings": [{
        "evidence_id": "fixed-document", "url": "https://archive.example/document.pdf",
        "content_hash": "a" * 64, "observed_at": "2026-09-02T08:00:00Z",
    }]}
    parsed = q.QualitativeGroupResult.model_validate(payload, context=context)
    assert all(e.url == context["frozen_citation_bindings"][0]["url"]
               for d in parsed.dimensions for e in d.evidence)


def test_unknown_does_not_become_factual_and_same_url_can_support_two_facts():
    unknown = q.QualitativeGroupResult.model_validate(wire_payload(source="unknown", url=None))
    assert not q._group_citations_complete(unknown)
    same_url = q.QualitativeGroupResult.model_validate(wire_payload())
    assert q._group_citations_complete(same_url)


def test_generation_schema_does_not_mutate_model_or_cache_validation():
    original = deepcopy(q.QualitativeGroupResult.model_json_schema())
    q._qualitative_group_json_schema()
    assert q.QualitativeGroupResult.model_json_schema() == original
    parsed = q.QualitativeGroupResult.model_validate(wire_payload())
    assert q.QualitativeGroupResult.model_validate_json(parsed.model_dump_json()) == parsed


def test_real_report_provider_path_sends_conditional_citation_schema(monkeypatch):
    provider = CodexProvider(SimpleNamespace(llm_request_timeout_seconds=5,
        default_model="offline-test", codex_reasoning_effort="low",
        uzi_llm_transient_retries=0))
    captured = []

    async def execute(**kwargs):
        captured.append(kwargs["output_schema"])
        assert not Draft202012Validator(kwargs["output_schema"]).is_valid(wire_payload(url=None))
        return {"output_text": json.dumps(wire_payload()), "metadata": {}}

    monkeypatch.setattr(provider, "execute_async", execute)
    result = q._evaluate_group(provider, system_prompt="offline fixture",
        group_label="C · Cost-Transmission", dimensions=("8_materials", "9_futures"),
        evidence_bundle={}, user_prompt="offline fixture", research_profile="medium")
    assert result.agent_reviewed and len(captured) == 1
