from __future__ import annotations

import re
import json
from typing import Any

from research_system.runtime.domain_packs.registry import DomainPackRegistry
from research_system.runtime.intake.pack_intake import PackIntakeService
from research_system.schemas.core_runtime import (
    IntakeClassificationProposal,
    RequestIntakeDecision,
)
from research_system.schemas.protocol import RequestEnvelope


_STOCK_CODE = re.compile(r"\b\d{6}\b")


class RequestIntakeService:
    """Domain-neutral request intake that never chooses a fixed execution graph."""

    def __init__(self, registry: DomainPackRegistry, llm_provider: Any | None = None) -> None:
        self.registry = registry
        self.pack_intake = PackIntakeService(registry)
        self.llm_provider = llm_provider

    def resolve(
        self,
        request: RequestEnvelope,
        *,
        runtime_context: dict[str, Any] | None = None,
    ) -> RequestIntakeDecision:
        query = str(request.user_query or "").strip()
        explicit_intent = str(request.intent or "").strip()
        if explicit_intent == "core.conversation.answer":
            return RequestIntakeDecision(
                status="accepted",
                request_kind="conversation",
                recommended_mode="core",
                intent=explicit_intent,
                    candidate_capabilities=[explicit_intent],
                    constraints={},
                    confidence=1.0,
                trace={"source": "explicit_core_conversation"},
            )
        if explicit_intent.startswith("core."):
            return RequestIntakeDecision(
                status="accepted",
                request_kind="task",
                recommended_mode="task",
                intent=explicit_intent,
                    candidate_capabilities=[explicit_intent],
                    constraints={},
                    confidence=1.0,
                trace={"source": "explicit_core_task"},
            )
        # Main Agent owns the first decision. A normal conversation must not be
        # interpreted as a Pack task merely because the thread contains a
        # previous research run.
        if self._looks_like_basic_conversation(query):
            return RequestIntakeDecision(
                status="accepted",
                request_kind="conversation",
                recommended_mode="core",
                intent="core.conversation.answer",
                candidate_capabilities=["core.conversation.answer"],
                constraints={},
                confidence=0.95,
                trace={"source": "core_conversation_first"},
            )
        if not explicit_intent:
            model_decision = self._resolve_model_candidate(
                request,
                runtime_context=runtime_context,
            )
            if model_decision is not None:
                return model_decision
        if explicit_intent.startswith("domain:") or explicit_intent in self._supported_intents():
            decision = self.pack_intake.resolve(request, runtime_context=runtime_context)
            if decision.status == "accepted":
                return RequestIntakeDecision(
                    status="accepted",
                    request_kind="domain_task",
                    recommended_mode="graph",
                    intent=decision.intent,
                    domain_id=decision.domain_id,
                    candidate_domains=[decision.domain_id] if decision.domain_id else [],
                    candidate_capabilities=self._capabilities(
                        decision.domain_id,
                        intent=decision.intent,
                        decision_capabilities=decision.candidate_capabilities,
                    ),
                    slots=decision.slots,
                    constraints=decision.constraints,
                    confidence=1.0,
                    trace={"source": "explicit_or_pack_intake", **dict(decision.trace)},
                )
            if decision.status == "needs_clarification":
                return RequestIntakeDecision(
                    status="needs_clarification",
                    request_kind="domain_task",
                    recommended_mode="graph",
                    intent=decision.intent,
                    domain_id=decision.domain_id,
                    slots=decision.slots,
                    missing_fields=(decision.clarification.missing_fields if decision.clarification else []),
                    clarification_question=(decision.clarification.question if decision.clarification else None),
                    clarification_context=(
                        decision.clarification.context if decision.clarification else {}
                    ),
                    constraints=decision.constraints,
                    trace={"source": "pack_intake", **dict(decision.trace)},
                )

        pack_decision = self.pack_intake.resolve(
            request,
            runtime_context=runtime_context,
        )
        if pack_decision.status == "accepted":
            decision = pack_decision
            return RequestIntakeDecision(
                status="accepted",
                request_kind="domain_task",
                recommended_mode="graph",
                intent=decision.intent,
                domain_id=decision.domain_id,
                candidate_domains=[decision.domain_id] if decision.domain_id else [],
                candidate_capabilities=self._capabilities(
                    decision.domain_id,
                    intent=decision.intent,
                    decision_capabilities=decision.candidate_capabilities,
                ),
                slots=decision.slots,
                constraints=decision.constraints,
                confidence=0.9,
                trace={"source": "pack_intake", **dict(decision.trace)},
            )
        if pack_decision.intent or pack_decision.domain_id:
            return RequestIntakeDecision(
                status="needs_clarification",
                request_kind="domain_task",
                recommended_mode="graph",
                intent=pack_decision.intent,
                domain_id=pack_decision.domain_id,
                candidate_domains=[pack_decision.domain_id] if pack_decision.domain_id else [],
                slots=pack_decision.slots,
                missing_fields=(pack_decision.clarification.missing_fields if pack_decision.clarification else []),
                clarification_question=(pack_decision.clarification.question if pack_decision.clarification else None),
                clarification_context=(
                    pack_decision.clarification.context if pack_decision.clarification else {}
                ),
                constraints=pack_decision.constraints,
                trace={"source": "pack_intake", **dict(pack_decision.trace)},
            )

        if self._looks_like_core_task(query, request):
            return RequestIntakeDecision(
                status="accepted",
                request_kind="task",
                recommended_mode="task",
                intent="core.task.execute",
                candidate_capabilities=self._core_task_capabilities(query),
                constraints={},
                confidence=0.8,
                trace={"source": "core_task_fallback"},
            )
        return RequestIntakeDecision(
            status="accepted",
            request_kind="conversation",
            recommended_mode="core",
            intent="core.conversation.answer",
            candidate_capabilities=["core.conversation.answer"],
            constraints={},
            confidence=0.35,
            trace={"source": "core_fallback"},
        )

    def _pack_candidates(self, request: RequestEnvelope, *, runtime_context: dict[str, Any] | None) -> list[Any]:
        candidates: list[Any] = []
        for manifest in self.registry.list():
            supported = {
                *(item.intent_id for item in manifest.intake.intents),
                *(manifest.planner_policies.get("supported_intents") or []),
            }
            if not supported:
                continue
            decision = self.pack_intake.resolve(
                request.model_copy(update={"intent": None}),
                runtime_context=runtime_context,
            )
            if decision.status == "accepted" and decision.domain_id == manifest.domain_id:
                candidates.append(decision)
        unique: dict[str, Any] = {}
        for decision in candidates:
            if decision.domain_id:
                unique[decision.domain_id] = decision
        return list(unique.values())

    def _supported_intents(self) -> set[str]:
        return {
            str(intent)
            for manifest in self.registry.list()
            for intent in [
                *(item.intent_id for item in manifest.intake.intents),
                *(manifest.planner_policies.get("supported_intents") or []),
            ]
        }

    def _capabilities(
        self,
        domain_id: str | None,
        *,
        intent: str | None = None,
        decision_capabilities: list[str] | None = None,
    ) -> list[str]:
        if not domain_id:
            return []
        if decision_capabilities:
            return list(dict.fromkeys(decision_capabilities))
        manifest = self.registry.get(domain_id)
        for definition in manifest.intake.intents:
            if definition.intent_id == intent and definition.candidate_capabilities:
                return list(dict.fromkeys(definition.candidate_capabilities))
        return [item.capability for item in self.registry.capabilities(domain_id=domain_id)]

    @staticmethod
    def _looks_like_basic_conversation(query: str) -> bool:
        lowered = query.lower()
        if any(
            marker in query
            for marker in (
                "\u521a\u521a\u5728\u7814\u7a76\u4ec0\u4e48",
                "\u521a\u624d\u5728\u7814\u7a76\u4ec0\u4e48",
                "\u4e4b\u524d\u5728\u7814\u7a76\u4ec0\u4e48",
                "\u521a\u521a\u5206\u6790\u4e86\u4ec0\u4e48",
                "\u521a\u624d\u5206\u6790\u4e86\u4ec0\u4e48",
            )
        ):
            return True
        markers = (
            "who are you",
            "what can you do",
            "how do i use",
            "what is agent os",
            "explain your architecture",
            "describe your architecture",
            "what is your architecture",
            "tell me about yourself",
            "introduce yourself",
            "what were we researching",
            "what did we just research",
            "what were we analyzing",
            "what did we just analyze",
            "hello",
            "hi",
            "help",
            "thanks",
            "thank you",
            "你是谁",
            "你能做什么",
            "怎么用",
            "你好",
            "谢谢",
            "什么是agent os",
            "什么是 agent os",
        )
        if any(marker in lowered or marker in query for marker in markers):
            return True
        if _STOCK_CODE.search(query):
            return False
        return query.endswith("?") or query.endswith("？") or query.startswith(("什么", "为什么", "如何", "请问"))

    @staticmethod
    def _looks_like_core_task(query: str, request: RequestEnvelope) -> bool:
        requested_mode = str((request.constraints or {}).get("execution_mode") or "").strip().lower()
        if requested_mode == "task":
            return True
        lowered = query.lower()
        markers = (
            "read file",
            "search files",
            "invoke skill",
            "调用 skill",
            "调用技能",
            "读取文件",
            "搜索文件",
            "查看运行",
            "运行状态",
            "查看 artifact",
            "查看记忆",
            "查询记忆",
        )
        return any(marker in lowered or marker in query for marker in markers)

    @staticmethod
    def _core_task_capabilities(query: str) -> list[str]:
        lowered = query.lower()
        if "read file" in lowered or "读取文件" in query:
            return ["core.file.read", "core.message.compose"]
        if "search files" in lowered or "搜索文件" in query:
            return ["core.file.search", "core.message.compose"]
        if "skill" in lowered or "技能" in query:
            return ["core.skill.invoke", "core.message.compose"]
        if "记忆" in query or "memory" in lowered:
            return ["core.memory.query", "core.message.compose"]
        if "artifact" in lowered or "artifact" in query.lower():
            return ["core.artifact.inspect", "core.message.compose"]
        return ["core.run.status", "core.message.compose"]

    def _resolve_model_candidate(
        self,
        request: RequestEnvelope,
        *,
        runtime_context: dict[str, Any] | None,
    ) -> RequestIntakeDecision | None:
        proposal = self._classify_with_model(request, runtime_context=runtime_context)
        if proposal is None:
            return None
        intent = str(proposal.intent or "").strip()
        domain_id = str(proposal.domain_id or "").strip()
        if intent.startswith("core."):
            return RequestIntakeDecision(
                status="accepted",
                request_kind="task" if intent != "core.conversation.answer" else "conversation",
                recommended_mode="task" if intent != "core.conversation.answer" else "core",
                intent=intent,
                candidate_capabilities=list(proposal.candidate_capabilities or [intent]),
                confidence=proposal.confidence,
                trace={"source": "model_classification"},
            )
        if not intent:
            return None
        matching_domains = [
            manifest.domain_id
            for manifest in self.registry.list()
            if intent in {
                *(item.intent_id for item in manifest.intake.intents),
                *(manifest.planner_policies.get("supported_intents") or []),
            }
        ]
        if domain_id:
            matching_domains = [item for item in matching_domains if item == domain_id]
        if len(matching_domains) != 1:
            return None
        resolved_request = request.model_copy(update={"intent": intent})
        decision = self.pack_intake.resolve(
            resolved_request,
            runtime_context=runtime_context,
        )
        trace = {"source": "model_classification", "model_proposal": proposal.model_dump(mode="json")}
        if decision.status == "accepted":
            return RequestIntakeDecision(
                status="accepted",
                request_kind="domain_task",
                recommended_mode="graph",
                intent=decision.intent,
                domain_id=matching_domains[0],
                candidate_domains=[matching_domains[0]],
                candidate_capabilities=list(
                    decision.candidate_capabilities or proposal.candidate_capabilities
                ),
                slots=decision.slots,
                constraints=decision.constraints,
                confidence=proposal.confidence,
                trace={**trace, "pack_intake_trace": dict(decision.trace)},
            )
        if decision.status == "needs_clarification":
            return RequestIntakeDecision(
                status="needs_clarification",
                request_kind="domain_task",
                recommended_mode="graph",
                intent=decision.intent or intent,
                domain_id=matching_domains[0],
                candidate_domains=[matching_domains[0]],
                slots=decision.slots,
                constraints=decision.constraints,
                missing_fields=(
                    decision.clarification.missing_fields
                    if decision.clarification
                    else list(proposal.missing_fields)
                ),
                clarification_question=(
                    decision.clarification.question
                    if decision.clarification
                    else proposal.clarification_question
                ),
                clarification_context=(
                    decision.clarification.context
                    if decision.clarification
                    else dict(proposal.clarification_context)
                ),
                confidence=proposal.confidence,
                trace={**trace, "pack_intake_trace": dict(decision.trace)},
            )
        return None

    def _classify_with_model(
        self,
        request: RequestEnvelope,
        *,
        runtime_context: dict[str, Any] | None,
    ) -> IntakeClassificationProposal | None:
        provider = self.llm_provider
        if provider is None:
            return None
        try:
            if not bool(getattr(provider, "available", False)):
                return None
            catalog = [
                {
                    "domain_id": manifest.domain_id,
                    "intents": [
                        {
                            "intent_id": intent.intent_id,
                            "description": intent.description,
                            "examples": intent.examples,
                        }
                        for intent in manifest.intake.intents
                    ],
                }
                for manifest in self.registry.list()
            ]
            proposal = provider.parse(
                system_prompt=(
                    "You are the Agent OS request classifier. Return only a JSON object "
                    "matching the supplied schema. Choose a core intent only for general "
                    "conversation or core file/memory/task operations. For professional "
                    "work, choose one installed DomainPack intent. Never invent domain "
                    "facts or fill required professional slots from guesses."
                ),
                user_prompt=json.dumps(
                    {
                        "user_query": request.user_query,
                        "conversation_context": request.conversation_context[-8:],
                        "available_packs": catalog,
                        "runtime_context": runtime_context or {},
                    },
                    ensure_ascii=False,
                ),
                schema=IntakeClassificationProposal,
                temperature=0.0,
            )
            return proposal if proposal.confidence > 0.0 and proposal.intent else None
        except Exception:
            return None


__all__ = ["RequestIntakeService"]
