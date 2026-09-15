"""Missing factual fields survive real provider wrapping without retaining content."""

import json
from itertools import combinations
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator
from jsonschema import ValidationError as SchemaValidationError
from pydantic import BaseModel, ValidationError, field_validator

from domain_packs.finance_ashare.subagents import uzi_llm_support as support
from domain_packs.finance_ashare.subagents.uzi_qualitative_deep_dive import (
    QualitativeEvidence,
)
from research_system.market_data.research_enrichment import ResearchProposalGroup
from research_system.runtime.providers import codex_provider

FIELDS = ("url", "finding")
CASES = [fields for size in (1, 2) for fields in combinations(FIELDS, size)]
CODE = "qualitative_factual_evidence_incomplete"
MESSAGE = "factual evidence requires URL and a concrete finding"
SECRET = "private-raw-quote-api-key-must-never-be-persisted"


def evidence(missing=()):
    row = {
        "source": "Frozen original document",
        "url": f"https://archive.example/{SECRET}",
        "retrieved_at": "2026-09-11",
        "finding": f"Archived fact with sensitive content {SECRET}",
    }
    row.update({field: " \n\t " if field == "finding" else None for field in missing})
    return row


def nested_payload(missing):
    return {
        "research": {
            "agent_reviewed": True,
            "group_label": "C · Cost-Transmission",
            "dimensions": [
                {
                    "dimension_id": dimension,
                    "evidence": [evidence(missing if index == 1 else ()), evidence()],
                    "conclusion": "Only frozen facts inform this conclusion.",
                }
                for index, dimension in enumerate(("8_materials", "9_futures"))
            ],
        },
        "proposals": [],
        "unresolved": [],
    }


@pytest.fixture(scope="module")
def record_validator():
    schema = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "domain_packs/finance_ashare/artifact_schemas/uzi_native_analysis.json"
        ).read_text()
    )
    return Draft202012Validator(
        schema["properties"]["recovery"]["properties"]["failure_records"]["items"]
    )


def failed_validation(model, payload):
    with pytest.raises(ValidationError) as caught:
        model.model_validate(payload)
    return caught.value


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*_args, **_kwargs):
        raise AssertionError("diagnostic regression must remain offline")

    monkeypatch.setattr("socket.create_connection", denied)
    monkeypatch.setattr("socket.socket.connect", denied)


@pytest.mark.parametrize("missing", CASES)
def test_each_missing_field_combination_has_finite_details(missing, record_validator):
    error = failed_validation(QualitativeEvidence, evidence(missing))
    record = support.model_failure_record(error, stage="qualitative_C")
    assert record["validation_codes"] == ["value_error"]
    assert record["validation_reasons"] == [
        {"field": "", "code": CODE, "missing_fields": list(missing)}
    ]
    assert error.errors(include_input=False)[0]["msg"] == f"Value error, {MESSAGE}"
    assert SECRET not in json.dumps(record) and "archive.example" not in json.dumps(
        record
    )
    record_validator.validate(record)


@pytest.mark.parametrize("missing", CASES)
@pytest.mark.parametrize("cancellable", [False, True])
def test_actual_provider_preserves_nested_sdk_validation_details(
    missing, cancellable, monkeypatch, record_validator
):
    settings = SimpleNamespace(llm_request_timeout_seconds=5, default_model="offline-test",
                               codex_reasoning_effort="low")
    provider = codex_provider.CodexProvider(settings)
    calls = []
    def model_output(**kwargs):
        calls.append(kwargs["output_schema"])
        return {"output_text": json.dumps(nested_payload(missing)), "metadata": {}}
    async def execute(**kwargs):
        return model_output(**kwargs)
    monkeypatch.setattr(provider, "execute_async", execute)
    with pytest.raises(codex_provider.StructuredOutputError) as caught:
        options = {
            "system_prompt": SECRET,
            "user_prompt": SECRET,
            "schema": ResearchProposalGroup,
        }
        if cancellable:
            provider.parse_cancellable(**options, cancellation=Event())
        else:
            provider.parse(**options)
    assert len(calls) == 1
    assert calls[0]['additionalProperties'] is False
    assert isinstance(caught.value.__cause__, ValidationError)
    record = support.model_failure_record(caught.value, stage="research_enrichment_C")
    assert record == {
        "stage": "research_enrichment_C",
        "kind": "agent_content_invalid",
        "error_type": "StructuredOutputError",
        "validation_fields": ["research.dimensions.1.evidence.0"],
        "validation_codes": ["value_error"],
        "validation_reasons": [
            {
                "field": "research.dimensions.1.evidence.0",
                "code": CODE,
                "missing_fields": list(missing),
            }
        ],
    }
    assert SECRET not in json.dumps(record) and "archive.example" not in json.dumps(
        record
    )
    record_validator.validate(record)


