from __future__ import annotations

from dataclasses import dataclass
from inspect import Parameter, signature
from math import ceil
from types import SimpleNamespace
from threading import RLock
from typing import Any

from research_system.runtime.domain_packs.registry import DomainPackRegistry
from research_system.runtime.orchestration.subagent_discovery_store import (
    SubagentDiscoveryStore,
)
from research_system.runtime.orchestration.subagent_runtime_packaging import (
    SubagentRuntimePackagingSupport,
)
from research_system.runtime.platform.skill_runtime import SkillRuntime
from research_system.schemas.artifacts import ArtifactDraft
from research_system.schemas.domain_pack import CapabilityDefinition
from research_system.schemas.execution_runtime import NodeExecutionOutput
from research_system.schemas.subagents import (
    RoleContract,
    SubagentActivationEntry,
    SubagentDiscoveryTicket,
)


@dataclass(frozen=True)
class _RuntimeRoleBinding:
    domain_id: str
    capability: str
    base: CapabilityDefinition
    alias: CapabilityDefinition
    activation: SubagentActivationEntry
    ticket: SubagentDiscoveryTicket
    runtime: Any | None = None


class RuntimeRoleExecutorAdapter:
    """Adapt a materialized runtime alias to the GraphRunner executor contract."""

    def __init__(self, binding: _RuntimeRoleBinding) -> None:
        self.binding = binding
        timeout = binding.alias.timeout_seconds
        self.supports_timeout = (
            timeout is None
            or self._supports_cancellation(binding.runtime)
            or self._has_native_timeout(binding.runtime)
        )

    def __call__(self, context: Any) -> NodeExecutionOutput:
        runtime = self.binding.runtime
        if runtime is None:
            raise RuntimeError(
                f"runtime role has no materialized executor: {self.binding.alias.subagent_type}"
            )
        arguments = dict(getattr(context.node, "inputs", {}) or {})
        for artifact in list(getattr(context, "input_artifacts", []) or []):
            content = getattr(artifact, "content", None)
            if isinstance(content, dict):
                arguments.update(content)
        arguments.setdefault(
            "query",
            str(
                getattr(context.node, "objective", None)
                or getattr(context.graph, "objective", None)
                or ""
            ),
        )
        arguments = self._validate_input(runtime, arguments)
        if self._supports_cancellation(runtime):
            arguments["cancellation"] = getattr(context, "cancellation", None)
        raw_output = runtime.run(**arguments)
        if isinstance(raw_output, NodeExecutionOutput):
            return raw_output
        if isinstance(raw_output, dict) and isinstance(raw_output.get("artifacts"), list):
            return NodeExecutionOutput.model_validate(raw_output)
        if not isinstance(raw_output, dict):
            raw_output = {"result": raw_output}
        produces = list(getattr(context.node, "produces", []) or [])
        if len(produces) != 1:
            raise RuntimeError(
                "runtime role output must provide explicit artifacts when the capability "
                f"declares {len(produces)} output ports: {self.binding.alias.subagent_type}"
            )
        port = produces[0]
        confidence = raw_output.get("confidence")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            confidence = None
        return NodeExecutionOutput(
            artifacts=[
                ArtifactDraft(
                    artifact_type=port.artifact_type,
                    schema_version=port.schema_version,
                    content=dict(raw_output),
                    source_refs=[
                        {"type": "runtime_role", "id": self.binding.activation.activation_id},
                        {"type": "request", "id": context.request_id},
                    ],
                    parent_artifact_ids=[
                        str(item.artifact_id)
                        for item in list(getattr(context, "input_artifacts", []) or [])
                    ],
                    confidence=confidence,
                    metadata={
                        "runtime_role_alias": self.binding.alias.subagent_type,
                        "runtime_role_activation_id": self.binding.activation.activation_id,
                    },
                )
            ]
        )

    @staticmethod
    def _validate_input(runtime: Any, arguments: dict[str, Any]) -> dict[str, Any]:
        model = getattr(runtime, "input_model", None)
        validator = getattr(model, "model_validate", None)
        if not callable(validator):
            return arguments
        validated = validator(arguments)
        dumper = getattr(validated, "model_dump", None)
        return (
            dict(dumper(mode="json", exclude_none=True))
            if callable(dumper)
            else arguments
        )

    @staticmethod
    def _supports_cancellation(runtime: Any) -> bool:
        try:
            parameters = signature(runtime.run).parameters
        except (TypeError, ValueError):
            return False
        parameter = parameters.get("cancellation")
        return parameter is not None and parameter.kind in {
            Parameter.POSITIONAL_OR_KEYWORD,
            Parameter.KEYWORD_ONLY,
        }

    @staticmethod
    def _has_native_timeout(runtime: Any) -> bool:
        try:
            timeout = float(getattr(runtime, "timeout_seconds", 0) or 0)
        except (TypeError, ValueError):
            return False
        return timeout > 0


