"""Offline post-parse diagnostics remain bounded and do not weaken evidence checks."""

import json
import logging
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator, ValidationError as SchemaValidationError

from domain_packs.finance_ashare.subagents import uzi_llm_support as support
from domain_packs.finance_ashare.subagents import uzi_qualitative_deep_dive as qualitative
from research_system.runtime.providers.errors import StructuredOutputError


SECRET = "private-answer-Settings-api-key-request-context"
GROUPS = dict(qualitative._GROUPS)
STAGE = "qualitative_C · Cost-Transmission"
CODES = (
    "qualitative_dimension_membership_mismatch",
    "qualitative_repeated_finding",
    "qualitative_ungrounded_url",
)


def group_payload(label="C · Cost-Transmission"):
    return {
        "agent_reviewed": True,
        "group_label": label,
        "dimensions": [
            {
                "dimension_id": dimension,
                "evidence": [
                    {
                        "source": "Frozen archive",
                        "url": f"https://archive.example/{dimension}",
                        "finding": f"Frozen distinct finding number {index} for {dimension}.",
                        "retrieved_at": "2026-09-10",
                    }
                    for index in (1, 2)
                ],
                "associations": [{
                    "link_to": "cost",
                    "chain_id": "frozen-cost-chain",
                    "causal_chain": "Archived cost changes can affect the downstream product margin.",
                    "estimated_impact": "Needs subsequent filing verification",
                }],
                "conclusion": "The conclusion is limited to the facts preserved in the frozen archive.",
            }
            for dimension in GROUPS[label]
        ],
    }


def evidence_bundle():
    return {
        dimension: {"allowed_urls": [f"https://archive.example/{dimension}"]}
        for dimensions in GROUPS.values()
        for dimension in dimensions
    }


def rejected_group(case, *, dimension_index=0):
    payload = group_payload()
    dimension = payload["dimensions"][dimension_index]
    if case == "membership":
        dimension["dimension_id"] = "3_macro"
    elif case == "duplicate_dimension":
        dimension["dimension_id"] = payload["dimensions"][1 - dimension_index]["dimension_id"]
    elif case == "repeated":
        dimension["evidence"][0]["finding"] = f"Repeated finding {SECRET}"
        dimension["evidence"][1]["finding"] = f"  REPEATED\n FINDING   {SECRET.upper()} "
    elif case == "ungrounded":
        dimension["evidence"][1]["url"] = f"https://untrusted.example/{SECRET}"
    else:
        raise AssertionError(case)
    return qualitative.QualitativeGroupResult.model_validate(payload)


def group_failure(case, *, dimension_index=0):
    with pytest.raises(ValueError) as caught:
        qualitative._validate_group(
            rejected_group(case, dimension_index=dimension_index),
            GROUPS["C · Cost-Transmission"],
            evidence_bundle(),
        )
    return caught.value


@pytest.fixture(scope="module")
def failure_record_validator():
    root = Path(__file__).resolve().parents[1]
    schema = json.loads((root / "domain_packs/finance_ashare/artifact_schemas/uzi_native_analysis.json").read_text())
    return Draft202012Validator(schema["properties"]["recovery"]["properties"]["failure_records"]["items"])


@pytest.mark.parametrize("dimension_index", [0, 1])
@pytest.mark.parametrize(("case", "code"), [
    ("membership", CODES[0]),
    ("duplicate_dimension", CODES[0]),
    ("repeated", CODES[1]),
    ("ungrounded", CODES[2]),
])
def test_actual_group_rejections_have_typed_safe_diagnostics(case, code, dimension_index, failure_record_validator):
    error = group_failure(case, dimension_index=dimension_index)
    record = support.model_failure_record(error, stage=STAGE)
    field = "dimensions" if code == CODES[0] else f"dimensions.{dimension_index}.evidence"
    assert record == {
        "stage": STAGE,
        "kind": "agent_content_invalid",
        "error_type": "UziQualitativeValidationError",
        "validation_fields": [field],
        "validation_codes": ["value_error"],
        "validation_reasons": [{"field": field, "code": code}],
    }
    assert isinstance(error, ValueError)
    assert str(error) == code
    assert SECRET not in json.dumps(record)
    assert "untrusted.example" not in json.dumps(record)
    failure_record_validator.validate(record)


@pytest.mark.parametrize("message", [
    "dimension membership mismatch: expected=('8_materials', '9_futures'), actual=['3_macro', '9_futures']",
    "8_materials repeated finding cannot count as distinct evidence",
    f"8_materials cited ungrounded URLs: ['https://example.test/{SECRET}']",
    *CODES,
])
def test_legacy_plain_value_error_never_acquires_a_retrospectively_inferred_reason(message):
    error = ValueError(message)
    error.validation_reasons = [{"field": SECRET, "code": CODES[2]}]
    record = support.model_failure_record(error, stage=STAGE)
    assert record == {"stage": STAGE, "kind": "agent_content_invalid", "error_type": "ValueError"}
    assert SECRET not in json.dumps(record)