@pytest.mark.parametrize(
    "attribute", [[SECRET], ["url", SECRET], "url", {"url": SECRET}, ["url"] * 4, None]
)
def test_mutated_typed_details_are_not_persisted(attribute):
    error = failed_validation(QualitativeEvidence, evidence(("url",)))
    error.errors(include_input=False)[0]["ctx"]["error"].missing_fields = attribute
    record = support.model_failure_record(error, stage="qualitative_C")
    assert record["validation_reasons"] == [{"field": "", "code": CODE}]
    assert SECRET not in json.dumps(record)


@pytest.mark.parametrize("message", [MESSAGE, f"{MESSAGE}: {SECRET}", SECRET])
def test_untyped_error_context_never_establishes_missing_fields(message):
    class UntrustedResponse(BaseModel):
        value: str

        @field_validator("value")
        @classmethod
        def reject(cls, value):
            error = ValueError(message)
            error.missing_fields = ["url", SECRET]
            error.raw = value
            raise error

    error = failed_validation(UntrustedResponse, {"value": SECRET})
    record = support.model_failure_record(error, stage="qualitative_C")
    assert SECRET not in json.dumps(record)
    if message == MESSAGE:
        assert record["validation_reasons"] == [{"field": "value", "code": CODE}]
    else:
        assert "validation_reasons" not in record


def test_group_cause_merge_preserves_factual_details():
    error = failed_validation(QualitativeEvidence, evidence(FIELDS))
    wrapper = support.UziQualitativeValidationError(
        field="dimensions", code="qualitative_dimension_membership_mismatch"
    )
    wrapper.__cause__ = error
    record = support.model_failure_record(wrapper, stage="qualitative_C")
    assert record["validation_reasons"] == [
        {"field": "", "code": CODE, "missing_fields": list(FIELDS)},
        {"field": "dimensions", "code": "qualitative_dimension_membership_mismatch"},
    ]


@pytest.mark.parametrize(
    "missing",
    [
        [],
        [SECRET],
        ["url", "url"],
        ["url", "retrieved_at", "finding", "url"],
        "url",
        {"url": SECRET},
    ],
)
def test_artifact_schema_rejects_unbounded_or_invalid_details(
    missing, record_validator
):
    record = {
        "stage": "C",
        "kind": "agent_content_invalid",
        "error_type": "ValidationError",
        "validation_reasons": [{"field": "", "code": CODE, "missing_fields": missing}],
    }
    with pytest.raises(SchemaValidationError):
        record_validator.validate(record)


def test_legacy_factual_record_and_unknown_disclosure_are_unchanged(record_validator):
    record_validator.validate(
        {
            "stage": "C",
            "kind": "agent_content_invalid",
            "error_type": "ValidationError",
            "validation_reasons": [{"field": "", "code": CODE}],
        }
    )
    parsed = QualitativeEvidence.model_validate(
        {
            "source": "unknown",
            "url": None,
            "retrieved_at": None,
            "finding": "该原文没有披露此事实，无法确认。",
        }
    )
    assert parsed.url is None and parsed.retrieved_at is None


@pytest.mark.parametrize(
    "missing", [(), (SECRET,), ("url", "url"), ["url"], "url", None]
)
def test_typed_error_rejects_non_finite_or_ambiguous_constructor_details(missing):
    with pytest.raises(
        ValueError, match="^invalid factual evidence validation diagnostic$"
    ):
        support.UziFactualEvidenceValidationError(missing_fields=missing)


@pytest.mark.parametrize(
    "changes", [{"finding": ""}, {"finding": None}, {"url": ""}]
)
def test_prior_field_level_rejections_are_not_relabelled(changes):
    error = failed_validation(QualitativeEvidence, {**evidence(), **changes})
    record = support.model_failure_record(error, stage="qualitative_C")
    assert "validation_reasons" not in record
    assert record["validation_codes"] != ["value_error"]


def test_diagnostic_change_does_not_add_new_date_validation():
    # This diagnostic split does not silently strengthen the old schema.
    assert (
        QualitativeEvidence.model_validate(
            {**evidence(), "retrieved_at": " "}
        ).retrieved_at
        == " "
    )
