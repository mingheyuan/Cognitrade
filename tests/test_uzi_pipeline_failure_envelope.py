"""Outer pipeline failure must retain execution causes across recovery/API layers."""

import pytest
from pydantic import BaseModel, ValidationError

from domain_packs.finance_ashare.subagents.uzi_392_analysis import _failed_symbol_analysis
from domain_packs.finance_ashare.subagents.uzi_llm_support import UziRequestDeadline
from domain_packs.finance_ashare.subagents.uzi_recovery import native_recovery
from research_system.runtime.providers.errors import LLMProviderError


@pytest.mark.parametrize("error,expected_kind,expected_action", [
    (UziRequestDeadline("llm_queue_timeout"), "deadline_exceeded", "resume_stage"),
    (LLMProviderError("llm_request_failed: Error code: 429 - {'code': 'gateway_concurrency_limit'}"),
     "provider_unavailable", "inspect_provider"),
    (TypeError("unexpected native nullable mapping"), "pipeline_failure", "inspect"),
])
def test_outer_exception_keeps_same_failure_in_execution_and_recovery(error, expected_kind, expected_action):
    result = _failed_symbol_analysis("688041.SH", profile="medium", exc=error, recovery_history=[])
    review = result["self_review"]
    recovery = native_recovery({"results": [result], "report_allowed": False})
    assert recovery["kind"] == expected_kind
    assert recovery["action"] == expected_action
    assert recovery["failure_records"] == review["execution_status"]["failure_records"]
    assert recovery["failure_records"] == review["issues"][0]["failure_records"]
    assert recovery["retryable"] is False
    assert recovery["repairable_stages"] == []
    assert review["native_python_review"] == {"status": "not_run"}
    assert review["issues"][0]["category"] == "agent-os-boundary"
    if expected_kind == "deadline_exceeded":
        assert recovery["failure_records"][0]["kind"] == "llm_queue_timeout"
        assert recovery["exhausted_stages"] == ["native_pipeline"]


def test_root_collection_failure_still_routes_to_explicit_collection():
    result = _failed_symbol_analysis("688041.SH", profile="medium", failure_type="RootCollectionError", recovery_history=[])
    recovery = native_recovery({"results": [result], "report_allowed": False})
    assert recovery["kind"] == "source_gap"
    assert recovery["action"] == "collect_data"
    assert recovery["retryable"] is False
    assert "new_manifest_required" in recovery["reason_codes"]


def test_actual_schema_validation_remains_output_failure_without_raw_values():
    class Output(BaseModel):
        value: int

    try:
        Output.model_validate({"value": "secret-token"})
    except ValidationError as exc:
        result = _failed_symbol_analysis("688041.SH", profile="medium", exc=exc, recovery_history=[])
    recovery = native_recovery({"results": [result], "report_allowed": False})
    assert recovery["kind"] == "output_invalid"
    assert recovery["repairable_stages"] == ["native_pipeline"]
    assert recovery["failure_records"][0]["validation_fields"] == ["value"]
    assert "secret-token" not in str(result)


def test_legacy_execution_layer_pipeline_failure_is_not_lost():
    record = {"stage": "native_pipeline", "kind": "pipeline_failure", "error_type": "TypeError"}
    result = {"self_review": {
        "issues": [{"severity": "critical", "issue": "old pipeline failed"}],
        "execution_status": {"status": "failed", "failure_records": [record]},
    }}
    recovery = native_recovery({"results": [result], "report_allowed": False})
    assert recovery["kind"] == "pipeline_failure"
    assert recovery["action"] == "inspect"
    assert recovery["failure_records"] == [record]