@pytest.mark.parametrize("wrapper_type", [StructuredOutputError, RuntimeError])
def test_chained_typed_error_preserves_safe_cause_without_wrapper_or_request_context(wrapper_type, failure_record_validator):
    cause = group_failure("ungrounded")
    wrapper = wrapper_type(f"raw response {SECRET} bad gateway timeout")
    wrapper.__cause__ = cause
    wrapper.body = {"error": {"code": SECRET}}
    wrapper.settings = SimpleNamespace(api_key=SECRET)
    record = support.model_failure_record(wrapper, stage=STAGE)
    assert record["error_type"] == wrapper_type.__name__
    assert record["kind"] == "agent_content_invalid"
    assert record["validation_reasons"] == [{"field": "dimensions.0.evidence", "code": CODES[2]}]
    assert SECRET not in json.dumps(record)
    failure_record_validator.validate(record)


@pytest.mark.parametrize(("field", "code"), [
    (SECRET, CODES[2]),
    ("dimensions.0.evidence", SECRET),
    ("dimensions.0.evidence", CODES[0]),
    ("dimensions.200000.evidence", CODES[2]),
])
def test_typed_diagnostic_attributes_are_rechecked_before_persistence(field, code):
    error = group_failure("ungrounded")
    error.field = field
    error.code = code
    record = support.model_failure_record(error, stage=STAGE)
    assert "validation_reasons" not in record
    assert SECRET not in json.dumps(record)


def test_typed_diagnostics_do_not_call_exception_string_conversion():
    class UnrenderableValidationError(support.UziQualitativeValidationError):
        def __str__(self):
            raise AssertionError("must not inspect raw exception text")

    error = UnrenderableValidationError(field="dimensions.0.evidence", code=CODES[2])
    error.args = (SECRET,)
    record = support.model_failure_record(error, stage=STAGE)
    assert record["validation_reasons"] == [{"field": "dimensions.0.evidence", "code": CODES[2]}]
    assert SECRET not in json.dumps(record)


@pytest.mark.parametrize("code", CODES)
def test_schema_accepts_only_fixed_reason_shape(code, failure_record_validator):
    record = {
        "stage": STAGE, "kind": "agent_content_invalid", "error_type": "UziQualitativeValidationError",
        "validation_reasons": [{"field": "dimensions.0.evidence", "code": code}],
    }
    failure_record_validator.validate(record)
    record["validation_reasons"][0]["message"] = SECRET
    with pytest.raises(SchemaValidationError):
        failure_record_validator.validate(record)


@pytest.mark.parametrize("variant", ["same_url", "reverse_membership", "unknown_disclosure", "unknown_cited"])
def test_group_acceptance_policy_is_unchanged(variant):
    payload = group_payload()
    if variant == "reverse_membership":
        payload["dimensions"].reverse()
    elif variant == "unknown_disclosure":
        payload["dimensions"][0]["evidence"].append({
            "source": "unknown", "url": None, "retrieved_at": None,
            "finding": "Not disclosed in the frozen archive; unknown exposure remains.",
        })
    elif variant == "unknown_cited":
        payload["dimensions"][0]["evidence"][0].update({
            "source": "unknown", "finding": "The original source is unknown but this fact is in the archive.",
        })
    group = qualitative.QualitativeGroupResult.model_validate(payload)
    before = group.model_dump()
    assert qualitative._validate_group(group, GROUPS["C · Cost-Transmission"], evidence_bundle()) is None
    assert group.model_dump() == before


@pytest.mark.parametrize("case", ["membership", "repeated", "ungrounded", "plain_error"])
def test_full_offline_rejection_reports_only_safe_diagnostics_and_keeps_other_groups(case, caplog):
    class Provider:
        available = True

        def __init__(self):
            self.calls = []

        def parse(self, *, user_prompt, **_kwargs):
            label = user_prompt.splitlines()[0].split("：", 1)[1]
            self.calls.append(label)
            if label.startswith("C"):
                if case == "plain_error":
                    raise ValueError(f"raw model answer Settings(api_key={SECRET})")
                return rejected_group(case)
            return qualitative.QualitativeGroupResult.model_validate(group_payload(label))

    rows = [
        {"snapshot_type": kind, "facts": {"sources": [{
            "url": f"https://archive.example/{dimension}", "finding": "A frozen archived concrete fact.",
        }]}}
        for dimension, kinds in qualitative._DIMENSION_TYPES.items()
        for kind in kinds
    ]
    before = deepcopy(rows)
    provider = Provider()
    progress = []
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        result = qualitative.apply_qualitative_deep_dive(
            {"results": [], "risk_flags": []}, snapshot_rows=rows,
            llm_provider=provider, progress_callback=progress.append,
        )
    execution = result["qualitative_agent_execution"]
    assert execution["status"] == "incomplete"
    assert execution["completed_group_count"] == 2
    assert set(result["qualitative_deep_dive"]) == {"3_macro", "13_policy", "7_industry", "15_events"}
    assert sorted(provider.calls) == sorted(GROUPS)
    assert rows == before
    assert len(execution["failure_records"]) == 1
    assert execution["failure_records"][0]["kind"] == "agent_content_invalid"
    if case == "plain_error":
        assert "validation_reasons" not in execution["failure_records"][0]
    else:
        assert execution["failure_records"][0]["validation_reasons"]
    for value in (json.dumps(result), json.dumps(progress), caplog.text):
        assert SECRET not in value
        assert SECRET.upper() not in value
        assert "untrusted.example" not in value


def test_cache_rejection_logging_does_not_render_untrusted_exception(caplog):
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        qualitative._discard_qualitative_stage_cache(None, ValueError(SECRET))
    assert SECRET not in caplog.text
