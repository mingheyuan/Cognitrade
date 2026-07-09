from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from research_system.agents._subagent_control_escalation import (
    SubagentControlEscalationPolicyService,
    SubagentControlEscalationRule,
)


class SubagentControlEscalationPolicyTests(unittest.TestCase):
    def test_resolve_uses_most_specific_matching_rule(self) -> None:
        service = SubagentControlEscalationPolicyService(
            default_strategy="discovery",
            rules=[
                SubagentControlEscalationRule(
                    strategy="action_replan",
                    rule_id="review_optional_phase",
                    intent="review_thesis",
                    stage="review",
                    notes=("skip_optional_review_phase",),
                ),
                SubagentControlEscalationRule(
                    strategy="discovery",
                    rule_id="generic_review",
                    stage="review",
                ),
            ],
        )

        decision = service.resolve(
            intent="review_thesis",
            stage="review",
            capability="missing_capability",
        )

        self.assertEqual(decision.strategy, "action_replan")
        self.assertEqual(decision.rule_id, "review_optional_phase")
        self.assertIn("subagent_control_escalation_rule=review_optional_phase", decision.notes)
        self.assertIn("skip_optional_review_phase", decision.notes)

    def test_loads_default_strategy_and_rules_from_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "subagent_control_escalation_policy.yml"
            config_path.write_text(
                "\n".join(
                    [
                        "defaults:",
                        "  strategy: discovery",
                        "rules:",
                        "  - rule_id: stock_screen_optional_review",
                        "    intent: stock_screen",
                        "    stage: review",
                        "    strategy: action_replan",
                        "    notes: [skip_optional_screen_review]",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            service = SubagentControlEscalationPolicyService(config_path=config_path)

            default_decision = service.resolve(
                intent="stock_check",
                stage="collect",
                capability="missing_capability",
            )
            matched_decision = service.resolve(
                intent="stock_screen",
                stage="review",
                capability="missing_capability",
            )

            self.assertEqual(default_decision.strategy, "discovery")
            self.assertEqual(default_decision.notes, ("subagent_control_escalation_rule=default",))
            self.assertEqual(matched_decision.strategy, "action_replan")
            self.assertEqual(matched_decision.rule_id, "stock_screen_optional_review")
            self.assertIn("skip_optional_screen_review", matched_decision.notes)


if __name__ == "__main__":
    unittest.main()
