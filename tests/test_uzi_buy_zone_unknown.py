"""Missing research prices stay native warnings, not synthetic prices/errors."""

import importlib
from copy import deepcopy

import pytest
from pydantic import ValidationError

from domain_packs.finance_ashare.subagents import uzi_392_analysis as host
from research_system.strategies.contracts import StrategyDocumentV2
from research_system.strategies.report_conditions import report_condition_calculation
from tests.test_report_condition_backtesting import _node, _report
from tests.test_uzi_synthesis_qualitative_context import (
    CaptureProvider,
    execute,
    research,
)

MISSING = {
    "price": None,
    "rationale": "冻结资料没有龙虎榜集中成交区间，无法据此估算游资买区。",
    "basis_fields": [],
    "calculation": "缺少对应数值依据，未计算价格，也不以现价或零值替代。",
}


class UnknownZoneProvider(CaptureProvider):
    def parse(self, **kwargs):
        result = super().parse(**kwargs)
        if kwargs["schema"] is host.AgentBuyZonePayload:
            return {"zone": deepcopy(MISSING)}
        return result


def test_native_validator_already_allows_disclosed_null_price_as_warning():
    host._initialize_native_imports()
    validator = importlib.import_module("lib.agent_analysis_validator")
    issues = validator.validate({"narrative_override": {"buy_zones": {
        key: {"price": None, "rationale": MISSING["rationale"]}
        for key in ("value", "growth", "technical", "youzi")
    }}})
    price_issues = [issue for issue in issues if issue.field.endswith(".price")]
    assert len(price_issues) == 4
    assert {issue.severity for issue in price_issues} == {"warning"}
    assert not any(issue.severity == "error" for issue in issues)


def test_host_transport_preserves_native_null_price():
    zone = host.AgentBuyZone.model_validate(MISSING)
    assert zone.model_dump() == MISSING


def test_null_price_traverses_real_stage_assembly_without_retry_or_fabrication(tmp_path):
    provider = UnknownZoneProvider(tmp_path)
    result = execute(provider, [research()])
    agent = result["results"][0]["agent_analysis"]
    assert agent is not None, "Host rejected a native warning before synthesis"
    for key in ("value", "growth", "technical", "youzi"):
        assert agent["narrative_override"]["buy_zones"][key] == {
            "price": None, "rationale": MISSING["rationale"],
        }
        assert agent["grounding"]["buy_zones"][key] == {
            "basis_fields": [], "calculation": MISSING["calculation"],
        }
    zones = [row for row in provider.requests if row["schema"] is host.AgentBuyZonePayload]
    assert len(zones) == 4, "Missing price retried an already complete stage"
    for row in zones:
        assert "必须让结构化输出失败" not in row["user_prompt"]
        assert "price=null" in row["user_prompt"]
        assert "price=null" in row["system_prompt"], "Cache identity omitted the changed policy"
    # Actual pinned schema, outside execute()'s unrelated review test seam.
    issues = host._validate_agent_analysis(agent)
    assert len([issue for issue in issues if str(issue.get("field", "")).endswith(".price")]) == 4
    assert not [issue for issue in issues if issue.get("severity") == "error"]
    assert result["results"][0]["synthesis"]["buy_zones"] == agent["narrative_override"]["buy_zones"]


def test_changed_missing_price_policy_invalidates_only_buy_zone_stage_cache(tmp_path, monkeypatch):
    current = host._BUY_ZONE_MISSING_PRICE_INSTRUCTION
    monkeypatch.setattr(host, "_BUY_ZONE_MISSING_PRICE_INSTRUCTION", "legacy policy fixture")
    first = CaptureProvider(tmp_path)
    execute(first, [research()])
    monkeypatch.setattr(host, "_BUY_ZONE_MISSING_PRICE_INSTRUCTION", current)
    revised = UnknownZoneProvider(tmp_path)
    execute(revised, [research()])
    assert len(revised.requests) == 4
    assert all(row["schema"] is host.AgentBuyZonePayload for row in revised.requests)
    replay = UnknownZoneProvider(tmp_path)
    execute(replay, [research()])
    assert replay.requests == []


@pytest.mark.parametrize("price", [0, -1, float("inf"), float("nan")])
def test_invalid_number_cannot_masquerade_as_unavailable_price(price):
    with pytest.raises(ValidationError):
        host.AgentBuyZone.model_validate({**MISSING, "price": price})


def test_numeric_price_still_requires_real_numeric_basis():
    with pytest.raises(ValidationError):
        host.AgentBuyZone.model_validate({**MISSING, "price": 12.0})


def test_report_conditions_never_substitute_research_buy_zones_for_battle_plan():
    node = _node()
    original = report_condition_calculation(node, _report(), StrategyDocumentV2())
    node.source_json["synthesis"]["buy_zones"] = {
        key: {"price": None, "rationale": MISSING["rationale"]}
        for key in ("value", "growth", "technical", "youzi")
    }
    actual = report_condition_calculation(node, _report(), StrategyDocumentV2())
    assert actual == original
    node.source_json["synthesis"]["dashboard"]["battle_plan"]["entry"] = None
    node.source_json["synthesis"]["buy_zones"]["value"]["price"] = 9.20
    invalid = report_condition_calculation(node, _report(), StrategyDocumentV2())
    assert invalid["execution_plan"]["executable"] is False


def test_native_html_renders_unavailable_research_prices_without_zero_fallback():
    from domain_packs.finance_ashare.reporting import render_uzi_native_html
    from tests.test_uzi_native_report import RUN_ID, sample_uzi_artifacts

    artifacts = sample_uzi_artifacts()
    synthesis = artifacts[0]["content"]["results"][0]["synthesis"]
    synthesis["buy_zones"] = {
        key: {"price": None, "rationale": MISSING["rationale"]}
        for key in ("value", "growth", "technical", "youzi")
    }
    original = deepcopy(artifacts)
    html = render_uzi_native_html(artifacts, run_id=RUN_ID).html
    assert artifacts == original
    assert html.count(MISSING["rationale"]) >= 4
    assert html.count('<div class="zone-price">¥—</div>') == 4
    assert "{{ZONE_" not in html
    assert ">None<" not in html
