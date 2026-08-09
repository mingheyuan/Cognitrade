from __future__ import annotations

from pathlib import Path
from typing import Any

from research_system.runtime.core.id_utils import generate_ulid
from research_system.runtime.domain_packs.registry import DomainPackRegistry
from research_system.runtime.execution.core.graph_runner import ExecutionGraphRunner
from research_system.runtime.planning.capability_planner import CapabilityPlanner
from research_system.runtime.storage.jsonl import read_jsonl
from research_system.schemas.domain_pack import CapabilityPlanningRequest
from research_system.schemas.evaluation import (
    PackEvaluationDefinition,
    PackEvaluationResult,
)
from research_system.schemas.execution_runtime import ExecutionRunResult


class PackEvaluationError(RuntimeError):
    pass


class PackEvaluationRunner:
    """Run Pack-owned contract and failure-recovery evaluations."""

    def __init__(self, *, registry: DomainPackRegistry, root: Path) -> None:
        self.registry = registry
        self.root = Path(root)
        self.planner = CapabilityPlanner(registry)

    def definitions(self, *, domain_id: str) -> list[PackEvaluationDefinition]:
        return self.registry.evaluations(domain_id=domain_id)

    def run_all(self, *, domain_id: str) -> list[PackEvaluationResult]:
        return [
            self.run_one(domain_id=domain_id, evaluation_id=definition.evaluation_id)
            for definition in self.definitions(domain_id=domain_id)
        ]

    def release_check(self, *, domain_id: str) -> list[PackEvaluationResult]:
        """Run every declared evaluation and fail closed for Pack release."""
        results = self.run_all(domain_id=domain_id)
        failed = [result for result in results if not result.passed]
        if failed:
            details = "; ".join(
                f"{result.evaluation_id}: {', '.join(result.failures) or 'failed'}"
                for result in failed
            )
            raise PackEvaluationError(
                f"Pack release evaluation failed for {domain_id}: {details}"
            )
        return results

    def run_one(self, *, domain_id: str, evaluation_id: str) -> PackEvaluationResult:
        definition = next(
            (
                item
                for item in self.definitions(domain_id=domain_id)
                if item.evaluation_id == evaluation_id
            ),
            None,
        )
        if definition is None:
            raise KeyError(f"unknown Pack evaluation: {domain_id}:{evaluation_id}")
        manifest = self.registry.get(domain_id)
        failures = self._validate_declaration(manifest, definition)
        if definition.mode == "contract" or failures:
            return PackEvaluationResult(
                evaluation_id=definition.evaluation_id,
                domain_id=domain_id,
                passed=not failures,
                executed=False,
                status="not_run",
                failures=failures,
                metadata={"mode": definition.mode},
            )

        run_id = f"eval-{definition.evaluation_id}-{generate_ulid()}"
        run_root = self.root / domain_id / definition.evaluation_id / run_id
        request = CapabilityPlanningRequest(
            domain_id=domain_id,
            objective=str(definition.objective),
            available_artifact_types=list(definition.available_artifact_types),
            required_artifact_types=list(definition.required_artifact_types),
            allowed_tools=list(manifest.governance.allowed_tools),
            allowed_permissions=list(manifest.governance.allowed_permissions),
            token_budget=manifest.governance.token_budget,
            cost_budget=manifest.governance.cost_budget,
            metadata={
                **dict(definition.metadata),
                "entry_protocol": "pack_evaluation",
                "evaluation_id": definition.evaluation_id,
                "intent": definition.intent,
            },
        )
        try:
            graph = self.planner.build_graph(request)
            events: list[dict[str, Any]] = []
            runner = ExecutionGraphRunner(
                registry=self.registry,
                run_root=run_root,
                event_callback=events.append,
                failure_injections=[
                    item.model_dump(mode="json")
                    for item in definition.failure_injections
                ],
            )
            execution_error: Exception | None = None
            try:
                result = runner.run(
                    graph=graph,
                    run_id=run_id,
                    thread_id=f"thread-{run_id}",
                    request_id=f"request-{run_id}",
                    seed_artifacts=list(definition.seed_artifacts),
                )
            except Exception as exc:
                execution_error = exc
                persisted = runner._load_snapshot()
                if persisted is None:
                    raise
                result = ExecutionRunResult(snapshot=persisted)
            events.extend(read_jsonl(run_root / "execution_events.jsonl"))
            event_types = list(
                dict.fromkeys(
                    str(event.get("event_type") or "")
                    for event in events
                    if str(event.get("event_type") or "")
                )
            )
            gate_verdicts = {
                evaluation.gate_id: evaluation.verdict
                for evaluation in result.snapshot.gate_evaluations
            }
            failures.extend(
                self._assert_result(
                    definition=definition,
                    result=result,
                    runner=runner,
                    event_types=event_types,
                    gate_verdicts=gate_verdicts,
                )
            )
            if execution_error is not None and result.snapshot.status != definition.expected_status:
                failures.append(
                    f"evaluation execution raised {execution_error.__class__.__name__}: {execution_error}"
                )
            return PackEvaluationResult(
                evaluation_id=definition.evaluation_id,
                domain_id=domain_id,
                passed=not failures,
                executed=True,
                status=result.snapshot.status,
                failures=failures,
                gate_verdicts=gate_verdicts,
                event_types=event_types,
                artifact_ids=list(result.artifact_ids),
                metadata={"run_id": run_id, "run_root": str(run_root)},
            )
        except Exception as exc:
            failures.append(f"evaluation execution failed: {exc.__class__.__name__}: {exc}")
            return PackEvaluationResult(
                evaluation_id=definition.evaluation_id,
                domain_id=domain_id,
                passed=False,
                executed=True,
                status="failed",
                failures=failures,
                metadata={"run_id": run_id, "run_root": str(run_root)},
            )

    def _validate_declaration(self, manifest, definition) -> list[str]:
        failures: list[str] = []
        declared_artifacts = {item.artifact_type for item in manifest.artifact_types}
        declared_capabilities = {item.capability for item in manifest.capabilities}
        declared_tools = set(manifest.governance.allowed_tools)
        declared_gates = {item.gate_id: item for item in manifest.quality_gates}
        failures.extend(
            f"required artifact is not declared: {artifact_type}"
            for artifact_type in definition.required_artifact_types
            if artifact_type not in declared_artifacts
        )
        if definition.required_gate_id:
            gate = declared_gates.get(definition.required_gate_id)
            if gate is None:
                failures.append(f"required gate is not declared: {definition.required_gate_id}")
            elif (
                definition.required_artifact_types
                and gate.artifact_type not in definition.required_artifact_types
            ):
                failures.append(
                    f"required gate artifact does not match evaluation output: {definition.required_gate_id}"
                )
        for injection in definition.failure_injections:
            if injection.kind == "capability" and injection.target not in declared_capabilities:
                failures.append(f"injected capability is not declared: {injection.target}")
            if injection.kind == "tool" and injection.target not in declared_tools:
                failures.append(f"injected Tool is not allowed by Pack governance: {injection.target}")
            if injection.kind == "gate" and injection.target not in declared_gates:
                failures.append(f"injected gate is not declared: {injection.target}")
        return failures

    @staticmethod
    def _assert_result(
        *,
        definition: PackEvaluationDefinition,
        result,
        runner: ExecutionGraphRunner,
        event_types: list[str],
        gate_verdicts: dict[str, str],
    ) -> list[str]:
        failures: list[str] = []
        if result.snapshot.status != definition.expected_status:
            failures.append(
                f"expected status {definition.expected_status}, got {result.snapshot.status}"
            )
        if definition.required_gate_id and definition.required_gate_id not in gate_verdicts:
            failures.append(f"required gate was not evaluated: {definition.required_gate_id}")
        for gate_id, expected in definition.expected_gate_verdicts.items():
            actual = gate_verdicts.get(gate_id)
            if actual != expected:
                failures.append(f"expected gate {gate_id}={expected}, got {actual}")
        missing_events = set(definition.required_event_types) - set(event_types)
        if missing_events:
            failures.append(f"missing required events: {sorted(missing_events)}")
        forbidden_events = set(definition.forbidden_event_types) & set(event_types)
        if forbidden_events:
            failures.append(f"forbidden events were emitted: {sorted(forbidden_events)}")
        capabilities = {
            str(node.capability)
            for node in result.snapshot.graph.nodes
            if node.node_kind == "subagent"
        }
        missing_fallbacks = set(definition.expected_fallback_capabilities) - capabilities
        if missing_fallbacks:
            failures.append(f"expected fallback capabilities missing: {sorted(missing_fallbacks)}")
        if definition.required_artifact_types:
            active_types = {
                artifact.artifact_type
                for artifact in runner.board.list_artifacts(
                    run_id=result.snapshot.run_id,
                    status="active",
                )
            }
            missing_artifacts = set(definition.required_artifact_types) - active_types
            if result.snapshot.status == "completed" and missing_artifacts:
                failures.append(f"required active artifacts missing: {sorted(missing_artifacts)}")
        return failures


__all__ = ["PackEvaluationError", "PackEvaluationRunner"]
