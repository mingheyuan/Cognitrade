from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from pydantic import ValidationError

from domain_packs.finance_ashare.subagents.uzi_agent_roleplay import (
    AgentRoleplayBatch,
    AgentRoleplayVote,
    PERSONAS_DIR,
    _validate_batch_members,
    apply_deep_agent_roleplay,
    load_persona,
)
from domain_packs.finance_ashare.subagents.uzi_methodology import build_features
from domain_packs.finance_ashare.subagents.uzi_panel_registry import panel_for_profile


def _snapshots() -> list[dict]:
    fixture = Path(__file__).resolve().parent / "fixtures" / "uzi_finance_snapshot_replay.json"
    return list(json.loads(fixture.read_text(encoding="utf-8"))["snapshots"])


def _panel(profile: str) -> dict:
    votes = [
        {
            "investor_id": str(row["id"]),
            "school": str(row["group"]),
            "group": str(row["group"]),
            "signal": "neutral",
            "score": 50.0,
            "confidence": 0.5,
            "reasoning": "冻结证据有限，规则引擎保持中性。",
        }
        for row in panel_for_profile(profile)
    ]
    return {
        "profile": profile,
        "votes": votes,
        "distribution": {"neutral": len(votes)},
        "average_score": 50.0,
        "evaluated_rule_count": len(votes),
        "quality_status": "available",
        "data_gap": [],
        "risk_flags": [],
    }


class _RoleplayProvider:
    available = True

    def __init__(self, *, incomplete: bool = False) -> None:
        self.incomplete = incomplete
        self.system_prompts: list[str] = []
        self._lock = Lock()

    def parse(self, *, system_prompt, user_prompt, schema, temperature=0.2):
        self.assert_schema(schema)
        group_label = user_prompt.splitlines()[0].split("：", 1)[1]
        reference = json.loads(user_prompt.split("RULE_ENGINE_REFERENCE:\n", 1)[1])
        if self.incomplete and group_label.startswith("F"):
            reference = reference[:-1]
        votes = [
            AgentRoleplayVote(
                investor_id=str(row["investor_id"]),
                signal="neutral",
                score=55,
                headline=f"规则分 {row['rule_score']}，冻结证据仍不足以形成方向结论",
                reasoning="只依据冻结快照与该评委方法论；可用规则证据有限，缺失字段不做补值，因此保留中性判断。",
                comment=None,
                verdict="中性",
                override_rule_engine=float(row["rule_score"] or 0) != 55,
                override_reason=("Agent 结合 persona 与缺失证据重估" if float(row["rule_score"] or 0) != 55 else None),
            )
            for row in reference
        ]
        with self._lock:
            self.system_prompts.append(system_prompt)
        return AgentRoleplayBatch(agent_reviewed=True, group_label=group_label, votes=votes)

    @staticmethod
    def assert_schema(schema) -> None:
        if schema is not AgentRoleplayBatch:
            raise AssertionError(f"unexpected schema: {schema}")


class _FlakyRoleplayProvider(_RoleplayProvider):
    def __init__(self, workspace_root: Path) -> None:
        super().__init__()
        self.settings = SimpleNamespace(
            workspace_root=workspace_root,
            provider_name="test",
            default_model="test-model",
            uzi_llm_max_concurrency=2,
        )
        self.calls: dict[str, int] = {}

    def parse(self, *, system_prompt, user_prompt, schema, temperature=0.2):
        group_label = user_prompt.splitlines()[0].split("：", 1)[1]
        with self._lock:
            self.calls[group_label] = self.calls.get(group_label, 0) + 1
            attempt = self.calls[group_label]
        if group_label.startswith("F") and attempt == 1:
            raise RuntimeError("temporary roleplay group failure")
        return super().parse(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=schema,
            temperature=temperature,
        )


class UziDeepRoleplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rows = _snapshots()
        cls.symbols = ["600519"]
        cls.features = build_features(cls.rows, cls.symbols[0])

    def test_all_uzi_yaml_personas_are_vendored_and_flagship_has_priority(self) -> None:
        self.assertEqual(len(list(PERSONAS_DIR.glob("*.yaml"))), 51)
        persona = load_persona({"investor_id": "buffett", "school": "A"})
        self.assertTrue(persona.is_flagship)
        self.assertTrue(persona.key_metrics)
        self.assertIn("Mr. Market", persona.voice)
        stub = load_persona({"investor_id": "templeton", "school": "A"})
        self.assertFalse(stub.is_flagship)

    def test_explicit_chinese_evidence_shortfall_is_a_concrete_headline(self) -> None:
        vote = AgentRoleplayVote(
            investor_id="zhang_mz",
            signal="insufficient_evidence",
            score=0,
            headline="资金接力强度证据不足",
            reasoning="冻结快照没有换手率、封板强度和席位联动数据，因此不能形成可审计的短线判断。",
            override_rule_engine=True,
            override_reason="规则骨架缺少 A 股资金证据，Agent 明确保留缺口。",
        )

        self.assertEqual(vote.signal, "insufficient_evidence")

    def test_roleplay_allows_summary_headline_when_reasoning_is_grounded(self) -> None:
        vote = AgentRoleplayVote(
            investor_id="zhang_mz",
            signal="bearish",
            score=20,
            headline="当前阶段暂不参与交易",
            reasoning="冻结快照显示当日跌幅 3.5%，同时缺少换手率和封板强度，因此不满足资金接力条件。",
            override_rule_engine=True,
            override_reason="根据冻结行情修正规则骨架。",
        )

        self.assertEqual(vote.headline, "当前阶段暂不参与交易")

    def test_explicit_skip_can_ground_abstention_in_verdict(self) -> None:
        vote = AgentRoleplayVote(
            investor_id="zhang_mz",
            signal="skip",
            score=0,
            headline="当前条件不进入交易评估",
            reasoning="依照该评委的交易纪律，本轮保持规则骨架给出的结论并停止方向判断。",
            comment="规则结果保持原样。",
            verdict="不追",
            override_rule_engine=False,
            override_reason=None,
        )

        self.assertEqual(vote.signal, "skip")

    def test_non_abstaining_vote_still_requires_grounded_evidence(self) -> None:
        with self.assertRaises(ValidationError):
            AgentRoleplayVote(
                investor_id="zhang_mz",
                signal="bullish",
                score=80,
                headline="当前阶段可以积极参与",
                reasoning="依照该评委的方法论，本轮形成明确的方向性判断并建议继续观察。",
                comment="保持原有观点。",
                verdict="看多",
                override_rule_engine=False,
                override_reason=None,
            )

    def test_group_code_alias_keeps_strict_membership_gate(self) -> None:
        batch = AgentRoleplayBatch(
            agent_reviewed=True,
            group_label="G+H+I",
            votes=[
                AgentRoleplayVote(
                    investor_id="shaw",
                    signal="skip",
                    score=0,
                    headline="当前条件不进入交易评估",
                    reasoning="依照该评委的交易纪律，本轮保持规则骨架给出的结论并停止方向判断。",
                    verdict="skip",
                    override_rule_engine=False,
                    override_reason=None,
                )
            ],
        )

        _validate_batch_members(
            batch,
            ["shaw"],
            expected_group_label="G+H+I · 量化、科技领袖与AI瓶颈",
        )
        with self.assertRaises(ValueError):
            _validate_batch_members(
                batch,
                ["simons"],
                expected_group_label="G+H+I · 量化、科技领袖与AI瓶颈",
            )

    def test_medium_never_invokes_agent_roleplay(self) -> None:
        provider = _RoleplayProvider()
        panel = _panel("medium")
        result = apply_deep_agent_roleplay(panel, features=self.features, llm_provider=provider)
        self.assertEqual(result["roleplay"]["status"], "not_requested")
        self.assertFalse(result["roleplay"]["required"])
        self.assertEqual(provider.system_prompts, [])
        self.assertEqual(len(result["votes"]), 66)

    def test_deep_requires_agent_os_provider_instead_of_silent_rule_fallback(self) -> None:
        panel = _panel("deep")
        result = apply_deep_agent_roleplay(panel, features=self.features, llm_provider=None)
        self.assertEqual(len(result["votes"]), 66)
        self.assertEqual(result["quality_status"], "data_gap")
        self.assertEqual(result["roleplay"]["status"], "required_but_unavailable")
        self.assertFalse(result["roleplay"]["agent_reviewed"])
        self.assertIn("agent_roleplay_required", result["data_gap"])
        self.assertEqual(result["per_investor_override"], {})

    def test_deep_roleplays_all_66_seats_in_five_prefix_stable_groups(self) -> None:
        provider = _RoleplayProvider()
        panel = _panel("deep")
        result = apply_deep_agent_roleplay(panel, features=self.features, llm_provider=provider)

        self.assertEqual(result["roleplay"]["status"], "completed")
        self.assertTrue(result["roleplay"]["agent_reviewed"])
        self.assertEqual(result["roleplay"]["group_count"], 5)
        self.assertEqual(len(provider.system_prompts), 5)
        self.assertEqual(len(set(provider.system_prompts)), 1)
        self.assertEqual(len(result["votes"]), 66)
        self.assertEqual(len(result["per_investor_override"]), 66)
        self.assertTrue(all(vote["agent_roleplay_applied"] for vote in result["votes"]))
        self.assertTrue(all("original_rule_score" in vote for vote in result["votes"]))

    def test_full_agent_workflow_roleplays_medium_panel_too(self) -> None:
        provider = _RoleplayProvider()
        result = apply_deep_agent_roleplay(
            _panel("medium"),
            features=self.features,
            llm_provider=provider,
            required=True,
        )

        self.assertEqual(result["roleplay"]["status"], "completed")
        self.assertEqual(len(result["per_investor_override"]), 66)
        self.assertEqual(len(provider.system_prompts), 5)

    def test_valid_roleplay_groups_resume_from_stage_cache(self) -> None:
        with TemporaryDirectory() as temporary:
            first_provider = _RoleplayProvider()
            first_provider.settings = SimpleNamespace(
                workspace_root=Path(temporary),
                provider_name="test",
                default_model="test-model",
                uzi_llm_max_concurrency=2,
            )
            first = apply_deep_agent_roleplay(
                _panel("deep"),
                features=self.features,
                llm_provider=first_provider,
            )
            second_provider = _RoleplayProvider()
            second_provider.settings = first_provider.settings
            second = apply_deep_agent_roleplay(
                _panel("deep"),
                features=self.features,
                llm_provider=second_provider,
            )

        self.assertEqual(first["roleplay"]["status"], "completed")
        self.assertEqual(second["roleplay"]["status"], "completed")
        self.assertEqual(len(first_provider.system_prompts), 5)
        self.assertEqual(second_provider.system_prompts, [])

    def test_only_failed_roleplay_group_is_retried(self) -> None:
        with TemporaryDirectory() as temporary:
            provider = _FlakyRoleplayProvider(Path(temporary))
            first = apply_deep_agent_roleplay(
                _panel("deep"),
                features=self.features,
                llm_provider=provider,
            )
            second = apply_deep_agent_roleplay(
                _panel("deep"),
                features=self.features,
                llm_provider=provider,
            )

        self.assertEqual(first["roleplay"]["status"], "incomplete")
        self.assertEqual(second["roleplay"]["status"], "completed")
        self.assertEqual(sum(provider.calls.values()), 6)
        self.assertEqual(
            next(count for label, count in provider.calls.items() if label.startswith("F")),
            2,
        )

    def test_incomplete_group_does_not_partially_claim_deep_completion(self) -> None:
        provider = _RoleplayProvider(incomplete=True)
        panel = _panel("deep")
        result = apply_deep_agent_roleplay(panel, features=self.features, llm_provider=provider)
        self.assertEqual(result["quality_status"], "data_gap")
        self.assertEqual(result["roleplay"]["status"], "incomplete")
        self.assertFalse(result["roleplay"]["agent_reviewed"])
        self.assertEqual(result["per_investor_override"], {})
        self.assertTrue(all("agent_roleplay_applied" not in vote for vote in result["votes"]))


if __name__ == "__main__":
    unittest.main()
