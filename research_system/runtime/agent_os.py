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
from research_system.runtime.intake.request_intake import RequestIntakeService
from research_system.runtime.planning.capability_planner import (
    CapabilityDynamicPlanner,
    CapabilityPlanner,
)
from research_system.runtime.platform.settings import Settings
from research_system.runtime.providers.provider_registry import ProviderRegistry
from research_system.runtime.presentation.display import localize_runtime_error, runtime_status_message
from research_system.runtime.storage.jsonl import read_json, write_json
from research_system.runtime.core.id_utils import generate_ulid
from research_system.runtime.event_store import append_canonical_event
from research_system.runtime.storage.runs.run_store import RunStore
from research_system.runtime.turn_runner import TurnContext, TurnRunner
from research_system.schemas.artifacts import ArtifactDraft
from research_system.schemas.core_runtime import (
    MainAgentRunResult,
    MainAgentState,
    RequestIntakeDecision,
)
from research_system.schemas.events import EventEnvelope
from research_system.schemas.domain_pack import CapabilityPlanningRequest
from research_system.schemas.execution_graph import ExecutionGraphPatch
from research_system.schemas.execution_runtime import (
    ExecutionRunResult,
    ExecutionRunSnapshot,
    GraphStepOutcome,
)
from research_system.schemas.protocol import ProgressState, RequestEnvelope, ResponseEnvelope
from research_system.schemas.state_machine import RUN_TERMINAL_STATUSES
from research_system.schemas.turn_runtime import TurnConfig
from research_system.services.memory.layers import LayeredMemoryStore


