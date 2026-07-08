from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from research_system.agents._subagent_dag import SubagentDagNodeResult, SubagentDagNodeSpec
from research_system.agents._subagent_phase_controller import SubagentPhaseController
from research_system.agents._subagent_phase_policy import ReporterPhasePolicy
from research_system.agents._subagent_invoker import RuntimeBoundSubagentInvoker
from research_system.runtime.core.execution_policy import RuntimeDegradeReason
from research_system.runtime.platform.skill_runtime import SkillRuntime
from research_system.runtime.platform.settings import Settings
from research_system.runtime.providers.provider_registry import OpenAIProvider
from research_system.services.methodologies.planning.planning_context import (
    build_planning_context_view,
    combine_focus_text,
    derive_methodology_focus,
    has_cashflow_focus,
    has_momentum_focus,
    has_policy_focus,
    normalize_planning_context_hints,
)
from research_system.services.reporting.reporting_utils import strategy_follow_up
from research_system.schemas.review import ClaimVerdictBreakdown, PosteriorReviewSummary
from research_system.schemas.subagents import (
    SubagentCatalogEntry,
    SubagentGraphPatch,
    SubagentInvocation,
    SubagentPlan,
    SubagentPlanNode,
)
from research_system.subagents.registry import SubagentRegistry
from research_system.subagents.reporting import (
    DeterministicBankCompareReportComposerSubagent,
    FallbackReportComposerSubagent,
    LLMErrorReportComposerSubagent,
    LLMReportComposerSubagent,
    ReportStrategyResolverSubagent,
)


class ReportPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conclusion: str
    evidence_ids: list[str] = Field(default_factory=list)
    risk: str = Field(validation_alias=AliasChoices("risk", "risk_statement"))
    follow_up: str | None = Field(
        default=None,
        validation_alias=AliasChoices("follow_up", "follow_up_suggestion"),
    )
    degraded_note: str | None = None


REPORT_EXECUTION_AUTO = "auto"
REPORT_EXECUTION_FALLBACK = "fallback"


