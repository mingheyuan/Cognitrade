from __future__ import annotations

import json
from pathlib import Path
import re
from threading import Lock
import unittest

import jsonschema

from domain_packs.finance_ashare.subagents.uzi_agent_roleplay import (
    AgentRoleplayBatch,
    AgentRoleplayVote,
    PERSONAS_DIR,
    apply_deep_agent_roleplay,
    load_persona,
)
from domain_packs.finance_ashare.subagents.uzi_methodology import (
    build_features,
    build_panel_review,
    select_profile,
)


def _snapshots() -> list[dict]:
    fixture = Path(__file__).resolve().parent / "fixtures" / "uzi_finance_snapshot_replay.json"
    return list(json.loads(fixture.read_text(encoding="utf-8"))["snapshots"])


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

    def test_medium_never_invokes_agent_roleplay(self) -> None:
        provider = _RoleplayProvider()
        panel = build_panel_review(self.rows, self.symbols, select_profile({"research_profile": "medium"}))
        result = apply_deep_agent_roleplay(panel, features=self.features, llm_provider=provider)
        self.assertEqual(result["roleplay"]["status"], "not_requested")
        self.assertFalse(result["roleplay"]["required"])
        self.assertEqual(provider.system_prompts, [])
        self.assertEqual(len(result["votes"]), 51)

    def test_deep_requires_agent_os_provider_instead_of_silent_rule_fallback(self) -> None:
        panel = build_panel_review(self.rows, self.symbols, select_profile({"research_profile": "deep"}))
        result = apply_deep_agent_roleplay(panel, features=self.features, llm_provider=None)
        self.assertEqual(len(result["votes"]), 66)
        self.assertEqual(result["quality_status"], "data_gap")
        self.assertEqual(result["roleplay"]["status"], "required_but_unavailable")
        self.assertFalse(result["roleplay"]["agent_reviewed"])
        self.assertIn("deep_agent_roleplay_required", result["data_gap"])
        self.assertEqual(result["per_investor_override"], {})

    def test_deep_roleplays_all_66_seats_in_five_prefix_stable_groups(self) -> None:
        provider = _RoleplayProvider()
        panel = build_panel_review(self.rows, self.symbols, select_profile({"research_profile": "deep"}))
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

        schema = json.loads(
            (Path(__file__).resolve().parents[1] / "domain_packs/finance_ashare/artifact_schemas/investor_panel_review.json")
            .read_text(encoding="utf-8")
        )
        jsonschema.validate(result, schema)

    def test_incomplete_group_does_not_partially_claim_deep_completion(self) -> None:
        provider = _RoleplayProvider(incomplete=True)
        panel = build_panel_review(self.rows, self.symbols, select_profile({"research_profile": "deep"}))
        result = apply_deep_agent_roleplay(panel, features=self.features, llm_provider=provider)
        self.assertEqual(result["quality_status"], "data_gap")
        self.assertEqual(result["roleplay"]["status"], "incomplete")
        self.assertFalse(result["roleplay"]["agent_reviewed"])
        self.assertEqual(result["per_investor_override"], {})
        self.assertTrue(all("agent_roleplay_applied" not in vote for vote in result["votes"]))


if __name__ == "__main__":
    unittest.main()