class _AgentOSGraphRunnerAdapter:
    """Adapt the existing Graph request implementation to GraphExecutor."""

    def __init__(self, agent_os: "AgentOS") -> None:
        self.agent_os = agent_os

    def step(self, context: TurnContext) -> GraphStepOutcome | ExecutionRunResult:
        if context.recovery_continuation:
            context.recovery_continuation = False
            return self.agent_os._recover_graph_task(
                context.run_id,
                thread_id=context.thread_id,
                event_callback=context.event_callback,
                deadline_at=context.deadline_at,
                step=True,
            )
        if context.worker_restart_recovery:
            context.worker_restart_recovery = False
            return self.agent_os._recover_graph_worker_task(
                context.run_id,
                thread_id=context.thread_id,
                event_callback=context.event_callback,
                deadline_at=context.deadline_at,
                step=True,
            )
        if context.resume:
            if context.rerun_from:
                rerun_from = context.rerun_from
                # A rerun is a one-time graph mutation.  The enclosing
                # RegularExecutor may call us again for the next frontier;
                # those calls must resume the prepared snapshot instead of
                # invalidating the same node repeatedly.
                context.rerun_from = None
                return self.agent_os._rerun_graph_task(
                    context.run_id,
                    rerun_from,
                    thread_id=context.thread_id,
                    event_callback=context.event_callback,
                    deadline_at=context.deadline_at,
                    step=True,
                )
            return self.agent_os._resume_graph_task(
                context.run_id,
                thread_id=context.thread_id,
                event_callback=context.event_callback,
                deadline_at=context.deadline_at,
                step=True,
            )
        if context.graph is not None:
            return self.agent_os._execute_prebuilt_graph(context)
        return self.agent_os._execute_graph_task(
            context.request,
            run_id=context.run_id,
            turn_id=context.turn.turn_id,
            intake_decision=context.decision,
            event_callback=context.event_callback,
            deadline_at=context.deadline_at,
            step=True,
        )

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
        self.turn_runner = TurnRunner(
            run_store=self.run_store,
            main_agent=self.main_agent,
            graph_adapter=_AgentOSGraphRunnerAdapter(self),
            timeout_seconds=float(settings.run_timeout_seconds),
        )

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
                "method_catalog": dict(manifest.planner_policies.get("method_catalog") or {}),
                "snapshot_dimensions": list(
                    manifest.planner_policies.get("snapshot_dimensions") or []
                ),
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
        terminal = {"completed", "completed_partial", "gated", "failed", "cancelled", "timed_out"}
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
                ]
                if str(intent).strip()
            }
        )

    def supported_methods(self) -> list[str]:
        """Return method identifiers declared by installed Packs."""
        methods: set[str] = set()
        for manifest in self.registry.list():
            policies = manifest.planner_policies or {}
            methods.update(
                str(method).strip().lower()
                for method in (policies.get("method_catalog") or {})
                if str(method).strip()
            )
            methods.update(
                str(method).strip().lower()
                for method in (policies.get("methodology_outputs") or {})
                if str(method).strip()
            )
        return sorted(methods)

    def supported_snapshot_types(self) -> list[str]:
        """Return Pack-owned snapshot dimension identifiers for request validation."""
        dimensions: set[str] = set()
        for manifest in self.registry.list():
            dimensions.update(
                str(item).strip().lower()
                for item in (manifest.planner_policies.get("snapshot_dimensions") or [])
                if str(item).strip()
            )
        return sorted(dimensions)

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
        effective_run_id, _, _ = self.run_store.create_run(
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
        if (
            graph.governance.timeout_seconds is None
            and str(graph.metadata.get("deadline_policy") or "default") != "unbounded"
        ):
            graph.governance.timeout_seconds = float(self.settings.run_timeout_seconds)
        thread_id = str(request.thread_id or request.slots.get("chat_thread_id") or graph.graph_id)
        decision = RequestIntakeDecision(
            status="accepted",
            request_kind="domain_task",
            recommended_mode="graph",
            intent=request.intent,
            domain_id=domain_id,
            confidence=1.0,
        )
        outcome = self.turn_runner.execute_task(
            request=request,
            decision=decision,
            run_id=effective_run_id,
            thread_id=thread_id,
            execution_mode="graph",
            graph=graph,
            seed_artifacts=input_artifacts,
            event_callback=event_callback,
        )
        result = outcome.result
        self.turn_runner.finalize(
            outcome=outcome,
            response_factory=lambda value: self._response_from_graph(request, value),
        )
        return result

    def _run_graph_runner(
        self,
        *,
        run_id: str,
        request: RequestEnvelope,
        thread_id: str,
        graph=None,
        seed_artifacts: list[ArtifactDraft] | None = None,
        event_callback=None,
        turn_id: str | None = None,
        resume: bool = False,
        intent: str | None = None,
        step: bool = False,
        deadline_at: float | None = None,
    ) -> GraphStepOutcome | ExecutionRunResult:
        """Advance one Graph safe point under the enclosing Root Turn."""
        if not step:
            raise ValueError(
                "GraphRunner must be advanced through the TurnRunner safe-point adapter"
            )
        runner = self._runner(
            run_id,
            event_callback=event_callback,
            turn_id=turn_id,
            deadline_at=deadline_at,
        )
        with self._lock:
            self._active[run_id] = runner
        try:
            if resume:
                persisted = read_json(runner.snapshot_path)
                if not isinstance(persisted, dict):
                    raise ValueError(f"graph snapshot is missing: {run_id}")
                snapshot = ExecutionRunSnapshot.model_validate(persisted)
                step_outcome = runner.step(
                    graph=snapshot.graph,
                    run_id=snapshot.run_id,
                    thread_id=snapshot.thread_id,
                    request_id=snapshot.request_id,
                    user_id=snapshot.user_id,
                    tenant_id=snapshot.tenant_id,
                    resume=True,
                )
                result = step_outcome.result
            else:
                if graph is None:
                    raise ValueError("Graph execution requires a graph")
                step_outcome = runner.step(
                    graph=graph,
                    run_id=run_id,
                    thread_id=thread_id,
                    request_id=request.request_id,
                    user_id=request.user_id,
                    tenant_id=request.tenant_id,
                    seed_artifacts=list(seed_artifacts or []),
                )
                result = step_outcome.result
            if not step or result.snapshot.status in {
                "completed",
                "completed_partial",
                "gated",
                "failed",
                "cancelled",
                "timed_out",
            }:
                result = self._ensure_graph_assistant_message(result)
            self._update_graph_agent_state(result)
            if not step or result.snapshot.status in {
                "completed",
                "completed_partial",
                "gated",
                "failed",
                "cancelled",
                "timed_out",
            }:
                self._write_summary(result, intent=intent or request.intent)
            return step_outcome.model_copy(update={"result": result})
        finally:
            with self._lock:
                self._active.pop(run_id, None)

    def _execute_prebuilt_graph(self, context: TurnContext) -> ExecutionRunResult:
        """Execute a caller-supplied graph inside the canonical Turn lifecycle."""
        graph = context.graph
        if graph is None:
            raise ValueError("prebuilt Graph execution requires a graph")
        paths = self.run_store.load_run_paths(context.run_id)
        self._write_graph_agent_state(
            paths.root,
            run_id=context.run_id,
            request=context.request,
            thread_id=context.thread_id,
            graph=graph,
            status="running",
        )
        write_json(
            paths.root / "agent_os_request.json",
            {
                "run_id": context.run_id,
                "thread_id": context.thread_id,
                "request": context.request.model_dump(mode="json"),
                "request_intake": context.decision.model_dump(mode="json"),
                "domain_id": graph.domain_id,
                "objective": graph.objective,
                "intent": context.request.intent,
                "required_artifact_types": list(
                    graph.metadata.get("required_artifact_types") or []
                ),
            },
        )
        return self._run_graph_runner(
            run_id=context.run_id,
            request=context.request,
            thread_id=context.thread_id,
            graph=graph,
            seed_artifacts=context.seed_artifacts,
            event_callback=context.event_callback,
            turn_id=context.turn.turn_id,
            intent=context.request.intent,
            deadline_at=context.deadline_at,
            step=True,
        )

    def _execute_graph_task(
        self,
        request: RequestEnvelope,
        *,
        run_id: str | None = None,
        intake_decision: RequestIntakeDecision | None = None,
        event_callback=None,
        turn_id: str | None = None,
        step: bool = False,
        deadline_at: float | None = None,
    ) -> GraphStepOutcome | ExecutionRunResult:
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
        for key in (
            "symbols",
            "fund_codes",
            "fund_category",
            "portfolio_holdings",
            "benchmark_return",
            "rebalance_targets",
            "asset_class_targets",
            "drift_threshold",
            "fee_model",
            "requested_methods",
            "model_updates",
            "method_assumptions",
            "sector",
            "source_records",
            "snapshots",
            "source_refs",
            "holding_context",
        ):
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
        return self._run_graph_runner(
            run_id=effective_run_id,
            request=request,
            thread_id=thread_id,
            graph=graph,
            seed_artifacts=[
                ArtifactDraft(
                    artifact_type="user_request",
                    content=input_payload,
                    source_refs=[{"type": "request", "id": request.request_id}],
                )
            ],
            event_callback=event_callback,
            turn_id=turn_id,
            intent=intent,
            deadline_at=deadline_at,
            step=step,
        )

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
            # Idempotency applies while a worker is still running too.  Return
            # the persisted lifecycle view instead of routing the same request
            # into a second execution attempt.
            return self._inflight_idempotent_response(existing_run_id)
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
        try:
            return self._handle_routed_request(
                routed_request=routed_request,
                effective_run_id=effective_run_id,
                thread_id=thread_id,
                run_id=run_id,
                event_callback=event_callback,
            )
        finally:
            with self._lock:
                self._prepared_run_ids.discard(effective_run_id)

    def _inflight_idempotent_response(self, run_id: str) -> ResponseEnvelope:
        """Return a stable response while an idempotent Run has no result yet."""
        root = self.run_store.load_run_paths(run_id).root
        request_payload = read_json(root / "request.json") or {}
        request = RequestEnvelope.model_validate(request_payload)
        turn = read_json(root / "turn_state.json") or {}
        if not isinstance(turn, dict):
            turn = {}
        raw_status = str(turn.get("status") or "received")
        status = raw_status if raw_status in {
            "running",
            "paused",
            "waiting_approval",
            "gated",
            "completed",
            "completed_partial",
            "failed",
            "cancelled",
            "timed_out",
        } else "running"
        task = read_json(root / "task_state.json") or {}
        continuation = task.get("continuation") if isinstance(task, dict) else None
        return ResponseEnvelope(
            request_id=request.request_id,
            run_id=run_id,
            thread_id=str(turn.get("thread_id") or request.thread_id or "") or None,
            turn_id=str(turn.get("turn_id") or "") or None,
            task_type=str(turn.get("task_type") or "regular"),
            mode=(
                str(turn.get("execution_mode"))
                if turn.get("execution_mode") in {"core", "task", "graph"}
                else None
            ),
            status=status,  # type: ignore[arg-type]
            message=(
                "该幂等 Run 已在执行中。"
                if status == "running"
                else runtime_status_message(status, subject="Agent OS Run ")
            ),
            progress=ProgressState(
                current_phase="turn",
                active_agent="main_agent",
                latest_event="turn_replayed_for_idempotency",
                status_message=runtime_status_message(status, subject="Agent OS Run "),
                percent=100 if status in RUN_TERMINAL_STATUSES else 0,
            ),
            continuation=continuation if isinstance(continuation, dict) else None,
            child_turn_ids=[
                str(item.get("child_turn_id"))
                for item in list(turn.get("child_turns") or [])
                if isinstance(item, dict) and item.get("child_turn_id")
            ],
        )

    def _handle_routed_request(
        self,
        *,
        routed_request: RequestEnvelope,
        effective_run_id: str,
        thread_id: str,
        run_id: str | None,
        event_callback=None,
    ) -> ResponseEnvelope:
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
                return self._update_clarification_run(
                    run_id=run_id,
                    request=routed_request,
                    decision=decision,
                    event_callback=event_callback,
                )
            return self._create_clarification_run(
                request=routed_request,
                decision=decision,
                run_id=effective_run_id,
                event_callback=event_callback,
            )

        resolved_request = decision.apply_to(routed_request)
        if run_id and self._is_pending_clarification(run_id):
            self._complete_clarification_run(
                run_id=run_id,
                request=resolved_request,
                event_callback=event_callback,
            )
            with self._lock:
                self._prepared_run_ids.add(effective_run_id)
        execution_mode = "graph" if decision.request_kind == "domain_task" else decision.recommended_mode
        if execution_mode == "graph":
            response_factory = lambda result: self._response_from_graph(resolved_request, result)
        else:
            response_factory = lambda result: self._finalize_core_turn_response(resolved_request, result)
        turn_config = (
            self._domain_turn_config(
                request=resolved_request,
                decision=decision,
                execution_mode=execution_mode,
            )
            if execution_mode == "graph"
            else None
        )
        if execution_mode != "graph":
            paths = self.run_store.load_run_paths(effective_run_id)
            self._prepared_run_ids.discard(effective_run_id)
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
                    "mode": execution_mode,
                    "request_intake": decision.model_dump(mode="json"),
                },
            )
        return self.turn_runner.run(
            request=resolved_request,
            decision=decision,
            run_id=effective_run_id,
            thread_id=thread_id,
            execution_mode=execution_mode,
            turn_config=turn_config,
            response_factory=response_factory,
            event_callback=event_callback,
        )

    def _domain_turn_config(
        self,
        *,
        request: RequestEnvelope,
        decision: RequestIntakeDecision,
        execution_mode: str,
    ) -> TurnConfig:
        """Align the root Turn deadline policy with the selected DomainPack profile.

        UZI research profiles describe depth and expected latency; upstream
        waits for submitted fetchers instead of enforcing a wall-clock cutoff.
        The enclosing root Turn must therefore opt out of the Agent OS default
        deadline whenever the selected profile declares unbounded execution.
        """

        config = self.turn_runner.resolve_config(
            request=request,
            decision=decision,
            execution_mode=execution_mode,  # type: ignore[arg-type]
        )
        explicit_turn_config = (request.constraints or {}).get("turn_config")
        if isinstance(explicit_turn_config, TurnConfig):
            if explicit_turn_config.timeout_seconds is not None:
                return config
        elif isinstance(explicit_turn_config, dict) and explicit_turn_config.get("timeout_seconds") is not None:
            return config

        domain_id = str(decision.domain_id or "").strip()
        if not domain_id:
            return config
        manifest = self.registry.get(domain_id)
        policies = dict(manifest.planner_policies or {})
        profiles = dict(policies.get("research_profiles") or {})
        explicit_profile = str(
            request.constraints.get("research_profile")
            or request.slots.get("research_profile")
            or ""
        ).strip().lower()
        intent = str(request.intent or decision.intent or "").strip()
        selected_profile = (
            explicit_profile
            if explicit_profile in profiles
            else str((policies.get("intent_profiles") or {}).get(intent) or "").strip().lower()
            or str(policies.get("default_profile") or "").strip().lower()
        )
        profile_policy = dict(profiles.get(selected_profile) or {})
        if str(profile_policy.get("deadline_policy") or "default").strip().lower() == "unbounded":
            return config.model_copy(
                update={"deadline_policy": "unbounded", "timeout_seconds": None}
            )
        timeout = profile_policy.get("max_duration_seconds")
        if timeout is None:
            return config
        return config.model_copy(update={"timeout_seconds": float(timeout)})

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
        self._append_execution_event(paths.root, event, event_callback)
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

    def _emit_clarification_event(self, root: Path, callback, *, run_id: str, request: RequestEnvelope, decision: RequestIntakeDecision) -> None:
        event = {
            "event_type": "clarification_requested",
            "run_id": run_id,
            "thread_id": str(request.thread_id or f"run:{run_id}"),
            "request_id": request.request_id,
            "actor": "main_agent",
            "question": decision.clarification_question or "请补充请求所需的信息后继续。",
            "missing_fields": list(decision.missing_fields),
        }
        self._append_execution_event(root, event, callback)

    @staticmethod
    def _append_execution_event(
        root: Path,
        event: dict[str, Any],
        callback=None,
    ) -> None:
        """Append a pre-Turn event without resetting the shared cursor."""
        path = root / "execution_events.jsonl"
        event["sequence"] = 0
        normalized = EventEnvelope.model_validate(event).model_dump(mode="json")
        normalized = append_canonical_event(path, normalized)
        if callback is not None:
            callback(normalized)

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
                status_message="RequestIntake 需要补充信息。",
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
        self._append_execution_event(
            root,
            {
                "event_type": "run_accepted",
                "run_id": effective_run_id,
                "thread_id": str(request.thread_id or f"run:{effective_run_id}"),
                "request_id": request.request_id,
                "actor": "agent_os",
                "message": "Agent OS 已接受后台运行请求。",
            },
        )
        return effective_run_id

    def adopt_prepared_run(self, run_id: str) -> None:
        """Re-register a persisted background reservation after process restart."""
        paths = self.run_store.load_run_paths(run_id)
        if read_json(paths.root / "agent_os_pending.json") is None:
            raise ValueError(f"run is not a persisted Agent OS background reservation: {run_id}")
        with self._lock:
            self._prepared_run_ids.add(run_id)

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
        requested_required = list(request.slots.get("required_artifact_types") or [])
        required = list(requested_required)
        required_outputs_source = "request_slots"
        if not required:
            intent_definition = next(
                (
                    item
                    for item in manifest.intake.intents
                    if item.intent_id == str(request.intent or "").strip()
                ),
                None,
            )
            intent_outputs = list(intent_definition.output_artifact_types) if intent_definition else []
            if intent_outputs:
                # Intent-specific delivery contracts must not inherit unrelated
                # report contracts such as research_report. The planner will
                # add the intent's specialist artifacts and any profile outputs.
                required = list(dict.fromkeys(str(item).strip() for item in intent_outputs if str(item).strip()))
                required_outputs_source = "intent_output_contract"
            else:
                required = [contract.artifact_type for contract in manifest.report_contracts]
                required_outputs_source = "manifest_report_contract"
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
                "required_outputs_source": required_outputs_source,
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
        turn_state = read_json(root / "turn_state.json")
        snapshot = read_json(root / "execution_snapshot.json")
        if snapshot is None:
            core_state = read_json(root / "main_agent_state.json")
            if core_state is not None:
                return {
                    "mode": "core",
                    "request": read_json(root / "request.json"),
                    "snapshot": core_state,
                    "turn": turn_state,
                    "task": read_json(root / "task_state.json"),
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
                    "turn": turn_state,
                    "task": read_json(root / "task_state.json"),
                    "artifacts": [],
                }
            raise KeyError(f"run has no canonical execution snapshot: {run_id}")
        return {
            "mode": "graph",
            "request": read_json(root / "agent_os_request.json"),
            "snapshot": snapshot,
            "turn": turn_state,
            "task": read_json(root / "task_state.json"),
            "main_agent_state": read_json(root / "main_agent_state.json"),
            "artifacts": [
                artifact.model_dump(mode="json")
                for artifact in self._runner(run_id).board.list_artifacts(thread_id=None)
            ],
        }

    def replay(self, run_id: str) -> ExecutionRunSnapshot:
        return self._runner(run_id).replay()

    def resume(self, run_id: str) -> ExecutionRunResult:
        detail = self.inspect_run(run_id)
        if detail.get("mode") == "core":
            state = MainAgentState.model_validate(detail["snapshot"])
            turn_status = str((detail.get("turn") or {}).get("status") or "")
            recover = turn_status in {"timed_out", "failed", "cancelled"}
            if (
                state.status != "paused"
                and turn_status not in {"running", "paused", "waiting_approval", "gated"}
                and not recover
            ):
                raise ValueError(f"core run is not resumable: {state.status}")
            request, decision = self._load_core_request(run_id)
            result, _ = self._resume_core_task(
                request=request,
                decision=decision,
                state=state,
                recover=recover,
            )
            return result  # type: ignore[return-value]
        request, decision = self._load_graph_request(run_id)
        snapshot = detail.get("snapshot") or {}
        turn_config = self._stored_turn_config(detail)
        turn_status = str((detail.get("turn") or {}).get("status") or "")
        outcome = self.turn_runner.execute_task(
            request=request,
            decision=decision,
            run_id=run_id,
            thread_id=str(snapshot.get("thread_id") or f"thread_{run_id}"),
            execution_mode="graph",
            turn_config=turn_config,
            resume=True,
            recover=turn_status in {"timed_out", "failed", "cancelled"},
        )
        result = outcome.result
        self._update_graph_agent_state(result)
        self._write_summary(result)
        self.turn_runner.finalize(
            outcome=outcome,
            response_factory=lambda value: self._response_from_graph(request, value),
        )
        return result

    def rerun_from(self, run_id: str, node_id: str) -> ExecutionRunResult:
        detail = self.inspect_run(run_id)
        if detail.get("mode") == "core":
            raise ValueError("Core Agent Loop runs do not expose graph nodes for partial rerun")
        request, decision = self._load_graph_request(run_id)
        snapshot = detail.get("snapshot") or {}
        turn_config = self._stored_turn_config(detail)
        outcome = self.turn_runner.execute_task(
            request=request,
            decision=decision,
            run_id=run_id,
            thread_id=str(snapshot.get("thread_id") or f"thread_{run_id}"),
            execution_mode="graph",
            turn_config=turn_config,
            resume=True,
            rerun_from=node_id,
        )
        result = outcome.result
        self.turn_runner.finalize(
            outcome=outcome,
            response_factory=lambda value: self._response_from_graph(request, value),
        )
        return result

    def pause(self, run_id: str) -> bool:
        paths = self.run_store.load_run_paths(run_id)
        turn_payload = read_json(paths.turn_state)
        if not isinstance(turn_payload, dict):
            control = read_json(paths.root / "execution_control.json") or {}
            cancel_requested = bool(control.get("cancel_requested"))
            write_json(
                paths.root / "execution_control.json",
                {
                    **control,
                    "pause_requested": not cancel_requested,
                    "cancel_requested": cancel_requested,
                },
            )
            return True
        status = str(turn_payload.get("status") or "")
        if status in RUN_TERMINAL_STATUSES or status in {"paused", "waiting_approval"}:
            return False
        self.turn_runner.pause(run_id, reason="调用方已请求暂停运行。")
        return True

    def cancel(self, run_id: str) -> bool:
        paths = self.run_store.load_run_paths(run_id)
        turn_payload = read_json(paths.turn_state)
        if not isinstance(turn_payload, dict):
            control = read_json(paths.root / "execution_control.json") or {}
            write_json(
                paths.root / "execution_control.json",
                {**control, "pause_requested": False, "cancel_requested": True},
            )
            return True
        status = str(turn_payload.get("status") or "")
        if status in RUN_TERMINAL_STATUSES or status == "cancelled":
            return False
        self.turn_runner.cancel(run_id, reason="调用方已请求取消运行。")
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
            runner = self._runner(run_id)
            snapshot = read_json(runner.snapshot_path)
            if not isinstance(snapshot, dict) or str(snapshot.get("status") or "") in RUN_TERMINAL_STATUSES:
                return False
        runner.submit_patch(patch)
        return True

    def _runner(
        self,
        run_id: str,
        *,
        event_callback=None,
        turn_id: str | None = None,
        deadline_at: float | None = None,
    ) -> ExecutionGraphRunner:
        paths = self.run_store.load_run_paths(run_id)
        request_payload = read_json(paths.request) or {}
        if isinstance(request_payload, dict) and isinstance(request_payload.get("request"), dict):
            request_payload = request_payload["request"]
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
            llm_provider=self.provider,
            cache_root=self.settings.memory_root / "agent_os_cache",
            event_callback=event_callback,
            progress_observer=self.main_agent.observe_graph_progress,
            turn_id=turn_id,
            user_id=(request_payload.get("user_id") if isinstance(request_payload, dict) else None),
            tenant_id=(request_payload.get("tenant_id") if isinstance(request_payload, dict) else None),
            deadline_at=deadline_at,
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

    def _resume_core_task(
        self,
        *,
        request: RequestEnvelope,
        decision: RequestIntakeDecision,
        state: MainAgentState,
        recover: bool = False,
    ) -> tuple[MainAgentRunResult, ResponseEnvelope]:
        turn_payload = read_json(
            self.run_store.load_run_paths(state.run_id).root / "turn_state.json"
        ) or {}
        turn_config = (
            TurnConfig.model_validate(turn_payload.get("config"))
            if isinstance(turn_payload, dict) and isinstance(turn_payload.get("config"), dict)
            else None
        )
        outcome = self.turn_runner.execute_task(
            request=request,
            decision=decision,
            run_id=state.run_id,
            thread_id=state.thread_id,
            execution_mode=state.mode,
            turn_config=turn_config,
            resume=True,
            recover=recover,
        )
        result = outcome.result
        self._write_core_summary(result)
        response = self.turn_runner.finalize(
            outcome=outcome,
            response_factory=lambda value: self._response_from_core(request, value),
        )
        return result, response

    @staticmethod
    def _stored_turn_config(detail: dict[str, Any]) -> TurnConfig | None:
        payload = detail.get("turn") if isinstance(detail, dict) else None
        if not isinstance(payload, dict) or not isinstance(payload.get("config"), dict):
            return None
        return TurnConfig.model_validate(payload["config"])

    def _load_graph_request(self, run_id: str):
        root = self.run_store.load_run_paths(run_id).root
        payload = read_json(root / "agent_os_request.json")
        if not isinstance(payload, dict):
            raise ValueError(f"graph run request is missing: {run_id}")
        request_payload = payload.get("request")
        if not isinstance(request_payload, dict):
            raise ValueError(f"graph run request envelope is missing: {run_id}")
        request = RequestEnvelope.model_validate(request_payload)
        decision = RequestIntakeDecision(
            status="accepted",
            request_kind="domain_task",
            recommended_mode="graph",
            intent=str(payload.get("intent") or request.intent or "") or None,
            domain_id=str(payload.get("domain_id") or "") or None,
            confidence=1.0,
        )
        return request, decision

    def _resume_graph_task(
        self,
        run_id: str,
        *,
        thread_id: str | None = None,
        event_callback=None,
        step: bool = False,
        deadline_at: float | None = None,
    ) -> GraphStepOutcome | ExecutionRunResult:
        turn_payload = read_json(
            self.run_store.load_run_paths(run_id).root / "turn_state.json"
        )
        request, _ = self._load_graph_request(run_id)
        return self._run_graph_runner(
            run_id=run_id,
            request=request,
            thread_id=str(thread_id or request.thread_id or f"thread_{run_id}"),
            event_callback=event_callback,
            turn_id=(
                str(turn_payload.get("turn_id"))
                if isinstance(turn_payload, dict) and turn_payload.get("turn_id")
                else None
            ),
            resume=True,
            deadline_at=deadline_at,
            step=step,
        )

    def _recover_graph_task(
        self,
        run_id: str,
        *,
        thread_id: str | None = None,
        event_callback=None,
        step: bool = False,
        deadline_at: float | None = None,
    ) -> GraphStepOutcome | ExecutionRunResult:
        """Prepare a terminal Graph checkpoint for a Turn recovery continuation."""
        if not step:
            raise ValueError("Graph recovery must be advanced through TurnRunner")
        request, _ = self._load_graph_request(run_id)
        runner = self._runner(
            run_id,
            event_callback=event_callback,
            turn_id=(
                str((read_json(self.run_store.load_run_paths(run_id).root / "turn_state.json") or {}).get("turn_id"))
                or None
            ),
            deadline_at=deadline_at,
        )
        runner.recover_terminal()
        return self._resume_graph_task(
            run_id,
            thread_id=str(thread_id or request.thread_id or f"thread_{run_id}"),
            event_callback=event_callback,
            deadline_at=deadline_at,
            step=True,
        )

    def _recover_graph_worker_task(
        self,
        run_id: str,
        *,
        thread_id: str | None = None,
        event_callback=None,
        step: bool = False,
        deadline_at: float | None = None,
    ) -> GraphStepOutcome | ExecutionRunResult:
        """Requeue in-flight Graph nodes before the next Turn safe point."""
        if not step:
            raise ValueError("Graph worker recovery must be advanced through TurnRunner")
        request, _ = self._load_graph_request(run_id)
        runner = self._runner(
            run_id,
            event_callback=event_callback,
            turn_id=(
                str((read_json(self.run_store.load_run_paths(run_id).root / "turn_state.json") or {}).get("turn_id"))
                or None
            ),
            deadline_at=deadline_at,
        )
        runner.recover_worker_restart()
        return self._resume_graph_task(
            run_id,
            thread_id=str(thread_id or request.thread_id or f"thread_{run_id}"),
            event_callback=event_callback,
            deadline_at=deadline_at,
            step=True,
        )

    def _rerun_graph_task(
        self,
        run_id: str,
        node_id: str,
        *,
        thread_id: str | None = None,
        event_callback=None,
        step: bool = False,
        deadline_at: float | None = None,
    ) -> GraphStepOutcome | ExecutionRunResult:
        request, _ = self._load_graph_request(run_id)
        turn_payload = read_json(
            self.run_store.load_run_paths(run_id).root / "turn_state.json"
        )
        runner = self._runner(
            run_id,
            event_callback=event_callback,
            turn_id=(
                str(turn_payload.get("turn_id"))
                if isinstance(turn_payload, dict) and turn_payload.get("turn_id")
                else None
            ),
            deadline_at=deadline_at,
        )
        runner.rerun_from(node_id)
        return self._run_graph_runner(
            run_id=run_id,
            request=request,
            thread_id=str(thread_id or request.thread_id or f"thread_{run_id}"),
            event_callback=event_callback,
            turn_id=(
                str(turn_payload.get("turn_id"))
                if isinstance(turn_payload, dict) and turn_payload.get("turn_id")
                else None
            ),
            resume=True,
            deadline_at=deadline_at,
            step=step,
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
            else snapshot.status
        )
        artifacts = self.inspect_run(snapshot.run_id).get("artifacts") or []
        final_ids = set(result.final_artifact_ids)
        turn_payload = read_json(
            self.run_store.load_run_paths(snapshot.run_id).root / "turn_state.json"
        ) or {}
        final_artifacts = [
            artifact
            for artifact in artifacts
            if artifact.get("artifact_id") in final_ids
            or artifact.get("artifact_type") == "assistant_message"
        ]
        assistant_artifact = next(
            (
                artifact
                for artifact in reversed(final_artifacts)
                if artifact.get("artifact_type") == "assistant_message"
                and isinstance(artifact.get("content"), dict)
            ),
            None,
        )
        assistant_content = (
            assistant_artifact.get("content")
            if isinstance(assistant_artifact, dict)
            else {}
        )
        delivery_status = str(
            (assistant_content or {}).get("delivery_status") or snapshot.status
        )
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
        elif status not in {"completed", "completed_partial"}:
            message = localize_runtime_error(
                message,
                fallback=runtime_status_message(status),
            )
        return ResponseEnvelope(
            mode="graph",
            request_id=snapshot.request_id,
            run_id=snapshot.run_id,
            # Graph may use an internal planning thread, but the externally
            # visible response belongs to the canonical Root Turn thread.
            thread_id=str(turn_payload.get("thread_id") or snapshot.thread_id),
            turn_id=str(turn_payload.get("turn_id") or "") or None,
            task_type=str(turn_payload.get("task_type") or "regular"),
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
                status_message=runtime_status_message(snapshot.status),
                percent=100 if status in {"completed", "completed_partial", "failed", "cancelled", "timed_out", "gated"} else 0,
            ),
            result={
                "protocol": "agent_os",
                "mode": "graph",
                "graph_id": snapshot.graph.graph_id,
                "graph_revision": snapshot.graph.revision,
                "artifact_ids": list(result.artifact_ids),
                "final_artifact_ids": list(result.final_artifact_ids),
                "pending_approval_ids": list(snapshot.pending_approval_ids),
                "tool_call_count": snapshot.tool_call_count,
                "delivery_status": delivery_status,
                "synthesis_unavailable": delivery_status == "synthesis_unavailable",
                "turn_id": str(turn_payload.get("turn_id") or "") or None,
            },
            artifacts=final_artifacts,
            artifact_ids=list(result.artifact_ids),
            final_artifact_ids=list(result.final_artifact_ids),
            pending_approval_ids=list(snapshot.pending_approval_ids),
            continuation=(
                read_json(self.run_store.load_run_paths(snapshot.run_id).root / "task_state.json") or {}
            ).get("continuation"),
        )

    def _response_from_core(
        self,
        request: RequestEnvelope,
        result: MainAgentRunResult,
    ) -> ResponseEnvelope:
        artifacts = self.inspect_run(result.run_id).get("artifacts") or []
        turn_payload = read_json(
            self.run_store.load_run_paths(result.run_id).root / "turn_state.json"
        ) or {}
        response_status = (
            "needs_clarification"
            if result.clarification_question
            else result.status
        )
        response_message = result.message
        if result.status not in {"completed", "completed_partial"} and not result.clarification_question:
            response_message = localize_runtime_error(
                result.message,
                fallback=runtime_status_message(result.status, subject="Main Agent 运行"),
            )
        return ResponseEnvelope(
            mode=result.mode,
            request_id=result.request_id,
            run_id=result.run_id,
            thread_id=result.thread_id,
            turn_id=str(turn_payload.get("turn_id") or "") or None,
            task_type=str(turn_payload.get("task_type") or "regular"),
            intent=result.intent,
            status=response_status,
            message=response_message,
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
                status_message=runtime_status_message(result.status, subject=f"Main Agent {result.mode} loop "),
                percent=100 if result.status in {"completed", "failed", "cancelled"} else 0,
            ),
            result={
                "protocol": "agent_os",
                "mode": result.mode,
                "message": response_message,
                "artifact_ids": list(result.artifact_ids),
                "pending_approval_ids": list(result.pending_approval_ids),
                "tool_calls": [item.model_dump(mode="json") for item in result.tool_calls],
                "clarification": (
                    {
                        "question": result.clarification_question,
                        "missing_fields": list(result.missing_fields),
                    }
                    if result.clarification_question
                    else None
                ),
                "turn_id": str(turn_payload.get("turn_id") or "") or None,
                "task_selection": turn_payload.get("task_selection") or {},
            },
            artifacts=[artifact for artifact in artifacts if artifact.get("artifact_id") in set(result.artifact_ids)],
            artifact_ids=list(result.artifact_ids),
            pending_approval_ids=list(result.pending_approval_ids),
            continuation=result.continuation,
        )

    def _write_core_summary(self, result: MainAgentRunResult) -> None:
        root = self.run_store.load_run_paths(result.run_id).root
        summary_message = result.message
        if result.status not in {"completed", "completed_partial"}:
            summary_message = localize_runtime_error(
                result.message,
                fallback=runtime_status_message(result.status, subject="Main Agent 运行"),
            )
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
                "message": summary_message,
                "artifact_ids": list(result.artifact_ids),
                "tool_call_ids": [item.call_id for item in result.tool_calls],
            },
        )

    def _finalize_core_turn_response(
        self,
        request: RequestEnvelope,
        result: MainAgentRunResult,
    ) -> ResponseEnvelope:
        """Build the Core response inside TurnRunner.finalize."""
        self._write_core_summary(result)
        return self._response_from_core(request, result)

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
            status=status if status in {"running", "completed", "completed_partial", "failed", "cancelled", "paused", "waiting_approval", "gated", "timed_out"} else "failed",
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
        from research_system.runtime.graph_delivery import ensure_graph_assistant_message

        return ensure_graph_assistant_message(self, result)

    @staticmethod
    def _mark_graph_partial_delivery(*, result: ExecutionRunResult, runner) -> None:
        """Persist the distinction between Pack completion and final synthesis."""

        snapshot = result.snapshot
        if snapshot.status != "completed":
            return
        runner._set_run_status(snapshot, "completed_partial")
        snapshot.updated_at = datetime.now(timezone.utc)
        runner._persist(snapshot)
        runner._emit(
            "graph_run_partial",
            status="completed_partial",
            delivery_status="synthesis_unavailable",
            reason="main_agent_synthesis_unavailable",
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
                "message": runtime_status_message(result.snapshot.status),
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
