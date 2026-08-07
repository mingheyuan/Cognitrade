from __future__ import annotations

from datetime import datetime, timedelta, timezone

from research_system.runtime.domain_packs.registry import DomainPackRegistry
from research_system.schemas.domain_pack import (
    CapabilityDefinition,
    CapabilityPlanningRequest,
)
from research_system.schemas.execution_graph import (
    ArtifactPort,
    ExecutionGraph,
    ExecutionGraphPatch,
    ExecutionGraphPatchOperation,
    ExecutionNode,
)


class CapabilityPlanningError(RuntimeError):
    pass


class CapabilityPlanner:
    def __init__(self, registry: DomainPackRegistry) -> None:
        self.registry = registry

    def build_lazy_graph(self, request: CapabilityPlanningRequest) -> ExecutionGraph:
        """Create the root compound and defer leaf selection to a safe point."""
        requested_version = str(request.metadata.get("domain_version") or "").strip() or None
        manifest = self.registry.get(request.domain_id, version=requested_version)
        required_outputs = self._required_artifact_types(manifest, request)
        metadata = dict(request.metadata or {})
        intent = str(metadata.get("intent") or "").strip()
        intent_inputs = dict(metadata.get("intent_inputs") or {})
        profile_intents = {
            str(item).strip()
            for item in (manifest.planner_policies.get("profile_intents") or [])
            if str(item).strip()
        }
        is_profile_intent = intent in profile_intents or intent.startswith("domain:")
        intent_profile_defaults = dict(manifest.planner_policies.get("intent_profiles") or {})
        profile_output_intents = {
            str(item).strip()
            for item in (manifest.planner_policies.get("profile_output_intents") or [])
            if str(item).strip()
        }
        selected_profile = str(
            intent_inputs.get("research_profile")
            or metadata.get("research_profile")
            or (
                intent_profile_defaults.get(intent)
                or manifest.planner_policies.get("default_profile")
                if is_profile_intent
                else ""
            )
            or ""
        ).strip().lower()
        self._require_explicit_profile(
            manifest=manifest,
            intent=intent,
            selected_profile=selected_profile,
            explicit_profile=str(
                intent_inputs.get("research_profile")
                or metadata.get("research_profile")
                or ""
            ).strip().lower(),
        )
        profile_policy = dict(
            (manifest.planner_policies.get("research_profiles") or {}).get(selected_profile)
            or {}
        )
        intent_inputs_methods = intent_inputs.get("requested_methods") or intent_inputs.get("methodologies")
        if isinstance(intent_inputs_methods, str):
            intent_inputs_methods = [intent_inputs_methods]
        use_profile_defaults = intent in profile_output_intents or intent.startswith("domain:")
        selected_method_ids = [
            str(item).strip().lower()
            for item in list(
                intent_inputs_methods
                or (profile_policy.get("methods") if use_profile_defaults else [])
                or []
            )
            if str(item).strip()
        ]
        metadata.update(
            {
                "domain_version": manifest.version,
                "available_artifact_types": sorted(set(request.available_artifact_types)),
                "available_artifact_refs": [
                    dict(ref) for ref in request.available_artifact_refs
                ],
                "required_artifact_types": required_outputs,
                "research_profile": selected_profile or None,
                "requested_research_profile": str(
                    intent_inputs.get("research_profile")
                    or metadata.get("research_profile")
                    or ""
                ).strip().lower()
                or None,
                "selected_research_profile": selected_profile or None,
                "profile_selection_reason": self._profile_selection_reason(
                    metadata=metadata,
                    intent=intent,
                    selected_profile=selected_profile,
                    profile_intents=profile_intents,
                ),
                "profile_intents": sorted(profile_intents),
                "selected_method_ids": selected_method_ids,
                "profile_policy": profile_policy,
                "progress_policy": dict(manifest.planner_policies.get("progress_policy") or {}),
                "planner": "capability_contract_lazy",
                "planning_request": request.model_dump(mode="json"),
            }
        )
        required_gate_ids = list(
            dict.fromkeys(
                [
                    gate.gate_id
                    for gate in manifest.quality_gates
                    if gate.blocking and gate.artifact_type in required_outputs
                ]
            )
        )
        method_catalog = manifest.method_definitions()
        method_gate_ids = [
            str(gate_id).strip()
            for method in method_catalog.values()
            if any(output in required_outputs for output in method.output_artifact_types)
            and (not selected_method_ids or method.method_id in selected_method_ids)
            for gate_id in method.required_gate_ids
            if str(gate_id).strip()
        ]
        required_gate_ids = list(dict.fromkeys([*required_gate_ids, *method_gate_ids]))
        governance = manifest.governance.model_copy(
            update={
                "required_gate_ids": required_gate_ids,
                "token_budget": request.token_budget or manifest.governance.token_budget,
                "timeout_seconds": (
                    profile_policy.get("max_duration_seconds")
                    or manifest.governance.timeout_seconds
                ),
                "cost_budget": (
                    request.cost_budget
                    if request.cost_budget is not None
                    else manifest.governance.cost_budget
                ),
            }
        )
        return ExecutionGraph(
            domain_id=manifest.domain_id,
            objective=request.objective,
            intent=str(metadata.get("intent") or "") or None,
            strategy="dynamic",
            governance=governance,
            nodes=[
                ExecutionNode(
                    node_id="domain_task",
                    node_kind="compound",
                    capability=f"{manifest.domain_id}.task",
                    objective=request.objective,
                    expansion_policy="lazy",
                    metadata={
                        "domain_id": manifest.domain_id,
                        "domain_version": manifest.version,
                        "planning_request": request.model_dump(mode="json"),
                    },
                )
            ],
            metadata=metadata,
        )

    def build_graph(self, request: CapabilityPlanningRequest) -> ExecutionGraph:
        requested_version = str(request.metadata.get("domain_version") or "").strip() or None
        manifest = self.registry.get(request.domain_id, version=requested_version)
        intent = str(request.metadata.get("intent") or "").strip()
        intent_inputs = dict(request.metadata.get("intent_inputs") or {})
        profile_intents = {
            str(item).strip()
            for item in (manifest.planner_policies.get("profile_intents") or [])
            if str(item).strip()
        }
        is_profile_intent = intent in profile_intents or intent.startswith("domain:")
        intent_profile_defaults = dict(manifest.planner_policies.get("intent_profiles") or {})
        profile_output_intents = {
            str(item).strip()
            for item in (manifest.planner_policies.get("profile_output_intents") or [])
            if str(item).strip()
        }
        explicit_profile = str(
            intent_inputs.get("research_profile")
            or request.metadata.get("research_profile")
            or ""
        ).strip().lower()
        requested_profile = str(
            explicit_profile
            or (
                intent_profile_defaults.get(intent)
                or manifest.planner_policies.get("default_profile")
                if is_profile_intent
                else ""
            )
            or ""
        ).strip().lower()
        self._require_explicit_profile(
            manifest=manifest,
            intent=intent,
            selected_profile=requested_profile,
            explicit_profile=explicit_profile,
        )
        profile_policy = dict(
            (manifest.planner_policies.get("research_profiles") or {}).get(requested_profile)
            or {}
        )
        if profile_policy:
            request = request.model_copy(
                update={
                    "max_cost_class": profile_policy.get("max_cost_class", request.max_cost_class),
                    "max_latency_class": profile_policy.get("max_latency_class", request.max_latency_class),
                    "token_budget": profile_policy.get("token_budget", request.token_budget),
                    "allowed_capability_tags": list(
                        profile_policy.get("allowed_capability_tags")
                        or request.allowed_capability_tags
                    ),
                    "metadata": {
                        **dict(request.metadata),
                        "research_profile": requested_profile,
                        "profile_policy": profile_policy,
                    },
                }
            )
        intent_definition = next(
            (item for item in manifest.intake.intents if item.intent_id == intent),
            None,
        )
        intent_capabilities = manifest.planner_policies.get("intent_capabilities") or {}
        policy_preferred = list(
            (intent_definition.candidate_capabilities if intent_definition else None)
            or intent_capabilities.get(intent)
            or []
        )
        if policy_preferred:
            request = request.model_copy(
                update={
                    "preferred_capabilities": list(
                        dict.fromkeys([*request.preferred_capabilities, *policy_preferred])
                    )
                }
            )
        method_catalog = manifest.method_definitions()
        requested_methods = intent_inputs.get("requested_methods") or intent_inputs.get("methodologies")
        if isinstance(requested_methods, str):
            requested_methods = [requested_methods]
        requested_methods = [
            str(item).strip().lower()
            for item in list(requested_methods or [])
            if str(item).strip()
        ]
        profile_methods = [
            str(item).strip().lower()
            for item in list(
                (profile_policy.get("methods") or [])
                if (intent in profile_output_intents or intent.startswith("domain:"))
                else []
            )
            if str(item).strip()
        ]
        selected_method_ids = requested_methods or profile_methods
        unknown_methods = sorted(set(requested_methods) - set(method_catalog))
        if unknown_methods:
            raise CapabilityPlanningError(
                f"unknown Pack method contract(s): {', '.join(unknown_methods)}"
            )
        method_capabilities = [method_catalog[item].capability for item in requested_methods]
        if method_capabilities:
            request = request.model_copy(
                update={
                    "preferred_capabilities": list(
                        dict.fromkeys([*request.preferred_capabilities, *method_capabilities])
                    )
                }
            )
        available = set(request.available_artifact_types)
        available_refs = [dict(ref) for ref in request.available_artifact_refs]
        required_outputs = self._required_artifact_types(manifest, request)
        selected_by_type: dict[str, str] = {}
        selected_by_subagent: dict[str, ExecutionNode] = {}
        selection_decisions: list[dict[str, object]] = []
        resolving: set[str] = set()

        def resolve_artifact(artifact_type: str) -> str | None:
            if artifact_type in available:
                return None
            if artifact_type in selected_by_type:
                return selected_by_type[artifact_type]
            if artifact_type in resolving:
                raise CapabilityPlanningError(f"capability artifact cycle: {artifact_type}")
            resolving.add(artifact_type)
            try:
                candidates = self._candidate_producers(
                    request=request,
                    artifact_type=artifact_type,
                )
                if not candidates:
                    raise CapabilityPlanningError(
                        f"no capability can produce required artifact: {artifact_type}"
                    )
                capability = candidates[0]
                selection_decisions.append(
                    {
                        "artifact_type": artifact_type,
                        "selected_capability": capability.capability,
                        "selected_subagent_type": capability.subagent_type,
                        "candidates": [
                            {
                                "capability": item.capability,
                                "subagent_type": item.subagent_type,
                                "cost_class": item.cost_class,
                                "latency_class": item.latency_class,
                                "reliability": item.reliability,
                                "preferred": item.capability in set(request.preferred_capabilities),
                            }
                            for item in candidates
                        ],
                        "reason": "selected by capability constraints, preference, cost, latency and reliability",
                    }
                )
                existing = selected_by_subagent.get(capability.subagent_type)
                if existing is not None:
                    for produced in capability.produces:
                        selected_by_type.setdefault(produced.artifact_type, existing.node_id)
                    return existing.node_id

                dependencies: list[str] = []
                consumes: list[ArtifactPort] = []
                for consumed in capability.consumes:
                    required_input = consumed.required or consumed.artifact_type in required_outputs
                    selector = dict(consumed.selector or {})
                    if required_input:
                        producer_node_id = resolve_artifact(consumed.artifact_type)
                        if producer_node_id and producer_node_id not in dependencies:
                            dependencies.append(producer_node_id)
                        if producer_node_id:
                            selector["producer_node_id"] = producer_node_id
                    consumes.append(
                        consumed.model_copy(
                            update={
                                "required": required_input,
                                "selector": selector,
                            },
                            deep=True,
                        )
                    )
                node_id = self._node_id(capability.subagent_type, selected_by_subagent)
                node = ExecutionNode(
                    node_id=node_id,
                    node_kind="subagent",
                    executor=capability.subagent_type,
                    capability=capability.capability,
                    parent_node_id="domain_task",
                    objective=capability.description or capability.capability,
                    inputs=dict(request.metadata.get("intent_inputs") or {}),
                    input_artifact_ids=self._select_available_artifact_ids(
                        capability.consumes,
                        available_refs,
                    ),
                    consumes=consumes,
                    produces=[port.model_copy(deep=True) for port in capability.produces],
                    depends_on=dependencies,
                    failure_mode=capability.failure_mode,
                    max_attempts=(
                        min(
                            manifest.governance.max_retries_per_node,
                            capability.max_retries,
                        )
                        + 1
                        if capability.max_retries is not None
                        else manifest.governance.max_retries_per_node + 1
                    ),
                    replayable=capability.replayable,
                    cache_scope=capability.cache_scope,
                    cache_key=":".join(
                        value
                        for value in (
                            capability.capability,
                            capability.capability_version,
                            str(capability.methodology_version or ""),
                            str(capability.policy_version or ""),
                            str(request.metadata.get("research_profile") or ""),
                        )
                        if value
                    ),
                    idempotency_key=capability.idempotency_key_template,
                    parallel_safe=capability.parallel_safe,
                    max_concurrency=capability.max_concurrency,
                    tools=list(capability.tools),
                    permissions=list(capability.permissions),
                    estimated_tokens=capability.estimated_tokens,
                    estimated_cost=capability.estimated_cost,
                    side_effect_level=capability.side_effect_level,
                    metadata={
                        "domain_id": manifest.domain_id,
                        "domain_version": manifest.version,
                        "tools": list(capability.tools),
                        "permissions": list(capability.permissions),
                        "cost_class": capability.cost_class,
                        "latency_class": capability.latency_class,
                        "reliability": capability.reliability,
                        "side_effect_level": capability.side_effect_level,
                        "requires_llm": capability.requires_llm,
                        "implementation": capability.implementation,
                        "max_retries": capability.max_retries,
                        "timeout_seconds": capability.timeout_seconds,
                        "capability_version": capability.capability_version,
                        "freshness_ttl_seconds": capability.freshness_ttl_seconds,
                        "data_period_policy": dict(capability.data_period_policy),
                        "methodology_version": capability.methodology_version,
                        "policy_version": capability.policy_version,
                        "research_profile": request.metadata.get("research_profile"),
                        "method_contracts": [
                            {
                                "method_id": method.method_id,
                                "version": method.version,
                                "required_inputs": [
                                    item.model_dump(mode="json")
                                    for item in method.required_inputs
                                ],
                                "required_gate_ids": list(method.required_gate_ids),
                                "allowed_quality_statuses": list(method.allowed_quality_statuses),
                                "result_equivalence": dict(method.result_equivalence),
                            }
                            for method in method_catalog.values()
                            if method.capability == capability.capability
                            and (not selected_method_ids or method.method_id in selected_method_ids)
                            and any(
                                output.artifact_type in required_outputs
                                for output in capability.produces
                            )
                        ],
                        "method_input_requirements": [
                            requirement.model_dump(mode="json")
                            for method in method_catalog.values()
                            if method.capability == capability.capability
                            and (not selected_method_ids or method.method_id in selected_method_ids)
                            and any(
                                output.artifact_type in required_outputs
                                for output in capability.produces
                            )
                            for requirement in method.required_inputs
                        ],
                        "method_gate_ids": list(
                            dict.fromkeys(
                                gate_id
                                for method in method_catalog.values()
                                if method.capability == capability.capability
                                and (not selected_method_ids or method.method_id in selected_method_ids)
                                and any(
                                    output.artifact_type in required_outputs
                                    for output in capability.produces
                                )
                                for gate_id in method.required_gate_ids
                            )
                        ),
                    },
                )
                selected_by_subagent[capability.subagent_type] = node
                for produced in capability.produces:
                    selected_by_type[produced.artifact_type] = node_id
                return node_id
            finally:
                resolving.remove(artifact_type)

        for artifact_type in required_outputs:
            resolve_artifact(artifact_type)

        nodes = [
            ExecutionNode(
                node_id="domain_task",
                node_kind="compound",
                capability=f"{manifest.domain_id}.task",
                objective=request.objective,
                expansion_policy="eager",
                metadata={
                    "domain_id": manifest.domain_id,
                    "domain_version": manifest.version,
                },
            ),
            *selected_by_subagent.values(),
        ]
        required_gate_ids = list(
            dict.fromkeys(
                [
                    gate.gate_id
                    for gate in manifest.quality_gates
                    if gate.blocking and gate.artifact_type in required_outputs
                ]
            )
        )
        method_gate_ids = [
            str(gate_id).strip()
            for node in nodes
            if node.node_kind == "subagent"
            for gate_id in list(node.metadata.get("method_gate_ids") or [])
            if str(gate_id).strip()
        ]
        required_gate_ids = list(dict.fromkeys([*required_gate_ids, *method_gate_ids]))
        governance = manifest.governance.model_copy(
            update={
                "required_gate_ids": required_gate_ids,
                "token_budget": request.token_budget or manifest.governance.token_budget,
                "timeout_seconds": (
                    profile_policy.get("max_duration_seconds")
                    or manifest.governance.timeout_seconds
                ),
                "cost_budget": (
                    request.cost_budget
                    if request.cost_budget is not None
                    else manifest.governance.cost_budget
                ),
            }
        )
        return ExecutionGraph(
            domain_id=manifest.domain_id,
            objective=request.objective,
            strategy="dynamic",
            nodes=nodes,
            governance=governance,
            metadata={
                "domain_version": manifest.version,
                "available_artifact_types": sorted(available),
                "required_artifact_types": required_outputs,
                "planner": "capability_contract",
                "capability_selections": selection_decisions,
                "requested_research_profile": explicit_profile or None,
                "selected_research_profile": requested_profile or None,
                "profile_selection_reason": self._profile_selection_reason(
                    metadata={
                        **dict(request.metadata),
                        "requested_research_profile": explicit_profile or None,
                    },
                    intent=intent,
                    selected_profile=requested_profile,
                    profile_intents=profile_intents,
                ),
                "profile_intents": sorted(profile_intents),
                "profile_policy": profile_policy,
                "progress_policy": dict(manifest.planner_policies.get("progress_policy") or {}),
                "requested_methods": requested_methods,
                "selected_method_ids": selected_method_ids,
                **dict(request.metadata or {}),
            },
        )

    @staticmethod
    def _require_explicit_profile(
        *,
        manifest,
        intent: str,
        selected_profile: str,
        explicit_profile: str,
    ) -> None:
        """Reject profile-required Pack intents that rely on a hidden default."""

        intent_definition = next(
            (item for item in manifest.intake.intents if item.intent_id == intent),
            None,
        )
        requires_profile = bool(
            intent_definition
            and "research_profile" in set(intent_definition.required_slots or [])
        )
        if requires_profile and not explicit_profile:
            raise CapabilityPlanningError(
                "research_profile is required; choose lite, medium or deep"
            )
        if requires_profile and selected_profile not in {"lite", "medium", "deep"}:
            raise CapabilityPlanningError(
                "research_profile must be lite, medium or deep"
            )

    @staticmethod
    def _required_artifact_types(
        manifest,
        request: CapabilityPlanningRequest,
    ) -> list[str]:
        required_outputs = list(dict.fromkeys(request.required_artifact_types))
        intent = str(request.metadata.get("intent") or "").strip()
        intent_inputs = dict(request.metadata.get("intent_inputs") or {})
        intent_definition = next(
            (item for item in manifest.intake.intents if item.intent_id == intent),
            None,
        )
        intent_outputs = manifest.planner_policies.get("intent_outputs") or {}
        declared_intent_outputs = [
            *(intent_definition.output_artifact_types if intent_definition else []),
            *(intent_outputs.get(intent) or []),
        ]
        for artifact_type in list(dict.fromkeys(declared_intent_outputs)):
            if artifact_type not in required_outputs:
                required_outputs.append(str(artifact_type))

        # Profile outputs are planning constraints, not a fixed graph template.
        # They are applied only to the professional stock research intents and
        # can be narrowed by an explicit methodology request.
        profile_intents = {
            str(item).strip()
            for item in (manifest.planner_policies.get("profile_intents") or [])
            if str(item).strip()
        }
        is_profile_intent = intent in profile_intents or intent.startswith("domain:")
        profile_output_intents = {
            str(item).strip()
            for item in (manifest.planner_policies.get("profile_output_intents") or [])
            if str(item).strip()
        }
        use_profile_defaults = intent in profile_output_intents or intent.startswith("domain:")
        profile = str(
            request.metadata.get("research_profile")
            or intent_inputs.get("research_profile")
            or (
                dict(manifest.planner_policies.get("intent_profiles") or {}).get(intent)
                or manifest.planner_policies.get("default_profile")
                if is_profile_intent
                else ""
            )
            or ""
        ).strip().lower()
        profile_outputs = manifest.planner_policies.get("profile_outputs") or {}
        method_catalog = manifest.method_definitions()
        methodology_outputs = manifest.planner_policies.get("methodology_outputs") or {}
        requested_methods = intent_inputs.get("requested_methods") or intent_inputs.get(
            "methodologies"
        )
        if isinstance(requested_methods, str):
            requested_methods = [requested_methods]
        requested_methods = [
            str(item).strip().lower()
            for item in list(requested_methods or [])
            if str(item).strip()
        ]
        explicit_outputs = intent_inputs.get("required_artifact_types") or intent_inputs.get(
            "research_deliverables"
        )
        if isinstance(explicit_outputs, str):
            explicit_outputs = [explicit_outputs]
        explicit_method_or_output_request = bool(explicit_outputs or requested_methods)
        unknown_methods = sorted(set(requested_methods) - set(method_catalog))
        if unknown_methods:
            raise CapabilityPlanningError(
                f"unknown Pack method contract(s): {', '.join(unknown_methods)}"
            )
        if explicit_outputs:
            selected_outputs = [str(item).strip() for item in explicit_outputs if str(item).strip()]
        elif requested_methods:
            selected_outputs = [
                str(artifact_type).strip()
                for method in requested_methods
                for artifact_type in list(
                    method_catalog[method].output_artifact_types
                    or methodology_outputs.get(method)
                    or []
                )
                if str(artifact_type).strip()
            ]
        else:
            selected_outputs = [
                str(artifact_type).strip()
                for artifact_type in list(
                    (profile_outputs.get(profile) or []) if use_profile_defaults else []
                )
                if str(artifact_type).strip()
            ]
        if is_profile_intent and requested_methods and not explicit_outputs:
            # A method-specific request replaces the manifest's default report
            # contract. Main Agent synthesis remains a separate delivery step.
            required_outputs = [
                artifact_type
                for artifact_type in required_outputs
                if artifact_type not in {"research_report", "report_presentation"}
            ]
        if use_profile_defaults or explicit_method_or_output_request:
            for artifact_type in selected_outputs:
                if artifact_type not in required_outputs:
                    required_outputs.append(artifact_type)
        return required_outputs

    @staticmethod
    def _profile_selection_reason(
        *,
        metadata: dict,
        intent: str,
        selected_profile: str,
        profile_intents: set[str] | None = None,
    ) -> str:
        explicit = str(
            (metadata.get("intent_inputs") or {}).get("research_profile")
            or metadata.get("requested_research_profile")
            or ""
        ).strip().lower()
        if explicit and explicit == selected_profile:
            return "user_explicit_profile"
        if selected_profile:
            return "request_constraint_profile"
        return "no_profile_selected"

    @staticmethod
    def usable_artifact_types(
        artifacts: list,
        *,
        allow_degraded: bool = False,
        allow_assumption_only: bool = False,
    ) -> set[str]:
        """Return only active, fresh, quality-compatible Board artifacts."""
        return {
            str(artifact.artifact_type)
            for artifact in artifacts
            if CapabilityPlanner._artifact_is_usable(
                artifact,
                allow_degraded=allow_degraded,
                allow_assumption_only=allow_assumption_only,
            )
        }

    @staticmethod
    def usable_artifact_refs(
        artifacts: list,
        *,
        allow_degraded: bool = False,
        allow_assumption_only: bool = False,
    ) -> list[dict[str, object]]:
        """Return concrete, compatible Board references for graph planning."""

        refs: list[dict[str, object]] = []
        for artifact in artifacts:
            if not CapabilityPlanner._artifact_is_usable(
                artifact,
                allow_degraded=allow_degraded,
                allow_assumption_only=allow_assumption_only,
            ):
                continue
            content = artifact.content if isinstance(getattr(artifact, "content", None), dict) else {}
            refs.append(
                {
                    "artifact_id": str(artifact.artifact_id),
                    "artifact_type": str(artifact.artifact_type),
                    "schema_version": str(artifact.schema_version),
                    "snapshot_type": str(
                        content.get("dimension")
                        or content.get("snapshot_type")
                        or artifact.metadata.get("snapshot_type")
                        or artifact.metadata.get("dimension")
                        or ""
                    ),
                    # Keep the old selector key in planning telemetry while
                    # the canonical content field is snapshot_type.
                    "dimension": str(
                        content.get("dimension")
                        or content.get("snapshot_type")
                        or artifact.metadata.get("snapshot_type")
                        or artifact.metadata.get("dimension")
                        or ""
                    ),
                    "scope": str(content.get("scope") or artifact.metadata.get("scope") or ""),
                    "symbol": str(content.get("symbol") or ""),
                    "quality_status": CapabilityPlanner._artifact_quality_status(
                        artifact_type=str(artifact.artifact_type),
                        content=content,
                        metadata=(artifact.metadata or {}),
                    ),
                    "content_hash": artifact.content_hash,
                    "run_id": artifact.run_id,
                }
            )
        return refs

    @staticmethod
    def _artifact_is_usable(
        artifact,
        *,
        allow_degraded: bool,
        allow_assumption_only: bool,
    ) -> bool:
        if getattr(artifact, "status", None) != "active":
            return False
        content = artifact.content if isinstance(getattr(artifact, "content", None), dict) else {}
        quality_status = CapabilityPlanner._artifact_quality_status(
            artifact_type=str(getattr(artifact, "artifact_type", "")),
            content=content,
            metadata=(getattr(artifact, "metadata", {}) or {}),
        )
        if quality_status in {"data_gap", "degraded"} and not allow_degraded:
            return False
        if quality_status == "assumption_only" and not allow_assumption_only:
            return False
        if list(content.get("data_gap") or []) and not allow_degraded:
            return False
        ttl_value = (getattr(artifact, "metadata", {}) or {}).get("freshness_ttl_seconds")
        try:
            ttl = int(ttl_value) if ttl_value is not None else None
        except (TypeError, ValueError):
            ttl = None
        if ttl == 0:
            return False
        if ttl is not None:
            created_at = getattr(artifact, "created_at", None)
            if created_at is None or (created_at + timedelta(seconds=ttl)) <= datetime.now(timezone.utc):
                return False
        return True

    @staticmethod
    def _artifact_quality_status(
        *, artifact_type: str, content: dict, metadata: dict | None = None
    ) -> str:
        """Collapse nested Pack quality without losing per-dimension gaps."""

        declared = str(
            content.get("quality_status")
            or content.get("status")
            or content.get("data_quality")
            or (metadata or {}).get("quality_status")
            or ""
        ).strip().lower()
        if declared in {"data_gap", "degraded", "assumption_only"}:
            return declared
        if artifact_type == "stock_snapshot_bundle":
            collection = content.get("collection") if isinstance(content.get("collection"), dict) else {}
            collection_status = str(
                collection.get("quality_status")
                or ("degraded" if collection.get("degraded") else "")
                or ""
            ).strip().lower()
            if collection_status in {"data_gap", "degraded", "assumption_only"}:
                return collection_status
            rows = [row for row in list(content.get("snapshots") or []) if isinstance(row, dict)]
            statuses = {
                CapabilityPlanner._artifact_quality_status(
                    artifact_type="snapshot",
                    content=row,
                )
                for row in rows
            }
            if rows and statuses and statuses.issubset({"data_gap", "assumption_only"}):
                return "data_gap"
            if "degraded" in statuses or "data_gap" in statuses:
                return "degraded"
            if "assumption_only" in statuses:
                return "assumption_only"
        if list(content.get("data_gap") or []):
            return "degraded"
        return "available"

    def _candidate_producers(
        self,
        *,
        request: CapabilityPlanningRequest,
        artifact_type: str,
    ) -> list[CapabilityDefinition]:
        allowed_tools = set(request.allowed_tools)
        allowed_permissions = set(request.allowed_permissions)
        allowed_side_effects = set(request.allowed_side_effect_levels)
        allowed_capability_tags = set(request.allowed_capability_tags)
        preferred = set(request.preferred_capabilities)
        excluded_subagent_types = {
            str(item).strip()
            for item in request.metadata.get("exclude_subagent_types") or []
            if str(item).strip()
        }
        fallback_priority = [
            str(item).strip()
            for item in request.metadata.get("fallback_priority") or []
            if str(item).strip()
        ]
        fallback_rank = {
            item: index for index, item in enumerate(fallback_priority)
        }
        intent = str(request.metadata.get("intent") or "").strip()
        candidates = []
        cost_rank = {"low": 0, "medium": 1, "high": 2}
        latency_rank = {"low": 0, "medium": 1, "high": 2}
        for capability in self.registry.capabilities(
            domain_id=request.domain_id,
            produces=artifact_type,
            version=(str(request.metadata.get("domain_version") or "").strip() or None),
        ):
            if capability.subagent_type in excluded_subagent_types:
                continue
            intent_tags = {
                tag.split(":", 1)[1]
                for tag in capability.tags
                if tag.startswith("intent:") and ":" in tag
            }
            if intent_tags and intent not in intent_tags:
                continue
            capability_profile_tags = {
                tag for tag in capability.tags if str(tag).startswith("profile:")
            }
            if capability_profile_tags and (
                not allowed_capability_tags
                or not (capability_profile_tags & allowed_capability_tags)
            ):
                continue
            if capability.side_effect_level not in allowed_side_effects:
                continue
            if allowed_tools and not set(capability.tools).issubset(allowed_tools):
                continue
            if (
                capability.permissions
                and allowed_permissions
                and not set(capability.permissions).issubset(allowed_permissions)
            ):
                continue
            if cost_rank[capability.cost_class] > cost_rank[request.max_cost_class]:
                continue
            if latency_rank[capability.latency_class] > latency_rank[request.max_latency_class]:
                continue
            if capability.reliability < request.minimum_reliability:
                continue
            candidates.append(capability)
        candidates.sort(
            key=lambda item: (
                0 if item.capability in preferred else 1,
                fallback_rank.get(item.capability, fallback_rank.get(item.subagent_type, len(fallback_rank))),
                cost_rank[item.cost_class],
                latency_rank[item.latency_class],
                -item.reliability,
                item.subagent_type,
            )
        )
        return candidates

    @staticmethod
    def _select_available_artifact_ids(
        consumes: list,
        available_refs: list[dict[str, object]],
    ) -> list[str]:
        """Bind graph inputs to concrete Board Artifact IDs when available."""

        selected: list[str] = []
        for port in consumes:
            matches = [
                ref for ref in available_refs
                if CapabilityPlanner._artifact_ref_matches_port(ref, port)
            ]
            if not port.multiple:
                matches = matches[-1:]
            for ref in matches:
                artifact_id = str(ref.get("artifact_id") or "").strip()
                if artifact_id and artifact_id not in selected:
                    selected.append(artifact_id)
        return selected

    @staticmethod
    def _artifact_ref_matches_port(ref: dict[str, object], port) -> bool:
        if str(ref.get("artifact_type") or "") != str(port.artifact_type):
            return False
        schema_version = str(port.schema_version or "1")
        if str(ref.get("schema_version") or "1") != schema_version:
            return False
        selector = dict(port.selector or {})
        for key, expected in selector.items():
            if key == "dimensions":
                actual = str(ref.get("snapshot_type") or ref.get("dimension") or "")
                if actual not in {str(item) for item in list(expected or [])}:
                    return False
                continue
            if key == "quality_status":
                if str(ref.get("quality_status") or "") not in {
                    str(item) for item in list(expected or [])
                }:
                    return False
                continue
            if str(ref.get(key) or "") != str(expected):
                return False
        return True

    @staticmethod
    def _node_id(subagent_type: str, selected: dict[str, ExecutionNode]) -> str:
        base = subagent_type.replace(".", "_").replace("-", "_")
        node_id = base
        suffix = 2
        existing_ids = {node.node_id for node in selected.values()}
        while node_id in existing_ids:
            node_id = f"{base}_{suffix}"
            suffix += 1
        return node_id


