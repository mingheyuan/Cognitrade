"""Bounded validation diagnostics must never persist model input or messages."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ValidationError, field_validator

from domain_packs.finance_ashare.subagents.uzi_llm_support import model_failure_record
from domain_packs.finance_ashare.subagents.uzi_qualitative_deep_dive import (
    QualitativeEvidence,
)
from research_system.runtime.providers.errors import StructuredOutputError


class ResearchDimension(BaseModel):
    evidence: list[QualitativeEvidence]


class ResearchContent(BaseModel):
    dimensions: list[ResearchDimension]


class ResearchResponse(BaseModel):
    research: ResearchContent


def validation_failure(model, payload):
    with pytest.raises(ValidationError) as caught:
        model.model_validate(payload)
    return caught.value


def valid_evidence():
    return {
        "source": "Archived official filing",
        "url": "https://example.test/document.pdf",
        "finding": "The immutable filing contains this concrete fact.",
        "retrieved_at": "2026-09-08T09:00:00Z",
    }


@pytest.fixture(scope="module")
def failure_record_validator():
    path = (
        Path(__file__).resolve().parents[1]
        / "domain_packs/finance_ashare/artifact_schemas/uzi_native_analysis.json"
    )
    schema = json.loads(path.read_text())
    return Draft202012Validator(
        schema["properties"]["recovery"]["properties"]["failure_records"]["items"]
    )


@pytest.mark.parametrize(
    ("changes", "path_suffix", "reason"),
    [
        (
            {"url": "ftp://example.test/private-secret"},
            ".url",
            "qualitative_evidence_url_invalid",
        ),
        (
            {"source": "unknown", "finding": " \n\t "},
            "",
            "qualitative_unknown_gap_not_disclosed",
        ),
        ({"url": None}, "", "qualitative_factual_evidence_incomplete"),
        ({"finding": " \n\t "}, "", "qualitative_factual_evidence_incomplete"),
    ],
)
def test_real_qualitative_validators_have_exact_stable_diagnostics(
    changes, path_suffix, reason, failure_record_validator
):
    evidence = {**valid_evidence(), **changes}
    error = validation_failure(
        ResearchResponse,
        {
            "research": {
                "dimensions": [
                    {"evidence": [valid_evidence()]},
                    {"evidence": [valid_evidence(), evidence]},
                ]
            }
        },
    )
    record = model_failure_record(error, stage="research_enrichment_A")
    field = "research.dimensions.1.evidence.1" + path_suffix
    detail = {"missing_fields": list(changes)} if reason == "qualitative_factual_evidence_incomplete" else {}
    assert record == {
        "stage": "research_enrichment_A",
        "kind": "agent_content_invalid",
        "error_type": "ValidationError",
        "validation_fields": [field],
        "validation_codes": ["value_error"],
        "validation_reasons": [{"field": field, "code": reason, **detail}],
    }
    serialized = json.dumps(record)
    assert "private-secret" not in serialized
    assert "Business is growing" not in serialized
    assert "example.test" not in serialized
    failure_record_validator.validate(record)


def test_chained_structured_output_keeps_reason_but_no_wrapper_message(
    failure_record_validator,
):
    cause = validation_failure(
        QualitativeEvidence, {**valid_evidence(), "url": None}
    )
    inner = StructuredOutputError("model response private-secret")
    inner.__cause__ = cause
    outer = StructuredOutputError("SDK context private-secret")
    outer.__cause__ = inner
    record = model_failure_record(outer, stage="qualitative_B")
    assert record["error_type"] == "StructuredOutputError"
    assert record["validation_reasons"] == [
        {"field": "", "code": "qualitative_factual_evidence_incomplete", "missing_fields": ["url"]}
    ]
    assert "private-secret" not in json.dumps(record)
    failure_record_validator.validate(record)


@pytest.mark.parametrize(
    "message",
    [
        "private-secret raw response",
        "evidence URL must be HTTP(S): private-secret",
        "unknown evidence must explicitly explain the missing fact private-secret",
        "factual evidence requires URL, retrieval date and a concrete finding\nprivate-secret",
    ],
)
def test_arbitrary_value_error_messages_are_not_persisted_or_prefix_matched(message):
    class UntrustedResponse(BaseModel):
        value: str

        @field_validator("value")
        @classmethod
        def reject(cls, value):
            raise ValueError(message)

    error = validation_failure(UntrustedResponse, {"value": "private-secret input"})
    record = model_failure_record(error, stage="narrative")
    assert record == {
        "stage": "narrative",
        "kind": "agent_content_invalid",
        "error_type": "ValidationError",
        "validation_fields": ["value"],
        "validation_codes": ["value_error"],
    }
    assert "private-secret" not in json.dumps(record)


def test_unrecognized_standard_schema_failure_preserves_legacy_record_exactly():
    class LegacyResponse(BaseModel):
        value: int

    error = validation_failure(LegacyResponse, {"value": "private-secret"})
    assert model_failure_record(error, stage="old") == {
        "stage": "old",
        "kind": "agent_content_invalid",
        "error_type": "ValidationError",
        "validation_fields": ["value"],
        "validation_codes": ["int_parsing"],
    }


def test_known_and_unknown_failures_keep_associated_fields_without_raw_messages():
    error = validation_failure(
        ResearchDimension,
        {
            "evidence": [
                {**valid_evidence(), "url": "ftp://example.test/private-secret"},
                {**valid_evidence(), "url": None},
                {**valid_evidence(), "finding": None},
            ]
        },
    )
    record = model_failure_record(error, stage="qualitative_C")
    assert record["validation_reasons"] == [
        {"field": "evidence.0.url", "code": "qualitative_evidence_url_invalid"},
        {"field": "evidence.1", "code": "qualitative_factual_evidence_incomplete", "missing_fields": ["url"]},
    ]
    assert record["validation_fields"] == [
        "evidence.0.url",
        "evidence.1",
        "evidence.2.finding",
    ]
    assert record["validation_codes"] == ["string_type", "value_error"]
    assert "private-secret" not in json.dumps(record)


def test_unknown_disclosure_remains_valid_and_no_validator_rules_change():
    parsed = QualitativeEvidence.model_validate(
        {
            "source": "unknown",
            "url": None,
            "finding": "未获取该政策原文，当前无法确定具体影响。",
            "retrieved_at": None,
        }
    )
    assert parsed.source == "unknown" and parsed.url is None
