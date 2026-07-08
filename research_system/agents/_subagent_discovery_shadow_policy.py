from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from research_system.runtime.core.execution_policy import derive_llm_error_reason
from research_system.runtime.providers.provider_registry import LLMProviderError
from research_system.schemas.subagents import SubagentCapabilityGap


class SubagentDiscoveryShadowSuggestionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_protocols: list[str] = Field(default_factory=list)
    reason: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    notes: list[str] = Field(default_factory=list)


class SubagentDiscoveryLLMShadowPolicy:
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
        self.service_name = service_name or "subagent_service"

    def suggest(
        self,
        *,
        gap: SubagentCapabilityGap,
        candidate_protocols: list[str],
        rule_preferred_protocols: list[str],
    ) -> dict[str, Any] | None:
        binding = dict(self.runtime_binding_getter() or {}) if callable(self.runtime_binding_getter) else {}
        context = binding.get("context")
        support = binding.get("support")
        request_id = str(binding.get("request_id") or "").strip()
        intent = str(binding.get("intent") or gap.intent or "").strip()
        phase = str(binding.get("phase") or gap.stage or "discovery").strip() or "discovery"
        agent = str(binding.get("agent") or self.service_name).strip() or self.service_name
        if context is None or support is None or not request_id or not intent:
            return None
        if not self._enabled_for_suggestion(binding=binding):
            return None
        llm_decision = support.resolve_llm_execution(
            context,
            request_id=request_id,
            agent=agent,
            phase=phase,
            strategy="subagent_discovery_shadow_policy",
            provider=self.llm_provider,
            reserve=True,
        )
        if not llm_decision.can_use_llm:
            return None
        if not candidate_protocols:
            return None
        prompt_payload = {
            "intent": gap.intent,
            "stage": gap.stage,
            "capability": gap.capability,
            "preferred_subagent_type": gap.preferred_subagent_type,
            "requires_llm": gap.requires_llm,
            "candidate_protocols": list(candidate_protocols),
            "rule_preferred_protocols": list(rule_preferred_protocols),
            "gap_notes": list(gap.notes or []),
        }
        try:
            suggestion = self.llm_provider.parse(
                system_prompt=(
                    "You are the shadow discovery-policy for a controlled subagent capability-gap router.\n"
                    "Choose the best preferred protocol order from the provided candidate_protocols list.\n"
                    "Never invent protocols outside candidate_protocols.\n"
                    "Return a compact structured suggestion."
                ),
                user_prompt=(f"Current discovery-policy state: {prompt_payload}\nReturn a compact structured suggestion."),
                schema=SubagentDiscoveryShadowSuggestionPayload,
                temperature=0.0,
                reasoning_effort=str(
                    getattr(self.settings, "subagent_discovery_shadow_llm_reasoning_effort", "low") or "low"
                ),
            )
        except LLMProviderError as exc:
            support.record_degradation(
                context,
                request_id=request_id,
                component="subagent_discovery_shadow_policy",
                reason=derive_llm_error_reason(exc),
                phase=phase,
                agent=agent,
                payload={"intent": intent, "stage": gap.stage, "capability": gap.capability},
            )
            return None
        except Exception as exc:
            support.record_degradation(
                context,
                request_id=request_id,
                component="subagent_discovery_shadow_policy",
                reason=derive_llm_error_reason(exc),
                phase=phase,
                agent=agent,
                payload={"intent": intent, "stage": gap.stage, "capability": gap.capability},
            )
            return None
        selected_protocols = self._normalize_protocols(
            list(suggestion.selected_protocols or []),
            allowed_protocols=candidate_protocols,
        )
        if not selected_protocols:
            selected_protocols = list(rule_preferred_protocols)
        return {
            "policy_source": "llm_shadow",
            "intent": gap.intent,
            "stage": gap.stage,
            "agent": agent,
            "capability": gap.capability,
            "preferred_subagent_type": gap.preferred_subagent_type,
            "selected_protocols": selected_protocols,
            "candidate_protocols": list(candidate_protocols),
            "rule_preferred_protocols": list(rule_preferred_protocols),
            "reason": suggestion.reason,
            "confidence": suggestion.confidence,
            "notes": list(suggestion.notes or []),
        }

    def observe(
        self,
        *,
        gap: SubagentCapabilityGap,
        candidate_protocols: list[str],
        rule_preferred_protocols: list[str],
        actual_protocols: list[str],
        shadow_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        payload = shadow_payload or self.suggest(
            gap=gap,
            candidate_protocols=candidate_protocols,
            rule_preferred_protocols=rule_preferred_protocols,
        )
        if not isinstance(payload, dict):
            return None
        enriched_payload = {
            "event": "subagent_discovery_shadow_suggested",
            **dict(payload),
            "actual_protocols": list(actual_protocols),
            "protocol_match": list(payload.get("selected_protocols") or []) == list(actual_protocols),
        }
        self._record(enriched_payload)
        return enriched_payload

    def _record(self, payload: dict[str, Any]) -> None:
        binding = dict(self.runtime_binding_getter() or {}) if callable(self.runtime_binding_getter) else {}
        context = binding.get("context")
        support = binding.get("support")
        request_id = str(binding.get("request_id") or "").strip()
        intent = str(payload.get("intent") or "").strip()
        if context is None or support is None or not request_id or not intent:
            return
        context.subagent_discovery_shadow_suggestions.append(dict(payload))
        context.dynamic_trace.append(dict(payload))
        if callable(self.emit_event):
            self.emit_event(
                event_type="subagent_discovery_shadow_suggested",
                message=f"Shadow discovery policy evaluated capability {payload.get('capability')}",
                payload=dict(payload),
                phase=str(payload.get("stage") or "").strip() or None,
                agent=str(payload.get("agent") or "").strip() or None,
            )
        else:
            support.emit_event(
                context,
                request_id=request_id,
                intent=intent,
                event_type="subagent_discovery_shadow_suggested",
                phase=str(payload.get("stage") or "").strip() or None,
                agent=str(payload.get("agent") or "").strip() or None,
                message=f"Shadow discovery policy evaluated capability {payload.get('capability')}",
                payload=dict(payload),
            )

    def _enabled_for_suggestion(self, *, binding: dict[str, Any]) -> bool:
        if self.llm_provider is None:
            return False
        if not bool(getattr(self.llm_provider, "available", True)):
            return False
        if not (
            bool(getattr(self.settings, "subagent_discovery_shadow_llm_enabled", False))
            or bool(getattr(self.settings, "subagent_discovery_llm_policy_enabled", False))
            or bool(getattr(self.settings, "subagent_discovery_hybrid_llm_enabled", False))
        ):
            return False
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

    @staticmethod
    def _normalize_protocols(raw_protocols: list[str], *, allowed_protocols: list[str]) -> list[str]:
        allowed = {str(item).strip() for item in list(allowed_protocols or []) if str(item).strip()}
        normalized: list[str] = []
        seen: set[str] = set()
        for item in list(raw_protocols or []):
            protocol = str(item or "").strip()
            if not protocol or protocol in seen or protocol not in allowed:
                continue
            normalized.append(protocol)
            seen.add(protocol)
        return normalized


__all__ = ["SubagentDiscoveryLLMShadowPolicy", "SubagentDiscoveryShadowSuggestionPayload"]
