from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from research_system.agents._subagent_dag import SubagentDagNodeResult
from research_system.agents._subagent_phase_controller import SubagentPhaseController
from research_system.agents._subagent_phase_policy import CriticPhasePolicy
from research_system.agents._subagent_invoker import RuntimeBoundSubagentInvoker
from research_system.runtime.platform.settings import Settings
from research_system.runtime.providers.provider_registry import OpenAIProvider
from research_system.schemas.review import CriticReviewFinding, CriticReviewSummary
from research_system.schemas.subagents import (
    SubagentGraphPatch,
    SubagentCatalogEntry,
    SubagentInvocation,
    SubagentPlan,
    SubagentPlanNode,
)
from research_system.subagents.critic_review import (
    CriticStrategyResolverSubagent,
    CriticSummaryBuilderSubagent,
    DimensionCriticSubagent,
    LogicBlockCriticSubagent,
    SummaryRiskCriticSubagent,
)
from research_system.subagents.registry import SubagentRegistry


class CriticService(RuntimeBoundSubagentInvoker):
    agent_name = "critic"

    def __init__(
        self,
        runs_root: Path | None = None,
        llm_provider: OpenAIProvider | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.llm_provider = llm_provider
        self.settings = settings
        self.runs_root = runs_root
        self.registry = SubagentRegistry(
            [
                CriticStrategyResolverSubagent(),
                SummaryRiskCriticSubagent(),
                LogicBlockCriticSubagent(attribution_review_payload=self._attribution_review_payload),
                DimensionCriticSubagent(),
                CriticSummaryBuilderSubagent(),
            ],
            catalog_overlay_provider=self._approved_catalog_overlay_entries,
            activation_provider=self._activated_discovery_alias_entries,
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
        self.phase_policy = CriticPhasePolicy()

    def run(
        self,
        *,
        distillation_summary: dict | None,
        logic_blocks: list[dict],
        distilled_dimensions: list[dict],
    ) -> dict:
        with self.local_run_scope(
            intent="critic_review",
            query=str((distillation_summary or {}).get("content_type") or "critic_review"),
            phase="review",
            title="critic",
            objective="Review distillation quality and produce critic findings.",
        ):
            notes: list[str] = []

            summary_notes = list((distillation_summary or {}).get("processing_notes") or [])
            if summary_notes:
                notes.extend(summary_notes[:3])
            review_plan = self._build_review_subagent_plan(
                notes=notes,
                logic_blocks=logic_blocks,
                distilled_dimensions=distilled_dimensions,
            )
            review_plan, _base_results, review_patch_rows, results = self.phase_controller.execute_patch_cycle(
                plan=review_plan,
                dag_id="critic_review",
                base_max_workers=1,
                final_max_workers=3,
                build_invocation_factory=lambda current_plan: self._build_review_plan_invocations(
                    plan=current_plan,
                    distillation_summary=distillation_summary,
                    logic_blocks=logic_blocks,
                    distilled_dimensions=distilled_dimensions,
                    notes=notes,
                ),
                patch_builder=lambda base_results: self._build_critic_replan_patch(base_results=base_results),
                replan_builder=lambda current_plan, patch, base_results: self._rebuild_review_subagent_plan(
                    plan=current_plan,
                    patch=patch,
                    base_results=base_results,
                    notes=notes,
                    logic_blocks=logic_blocks,
                    distilled_dimensions=distilled_dimensions,
                ),
                candidate_plan_variants_builder=lambda rule_final_plan, patch, base_results: self._build_critic_candidate_plan_variants(
                    rule_final_plan=rule_final_plan,
                    patch=patch,
                    base_results=base_results,
                    notes=notes,
                    logic_blocks=logic_blocks,
                    distilled_dimensions=distilled_dimensions,
                ),
            )
            summary_outputs = results["critic_summary_builder"].outputs or {}
            scorecard = self._build_scorecard(
                logic_blocks=logic_blocks,
                findings=[CriticReviewFinding.model_validate(item) for item in summary_outputs["findings"]],
                summary=summary_outputs["summary"],
            )
            return {
                "summary": summary_outputs["summary"],
                "scorecard": scorecard,
                "findings": summary_outputs["findings"],
                "subagent_trace": self.phase_controller.trace_from_results(results=results),
                "subagent_plans": [review_plan.model_dump(mode="json")],
                "subagent_plan_patches": review_patch_rows,
            }

    def _select_review_catalog_entry(
        self,
        *,
        capability: str,
        preferred_subagent_type: str | None = None,
    ) -> SubagentCatalogEntry:
        return self.phase_controller.select_catalog_entry(
            stage="review",
            intent="critic_review",
            capability=capability,
            preferred_subagent_type=preferred_subagent_type,
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

    def _build_review_subagent_plan(
        self,
        *,
        notes: list[str],
        logic_blocks: list[dict],
        distilled_dimensions: list[dict],
    ) -> SubagentPlan:
        risk_entry = self._select_review_catalog_entry(
            capability="summary_risk_review",
            preferred_subagent_type="summary_risk_critic",
        )
        logic_entry = self._select_review_catalog_entry(
            capability="logic_block_review",
            preferred_subagent_type="logic_block_critic",
        )
        dimension_entry = self._select_review_catalog_entry(
            capability="dimension_review",
            preferred_subagent_type="dimension_critic",
        )
        summary_entry = self._select_review_catalog_entry(
            capability="critic_summary",
            preferred_subagent_type="critic_summary_builder",
        )
        return SubagentPlan(
            intent="critic_review",
            phase="review",
            strategy="catalog",
            nodes=[
                self._build_catalog_plan_node(
                    node_id="critic_strategy_resolver",
                    subagent_type=self._select_review_catalog_entry(
                        capability="critic_strategy",
                        preferred_subagent_type="critic_strategy_resolver",
                    ).subagent_type,
                    objective="Resolve critic-review execution strategy from distillation artifacts.",
                    inputs={},
                    cache_key="critic_strategy_resolver",
                    notes=["phase=preprocess"],
                ),
            ],
            notes=["critic_review_base_catalog_plan"],
            catalog_trace={
                "stage": "review",
                "selection_mode": "catalog",
                "selected_subagent_types": {
                    "critic_strategy_resolver": "critic_strategy_resolver",
                },
            },
        )

    def _rebuild_review_subagent_plan(
        self,
        *,
        plan: SubagentPlan,
        patch: SubagentGraphPatch,
        base_results: dict[str, SubagentDagNodeResult],
        notes: list[str],
        logic_blocks: list[dict],
        distilled_dimensions: list[dict],
        include_logic_block_review: bool | None = None,
        include_dimension_review: bool | None = None,
    ) -> SubagentPlan:
        _ = plan
        strategy_outputs = dict(((base_results["critic_strategy_resolver"].outputs or {}).get("review_strategy") or {}))
        metadata = dict((patch.operations[0].metadata or {}) if patch.operations else {})
        include_logic = (
            bool(include_logic_block_review)
            if include_logic_block_review is not None
            else str(metadata.get("include_logic_block_review") or strategy_outputs.get("include_logic_block_review") or "").lower() == "true"
        )
        include_dimension = (
            bool(include_dimension_review)
            if include_dimension_review is not None
            else str(metadata.get("include_dimension_review") or strategy_outputs.get("include_dimension_review") or "").lower() == "true"
        )
        risk_entry = self._select_review_catalog_entry(
            capability="summary_risk_review",
            preferred_subagent_type="summary_risk_critic",
        )
        logic_entry = self._select_review_catalog_entry(
            capability="logic_block_review",
            preferred_subagent_type="logic_block_critic",
        )
        dimension_entry = self._select_review_catalog_entry(
            capability="dimension_review",
            preferred_subagent_type="dimension_critic",
        )
        summary_entry = self._select_review_catalog_entry(
            capability="critic_summary",
            preferred_subagent_type="critic_summary_builder",
        )
        nodes = [
            self._build_catalog_plan_node(
                node_id="critic_strategy_resolver",
                subagent_type="critic_strategy_resolver",
                objective="Resolve critic-review execution strategy from distillation artifacts.",
                inputs={},
                cache_key="critic_strategy_resolver",
                notes=["phase=preprocess"],
            ),
            self._build_catalog_plan_node(
                node_id="summary_risk_critic",
                subagent_type=risk_entry.subagent_type,
                objective="Check summary-level distillation risks.",
                inputs={"note_count": len(notes)},
                depends_on=["critic_strategy_resolver"],
                tags=["catalog_selected", "parallel_candidate"],
                cache_key="summary_risk_critic",
                notes=["phase=review"],
            ),
        ]
        critic_dependencies = ["summary_risk_critic"]
        if include_logic:
            nodes.append(
                self._build_catalog_plan_node(
                    node_id="logic_block_critic",
                    subagent_type=logic_entry.subagent_type,
                    objective="Check logic-block quality and attribution risks.",
                    inputs={"logic_block_count": len(logic_blocks)},
                    depends_on=["critic_strategy_resolver"],
                    tags=["catalog_selected", "parallel_candidate"],
                    cache_key="logic_block_critic",
                    notes=["phase=review"],
                )
            )
            critic_dependencies.append("logic_block_critic")
        if include_dimension:
            nodes.append(
                self._build_catalog_plan_node(
                    node_id="dimension_critic",
                    subagent_type=dimension_entry.subagent_type,
                    objective="Check dimension coverage and confidence.",
                    inputs={
                        "dimension_count": len(distilled_dimensions),
                        "logic_block_count": len(logic_blocks),
                    },
                    depends_on=["critic_strategy_resolver"],
                    tags=["catalog_selected", "parallel_candidate"],
                    cache_key="dimension_critic",
                    notes=["phase=review"],
                )
            )
            critic_dependencies.append("dimension_critic")
        nodes.append(
            self._build_catalog_plan_node(
                node_id="critic_summary_builder",
                subagent_type=summary_entry.subagent_type,
                objective="Build critic summary from findings.",
                inputs={"note_count": len(notes)},
                depends_on=critic_dependencies,
                cache_key="critic_summary_builder",
                notes=["phase=postprocess"],
            )
        )
        review_mode = "full_parallel" if include_logic or include_dimension else "summary_only"
        return SubagentPlan(
            intent="critic_review",
            phase="review",
            strategy="catalog",
            nodes=nodes,
            notes=self._unique_notes(
                [
                    "critic_review_replanned_from_strategy",
                    f"critic_review_mode={review_mode}",
                    f"include_logic_block_review={str(include_logic).lower()}",
                    f"include_dimension_review={str(include_dimension).lower()}",
                ]
            ),
            catalog_trace={
                "stage": "review",
                "selection_mode": "catalog",
                "replanned_from_strategy": True,
                "selected_subagent_types": {
                    "critic_strategy_resolver": "critic_strategy_resolver",
                    "summary_risk_critic": risk_entry.subagent_type,
                    "logic_block_critic": logic_entry.subagent_type if include_logic else None,
                    "dimension_critic": dimension_entry.subagent_type if include_dimension else None,
                    "critic_summary_builder": summary_entry.subagent_type,
                },
                "review_mode": review_mode,
            },
        )

    def _build_critic_replan_patch(
        self,
        *,
        base_results: dict[str, SubagentDagNodeResult],
    ) -> SubagentGraphPatch:
        strategy_outputs = dict(((base_results["critic_strategy_resolver"].outputs or {}).get("review_strategy") or {}))
        return self.phase_policy.build_critic_replan_patch(
            review_mode=str(strategy_outputs.get("review_mode") or "summary_only"),
            include_logic_block_review=bool(strategy_outputs.get("include_logic_block_review")),
            include_dimension_review=bool(strategy_outputs.get("include_dimension_review")),
            summary_risk_count=len(list(strategy_outputs.get("notes") or [])),
        )

    def _build_critic_candidate_plan_variants(
        self,
        *,
        rule_final_plan: SubagentPlan,
        patch: SubagentGraphPatch | None,
        base_results: dict[str, SubagentDagNodeResult],
        notes: list[str],
        logic_blocks: list[dict],
        distilled_dimensions: list[dict],
    ) -> dict[str, SubagentPlan]:
        rule_variant_id = (
            "full_parallel"
            if any(node.node_id in {"logic_block_critic", "dimension_critic"} for node in rule_final_plan.nodes)
            else "summary_only"
        )
        variants: dict[str, SubagentPlan] = {rule_variant_id: rule_final_plan}
        variants["summary_only"] = variants.get("summary_only") or self._rebuild_review_subagent_plan(
            plan=self._build_review_subagent_plan(notes=notes, logic_blocks=logic_blocks, distilled_dimensions=distilled_dimensions),
            patch=patch or self.phase_policy.build_critic_replan_patch(
                review_mode="summary_only",
                include_logic_block_review=False,
                include_dimension_review=False,
                summary_risk_count=0,
            ),
            base_results=base_results,
            notes=notes,
            logic_blocks=logic_blocks,
            distilled_dimensions=distilled_dimensions,
            include_logic_block_review=False,
            include_dimension_review=False,
        )
        variants["summary_only"].notes = self._unique_notes([*list(variants["summary_only"].notes or []), "critic_candidate_variant", "critic_review_mode=summary_only"])
        if logic_blocks or distilled_dimensions:
            variants["full_parallel"] = variants.get("full_parallel") or self._rebuild_review_subagent_plan(
                plan=self._build_review_subagent_plan(notes=notes, logic_blocks=logic_blocks, distilled_dimensions=distilled_dimensions),
                patch=patch or self.phase_policy.build_critic_replan_patch(
                    review_mode="full_parallel",
                    include_logic_block_review=bool(logic_blocks),
                    include_dimension_review=bool(distilled_dimensions or logic_blocks),
                    summary_risk_count=0,
                ),
                base_results=base_results,
                notes=notes,
                logic_blocks=logic_blocks,
                distilled_dimensions=distilled_dimensions,
                include_logic_block_review=bool(logic_blocks),
                include_dimension_review=bool(distilled_dimensions or logic_blocks),
            )
            variants["full_parallel"].notes = self._unique_notes([*list(variants["full_parallel"].notes or []), "critic_candidate_variant", "critic_review_mode=full_parallel"])
        return variants

    def _execute_subagent_plan(
        self,
        *,
        plan: SubagentPlan,
        max_workers: int | None,
        dag_id: str,
        build_invocation,
    ) -> dict[str, SubagentDagNodeResult]:
        return self.phase_controller.execute_plan(
            plan=plan,
            build_invocation=build_invocation,
            dag_id=dag_id,
            max_workers=max_workers,
        )

    def _build_review_plan_invocations(
        self,
        *,
        plan: SubagentPlan,
        distillation_summary: dict | None,
        logic_blocks: list[dict],
        distilled_dimensions: list[dict],
        notes: list[str],
    ) -> dict[str, dict[str, Any]]:
        _ = plan
        return {
            "critic_strategy_resolver": {
                "builder": lambda _results: self._build_subagent_invocation(
                    subagent_type="critic_strategy_resolver",
                    objective="Resolve critic-review execution strategy from distillation artifacts.",
                    inputs={
                        "distillation_summary": distillation_summary,
                        "logic_blocks": logic_blocks,
                        "distilled_dimensions": distilled_dimensions,
                        "notes": notes,
                    },
                    task_name="critic:preprocess:critic_strategy_resolver",
                    agent_path="critic/preprocess/critic_strategy_resolver",
                ),
                "metadata": self._dag_metadata(
                    task_name="critic:preprocess:critic_strategy_resolver",
                    agent_path="critic/preprocess/critic_strategy_resolver",
                    objective="Resolve critic-review execution strategy from distillation artifacts.",
                ),
            },
            "summary_risk_critic": {
                "builder": lambda _results: self._build_subagent_invocation(
                    subagent_type="summary_risk_critic",
                    objective="Check summary-level distillation risks.",
                    inputs={"distillation_summary": distillation_summary},
                    task_name="critic:review:summary_risk_critic",
                    agent_path="critic/review/summary_risk_critic",
                ),
                "metadata": self._dag_metadata(
                    task_name="critic:review:summary_risk_critic",
                    agent_path="critic/review/summary_risk_critic",
                    objective="Check summary-level distillation risks.",
                ),
            },
            "logic_block_critic": {
                "builder": lambda _results: self._build_subagent_invocation(
                    subagent_type="logic_block_critic",
                    objective="Check logic-block quality and attribution risks.",
                    inputs={"logic_blocks": logic_blocks},
                    task_name="critic:review:logic_block_critic",
                    agent_path="critic/review/logic_block_critic",
                ),
                "metadata": self._dag_metadata(
                    task_name="critic:review:logic_block_critic",
                    agent_path="critic/review/logic_block_critic",
                    objective="Check logic-block quality and attribution risks.",
                ),
            },
            "dimension_critic": {
                "builder": lambda _results: self._build_subagent_invocation(
                    subagent_type="dimension_critic",
                    objective="Check dimension coverage and confidence.",
                    inputs={"distilled_dimensions": distilled_dimensions, "logic_blocks": logic_blocks},
                    task_name="critic:review:dimension_critic",
                    agent_path="critic/review/dimension_critic",
                ),
                "metadata": self._dag_metadata(
                    task_name="critic:review:dimension_critic",
                    agent_path="critic/review/dimension_critic",
                    objective="Check dimension coverage and confidence.",
                ),
            },
            "critic_summary_builder": {
                "builder": lambda results: self._build_subagent_invocation(
                    subagent_type="critic_summary_builder",
                    objective="Build critic summary from findings.",
                    inputs={
                        "findings": [
                            finding
                            for node_id in ("summary_risk_critic", "logic_block_critic", "dimension_critic")
                            for finding in list(
                                (
                                    (((results.get(node_id) or {}).outputs if results.get(node_id) is not None else {}) or {})
                                    .get("findings")
                                    or []
                                )
                            )
                        ],
                        "notes": self._merge_summary_notes(results=results, notes=notes),
                    },
                    task_name="critic:postprocess:critic_summary_builder",
                    agent_path="critic/postprocess/critic_summary_builder",
                ),
                "metadata": self._dag_metadata(
                    task_name="critic:postprocess:critic_summary_builder",
                    agent_path="critic/postprocess/critic_summary_builder",
                    objective="Build critic summary from findings.",
                ),
            },
        }

    def _dag_metadata(
        self,
        *,
        task_name: str,
        agent_path: str,
        objective: str | None,
    ) -> dict[str, Any]:
        return self.phase_controller.build_metadata(
            task_name=task_name,
            agent_path=agent_path,
            objective=objective,
            phase="review",
        )

    def _build_subagent_invocation(
        self,
        *,
        subagent_type: str,
        objective: str | None,
        inputs: dict[str, Any],
        task_name: str,
        agent_path: str,
    ) -> SubagentInvocation:
        return self.phase_controller.build_invocation(
            subagent_type=subagent_type,
            objective=objective,
            inputs=inputs,
            task_name=task_name,
            agent_path=agent_path,
            phase="review",
        )

    def _merge_summary_notes(self, *, results: dict[str, SubagentDagNodeResult], notes: list[str]) -> list[str]:
        strategy_outputs = dict(((results.get("critic_strategy_resolver").outputs or {}).get("review_strategy") or {})) if results.get("critic_strategy_resolver") is not None else {}
        merged = list(notes)
        for item in list(strategy_outputs.get("notes") or []):
            normalized = str(item or "").strip()
            if normalized and normalized not in merged:
                merged.append(normalized)
        return merged

    def _build_scorecard(self, *, logic_blocks: list[dict], findings: list[CriticReviewFinding], summary: dict) -> dict:
        speaker_counts: dict[str, int] = {
            "author": 0,
            "reply": 0,
            "repost": 0,
            "reported": 0,
            "quoted": 0,
            "uncertain": 0,
        }
        for block in logic_blocks:
            speaker_type = str(block.get("speaker_type") or "")
            if speaker_type in speaker_counts:
                speaker_counts[speaker_type] += 1

        total_logic_blocks = max(len(logic_blocks), 1)
        quoted_like = (
            speaker_counts["reply"]
            + speaker_counts["repost"]
            + speaker_counts["reported"]
            + speaker_counts["quoted"]
            + speaker_counts["uncertain"]
        )
        review_strength = "high" if summary["high_severity_count"] > 0 else "medium" if summary["medium_severity_count"] > 0 else "low"
        attribution_risk = "high" if speaker_counts["repost"] > 0 else "medium" if quoted_like > 0 else "low"
        author_ratio = round(speaker_counts["author"] / total_logic_blocks, 4)
        quoted_ratio = round(quoted_like / total_logic_blocks, 4)
        setup_label = "author_led" if author_ratio >= 0.7 else "mixed_attribution" if author_ratio >= 0.4 else "quote_dominated"

        return {
            "total_logic_blocks": len(logic_blocks),
            "speaker_type_counts": speaker_counts,
            "author_ratio": author_ratio,
            "quoted_like_ratio": quoted_ratio,
            "review_strength": review_strength,
            "attribution_risk": attribution_risk,
            "setup_label": setup_label,
            "summary": (
                f"attribution={setup_label}; author_ratio={author_ratio}; "
                f"quoted_like_ratio={quoted_ratio}; review_strength={review_strength}"
            ),
            "finding_types": summary.get("finding_types", []),
        }

    def _attribution_review_payload(self, *, block: dict, risk_flags: list[str]) -> tuple[str, str, str]:
        speaker_type = str(block.get("speaker_type") or "")
        attribution_label = str(block.get("attribution_label") or "")

        if speaker_type == "reply" or "reply_attribution" in risk_flags:
            return (
                "medium",
                "Logic block appears to be a reply to another user, so the stance may depend on prior context.",
                "Review the upstream conversation before treating this as a standalone KOL thesis.",
            )
        if speaker_type == "repost" or "repost_attribution" in risk_flags:
            return (
                "high",
                "Logic block appears to be reposted or forwarded content rather than the author's original statement.",
                "Trace the original source before counting this block as first-hand viewpoint evidence.",
            )
        if speaker_type == "reported" or "reported_attribution" in risk_flags:
            return (
                "medium",
                "Logic block is reported speech or paraphrase, so wording may not reflect the source precisely.",
                "Check the original referenced statement and avoid over-interpreting paraphrased claims.",
            )
        if speaker_type == "quoted" or attribution_label == "explicit_quote" or "explicit_quote_marker" in risk_flags:
            return (
                "medium",
                "Logic block contains explicit quoted content and may not represent the author's own viewpoint.",
                "Keep the quote/source distinction explicit in downstream summaries and review the original wording.",
            )
        return (
            "medium",
            "Logic block may contain quoted or forwarded content rather than the author's own view.",
            "Confirm speaker attribution before counting it as a primary KOL viewpoint.",
        )

    @staticmethod
    def _unique_notes(notes: list[str]) -> list[str]:
        unique: list[str] = []
        for note in notes:
            normalized = str(note or "").strip()
            if not normalized or normalized in unique:
                continue
            unique.append(normalized)
        return unique
