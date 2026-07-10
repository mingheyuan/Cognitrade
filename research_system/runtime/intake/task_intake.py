from __future__ import annotations

from typing import Any

from research_system.runtime.domain_packs.registry import DomainPackRegistry
from research_system.schemas.protocol import ClarificationPayload, RequestEnvelope
from research_system.schemas.task_intake import TaskIntakeDecision


class TaskIntakeService:
    """Resolve requests through installed DomainPack-owned intake policies."""

    def __init__(self, registry: DomainPackRegistry) -> None:
        self.registry = registry

    def resolve(
        self,
        request: RequestEnvelope,
        *,
        runtime_context: dict[str, Any] | None = None,
    ) -> TaskIntakeDecision:
        explicit_intent = str(request.intent or "").strip()
        if explicit_intent.startswith("domain:"):
            domain_id = explicit_intent.split(":", 1)[1].strip()
            if domain_id:
                self.registry.get(domain_id)
                return TaskIntakeDecision(
                    status="accepted",
                    intent=explicit_intent,
                    domain_id=domain_id,
                    trace={"source": "explicit_domain"},
                )

        candidates: list[TaskIntakeDecision] = []
        clarifications: list[TaskIntakeDecision] = []
        for manifest in self.registry.list():
            supported = set(manifest.planner_policies.get("supported_intents") or [])
            if explicit_intent and explicit_intent not in supported:
                continue
            implementation = str(manifest.planner_policies.get("task_intake") or "").strip()
            if not implementation:
                continue
            resolver = self.registry.resolve_callable(
                domain_id=manifest.domain_id,
                implementation=implementation,
            )
            payload = request.model_dump(mode="json")
            if runtime_context:
                payload["_runtime_context"] = dict(runtime_context)
            raw = resolver(payload)
            decision = TaskIntakeDecision.model_validate(raw)
            if decision.domain_id not in {None, manifest.domain_id}:
                raise ValueError(
                    f"Task Intake decision escapes its DomainPack: {manifest.domain_id}"
                )
            decision = decision.model_copy(update={"domain_id": manifest.domain_id})
            if decision.intent and decision.intent not in supported:
                raise ValueError(
                    f"Task Intake decision returns an unsupported intent: {manifest.domain_id}:{decision.intent}"
                )
            if decision.status == "accepted":
                candidates.append(decision)
            elif decision.status == "needs_clarification":
                clarifications.append(decision)

        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            return TaskIntakeDecision(
                status="needs_clarification",
                clarification=ClarificationPayload(
                    question="More than one installed professional pack can handle this request. Please specify the domain.",
                    missing_fields=["domain_id"],
                ),
                trace={"source": "ambiguous_domain_pack"},
            )
        if clarifications:
            return clarifications[0]
        return TaskIntakeDecision(
            status="needs_clarification",
            clarification=ClarificationPayload(
                question="Please specify a professional domain or provide a request that an installed DomainPack can handle.",
                missing_fields=["domain_id", "intent"],
            ),
            trace={"source": "no_domain_pack_match", "explicit_intent": explicit_intent or None},
        )


__all__ = ["TaskIntakeService"]
