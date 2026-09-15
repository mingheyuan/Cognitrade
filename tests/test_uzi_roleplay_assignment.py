"""The executed host sub-agent must honor the actual native seat assignment."""

import hashlib
import json
import socket
from copy import deepcopy
from types import SimpleNamespace

import pytest

from domain_packs.finance_ashare.subagents import uzi_agent_roleplay as roleplay
from tests.test_uzi_deep_roleplay import _panel, _RoleplayProvider


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Roleplay assignment regression is offline")
    monkeypatch.setattr(socket.socket, "connect", denied)


def assignment(prompt):
    lines = [line for line in prompt.splitlines() if line.startswith("ROLEPLAY_ASSIGNMENT=")]
    assert len(lines) == 1, "Executed host prompt must contain one authoritative assignment"
    return json.loads(lines[0].split("=", 1)[1])


def test_every_actual_group_has_an_explicit_current_seat_assignment():
    counts = {"A+B": 15, "C": 7, "D+E": 11, "F": 24, "G+H+I": 9}
    for label, votes in roleplay._nonempty_batches(_panel("deep")["votes"]):
        prompt = roleplay._batch_user_prompt(label, votes)
        code = label.split(" · ")[0]
        assert assignment(prompt) == {
            "group": code, "required_investor_ids": [vote["investor_id"] for vote in votes],
            "required_investor_count": counts[code],
        }
        assert "旧人数仅为背景" in prompt
        assert "合法 skip" in prompt


class AssignedProvider(_RoleplayProvider):
    def __init__(self, workspace):
        super().__init__()
        self.settings = SimpleNamespace(workspace_root=workspace, uzi_llm_max_concurrency=2)
        self.assignments = []

    def parse(self, **kwargs):
        current = assignment(kwargs["user_prompt"])
        result = super().parse(system_prompt=kwargs["system_prompt"], user_prompt=kwargs["user_prompt"],
            schema=kwargs["schema"], temperature=kwargs.get("temperature", 0.1))
        assert {v.investor_id for v in result.votes} == set(current["required_investor_ids"])
        self.assignments.append(current)
        return result


def seed_legacy_cache(provider, *, incomplete_ab=False):
    # The exact old body is reconstructed by removing only the new assignment.
    # This tests a known compatible cache, not generic old-prompt reuse.
    features = {"price": 10.0}
    prompt = roleplay._system_prompt(json.dumps(roleplay._prompt_features(features),
        ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    paths = []
    for label, votes in roleplay._nonempty_batches(_panel("deep")["votes"]):
        legacy = roleplay._batch_user_prompt(label, votes, include_assignment=False)
        payload = _RoleplayProvider().parse(system_prompt=prompt, user_prompt=legacy,
            schema=roleplay.AgentRoleplayBatch)
        if incomplete_ab and label.startswith("A+B"):
            missing = {"andreessen", "chamath", "gerstner", "gurley", "naval"}
            payload.votes = [vote for vote in payload.votes if vote.investor_id not in missing]
        path = roleplay._roleplay_stage_cache_path(provider, profile="deep", group_label=label,
            system_prompt=prompt, user_prompt=legacy)
        roleplay._write_roleplay_stage_cache(path, payload)
        paths.append(path)
    return paths


def hashes(paths):
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def test_only_actual_missing_group_calls_model_when_legacy_groups_are_complete(tmp_path):
    provider = AssignedProvider(tmp_path)
    paths = seed_legacy_cache(provider, incomplete_ab=True)
    before = hashes(paths)
    panel = _panel("deep")
    original = deepcopy(panel)
    progress = []
    result = roleplay.apply_deep_agent_roleplay(panel, features={"price":10.0}, required=True,
        llm_provider=provider, progress_callback=progress.append)
    assert result["roleplay"]["status"] == "completed", result["roleplay"]
    assert len(provider.assignments) == 1
    assert provider.assignments[0]["group"] == "A+B"
    assert provider.assignments[0]["required_investor_count"] == 15
    assert len(result["per_investor_override"]) == 66
    assert sum(row.get("cache_reason") == "legacy_assignment_membership_validated" for row in progress) == 4
    assert hashes(paths) == before, "Legacy cache files are evidence, not rewritten as new model responses"
    assert panel == original


def test_new_raw_input_cannot_reuse_legacy_assignment_cache(tmp_path):
    provider = AssignedProvider(tmp_path)
    paths = seed_legacy_cache(provider)
    before = hashes(paths)
    result = roleplay.apply_deep_agent_roleplay(_panel("deep"), features={"price":11.0}, required=True,
        llm_provider=provider)
    assert result["roleplay"]["status"] == "completed", result["roleplay"]
    assert len(provider.assignments) == 5
    assert hashes(paths) == before
