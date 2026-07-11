from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
from threading import RLock
from typing import Any

from research_system.runtime.domain_packs.registry import DomainPackRegistry
from research_system.runtime.domain_packs.skill_runtime import PackSkillRuntime
from research_system.runtime.core.main_agent import MainAgent, MainAgentPlanRole
from research_system.runtime.core.tool_registry import (
    build_core_tool_registry,
    register_domain_pack_tools,
    register_mcp_tools,
)
from research_system.runtime.execution.core.graph_runner import ExecutionGraphRunner
from research_system.runtime.intake.task_intake import TaskIntakeService
from research_system.runtime.intake.request_intake import RequestIntakeService
from research_system.runtime.planning.capability_planner import (
    CapabilityDynamicPlanner,
    CapabilityPlanner,
)
from research_system.runtime.platform.settings import Settings
from research_system.runtime.providers.provider_registry import ProviderRegistry
from research_system.runtime.storage.jsonl import read_json, write_json
from research_system.runtime.storage.jsonl import append_jsonl
from research_system.runtime.core.id_utils import generate_ulid
from research_system.runtime.storage.runs.run_store import RunStore
from research_system.schemas.artifacts import ArtifactDraft, ArtifactProducer, ArtifactRecord
from research_system.schemas.core_runtime import (
    MainAgentRunResult,
    MainAgentState,
    RequestIntakeDecision,
)
from research_system.schemas.events import EventEnvelope
from research_system.schemas.domain_pack import CapabilityPlanningRequest
from research_system.schemas.execution_graph import ExecutionGraphPatch
from research_system.schemas.execution_runtime import ExecutionRunResult, ExecutionRunSnapshot
from research_system.schemas.protocol import ProgressState, RequestEnvelope, ResponseEnvelope
from research_system.services.memory.layers import LayeredMemoryStore


