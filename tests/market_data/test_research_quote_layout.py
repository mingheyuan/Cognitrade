"""Resolve PDF Chinese line wraps, never fuzzy facts or incorrect source hints."""

from copy import deepcopy
from hashlib import sha256

import pytest

from research_system.market_data import research_suggestions as suggestions
from tests.market_data import test_company_profile_revision as fixture
from tests.market_data.test_pit_core import session as session  # noqa: PLC0414
from tests.market_data.test_research_suggestions import profile, profile_scope


def scoped(text):
    value = profile_scope()
    value["documents"][0].update(inline_content=text, text_content_hash=sha256(text.encode()).hexdigest())
    return value


@pytest.mark.parametrize("linebreak", ["\n", "\r\n", " \n  ", "\t\n\t"])
def test_chinese_pdf_wraps_rebind_to_the_exact_original_span(linebreak):
    text = f"前言\n公司属于集成电{linebreak}路设计行业，具备技术密集型的特{linebreak}征。\n末尾"
    scope = scoped(text)
    before = deepcopy(scope)
    quote = "公司属于集成电路设计行业，具备技术密集型的特征。 "
    proposed = profile(citations=[{"evidence_id": "proof", "quote": quote, "source_snapshot_id": "source"}])
    result = suggestions.resolve_profile_suggestions([proposed], scope=scope)
    assert result["rejected"] == []
    citation = result["proposals"][0]["citations"][0]
    assert citation["quote"] == text[citation["start"]:citation["end"]]
    assert linebreak in citation["quote"]
    assert scope == before


def test_unique_evidence_binds_source_without_model_copying_redundant_long_id():
    scope = profile_scope()
    proposed = profile()
    del proposed["citations"][0]["source_snapshot_id"]
    result = suggestions.resolve_profile_suggestions([proposed], scope=scope)
    assert result["rejected"] == []
    assert result["proposals"][0]["citations"][0]["source_snapshot_id"] == "source"
    assert result["proposals"][0]["citations"][0]["source_snapshot_hash"] == "d" * 64


@pytest.mark.parametrize("text,quote", [
    ("原文金额1\n0亿元。", "原文金额10亿元。"),
    ("原文公司AB\nCD。", "原文公司ABCD。"),
    ("公司属于集成电路设计行业。", "公司属于软件行业。"),
    ("公司属于集成电路设计行业。", "公司属于集成电路设计行业！"),
    ("原文金额10亿元。", "原文金额100亿元。"),
    ("公司属于集成电\n路设计行业。", "公司属于集成电路设计行业，盈利很好。"),
])
def test_layout_matching_never_changes_digits_latin_punctuation_or_content(text, quote):
    result = suggestions.resolve_profile_suggestions([profile(citations=[{
        "source_snapshot_id": "source", "evidence_id": "proof", "quote": quote,
    }])], scope=scoped(text))
    assert result["proposals"] == []
    assert result["rejected"][0]["reason"] == "quote_not_found"


def test_layout_equivalent_repeated_spans_are_ambiguous():
    text = "公司属于集成电\n路设计行业。\n公司属于集成\n电路设计行业。"
    result = suggestions.resolve_profile_suggestions([profile(citations=[{
        "evidence_id": "proof", "quote": "公司属于集成电路设计行业。", "source_snapshot_id": "source",
    }])], scope=scoped(text))
    assert result["rejected"][0]["reason"] == "quote_ambiguous"


def test_wrong_explicit_source_is_not_repaired_and_unhinted_ambiguous_evidence_refuses():
    scope = profile_scope()
    bad = profile()
    bad["citations"][0]["source_snapshot_id"] = "model-corrupted-id"
    assert suggestions.resolve_profile_suggestions([bad], scope=scope)["rejected"][0]["reason"] == "evidence_not_bound"
    another = deepcopy(scope["documents"][0])
    another["source_snapshot_id"] = "another-source"
    scope["documents"].append(another)
    unhinted = profile()
    del unhinted["citations"][0]["source_snapshot_id"]
    assert suggestions.resolve_profile_suggestions([unhinted], scope=scope)["rejected"][0]["reason"] == "evidence_ambiguous"
    assert suggestions.resolve_profile_suggestions([profile()], scope=scope)["rejected"] == []


def test_layout_quote_and_value_pass_real_append_only_persistence(session, tmp_path, monkeypatch):
    original_text = fixture.TEXT
    monkeypatch.setattr(fixture, "TEXT", original_text.replace("电路", "电\n路"))
    state = fixture.seed(session, tmp_path, full=True)
    scope = fixture.scope_for(state)
    old_payload, old_hash = deepcopy(state.basic.payload_json), state.basic.content_hash
    suggestion = profile(new_value="集成电路设计", citations=[{
        "evidence_id": state.proof.id, "quote": original_text,
    }])
    compiled = suggestions.resolve_profile_suggestions([suggestion], scope=scope)
    assert compiled["rejected"] == []
    result = fixture.persist(state, scope, compiled["proposals"])
    assert result["rejected"] == [] and len(result["revisions"]) == 1
    assert result["rebuild_required"] and result["ready_to_freeze"] is False
    assert state.basic.content_hash == old_hash and state.basic.payload_json == old_payload
    new = state.repo.require_snapshot(result["revisions"][0]["snapshot_id"])
    assert new.payload_json["raw"]["industry"] == "集成电路设计"
    assert new.payload_json["_uzi_raw_result"] == old_payload["_uzi_raw_result"]
