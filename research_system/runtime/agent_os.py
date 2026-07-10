from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import Any

from research_system.runtime.domain_packs.registry import DomainPackRegistry
from research_system.runtime.execution.core.graph_runner import ExecutionGraphRunner
from research_system.runtime.intake.task_intake import TaskIntakeService
from research_system.runtime.planning.capability_planner import CapabilityPlanner
from research_system.runtime.planning.planner import RuntimePlanner
from research_system.runtime.platform.settings import Settings
from research_system.runtime.storage.jsonl import read_json, write_json
from research_system.runtime.storage.runs.run_store import RunStore
from research_system.schemas.artifacts import ArtifactDraft
from research_system.schemas.domain_pack import CapabilityPlanningRequest
from research_system.schemas.execution_graph import ExecutionGraphPatch
from research_system.schemas.execution_runtime import ExecutionRunResult, ExecutionRunSnapshot
from research_system.schemas.protocol import RequestEnvelope
from research_system.services.memory.layers import LayeredMemoryStore


class AgentOS:
    """Public domain-neutral API for capability planning and graph execution."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.registry = DomainPackRegistry.load(settings.domain_packs_root)
        self.planner = CapabilityPlanner(self.registry)
        self.task_intake = TaskIntakeService(self.registry)
        self.run_store = RunStore(settings.runs_root)
        self.memory = LayeredMemoryStore(settings.memory_root / "agent_os")
        self._active: dict[str, ExecutionGraphRunner] = {}
        self._lock = RLock()

    def list_domain_packs(self) -> list[dict[str, Any]]:
        return [
            {
                "domain_id": manifest.domain_id,
                "version": manifest.version,
                "description": manifest.description,
                "capabilities": [
                    {
                        "subagent_type": capability.subagent_type,
                        "capability": capability.capability,
                        "consumes": [port.artifact_type for port in capability.consumes],
                        "produces": [port.artifact_type for port in capability.produces],
                    }
                    for capability in manifest.capabilities
                ],
                "required_gate_ids": [
                    gate.gate_id for gate in manifest.quality_gates if gate.blocking
                ],
                "pack_path": str(self.registry.pack_path(manifest.domain_id)),
            }
            for manifest in self.registry.list()
        ]

    def run_domain(
        self,
        *,
        domain_id: str,
        objective: str,
        input_artifacts: list[ArtifactDraft],
        required_artifact_types: list[str] | None = None,
        preferred_capabilities: list[str] | None = None,
        run_id: str | None = None,
    ) -> ExecutionRunResult:
        manifest = self.registry.get(domain_id)
        required = list(required_artifact_types or [])
        if not required:
            required = [contract.artifact_type for contract in manifest.report_contracts]
        request = RequestEnvelope(
            intent=f"domain:{domain_id}",
            user_query=objective,
            slots={
                "domain_id": domain_id,
                "required_artifact_types": required,
            },
        )
        effective_run_id, paths, _ = self.run_store.create_run(
            request,
            request.intent or f"domain:{domain_id}",
            run_id=run_id,
        )
        graph = self.planner.build_graph(
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
                metadata={"request_id": request.request_id},
            )
        )
        if graph.governance.timeout_seconds is None:
            graph.governance.timeout_seconds = float(self.settings.run_timeout_seconds)
        thread_id = graph.graph_id
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
        runner = self._runner(effective_run_id)
        with self._lock:
            self._active[effective_run_id] = runner
        try:
            result = runner.run(
                graph=graph,
                run_id=effective_run_id,
                thread_id=thread_id,
                request_id=request.request_id,
                seed_artifacts=input_artifacts,
            )
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
    ) -> ExecutionRunResult:
        decision = self.intake(request)
        if decision.status != "accepted":
            raise ValueError("Agent OS Task Intake requires clarification before execution")
        request = decision.apply_to(request)
        intent = str(request.intent or "").strip()
        graph = RuntimePlanner(settings=self.settings).build_initial_execution_graph(
            intent=intent,
            request=request,
        )
        effective_run_id, paths, _ = self.run_store.create_run(
            request,
            intent,
            run_id=run_id,
        )
        thread_id = graph.graph_id
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
        runner = self._runner(effective_run_id)
        with self._lock:
            self._active[effective_run_id] = runner
        try:
            result = runner.run(
                graph=graph,
                run_id=effective_run_id,
                thread_id=thread_id,
                request_id=request.request_id,
                seed_artifacts=[
                    ArtifactDraft(
                        artifact_type="user_request",
                        content=input_payload,
                        source_refs=[{"type": "request", "id": request.request_id}],
                    )
                ],
            )
            self._write_summary(result, intent=intent)
            return result
        finally:
            with self._lock:
                self._active.pop(effective_run_id, None)

    def intake(self, request: RequestEnvelope):
        return self.task_intake.resolve(request)

    def inspect_run(self, run_id: str) -> dict[str, Any]:
        root = self.run_store.load_run_paths(run_id).root
        snapshot = read_json(root / "execution_snapshot.json")
        if snapshot is None:
            raise KeyError(f"run has no canonical execution snapshot: {run_id}")
        return {
            "request": read_json(root / "agent_os_request.json"),
            "snapshot": snapshot,
            "artifacts": [
                artifact.model_dump(mode="json")
                for artifact in self._runner(run_id).board.list_artifacts(thread_id=None)
            ],
        }

    def replay(self, run_id: str) -> ExecutionRunSnapshot:
        return self._runner(run_id).replay()

    def resume(self, run_id: str) -> ExecutionRunResult:
        runner = self._runner(run_id)
        with self._lock:
            self._active[run_id] = runner
        try:
            result = runner.resume()
            self._write_summary(result)
            return result
        finally:
            with self._lock:
                self._active.pop(run_id, None)

    def rerun_from(self, run_id: str, node_id: str) -> ExecutionRunResult:
        runner = self._runner(run_id)
        runner.rerun_from(node_id)
        with self._lock:
            self._active[run_id] = runner
        try:
            result = runner.resume()
            self._write_summary(result)
            return result
        finally:
            with self._lock:
                self._active.pop(run_id, None)

    def pause(self, run_id: str) -> bool:
        with self._lock:
            runner = self._active.get(run_id)
        if runner is None:
            runner = self._runner(run_id)
            snapshot = read_json(runner.snapshot_path)
            if not snapshot or snapshot.get("status") in {"completed", "failed", "cancelled", "gated"}:
                return False
        runner.request_pause()
        return True

    def cancel(self, run_id: str) -> bool:
        with self._lock:
            runner = self._active.get(run_id)
        if runner is None:
            runner = self._runner(run_id)
            snapshot = read_json(runner.snapshot_path)
            if not snapshot or snapshot.get("status") in {"completed", "failed", "cancelled", "gated"}:
                return False
        runner.request_cancel()
        return True

    def patch(self, run_id: str, patch: ExecutionGraphPatch) -> bool:
        with self._lock:
            runner = self._active.get(run_id)
        if runner is None:
            return False
        runner.submit_patch(patch)
        return True

    def _runner(self, run_id: str) -> ExecutionGraphRunner:
        paths = self.run_store.load_run_paths(run_id)
        return ExecutionGraphRunner(
            registry=self.registry,
            run_root=paths.root,
            memory_store=self.memory,
            cache_root=self.settings.memory_root / "agent_os_cache",
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
