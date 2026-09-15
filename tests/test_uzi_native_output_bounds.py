"""Pinned SKILL requirements, not arbitrary host text/array quotas."""
import pytest
from pydantic import ValidationError

from domain_packs.finance_ashare.subagents import uzi_392_analysis as host
from domain_packs.finance_ashare.subagents.uzi_agent_roleplay import AgentRoleplayVote
from domain_packs.finance_ashare.subagents.uzi_qualitative_deep_dive import (
    QualitativeEvidence,
)


def test_native_five_character_rationale_and_more_than_three_debate_rounds():
    host._initialize_native_imports()
    zone = host.AgentBuyZone(price=10, rationale="净资产折价", basis_fields=["raw.nav"], calculation="NAV×0.8")
    debate = host.AgentGreatDivide(punchline="折价支持买方，库存风险支持卖方。",
        bull_say_rounds=[host.GroundedRound(text="净资产折价", evidence_paths=["raw.nav"])] * 4,
        bear_say_rounds=[host.GroundedRound(text="库存跌价风险", evidence_paths=["raw.stock"])] * 4)
    native = {"great_divide_override": {
        "punchline": debate.punchline,
        "bull_say_rounds": [r.text for r in debate.bull_say_rounds],
        "bear_say_rounds": [r.text for r in debate.bear_say_rounds]},
        "narrative_override": {"buy_zones": {key: {"price": zone.price, "rationale": zone.rationale}
                                             for key in ("value", "growth", "technical", "youzi")}},
        "agent_reviewed": True}
    assert not host._validate_agent_analysis(native)
    assert host.full_skill_debate_complete(native)


def test_native_short_specific_fact_is_not_rejected_by_host_word_quota():
    vote = AgentRoleplayVote(investor_id="test", signal="bearish", score=30,
        headline="库存跌价", reasoning="库存增长快于收入。", override_rule_engine=True,
        override_reason="库存增长快于收入。")
    assert vote.headline == "库存跌价"
    assert host.GroundedRisk(text="汇率风险", evidence_paths=["raw.fx"]).text == "汇率风险"
    assert host.AgentDataGapAcknowledgement(dimension_id="12_capital_flow", reason="来源未返回").reason == "来源未返回"


@pytest.mark.parametrize("length", [1201, 2200])
def test_native_valid_evidence_is_not_rejected_by_host_maximum_length(length):
    evidence = QualitativeEvidence(source="annual_report", url="https://example.org/annual",
        retrieved_at="2026-09-02", finding="证" * length)
    assert len(evidence.finding) == length


def test_pinned_minimums_and_frozen_price_basis_are_retained():
    with pytest.raises(ValidationError):
        host.AgentBuyZone(price=10, rationale="短理由", basis_fields=["raw.nav"], calculation="NAV")
    with pytest.raises(ValidationError):
        host.AgentBuyZone(price=10, rationale="净资产折价", basis_fields=[], calculation="NAV")
    with pytest.raises(ValidationError):
        host.GroundedText(text="过短", evidence_paths=["raw.nav"])


@pytest.mark.parametrize("metadata", [{}, {"retrieved_at": None}, {"retrieved_at": ""}])
def test_native_citation_does_not_require_example_retrieval_metadata(metadata):
    host._initialize_native_imports()
    citation = {"source": "公开年报", "url": "https://example.org/annual",
                "finding": "公司披露研发投入增加。", **metadata}
    native = {"agent_reviewed": True, "qualitative_deep_dive": {
        "7_industry": {"evidence": [citation, citation], "associations": [],
                       "conclusion": "公司披露研发投入增加。"}}}
    assert not host._validate_agent_analysis(native)
    parsed = QualitativeEvidence.model_validate(citation)
    assert parsed.retrieved_at == metadata.get("retrieved_at")