class RuntimeRoleOverlay:
    """Project approved runtime aliases into Pack capabilities and executors.

    Aliases are opt-in at planning time through ``runtime_role_aliases``. This
    keeps existing graphs deterministic while giving the canonical planner and
    GraphRunner one governed path for an explicitly selected runtime role.
    """

    def __init__(
        self,
        registry: DomainPackRegistry,
        discovery_store: SubagentDiscoveryStore,
        *,
        settings: Any,
        runtime_packaging: SubagentRuntimePackagingSupport | None = None,
        skill_runtime: Any | None = None,
    ) -> None:
        self.registry = registry
        self.discovery_store = discovery_store
        self.settings = settings
        self.runtime_packaging = runtime_packaging or SubagentRuntimePackagingSupport(
            SimpleNamespace(settings=settings, orchestrator_error_cls=ValueError)
        )
        self.skill_runtime = skill_runtime
        self._lock = RLock()
        self._bindings: dict[tuple[str, str, str], _RuntimeRoleBinding] = {}
        self._errors: dict[str, str] = {}
        self.refresh()

    def refresh(self) -> None:
        with self._lock:
            self._bindings = {}
            self._errors = {}
            for activation in self.discovery_store.activated_alias_entries():
                located = self.discovery_store.find(activation.ticket_id)
                if located is None:
                    continue
                ticket, _ = located
                capability = str(ticket.capability_gap.capability or "").strip()
                if not capability:
                    continue
                for manifest in self.registry.list():
                    bases = self.registry.capabilities(
                        domain_id=manifest.domain_id,
                        capability=capability,
                        version=manifest.version,
                    )
                    for base in bases:
                        runtime = None
                        if activation.runtime_registration is not None:
                            try:
                                if self.skill_runtime is None:
                                    self.skill_runtime = SkillRuntime(self.settings)
                                runtime = self.runtime_packaging.materialize_runtime_subagent(
                                    activation_entry=activation,
                                    skill_runtime=self.skill_runtime,
                                )
                            except Exception as exc:
                                self._errors[activation.subagent_type] = (
                                    f"{exc.__class__.__name__}: {exc}"
                                )
                                continue
                            if runtime is None:
                                self._errors[activation.subagent_type] = (
                                    "runtime registration could not be materialized"
                                )
                                continue
                        alias = self._alias_definition(base, activation, runtime=runtime)
                        binding = _RuntimeRoleBinding(
                            domain_id=manifest.domain_id,
                            capability=base.capability,
                            base=base,
                            alias=alias,
                            activation=activation,
                            ticket=ticket,
                            runtime=runtime,
                        )
                        self._bindings[(manifest.domain_id, base.capability, alias.subagent_type)] = binding

    def capabilities(
        self,
        *,
        domain_id: str,
        capability: str | None = None,
        produces: str | None = None,
        version: str | None = None,
    ) -> list[CapabilityDefinition]:
        with self._lock:
            rows = self.registry.capabilities(
                domain_id=domain_id,
                capability=capability,
                produces=produces,
                version=version,
            )
            aliases = [
                binding.alias
                for binding in self._bindings.values()
                if binding.domain_id == domain_id
                and (version is None or binding.base.capability_version == binding.alias.capability_version)
                and (capability is None or binding.alias.capability == capability)
                and (produces is None or produces in {port.artifact_type for port in binding.alias.produces})
            ]
            return [*rows, *aliases]

    def get(self, domain_id: str, *, version: str | None = None):
        """Expose the manifest contract required by planner and GraphRunner."""
        return self.registry.get(domain_id, version=version)

    def artifact_schema(
        self,
        *,
        domain_id: str,
        artifact_type: str,
        version: str | None = None,
    ) -> dict[str, Any]:
        """Keep artifact validation owned by the underlying Pack Registry."""
        return self.registry.artifact_schema(
            domain_id=domain_id,
            artifact_type=artifact_type,
            version=version,
        )

    def resolve_executor(
        self,
        *,
        domain_id: str,
        capability: str,
        version: str | None = None,
        subagent_type: str | None = None,
    ):
        normalized_subagent_type = str(subagent_type or "").strip()
        if normalized_subagent_type:
            with self._lock:
                binding = self._bindings.get((domain_id, capability, normalized_subagent_type))
            if binding is not None:
                if binding.runtime is not None:
                    return RuntimeRoleExecutorAdapter(binding)
                delegate = str(binding.activation.delegate_subagent_type or "").strip()
                if delegate:
                    candidates = [
                        item
                        for item in self.registry.capabilities(
                            domain_id=domain_id,
                            capability=capability,
                            version=version,
                        )
                        if item.subagent_type == delegate
                    ]
                    if not candidates or not candidates[0].implementation:
                        raise KeyError(
                            f"runtime role delegate is not executable: {normalized_subagent_type}"
                        )
                    return self.registry.resolve_callable(
                        domain_id=domain_id,
                        implementation=str(candidates[0].implementation),
                        version=version,
                    )
                error = self._errors.get(normalized_subagent_type)
                raise RuntimeError(
                    error or f"runtime role is not executable: {normalized_subagent_type}"
                )
        return self.registry.resolve_executor(
            domain_id=domain_id,
            capability=capability,
            version=version,
            subagent_type=normalized_subagent_type or None,
        )

    def role_contracts(self) -> list[RoleContract]:
        with self._lock:
            return [
                RoleContract(
                    role_id=binding.alias.subagent_type,
                    role_version=binding.activation.activation_id,
                    display_name=(
                        binding.activation.catalog_entry.summary
                        or binding.alias.subagent_type
                    ),
                    objective=binding.alias.description or binding.alias.capability,
                    input_artifact_types=[
                        port.artifact_type for port in binding.alias.consumes
                    ],
                    output_artifact_types=[
                        port.artifact_type for port in binding.alias.produces
                    ],
                    required_capabilities=[binding.alias.capability],
                    allowed_tools=list(binding.alias.tools),
                    max_model_turns=1,
                    timeout_seconds=max(
                        1,
                        ceil(binding.alias.timeout_seconds or 30),
                    ),
                    failure_mode=(
                        "continue" if binding.alias.failure_mode == "isolate" else binding.alias.failure_mode
                    ),
                    source="runtime",
                )
                for binding in self._bindings.values()
            ]

    @staticmethod
    def _alias_definition(
        base: CapabilityDefinition,
        activation: SubagentActivationEntry,
        *,
        runtime: Any | None,
    ) -> CapabilityDefinition:
        alias_name = str(activation.subagent_type).strip()
        catalog = activation.catalog_entry
        runtime_policy = getattr(runtime, "runtime_policy", None)
        allow_parallel = base.parallel_safe and bool(
            getattr(runtime_policy, "allow_parallel", catalog.allow_parallel)
        )
        max_concurrency = getattr(runtime_policy, "max_concurrency", None)
        if max_concurrency is None:
            max_concurrency = catalog.max_concurrency
        concurrency_limits = [
            value
            for value in (base.max_concurrency, catalog.max_concurrency, max_concurrency)
            if value is not None
        ]
        max_concurrency = min(concurrency_limits) if concurrency_limits else None
        retry_limits = [
            value
            for value in (
                base.max_retries,
                getattr(runtime_policy, "max_retries", None),
            )
            if value is not None
        ]
        max_retries = min(retry_limits) if retry_limits else None
        timeout_values = [
            value
            for value in (
                getattr(activation.runtime_registration, "timeout_seconds", None),
                getattr(runtime_policy, "timeout_seconds", None),
                base.timeout_seconds,
            )
            if value is not None and float(value) > 0
        ]
        side_effect_levels = {
            "none": 0,
            "read_only": 1,
            "write_local": 2,
            "write_external": 3,
        }
        side_effect_level = max(
            (base.side_effect_level, catalog.side_effect_level),
            key=side_effect_levels.get,
        )
        cost_levels = {"low": 0, "medium": 1, "high": 2}
        cost_class = max(
            (base.cost_class, catalog.cost_class or base.cost_class),
            key=cost_levels.get,
        )
        latency_class = max(
            (base.latency_class, catalog.latency_class or base.latency_class),
            key=cost_levels.get,
        )
        return base.model_copy(
            update={
                "subagent_type": alias_name,
                "description": activation.catalog_entry.summary or base.description,
                "implementation": f"runtime_alias:{activation.activation_id}",
                "parallel_safe": allow_parallel,
                "max_concurrency": max_concurrency,
                "max_retries": max_retries if max_retries is not None else base.max_retries,
                "timeout_seconds": min(timeout_values) if timeout_values else None,
                "cost_class": cost_class,
                "latency_class": latency_class,
                "requires_llm": bool(base.requires_llm or catalog.requires_llm),
                "side_effect_level": side_effect_level,
                "tags": [
                    *list(base.tags),
                    "runtime_alias",
                    f"runtime_alias:{alias_name}",
                    f"runtime_activation:{activation.activation_id}",
                ],
            },
        )


__all__ = ["RuntimeRoleExecutorAdapter", "RuntimeRoleOverlay"]
