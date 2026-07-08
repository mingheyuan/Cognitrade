from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


DEFAULT_ALLOWED_SIDE_EFFECT_LEVELS = ("none", "read_only")


@dataclass(frozen=True)
class SubagentDiscoveryApprovalPolicyRule:
    auto_approve: bool = False
    rule_id: str | None = None
    intent: str | None = None
    stage: str | None = None
    capability: str | None = None
    preferred_subagent_type: str | None = None
    allowed_protocols: tuple[str, ...] = ()
    allowed_side_effect_levels: tuple[str, ...] = DEFAULT_ALLOWED_SIDE_EFFECT_LEVELS
    max_candidate_count: int | None = 1
    allow_recommended_candidate_override: bool = False
    require_recommended_candidate: bool = True
    require_runtime_registration_template: bool = True
    notes: tuple[str, ...] = ()


class SubagentDiscoveryApprovalPolicyService:
    def __init__(
        self,
        *,
        settings=None,
        defaults: SubagentDiscoveryApprovalPolicyRule | None = None,
        rules: list[SubagentDiscoveryApprovalPolicyRule] | None = None,
        config_path: Path | None = None,
    ) -> None:
        if defaults is not None or rules is not None:
            self.defaults = defaults or SubagentDiscoveryApprovalPolicyRule()
            self.rules = list(rules or [])
            return
        self.defaults, self.rules = self._load_from_path(
            config_path or getattr(settings, "subagent_discovery_approval_policy_path", None)
        )

    def evaluate(self, *, ticket) -> dict[str, object]:
        candidate_entries = list(ticket.candidate_catalog_entries or [])
        recommendation = ticket.candidate_recommendation
        matched_rule = self._matching_rule(ticket=ticket)
        effective_rule = matched_rule or self.defaults
        rule_id = str(effective_rule.rule_id or "default").strip() or "default"

        if not bool(effective_rule.auto_approve):
            return self._pending_decision(
                reason="pending_manual_review_discovery_auto_approve_disabled",
                rule_id=rule_id,
                notes=effective_rule.notes,
            )
        if not candidate_entries:
            return self._pending_decision(
                reason="pending_manual_review_discovery_candidate_preview_empty",
                rule_id=rule_id,
                notes=effective_rule.notes,
            )
        if (
            effective_rule.max_candidate_count is not None
            and len(candidate_entries) > int(effective_rule.max_candidate_count)
        ):
            if not bool(effective_rule.allow_recommended_candidate_override):
                return self._pending_decision(
                    reason=f"pending_manual_review_discovery_candidate_count>{int(effective_rule.max_candidate_count)}",
                    rule_id=rule_id,
                    notes=effective_rule.notes,
                )
        candidate_entry = self._recommended_entry(ticket=ticket)
        if candidate_entry is None:
            return self._pending_decision(
                reason="pending_manual_review_discovery_recommended_candidate_missing",
                rule_id=rule_id,
                notes=effective_rule.notes,
            )
        if effective_rule.require_recommended_candidate and recommendation is None:
            return self._pending_decision(
                reason="pending_manual_review_discovery_missing_recommendation",
                rule_id=rule_id,
                notes=effective_rule.notes,
            )
        selected_protocol = self._selected_protocol(ticket=ticket, candidate_entry=candidate_entry)
        allowed_protocols = set(effective_rule.allowed_protocols or ())
        if allowed_protocols and selected_protocol not in allowed_protocols:
            return self._pending_decision(
                reason=f"pending_manual_review_discovery_protocol_not_auto_approved:{selected_protocol or 'unknown'}",
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
                reason=f"pending_manual_review_discovery_side_effect_not_auto_approved:{candidate_side_effect}",
                rule_id=rule_id,
                notes=effective_rule.notes,
            )
        if (
            effective_rule.require_runtime_registration_template
            and not list(candidate_entry.runtime_registration_templates or [])
        ):
            return self._pending_decision(
                reason="pending_manual_review_discovery_runtime_template_missing",
                rule_id=rule_id,
                notes=effective_rule.notes,
            )
        return {
            "approved": True,
            "decision": "approved",
            "reviewer": "system_auto_approve",
            "source": "auto_discovery_policy",
            "reason": f"auto_approve_discovery_candidate_preview_rule={rule_id}",
            "notes": [
                f"discovery_approval_rule={rule_id}",
                f"approved_candidate={candidate_entry.subagent_type}",
                *(
                    [f"recommended_candidate_override_count={len(candidate_entries)}"]
                    if (
                        effective_rule.allow_recommended_candidate_override
                        and effective_rule.max_candidate_count is not None
                        and len(candidate_entries) > int(effective_rule.max_candidate_count)
                    )
                    else []
                ),
                *( [f"approved_protocol={selected_protocol}"] if selected_protocol else [] ),
                *list(effective_rule.notes or ()),
            ],
        }

    def _matching_rule(self, *, ticket) -> SubagentDiscoveryApprovalPolicyRule | None:
        matched_rule: SubagentDiscoveryApprovalPolicyRule | None = None
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
        rule: SubagentDiscoveryApprovalPolicyRule,
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
            "approved": False,
            "decision": "pending",
            "reviewer": "system_auto_gate",
            "source": "auto_discovery_policy",
            "reason": reason,
            "notes": [f"discovery_approval_rule={rule_id}", *list(notes or ())],
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
    ) -> tuple[SubagentDiscoveryApprovalPolicyRule, list[SubagentDiscoveryApprovalPolicyRule]]:
        if config_path is None or not Path(config_path).exists():
            return SubagentDiscoveryApprovalPolicyRule(), []
        raw_config = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
        defaults_row = dict(raw_config.get("defaults") or {}) if isinstance(raw_config, dict) else {}
        defaults = cls._rule_from_row(defaults_row, index=0, fallback_rule_id="default")
        rules: list[SubagentDiscoveryApprovalPolicyRule] = []
        for index, row in enumerate(list((raw_config.get("rules") if isinstance(raw_config, dict) else []) or [])):
            if not isinstance(row, dict):
                continue
            rules.append(cls._rule_from_row(row, index=index + 1, fallback_rule_id=f"rule_{index + 1}"))
        return defaults, rules

    @classmethod
    def _rule_from_row(
        cls,
        row: dict,
        *,
        index: int,
        fallback_rule_id: str,
    ) -> SubagentDiscoveryApprovalPolicyRule:
        return SubagentDiscoveryApprovalPolicyRule(
            auto_approve=bool(row.get("auto_approve", False)),
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
            allow_recommended_candidate_override=bool(
                row.get("allow_recommended_candidate_override", False)
            ),
            require_recommended_candidate=bool(row.get("require_recommended_candidate", True)),
            require_runtime_registration_template=bool(row.get("require_runtime_registration_template", True)),
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
    "SubagentDiscoveryApprovalPolicyRule",
    "SubagentDiscoveryApprovalPolicyService",
]
