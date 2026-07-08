from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from research_system.agents._subagent_dag import (
    SubagentDagExecutor,
    SubagentDagNodeResult,
    SubagentDagNodeSpec,
)
from research_system.agents._subagent_invoker import RuntimeBoundSubagentInvoker
from research_system.schemas.review import CriticReviewFinding, CriticReviewSummary
from research_system.schemas.subagents import (
    SubagentCatalogEntry,
    SubagentInvocation,
    SubagentPlan,
    SubagentPlanNode,
)
from research_system.subagents.critic_review import (
    CriticSummaryBuilderSubagent,
    DimensionCriticSubagent,
    LogicBlockCriticSubagent,
    SummaryRiskCriticSubagent,
)
from research_system.subagents.registry import SubagentRegistry


class CriticService(RuntimeBoundSubagentInvoker):
    agent_name = "critic"

    def __init__(self, runs_root: Path | None = None) -> None:
        self.runs_root = runs_root
        self.registry = SubagentRegistry(
            [
                SummaryRiskCriticSubagent(),
                LogicBlockCriticSubagent(attribution_review_payload=self._attribution_review_payload),
                DimensionCriticSubagent(),
                CriticSummaryBuilderSubagent(),
            ]
        )

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
            results = self._execute_subagent_plan(
                plan=review_plan,
                max_workers=3,
                dag_id="critic_review",
                build_invocation=self._build_review_plan_invocations(
                    plan=review_plan,
                    distillation_summary=distillation_summary,
                    logic_blocks=logic_blocks,
                    distilled_dimensions=distilled_dimensions,
                    notes=notes,
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
                "subagent_plans": [review_plan.model_dump(mode="json")],
                "subagent_plan_patches": [],
            }

    def _select_review_catalog_entry(
        self,
        *,
        capability: str,
        preferred_subagent_type: str | None = None,
    ) -> SubagentCatalogEntry:
        candidates = self.registry.catalog_entries(
            stage="review",
            intent="critic_review",
            capability=capability,
        )
        if not candidates:
            candidates = self.registry.catalog_entries(
                stage="review",
                capability=capability,
            )
        if not candidates:
            raise KeyError(f"no critic catalog entry for capability={capability!r}")
        if preferred_subagent_type:
            for candidate in candidates:
                if candidate.subagent_type == preferred_subagent_type:
                    return candidate
        return sorted(
            candidates,
            key=lambda candidate: (
                self._cost_rank(candidate.cost_class),
                self._latency_rank(candidate.latency_class),
                candidate.subagent_type,
            ),
        )[0]

    def _cost_rank(self, cost_class: str | None) -> int:
        return {"low": 0, "medium": 1, "high": 2}.get(str(cost_class or "").strip().lower(), 3)

    def _latency_rank(self, latency_class: str | None) -> int:
        return {"low": 0, "medium": 1, "high": 2}.get(str(latency_class or "").strip().lower(), 3)

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
        return SubagentPlanNode(
            node_id=node_id,
            subagent_type=subagent_type,
            objective=objective,
            inputs=dict(inputs),
            depends_on=list(depends_on or []),
            tags=list(tags or ["catalog_selected"]),
            max_attempts=max_attempts,
            replayable=True,
            cache_scope=cache_scope,  # type: ignore[arg-type]
            cache_key=cache_key,
            notes=list(notes or []),
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
                    node_id="summary_risk_critic",
                    subagent_type=risk_entry.subagent_type,
                    objective="Check summary-level distillation risks.",
                    inputs={"note_count": len(notes)},
                    tags=["catalog_selected", "parallel_candidate"],
                    cache_key="summary_risk_critic",
                    notes=["phase=review"],
                ),
                self._build_catalog_plan_node(
                    node_id="logic_block_critic",
                    subagent_type=logic_entry.subagent_type,
                    objective="Check logic-block quality and attribution risks.",
                    inputs={"logic_block_count": len(logic_blocks)},
                    tags=["catalog_selected", "parallel_candidate"],
                    cache_key="logic_block_critic",
                    notes=["phase=review"],
                ),
                self._build_catalog_plan_node(
                    node_id="dimension_critic",
                    subagent_type=dimension_entry.subagent_type,
                    objective="Check dimension coverage and confidence.",
                    inputs={
                        "dimension_count": len(distilled_dimensions),
                        "logic_block_count": len(logic_blocks),
                    },
                    tags=["catalog_selected", "parallel_candidate"],
                    cache_key="dimension_critic",
                    notes=["phase=review"],
                ),
                self._build_catalog_plan_node(
                    node_id="critic_summary_builder",
                    subagent_type=summary_entry.subagent_type,
                    objective="Build critic summary from findings.",
                    inputs={"note_count": len(notes)},
                    depends_on=["summary_risk_critic", "logic_block_critic", "dimension_critic"],
                    cache_key="critic_summary_builder",
                    notes=["phase=postprocess"],
                ),
            ],
            notes=["critic_review_catalog_plan"],
            catalog_trace={
                "stage": "review",
                "selection_mode": "catalog",
                "selected_subagent_types": {
                    "summary_risk_critic": risk_entry.subagent_type,
                    "logic_block_critic": logic_entry.subagent_type,
                    "dimension_critic": dimension_entry.subagent_type,
                    "critic_summary_builder": summary_entry.subagent_type,
                },
            },
        )

    def _execute_subagent_plan(
        self,
        *,
        plan: SubagentPlan,
        max_workers: int | None,
        dag_id: str,
        build_invocation,
    ) -> dict[str, SubagentDagNodeResult]:
        dag_nodes: list[SubagentDagNodeSpec] = []
        for plan_node in plan.nodes:
            runtime_node = build_invocation[plan_node.node_id]
            dag_nodes.append(
                SubagentDagNodeSpec(
                    node_id=plan_node.node_id,
                    build_invocation=lambda results, *, builder=runtime_node["builder"]: builder(results),
                    depends_on=tuple(plan_node.depends_on),
                    failure_mode=plan_node.failure_mode,
                    allow_partial_dependencies=plan_node.allow_partial_dependencies,
                    max_attempts=plan_node.max_attempts,
                    replayable=plan_node.replayable,
                    cache_scope=plan_node.cache_scope,
                    cache_key=plan_node.cache_key,
                    metadata={
                        **dict(runtime_node["metadata"]),
                        "plan_id": plan.plan_id,
                        "plan_node_id": plan_node.node_id,
                        "subagent_plan_strategy": plan.strategy,
                    },
                )
            )
        return self._execute_subagent_dag(
            dag_nodes,
            max_workers=max_workers,
            dag_id=dag_id,
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
                            for finding in list(((results[node_id].outputs or {}).get("findings") or []))
                        ],
                        "notes": notes,
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

    def _execute_subagent_dag(
        self,
        nodes: list[SubagentDagNodeSpec],
        *,
        max_workers: int | None = None,
        dag_id: str,
        start_from_node_id: str | None = None,
    ) -> dict[str, SubagentDagNodeResult]:
        executor = SubagentDagExecutor(
            invoke_subagent=self._invoke_subagent,
            emit_event=self._emit_runtime_event,
        )
        resolved_start_from_node_id = start_from_node_id or self._subagent_dag_start_from_node_id(
            dag_id=dag_id,
        )
        return executor.execute(
            nodes,
            max_workers=max_workers,
            cache_context=self._subagent_dag_cache_context(dag_id=dag_id),
            start_from_node_id=resolved_start_from_node_id,
        )

    def _dag_metadata(
        self,
        *,
        task_name: str,
        agent_path: str,
        objective: str | None,
    ) -> dict[str, Any]:
        return {
            "task_name": task_name,
            "agent_path": agent_path,
            "objective": objective,
            "phase": "review",
        }

    def _build_subagent_invocation(
        self,
        *,
        subagent_type: str,
        objective: str | None,
        inputs: dict[str, Any],
        task_name: str,
        agent_path: str,
    ) -> SubagentInvocation:
        return SubagentInvocation(
            subagent_type=subagent_type,
            objective=objective,
            inputs=inputs,
            context={
                "task_name": task_name,
                "agent_path": agent_path,
                "phase": "review",
            },
        )

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
