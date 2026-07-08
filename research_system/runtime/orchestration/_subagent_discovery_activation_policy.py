from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


DEFAULT_ALLOWED_SIDE_EFFECT_LEVELS = ("none", "read_only")


@dataclass(frozen=True)
class SubagentDiscoveryActivationPolicyRule:
    auto_activate: bool = False
    rule_id: str | None = None
    intent: str | None = None
    stage: str | None = None
    capability: str | None = None
    preferred_subagent_type: str | None = None
    allowed_protocols: tuple[str, ...] = ()
    allowed_side_effect_levels: tuple[str, ...] = DEFAULT_ALLOWED_SIDE_EFFECT_LEVELS
    max_candidate_count: int | None = 1
    require_approved_ticket: bool = True
    require_recommended_candidate: bool = True
    require_single_runtime_registration_template: bool = True
    notes: tuple[str, ...] = ()


class SubagentDiscoveryActivationPolicyService:
    def __init__(
        self,
        *,
        settings=None,
        defaults: SubagentDiscoveryActivationPolicyRule | None = None,
        rules: list[SubagentDiscoveryActivationPolicyRule] | None = None,
        config_path: Path | None = None,
    ) -> None:
        if defaults is not None or rules is not None:
            self.defaults = defaults or SubagentDiscoveryActivationPolicyRule()
            self.rules = list(rules or [])
            return
        self.defaults, self.rules = self._load_from_path(
            config_path or getattr(settings, "subagent_discovery_activation_policy_path", None)
        )

    def evaluate(self, *, ticket) -> dict[str, object]:
        candidate_entries = list(ticket.candidate_catalog_entries or [])
        matched_rule = self._matching_rule(ticket=ticket)
        effective_rule = matched_rule or self.defaults
        rule_id = str(effective_rule.rule_id or "default").strip() or "default"

        if not bool(effective_rule.auto_activate):
            return self._pending_decision(
                reason="discovery_auto_activation_disabled",
                rule_id=rule_id,
                notes=effective_rule.notes,
            )
        if effective_rule.require_approved_ticket and str(ticket.status or "").strip().lower() != "approved":
            return self._pending_decision(
                reason=f"discovery_auto_activation_requires_approved_ticket:{ticket.status}",
                rule_id=rule_id,
                notes=effective_rule.notes,
            )
        if ticket.activated_entries:
            return self._pending_decision(
                reason="discovery_auto_activation_already_active",
                rule_id=rule_id,
                notes=effective_rule.notes,
            )
        if not candidate_entries:
            return self._pending_decision(
                reason="discovery_auto_activation_candidate_preview_empty",
                rule_id=rule_id,
                notes=effective_rule.notes,
            )
        if (
            effective_rule.max_candidate_count is not None
            and len(candidate_entries) > int(effective_rule.max_candidate_count)
        ):
            return self._pending_decision(
                reason=f"discovery_auto_activation_candidate_count>{int(effective_rule.max_candidate_count)}",
                rule_id=rule_id,
                notes=effective_rule.notes,
            )
        candidate_entry = self._recommended_entry(ticket=ticket)
        if candidate_entry is None:
            return self._pending_decision(
                reason="discovery_auto_activation_recommended_candidate_missing",
                rule_id=rule_id,
                notes=effective_rule.notes,
            )
        selected_protocol = self._selected_protocol(ticket=ticket, candidate_entry=candidate_entry)
        allowed_protocols = set(effective_rule.allowed_protocols or ())
        if allowed_protocols and selected_protocol not in allowed_protocols:
            return self._pending_decision(
                reason=f"discovery_auto_activation_protocol_not_allowed:{selected_protocol or 'unknown'}",
                rule_id=rule_id,
                notes=effective_rule.notes,
            )
        allowed_side_effect_levels = {
            str(item).strip().lower()
            for item in list(effective_rule.allowed_side_effect_levels or ())
            if str(item).strip()
        }
        candidate_side_effect = str(candidate_entry.side_effect_level or "").strip().lower() or "none"
        if allowed_side_effect_levels and candidate_side_effect not in allowed_side_effect_levels:
            return self._pending_decision(
                reason=f"discovery_auto_activation_side_effect_not_allowed:{candidate_side_effect}",
                rule_id=rule_id,
                notes=effective_rule.notes,
            )
        templates = list(candidate_entry.runtime_registration_templates or [])
        if not templates:
            return self._pending_decision(
                reason="discovery_auto_activation_runtime_template_missing",
                rule_id=rule_id,
                notes=effective_rule.notes,
            )
        if effective_rule.require_single_runtime_registration_template and len(templates) != 1:
            return self._pending_decision(
                reason=f"discovery_auto_activation_runtime_template_count={len(templates)}",
                rule_id=rule_id,
                notes=effective_rule.notes,
            )
        selected_template = templates[0]
        return {
            "activate": True,
            "candidate_subagent_type": str(candidate_entry.subagent_type).strip(),
            "registration_template_id": str(selected_template.template_id).strip(),
            "reason": f"auto_activate_discovery_candidate_preview_rule={rule_id}",
            "notes": [
                f"discovery_activation_rule={rule_id}",
                f"auto_activated_candidate={candidate_entry.subagent_type}",
                f"auto_activated_template={selected_template.template_id}",
                *( [f"auto_activated_protocol={selected_protocol}"] if selected_protocol else [] ),
                *list(effective_rule.notes or ()),
            ],
        }

    def _matching_rule(self, *, ticket) -> SubagentDiscoveryActivationPolicyRule | None:
        matched_rule: SubagentDiscoveryActivationPolicyRule | None = None
        matched_score = -1
        normalized_intent = str(ticket.capability_gap.intent or "").strip().lower()
        normalized_stage = str(ticket.capability_gap.stage or "").strip().lower()
        normalized_capability = str(ticket.capability_gap.capability or "").strip().lower()
        normalized_preferred_subagent_type = str(ticket.capability_gap.preferred_subagent_type or "").strip().lower()
        for rule in self.rules:
            matches, score = self._match_rule(
                rule,
                intent=normalized_intent,
                stage=normalized_stage,
                capability=normalized_capability,
                preferred_subagent_type=normalized_preferred_subagent_type,
            )
            if matches and score > matched_score:
                matched_rule = rule
                matched_score = score
        return matched_rule

    @staticmethod
    def _match_rule(
        rule: SubagentDiscoveryActivationPolicyRule,
        *,
        intent: str,
        stage: str,
        capability: str,
        preferred_subagent_type: str,
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
        return True, score

    @staticmethod
    def _pending_decision(
        *,
        reason: str,
        rule_id: str,
        notes: tuple[str, ...],
    ) -> dict[str, object]:
        return {
            "activate": False,
            "reason": reason,
            "notes": [f"discovery_activation_rule={rule_id}", *list(notes or ())],
        }

    @staticmethod
    def _recommended_entry(*, ticket):
        recommendation = ticket.candidate_recommendation
        requested = str(getattr(recommendation, "candidate_subagent_type", "") or "").strip()
        if requested:
            for entry in list(ticket.candidate_catalog_entries or []):
                if str(entry.subagent_type).strip() == requested:
                    return entry
        entries = list(ticket.candidate_catalog_entries or [])
        if len(entries) == 1:
            return entries[0]
        return None

    @staticmethod
    def _selected_protocol(*, ticket, candidate_entry) -> str | None:
        recommendation = ticket.candidate_recommendation
        protocol = str(getattr(recommendation, "source_protocol", "") or "").strip()
        if protocol:
            return protocol
        tags = [str(tag).strip() for tag in list(candidate_entry.tags or []) if str(tag).strip()]
        for tag in tags:
            if tag.startswith("mcp_source:"):
                return "mcp"
            if tag.startswith("openapi_source:"):
                return "openapi"
            if tag.startswith("skill_registration:"):
                return "skill"
            if tag.startswith("http_json_source:"):
                return "http_json"
        return None

    @classmethod
    def _load_from_path(
        cls,
        config_path: Path | None,
    ) -> tuple[SubagentDiscoveryActivationPolicyRule, list[SubagentDiscoveryActivationPolicyRule]]:
        if config_path is None or not Path(config_path).exists():
            return SubagentDiscoveryActivationPolicyRule(), []
        raw_config = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
        defaults_row = dict(raw_config.get("defaults") or {}) if isinstance(raw_config, dict) else {}
        defaults = cls._rule_from_row(defaults_row, fallback_rule_id="default")
        rules: list[SubagentDiscoveryActivationPolicyRule] = []
        for index, row in enumerate(list((raw_config.get("rules") if isinstance(raw_config, dict) else []) or [])):
            if not isinstance(row, dict):
                continue
            rules.append(cls._rule_from_row(row, fallback_rule_id=f"rule_{index + 1}"))
        return defaults, rules

    @classmethod
    def _rule_from_row(
        cls,
        row: dict,
        *,
        fallback_rule_id: str,
    ) -> SubagentDiscoveryActivationPolicyRule:
        return SubagentDiscoveryActivationPolicyRule(
            auto_activate=bool(row.get("auto_activate", False)),
            rule_id=str(row.get("rule_id") or "").strip() or fallback_rule_id,
            intent=str(row.get("intent") or "").strip() or None,
            stage=str(row.get("stage") or "").strip() or None,
            capability=str(row.get("capability") or "").strip() or None,
            preferred_subagent_type=str(row.get("preferred_subagent_type") or "").strip() or None,
            allowed_protocols=cls._normalize_strings(row.get("allowed_protocols") or ()),
            allowed_side_effect_levels=cls._normalize_strings(
                row.get("allowed_side_effect_levels") or DEFAULT_ALLOWED_SIDE_EFFECT_LEVELS
            ),
            max_candidate_count=(
                int(row.get("max_candidate_count"))
                if row.get("max_candidate_count") is not None
                else None
            ),
            require_approved_ticket=bool(row.get("require_approved_ticket", True)),
            require_recommended_candidate=bool(row.get("require_recommended_candidate", True)),
            require_single_runtime_registration_template=bool(
                row.get("require_single_runtime_registration_template", True)
            ),
            notes=cls._normalize_strings(row.get("notes") or ()),
        )

    @staticmethod
    def _normalize_strings(raw_values) -> tuple[str, ...]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in list(raw_values or []):
            value = str(item or "").strip()
            if not value or value in seen:
                continue
            normalized.append(value)
            seen.add(value)
        return tuple(normalized)


__all__ = [
    "DEFAULT_ALLOWED_SIDE_EFFECT_LEVELS",
    "SubagentDiscoveryActivationPolicyRule",
    "SubagentDiscoveryActivationPolicyService",
]
