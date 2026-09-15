"""Roleplay repair must name the failed obligation, not only votes.N."""

import json

import pytest
from pydantic import BaseModel, ValidationError, field_validator

from domain_packs.finance_ashare.subagents import uzi_agent_roleplay as roleplay
from domain_packs.finance_ashare.subagents.uzi_llm_support import model_failure_record
from research_system.runtime.providers.errors import StructuredOutputError
from tests.test_uzi_deep_roleplay import _panel, _RoleplayProvider
from tests.test_uzi_roleplay_assignment import AssignedProvider
from tests.test_uzi_validation_diagnostic_contract import VALIDATOR


def invalid_batch(kind):
    label, votes = roleplay._nonempty_batches(_panel("deep")["votes"])[0]
    valid = _RoleplayProvider().parse(
        system_prompt="", user_prompt=roleplay._batch_user_prompt(label, votes),
        schema=roleplay.AgentRoleplayBatch,
    ).model_dump(mode="json")
    vote = valid["votes"][2]
    assert kind == "roleplay_override_reason_missing"
    vote.update(override_rule_engine=True, override_reason=" \t ")
    with pytest.raises(ValidationError) as caught:
        roleplay.AgentRoleplayBatch.model_validate(valid)
    return caught.value


@pytest.mark.parametrize("kind", [
    "roleplay_override_reason_missing",
])
def test_actual_vote_validator_has_safe_reason_through_provider_wrapper(kind):
    error = StructuredOutputError("private model response must not escape")
    error.__cause__ = invalid_batch(kind)
    record = model_failure_record(error, stage="roleplay_A+B · 经典价值与成长")
    assert record["validation_reasons"] == [{"field": "votes.2", "code": kind}]
    assert record["validation_fields"] == ["votes.2"]
    assert record["kind"] == "agent_content_invalid"
    assert "private model" not in json.dumps(record)
    VALIDATOR.validate(record)


@pytest.mark.parametrize("kind,hint", [
    ("roleplay_override_reason_missing", "override_reason"),
])
def test_only_failed_group_receives_specific_repair_without_changing_votes(tmp_path, kind, hint):
    class Provider(AssignedProvider):
        fail = True

        def parse(self, **kwargs):
            prompt = kwargs["user_prompt"]
            if prompt.startswith("负责分组：A+B"):
                if self.fail:
                    error = StructuredOutputError("private model response")
                    error.__cause__ = invalid_batch(kind)
                    raise error
                repair = prompt.split("\nOUTPUT_REPAIR=", 1)[1]
                record = json.loads(repair.splitlines()[0])
                assert record["validation_reasons"] == [{"field": "votes.2", "code": kind}]
                assert hint in repair and "不改变评分或评级" in repair
                kwargs["user_prompt"] = prompt.split("\nOUTPUT_REPAIR=", 1)[0]
            return super().parse(**kwargs)

    provider = Provider(tmp_path)
    panel = _panel("deep")
    kwargs = {"features": {"price": 10.0}, "required": True, "llm_provider": provider}
    failed = roleplay.apply_deep_agent_roleplay(panel, **kwargs)
    assert failed["roleplay"]["status"] == "incomplete"
    assert failed["roleplay"]["completed_group_count"] == 4
    assert failed["per_investor_override"] == {}
    failures = failed["roleplay"]["failure_records"]
    assert failures[0]["validation_reasons"] == [{"field": "votes.2", "code": kind}]
    provider.fail = False
    provider.assignments.clear()
    fixed = roleplay.apply_deep_agent_roleplay(panel, previous_failures=failures, **kwargs)
    assert fixed["roleplay"]["status"] == "completed"
    assert len(fixed["per_investor_override"]) == 66
    assert [a["group"] for a in provider.assignments] == ["A+B"]


@pytest.mark.parametrize("message", [
    "override_reason is required when overriding the rule engine: private-secret",
    ("vote rationale must cite a concrete number, observed fact, explicit data gap, "
     "or an explicit abstention for skip/insufficient_evidence private-secret"),
])
def test_unrecognized_messages_are_not_guessed_or_exposed(message):
    class Response(BaseModel):
        value: str

        @field_validator("value")
        @classmethod
        def reject(cls, value):
            raise ValueError(message)

    with pytest.raises(ValidationError) as caught:
        Response(value="private-secret")
    record = model_failure_record(caught.value, stage="roleplay_A+B")
    assert "validation_reasons" not in record
    assert "private-secret" not in json.dumps(record)


def test_concrete_fact_does_not_need_host_digits_or_keywords():
    value = dict(investor_id="buffett", signal="neutral", score=48,
        headline="公司收入依赖晶圆制造客户资本开支",
        reasoning="公司主要产品为刻蚀设备，客户扩产节奏决定订单兑现；长期竞争力取决于工艺验证与售后服务。",
        override_rule_engine=False)
    parsed = roleplay.AgentRoleplayVote.model_validate(value)
    assert parsed.model_dump(include=set(value)) == value
    # Concrete-fact semantics remain in Task3; schema acceptance is not a
    # substitute for its review, and adding a digit is not proof of grounding.
    protocol = (roleplay.PERSONAS_DIR.parent / "references/task3-agent-evaluation.md").read_text()
    assert "每个 headline 必须引用具体数字或事实" in protocol