class ReporterService(RuntimeBoundSubagentInvoker):
    agent_name = "reporter"

    def __init__(
        self,
        llm_provider: OpenAIProvider,
        runs_root: Path | None = None,
        settings: Settings | None = None,
        skill_runtime: SkillRuntime | None = None,
    ) -> None:
        self.llm_provider = llm_provider
        self.settings = settings
        self.runs_root = runs_root
        self.skill_runtime = skill_runtime
        self.registry = SubagentRegistry(
            [
                ReportStrategyResolverSubagent(
                    normalize_planning_context_hints=self._normalize_planning_context_hints,
                    derive_report_strategy=self._derive_report_strategy,
                    screening_scorecard_payload=self._screening_scorecard_payload,
                    screening_candidates_payload=self._screening_candidates_payload,
                    methodology_payloads=self._methodology_payloads,
                    review_summary_payload=self._review_summary_payload,
                    bank_compare_scorecard_payload=self._bank_compare_scorecard_payload,
                    distillation_scorecard_digest=self._distillation_scorecard_digest,
                    distillation_scorecard_payload=self._distillation_scorecard_payload,
                    report_strategy_trace_notes=self._report_strategy_trace_notes,
                ),
                DeterministicBankCompareReportComposerSubagent(
                    compose_report=self._deterministic_stock_compare_report,
                    bank_compare_scorecard_payload=self._bank_compare_scorecard_payload,
                ),
                FallbackReportComposerSubagent(
                    review_verdict_by_claim_type_payload=self._review_verdict_by_claim_type_payload,
                    screening_digest=self._screening_digest,
                    compare_targets_payload=self._compare_targets_payload,
                    compare_digest=self._compare_digest,
                ),
                LLMReportComposerSubagent(
                    llm_provider=self.llm_provider,
                    schema=ReportPayload,
                    normalize_review_conclusion=self._normalize_review_conclusion,
                ),
                LLMErrorReportComposerSubagent(),
            ],
            catalog_overlay_provider=self._approved_catalog_overlay_entries,
            activation_provider=self._activated_discovery_alias_entries,
            activation_factory_provider=self._activated_discovery_runtime_subagent,
        )
        self.phase_controller = SubagentPhaseController(
            registry=self.registry,
            invoke_subagent=self._invoke_subagent,
            emit_event=self._emit_runtime_event,
            cache_context_builder=self._subagent_dag_cache_context,
            start_from_node_id_builder=self._subagent_dag_start_from_node_id,
            runtime_binding_getter=self._runtime_binding_for_events,
            llm_provider=self.llm_provider,
            settings=self.settings,
            service_name=self.agent_name,
        )
        self.phase_policy = ReporterPhasePolicy()

    def run(
        self,
        *,
        intent: str,
        query: str,
        evidences: list[dict],
        retrieval_summary: str | None = None,
        planning_context_hints: dict[str, str] | None = None,
        execution_mode: str = REPORT_EXECUTION_AUTO,
        runtime_degraded_note: str | None = None,
    ) -> dict:
        with self.local_run_scope(
            intent=intent,
            query=query,
            phase="report",
            title="reporter",
            objective="Compose the final report from selected evidence.",
        ):
            return self._run_impl(
                intent=intent,
                query=query,
                evidences=evidences,
                retrieval_summary=retrieval_summary,
                planning_context_hints=planning_context_hints,
                execution_mode=execution_mode,
                runtime_degraded_note=runtime_degraded_note,
            )

    def _run_impl(
        self,
        *,
        intent: str,
        query: str,
        evidences: list[dict],
        retrieval_summary: str | None = None,
        planning_context_hints: dict[str, str] | None = None,
        execution_mode: str = REPORT_EXECUTION_AUTO,
        runtime_degraded_note: str | None = None,
    ) -> dict:
        force_fallback = execution_mode == REPORT_EXECUTION_FALLBACK
        provider_available = bool(getattr(self.llm_provider, "available", True))
        subagent_plan = self._build_report_subagent_plan(
            intent=intent,
            query=query,
            evidences=evidences,
            retrieval_summary=retrieval_summary,
            planning_context_hints=planning_context_hints,
            branch_mode=None,
        )
        subagent_plan, _base_results, subagent_plan_patches, results = self.phase_controller.execute_patch_cycle(
            plan=subagent_plan,
            dag_id="report",
            base_max_workers=1,
            final_max_workers=1,
            build_invocation_factory=lambda current_plan: self._build_report_plan_invocations(
                plan=current_plan,
                intent=intent,
                query=query,
                evidences=evidences,
                retrieval_summary=retrieval_summary,
                planning_context_hints=planning_context_hints,
                runtime_degraded_note=runtime_degraded_note,
            ),
            patch_builder=lambda base_results: self._build_report_subagent_replan_patch(
                intent=intent,
                force_fallback=force_fallback,
                provider_available=provider_available,
                base_results=base_results,
            ),
            replan_builder=lambda current_plan, patch, base_results: self._rebuild_report_subagent_plan(
                plan=current_plan,
                intent=intent,
                query=query,
                evidences=evidences,
                retrieval_summary=retrieval_summary,
                planning_context_hints=planning_context_hints,
                runtime_degraded_note=runtime_degraded_note,
                patch=patch,
                base_results=base_results,
            ),
            candidate_plan_variants_builder=lambda rule_final_plan, patch, base_results: self._build_report_candidate_plan_variants(
                rule_final_plan=rule_final_plan,
                intent=intent,
                query=query,
                evidences=evidences,
                retrieval_summary=retrieval_summary,
                planning_context_hints=planning_context_hints,
                runtime_degraded_note=runtime_degraded_note,
                force_fallback=force_fallback,
                provider_available=provider_available,
                patch=patch,
                base_results=base_results,
            ),
        )
        branch_mode = self._report_branch_mode_from_plan(subagent_plan)
        trace = self._report_trace(
            intent=intent,
            evidences=evidences,
            execution_mode=execution_mode,
            runtime_degraded_note=runtime_degraded_note,
            branch_mode=branch_mode,
            results=results,
        )
        outputs = self._finalize_report_outputs(
            intent=intent,
            branch_mode=branch_mode,
            evidences=evidences,
            results=results,
        )
        outputs["subagent_trace"] = trace
        outputs["subagent_plans"] = [subagent_plan.model_dump(mode="json")]
        outputs["subagent_plan_patches"] = subagent_plan_patches
        return outputs

    def _deterministic_stock_compare_report(
        self,
        *,
        evidences: list[dict],
        methodology_payloads: list[dict],
        report_strategy: dict[str, str | list[str] | None],
    ) -> dict:
        scorecard = self._bank_compare_scorecard_payload(methodology_payloads) or {}
        compare_digest = self._compare_digest(
            compare_targets=self._compare_targets_payload(evidences),
            methodology_payloads=methodology_payloads,
        )
        missing_metrics = [str(item) for item in list(scorecard.get("missing_metrics") or []) if str(item).strip()]
        risk = (
            "当前排序基于同口径银行股估值与经营质量快照，适合作为优先跟踪顺序，"
            "但仍不替代完整财报建模、资产质量穿透和行业景气判断。"
        )
        if missing_metrics:
            risk += f" 当前仍缺少部分关键字段：{', '.join(missing_metrics[:5])}。"
        follow_up = "继续补齐银行股同口径核心字段，尤其是净息差、不良率、拨备覆盖率、资本充足率和股息率，再做最终定序。"
        return {
            "conclusion": compare_digest,
            "evidence": evidences[:8],
            "methodologies": methodology_payloads or None,
            "review_summary": None,
            "risk": risk,
            "follow_up": self._merge_follow_up(follow_up, report_strategy),
            "degraded_note": None,
        }

    def _select_report_catalog_entry(
        self,
        *,
        intent: str,
        capability: str,
        preferred_subagent_type: str | None = None,
        requires_llm: bool | None = None,
    ) -> SubagentCatalogEntry:
        return self.phase_controller.select_catalog_entry(
            stage="report",
            intent=intent,
            capability=capability,
            preferred_subagent_type=preferred_subagent_type,
            requires_llm=requires_llm,
        )

    def _cost_rank(self, cost_class: str | None) -> int:
        return self.phase_controller._cost_rank(cost_class)

    def _latency_rank(self, latency_class: str | None) -> int:
        return self.phase_controller._latency_rank(latency_class)

    def _build_catalog_plan_node(
        self,
        *,
        node_id: str,
        subagent_type: str,
        objective: str,
        inputs: dict[str, Any],
        depends_on: list[str] | None = None,
        tags: list[str] | None = None,
        max_attempts: int = 1,
        cache_scope: str | None = "run",
        cache_key: str | None = None,
        notes: list[str] | None = None,
    ) -> SubagentPlanNode:
        return self.phase_controller.build_catalog_plan_node(
            node_id=node_id,
            subagent_type=subagent_type,
            objective=objective,
            inputs=inputs,
            depends_on=depends_on,
            tags=tags,
            max_attempts=max_attempts,
            cache_scope=cache_scope,
            cache_key=cache_key,
            notes=notes,
        )

    def _build_report_subagent_plan(
        self,
        *,
        intent: str,
        query: str,
        evidences: list[dict],
        retrieval_summary: str | None,
        planning_context_hints: dict[str, str] | None,
        branch_mode: str | None,
    ) -> SubagentPlan:
        resolver_entry = self._select_report_catalog_entry(
            intent=intent,
            capability="report_strategy",
            preferred_subagent_type="report_strategy_resolver",
        )
        nodes = [
            self._build_catalog_plan_node(
                node_id="report_strategy_resolver",
                subagent_type=resolver_entry.subagent_type,
                objective="Resolve report strategy from retrieval summary and planning context.",
                inputs={
                    "intent": intent,
                    "query": query,
                    "retrieval_summary": retrieval_summary,
                    "planning_context_hints": dict(planning_context_hints or {}),
                    "evidence_count": len(evidences),
                },
                cache_key="report_strategy_resolver",
                notes=["report_phase=strategy"],
            )
        ]
        selected_subagent_types = {
            "report_strategy_resolver": resolver_entry.subagent_type,
        }
        if branch_mode is not None:
            terminal_node = self._build_report_terminal_plan_node(
                intent=intent,
                query=query,
                evidences=evidences,
                retrieval_summary=retrieval_summary,
                runtime_degraded_note=None,
                branch_mode=branch_mode,
            )
            nodes.append(terminal_node)
            selected_subagent_types["report_terminal"] = terminal_node.subagent_type
        return SubagentPlan(
            intent=intent,
            phase="report",
            strategy="catalog",
            nodes=nodes,
            notes=["report_catalog_plan"] if branch_mode is not None else ["report_strategy_base_plan"],
            catalog_trace={
                "stage": "report",
                "selection_mode": "catalog",
                "selected_subagent_types": selected_subagent_types,
            },
        )

    def _build_report_terminal_plan_node(
        self,
        *,
        intent: str,
        query: str,
        evidences: list[dict],
        retrieval_summary: str | None,
        runtime_degraded_note: str | None,
        branch_mode: str,
    ) -> SubagentPlanNode:
        if branch_mode == "deterministic_bank_compare":
            deterministic_entry = self._select_report_catalog_entry(
                intent=intent,
                capability="deterministic_bank_compare",
                preferred_subagent_type="bank_compare_report_composer",
            )
            return self._build_catalog_plan_node(
                node_id="report_terminal",
                subagent_type=deterministic_entry.subagent_type,
                objective="Compose deterministic bank compare report from same-basis scorecard evidence.",
                inputs={"intent": intent, "query": query, "evidence_count": len(evidences)},
                depends_on=["report_strategy_resolver"],
                cache_key="report_terminal",
                notes=["report_phase=terminal", "candidate=bank_compare_report_composer"],
            )
        if branch_mode == "fallback":
            fallback_entry = self._select_report_catalog_entry(
                intent=intent,
                capability="fallback_reporting",
                preferred_subagent_type="fallback_report_composer",
            )
            return self._build_catalog_plan_node(
                node_id="report_terminal",
                subagent_type=fallback_entry.subagent_type,
                objective="Compose degraded fallback report.",
                inputs={
                    "intent": intent,
                    "query": query,
                    "evidence_count": len(evidences),
                    "degraded_note": runtime_degraded_note or RuntimeDegradeReason.LLM_UNAVAILABLE.value,
                },
                depends_on=["report_strategy_resolver"],
                cache_key="report_terminal",
                notes=["report_phase=terminal", "candidate=fallback_report_composer"],
            )
        llm_entry = self._select_report_catalog_entry(
            intent=intent,
            capability="report_composition",
            preferred_subagent_type="llm_report_composer",
            requires_llm=True,
        )
        return self._build_catalog_plan_node(
            node_id="report_terminal",
            subagent_type=llm_entry.subagent_type,
            objective="Compose final report via LLM.",
            inputs={
                "intent": intent,
                "query": query,
                "retrieval_summary": retrieval_summary,
                "evidence_count": len(evidences),
            },
            depends_on=["report_strategy_resolver"],
            cache_key="report_terminal",
            notes=["report_phase=terminal", "candidate=llm_report_composer"],
        )

    def _build_report_subagent_replan_patch(
        self,
        *,
        intent: str,
        force_fallback: bool,
        provider_available: bool,
        base_results: dict[str, SubagentDagNodeResult],
    ) -> SubagentGraphPatch:
        branch_mode = self._report_branch_mode(
            intent=intent,
            strategy_outputs=dict((base_results["report_strategy_resolver"].outputs or {}) or {}),
            force_fallback=force_fallback,
            provider_available=provider_available,
        )
        return self.phase_policy.build_report_replan_patch(branch_mode=branch_mode)

    def _rebuild_report_subagent_plan(
        self,
        *,
        plan: SubagentPlan,
        intent: str,
        query: str,
        evidences: list[dict],
        retrieval_summary: str | None,
        planning_context_hints: dict[str, str] | None,
        runtime_degraded_note: str | None,
        patch: SubagentGraphPatch,
        base_results: dict[str, SubagentDagNodeResult],
    ) -> SubagentPlan:
        _ = plan
        branch_mode = self._report_branch_mode(
            intent=intent,
            strategy_outputs=dict((base_results["report_strategy_resolver"].outputs or {}) or {}),
            force_fallback=False,
            provider_available=True,
        )
        requested_branch_mode = branch_mode
        if patch.operations:
            requested_branch_mode = (
                str((patch.operations[0].metadata or {}).get("branch_mode") or "").strip()
                or requested_branch_mode
            )
        replanned = self._build_report_subagent_plan(
            intent=intent,
            query=query,
            evidences=evidences,
            retrieval_summary=retrieval_summary,
            planning_context_hints=planning_context_hints,
            branch_mode=requested_branch_mode,
        )
        replanned.notes = self._unique_notes(
            [
                *list(replanned.notes or []),
                "report_terminal_replanned_from_strategy",
                f"report_branch={requested_branch_mode}",
            ]
        )
        replanned.catalog_trace = {
            **dict(replanned.catalog_trace or {}),
            "replanned_from_strategy": True,
        }
        if requested_branch_mode == "fallback":
            report_terminal = next(node for node in replanned.nodes if node.node_id == "report_terminal")
            report_terminal.inputs = {
                **dict(report_terminal.inputs or {}),
                "degraded_note": runtime_degraded_note or RuntimeDegradeReason.LLM_UNAVAILABLE.value,
            }
        return replanned

    def _build_report_candidate_plan_variants(
        self,
        *,
        rule_final_plan: SubagentPlan,
        intent: str,
        query: str,
        evidences: list[dict],
        retrieval_summary: str | None,
        planning_context_hints: dict[str, str] | None,
        runtime_degraded_note: str | None,
        force_fallback: bool,
        provider_available: bool,
        patch: SubagentGraphPatch | None,
        base_results: dict[str, SubagentDagNodeResult],
    ) -> dict[str, SubagentPlan]:
        strategy_outputs = dict((base_results["report_strategy_resolver"].outputs or {}) or {})
        rule_branch_mode = self._report_branch_mode_from_plan(rule_final_plan)
        branch_modes: list[str] = []
        if intent == "stock_compare" and strategy_outputs.get("bank_compare_scorecard_payload"):
            branch_modes.append("deterministic_bank_compare")
        if "fallback" not in branch_modes:
            branch_modes.append("fallback")
        if provider_available and not force_fallback:
            branch_modes.append("llm")
        variants: dict[str, SubagentPlan] = {rule_branch_mode: rule_final_plan}
        requested_branch_mode = (
            str((patch.operations[0].metadata or {}).get("branch_mode") or "").strip()
            if patch is not None and patch.operations
            else ""
        )
        for branch_mode in branch_modes:
            if branch_mode == rule_branch_mode:
                continue
            variants[branch_mode] = self._build_report_subagent_plan(
                intent=intent,
                query=query,
                evidences=evidences,
                retrieval_summary=retrieval_summary,
                planning_context_hints=planning_context_hints,
                branch_mode=branch_mode,
            )
            variants[branch_mode].notes = self._unique_notes(
                [
                    *list(variants[branch_mode].notes or []),
                    "report_terminal_candidate_variant",
                    f"report_branch={branch_mode}",
                    f"rule_requested_branch={requested_branch_mode or rule_branch_mode}",
                ]
            )
        return variants

    def _execute_subagent_plan(
        self,
        *,
        plan: SubagentPlan,
        dag_id: str,
        build_invocation: dict[str, dict[str, Any]],
    ) -> dict[str, SubagentDagNodeResult]:
        return self.phase_controller.execute_plan(
            plan=plan,
            build_invocation=build_invocation,
            dag_id=dag_id,
            max_workers=1,
        )

    def _build_report_plan_invocations(
        self,
        *,
        plan: SubagentPlan,
        intent: str,
        query: str,
        evidences: list[dict],
        retrieval_summary: str | None,
        planning_context_hints: dict[str, str] | None,
        runtime_degraded_note: str | None,
    ) -> dict[str, dict[str, Any]]:
        runtime_nodes: dict[str, dict[str, Any]] = {
            "report_strategy_resolver": {
                "builder": lambda _results: self._build_subagent_invocation(
                    subagent_type="report_strategy_resolver",
                    objective="Resolve report strategy from retrieval summary and planning context.",
                    inputs={
                        "intent": intent,
                        "query": query,
                        "evidences": evidences,
                        "retrieval_summary": retrieval_summary,
                        "planning_context_hints": planning_context_hints,
                    },
                    task_name="report:strategy:report_strategy_resolver",
                    agent_path="reporter/strategy/report_strategy_resolver",
                ),
                "metadata": self._dag_metadata(
                    task_name="report:strategy:report_strategy_resolver",
                    agent_path="reporter/strategy/report_strategy_resolver",
                    objective="Resolve report strategy from retrieval summary and planning context.",
                ),
            }
        }

        plan_node_map = {node.node_id: node for node in plan.nodes}
        if "report_terminal" not in plan_node_map:
            return {
                node_id: runtime_nodes[node_id]
                for node_id in [node.node_id for node in plan.nodes]
                if node_id in runtime_nodes
            }
        terminal_subagent_type = plan_node_map["report_terminal"].subagent_type
        if terminal_subagent_type == "bank_compare_report_composer":
            runtime_nodes["report_terminal"] = {
                "builder": lambda results: self._build_subagent_invocation(
                    subagent_type="bank_compare_report_composer",
                    objective="Compose deterministic bank compare report from same-basis scorecard evidence.",
                    inputs={
                        "evidences": evidences,
                        "methodology_payloads": list((results["report_strategy_resolver"].outputs or {}).get("methodology_payloads") or []),
                        "report_strategy": dict((results["report_strategy_resolver"].outputs or {}).get("report_strategy") or {}),
                    },
                    task_name="report:deterministic:bank_compare_report_composer",
                    agent_path="reporter/deterministic/bank_compare_report_composer",
                ),
                "metadata": self._dag_metadata(
                    task_name="report:deterministic:bank_compare_report_composer",
                    agent_path="reporter/deterministic/bank_compare_report_composer",
                    objective="Compose deterministic bank compare report from same-basis scorecard evidence.",
                ),
            }
        elif terminal_subagent_type == "fallback_report_composer":
            runtime_nodes["report_terminal"] = {
                "builder": lambda results: self._build_subagent_invocation(
                    subagent_type="fallback_report_composer",
                    objective="Compose degraded fallback report.",
                    inputs={
                        "intent": intent,
                        "query": query,
                        "evidences": evidences,
                        "review_summary_payload": (results["report_strategy_resolver"].outputs or {}).get("review_summary_payload"),
                        "scorecard_digest": (results["report_strategy_resolver"].outputs or {}).get("scorecard_digest"),
                        "scorecard_payload": (results["report_strategy_resolver"].outputs or {}).get("scorecard_payload"),
                        "screening_scorecard_payload": (results["report_strategy_resolver"].outputs or {}).get("screening_scorecard_payload"),
                        "screening_candidates_payload": list((results["report_strategy_resolver"].outputs or {}).get("screening_candidates_payload") or []),
                        "methodology_payloads": list((results["report_strategy_resolver"].outputs or {}).get("methodology_payloads") or []),
                        "report_strategy": dict((results["report_strategy_resolver"].outputs or {}).get("report_strategy") or {}),
                        "degraded_note": runtime_degraded_note or RuntimeDegradeReason.LLM_UNAVAILABLE.value,
                    },
                    task_name="report:fallback:fallback_report_composer",
                    agent_path="reporter/fallback/fallback_report_composer",
                ),
                "metadata": self._dag_metadata(
                    task_name="report:fallback:fallback_report_composer",
                    agent_path="reporter/fallback/fallback_report_composer",
                    objective="Compose degraded fallback report.",
                ),
            }
        else:
            runtime_nodes["report_terminal"] = {
                "builder": lambda results: self._build_subagent_invocation(
                    subagent_type="llm_report_composer",
                    objective="Compose final report via LLM.",
                    inputs={
                        "intent": intent,
                        "query": query,
                        "evidences": evidences,
                        "retrieval_summary": retrieval_summary,
                        "review_summary_payload": (results["report_strategy_resolver"].outputs or {}).get("review_summary_payload"),
                        "report_strategy": dict((results["report_strategy_resolver"].outputs or {}).get("report_strategy") or {}),
                    },
                    task_name="report:llm:llm_report_composer",
                    agent_path="reporter/llm/llm_report_composer",
                    requires_llm=True,
                    fallback_subagent_type="fallback_report_composer",
                    fallback_objective="Compose degraded fallback report.",
                    fallback_inputs={
                        "intent": intent,
                        "query": query,
                        "evidences": evidences,
                        "review_summary_payload": (results["report_strategy_resolver"].outputs or {}).get("review_summary_payload"),
                        "scorecard_digest": (results["report_strategy_resolver"].outputs or {}).get("scorecard_digest"),
                        "scorecard_payload": (results["report_strategy_resolver"].outputs or {}).get("scorecard_payload"),
                        "screening_scorecard_payload": (results["report_strategy_resolver"].outputs or {}).get("screening_scorecard_payload"),
                        "screening_candidates_payload": list((results["report_strategy_resolver"].outputs or {}).get("screening_candidates_payload") or []),
                        "methodology_payloads": list((results["report_strategy_resolver"].outputs or {}).get("methodology_payloads") or []),
                        "report_strategy": dict((results["report_strategy_resolver"].outputs or {}).get("report_strategy") or {}),
                        "degraded_note": runtime_degraded_note,
                    },
                    fallback_context={
                        "task_name": "report:fallback:fallback_report_composer",
                        "agent_path": "reporter/fallback/fallback_report_composer",
                        "phase": "report",
                    },
                    error_fallback_subagent_type="llm_error_report_composer",
                    error_fallback_objective="Compose degraded report after LLM error.",
                    error_fallback_inputs={
                        "intent": intent,
                        "query": query,
                        "evidences": evidences,
                        "review_summary_payload": (results["report_strategy_resolver"].outputs or {}).get("review_summary_payload"),
                        "scorecard_digest": (results["report_strategy_resolver"].outputs or {}).get("scorecard_digest"),
                        "scorecard_payload": (results["report_strategy_resolver"].outputs or {}).get("scorecard_payload"),
                        "screening_scorecard_payload": (results["report_strategy_resolver"].outputs or {}).get("screening_scorecard_payload"),
                        "screening_candidates_payload": list((results["report_strategy_resolver"].outputs or {}).get("screening_candidates_payload") or []),
                        "screening_digest": self._screening_digest(
                            (results["report_strategy_resolver"].outputs or {}).get("screening_scorecard_payload"),
                            list((results["report_strategy_resolver"].outputs or {}).get("screening_candidates_payload") or []),
                        ) if intent == "stock_screen" else None,
                        "methodology_payloads": list((results["report_strategy_resolver"].outputs or {}).get("methodology_payloads") or []),
                        "report_strategy": dict((results["report_strategy_resolver"].outputs or {}).get("report_strategy") or {}),
                    },
                    error_fallback_context={
                        "task_name": "report:llm:error:llm_error_report_composer",
                        "agent_path": "reporter/llm/error/llm_error_report_composer",
                        "phase": "report",
                    },
                ),
                "metadata": self._dag_metadata(
                    task_name="report:llm:llm_report_composer",
                    agent_path="reporter/llm/llm_report_composer",
                    objective="Compose final report via LLM.",
                ),
            }
        return {
            node_id: runtime_nodes[node_id]
            for node_id in [node.node_id for node in plan.nodes]
            if node_id in runtime_nodes
        }

    def _report_branch_mode(
        self,
        *,
        intent: str,
        strategy_outputs: dict[str, Any],
        force_fallback: bool,
        provider_available: bool,
    ) -> str:
        if intent == "stock_compare" and strategy_outputs.get("bank_compare_scorecard_payload"):
            return "deterministic_bank_compare"
        if force_fallback or not provider_available:
            return "fallback"
        return "llm"

    def _report_branch_mode_from_plan(self, plan: SubagentPlan) -> str:
        terminal_node = next((node for node in plan.nodes if node.node_id == "report_terminal"), None)
        terminal_subagent_type = str((terminal_node.subagent_type if terminal_node is not None else "") or "").strip()
        if terminal_subagent_type == "bank_compare_report_composer":
            return "deterministic_bank_compare"
        if terminal_subagent_type == "fallback_report_composer":
            return "fallback"
        return "llm"

    def _report_nodes(
        self,
        *,
        intent: str,
        query: str,
        evidences: list[dict],
        retrieval_summary: str | None,
        planning_context_hints: dict[str, str] | None,
        execution_mode: str,
        runtime_degraded_note: str | None,
        branch_mode: str,
    ) -> list[SubagentDagNodeSpec]:
        nodes = [
            SubagentDagNodeSpec(
                node_id="report_strategy_resolver",
                replayable=True,
                cache_scope="run",
                cache_key="report_strategy_resolver",
                build_invocation=lambda _results: self._build_subagent_invocation(
                    subagent_type="report_strategy_resolver",
                    objective="Resolve report strategy from retrieval summary and planning context.",
                    inputs={
                        "intent": intent,
                        "query": query,
                        "evidences": evidences,
                        "retrieval_summary": retrieval_summary,
                        "planning_context_hints": planning_context_hints,
                    },
                    task_name="report:strategy:report_strategy_resolver",
                    agent_path="reporter/strategy/report_strategy_resolver",
                ),
                metadata=self._dag_metadata(
                    task_name="report:strategy:report_strategy_resolver",
                    agent_path="reporter/strategy/report_strategy_resolver",
                    objective="Resolve report strategy from retrieval summary and planning context.",
                ),
            ),
        ]
        if branch_mode == "deterministic_bank_compare":
            nodes.append(
                SubagentDagNodeSpec(
                    node_id="report_terminal",
                    depends_on=("report_strategy_resolver",),
                    build_invocation=lambda results: self._build_subagent_invocation(
                        subagent_type="bank_compare_report_composer",
                        objective="Compose deterministic bank compare report from same-basis scorecard evidence.",
                        inputs={
                            "evidences": evidences,
                            "methodology_payloads": list((results["report_strategy_resolver"].outputs or {}).get("methodology_payloads") or []),
                            "report_strategy": dict((results["report_strategy_resolver"].outputs or {}).get("report_strategy") or {}),
                        },
                        task_name="report:deterministic:bank_compare_report_composer",
                        agent_path="reporter/deterministic/bank_compare_report_composer",
                    ),
                    metadata=self._dag_metadata(
                        task_name="report:deterministic:bank_compare_report_composer",
                        agent_path="reporter/deterministic/bank_compare_report_composer",
                        objective="Compose deterministic bank compare report from same-basis scorecard evidence.",
                    ),
                )
            )
            return nodes
        if branch_mode == "fallback":
            nodes.append(
                SubagentDagNodeSpec(
                    node_id="report_terminal",
                    depends_on=("report_strategy_resolver",),
                    build_invocation=lambda results: self._build_subagent_invocation(
                        subagent_type="fallback_report_composer",
                        objective="Compose degraded fallback report.",
                        inputs={
                            "intent": intent,
                            "query": query,
                            "evidences": evidences,
                            "review_summary_payload": (results["report_strategy_resolver"].outputs or {}).get("review_summary_payload"),
                            "scorecard_digest": (results["report_strategy_resolver"].outputs or {}).get("scorecard_digest"),
                            "scorecard_payload": (results["report_strategy_resolver"].outputs or {}).get("scorecard_payload"),
                            "screening_scorecard_payload": (results["report_strategy_resolver"].outputs or {}).get("screening_scorecard_payload"),
                            "screening_candidates_payload": list((results["report_strategy_resolver"].outputs or {}).get("screening_candidates_payload") or []),
                            "methodology_payloads": list((results["report_strategy_resolver"].outputs or {}).get("methodology_payloads") or []),
                            "report_strategy": dict((results["report_strategy_resolver"].outputs or {}).get("report_strategy") or {}),
                            "degraded_note": runtime_degraded_note or RuntimeDegradeReason.LLM_UNAVAILABLE.value,
                        },
                        task_name="report:fallback:fallback_report_composer",
                        agent_path="reporter/fallback/fallback_report_composer",
                    ),
                    metadata=self._dag_metadata(
                        task_name="report:fallback:fallback_report_composer",
                        agent_path="reporter/fallback/fallback_report_composer",
                        objective="Compose degraded fallback report.",
                    ),
                )
            )
            return nodes
        nodes.append(
            SubagentDagNodeSpec(
                node_id="report_terminal",
                depends_on=("report_strategy_resolver",),
                build_invocation=lambda results: self._build_subagent_invocation(
                    subagent_type="llm_report_composer",
                    objective="Compose final report via LLM.",
                    inputs={
                        "intent": intent,
                        "query": query,
                        "evidences": evidences,
                        "retrieval_summary": retrieval_summary,
                        "review_summary_payload": (results["report_strategy_resolver"].outputs or {}).get("review_summary_payload"),
                        "report_strategy": dict((results["report_strategy_resolver"].outputs or {}).get("report_strategy") or {}),
                    },
                    task_name="report:llm:llm_report_composer",
                    agent_path="reporter/llm/llm_report_composer",
                    requires_llm=True,
                    fallback_subagent_type="fallback_report_composer",
                    fallback_objective="Compose degraded fallback report.",
                    fallback_inputs={
                        "intent": intent,
                        "query": query,
                        "evidences": evidences,
                        "review_summary_payload": (results["report_strategy_resolver"].outputs or {}).get("review_summary_payload"),
                        "scorecard_digest": (results["report_strategy_resolver"].outputs or {}).get("scorecard_digest"),
                        "scorecard_payload": (results["report_strategy_resolver"].outputs or {}).get("scorecard_payload"),
                        "screening_scorecard_payload": (results["report_strategy_resolver"].outputs or {}).get("screening_scorecard_payload"),
                        "screening_candidates_payload": list((results["report_strategy_resolver"].outputs or {}).get("screening_candidates_payload") or []),
                        "methodology_payloads": list((results["report_strategy_resolver"].outputs or {}).get("methodology_payloads") or []),
                        "report_strategy": dict((results["report_strategy_resolver"].outputs or {}).get("report_strategy") or {}),
                        "degraded_note": runtime_degraded_note,
                    },
                    fallback_context={
                        "task_name": "report:fallback:fallback_report_composer",
                        "agent_path": "reporter/fallback/fallback_report_composer",
                        "phase": "report",
                    },
                    error_fallback_subagent_type="llm_error_report_composer",
                    error_fallback_objective="Compose degraded report after LLM error.",
                    error_fallback_inputs={
                        "intent": intent,
                        "query": query,
                        "evidences": evidences,
                        "review_summary_payload": (results["report_strategy_resolver"].outputs or {}).get("review_summary_payload"),
                        "scorecard_digest": (results["report_strategy_resolver"].outputs or {}).get("scorecard_digest"),
                        "scorecard_payload": (results["report_strategy_resolver"].outputs or {}).get("scorecard_payload"),
                        "screening_scorecard_payload": (results["report_strategy_resolver"].outputs or {}).get("screening_scorecard_payload"),
                        "screening_candidates_payload": list((results["report_strategy_resolver"].outputs or {}).get("screening_candidates_payload") or []),
                        "screening_digest": self._screening_digest(
                            (results["report_strategy_resolver"].outputs or {}).get("screening_scorecard_payload"),
                            list((results["report_strategy_resolver"].outputs or {}).get("screening_candidates_payload") or []),
                        ) if intent == "stock_screen" else None,
                        "methodology_payloads": list((results["report_strategy_resolver"].outputs or {}).get("methodology_payloads") or []),
                        "report_strategy": dict((results["report_strategy_resolver"].outputs or {}).get("report_strategy") or {}),
                    },
                    error_fallback_context={
                        "task_name": "report:llm:error:llm_error_report_composer",
                        "agent_path": "reporter/llm/error/llm_error_report_composer",
                        "phase": "report",
                    },
                ),
                metadata=self._dag_metadata(
                    task_name="report:llm:llm_report_composer",
                    agent_path="reporter/llm/llm_report_composer",
                    objective="Compose final report via LLM.",
                ),
            )
        )
        return nodes

    def _build_subagent_invocation(
        self,
        *,
        subagent_type: str,
        objective: str | None,
        inputs: dict,
        task_name: str,
        agent_path: str,
        phase: str | None = None,
        requires_llm: bool = False,
        fallback_subagent_type: str | None = None,
        fallback_objective: str | None = None,
        fallback_inputs: dict | None = None,
        fallback_context: dict | None = None,
        error_fallback_subagent_type: str | None = None,
        error_fallback_objective: str | None = None,
        error_fallback_inputs: dict | None = None,
        error_fallback_context: dict | None = None,
    ) -> SubagentInvocation:
        context = {
            "task_name": task_name,
            "agent_path": agent_path,
            "phase": phase or "report",
        }
        resolved_fallback_context = dict(fallback_context or {})
        resolved_error_fallback_context = dict(error_fallback_context or {})
        return SubagentInvocation(
            subagent_type=subagent_type,
            objective=objective,
            inputs=inputs,
            context=context,
            requires_llm=requires_llm,
            fallback_subagent_type=fallback_subagent_type,
            fallback_objective=fallback_objective,
            fallback_inputs=dict(fallback_inputs or {}),
            fallback_context=resolved_fallback_context,
            error_fallback_subagent_type=error_fallback_subagent_type,
            error_fallback_objective=error_fallback_objective,
            error_fallback_inputs=dict(error_fallback_inputs or {}),
            error_fallback_context=resolved_error_fallback_context,
        )

    def _dag_metadata(
        self,
        *,
        task_name: str,
        agent_path: str,
        objective: str | None,
        phase: str | None = None,
    ) -> dict:
        return self.phase_controller.build_metadata(
            task_name=task_name,
            agent_path=agent_path,
            objective=objective,
            phase=phase or "report",
        )

    def _report_trace(
        self,
        *,
        intent: str,
        evidences: list[dict],
        execution_mode: str,
        runtime_degraded_note: str | None,
        branch_mode: str,
        results: dict[str, SubagentDagNodeResult],
    ) -> list[dict]:
        trace = [
            self._trace_record_from_dag_result(
                result=results["report_strategy_resolver"],
                fallback_subagent_type="report_strategy_resolver",
                fallback_objective="Resolve report strategy from retrieval summary and planning context.",
                fallback_task_name="report:strategy:report_strategy_resolver",
                fallback_agent_path="reporter/strategy/report_strategy_resolver",
            )
        ]
        terminal_result = results["report_terminal"]
        terminal_outputs = dict(terminal_result.outputs or {})
        if branch_mode == "deterministic_bank_compare":
            trace.append(
                self._trace_record_from_dag_result(
                    result=terminal_result,
                    fallback_subagent_type="bank_compare_report_composer",
                    fallback_objective="Compose deterministic bank compare report from same-basis scorecard evidence.",
                    fallback_task_name="report:deterministic:bank_compare_report_composer",
                    fallback_agent_path="reporter/deterministic/bank_compare_report_composer",
                )
            )
            return trace
        if branch_mode == "fallback":
            trace.append(
                self._trace_record_from_dag_result(
                    result=terminal_result,
                    fallback_subagent_type="fallback_report_composer",
                    fallback_objective="Compose degraded fallback report.",
                    fallback_task_name="report:fallback:fallback_report_composer",
                    fallback_agent_path="reporter/fallback/fallback_report_composer",
                    status_override="completed",
                    prefer_metadata=False,
                    extra_notes=[
                        f"evidence_count={len(evidences)}",
                        f"degraded_note={runtime_degraded_note or RuntimeDegradeReason.LLM_UNAVAILABLE.value}",
                        f"execution_mode={execution_mode}",
                    ],
                )
            )
            return trace
        degrade_reason = str(terminal_outputs.get("degrade_reason") or "").strip()
        if "selected_evidence" not in terminal_outputs and "evidence" in terminal_outputs:
            if degrade_reason.startswith("llm_error:") or (
                terminal_result.record is not None
                and str(terminal_result.record.subagent_type or "") == "llm_error_report_composer"
            ):
                trace.append(
                    self._build_trace_record(
                        subagent_type="llm_report_composer",
                        objective="Compose final report via LLM.",
                        status="failed",
                        task_name="report:llm:llm_report_composer",
                        agent_path="reporter/llm/llm_report_composer",
                        notes=[f"error_name={degrade_reason.split(':', 1)[1] or 'LLMProviderError'}"],
                    )
                )
                trace.append(
                    self._trace_record_from_dag_result(
                        result=terminal_result,
                        fallback_subagent_type="llm_error_report_composer",
                        fallback_objective="Compose degraded report after LLM error.",
                        fallback_task_name="report:llm:error:llm_error_report_composer",
                        fallback_agent_path="reporter/llm/error/llm_error_report_composer",
                        status_override="completed",
                        prefer_metadata=False,
                        extra_notes=[f"degrade_reason={degrade_reason}"] if degrade_reason else None,
                    )
                )
                return trace
            trace.append(
                self._trace_record_from_dag_result(
                    result=terminal_result,
                    fallback_subagent_type="fallback_report_composer",
                    fallback_objective="Compose degraded fallback report.",
                    fallback_task_name="report:fallback:fallback_report_composer",
                    fallback_agent_path="reporter/fallback/fallback_report_composer",
                    status_override="completed",
                    prefer_metadata=False,
                    extra_notes=[
                        f"evidence_count={len(evidences)}",
                        f"degraded_note={degrade_reason or runtime_degraded_note or RuntimeDegradeReason.RUNTIME_FALLBACK.value}",
                        "execution_mode=runtime_fallback",
                    ],
                )
            )
            return trace
        trace.append(
            self._trace_record_from_dag_result(
                result=terminal_result,
                fallback_subagent_type="llm_report_composer",
                fallback_objective="Compose final report via LLM.",
                fallback_task_name="report:llm:llm_report_composer",
                fallback_agent_path="reporter/llm/llm_report_composer",
                extra_notes=[
                    f"selected_evidence={len(list(terminal_outputs.get('selected_evidence') or []))}",
                    f"degraded_note={str(terminal_outputs.get('degraded_note') or 'none')}",
                ],
            )
        )
        return trace

    def _finalize_report_outputs(
        self,
        *,
        intent: str,
        branch_mode: str,
        evidences: list[dict],
        results: dict[str, SubagentDagNodeResult],
    ) -> dict:
        strategy_outputs = dict(results["report_strategy_resolver"].outputs or {})
        terminal_outputs = dict(results["report_terminal"].outputs or {})
        terminal_outputs.pop("notes", None)
        if branch_mode in {"deterministic_bank_compare", "fallback"}:
            return terminal_outputs
        if "selected_evidence" not in terminal_outputs and "evidence" in terminal_outputs:
            return terminal_outputs
        selected_evidence = list(terminal_outputs.get("selected_evidence") or [])
        screening_scorecard = self._screening_scorecard_payload(selected_evidence) if intent == "stock_screen" else None
        screening_candidates = self._screening_candidates_payload(selected_evidence) if intent == "stock_screen" else None
        report_strategy = dict(strategy_outputs.get("report_strategy") or {})
        return {
            "conclusion": terminal_outputs["conclusion"],
            "evidence": selected_evidence[:5],
            "scorecard": self._distillation_scorecard_payload(selected_evidence) if intent == "distill_kol_logic" else None,
            "screening_scorecard": screening_scorecard,
            "candidates": screening_candidates,
            "methodologies": strategy_outputs.get("methodology_payloads") or None,
            "review_summary": strategy_outputs.get("review_summary_payload"),
            "risk": terminal_outputs["risk"],
            "follow_up": self._merge_follow_up(terminal_outputs.get("follow_up"), report_strategy),
            "degraded": bool(terminal_outputs.get("degraded") or terminal_outputs.get("degraded_note")),
            "degrade_reason": str(
                terminal_outputs.get("degrade_reason")
                or terminal_outputs.get("degraded_note")
                or ""
            ).strip()
            or None,
            "degraded_note": terminal_outputs.get("degraded_note"),
        }

    def _trace_record_from_dag_result(
        self,
        *,
        result: SubagentDagNodeResult,
        fallback_subagent_type: str,
        fallback_objective: str,
        fallback_task_name: str,
        fallback_agent_path: str,
        status_override: str | None = None,
        prefer_metadata: bool = True,
        extra_notes: list[str] | None = None,
    ) -> dict:
        record = result.record
        notes = list((record.notes if record is not None else []) or [])
        for note in list(extra_notes or []):
            if note not in notes:
                notes.append(note)
        if result.error_summary and f"error={result.error_summary}" not in notes:
            notes.append(f"error={result.error_summary}")
        metadata = dict(result.metadata or {})
        trace_record = self._build_trace_record(
            subagent_type=str((record.subagent_type if record is not None else fallback_subagent_type) or fallback_subagent_type),
            objective=str((record.objective if record is not None else fallback_objective) or fallback_objective),
            status=status_override or result.status,
            task_name=str((metadata.get("task_name") if prefer_metadata else None) or fallback_task_name),
            agent_path=str((metadata.get("agent_path") if prefer_metadata else None) or fallback_agent_path),
            notes=notes,
            invocation_id=result.invocation_id,
            phase=str(metadata.get("phase") or "report"),
            agent=self.agent_name,
            node_id=str(metadata.get("node_id") or "") or None,
        )
        if metadata.get("plan_id"):
            trace_record["plan_id"] = metadata["plan_id"]
        if metadata.get("plan_node_id"):
            trace_record["plan_node_id"] = metadata["plan_node_id"]
        if metadata.get("subagent_plan_strategy"):
            trace_record["subagent_plan_strategy"] = metadata["subagent_plan_strategy"]
        return trace_record

    def _normalize_planning_context_hints(self, raw_hints) -> dict[str, str]:
        return normalize_planning_context_hints(raw_hints)

    def _build_trace_record(
        self,
        *,
        subagent_type: str,
        objective: str,
        status: str,
        task_name: str,
        agent_path: str,
        notes: list[str] | None = None,
        invocation_id: str | None = None,
        phase: str | None = None,
        agent: str | None = None,
        node_id: str | None = None,
    ) -> dict:
        payload = {
            "subagent_type": subagent_type,
            "objective": objective,
            "status": status,
            "task_name": task_name,
            "agent_path": agent_path,
            "notes": list(notes or []),
        }
        if invocation_id:
            payload["invocation_id"] = invocation_id
        if phase:
            payload["phase"] = phase
        if agent:
            payload["agent"] = agent
        if node_id:
            payload["node_id"] = node_id
        return payload

    def _report_strategy_trace_notes(self, report_strategy: dict[str, str | list[str] | None]) -> list[str]:
        notes: list[str] = []
        focus_area = str(report_strategy.get("focus_area") or "").strip()
        preferred_methodology = str(report_strategy.get("preferred_methodology") or "").strip()
        if focus_area:
            notes.append(f"focus_area={focus_area}")
        if preferred_methodology:
            notes.append(f"preferred_methodology={preferred_methodology}")
        strategy_notes = [str(item).strip() for item in list(report_strategy.get("notes") or []) if str(item).strip()]
        notes.extend(strategy_notes)
        if not notes:
            notes.append("report_strategy=default")
        return notes

    def _derive_report_strategy(
        self,
        *,
        query: str,
        retrieval_summary: str | None,
        planning_context_hints: dict[str, str],
    ) -> dict[str, str | list[str] | None]:
        strategy: dict[str, str | list[str] | None] = {
            "focus_area": None,
            "preferred_methodology": None,
            "notes": [],
        }
        if not planning_context_hints:
            return strategy
        context = build_planning_context_view(
            query=query,
            planning_context_hints=planning_context_hints,
            extra_texts=[str(retrieval_summary or "")],
        )
        lowered = combine_focus_text(
            context.query_text,
            str(retrieval_summary or "").strip().lower(),
            context.recommended_next_step,
            context.open_question,
            context.previous_summary,
            context.thread_topic,
        )
        preferred_methodology, focus_source = derive_methodology_focus(
            cashflow_text=lowered,
            comparison_text=lowered,
            planning_context_hints=planning_context_hints,
        )
        if preferred_methodology == "cashflow_resilience":
            strategy["focus_area"] = "cashflow_resilience"
            strategy["preferred_methodology"] = preferred_methodology
        elif preferred_methodology == "comps":
            strategy["focus_area"] = "same_basis_compare"
            strategy["preferred_methodology"] = preferred_methodology
        elif preferred_methodology == "dcf":
            strategy["focus_area"] = "valuation"
            strategy["preferred_methodology"] = preferred_methodology
        elif has_policy_focus(lowered):
            strategy["focus_area"] = "catalyst_validation"
        elif has_momentum_focus(lowered):
            strategy["focus_area"] = "momentum_validation"
        if strategy["focus_area"]:
            strategy["notes"] = [f"report_focus={strategy['focus_area']}"]
            if focus_source:
                strategy["notes"].append(f"report_focus_source={focus_source}")
        return strategy

    def _strategy_follow_up(self, report_strategy: dict[str, str | list[str] | None]) -> str | None:
        return strategy_follow_up(report_strategy)

    def _merge_follow_up(
        self,
        base_follow_up: str | None,
        report_strategy: dict[str, str | list[str] | None],
    ) -> str | None:
        strategy_follow_up = self._strategy_follow_up(report_strategy)
        if not strategy_follow_up:
            return base_follow_up
        base_text = str(base_follow_up or "").strip()
        if not base_text:
            return strategy_follow_up
        if strategy_follow_up.lower() in base_text.lower():
            return base_text
        return f"{base_text} {strategy_follow_up}"

    @staticmethod
    def _unique_notes(notes: list[str]) -> list[str]:
        unique: list[str] = []
        for note in notes:
            normalized = str(note or "").strip()
            if not normalized or normalized in unique:
                continue
            unique.append(normalized)
        return unique

    def _compare_targets_payload(self, evidences: list[dict]) -> list[dict]:
        for item in evidences:
            hint = item.get("retrieval_hint") or {}
            if hint.get("hint_type") == "compare_request":
                targets = hint.get("compare_targets") or []
                if isinstance(targets, list):
                    return [
                        {
                            "symbol": str(target.get("symbol") or "").strip(),
                            "name": str(target.get("name") or target.get("symbol") or "").strip(),
                        }
                        for target in targets
                        if isinstance(target, dict) and str(target.get("symbol") or "").strip()
                    ]
            metadata = item.get("metadata") or {}
            if str(metadata.get("source_type") or "") != "compare_request":
                continue
            summary = str(item.get("summary") or "")
            lines = summary.splitlines()
            if len(lines) < 2:
                continue
            tail = lines[-1].strip()
            import ast
            try:
                parsed = ast.literal_eval(tail)
            except Exception:
                parsed = None
            if not isinstance(parsed, dict):
                continue
            targets = parsed.get("compare_targets") or []
            if isinstance(targets, list):
                return [
                    {
                        "symbol": str(target.get("symbol") or "").strip(),
                        "name": str(target.get("name") or target.get("symbol") or "").strip(),
                    }
                    for target in targets
                    if isinstance(target, dict) and str(target.get("symbol") or "").strip()
                ]
        return []

    def _compare_digest(self, *, compare_targets: list[dict], methodology_payloads: list[dict]) -> str:
        scorecard = self._bank_compare_scorecard_payload(methodology_payloads)
        if isinstance(scorecard, dict):
            digest = self._bank_compare_digest(scorecard)
            if digest:
                return digest
        if not compare_targets:
            return "当前比较请求缺少可识别的目标股票，无法形成横向结论。"
        methodology_by_symbol: dict[str, dict[str, dict]] = {}
        for item in methodology_payloads:
            title = str(item.get("title") or "")
            symbol = title.split(":", 1)[1].strip() if ":" in title else ""
            if not symbol:
                continue
            methodology_by_symbol.setdefault(symbol, {})[str(item.get("methodology_type") or "")] = item

        lines = [f"已按同一研究口径对 {len(compare_targets)} 只股票做对比。"]
        scored_targets: list[tuple[float, str, str]] = []
        for target in compare_targets:
            symbol = str(target.get("symbol") or "").strip()
            name = str(target.get("name") or symbol).strip() or symbol
            methods = methodology_by_symbol.get(symbol, {})
            comps = methods.get("comps")
            dcf = methods.get("dcf")
            resilience = methods.get("cashflow_resilience")
            notes: list[str] = []
            score = 0.0
            if comps:
                view = self._methodology_output_value(comps, "relative_view")
                if view:
                    notes.append(f"comps={view}")
                    if view == "relative_discount_like":
                        score += 2.0
                    elif view == "growth_supported_neutral":
                        score += 1.0
                    elif view == "relative_fair_like":
                        score += 0.5
                    elif view == "relative_premium_like":
                        score -= 1.0
            if dcf:
                gap = self._methodology_output_float(dcf, "market_to_intrinsic_gap")
                if gap is not None:
                    notes.append(f"dcf_gap={gap:.2%}")
                    if gap <= -0.1:
                        score += 1.5
                    elif gap >= 0.1:
                        score -= 1.0
            if resilience:
                debt_state = self._methodology_output_value(resilience, "debt_capacity_state")
                cashflow_state = self._methodology_output_value(resilience, "cashflow_state")
                if debt_state:
                    notes.append(f"debt={debt_state}")
                    if debt_state == "comfortable":
                        score += 0.8
                    elif debt_state == "stressed":
                        score -= 1.2
                if cashflow_state:
                    notes.append(f"cashflow={cashflow_state}")
                    if cashflow_state == "resilient":
                        score += 0.8
                    elif cashflow_state == "fragile":
                        score -= 1.0
            if not notes:
                notes.append("methodology_signal=limited")
            lines.append(f"{name}（{symbol}）：{'；'.join(notes)}。")
            scored_targets.append((score, symbol, name))

        available_signal_count = sum(1 for item in scored_targets if item[0] != 0.0)
        if len(scored_targets) >= 2 and available_signal_count > 0:
            winner = max(scored_targets, key=lambda item: item[0])
            if winner[0] > 0:
                lines.append(f"基于当前相对估值、轻量 DCF 和现金流韧性信号，暂时更占优的是 {winner[2]}（{winner[1]}）。")
            else:
                lines.append("当前方法论信号没有形成明显领先者，更适合作为并列观察名单。")
        else:
            lines.append("当前证据更多是单票快照，仍需补充更完整估值和行业景气数据后再排序。")
        return " ".join(lines)

    def _bank_compare_scorecard_payload(self, items: list[dict]) -> dict | None:
        for item in items:
            source_type = str(item.get("source_type") or item.get("metadata", {}).get("source_type") or "")
            if source_type != "bank_compare_scorecard":
                continue
            summary = str(item.get("summary") or "")
            tail = summary.split("\n", 1)[1] if "\n" in summary else summary
            try:
                parsed = ast.literal_eval(tail.strip())
            except Exception:
                parsed = None
            if isinstance(parsed, dict):
                return parsed
        return None

    def _bank_compare_digest(self, scorecard: dict) -> str | None:
        ranked_candidates = list(scorecard.get("ranked_candidates") or [])
        if not ranked_candidates:
            return None
        top_tier = ranked_candidates[:3]
        metric_weights = dict(scorecard.get("metric_weights") or {})
        valuation_weight = float(metric_weights.get("pb") or 0.0)
        lines = [
            f"基于同口径银行股指标与估值锚点，本次共比较 {int(scorecard.get('candidate_count') or len(ranked_candidates))} 只银行股。",
            f"当前更值得优先跟踪的第一梯队是：{', '.join(str(item.get('name') or item.get('symbol') or '').strip() for item in top_tier if str(item.get('name') or item.get('symbol') or '').strip())}。",
        ]
        if valuation_weight > 0:
            lines.append(f"本轮排序已把 PB 纳入核心权重，权重约为 {valuation_weight:.0%}，不再只依赖经营质量代理指标。")
        for item in top_tier:
            name = str(item.get("name") or item.get("symbol") or "").strip()
            symbol = str(item.get("symbol") or "").strip()
            if not name:
                continue
            detail = f"{name}（{symbol}）"
            if item.get("score") is not None:
                detail += f" score={float(item.get('score')):.2f}"
            if item.get("pb") is not None:
                detail += f"，PB={float(item.get('pb')):.2f}"
            highlights = [str(x) for x in list(item.get("highlights") or []) if str(x).strip()]
            if highlights:
                detail += f"，核心指标：{'；'.join(highlights[:3])}"
            lines.append(detail + "。")
        leader = top_tier[0]
        leader_name = str(leader.get("name") or leader.get("symbol") or "").strip()
        if leader_name:
            lines.append(f"当前综合得分领先的是 {leader_name}，但这仍然是基于同口径估值与经营质量的快速排序，不替代完整财报建模。")
        missing_metrics = [str(item) for item in list(scorecard.get("missing_metrics") or []) if str(item).strip()]
        if missing_metrics:
            lines.append(f"仍有缺口指标：{', '.join(missing_metrics[:5])}，补齐后再做最终定序。")
        candidate_gap_digest = self._bank_compare_candidate_gap_digest(top_tier)
        if candidate_gap_digest:
            lines.append(candidate_gap_digest)
        return " ".join(lines)

    def _bank_compare_candidate_gap_digest(self, ranked_candidates: list[dict]) -> str | None:
        gap_parts: list[str] = []
        for item in ranked_candidates:
            name = str(item.get("name") or item.get("symbol") or "").strip()
            missing = [str(metric) for metric in list(item.get("missing_metrics") or []) if str(metric).strip()]
            if not name or not missing:
                continue
            gap_parts.append(f"{name} 缺少 {', '.join(missing[:3])}")
        if not gap_parts:
            return None
        return "个别银行仍有字段缺口：" + "；".join(gap_parts) + "。"

    def _methodology_output_value(self, methodology_payload: dict, key: str) -> str | None:
        result = self._parse_methodology_result(methodology_payload)
        outputs = result.get("outputs") or {}
        value = outputs.get(key)
        if value in (None, ""):
            return None
        return str(value)

    def _methodology_output_float(self, methodology_payload: dict, key: str) -> float | None:
        value = self._methodology_output_value(methodology_payload, key)
        if value is None:
            return None
        try:
            return float(value)
        except Exception:
            return None

    def _parse_methodology_result(self, methodology_payload: dict) -> dict:
        summary = str(methodology_payload.get("summary") or "")
        lines = summary.splitlines()
        payload: dict = {}
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("outputs="):
                try:
                    payload["outputs"] = ast.literal_eval(stripped.split("=", 1)[1].strip())
                except Exception:
                    payload["outputs"] = {}
            elif stripped.startswith("assumptions="):
                try:
                    payload["assumptions"] = ast.literal_eval(stripped.split("=", 1)[1].strip())
                except Exception:
                    payload["assumptions"] = {}
            elif stripped.startswith("warnings="):
                try:
                    payload["warnings"] = ast.literal_eval(stripped.split("=", 1)[1].strip())
                except Exception:
                    payload["warnings"] = []
        return payload

    def _distillation_attribution_digest(self, evidences: list[dict]) -> str:
        counts = {
            "author": 0,
            "reply": 0,
            "repost": 0,
            "reported": 0,
            "quoted": 0,
            "uncertain": 0,
        }
        for item in evidences:
            metadata = item.get("metadata") or {}
            if str(metadata.get("source_type") or "") != "logic_block":
                continue
            summary = str(item.get("summary") or "")
            marker = "speaker_type="
            if marker not in summary:
                continue
            speaker_type = summary.split(marker, 1)[1].split()[0].strip()
            if speaker_type in counts:
                counts[speaker_type] += 1

        non_zero = [f"{name}={count}" for name, count in counts.items() if count > 0]
        if not non_zero:
            return "Attribution mix unavailable from selected evidence."
        return "Attribution mix: " + ", ".join(non_zero) + "."

    def _distillation_scorecard_digest(self, evidences: list[dict]) -> str:
        for item in evidences:
            metadata = item.get("metadata") or {}
            if str(metadata.get("source_type") or "") != "distillation_scorecard":
                continue
            summary = str(item.get("summary") or "")
            if "\n" in summary:
                _, tail = summary.split("\n", 1)
                return f"Scorecard: {tail}"
            return f"Scorecard: {summary}"
        return self._distillation_attribution_digest(evidences)

    def _distillation_scorecard_payload(self, evidences: list[dict]) -> dict | None:
        for item in evidences:
            metadata = item.get("metadata") or {}
            if str(metadata.get("source_type") or "") != "distillation_scorecard":
                continue
            summary = str(item.get("summary") or "")
            if "\n" not in summary:
                return None
            _, tail = summary.split("\n", 1)
            import ast

            try:
                parsed = ast.literal_eval(tail)
            except Exception:
                return None
            return parsed if isinstance(parsed, dict) else None
        return None

    def _review_verdict_by_claim_type(self, review_findings: list[dict]) -> dict[str, ClaimVerdictBreakdown]:
        buckets: dict[str, ClaimVerdictBreakdown] = {}
        for item in review_findings:
            summary = str(item.get("summary") or "")
            if "claim_type=" not in summary or "verdict=" not in summary:
                continue
            claim_type = summary.split("claim_type=", 1)[1].split()[0].strip()
            verdict = summary.split("verdict=", 1)[1].split()[0].strip()
            if claim_type not in buckets:
                buckets[claim_type] = ClaimVerdictBreakdown()
            current = buckets[claim_type]
            if verdict == "confirmed":
                current.confirmed += 1
            elif verdict == "rejected":
                current.rejected += 1
            else:
                current.inconclusive += 1
        return buckets

    def _review_verdict_by_claim_type_payload(self, review_findings: list[dict]) -> dict[str, dict[str, int]]:
        return {
            claim_type: breakdown.model_dump(mode="json")
            for claim_type, breakdown in self._review_verdict_by_claim_type(review_findings).items()
        }

    def _review_benchmark_scope_counts(self, review_findings: list[dict]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in review_findings:
            summary = str(item.get("summary") or "")
            if "scope=" not in summary:
                continue
            scope = summary.split("scope=", 1)[1].split()[0].strip()
            counts[scope] = counts.get(scope, 0) + 1
        return counts

    def _review_benchmark_scope_explanations(self, benchmark_scope_counts: dict[str, int]) -> dict[str, str]:
        explanations: dict[str, str] = {}
        for scope in benchmark_scope_counts:
            if scope == "stock_vs_sector":
                explanations[scope] = "个股方向主张按个股相对所属板块表现复核。"
            elif scope == "sector_vs_hs300":
                explanations[scope] = "板块方向主张按板块相对 HS300 表现复核。"
            elif scope == "stock_absolute":
                explanations[scope] = "缺少板块基准时，个股方向主张按个股自身涨跌做最小复核。"
            else:
                explanations[scope] = "按当前 review policy 的默认 benchmark 口径复核。"
        return explanations

    def _review_overall_verdict(
        self,
        *,
        finding_count: int,
        confirmed: int,
        rejected: int,
        inconclusive: int,
    ) -> str:
        if finding_count == 0:
            return "no_findings"
        if rejected > 0 and confirmed == 0:
            return "rejected"
        if rejected > 0 and confirmed > 0:
            return "mixed"
        if inconclusive > 0 and confirmed == 0:
            return "inconclusive"
        if confirmed > 0 and inconclusive > 0:
            return "confirmed_with_caveats"
        if confirmed > 0:
            return "confirmed"
        return "inconclusive"

    def _review_summary_text(
        self,
        *,
        overall_verdict: str,
        finding_count: int,
        confirmed: int,
        rejected: int,
        inconclusive: int,
        benchmark_scope_counts: dict[str, int],
    ) -> str:
        scope_labels = ",".join(sorted(benchmark_scope_counts.keys())) if benchmark_scope_counts else "no_benchmark"
        return (
            f"overall_verdict={overall_verdict}; findings={finding_count}; "
            f"confirmed={confirmed}; rejected={rejected}; inconclusive={inconclusive}; "
            f"benchmark_scopes={scope_labels}"
        )

    def _normalize_review_conclusion(self, *, conclusion: str, review_summary_payload: dict | None) -> str:
        if not review_summary_payload:
            return conclusion
        overall_verdict = str(review_summary_payload.get("overall_verdict") or "").strip()
        if not overall_verdict:
            return conclusion
        if f"overall_verdict={overall_verdict}" in conclusion:
            return conclusion
        return f"overall_verdict={overall_verdict}. {conclusion}"

    def _review_summary_payload(self, evidences: list[dict]) -> dict | None:
        for item in evidences:
            metadata = item.get("metadata") or {}
            if str(metadata.get("source_type") or "") != "review_summary":
                continue
            summary = str(item.get("summary") or "")
            if "\n" not in summary:
                continue
            _, tail = summary.split("\n", 1)
            import ast
            import json

            try:
                parsed = ast.literal_eval(tail)
            except Exception:
                try:
                    parsed = json.loads(tail)
                except Exception:
                    normalized_tail = (
                        tail.replace(": true", ": True")
                        .replace(": false", ": False")
                        .replace(": null", ": None")
                    )
                    try:
                        parsed = ast.literal_eval(normalized_tail)
                    except Exception:
                        parsed = None
            if isinstance(parsed, dict):
                return parsed
        review_findings = [
            item for item in evidences
            if str((item.get("metadata") or {}).get("source_type") or "") == "review_finding"
        ]
        if not review_findings:
            return None
        confirmed = sum(1 for item in review_findings if "verdict=confirmed" in str(item.get("summary") or ""))
        rejected = sum(1 for item in review_findings if "verdict=rejected" in str(item.get("summary") or ""))
        inconclusive = sum(1 for item in review_findings if "verdict=inconclusive" in str(item.get("summary") or ""))
        claim_type_counts: dict[str, int] = {}
        verdict_by_claim_type = self._review_verdict_by_claim_type(review_findings)
        benchmark_scope_counts = self._review_benchmark_scope_counts(review_findings)
        for claim_type, breakdown in verdict_by_claim_type.items():
            claim_type_counts[claim_type] = breakdown.confirmed + breakdown.rejected + breakdown.inconclusive
        overall_verdict = self._review_overall_verdict(
            finding_count=len(review_findings),
            confirmed=confirmed,
            rejected=rejected,
            inconclusive=inconclusive,
        )
        summary = PosteriorReviewSummary(
            review_required=False,
            finding_count=len(review_findings),
            reviewable_claim_count=len(review_findings),
            claim_type_counts=claim_type_counts,
            benchmark_scope_counts=benchmark_scope_counts,
            benchmark_scope_explanations=self._review_benchmark_scope_explanations(benchmark_scope_counts),
            verdict_by_claim_type=verdict_by_claim_type,
            overall_verdict=overall_verdict,
            summary_text=self._review_summary_text(
                overall_verdict=overall_verdict,
                finding_count=len(review_findings),
                confirmed=confirmed,
                rejected=rejected,
                inconclusive=inconclusive,
                benchmark_scope_counts=benchmark_scope_counts,
            ),
            confirmed=confirmed,
            rejected=rejected,
            inconclusive=inconclusive,
            notes=[],
        )
        return summary.model_dump(mode="json")

    def _screening_scorecard_payload(self, evidences: list[dict]) -> dict | None:
        for item in evidences:
            metadata = item.get("metadata") or {}
            if str(metadata.get("source_type") or "") != "screening_scorecard":
                continue
            summary = str(item.get("summary") or "")
            if "\n" not in summary:
                return None
            _, tail = summary.split("\n", 1)
            import ast

            try:
                parsed = ast.literal_eval(tail)
            except Exception:
                return None
            return parsed if isinstance(parsed, dict) else None
        return None

    def _screening_candidates_payload(self, evidences: list[dict]) -> list[dict]:
        candidates: list[dict] = []
        for item in evidences:
            metadata = item.get("metadata") or {}
            source_type = str(metadata.get("source_type") or "")
            if source_type not in {"screen_candidate", "watchlist_candidate"}:
                continue
            summary = str(item.get("summary") or "")
            title = str(item.get("title") or "")
            symbol = title.split(":", 1)[1].strip() if ":" in title else title
            parsed_candidate = self._parse_screen_candidate_summary(summary)
            name = str(parsed_candidate.get("name") or symbol)
            rationale = str(parsed_candidate.get("why_it_made_the_list") or "")
            risk = str(parsed_candidate.get("main_risk") or "")
            score = parsed_candidate.get("score")
            missing_evidence = parsed_candidate.get("missing_evidence") or ""
            deep_checked = bool(parsed_candidate.get("deep_checked"))
            candidate = {
                "symbol": symbol,
                "name": name,
                "title": title,
                "summary": summary,
                "score": score,
                "why_it_made_the_list": rationale,
                "main_risk": risk,
                "missing_evidence": missing_evidence,
                "deep_checked": deep_checked,
                "candidate_tier": "watchlist" if source_type == "watchlist_candidate" else "core",
            }
            candidates.append(candidate)
        return candidates

    def _methodology_payloads(self, evidences: list[dict]) -> list[dict]:
        payloads: list[dict] = []
        for item in evidences:
            metadata = item.get("metadata") or {}
            source_type = str(metadata.get("source_type") or "")
            if not source_type.startswith("methodology_") and source_type != "bank_compare_scorecard":
                continue
            payloads.append(
                {
                    "title": str(item.get("title") or ""),
                    "source_type": source_type,
                    "methodology_type": metadata.get("methodology_type"),
                    "valuation_band_label": metadata.get("valuation_band_label"),
                    "summary": str(item.get("summary") or ""),
                }
            )
        return payloads

    def _parse_screen_candidate_summary(self, summary: str) -> dict:
        import ast

        lines = summary.splitlines()
        if len(lines) >= 2:
            tail = lines[-1].strip()
            if tail.startswith("{") and tail.endswith("}"):
                try:
                    parsed = ast.literal_eval(tail)
                except Exception:
                    parsed = None
                if isinstance(parsed, dict):
                    return parsed

        parsed: dict = {}
        for line in lines:
            if line.startswith("screen_candidate symbol=") or line.startswith("watchlist_candidate symbol="):
                parts = line.split()
                for part in parts:
                    if "=" not in part:
                        continue
                    key, value = part.split("=", 1)
                    if key == "symbol":
                        parsed["symbol"] = value.strip()
                    elif key == "name":
                        parsed["name"] = value.strip()
            elif line.startswith("why="):
                parsed["why_it_made_the_list"] = line.split("=", 1)[1].strip()
            elif line.startswith("risk="):
                parsed["main_risk"] = line.split("=", 1)[1].strip()
            elif line.startswith("score="):
                parsed["score"] = line.split("=", 1)[1].split()[0].strip()
            elif line.startswith("deep_checked="):
                parsed["deep_checked"] = line.split("=", 1)[1].strip().lower() == "true"
            elif line.startswith("missing_evidence="):
                parsed["missing_evidence"] = line.split("=", 1)[1].strip()
        return parsed

    def _screening_digest(self, screening_scorecard_payload: dict | None, screening_candidates_payload: list[dict]) -> str:
        if not screening_candidates_payload:
            return "未筛出满足最小条件的候选，当前更适合继续观察而不是直接给出标的。"
        core_candidates = [item for item in screening_candidates_payload if str(item.get("candidate_tier") or "core") == "core"]
        watchlist_candidates = [item for item in screening_candidates_payload if str(item.get("candidate_tier") or "") == "watchlist"]
        top_names = [str(item.get("name") or item.get("symbol") or "") for item in core_candidates[:3] if str(item.get("name") or item.get("symbol") or "").strip()]
        watch_names = [str(item.get("name") or item.get("symbol") or "") for item in watchlist_candidates[:3] if str(item.get("name") or item.get("symbol") or "").strip()]
        cycle = str((screening_scorecard_payload or {}).get("investment_cycle") or "mid_term")
        summary = str((screening_scorecard_payload or {}).get("summary") or "").strip()
        deep_checked_count = int((screening_scorecard_payload or {}).get("deep_checked_count") or 0)
        first = (core_candidates or watchlist_candidates or screening_candidates_payload)[0]
        leader = str(first.get("name") or first.get("symbol") or "").strip()
        leader_reason = str(first.get("why_it_made_the_list") or "").strip()
        leader_risk = str(first.get("main_risk") or "").strip()
        lines = [f"当前更适合作为 {cycle} 观察名单，而不是直接交易指令。"]
        if top_names:
            lines.append(f"优先关注：{', '.join(top_names)}。")
        if watch_names:
            lines.append(f"次级观察：{', '.join(watch_names)}。")
        if summary:
            lines.append(summary)
        if deep_checked_count > 0:
            lines.append(f"其中有 {deep_checked_count} 只候选已经完成市场、财务和公告的深度核查。")
        else:
            lines.append("当前候选仍以轻量筛选为主，尚未完成系统性的深度核查。")
        if leader and leader_reason:
            lines.append(f"其中 {leader} 的入选理由是：{leader_reason}。")
        if leader_risk:
            lines.append(f"主要风险是：{leader_risk}。")
        return " ".join(lines)