class CapabilityDynamicPlanner:
    """Materialize the next graph frontier from the current Board state."""

    def __init__(self, planner: CapabilityPlanner) -> None:
        self.planner = planner

    def expand_compound(
        self,
        *,
        graph: ExecutionGraph,
        compound: ExecutionNode,
        artifacts,
    ) -> ExecutionGraphPatch:
        payload = compound.metadata.get("planning_request") or graph.metadata.get(
            "planning_request"
        )
        if not isinstance(payload, dict):
            raise CapabilityPlanningError(
                f"lazy compound has no planning request: {compound.node_id}"
            )
        request = CapabilityPlanningRequest.model_validate(payload)
        allow_degraded = bool(
            (request.metadata or {}).get("allow_degraded_artifacts")
            or graph.metadata.get("allow_degraded_artifacts")
        )
        allow_assumption_only = bool(
            (request.metadata or {}).get("allow_assumption_only_artifacts")
            or graph.metadata.get("allow_assumption_only_artifacts")
        )
        available = {
            *request.available_artifact_types,
            *self.planner.usable_artifact_types(
                artifacts,
                allow_degraded=allow_degraded,
                allow_assumption_only=allow_assumption_only,
            ),
        }
        usable_refs = self.planner.usable_artifact_refs(
            artifacts,
            allow_degraded=allow_degraded,
            allow_assumption_only=allow_assumption_only,
        )
        request = request.model_copy(
            update={
                "available_artifact_types": sorted(available),
                "available_artifact_refs": [
                    *list(request.available_artifact_refs),
                    *usable_refs,
                ],
                "metadata": {
                    **dict(request.metadata or {}),
                    "domain_version": graph.metadata.get("domain_version"),
                },
            }
        )
        materialized = self.planner.build_graph(request)
        existing_ids = {node.node_id for node in graph.nodes}
        operations: list[ExecutionGraphPatchOperation] = []
        for node in materialized.nodes:
            if node.node_kind != "subagent" or node.node_id in existing_ids:
                continue
            operations.append(
                ExecutionGraphPatchOperation(
                    op_type="insert_node",
                    node=node.model_copy(
                        update={"parent_node_id": compound.node_id},
                        deep=True,
                    ),
                    reason="capability planner materialized the next frontier",
                )
            )
        if not operations:
            raise CapabilityPlanningError(
                f"lazy compound produced no executable capabilities: {compound.node_id}"
            )
        return ExecutionGraphPatch(
            base_revision=graph.revision,
            decision_type="continue",
            policy_source="hybrid",
            operations=operations,
            notes=[
                "Materialized from current task, installed capability catalog, and Board artifacts.",
            ],
        )

    def evaluate_frontier(
        self,
        *,
        graph: ExecutionGraph,
        frontier: list[ExecutionNode],
        artifacts,
    ) -> ExecutionGraphPatch | None:
        """Ask the capability catalog for a replacement when a required output is absent."""
        payload = graph.metadata.get("planning_request")
        if not isinstance(payload, dict):
            return None
        request = CapabilityPlanningRequest.model_validate(payload)
        request = request.model_copy(
            update={
                "metadata": {
                    **dict(request.metadata or {}),
                    "domain_version": graph.metadata.get("domain_version"),
                }
            }
        )
        control_requests = [
            row
            for row in list(graph.metadata.get("control_requests") or [])
            if isinstance(row, dict)
        ]
        requested_artifacts = [
            str(artifact_type).strip()
            for row in control_requests
            for artifact_type in list(row.get("requested_artifact_types") or [])
            if str(artifact_type).strip()
        ]
        preferred_capabilities = [
            str(capability).strip()
            for row in control_requests
            for capability in list(row.get("preferred_capabilities") or [])
            if str(capability).strip()
        ]
        retry_targets = [
            str(row.get("target_node_id") or "").strip()
            for row in control_requests
            if row.get("kind") == "request_retry" and str(row.get("target_node_id") or "").strip()
        ]
        if retry_targets:
            return ExecutionGraphPatch(
                base_revision=graph.revision,
                decision_type="continue",
                policy_source="hybrid",
                operations=[
                    ExecutionGraphPatchOperation(
                        op_type="rerun_from",
                        target_node_id=node_id,
                        reason="Subagent requested a governed retry at the next safe point.",
                    )
                    for node_id in dict.fromkeys(retry_targets)
                ],
                notes=["Applied Subagent retry request at a safe point."],
            )
        if requested_artifacts or preferred_capabilities:
            request = request.model_copy(
                update={
                    "required_artifact_types": list(
                        dict.fromkeys(
                            [*request.required_artifact_types, *requested_artifacts]
                        )
                    ),
                    "preferred_capabilities": list(
                        dict.fromkeys(
                            [*request.preferred_capabilities, *preferred_capabilities]
                        )
                    ),
                }
            )
        allow_degraded = bool(
            (request.metadata or {}).get("allow_degraded_artifacts")
            or graph.metadata.get("allow_degraded_artifacts")
        )
        allow_assumption_only = bool(
            (request.metadata or {}).get("allow_assumption_only_artifacts")
            or graph.metadata.get("allow_assumption_only_artifacts")
        )
        active_types = self.planner.usable_artifact_types(
            artifacts,
            allow_degraded=allow_degraded,
            allow_assumption_only=allow_assumption_only,
        )
        required = self.planner._required_artifact_types(
            self.planner.registry.get(
                request.domain_id,
                version=(str(request.metadata.get("domain_version") or "").strip() or None),
            ),
            request,
        )
        missing = [artifact_type for artifact_type in required if artifact_type not in active_types]
        if not missing:
            return None
        failed_subagents = [
            node.executor
            for node in graph.nodes
            if node.node_kind == "subagent" and node.status == "failed" and node.executor
        ]
        if failed_subagents:
            fallback_priority: list[str] = []
            for failed_subagent in failed_subagents:
                failed_definition = next(
                    (
                        item
                        for item in self.planner.registry.capabilities(
                            domain_id=request.domain_id,
                            version=(str(request.metadata.get("domain_version") or "").strip() or None),
                        )
                        if item.subagent_type == failed_subagent
                    ),
                    None,
                )
                if failed_definition is None:
                    continue
                fallback_priority.extend(failed_definition.fallback_capabilities)
                fallback_priority.extend(failed_definition.fallback_subagent_types)
            request = request.model_copy(
                update={
                    "metadata": {
                        **dict(request.metadata or {}),
                        "exclude_subagent_types": failed_subagents,
                        "fallback_priority": list(dict.fromkeys(fallback_priority)),
                    }
                }
            )
        request = request.model_copy(
            update={
                "available_artifact_types": sorted(active_types),
                "required_artifact_types": missing,
            }
        )
        try:
            candidate_graph = self.planner.build_graph(request)
        except CapabilityPlanningError:
            return None
        existing_ids = {node.node_id for node in graph.nodes}
        operations: list[ExecutionGraphPatchOperation] = []
        parent_id = next(
            (node.node_id for node in graph.nodes if node.node_kind == "compound"),
            None,
        )
        for node in candidate_graph.nodes:
            if node.node_kind != "subagent" or node.node_id in existing_ids:
                continue
            operations.append(
                ExecutionGraphPatchOperation(
                    op_type="insert_node",
                    node=node.model_copy(update={"parent_node_id": parent_id}, deep=True),
                    reason=f"required artifacts missing after frontier: {','.join(missing)}",
                )
            )
        if not operations:
            return None
        return ExecutionGraphPatch(
            base_revision=graph.revision,
            decision_type="replan",
            policy_source="hybrid",
            operations=operations,
            notes=["Planner evaluated the Board at a safe point and added a replacement frontier."],
        )


__all__ = [
    "CapabilityDynamicPlanner",
    "CapabilityPlanner",
    "CapabilityPlanningError",
]