class AgentOS:
    """Public domain-neutral API for capability planning and graph execution."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.registry = DomainPackRegistry.load(
            settings.domain_packs_root,
            require_integrity=settings.domain_pack_require_integrity,
            trusted_digests=settings.domain_pack_trusted_digests,
        )
        self.planner = CapabilityPlanner(self.registry)
        self.provider = ProviderRegistry(settings).default()
        self.task_intake = TaskIntakeService(self.registry)
        self.request_intake = RequestIntakeService(self.registry, llm_provider=self.provider)
        self.run_store = RunStore(settings.runs_root)
        memory_retrieval_backend = self._memory_retrieval_backend()
        self.memory = LayeredMemoryStore(
            settings.memory_root / "agent_os",
            retrieval_backend=memory_retrieval_backend,
        )
        self.tool_registry = register_mcp_tools(
            register_domain_pack_tools(
                build_core_tool_registry(settings=settings),
                self.registry,
            ),
            settings=settings,
        )
        self.main_agent = MainAgent(
            settings=settings,
            run_store=self.run_store,
            memory_store=self.memory,
            llm_provider=self.provider,
            tool_registry=self.tool_registry,
        )
        self.dynamic_planner = CapabilityDynamicPlanner(self.planner)
        self.plan_agent = MainAgentPlanRole(self.dynamic_planner)
        self._active: dict[str, ExecutionGraphRunner] = {}
        self._prepared_run_ids: set[str] = set()
        self._lock = RLock()

    def list_domain_packs(self) -> list[dict[str, Any]]:
        return [
            {
                "domain_id": manifest.domain_id,
                "version": manifest.version,
                "payload_sha256": self.registry.pack_digest(manifest.domain_id),
                "integrity": manifest.integrity.model_dump(mode="json"),
                "dependencies": [
                    dependency.model_dump(mode="json")
                    for dependency in manifest.dependencies
                ],
                "description": manifest.description,
                "capabilities": [
                    {
                        "subagent_type": capability.subagent_type,
                        "capability": capability.capability,
                        "consumes": [port.artifact_type for port in capability.consumes],
                        "produces": [port.artifact_type for port in capability.produces],
                        "tools": list(capability.tools),
                        "permissions": list(capability.permissions),
                        "parallel_safe": capability.parallel_safe,
                        "failure_mode": capability.failure_mode,
                    }
                    for capability in manifest.capabilities
                ],
                "tools": [
                    tool.model_dump(mode="json")
                    for tool in self.registry.tool_definitions(domain_id=manifest.domain_id)
                ],
                "skills": [
                    skill.model_dump(mode="json")
                    for skill in self.registry.skill_definitions(domain_id=manifest.domain_id)
                ],
                "required_gate_ids": [
                    gate.gate_id for gate in manifest.quality_gates if gate.blocking
                ],
                "pack_path": str(self.registry.pack_path(manifest.domain_id)),
            }
            for manifest in self.registry.list()
        ]

    def reload_domain_packs(self) -> list[dict[str, Any]]:
        """Refresh the Pack catalog at a safe point between Runs."""

        with self._lock:
            self._assert_pack_reload_safe()
            self._configure_registry(self.registry.reload())
            return self.list_domain_packs()

    def install_domain_pack(self, source: Path, *, replace: bool = False) -> dict[str, Any]:
        """Install and activate one local Pack without changing Core Runtime code."""

        with self._lock:
            self._assert_pack_reload_safe()
            manifest = self.registry.install(source, replace=replace)
            self._configure_registry(self.registry)
            return next(
                row for row in self.list_domain_packs()
                if row["domain_id"] == manifest.domain_id
            )

    def uninstall_domain_pack(self, domain_id: str) -> dict[str, Any]:
        """Uninstall a Pack only when no Run can still execute it."""

        with self._lock:
            self._assert_pack_reload_safe(domain_id=domain_id)
            manifest = self.registry.uninstall(domain_id)
            self._configure_registry(self.registry)
            return {
                "domain_id": manifest.domain_id,
                "version": manifest.version,
                "status": "uninstalled",
            }

    def _configure_registry(self, registry: DomainPackRegistry) -> None:
        """Rebind all capability-facing services to one registry snapshot."""

        self.registry = registry
        self.planner = CapabilityPlanner(self.registry)
        self.task_intake = TaskIntakeService(self.registry)
        self.request_intake = RequestIntakeService(self.registry, llm_provider=self.provider)
        self.tool_registry = register_mcp_tools(
            register_domain_pack_tools(
                build_core_tool_registry(settings=self.settings),
                self.registry,
            ),
            settings=self.settings,
        )
        self.main_agent.tools = self.tool_registry
        self.dynamic_planner = CapabilityDynamicPlanner(self.planner)
        self.plan_agent = MainAgentPlanRole(self.dynamic_planner)

    def _assert_pack_reload_safe(self, *, domain_id: str | None = None) -> None:
        if self._active:
            raise RuntimeError("DomainPack lifecycle changes require no active Agent OS Runs")
        if self._prepared_run_ids:
            raise RuntimeError("DomainPack lifecycle changes require no prepared Agent OS Runs")
        runs_root = self.settings.runs_root
        if not runs_root.exists():
            return
        terminal = {"completed", "gated", "failed", "cancelled", "timed_out"}
        for run_root in runs_root.iterdir():
            if not run_root.is_dir():
                continue
            snapshot = read_json(run_root / "execution_snapshot.json")
            if not isinstance(snapshot, dict) or snapshot.get("status") in terminal:
                continue
            graph = snapshot.get("graph") or {}
            if domain_id is None or graph.get("domain_id") == domain_id:
                raise RuntimeError(
                    "DomainPack lifecycle changes require no non-terminal Run "
                    f"for {graph.get('domain_id') or 'the requested Pack'}"
                )

    def _memory_retrieval_backend(self):
        if not (self.settings.memory_vector_search_enabled or self.settings.qdrant_url):
            return None
        try:
            from research_system.adapters.storage.qdrant_adapter import QdrantAdapter

            return QdrantAdapter(
                self.settings,
                embedding_provider=self.provider,
            )
        except Exception:
            # JSONL remains the source of truth and LayeredMemoryStore falls
            # back to lexical retrieval when the optional index is unavailable.
            return None

    def supported_intents(self) -> list[str]:
        """Return Pack-owned intent identifiers for transport validation."""
        return sorted(
            {
                str(intent).strip()
                for manifest in self.registry.list()
                for intent in [
                    *(item.intent_id for item in manifest.intake.intents),
                    *(manifest.planner_policies.get("supported_intents") or []),
                ]
                if str(intent).strip()
            }
        )

    def run_domain(
        self,
        *,
        domain_id: str,
        objective: str,
        input_artifacts: list[ArtifactDraft],
        required_artifact_types: list[str] | None = None,
        preferred_capabilities: list[str] | None = None,
        run_id: str | None = None,
        user_id: str | None = None,
        tenant_id: str | None = None,
        event_callback=None,
    ) -> ExecutionRunResult:
        manifest = self.registry.get(domain_id)
        required = list(required_artifact_types or [])
        if not required:
            required = [contract.artifact_type for contract in manifest.report_contracts]
        request = RequestEnvelope(
            intent=f"domain:{domain_id}",
            user_query=objective,
            user_id=user_id,
            tenant_id=tenant_id,
            slots={
                "domain_id": domain_id,
                "required_artifact_types": required,
            },
        )
        effective_run_id, paths, _ = self.run_store.create_run(
            request,
            request.intent or f"domain:{domain_id}",
            run_id=run_id,
            reuse_existing=run_id in self._prepared_run_ids,
        )
        self._prepared_run_ids.discard(effective_run_id)
        graph = self.planner.build_lazy_graph(
            CapabilityPlanningRequest(
                domain_id=domain_id,
                objective=objective,
                available_artifact_types=[artifact.artifact_type for artifact in input_artifacts],
                required_artifact_types=required,
                allowed_tools=list(manifest.governance.allowed_tools),
                allowed_permissions=list(manifest.governance.allowed_permissions),
                preferred_capabilities=list(preferred_capabilities or []),
                token_budget=manifest.governance.token_budget,
                cost_budget=manifest.governance.cost_budget,
                metadata={
                    "request_id": request.request_id,
                    "intent": request.intent or f"domain:{domain_id}",
                },
            )
        )
        if graph.governance.timeout_seconds is None:
            graph.governance.timeout_seconds = float(self.settings.run_timeout_seconds)
        thread_id = str(request.thread_id or request.slots.get("chat_thread_id") or graph.graph_id)
        self._write_graph_agent_state(
            paths.root,
            run_id=effective_run_id,
            request=request,
            thread_id=thread_id,
            graph=graph,
            status="running",
        )
        write_json(
            paths.root / "agent_os_request.json",
            {
                "run_id": effective_run_id,
                "thread_id": thread_id,
                "request_id": request.request_id,
                "domain_id": domain_id,
                "objective": objective,
                "required_artifact_types": required,
            },
        )
        runner = self._runner(effective_run_id, event_callback=event_callback)
        with self._lock:
            self._active[effective_run_id] = runner
        try:
            result = runner.run(
                graph=graph,
                run_id=effective_run_id,
                thread_id=thread_id,
                request_id=request.request_id,
                user_id=request.user_id,
                tenant_id=request.tenant_id,
                seed_artifacts=input_artifacts,
            )
            result = self._ensure_graph_assistant_message(result)
            self._update_graph_agent_state(result)
            self._write_summary(result)
            return result
        finally:
            with self._lock:
                self._active.pop(effective_run_id, None)

    def run_request(
        self,
        request: RequestEnvelope,
        *,
        run_id: str | None = None,
        intake_decision: RequestIntakeDecision | None = None,
        event_callback=None,
    ) -> ExecutionRunResult:
        decision = intake_decision or self.request_intake_decision(request)
        if decision.status != "accepted":
            raise ValueError("Agent OS RequestIntake requires clarification before execution")
        request = decision.apply_to(request)
        intent = str(request.intent or "").strip()
        graph = self._build_initial_graph(request=request, decision=decision)
        effective_run_id, paths, _ = self.run_store.create_run(
            request,
            intent,
            run_id=run_id,
            reuse_existing=run_id in self._prepared_run_ids,
        )
        self._prepared_run_ids.discard(effective_run_id)
        thread_id = str(request.thread_id or request.slots.get("chat_thread_id") or graph.graph_id)
        self._write_graph_agent_state(
            paths.root,
            run_id=effective_run_id,
            request=request,
            thread_id=thread_id,
            graph=graph,
            status="running",
        )
        input_payload = dict(request.slots.get("user_request") or {})
        input_payload.setdefault("query", request.user_query)
        for key in ("symbols", "sector", "source_records", "snapshots", "source_refs"):
            if key in request.slots and key not in input_payload:
                input_payload[key] = request.slots[key]
        write_json(
            paths.root / "agent_os_request.json",
            {
                "run_id": effective_run_id,
                "thread_id": thread_id,
                "request": request.model_dump(mode="json"),
                "domain_id": graph.domain_id,
                "objective": graph.objective,
                "intent": intent,
                "required_artifact_types": list(
                    graph.metadata.get("required_artifact_types") or []
                ),
            },
        )
        runner = self._runner(effective_run_id, event_callback=event_callback)
        with self._lock:
            self._active[effective_run_id] = runner
        try:
            result = runner.run(
                graph=graph,
                run_id=effective_run_id,
                thread_id=thread_id,
                request_id=request.request_id,
                user_id=request.user_id,
                tenant_id=request.tenant_id,
                seed_artifacts=[
                    ArtifactDraft(
                        artifact_type="user_request",
                        content=input_payload,
                        source_refs=[{"type": "request", "id": request.request_id}],
                    )
                ],
            )
            result = self._ensure_graph_assistant_message(result)
            self._update_graph_agent_state(result)
            self._write_summary(result, intent=intent)
            return result
        finally:
            with self._lock:
                self._active.pop(effective_run_id, None)

    def handle_request(
        self,
        request: RequestEnvelope,
        *,
        run_id: str | None = None,
        event_callback=None,
    ) -> ResponseEnvelope:
        """Run one request through the single Main Agent entry protocol."""
        request = self._merge_pending_clarification(request, run_id=run_id)
        existing_run_id = self.run_store.find_run_by_idempotency_key(
            request.idempotency_key or "",
            tenant_id=request.tenant_id,
            user_id=request.user_id,
        )
        if existing_run_id:
            persisted_response = read_json(
                self.run_store.load_run_paths(existing_run_id).root / "response.json"
            )
            if isinstance(persisted_response, dict):
                return ResponseEnvelope.model_validate(persisted_response)
        reuse_existing = False
        if run_id:
            existing_paths = self.run_store.load_run_paths(run_id)
            reuse_existing = (
                run_id in self._prepared_run_ids
                or isinstance(read_json(existing_paths.root / "agent_os_pending.json"), dict)
                or isinstance(read_json(existing_paths.root / "clarification_pending.json"), dict)
            )
        thread_id = str(request.thread_id or f"thread_{run_id or generate_ulid()}")
        routed_request = request.model_copy(update={"thread_id": thread_id})
        effective_run_id, _, _ = self.run_store.create_run(
            routed_request,
            request.intent or "agent_os.pending",
            run_id=run_id,
            reuse_existing=reuse_existing,
        )
        with self._lock:
            self._prepared_run_ids.add(effective_run_id)
        decision = self.main_agent.route(
            request=routed_request,
            run_id=effective_run_id,
            thread_id=thread_id,
            intake_resolver=self.request_intake.resolve,
            runtime_context={"recent_runs": self.run_store.list_runs()},
            event_callback=event_callback,
        )
        if decision.status == "needs_clarification":
            if run_id and self._is_pending_clarification(run_id):
                response = self._update_clarification_run(
                    run_id=run_id,
                    request=routed_request,
                    decision=decision,
                    event_callback=event_callback,
                )
                with self._lock:
                    self._prepared_run_ids.discard(effective_run_id)
                return response
            response = self._create_clarification_run(
                request=routed_request,
                decision=decision,
                run_id=effective_run_id,
                event_callback=event_callback,
            )
            with self._lock:
                self._prepared_run_ids.discard(effective_run_id)
            return response

        resolved_request = decision.apply_to(routed_request)
        if run_id and self._is_pending_clarification(run_id):
            self._complete_clarification_run(
                run_id=run_id,
                request=resolved_request,
                event_callback=event_callback,
            )
            with self._lock:
                self._prepared_run_ids.add(effective_run_id)
        if decision.request_kind == "domain_task":
            result = self.run_request(
                resolved_request,
                run_id=effective_run_id,
                intake_decision=decision,
                event_callback=event_callback,
            )
            response = self._response_from_graph(resolved_request, result)
            self._persist_response(response)
            return response

        effective_run_id, paths, _ = self.run_store.create_run(
            resolved_request,
            decision.intent or "core.conversation.answer",
            run_id=effective_run_id,
            reuse_existing=True,
        )
        self._prepared_run_ids.discard(effective_run_id)
        thread_id = str(
            resolved_request.thread_id
            or resolved_request.slots.get("chat_thread_id")
            or f"thread_{effective_run_id}"
        )
        resolved_request = resolved_request.model_copy(
            update={
                "slots": {
                    **dict(resolved_request.slots),
                    "run_id": effective_run_id,
                    "thread_id": thread_id,
                }
            }
        )
        write_json(
            paths.root / "agent_os_request.json",
            {
                "run_id": effective_run_id,
                "thread_id": thread_id,
                "request": resolved_request.model_dump(mode="json"),
                "mode": "core",
                "request_intake": decision.model_dump(mode="json"),
            },
        )
        result = self.main_agent.run(
            request=resolved_request,
            decision=decision,
            run_id=effective_run_id,
            thread_id=thread_id,
            event_callback=event_callback,
            emit_started=False,
        )
        self._write_core_summary(result)
        response = self._response_from_core(resolved_request, result)
        self._persist_response(response)
        return response

    def _clarification_path(self, run_id: str) -> Path:
        return self.run_store.load_run_paths(run_id).root / "clarification_pending.json"

    def _is_pending_clarification(self, run_id: str) -> bool:
        return isinstance(read_json(self._clarification_path(run_id)), dict)

    def _merge_pending_clarification(
        self,
        request: RequestEnvelope,
        *,
        run_id: str | None,
    ) -> RequestEnvelope:
        if not run_id:
            return request
        pending = read_json(self._clarification_path(run_id))
        if not isinstance(pending, dict):
            return request
        paths = self.run_store.load_run_paths(run_id)
        original_payload = read_json(paths.request)
        if not isinstance(original_payload, dict):
            raise ValueError(f"clarification run request is missing: {run_id}")
        original = RequestEnvelope.model_validate(original_payload)
        expected_thread = str(
            original.thread_id
            or pending.get("thread_id")
            or ""
        ).strip()
        supplied_thread = str(request.thread_id or expected_thread).strip()
        if expected_thread and supplied_thread != expected_thread:
            raise ValueError(f"clarification response belongs to a different thread: {run_id}")
        answer = str(request.user_query or "").strip()
        if not answer:
            raise ValueError("clarification response must not be empty")
        clarification_context = {
            "type": "clarification_response",
            "run_id": run_id,
            "question": str(pending.get("question") or ""),
            "missing_fields": list(pending.get("missing_fields") or []),
            "answer": answer,
        }
        return original.model_copy(
            update={
                "thread_id": supplied_thread or None,
                "intent": request.intent or original.intent,
                "user_query": f"{original.user_query}\n\nClarification response: {answer}",
                "conversation_context": [
                    *list(original.conversation_context),
                    *list(request.conversation_context),
                    clarification_context,
                ],
                "slots": {**dict(original.slots), **dict(request.slots)},
                "constraints": {**dict(original.constraints), **dict(request.constraints)},
                "clarification_status": "resolved",
            }
        )

    def _create_clarification_run(
        self,
        *,
        request: RequestEnvelope,
        decision: RequestIntakeDecision,
        run_id: str | None = None,
        event_callback=None,
    ) -> ResponseEnvelope:
        effective_requested_run_id = run_id or generate_ulid()
        thread_id = str(request.thread_id or f"thread_{effective_requested_run_id}")
        persisted_request = request.model_copy(update={"thread_id": thread_id})
        if run_id:
            effective_run_id = run_id
            paths = self.run_store.load_run_paths(run_id)
        else:
            effective_run_id, paths, _ = self.run_store.create_run(
                persisted_request,
                decision.intent or request.intent or "agent_os.clarification",
                run_id=effective_requested_run_id,
            )
        self._write_clarification_state(
            run_id=effective_run_id,
            request=persisted_request,
            decision=decision,
            status="needs_clarification",
        )
        self._emit_clarification_event(
            paths.root,
            event_callback,
            run_id=effective_run_id,
            request=persisted_request,
            decision=decision,
        )
        return self._clarification_response(
            run_id=effective_run_id,
            request=persisted_request,
            decision=decision,
        )

    def _update_clarification_run(
        self,
        *,
        run_id: str,
        request: RequestEnvelope,
        decision: RequestIntakeDecision,
        event_callback=None,
    ) -> ResponseEnvelope:
        paths = self.run_store.load_run_paths(run_id)
        self._write_clarification_state(
            run_id=run_id,
            request=request,
            decision=decision,
            status="needs_clarification",
        )
        self._emit_clarification_event(
            paths.root,
            event_callback,
            run_id=run_id,
            request=request,
            decision=decision,
        )
        return self._clarification_response(run_id=run_id, request=request, decision=decision)

    def _complete_clarification_run(
        self,
        *,
        run_id: str,
        request: RequestEnvelope,
        event_callback=None,
    ) -> None:
        paths = self.run_store.load_run_paths(run_id)
        pending = read_json(self._clarification_path(run_id)) or {}
        event = {
            "event_type": "clarification_resolved",
            "run_id": run_id,
            "thread_id": str(request.thread_id or f"run:{run_id}"),
            "request_id": request.request_id,
            "actor": "main_agent",
            "question": pending.get("question"),
            "missing_fields": list(pending.get("missing_fields") or []),
        }
        event = EventEnvelope.model_validate(event).model_dump(mode="json")
        append_jsonl(paths.root / "execution_events.jsonl", event)
        if event_callback is not None:
            event_callback(event)
        self._clarification_path(run_id).unlink(missing_ok=True)

    def _write_clarification_state(
        self,
        *,
        run_id: str,
        request: RequestEnvelope,
        decision: RequestIntakeDecision,
        status: str,
    ) -> None:
        paths = self.run_store.load_run_paths(run_id)
        question = decision.clarification_question or "请补充请求所需的信息后继续。"
        missing_fields = list(decision.missing_fields)
        write_json(
            paths.root / "clarification_pending.json",
            {
                "run_id": run_id,
                "thread_id": request.thread_id,
                "request_id": request.request_id,
                "question": question,
                "missing_fields": missing_fields,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        write_json(
            paths.root / "agent_os_request.json",
            {
                "run_id": run_id,
                "thread_id": request.thread_id,
                "request": request.model_dump(mode="json"),
                "mode": decision.recommended_mode,
                "request_intake": decision.model_dump(mode="json"),
                "clarification_pending": True,
            },
        )
        state = MainAgentState(
            run_id=run_id,
            thread_id=str(request.thread_id or ""),
            request_id=request.request_id,
            status="paused",
            mode=decision.recommended_mode,
            intent=decision.intent or request.intent,
            request_kind=decision.request_kind,
            clarification_question=question,
            missing_fields=missing_fields,
            assistant_message=question,
        )
        write_json(paths.root / "main_agent_state.json", state.model_dump(mode="json"))
        run_state = read_json(paths.root / "run_state.json") or {}
        run_state.update(
            {
                "run_id": run_id,
                "request_id": request.request_id,
                "intent": decision.intent or request.intent or "agent_os.clarification",
                "status": status,
                "thread_id": request.thread_id,
            }
        )
        write_json(paths.root / "run_state.json", run_state)
        write_json(
            paths.root / "summary.json",
            {
                "run_id": run_id,
                "request_id": request.request_id,
                "intent": decision.intent or request.intent,
                "status": "needs_clarification",
                "mode": decision.recommended_mode,
                "message": question,
                "clarification": {
                    "question": question,
                    "missing_fields": missing_fields,
                },
            },
        )

    @staticmethod
    def _emit_clarification_event(root: Path, callback, *, run_id: str, request: RequestEnvelope, decision: RequestIntakeDecision) -> None:
        event = {
            "event_type": "clarification_requested",
            "run_id": run_id,
            "thread_id": str(request.thread_id or f"run:{run_id}"),
            "request_id": request.request_id,
            "actor": "main_agent",
            "question": decision.clarification_question or "请补充请求所需的信息后继续。",
            "missing_fields": list(decision.missing_fields),
        }
        event = EventEnvelope.model_validate(event).model_dump(mode="json")
        append_jsonl(root / "execution_events.jsonl", event)
        if callback is not None:
            callback(event)

    def _clarification_response(
        self,
        *,
        run_id: str,
        request: RequestEnvelope,
        decision: RequestIntakeDecision,
    ) -> ResponseEnvelope:
        question = decision.clarification_question or "请补充请求所需的信息后继续。"
        missing_fields = list(decision.missing_fields)
        response = ResponseEnvelope(
            request_id=request.request_id,
            run_id=run_id,
            mode=decision.recommended_mode,
            intent=decision.intent or request.intent,
            status="needs_clarification",
            message=question,
            clarification={"question": question, "missing_fields": missing_fields},
            progress=ProgressState(
                current_phase="request_intake",
                active_agent="main_agent",
                latest_event="clarification_requested",
                status_message="RequestIntake requires more information.",
                percent=0,
            ),
            result={
                "protocol": "agent_os",
                "request_intake": decision.model_dump(mode="json"),
                "clarification_pending": True,
            },
        )
        self._persist_response(response)
        return response

    def prepare_run(
        self,
        request: RequestEnvelope,
        *,
        run_id: str,
        intent: str | None = None,
    ) -> str:
        """Reserve a canonical Run directory for asynchronous execution."""
        effective_run_id, _, _ = self.run_store.create_run(
            request,
            intent or request.intent or "agent_os.pending",
            run_id=run_id,
        )
        with self._lock:
            self._prepared_run_ids.add(effective_run_id)
        root = self.run_store.load_run_paths(effective_run_id).root
        write_json(
            root / "agent_os_pending.json",
            {
                "run_id": effective_run_id,
                "request_id": request.request_id,
                "intent": intent or request.intent,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        event = EventEnvelope(
            event_type="run_accepted",
            run_id=effective_run_id,
            thread_id=str(request.thread_id or f"run:{effective_run_id}"),
            request_id=request.request_id,
            actor="agent_os",
            message="Agent OS run accepted for background execution.",
        ).model_dump(mode="json")
        append_jsonl(root / "execution_events.jsonl", event)
        return effective_run_id

    def adopt_prepared_run(self, run_id: str) -> None:
        """Re-register a persisted background reservation after process restart."""
        paths = self.run_store.load_run_paths(run_id)
        if read_json(paths.root / "agent_os_pending.json") is None:
            raise ValueError(f"run is not a persisted Agent OS background reservation: {run_id}")
        with self._lock:
            self._prepared_run_ids.add(run_id)

    def intake(self, request: RequestEnvelope):
        """Legacy Pack-only intake adapter; normal entry uses request_intake_decision."""
        return self.task_intake.resolve(
            request,
            runtime_context={"recent_runs": self.run_store.list_runs()},
        )

    def request_intake_decision(self, request: RequestEnvelope):
        return self.request_intake.resolve(
            request,
            runtime_context={"recent_runs": self.run_store.list_runs()},
        )

    def _build_initial_graph(
        self,
        *,
        request: RequestEnvelope,
        decision: RequestIntakeDecision,
    ):
        """Build a lazy graph from the accepted request and installed Pack contracts."""
        domain_id = str(decision.domain_id or request.slots.get("domain_id") or "").strip()
        if not domain_id:
            raise ValueError("accepted domain request has no DomainPack")
        manifest = self.registry.get(domain_id)
        required = list(request.slots.get("required_artifact_types") or [])
        if not required:
            required = [contract.artifact_type for contract in manifest.report_contracts]
        planning_request = CapabilityPlanningRequest(
            domain_id=domain_id,
            objective=request.user_query,
            available_artifact_types=list(
                request.slots.get("available_artifact_types") or ["user_request"]
            ),
            required_artifact_types=required,
            allowed_tools=list(manifest.governance.allowed_tools),
            allowed_permissions=list(manifest.governance.allowed_permissions),
            preferred_capabilities=list(request.slots.get("preferred_capabilities") or []),
            token_budget=manifest.governance.token_budget,
            cost_budget=manifest.governance.cost_budget,
            metadata={
                "entry_protocol": "agent_os",
                "intent": str(request.intent or decision.intent or ""),
                "request_id": request.request_id,
                "intent_inputs": {
                    "intent": request.intent,
                    **dict(request.slots),
                    **dict(request.constraints),
                },
                "request_constraints": dict(request.constraints),
                "chat_dispatch": (
                    dict(request.constraints.get("chat_dispatch") or {})
                    if isinstance(request.constraints.get("chat_dispatch"), dict)
                    else None
                ),
                "conversation_context": list(request.conversation_context),
            },
        )
        return self.plan_agent.build_initial_graph(planning_request)

    def inspect_run(self, run_id: str) -> dict[str, Any]:
        root = self.run_store.load_run_paths(run_id).root
        snapshot = read_json(root / "execution_snapshot.json")
        if snapshot is None:
            core_state = read_json(root / "main_agent_state.json")
            if core_state is not None:
                return {
                    "mode": "core",
                    "request": read_json(root / "agent_os_request.json") or read_json(root / "request.json"),
                    "snapshot": core_state,
                    "artifacts": [
                        artifact.model_dump(mode="json")
                        for artifact in self._runner_board(run_id).list_artifacts(thread_id=None)
                    ],
                }
            if read_json(root / "agent_os_pending.json") is not None:
                return {
                    "mode": "pending",
                    "request": read_json(root / "request.json"),
                    "snapshot": read_json(root / "run_state.json") or {
                        "run_id": run_id,
                        "status": "received",
                    },
                    "artifacts": [],
                }
            raise KeyError(f"run has no canonical execution snapshot: {run_id}")
        return {
            "mode": "graph",
            "request": read_json(root / "agent_os_request.json"),
            "snapshot": snapshot,
            "main_agent_state": read_json(root / "main_agent_state.json"),
            "artifacts": [
                artifact.model_dump(mode="json")
                for artifact in self._runner(run_id).board.list_artifacts(thread_id=None)
            ],
        }

    def assert_run_scope(
        self,
        run_id: str,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> None:
        """Enforce the same identity scope for the unversioned workspace API."""
        if user_id is None and tenant_id is None:
            return
        root = self.run_store.load_run_paths(run_id).root
        request = read_json(root / "request.json")
        if not isinstance(request, dict):
            if user_id == "api_token" and tenant_id == "api_token":
                return
            raise FileNotFoundError(f"run not found: {run_id}")
        if (
            request.get("user_id") in (None, "")
            and request.get("tenant_id") in (None, "")
            and user_id == "api_token"
            and tenant_id == "api_token"
        ):
            return
        if user_id is not None and request.get("user_id") != user_id:
            raise FileNotFoundError(f"run not found: {run_id}")
        if tenant_id is not None and request.get("tenant_id") != tenant_id:
            raise FileNotFoundError(f"run not found: {run_id}")

    def replay(self, run_id: str) -> ExecutionRunSnapshot:
        return self._runner(run_id).replay()

    def resume(self, run_id: str) -> ExecutionRunResult:
        detail = self.inspect_run(run_id)
        if detail.get("mode") == "core":
            state = MainAgentState.model_validate(detail["snapshot"])
            if state.status != "paused":
                raise ValueError(f"core run is not resumable: {state.status}")
            request, decision = self._load_core_request(run_id)
            result = self.main_agent.run(
                request=request,
                decision=decision,
                run_id=state.run_id,
                thread_id=state.thread_id,
                resume=True,
            )
            self._write_core_summary(result)
            return result  # type: ignore[return-value]
        runner = self._runner(run_id)
        with self._lock:
            self._active[run_id] = runner
        try:
            result = runner.resume()
            result = self._ensure_graph_assistant_message(result)
            self._update_graph_agent_state(result)
            self._write_summary(result)
            return result
        finally:
            with self._lock:
                self._active.pop(run_id, None)

    def rerun_from(self, run_id: str, node_id: str) -> ExecutionRunResult:
        detail = self.inspect_run(run_id)
        if detail.get("mode") == "core":
            raise ValueError("Core Agent Loop runs do not expose graph nodes for partial rerun")
        runner = self._runner(run_id)
        runner.rerun_from(node_id)
        with self._lock:
            self._active[run_id] = runner
        try:
            result = runner.resume()
            result = self._ensure_graph_assistant_message(result)
            self._update_graph_agent_state(result)
            self._write_summary(result)
            return result
        finally:
            with self._lock:
                self._active.pop(run_id, None)

    def pause(self, run_id: str) -> bool:
        detail = self.inspect_run(run_id)
        if detail.get("mode") == "pending":
            paths = self.run_store.load_run_paths(run_id)
            write_json(
                paths.root / "execution_control.json",
                {"pause_requested": True, "cancel_requested": False},
            )
            return True
        if detail.get("mode") == "core":
            state = MainAgentState.model_validate(detail["snapshot"])
            if state.status in {"completed", "failed", "cancelled", "paused"}:
                return False
            paths = self.run_store.load_run_paths(run_id)
            write_json(
                paths.root / "execution_control.json",
                {"pause_requested": True, "cancel_requested": False},
            )
            return True
        with self._lock:
            runner = self._active.get(run_id)
        if runner is None:
            runner = self._runner(run_id)
            snapshot = read_json(runner.snapshot_path)
            if not snapshot or snapshot.get("status") in {"completed", "failed", "cancelled", "timed_out", "gated", "waiting_approval"}:
                return False
        runner.request_pause()
        return True

    def cancel(self, run_id: str) -> bool:
        detail = self.inspect_run(run_id)
        if detail.get("mode") == "pending":
            paths = self.run_store.load_run_paths(run_id)
            write_json(
                paths.root / "execution_control.json",
                {"pause_requested": False, "cancel_requested": True},
            )
            return True
        if detail.get("mode") == "core":
            state = MainAgentState.model_validate(detail["snapshot"])
            if state.status in {"completed", "failed", "cancelled"}:
                return False
            paths = self.run_store.load_run_paths(run_id)
            write_json(
                paths.root / "execution_control.json",
                {"pause_requested": False, "cancel_requested": True},
            )
            return True
        with self._lock:
            runner = self._active.get(run_id)
        if runner is None:
            runner = self._runner(run_id)
            snapshot = read_json(runner.snapshot_path)
            if not snapshot or snapshot.get("status") in {"completed", "failed", "cancelled", "timed_out", "gated"}:
                return False
        runner.request_cancel()
        return True

    def patch(
        self,
        run_id: str,
        patch: ExecutionGraphPatch,
        *,
        actor: str | None = None,
    ) -> bool:
        if actor != "main_agent.plan_agent" or patch.actor != actor:
            raise PermissionError(
                "only the Main Agent PlanAgent role may submit an Agent OS graph patch"
            )
        with self._lock:
            runner = self._active.get(run_id)
        if runner is None:
            return False
        runner.submit_patch(patch)
        return True

    def _runner(self, run_id: str, *, event_callback=None) -> ExecutionGraphRunner:
        paths = self.run_store.load_run_paths(run_id)
        return ExecutionGraphRunner(
            registry=self.registry,
            run_root=paths.root,
            dynamic_planner=self.plan_agent,
            memory_store=self.memory,
            tool_registry=self.main_agent.tools,
            skill_runtime_factory=lambda domain_id, version: PackSkillRuntime(
                registry=self.registry,
                settings=self.settings,
                domain_id=domain_id,
                version=version,
            ),
            settings=self.settings,
            cache_root=self.settings.memory_root / "agent_os_cache",
            event_callback=event_callback,
            progress_observer=self.main_agent.observe_graph_progress,
        )

    def _runner_board(self, run_id: str):
        return self._runner(run_id).board

    def _load_core_request(self, run_id: str):
        root = self.run_store.load_run_paths(run_id).root
        payload = read_json(root / "agent_os_request.json")
        if not isinstance(payload, dict) or not isinstance(payload.get("request"), dict):
            raise ValueError(f"core run request is missing: {run_id}")
        decision_payload = payload.get("request_intake")
        if not isinstance(decision_payload, dict):
            raise ValueError(f"core run intake decision is missing: {run_id}")
        return (
            RequestEnvelope.model_validate(payload["request"]),
            RequestIntakeDecision.model_validate(decision_payload),
        )

    def _response_from_graph(
        self,
        request: RequestEnvelope,
        result: ExecutionRunResult,
    ) -> ResponseEnvelope:
        snapshot = result.snapshot
        status = (
            "needs_clarification"
            if snapshot.clarification_question
            else {"paused": "running"}.get(snapshot.status, snapshot.status)
        )
        artifacts = self.inspect_run(snapshot.run_id).get("artifacts") or []
        final_ids = set(result.final_artifact_ids)
        final_artifacts = [
            artifact
            for artifact in artifacts
            if artifact.get("artifact_id") in final_ids
            or artifact.get("artifact_type") == "assistant_message"
        ]
        message = next(
            (
                str(artifact.get("content", {}).get("message"))
                for artifact in final_artifacts
                if artifact.get("artifact_type") == "assistant_message"
                and isinstance(artifact.get("content"), dict)
                and artifact.get("content", {}).get("message")
            ),
            None,
        )
        if not message:
            message = next(
                (
                    str(artifact.get("content", {}).get("conclusion"))
                    for artifact in final_artifacts
                    if isinstance(artifact.get("content"), dict)
                    and artifact.get("content", {}).get("conclusion")
                ),
                f"Agent OS run {snapshot.status}",
            )
        if snapshot.clarification_question:
            message = snapshot.clarification_question
        return ResponseEnvelope(
            mode="graph",
            request_id=snapshot.request_id,
            run_id=snapshot.run_id,
            intent=str(snapshot.graph.metadata.get("intent") or request.intent or ""),
            status=status,
            message=message,
            clarification=(
                {
                    "question": snapshot.clarification_question,
                    "missing_fields": list(snapshot.missing_fields),
                }
                if snapshot.clarification_question
                else None
            ),
            progress=ProgressState(
                current_phase="execution_graph",
                active_agent="main_agent",
                latest_event=f"graph_run_{snapshot.status}",
                status_message=f"Agent OS run {snapshot.status}",
                percent=100 if status in {"completed", "failed", "cancelled", "timed_out", "gated"} else 0,
            ),
            result={
                "protocol": "agent_os",
                "mode": "graph",
                "graph_id": snapshot.graph.graph_id,
                "graph_revision": snapshot.graph.revision,
                "artifact_ids": list(result.artifact_ids),
                "final_artifact_ids": list(result.final_artifact_ids),
                "tool_call_count": snapshot.tool_call_count,
            },
            artifacts=final_artifacts,
        )

    def _response_from_core(
        self,
        request: RequestEnvelope,
        result: MainAgentRunResult,
    ) -> ResponseEnvelope:
        artifacts = self.inspect_run(result.run_id).get("artifacts") or []
        response_status = (
            "needs_clarification"
            if result.clarification_question
            else {"paused": "running"}.get(result.status, result.status)
        )
        return ResponseEnvelope(
            mode=result.mode,
            request_id=result.request_id,
            run_id=result.run_id,
            intent=result.intent,
            status=response_status,
            message=result.message,
            clarification=(
                {
                    "question": result.clarification_question,
                    "missing_fields": list(result.missing_fields),
                }
                if result.clarification_question
                else None
            ),
            progress=ProgressState(
                current_phase=f"{result.mode}_agent_loop",
                active_agent="main_agent",
                latest_event=f"main_agent_{result.status}",
                status_message=f"Main Agent {result.mode} loop {result.status}",
                percent=100 if result.status in {"completed", "failed", "cancelled"} else 0,
            ),
            result={
                "protocol": "agent_os",
                "mode": result.mode,
                "message": result.message,
                "artifact_ids": list(result.artifact_ids),
                "tool_calls": [item.model_dump(mode="json") for item in result.tool_calls],
                "clarification": (
                    {
                        "question": result.clarification_question,
                        "missing_fields": list(result.missing_fields),
                    }
                    if result.clarification_question
                    else None
                ),
            },
            artifacts=[artifact for artifact in artifacts if artifact.get("artifact_id") in set(result.artifact_ids)],
        )

    def _write_core_summary(self, result: MainAgentRunResult) -> None:
        root = self.run_store.load_run_paths(result.run_id).root
        run_state = read_json(root / "run_state.json") or {}
        run_state.update(
            {
                "run_id": result.run_id,
                "request_id": result.request_id,
                "intent": result.intent or "core.conversation.answer",
                "mode": result.mode,
                "status": result.status,
                "thread_id": result.thread_id,
            }
        )
        write_json(root / "run_state.json", run_state)
        write_json(
            root / "summary.json",
            {
                "run_id": result.run_id,
                "request_id": result.request_id,
                "intent": result.intent or "core.conversation.answer",
                "mode": result.mode,
                "status": result.status,
                "message": result.message,
                "artifact_ids": list(result.artifact_ids),
                "tool_call_ids": [item.call_id for item in result.tool_calls],
            },
        )

    def _persist_response(self, response: ResponseEnvelope) -> None:
        if not response.run_id:
            return
        root = self.run_store.load_run_paths(response.run_id).root
        write_json(root / "response.json", response.model_dump(mode="json"))

    def _write_graph_agent_state(
        self,
        root: Path,
        *,
        run_id: str,
        request: RequestEnvelope,
        thread_id: str,
        graph,
        status: str,
    ) -> None:
        existing = read_json(root / "main_agent_state.json")
        created_at = None
        if isinstance(existing, dict):
            created_at = existing.get("created_at")
        state = MainAgentState(
            run_id=run_id,
            thread_id=thread_id,
            request_id=request.request_id,
            status=status if status in {"running", "completed", "failed", "cancelled", "paused", "waiting_approval", "gated", "timed_out"} else "failed",
            mode="graph",
            intent=request.intent,
            request_kind="domain_task",
            graph_id=graph.graph_id,
            graph_revision=graph.revision,
            created_at=created_at or datetime.now(timezone.utc),
        )
        write_json(root / "main_agent_state.json", state.model_dump(mode="json"))

    def _update_graph_agent_state(self, result: ExecutionRunResult) -> None:
        root = self.run_store.load_run_paths(result.snapshot.run_id).root
        existing = read_json(root / "main_agent_state.json")
        if isinstance(existing, dict):
            state = MainAgentState.model_validate(existing).model_copy(
                update={
                    "status": result.snapshot.status,
                    "graph_id": result.snapshot.graph.graph_id,
                    "graph_revision": result.snapshot.graph.revision,
                    "artifact_ids": list(result.artifact_ids),
                    "updated_at": datetime.now(timezone.utc),
                }
            )
            self._persist_main_agent_state(root, state)

    @staticmethod
    def _persist_main_agent_state(root: Path, state: MainAgentState) -> None:
        write_json(root / "main_agent_state.json", state.model_dump(mode="json"))

    def _ensure_graph_assistant_message(
        self,
        result: ExecutionRunResult,
    ) -> ExecutionRunResult:
        runner = self._runner(result.snapshot.run_id)
        existing = runner.board.list_artifacts(
            run_id=result.snapshot.run_id,
            artifact_type="assistant_message",
            status="active",
        )
        if existing:
            assistant_id = existing[-1].artifact_id
            runner._emit(
                "assistant_message_ready",
                run_id=result.snapshot.run_id,
                thread_id=result.snapshot.thread_id,
                request_id=result.snapshot.request_id,
                mode="graph",
                delivery_status=result.snapshot.status,
                message=str(existing[-1].content.get("message") or ""),
                artifact_ids=[assistant_id],
            )
            return result.model_copy(
                update={
                    "artifact_ids": [*result.artifact_ids, assistant_id]
                    if assistant_id not in result.artifact_ids
                    else result.artifact_ids,
                }
            )
        artifacts = runner.board.list_artifacts(run_id=result.snapshot.run_id, status="active")
        report = next(
            (
                artifact
                for artifact in reversed(artifacts)
                if artifact.artifact_type == "research_report"
            ),
            None,
        )
        synthesis = None
        synthesis_artifact = None
        if (
            result.snapshot.status == "completed"
            and report is not None
            and result.snapshot.graph.domain_id == "finance_ashare"
        ):
            request_payload = read_json(runner.run_root / "request.json") or {}
            request_text = str(
                request_payload.get("user_query")
                or request_payload.get("query")
                or result.snapshot.graph.objective
                or ""
            )
            synthesis = self.main_agent.compose_research_synthesis(
                request=request_text,
                report=dict(report.content),
            )
            if synthesis:
                synthesis_artifact = runner.board.add(
                    ArtifactRecord(
                        artifact_type="main_agent_synthesis",
                        schema_version="1",
                        run_id=result.snapshot.run_id,
                        thread_id=result.snapshot.thread_id,
                        request_id=result.snapshot.request_id,
                        producer=ArtifactProducer(
                            executor="main_agent",
                            capability="core.research.synthesize",
                            domain_id=result.snapshot.graph.domain_id,
                            responsibility_node_id="main_agent",
                        ),
                        content={
                            **synthesis,
                            "source_artifact_id": report.artifact_id,
                            "model_generated": True,
                        },
                        source_refs=list(report.source_refs),
                        parent_artifact_ids=[report.artifact_id],
                        confidence=report.confidence,
                        risk_flags=[*report.risk_flags, "main_agent_synthesis"],
                    )
                )
        content = report.content if report is not None else {}
        if result.snapshot.status == "completed":
            message = str(
                (synthesis or {}).get("assistant_message")
                or content.get("conclusion")
                or content.get("summary")
                or "Agent OS graph run completed."
            )
            source_refs = list(report.source_refs) if report is not None else [
                {"type": "request", "id": result.snapshot.request_id}
            ]
        elif result.snapshot.clarification_question:
            message = result.snapshot.clarification_question
            source_refs = [{"type": "request", "id": result.snapshot.request_id}]
        else:
            message = {
                "gated": "Agent OS produced professional artifacts, but delivery is blocked by a quality gate or approval.",
                "waiting_approval": "Agent OS is waiting for the required approval before delivery.",
                "failed": "Agent OS could not complete the professional run; produced artifacts remain available for inspection.",
            }.get(
                result.snapshot.status,
                f"Agent OS graph run {result.snapshot.status}.",
            )
            source_refs = [{"type": "request", "id": result.snapshot.request_id}]
        assistant = runner.board.add(
            ArtifactRecord(
                artifact_type="assistant_message",
                schema_version="1",
                run_id=result.snapshot.run_id,
                thread_id=result.snapshot.thread_id,
                request_id=result.snapshot.request_id,
                producer=ArtifactProducer(
                    executor="main_agent",
                    capability="core.message.compose",
                    domain_id=result.snapshot.graph.domain_id,
                    responsibility_node_id="main_agent",
                ),
                content={
                    "message": message,
                    "source_artifact_ids": [
                        artifact_id
                        for artifact_id in (
                            report.artifact_id if report is not None else None,
                            synthesis_artifact.artifact_id if synthesis_artifact is not None else None,
                        )
                        if artifact_id
                    ],
                    "mode": "graph",
                    "delivery_status": result.snapshot.status,
                },
                source_refs=source_refs,
                parent_artifact_ids=[
                    artifact_id
                    for artifact_id in (
                        report.artifact_id if report is not None else None,
                        synthesis_artifact.artifact_id if synthesis_artifact is not None else None,
                    )
                    if artifact_id
                ],
                confidence=(
                    report.confidence
                    if result.snapshot.status == "completed" and report is not None
                    else 0.0
                ),
            )
        )
        runner._emit(
            "assistant_message_ready",
            run_id=result.snapshot.run_id,
            thread_id=result.snapshot.thread_id,
            request_id=result.snapshot.request_id,
            mode="graph",
            delivery_status=result.snapshot.status,
            message=message,
            artifact_ids=[assistant.artifact_id],
            source_artifact_ids=[
                artifact_id
                for artifact_id in (
                    report.artifact_id if report is not None else None,
                    synthesis_artifact.artifact_id if synthesis_artifact is not None else None,
                )
                if artifact_id
            ],
        )
        return result.model_copy(
            update={
                "artifact_ids": [
                    *result.artifact_ids,
                    *(
                        [synthesis_artifact.artifact_id]
                        if synthesis_artifact is not None
                        else []
                    ),
                    assistant.artifact_id,
                ],
            }
        )

    def _write_summary(
        self,
        result: ExecutionRunResult,
        *,
        intent: str | None = None,
    ) -> None:
        root = self.run_store.load_run_paths(result.snapshot.run_id).root
        write_json(
            root / "summary.json",
            {
                "run_id": result.snapshot.run_id,
                "request_id": result.snapshot.request_id,
                "intent": intent or f"domain:{result.snapshot.graph.domain_id}",
                "domain_id": result.snapshot.graph.domain_id,
                "status": result.snapshot.status,
                "message": f"Agent OS run {result.snapshot.status}",
                "graph_id": result.snapshot.graph.graph_id,
                "graph_revision": result.snapshot.graph.revision,
                "artifact_ids": result.artifact_ids,
                "final_artifact_ids": result.final_artifact_ids,
                "gate_evaluations": [
                    gate.model_dump(mode="json")
                    for gate in result.snapshot.gate_evaluations
                ],
            },
        )


__all__ = ["AgentOS"]
