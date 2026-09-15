"""The native mechanical result must not absorb host workflow checks."""

import importlib
from copy import deepcopy
from unittest.mock import patch

from domain_packs.finance_ashare.subagents.uzi_392_analysis import (
    _initialize_native_imports,
    run_native_self_review,
)


def _review(**updates):
    kwargs = {
        "symbol": "688041.SH", "profile": "medium", "school": None,
        "raw": {"ticker": "688041.SH", "market": "A", "dimensions": {}},
        "dimensions": {}, "panel": {}, "synthesis": {},
        "agent_analysis": {"agent_reviewed": True}, "agent_validation_issues": [],
        "roleplay": {"required": False}, "qualitative_execution": {"required": False},
        "computation_error": None, "full_agent_workflow": True,
    }
    kwargs.update(updates)
    return run_native_self_review(**kwargs)


def test_native_failure_and_skill_failure_have_separate_counts():
    _initialize_native_imports()
    native = importlib.import_module("lib.self_review")

    def check_data(ctx):
        return [native.Issue(severity="critical", category="data", dim="19_contests",
                             issue="native missing data", evidence="data={}")]

    with patch.object(native, "CHECKS", [check_data]):
        result = _review(agent_analysis={"agent_reviewed": True, "great_divide_override": {
            "punchline": "Offline complete debate fixture; not a real investment conclusion",
            "bull_say_rounds": ["Frozen fixture fact 12"] * 3,
            "bear_say_rounds": ["Frozen fixture risk 12"] * 3,
        }}, qualitative_execution={
            "required": True, "status": "failed", "agent_reviewed": False,
            "failure_records": [{"stage": "qualitative_A", "kind": "source_gap",
                                 "error_type": "FrozenEvidenceUnavailable"}],
        })
    assert result["critical_count"] == 2  # Backwards-compatible delivery gate.
    assert result["native_python_review"]["critical_count"] == 1
    assert result["native_python_review"]["issues"] == [check_data({})[0].to_dict()]
    assert result["native_skill_completion"]["critical_count"] == 1
    assert result["execution_status"]["status"] == "incomplete"
    assert result["frozen_input_safety"]["status"] == "not_evaluated_here"
    assert result["report_allowed"] is False


def test_native_checks_see_unmodified_nullable_and_valuation_fields():
    _initialize_native_imports()
    native = importlib.import_module("lib.self_review")
    raw = {"ticker": "688041.SH", "market": "A", "dimensions": {
        "0_basic": {"data": {"industry": None}},
        "20_valuation_models": {"data": {"dcf": {"intrinsic_per_share": 12.3}}},
    }}
    original = deepcopy(raw)
    seen = []

    def check_original(ctx):
        seen.append(deepcopy(ctx["raw"]))
        return []

    def check_broken(ctx):
        raise TypeError("native nullable input")

    with patch.object(native, "CHECKS", [check_original, check_broken]):
        result = _review(raw=raw)
    assert seen == [original]
    assert raw == original
    assert result["native_python_review"]["issues"] == [native.Issue(
        severity="warning", category="self-check", dim="review-engine",
        issue="check check_broken 自己炸了: TypeError: native nullable input",
    ).to_dict()]


def test_model_failure_is_not_reported_as_native_check_failure():
    _initialize_native_imports()
    native = importlib.import_module("lib.self_review")
    with patch.object(native, "CHECKS", []):
        result = _review(agent_validation_issues=[{
            "severity": "error", "field": "agent_analysis", "message": "queue expired",
            "failure_records": [{"stage": "dimensions_1_5", "kind": "llm_queue_timeout",
                                 "error_type": "UziRequestDeadline"}],
        }])
    assert result["native_python_review"]["passed"] is True
    assert result["execution_status"]["status"] == "failed"
    assert result["execution_status"]["failure_records"][0]["kind"] == "llm_queue_timeout"
    assert result["report_allowed"] is False


def test_delivery_keeps_native_success_separate_from_host_failure():
    from types import SimpleNamespace

    from domain_packs.finance_ashare.subagents.runtime import (
        compose_uzi_native_delivery,
    )

    review = {
        "critical_count": 1, "warning_count": 0,
        "native_python_review": {"passed": True, "critical_count": 0, "issues": []},
        "native_skill_completion": {"status": "incomplete", "critical_count": 1},
        "execution_status": {"status": "failed", "failure_records": [{"kind": "llm_queue_timeout"}]},
        "frozen_input_safety": {"status": "not_evaluated_here"},
        "issues": [{"severity": "critical", "issue": "model queue expired"}],
    }
    original = deepcopy(review)
    analysis = SimpleNamespace(
        artifact_id="native", source_refs=[], confidence=0, risk_flags=[],
        content={"report_allowed": False, "critical_count": 1,
                 "results": [{"symbol": "688041.SH", "self_review": review}]},
    )
    context = SimpleNamespace(require_artifact=lambda _kind: analysis)
    report = compose_uzi_native_delivery(context).content
    assert report["critical_count"] == 1
    assert report["assessment"]["review_scope"] == "combined_delivery_gate"
    layers = report["assessment"]["factors"][0]["review_layers"]
    assert layers["native_python_review"]["critical_count"] == 0
    assert layers["execution_status"]["status"] == "failed"
    assert "UZI critical" not in report["conclusion"]
    assert "执行" in report["conclusion"]
    assert review == original


def test_legacy_review_is_not_invented_as_native_pass():
    from domain_packs.finance_ashare.subagents.runtime import _uzi_review_layers

    layers = _uzi_review_layers({"self_review": {"critical_count": 0, "passed": True}})
    assert layers["native_python_review"] == {"status": "not_recorded"}
    assert layers["execution_status"] == {"status": "not_recorded"}


def test_pipeline_failure_does_not_claim_native_review_ran():
    from domain_packs.finance_ashare.subagents.uzi_392_analysis import (
        _failed_symbol_analysis,
    )

    result = _failed_symbol_analysis("688041.SH", profile="medium",
                                     exc=TimeoutError("queue expired"), recovery_history=[])
    review = result["self_review"]
    assert review["critical_count"] == 1
    assert review["native_python_review"]["status"] == "not_run"
    assert review["execution_status"]["status"] == "failed"
    assert "重新采集" not in review["issues"][0]["suggested_fix"]


def test_recovery_schema_retains_only_bounded_provider_diagnostics():
    import json
    from pathlib import Path

    import jsonschema
    import pytest

    path = Path(__file__).resolve().parents[1] / "domain_packs/finance_ashare/artifact_schemas/uzi_native_analysis.json"
    schema = json.loads(path.read_text())["properties"]["recovery"]["properties"]["failure_records"]["items"]
    row = {
        "stage": "qualitative_C", "kind": "llm_provider_unavailable", "error_type": "RateLimitError",
        "provider_status_code": 429, "provider_error_code": "gateway_concurrency_limit",
        "provider_error_type": "rate_limit_error", "provider_request_id": "request-example",
        "retry_after_seconds": 2.0, "provider_retryable": True,
    }
    jsonschema.validate(row, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**row, "headers": {"Authorization": "not permitted"}}, schema)
