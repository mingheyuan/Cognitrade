from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

from research_system.schemas.planning import ActionPlanPatch
from research_system.schemas.subagents import SubagentCapabilityGap


DEFAULT_SUBAGENT_CONTROL_ESCALATION_STRATEGY = "discovery"


@dataclass(frozen=True)
class SubagentControlEscalationRule:
    strategy: Literal["discovery", "action_replan"]
    rule_id: str | None = None
    intent: str | None = None
    stage: str | None = None
    capability: str | None = None
    preferred_subagent_type: str | None = None
    requires_llm: bool | None = None
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class SubagentControlEscalationDecision:
    strategy: Literal["discovery", "action_replan"]
    policy_source: Literal["rule"] = "rule"
    rule_id: str | None = None
    reason: str = DEFAULT_SUBAGENT_CONTROL_ESCALATION_STRATEGY
    notes: tuple[str, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)


class SubagentControlEscalationError(RuntimeError):
    def __init__(
        self,
        *,
        gap: SubagentCapabilityGap,
        escalation_decision: SubagentControlEscalationDecision,
        plan_patch: ActionPlanPatch,
    ) -> None:
        self.gap = gap
        self.escalation_decision = escalation_decision
        self.plan_patch = plan_patch
        super().__init__(gap.reason)


class SubagentControlEscalationPolicyService:
    def __init__(
        self,
        *,
        settings=None,
        default_strategy: Literal["discovery", "action_replan"] | None = None,
        rules: list[SubagentControlEscalationRule] | None = None,
        config_path: Path | None = None,
    ) -> None:
        if rules is not None or default_strategy is not None:
            self.default_strategy = str(default_strategy or DEFAULT_SUBAGENT_CONTROL_ESCALATION_STRATEGY).strip().lower()
            self.rules = list(rules or [])
            return
        loaded_default, loaded_rules = self._load_from_path(
            config_path or getattr(settings, "subagent_control_escalation_policy_path", None)
        )
        self.default_strategy = loaded_default
        self.rules = loaded_rules

    def resolve(
        self,
        *,
        intent: str | None,
        stage: str | None,
        capability: str,
        preferred_subagent_type: str | None = None,
        requires_llm: bool | None = None,
    ) -> SubagentControlEscalationDecision:
        matched_rule: SubagentControlEscalationRule | None = None
        matched_score = -1
        normalized_intent = str(intent or "").strip().lower()
        normalized_stage = str(stage or "").strip().lower()
        normalized_capability = str(capability or "").strip().lower()
        normalized_preferred_subagent_type = str(preferred_subagent_type or "").strip().lower()
        for rule in self.rules:
            matches, score = self._match_rule(
                rule,
                intent=normalized_intent,
                stage=normalized_stage,
                capability=normalized_capability,
                preferred_subagent_type=normalized_preferred_subagent_type,
                requires_llm=requires_llm,
            )
            if matches and score > matched_score:
                matched_rule = rule
                matched_score = score
        if matched_rule is None:
            strategy = self._normalize_strategy(self.default_strategy)
            return SubagentControlEscalationDecision(
                strategy=strategy,
                reason=f"subagent_control_escalation={strategy}",
                notes=("subagent_control_escalation_rule=default",),
                metadata={},
            )
        strategy = self._normalize_strategy(matched_rule.strategy)
        rule_id = matched_rule.rule_id or "anonymous"
        return SubagentControlEscalationDecision(
            strategy=strategy,
            rule_id=rule_id,
            reason=f"subagent_control_escalation={strategy}",
            notes=(
                f"subagent_control_escalation_rule={rule_id}",
                *tuple(matched_rule.notes or ()),
            ),
            metadata={},
        )

    @staticmethod
    def _match_rule(
        rule: SubagentControlEscalationRule,
        *,
        intent: str,
        stage: str,
        capability: str,
        preferred_subagent_type: str,
        requires_llm: bool | None,
    ) -> tuple[bool, int]:
        score = 0
        if rule.intent is not None:
            if str(rule.intent).strip().lower() != intent:
                return False, 0
            score += 2
        if rule.stage is not None:
            if str(rule.stage).strip().lower() != stage:
                return False, 0
            score += 1
        if rule.capability is not None:
            if str(rule.capability).strip().lower() != capability:
                return False, 0
            score += 4
        if rule.preferred_subagent_type is not None:
            if str(rule.preferred_subagent_type).strip().lower() != preferred_subagent_type:
                return False, 0
            score += 1
        if rule.requires_llm is not None:
            if bool(rule.requires_llm) != bool(requires_llm):
                return False, 0
            score += 1
        return True, score

    @classmethod
    def _load_from_path(
        cls,
        config_path: Path | None,
    ) -> tuple[str, list[SubagentControlEscalationRule]]:
        if config_path is None or not Path(config_path).exists():
            return DEFAULT_SUBAGENT_CONTROL_ESCALATION_STRATEGY, []
        raw_config = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
        defaults_row = dict(raw_config.get("defaults") or {}) if isinstance(raw_config, dict) else {}
        default_strategy = cls._normalize_strategy(
            defaults_row.get("strategy") or DEFAULT_SUBAGENT_CONTROL_ESCALATION_STRATEGY
        )
        rules: list[SubagentControlEscalationRule] = []
        for index, row in enumerate(list((raw_config.get("rules") if isinstance(raw_config, dict) else []) or [])):
            if not isinstance(row, dict):
                continue
            strategy = cls._normalize_strategy(row.get("strategy") or "")
            if not strategy:
                continue
            rules.append(
                SubagentControlEscalationRule(
                    strategy=strategy,
                    rule_id=str(row.get("rule_id") or "").strip() or f"rule_{index + 1}",
                    intent=str(row.get("intent") or "").strip() or None,
                    stage=str(row.get("stage") or "").strip() or None,
                    capability=str(row.get("capability") or "").strip() or None,
                    preferred_subagent_type=str(row.get("preferred_subagent_type") or "").strip() or None,
                    requires_llm=(bool(row.get("requires_llm")) if row.get("requires_llm") is not None else None),
                    notes=tuple(
                        str(item).strip()
                        for item in list(row.get("notes") or [])
                        if str(item).strip()
                    ),
                )
            )
        return default_strategy, rules

    @staticmethod
    def _normalize_strategy(raw_strategy: str | None) -> Literal["discovery", "action_replan"]:
        normalized = str(raw_strategy or "").strip().lower()
        if normalized == "action_replan":
            return "action_replan"
        return "discovery"


__all__ = [
    "DEFAULT_SUBAGENT_CONTROL_ESCALATION_STRATEGY",
    "SubagentControlEscalationDecision",
    "SubagentControlEscalationError",
    "SubagentControlEscalationPolicyService",
    "SubagentControlEscalationRule",
]
