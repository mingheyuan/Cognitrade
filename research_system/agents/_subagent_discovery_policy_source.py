from __future__ import annotations

from collections.abc import Callable
from typing import Any

from research_system.agents._subagent_discovery_shadow_policy import SubagentDiscoveryLLMShadowPolicy
from research_system.schemas.subagents import SubagentCapabilityGap


class SubagentDiscoveryLLMPolicySource:
    def __init__(
        self,
        *,
        shadow_policy: SubagentDiscoveryLLMShadowPolicy,
        settings=None,
        runtime_binding_getter: Callable[[], dict[str, Any]] | None = None,
        emit_event: Callable[..., None] | None = None,
    ) -> None:
        self.shadow_policy = shadow_policy
        self.settings = settings
        self.runtime_binding_getter = runtime_binding_getter
        self.emit_event = emit_event

    def apply(
        self,
        *,
        gap: SubagentCapabilityGap,
        candidate_protocols: list[str],
        rule_preferred_protocols: list[str],
        rule_notes: list[str],
    ) -> tuple[list[str], list[str], dict[str, Any] | None]:
        shadow_payload = self.shadow_policy.suggest(
            gap=gap,
            candidate_protocols=candidate_protocols,
            rule_preferred_protocols=rule_preferred_protocols,
        )
        final_protocols = list(rule_preferred_protocols)
        final_notes = list(rule_notes)

        if isinstance(shadow_payload, dict) and self._llm_enabled():
            selected_llm_protocols = self._select_protocols(
                shadow_payload=shadow_payload,
                min_confidence=float(getattr(self.settings, "subagent_discovery_llm_policy_min_confidence", 0.7) or 0.7),
            )
            if selected_llm_protocols is not None:
                final_protocols = list(selected_llm_protocols)
                final_notes = [
                    *final_notes,
                    "apply_subagent_discovery_llm_protocols",
                    f"llm_confidence={float(shadow_payload.get('confidence') or 0.0):.2f}",
                    *list(shadow_payload.get("notes") or []),
                ]
                self._record_policy_application(
                    event_name="subagent_discovery_llm_applied",
                    policy_source="llm",
                    decision_store_name="subagent_discovery_llm_decisions",
                    gap=gap,
                    rule_preferred_protocols=rule_preferred_protocols,
                    applied_protocols=final_protocols,
                    shadow_payload=shadow_payload,
                )
                self.shadow_policy.observe(
                    gap=gap,
                    candidate_protocols=candidate_protocols,
                    rule_preferred_protocols=rule_preferred_protocols,
                    actual_protocols=final_protocols,
                    shadow_payload=shadow_payload,
                )
                return final_protocols, final_notes, shadow_payload

        if isinstance(shadow_payload, dict) and self._hybrid_enabled():
            selected_hybrid_protocols = self._select_protocols(
                shadow_payload=shadow_payload,
                min_confidence=float(getattr(self.settings, "subagent_discovery_hybrid_llm_min_confidence", 0.7) or 0.7),
            )
            if selected_hybrid_protocols is not None and list(selected_hybrid_protocols) != list(rule_preferred_protocols):
                final_protocols = list(selected_hybrid_protocols)
                final_notes = [
                    *final_notes,
                    "apply_subagent_discovery_hybrid_protocols",
                    f"hybrid_confidence={float(shadow_payload.get('confidence') or 0.0):.2f}",
                    *list(shadow_payload.get("notes") or []),
                ]
                self._record_policy_application(
                    event_name="subagent_discovery_hybrid_applied",
                    policy_source="llm_hybrid",
                    decision_store_name="subagent_discovery_hybrid_decisions",
                    gap=gap,
                    rule_preferred_protocols=rule_preferred_protocols,
                    applied_protocols=final_protocols,
                    shadow_payload=shadow_payload,
                )

        self.shadow_policy.observe(
            gap=gap,
            candidate_protocols=candidate_protocols,
            rule_preferred_protocols=rule_preferred_protocols,
            actual_protocols=final_protocols,
            shadow_payload=shadow_payload,
        )
        return final_protocols, final_notes, shadow_payload

    def _llm_enabled(self) -> bool:
        return bool(getattr(self.settings, "subagent_discovery_llm_policy_enabled", False))

    def _hybrid_enabled(self) -> bool:
        return bool(getattr(self.settings, "subagent_discovery_hybrid_llm_enabled", False))

    @staticmethod
    def _select_protocols(
        *,
        shadow_payload: dict[str, Any],
        min_confidence: float,
    ) -> list[str] | None:
        confidence = float(shadow_payload.get("confidence") or 0.0)
        if confidence < float(min_confidence):
            return None
        selected_protocols = [
            str(item).strip()
            for item in list(shadow_payload.get("selected_protocols") or [])
            if str(item).strip()
        ]
        if not selected_protocols:
            return None
        return selected_protocols

    def _record_policy_application(
        self,
        *,
        event_name: str,
        policy_source: str,
        decision_store_name: str,
        gap: SubagentCapabilityGap,
        rule_preferred_protocols: list[str],
        applied_protocols: list[str],
        shadow_payload: dict[str, Any],
    ) -> None:
        binding = dict(self.runtime_binding_getter() or {}) if callable(self.runtime_binding_getter) else {}
        context = binding.get("context")
        support = binding.get("support")
        request_id = str(binding.get("request_id") or "").strip()
        intent = str(binding.get("intent") or gap.intent or "").strip()
        if context is None or support is None or not request_id or not intent:
            return
        payload = {
            "event": event_name,
            "intent": gap.intent,
            "stage": gap.stage,
            "agent": shadow_payload.get("agent"),
            "capability": gap.capability,
            "preferred_subagent_type": gap.preferred_subagent_type,
            "policy_source": policy_source,
            "selected_protocols": list(shadow_payload.get("selected_protocols") or []),
            "candidate_protocols": list(shadow_payload.get("candidate_protocols") or []),
            "rule_preferred_protocols": list(rule_preferred_protocols),
            "applied_protocols": list(applied_protocols),
            "override_rule_protocols": list(rule_preferred_protocols) != list(applied_protocols),
            "reason": shadow_payload.get("reason"),
            "confidence": shadow_payload.get("confidence"),
            "notes": list(shadow_payload.get("notes") or []),
        }
        getattr(context, decision_store_name).append(dict(payload))
        context.dynamic_trace.append(dict(payload))
        if callable(self.emit_event):
            self.emit_event(
                event_type=event_name,
                message=f"{event_name} selected protocols for capability {gap.capability}",
                payload=dict(payload),
                phase=str(gap.stage or "").strip() or None,
                agent=str(shadow_payload.get("agent") or "").strip() or None,
            )
        else:
            support.emit_event(
                context,
                request_id=request_id,
                intent=intent,
                event_type=event_name,
                phase=str(gap.stage or "").strip() or None,
                agent=str(shadow_payload.get("agent") or "").strip() or None,
                message=f"{event_name} selected protocols for capability {gap.capability}",
                payload=dict(payload),
            )


__all__ = ["SubagentDiscoveryLLMPolicySource"]
