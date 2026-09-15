"""Proposal permission is distinct from immutable input; diagnostics adopt nothing."""

import json
from copy import deepcopy
from hashlib import sha256

import pytest

from research_system.market_data import research_enrichment as enrichment
from tests.market_data.test_company_profile_workflow import (
    Provider,
    run,
)
from tests.market_data.test_company_profile_workflow import (
    company as company,  # noqa: PLC0414
)
from tests.market_data.test_company_profile_workflow import (
    offline as offline,  # noqa: PLC0414
)
from tests.market_data.test_research_suggestion_pipeline import (
    response_for,
)
from tests.market_data.test_research_suggestion_pipeline import (
    run as run_fact,
)
from tests.market_data.test_research_suggestion_pipeline import (
    source as source,  # noqa: PLC0414
)


@pytest.mark.parametrize("dimensions", [("0_basic",), ("0_basic", "7_industry")])
def test_model_receives_explicit_profile_proposal_authority_not_read_only_ban(company, dimensions):
    before = deepcopy(company.scope)
    provider = Provider(company)
    result = run(company, provider, dimensions)
    prompt = provider.calls[0]
    view = json.loads(prompt["user_prompt"].split("\nCOMPANY_PROFILE_SCOPE=", 1)[1])
    assert "read_only" not in view
    assert view["input_immutable"] is True
    assert view["proposal_permission"] == {
        "version": "uzi-company-profile-proposal-view-v1",
        "allowed": True,
        "target_dimension": "0_basic",
        "target_snapshot_id": company.basic_id,
        "fields": ["name", "industry", "main_business"],
        "output_field": "company_profile_proposals",
        "persistence": "validated_new_snapshot_only",
    }
    assert "revision_allowed=false不限制独立的company_profile_proposals" in prompt["system_prompt"]
    assert "company_profile_proposals每个new_value必须是单条citation.quote中的连续原文" in prompt["system_prompt"]
    assert company.scope == before and company.scope["read_only"] is True
    assert len(result["revisions"]) == 1
    assert result["company_profile_rebuilds"][0]["status"] == "missing_dependency"


def test_industry_only_task_still_has_no_company_identity_proposal_permission(company):
    provider = Provider(company, propose=False)
    result = run(company, provider, ("7_industry",))
    assert "COMPANY_PROFILE_SCOPE=" not in provider.calls[0]["user_prompt"]
    assert result["revisions"] == []


@pytest.mark.parametrize("period,literal", [
    ("2026年1-6月", "2026年1-6月"),
    ("2026年下半年度", "2026年下半年度"),
    ("2026-02-30", "2026-02-30"),
    ("2026年1月1日至2026年6月30日", "2026年1月1日至2026年6月30日"),
    ("MODEL_PRIVATE_PERIOD_PAYLOAD", None),
    ("2026年\nSECRET_TOKEN", None),
    ("1" * 200, None),
])
def test_rejected_period_has_safe_diagnostic_without_changing_validation(source, period, literal):
    response = response_for(source)
    response["proposals"][0]["period_text"] = period
    result, native = run_fact(source[0], response)
    assert result["status"] == "partial"
    assert result["revisions"] == [] and native == []
    group = result["groups"][0]
    assert group["proposal_rejections"] == [{
        "proposal_kind": "fact", "suggestion_index": 0,
        "reason": "period_not_supported_or_mismatch",
    }]
    diagnostic = group["proposal_diagnostics"][0]
    assert diagnostic == {
        "version": "uzi-fact-period-diagnostics-v1",
        "suggestion_index": 0,
        "stage": "period_resolution",
        "period_text_sha256": sha256(period.encode()).hexdigest(),
        "period_text_length": len(period),
        "period_literal": literal,
    }
    assert "MODEL_PRIVATE" not in str(result)
    assert "SECRET_TOKEN" not in str(result)


def test_profile_view_does_not_expand_allowed_fields(company):
    scope = deepcopy(company.scope)
    scope["allowed_fields"] = ["name"]
    before = deepcopy(scope)
    view = enrichment._company_profile_prompt_scope(scope, {})
    assert view["proposal_permission"]["fields"] == ["name"]
    assert set(view["target"]["raw"]) <= {"name"}
    assert scope == before
