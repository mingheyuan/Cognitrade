from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from research_system.runtime.core.execution_policy import derive_llm_error_reason
from research_system.runtime.providers.provider_registry import LLMProviderError
from research_system.schemas.subagents import (
    SubagentCatalogEntry,
    SubagentDiscoveryCandidateRecommendation,
    SubagentDiscoveryTicket,
)


class _SubagentDiscoveryCandidateShadowPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_subagent_type: str
    reason: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    notes: list[str] = Field(default_factory=list)


class SubagentDiscoveryCandidatePolicyService:
    def __init__(
        self,
        *,
        llm_provider=None,
        settings=None,
        runtime_binding_getter=None,
        emit_event=None,
        service_name: str | None = None,
    ) -> None:
        self.llm_provider = llm_provider
        self.settings = settings
        self.runtime_binding_getter = runtime_binding_getter
        self.emit_event = emit_event
        self.service_name = service_name or "subagent_discovery"

    def recommend(
        self,
        *,
        ticket: SubagentDiscoveryTicket,
        candidate_entries: list[SubagentCatalogEntry],
    ) -> tuple[SubagentDiscoveryCandidateRecommendation | None, SubagentDiscoveryCandidateRecommendation | None]:
        entries = [entry for entry in list(candidate_entries or []) if isinstance(entry, SubagentCatalogEntry)]
        if not entries:
            return None, None
        rule_recommendation = self._rule_recommendation(ticket=ticket, candidate_entries=entries)
        shadow_recommendation, shadow_error_note = self._shadow_recommendation(
            ticket=ticket,
            candidate_entries=entries,
            rule_recommendation=rule_recommendation,
        )
        if shadow_error_note:
            rule_recommendation = rule_recommendation.model_copy(
                update={"notes": [*list(rule_recommendation.notes or []), shadow_error_note]},
                deep=True,
            )
        actual_recommendation = rule_recommendation
        applied_event_name: str | None = None
        applied_policy_source: str | None = None
        if shadow_recommendation is not None and self._llm_enabled():
            actual_llm_recommendation = self._apply_shadow_recommendation(
                ticket=ticket,
                candidate_entries=entries,
                shadow_recommendation=shadow_recommendation,
                min_confidence=float(
                    getattr(self.settings, "subagent_discovery_llm_policy_min_confidence", 0.7) or 0.7
                ),
                policy_source="llm",
                note_prefix="apply_subagent_discovery_llm_candidate",
            )
            if actual_llm_recommendation is not None:
                actual_recommendation = actual_llm_recommendation
                applied_event_name = "subagent_discovery_candidate_llm_applied"
                applied_policy_source = "llm"
                self._record_shadow_recommendation(
                    ticket=ticket,
                    candidate_entries=entries,
                    rule_recommendation=rule_recommendation,
                    shadow_recommendation=shadow_recommendation,
                    actual_recommendation=actual_recommendation,
                )
                self._record_policy_application(
                    event_name=applied_event_name,
                    decision_store_name="subagent_discovery_candidate_llm_decisions",
                    policy_source=applied_policy_source,
                    ticket=ticket,
                    candidate_entries=entries,
                    rule_recommendation=rule_recommendation,
                    actual_recommendation=actual_recommendation,
                    shadow_recommendation=shadow_recommendation,
                )
                return actual_recommendation, shadow_recommendation
        if shadow_recommendation is not None and self._hybrid_enabled():
            actual_hybrid_recommendation = self._apply_shadow_recommendation(
                ticket=ticket,
                candidate_entries=entries,
                shadow_recommendation=shadow_recommendation,
                min_confidence=float(
                    getattr(self.settings, "subagent_discovery_hybrid_llm_min_confidence", 0.7) or 0.7
                ),
                policy_source="hybrid",
                note_prefix="apply_subagent_discovery_hybrid_candidate",
            )
            if (
                actual_hybrid_recommendation is not None
                and actual_hybrid_recommendation.candidate_subagent_type != rule_recommendation.candidate_subagent_type
            ):
                actual_recommendation = actual_hybrid_recommendation
                applied_event_name = "subagent_discovery_candidate_hybrid_applied"
                applied_policy_source = "hybrid"
        self._record_shadow_recommendation(
            ticket=ticket,
            candidate_entries=entries,
            rule_recommendation=rule_recommendation,
            shadow_recommendation=shadow_recommendation,
            actual_recommendation=actual_recommendation,
        )
        if (
            shadow_recommendation is not None
            and applied_event_name is not None
            and applied_policy_source is not None
        ):
            self._record_policy_application(
                event_name=applied_event_name,
                decision_store_name=(
                    "subagent_discovery_candidate_hybrid_decisions"
                    if applied_policy_source == "hybrid"
                    else "subagent_discovery_candidate_llm_decisions"
                ),
                policy_source=applied_policy_source,
                ticket=ticket,
                candidate_entries=entries,
                rule_recommendation=rule_recommendation,
                actual_recommendation=actual_recommendation,
                shadow_recommendation=shadow_recommendation,
            )
        return actual_recommendation, shadow_recommendation

    def _rule_recommendation(
        self,
        *,
        ticket: SubagentDiscoveryTicket,
        candidate_entries: list[SubagentCatalogEntry],
    ) -> SubagentDiscoveryCandidateRecommendation:
        gap = ticket.capability_gap
        request = ticket.tool_discovery_request
        preferred_protocols = [
            str(item).strip().lower()
            for item in list(request.preferred_protocols or [])
            if str(item).strip()
        ]
        protocol_order = {protocol: index for index, protocol in enumerate(preferred_protocols)}
        preferred_subagent_type = str(gap.preferred_subagent_type or "").strip()
        selected_entry = sorted(
            candidate_entries,
            key=lambda entry: (
                0 if preferred_subagent_type and str(entry.subagent_type).strip() == preferred_subagent_type else 1,
                self._protocol_rank(self._entry_protocol(entry), protocol_order),
                0 if self._matches_intent(entry, gap.intent) else 1,
                0 if self._matches_stage(entry, gap.stage) else 1,
                self._side_effect_rank(entry.side_effect_level),
                self._requires_llm_rank(entry.requires_llm, gap.requires_llm),
                self._cost_rank(entry.cost_class),
                self._latency_rank(entry.latency_class),
                str(entry.subagent_type or "").strip(),
            ),
        )[0]
        selected_protocol = self._entry_protocol(selected_entry)
        notes = [
            "candidate_recommendation_rule_selected",
            *(["preferred_subagent_type_match"] if preferred_subagent_type == str(selected_entry.subagent_type).strip() else []),
            *(["candidate_stage_match"] if self._matches_stage(selected_entry, gap.stage) else []),
            *(["candidate_intent_match"] if self._matches_intent(selected_entry, gap.intent) else []),
            *([f"preferred_protocol_rank={protocol_order[selected_protocol]}"] if selected_protocol in protocol_order else []),
        ]
        reason_parts = []
        if preferred_subagent_type == str(selected_entry.subagent_type).strip():
            reason_parts.append("matched preferred_subagent_type")
        if selected_protocol:
            reason_parts.append(f"preferred protocol {selected_protocol}")
        if self._matches_intent(selected_entry, gap.intent):
            reason_parts.append("intent-compatible")
        if self._matches_stage(selected_entry, gap.stage):
            reason_parts.append("stage-compatible")
        reason = ", ".join(reason_parts) or "selected highest-ranked candidate by rule ordering"
        return SubagentDiscoveryCandidateRecommendation(
            candidate_subagent_type=str(selected_entry.subagent_type).strip(),
            source_protocol=selected_protocol,
            reason=reason,
            policy_source="rule",
            confidence=1.0,
            notes=notes,
        )

    def _shadow_recommendation(
        self,
        *,
        ticket: SubagentDiscoveryTicket,
        candidate_entries: list[SubagentCatalogEntry],
        rule_recommendation: SubagentDiscoveryCandidateRecommendation,
    ) -> tuple[SubagentDiscoveryCandidateRecommendation | None, str | None]:
        if not self._shadow_enabled():
            return None, None
        if self.llm_provider is None or not bool(getattr(self.llm_provider, "available", True)):
            return None, None
        binding = dict(self.runtime_binding_getter() or {}) if callable(self.runtime_binding_getter) else {}
        context = binding.get("context")
        support = binding.get("support")
        request_id = str(binding.get("request_id") or "").strip()
        intent = str(binding.get("intent") or ticket.capability_gap.intent or "").strip()
        phase = str(binding.get("phase") or ticket.capability_gap.stage or "discovery").strip() or "discovery"
        agent = str(binding.get("agent") or self.service_name).strip() or self.service_name
        runtime_managed = context is not None and support is not None and bool(request_id) and bool(intent)
        if runtime_managed and not self._enabled_for_runtime_suggestion(binding=binding):
            return None, None
        if runtime_managed:
            llm_decision = support.resolve_llm_execution(
                context,
                request_id=request_id,
                agent=agent,
                phase=phase,
                strategy="subagent_discovery_candidate_shadow_policy",
                provider=self.llm_provider,
                reserve=True,
            )
            if not llm_decision.can_use_llm:
                return None, None
        payload = {
            "intent": ticket.capability_gap.intent,
            "stage": ticket.capability_gap.stage,
            "capability": ticket.capability_gap.capability,
            "preferred_subagent_type": ticket.capability_gap.preferred_subagent_type,
            "requires_llm": ticket.capability_gap.requires_llm,
            "preferred_protocols": list(ticket.tool_discovery_request.preferred_protocols or []),
            "rule_recommendation": rule_recommendation.model_dump(mode="json"),
            "candidates": [self._candidate_prompt_row(entry) for entry in candidate_entries],
        }
        try:
            suggestion = self.llm_provider.parse(
                system_prompt=(
                    "You are the shadow candidate-ranking policy for a controlled subagent discovery approval system.\n"
                    "Select exactly one candidate_subagent_type from the provided candidates.\n"
                    "Never invent a candidate outside the provided list.\n"
                    "Return a compact structured suggestion."
                ),
                user_prompt=f"Current discovery candidate ranking state: {payload}\nReturn a compact structured suggestion.",
                schema=_SubagentDiscoveryCandidateShadowPayload,
                temperature=0.0,
                reasoning_effort=str(
                    getattr(self.settings, "subagent_discovery_shadow_llm_reasoning_effort", "low") or "low"
                ),
            )
        except LLMProviderError as exc:
            if runtime_managed:
                support.record_degradation(
                    context,
                    request_id=request_id,
                    component="subagent_discovery_candidate_shadow_policy",
                    reason=derive_llm_error_reason(exc),
                    phase=phase,
                    agent=agent,
                    payload={"intent": intent, "stage": ticket.capability_gap.stage, "capability": ticket.capability_gap.capability},
                )
            return None, f"candidate_shadow_policy_error={derive_llm_error_reason(exc)}"
        except Exception as exc:
            if runtime_managed:
                support.record_degradation(
                    context,
                    request_id=request_id,
                    component="subagent_discovery_candidate_shadow_policy",
                    reason=derive_llm_error_reason(exc),
                    phase=phase,
                    agent=agent,
                    payload={"intent": intent, "stage": ticket.capability_gap.stage, "capability": ticket.capability_gap.capability},
                )
            return None, f"candidate_shadow_policy_error={derive_llm_error_reason(exc)}"
        selected_entry = self._find_candidate_entry(candidate_entries, suggestion.candidate_subagent_type)
        if selected_entry is None:
            return None, "candidate_shadow_policy_error=selected_candidate_not_available"
        return (
            SubagentDiscoveryCandidateRecommendation(
                candidate_subagent_type=str(selected_entry.subagent_type).strip(),
                source_protocol=self._entry_protocol(selected_entry),
                reason=str(suggestion.reason or "").strip() or "shadow candidate ranking recommendation",
                policy_source="llm_shadow",
                confidence=float(suggestion.confidence or 0.0),
                notes=[
                    "candidate_recommendation_shadow_suggested",
                    *list(suggestion.notes or []),
                ],
            ),
            None,
        )

    def _record_shadow_recommendation(
        self,
        *,
        ticket: SubagentDiscoveryTicket,
        candidate_entries: list[SubagentCatalogEntry],
        rule_recommendation: SubagentDiscoveryCandidateRecommendation,
        shadow_recommendation: SubagentDiscoveryCandidateRecommendation | None,
        actual_recommendation: SubagentDiscoveryCandidateRecommendation,
    ) -> None:
        if shadow_recommendation is None:
            return
        binding = dict(self.runtime_binding_getter() or {}) if callable(self.runtime_binding_getter) else {}
        context = binding.get("context")
        support = binding.get("support")
        request_id = str(binding.get("request_id") or "").strip()
        intent = str(binding.get("intent") or ticket.capability_gap.intent or "").strip()
        phase = str(binding.get("phase") or ticket.capability_gap.stage or "discovery").strip() or "discovery"
        agent = str(binding.get("agent") or self.service_name).strip() or self.service_name
        if context is None or support is None or not request_id or not intent:
            return
        payload = {
            "event": "subagent_discovery_candidate_shadow_suggested",
            "intent": ticket.capability_gap.intent,
            "stage": ticket.capability_gap.stage,
            "agent": agent,
            "capability": ticket.capability_gap.capability,
            "preferred_subagent_type": ticket.capability_gap.preferred_subagent_type,
            "selected_candidate_subagent_type": shadow_recommendation.candidate_subagent_type,
            "selected_protocol": shadow_recommendation.source_protocol,
            "rule_candidate_subagent_type": rule_recommendation.candidate_subagent_type,
            "rule_protocol": rule_recommendation.source_protocol,
            "actual_candidate_subagent_type": actual_recommendation.candidate_subagent_type,
            "actual_protocol": actual_recommendation.source_protocol,
            "candidate_subagent_types": [
                str(entry.subagent_type).strip()
                for entry in candidate_entries
                if str(entry.subagent_type or "").strip()
            ],
            "candidate_protocols": [
                self._entry_protocol(entry)
                for entry in candidate_entries
            ],
            "reason": shadow_recommendation.reason,
            "confidence": shadow_recommendation.confidence,
            "notes": list(shadow_recommendation.notes or []),
            "candidate_match": (
                shadow_recommendation.candidate_subagent_type
                == actual_recommendation.candidate_subagent_type
            ),
        }
        context.subagent_discovery_candidate_shadow_suggestions.append(dict(payload))
        context.dynamic_trace.append(dict(payload))
        if callable(self.emit_event):
            self.emit_event(
                event_type="subagent_discovery_candidate_shadow_suggested",
                message=(
                    f"Shadow discovery candidate policy evaluated capability "
                    f"{ticket.capability_gap.capability}"
                ),
                payload=dict(payload),
                phase=phase,
                agent=agent,
            )
        else:
            support.emit_event(
                context,
                request_id=request_id,
                intent=intent,
                event_type="subagent_discovery_candidate_shadow_suggested",
                phase=phase,
                agent=agent,
                message=(
                    f"Shadow discovery candidate policy evaluated capability "
                    f"{ticket.capability_gap.capability}"
                ),
                payload=dict(payload),
            )

    def _record_policy_application(
        self,
        *,
        event_name: str,
        decision_store_name: str,
        policy_source: str,
        ticket: SubagentDiscoveryTicket,
        candidate_entries: list[SubagentCatalogEntry],
        rule_recommendation: SubagentDiscoveryCandidateRecommendation,
        actual_recommendation: SubagentDiscoveryCandidateRecommendation,
        shadow_recommendation: SubagentDiscoveryCandidateRecommendation,
    ) -> None:
        binding = dict(self.runtime_binding_getter() or {}) if callable(self.runtime_binding_getter) else {}
        context = binding.get("context")
        support = binding.get("support")
        request_id = str(binding.get("request_id") or "").strip()
        intent = str(binding.get("intent") or ticket.capability_gap.intent or "").strip()
        phase = str(binding.get("phase") or ticket.capability_gap.stage or "discovery").strip() or "discovery"
        agent = str(binding.get("agent") or self.service_name).strip() or self.service_name
        if context is None or support is None or not request_id or not intent:
            return
        payload = {
            "event": event_name,
            "intent": ticket.capability_gap.intent,
            "stage": ticket.capability_gap.stage,
            "agent": agent,
            "capability": ticket.capability_gap.capability,
            "preferred_subagent_type": ticket.capability_gap.preferred_subagent_type,
            "policy_source": policy_source,
            "selected_candidate_subagent_type": shadow_recommendation.candidate_subagent_type,
            "selected_protocol": shadow_recommendation.source_protocol,
            "rule_candidate_subagent_type": rule_recommendation.candidate_subagent_type,
            "rule_protocol": rule_recommendation.source_protocol,
            "applied_candidate_subagent_type": actual_recommendation.candidate_subagent_type,
            "applied_protocol": actual_recommendation.source_protocol,
            "override_rule_candidate": (
                actual_recommendation.candidate_subagent_type
                != rule_recommendation.candidate_subagent_type
            ),
            "candidate_subagent_types": [
                str(entry.subagent_type).strip()
                for entry in candidate_entries
                if str(entry.subagent_type or "").strip()
            ],
            "candidate_protocols": [
                self._entry_protocol(entry)
                for entry in candidate_entries
            ],
            "reason": shadow_recommendation.reason,
            "confidence": shadow_recommendation.confidence,
            "notes": list(shadow_recommendation.notes or []),
        }
        getattr(context, decision_store_name).append(dict(payload))
        context.dynamic_trace.append(dict(payload))
        if callable(self.emit_event):
            self.emit_event(
                event_type=event_name,
                message=(
                    f"{event_name} ranked discovery candidates for capability "
                    f"{ticket.capability_gap.capability}"
                ),
                payload=dict(payload),
                phase=phase,
                agent=agent,
            )
        else:
            support.emit_event(
                context,
                request_id=request_id,
                intent=intent,
                event_type=event_name,
                phase=phase,
                agent=agent,
                message=(
                    f"{event_name} ranked discovery candidates for capability "
                    f"{ticket.capability_gap.capability}"
                ),
                payload=dict(payload),
            )

    def _apply_shadow_recommendation(
        self,
        *,
        ticket: SubagentDiscoveryTicket,
        candidate_entries: list[SubagentCatalogEntry],
        shadow_recommendation: SubagentDiscoveryCandidateRecommendation,
        min_confidence: float,
        policy_source: str,
        note_prefix: str,
    ) -> SubagentDiscoveryCandidateRecommendation | None:
        confidence = float(shadow_recommendation.confidence or 0.0)
        if confidence < float(min_confidence):
            return None
        selected_entry = self._find_candidate_entry(
            candidate_entries,
            shadow_recommendation.candidate_subagent_type,
        )
        if selected_entry is None:
            return None
        return SubagentDiscoveryCandidateRecommendation(
            candidate_subagent_type=str(selected_entry.subagent_type).strip(),
            source_protocol=self._entry_protocol(selected_entry),
            reason=str(shadow_recommendation.reason or "").strip() or ticket.capability_gap.reason,
            policy_source=policy_source,  # type: ignore[arg-type]
            confidence=confidence,
            notes=[
                note_prefix,
                f"confidence={confidence:.2f}",
                *list(shadow_recommendation.notes or []),
            ],
        )

    def _shadow_enabled(self) -> bool:
        return bool(
            getattr(self.settings, "subagent_discovery_shadow_llm_enabled", False)
            or getattr(self.settings, "subagent_discovery_llm_policy_enabled", False)
            or getattr(self.settings, "subagent_discovery_hybrid_llm_enabled", False)
        )

    def _enabled_for_runtime_suggestion(self, *, binding: dict[str, Any]) -> bool:
        execution_policy = binding.get("execution_policy")
        max_llm_calls_per_turn = int(
            getattr(
                execution_policy,
                "max_llm_calls_per_turn",
                getattr(self.settings, "max_llm_calls_per_turn", 1),
            )
            or 1
        )
        return max_llm_calls_per_turn > 1

    def _llm_enabled(self) -> bool:
        return bool(getattr(self.settings, "subagent_discovery_llm_policy_enabled", False))

    def _hybrid_enabled(self) -> bool:
        return bool(getattr(self.settings, "subagent_discovery_hybrid_llm_enabled", False))

    @staticmethod
    def _find_candidate_entry(
        candidate_entries: list[SubagentCatalogEntry],
        candidate_subagent_type: str,
    ) -> SubagentCatalogEntry | None:
        requested = str(candidate_subagent_type or "").strip()
        for entry in candidate_entries:
            if str(entry.subagent_type).strip() == requested:
                return entry
        return None

    @staticmethod
    def _candidate_prompt_row(entry: SubagentCatalogEntry) -> dict[str, Any]:
        templates = [
            str(template.template_id).strip()
            for template in list(entry.runtime_registration_templates or [])
            if str(template.template_id or "").strip()
        ]
        return {
            "candidate_subagent_type": str(entry.subagent_type or "").strip(),
            "summary": str(entry.summary or "").strip(),
            "capabilities": list(entry.capabilities or []),
            "stage": entry.stage,
            "suitable_intents": list(entry.suitable_intents or []),
            "source_protocol": SubagentDiscoveryCandidatePolicyService._entry_protocol(entry),
            "cost_class": entry.cost_class,
            "latency_class": entry.latency_class,
            "side_effect_level": entry.side_effect_level,
            "requires_llm": entry.requires_llm,
            "tags": list(entry.tags or []),
            "runtime_registration_templates": templates,
        }

    @staticmethod
    def _entry_protocol(entry: SubagentCatalogEntry) -> str | None:
        tags = [str(tag).strip() for tag in list(entry.tags or []) if str(tag).strip()]
        for tag in tags:
            if tag.startswith("mcp_source:"):
                return "mcp"
            if tag.startswith("openapi_source:"):
                return "openapi"
            if tag.startswith("skill_registration:"):
                return "skill"
            if tag.startswith("http_json_source:"):
                return "http_json"
        for template in list(entry.runtime_registration_templates or []):
            registration_type = str(template.registration_type or "").strip()
            if registration_type == "mcp_tool_runner":
                return "mcp"
            if registration_type == "openapi_http_runner":
                return "openapi"
            if registration_type in {"snapshot_skill_runner", "skill_runner"}:
                return "skill"
            if registration_type == "http_json_runner":
                return "http_json"
        return None

    @staticmethod
    def _matches_intent(entry: SubagentCatalogEntry, intent: str | None) -> bool:
        normalized_intent = str(intent or "").strip()
        suitable_intents = {str(item).strip() for item in list(entry.suitable_intents or []) if str(item).strip()}
        if not normalized_intent or not suitable_intents:
            return True
        return normalized_intent in suitable_intents

    @staticmethod
    def _matches_stage(entry: SubagentCatalogEntry, stage: str | None) -> bool:
        normalized_stage = str(stage or "").strip().lower()
        entry_stage = str(entry.stage or "").strip().lower()
        if not normalized_stage or not entry_stage:
            return True
        return normalized_stage == entry_stage

    @staticmethod
    def _protocol_rank(protocol: str | None, protocol_order: dict[str, int]) -> int:
        if protocol is None:
            return len(protocol_order) + 1
        return int(protocol_order.get(protocol, len(protocol_order) + 1))

    @staticmethod
    def _side_effect_rank(side_effect_level: str | None) -> int:
        return {"none": 0, "read_only": 1, "write_local": 2, "write_external": 3}.get(
            str(side_effect_level or "").strip().lower(),
            4,
        )

    @staticmethod
    def _requires_llm_rank(entry_requires_llm: bool, gap_requires_llm: bool | None) -> int:
        if gap_requires_llm is None:
            return 0 if not entry_requires_llm else 1
        if gap_requires_llm:
            return 0 if entry_requires_llm else 1
        return 0 if not entry_requires_llm else 1

    @staticmethod
    def _cost_rank(cost_class: str | None) -> int:
        return {"low": 0, "medium": 1, "high": 2}.get(str(cost_class or "").strip().lower(), 3)

    @staticmethod
    def _latency_rank(latency_class: str | None) -> int:
        return {"low": 0, "medium": 1, "high": 2}.get(str(latency_class or "").strip().lower(), 3)


__all__ = ["SubagentDiscoveryCandidatePolicyService"]
