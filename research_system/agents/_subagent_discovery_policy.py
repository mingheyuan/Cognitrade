from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


DEFAULT_DISCOVERY_PROTOCOLS = ("mcp", "openapi", "skill", "http_json")


@dataclass(frozen=True)
class SubagentDiscoveryPolicyRule:
    preferred_protocols: tuple[str, ...]
    rule_id: str | None = None
    intent: str | None = None
    stage: str | None = None
    capability: str | None = None
    preferred_subagent_type: str | None = None
    requires_llm: bool | None = None
    notes: tuple[str, ...] = ()


class SubagentDiscoveryPolicyService:
    def __init__(
        self,
        *,
        settings=None,
        default_preferred_protocols: tuple[str, ...] | list[str] | None = None,
        rules: list[SubagentDiscoveryPolicyRule] | None = None,
        config_path: Path | None = None,
    ) -> None:
        if rules is not None or default_preferred_protocols is not None:
            self.default_preferred_protocols = self._normalize_protocols(
                default_preferred_protocols or DEFAULT_DISCOVERY_PROTOCOLS
            )
            self.rules = list(rules or [])
            return
        loaded_defaults, loaded_rules = self._load_from_path(
            config_path or getattr(settings, "subagent_discovery_policy_path", None)
        )
        self.default_preferred_protocols = loaded_defaults
        self.rules = loaded_rules

    def resolve(
        self,
        *,
        intent: str | None,
        stage: str | None,
        capability: str,
        preferred_subagent_type: str | None = None,
        requires_llm: bool | None = None,
    ) -> tuple[list[str], list[str]]:
        matched_rule: SubagentDiscoveryPolicyRule | None = None
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
            return list(self.default_preferred_protocols), ["discovery_policy_rule=default"]
        notes = [
            f"discovery_policy_rule={matched_rule.rule_id or 'anonymous'}",
            *list(matched_rule.notes or ()),
        ]
        return list(matched_rule.preferred_protocols or self.default_preferred_protocols), notes

    @staticmethod
    def _match_rule(
        rule: SubagentDiscoveryPolicyRule,
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
    ) -> tuple[tuple[str, ...], list[SubagentDiscoveryPolicyRule]]:
        if config_path is None or not Path(config_path).exists():
            return DEFAULT_DISCOVERY_PROTOCOLS, []
        raw_config = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
        defaults_row = dict(raw_config.get("defaults") or {}) if isinstance(raw_config, dict) else {}
        default_preferred_protocols = cls._normalize_protocols(
            defaults_row.get("preferred_protocols") or DEFAULT_DISCOVERY_PROTOCOLS
        )
        rules: list[SubagentDiscoveryPolicyRule] = []
        for index, row in enumerate(list((raw_config.get("rules") if isinstance(raw_config, dict) else []) or [])):
            if not isinstance(row, dict):
                continue
            preferred_protocols = cls._normalize_protocols(row.get("preferred_protocols") or ())
            if not preferred_protocols:
                continue
            rules.append(
                SubagentDiscoveryPolicyRule(
                    preferred_protocols=preferred_protocols,
                    rule_id=str(row.get("rule_id") or "").strip() or f"rule_{index + 1}",
                    intent=str(row.get("intent") or "").strip() or None,
                    stage=str(row.get("stage") or "").strip() or None,
                    capability=str(row.get("capability") or "").strip() or None,
                    preferred_subagent_type=str(row.get("preferred_subagent_type") or "").strip() or None,
                    requires_llm=(
                        bool(row.get("requires_llm"))
                        if row.get("requires_llm") is not None
                        else None
                    ),
                    notes=tuple(
                        str(item).strip()
                        for item in list(row.get("notes") or [])
                        if str(item).strip()
                    ),
                )
            )
        return default_preferred_protocols, rules

    @staticmethod
    def _normalize_protocols(raw_protocols) -> tuple[str, ...]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in list(raw_protocols or []):
            protocol = str(item or "").strip()
            if not protocol or protocol in seen:
                continue
            normalized.append(protocol)
            seen.add(protocol)
        return tuple(normalized)


__all__ = [
    "DEFAULT_DISCOVERY_PROTOCOLS",
    "SubagentDiscoveryPolicyRule",
    "SubagentDiscoveryPolicyService",
]
