"""Safe optional diagnostics survive the strict report-artifact boundary."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "domain_packs/finance_ashare/artifact_schemas/uzi_native_analysis.json").read_text())
VALIDATOR = Draft202012Validator(
    SCHEMA["properties"]["recovery"]["properties"]["failure_records"]["items"]
)
LEGACY_RECORD = {
    "stage": "research_enrichment_A",
    "kind": "agent_content_invalid",
    "error_type": "StructuredOutputError",
    "validation_fields": ["research.dimensions.1.evidence.0"],
    "validation_codes": ["value_error"],
}


def test_legacy_failure_record_does_not_require_new_diagnostics():
    before = deepcopy(LEGACY_RECORD)
    VALIDATOR.validate(LEGACY_RECORD)
    assert LEGACY_RECORD == before


@pytest.mark.parametrize("code", [
    "qualitative_evidence_url_invalid",
    "qualitative_unknown_gap_not_disclosed",
    "qualitative_factual_evidence_incomplete",
])
def test_known_safe_reason_codes_are_allowed(code):
    VALIDATOR.validate({
        **LEGACY_RECORD,
        "validation_reasons": [{"field": "research.dimensions.1.evidence.0", "code": code}],
    })


@pytest.mark.parametrize("reason", [
    {"field": "research.dimensions.1.evidence.0", "code": "arbitrary_raw_error"},
    {"field": "research.dimensions.1.evidence.0", "code": "qualitative_factual_evidence_incomplete", "message": "raw model answer"},
    {"field": "research.dimensions.1.evidence.0"},
])
def test_arbitrary_messages_and_incomplete_diagnostics_are_rejected(reason):
    with pytest.raises(ValidationError):
        VALIDATOR.validate({**LEGACY_RECORD, "validation_reasons": [reason]})
