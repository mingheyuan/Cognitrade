from __future__ import annotations

from pathlib import Path
from typing import Any

from research_system.agents._subagent_phase_controller import SubagentPhaseController
from research_system.agents._subagent_phase_policy import CollectorPhasePolicy
from research_system.schemas.subagents import (
    SubagentGraphPatch,
    SubagentCatalogEntry,
    SubagentExecutionRecord,
    SubagentInvocation,
    SubagentPlan,
    SubagentPlanNode,
)
from research_system.runtime.platform.settings import Settings
from research_system.runtime.providers.provider_registry import OpenAIProvider
from research_system.runtime.platform.skill_runtime import SkillRuntime
from research_system.agents._subagent_invoker import RuntimeBoundSubagentInvoker
from research_system.services.routing.stock_target_resolution import (
    extract_stock_symbols,
    primary_stock_symbol,
    stock_name_for_symbol,
)
from research_system.services.methodologies.planning.planning_context import (
    build_planning_context_view,
    combine_focus_text,
    derive_methodology_focus,
    has_cashflow_focus,
    has_long_term_bias,
    has_mid_term_bias,
    has_momentum_focus,
    has_notice_focus,
    has_policy_focus,
    normalize_planning_context_hints,
)
from research_system.subagents.collector import (
    CandidateUniverseBuilderSubagent,
    CollectionStrategyResolverSubagent,
    QueryNormalizerSubagent,
    ReviewTargetLoaderSubagent,
    SectorSnapshotCollectorSubagent,
    StockSnapshotCollectorSubagent,
    _extract_investment_cycle as extract_investment_cycle,
    _extract_jsonl_path as extract_jsonl_path,
    _extract_sector_query as extract_sector_query,
    _extract_symbol as extract_symbol,
)
from research_system.subagents.registry import SubagentRegistry


class CollectorService(RuntimeBoundSubagentInvoker):
    agent_name = "collector"

    def __init__(
        self,
        skill_runtime: SkillRuntime,
        runs_root: Path | None = None,
        llm_provider: OpenAIProvider | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._skill_runtime = skill_runtime
        self.llm_provider = llm_provider
        self.settings = settings
        self.runs_root = runs_root
        self.registry = SubagentRegistry(
            [
                QueryNormalizerSubagent(normalize_query=self._normalize_query),
                CollectionStrategyResolverSubagent(derive_collection_strategy=self._derive_collection_strategy),
                StockSnapshotCollectorSubagent(skill_runtime),
                SectorSnapshotCollectorSubagent(skill_runtime),
                CandidateUniverseBuilderSubagent(skill_runtime),
                ReviewTargetLoaderSubagent(runs_root),
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
        self.phase_policy = CollectorPhasePolicy()

    @property
    def skill_runtime(self) -> SkillRuntime:
        return self._skill_runtime

    @skill_runtime.setter
    def skill_runtime(self, value: SkillRuntime) -> None:
        self._skill_runtime = value
        for subagent in self.registry._subagents.values():
            if hasattr(subagent, "skill_runtime"):
                setattr(subagent, "skill_runtime", value)

    def run(self, *, intent: str, query: str, constraints: dict | None = None) -> dict:
        with self.local_run_scope(
            intent=intent,
            query=query,
            phase="collect",
            title="collector",
            objective="Collect source records and snapshots for the active request.",
        ):
            return self._run_impl(intent=intent, query=query, constraints=constraints)

    def _run_impl(self, *, intent: str, query: str, constraints: dict | None = None) -> dict:
        constraints = constraints or {}
        if "[wait_external]" in query:
            return {
                "requires_wait": True,
                "sources": [],
                "snapshots": [],
                "subagent_trace": [],
                "subagent_plans": [],
            }
        if intent in {"stock_check", "stock_compare", "sector_research", "stock_screen", "review_thesis"}:
            return self._run_strategy_controlled_collect(
                intent=intent,
                query=query,
                constraints=constraints,
            )
        trace = []
        subagent_plans: list[dict[str, Any]] = []
        normalized, trace_record = self._execute_single_subagent_node(
            node_id="query_normalizer",
            invocation=SubagentInvocation(
                subagent_type="query_normalizer",
                objective="Extract structured query targets.",
                inputs={"query": query},
                context={
                    "task_name": "collect:query_normalizer",
                    "agent_path": "collector/query_normalizer",
                },
            ),
        )
        trace.append(trace_record)
        symbol = str(constraints.get("symbol") or normalized.get("symbol") or "").strip() or None
        symbols = list(constraints.get("symbols") or normalized.get("symbols") or [])
        sector_query = str(constraints.get("sector_query") or normalized.get("sector_query") or "").strip() or None
        investment_cycle = constraints.get("investment_cycle") or normalized.get("investment_cycle")
        planning_context_hints = self._normalize_planning_context_hints(constraints.get("planning_context_hints"))
        collection_strategy = self._derive_collection_strategy(
            intent=intent,
            query=query,
            planning_context_hints=planning_context_hints,
            investment_cycle=investment_cycle,
        )
        if planning_context_hints or collection_strategy.get("notes"):
            trace.append(
                self._build_trace_record(
                    subagent_type="collection_strategy_resolver",
                    objective="Resolve planning-aware collection strategy.",
                    inputs={
                        "intent": intent,
                        "query": query,
                        "planning_context_hints": planning_context_hints,
                    },
                    outputs={"collection_strategy": collection_strategy, "notes": list(collection_strategy.get("notes") or [])},
                )
            )
        source_type_override = str(constraints.get("source_type") or "").strip() or None
        snapshot_payloads = []
        source_payload = {
            "title": f"{intent} input query",
            "content": query,
            "source_type": source_type_override or "user_query",
            "symbol": symbol,
        }
        if symbols:
            source_payload["symbols"] = symbols
        if planning_context_hints:
            source_payload["planning_context_hints"] = planning_context_hints
        if collection_strategy:
            source_payload["collection_strategy"] = collection_strategy
        child_thread_ids: list[str] = []

        if intent == "stock_check":
            requested_snapshot_types = self._merge_snapshot_types(
                constraints.get("snapshot_types")
                or ["market", "fundamental", "notice"],
                collection_strategy.get("hint_augmented_snapshot_types") or [],
            )
            plan, invocations = self._build_single_node_collect_plan(
                intent=intent,
                node_id="stock_snapshot_collector",
                capability="stock_snapshot_bundle",
                objective="Collect stock-level verified snapshots.",
                inputs={
                    "query": query,
                    "symbol": symbol,
                    "snapshot_types": requested_snapshot_types,
                    "allow_partial": bool(constraints.get("allow_partial_snapshot_types")) or bool(collection_strategy.get("hint_augmented_snapshot_types")),
                },
                task_name="collect:stock_snapshot_collector",
                agent_path="collector/stock_snapshot_collector",
                max_attempts=2,
            )
            subagent_plans.append(plan.model_dump(mode="json"))
            stock_outputs, plan_trace = self._execute_subagent_plan(
                plan=plan,
                invocations=invocations,
            )
            trace.extend(plan_trace)
            snapshot_payloads.extend(list((stock_outputs.get("stock_snapshot_collector") or {}).get("snapshots") or []))
        elif intent == "stock_compare":
            requested_snapshot_types = self._merge_snapshot_types(
                constraints.get("snapshot_types")
                or ["market", "fundamental", "notice"],
                collection_strategy.get("hint_augmented_snapshot_types") or [],
            )
            compare_symbols = [item for item in symbols if str(item).strip()]
            if symbol and symbol not in compare_symbols:
                compare_symbols.append(symbol)
            if not compare_symbols:
                raise ValueError("stock_compare requires at least two target stocks")
            compare_targets = self._build_compare_targets(query=query, symbols=compare_symbols)
            source_payload.update(
                {
                    "title": "stock_compare input request",
                    "source_type": "compare_request",
                    "symbols": compare_symbols,
                    "compare_targets": compare_targets,
                }
            )
            compare_jobs: list[tuple[str, object | None, SubagentInvocation]] = []
            for compare_symbol in compare_symbols:
                child_thread, child_turn = self.spawn_child_thread(
                    intent="collector_compare_target",
                    objective=f"collect:compare:{compare_symbol}",
                    agent=self.agent_name,
                    phase="collect_compare_target",
                )
                compare_jobs.append(
                    (
                        compare_symbol,
                        child_thread,
                        self._build_stock_snapshot_invocation(
                            objective="Collect stock-level verified snapshots for comparison targets.",
                            query=compare_symbol,
                            symbol=compare_symbol,
                            snapshot_types=requested_snapshot_types,
                            allow_partial=bool(constraints.get("allow_partial_snapshot_types")) or bool(collection_strategy.get("hint_augmented_snapshot_types")),
                            task_name=f"collect:compare:{compare_symbol}:stock_snapshot_collector",
                            agent_path=f"collector/compare/{compare_symbol}/stock_snapshot_collector",
                            child_thread=child_thread,
                            child_turn=child_turn,
                            phase="collect_compare_target",
                        ),
                    )
                )
            compare_plan, compare_invocations = self._build_stock_snapshot_job_plan(
                intent=intent,
                jobs=compare_jobs,
                task_name_prefix="collect:compare",
                agent_path_prefix="collector/compare",
            )
            subagent_plans.append(compare_plan.model_dump(mode="json"))
            compare_outputs, compare_trace_rows = self._execute_subagent_plan(
                plan=compare_plan,
                invocations=compare_invocations,
                max_workers=len(compare_plan.nodes),
            )
            compare_snapshots = []
            compare_child_thread_ids: list[str] = []
            for trace_row, node in zip(compare_trace_rows, compare_plan.nodes, strict=False):
                compare_snapshots.extend(list((compare_outputs.get(node.node_id) or {}).get("snapshots") or []))
                child_thread_id = str(trace_row.get("child_thread_id") or "").strip()
                if child_thread_id:
                    compare_child_thread_ids.append(child_thread_id)
            trace.extend(compare_trace_rows)
            snapshot_payloads.extend(compare_snapshots)
            child_thread_ids.extend(compare_child_thread_ids)
        elif intent == "sector_research":
            include_policy_snapshot = bool(constraints.get("include_policy_snapshot")) or bool(collection_strategy.get("include_policy_snapshot"))
            plan, invocations = self._build_single_node_collect_plan(
                intent=intent,
                node_id="sector_snapshot_collector",
                capability="sector_snapshot",
                objective="Collect sector-level verified snapshot.",
                inputs={"query": query, "include_policy_snapshot": include_policy_snapshot},
                task_name="collect:sector_snapshot_collector",
                agent_path="collector/sector_snapshot_collector",
            )
            subagent_plans.append(plan.model_dump(mode="json"))
            sector_outputs, plan_trace = self._execute_subagent_plan(
                plan=plan,
                invocations=invocations,
            )
            trace.extend(plan_trace)
            sector_snapshot = dict((sector_outputs.get("sector_snapshot_collector") or {}).get("snapshot") or {})
            source_payload["sector"] = sector_snapshot.get("sector")
            source_payload["theme"] = sector_snapshot.get("theme")
            if include_policy_snapshot:
                source_payload["include_policy_snapshot"] = True
            snapshot_payloads.append(sector_snapshot)
            if include_policy_snapshot:
                snapshot_payloads.extend(list((sector_outputs.get("sector_snapshot_collector") or {}).get("related_snapshots") or []))
        elif intent == "stock_screen":
            candidate_pool_size = int(constraints.get("candidate_pool_size") or 8)
            resolved_cycle = investment_cycle or constraints.get("investment_cycle") or "mid_term"
            plan, invocations = self._build_single_node_collect_plan(
                intent=intent,
                node_id="candidate_universe_builder",
                capability="candidate_universe",
                objective="Build candidate universe for stock screening.",
                inputs={
                    "query": query,
                    "sector_query": sector_query,
                    "top_n": candidate_pool_size,
                },
                task_name="collect:candidate_universe_builder",
                agent_path="collector/candidate_universe_builder",
            )
            subagent_plans.append(plan.model_dump(mode="json"))
            universe_outputs, plan_trace = self._execute_subagent_plan(
                plan=plan,
                invocations=invocations,
            )
            trace.extend(plan_trace)
            selected_universe_outputs = dict(universe_outputs.get("candidate_universe_builder") or {})
            source_payload.update(
                {
                    "title": "stock_screen input request",
                    "source_type": "screen_request",
                    "investment_cycle": collection_strategy.get("resolved_investment_cycle") or resolved_cycle,
                    "investment_cycle_assumption": (
                        str(collection_strategy.get("cycle_assumption") or "").strip()
                        or ("default_mid_term" if not investment_cycle and not constraints.get("investment_cycle") else "explicit")
                    ),
                    "candidate_universe": selected_universe_outputs.get("candidate_universe"),
                    "sector_context": selected_universe_outputs.get("sector_context"),
                }
            )
            sector_context = selected_universe_outputs.get("sector_context")
            if sector_context and sector_context.get("sector_snapshot"):
                sector_snapshot = sector_context["sector_snapshot"]
                source_payload["sector"] = sector_snapshot.get("sector")
                source_payload["theme"] = sector_snapshot.get("theme")
                snapshot_payloads.append(sector_snapshot)
        elif intent == "distill_kol_logic":
            source_payload["source_type"] = source_type_override or "distill_request"
            distill_outputs = self._build_distill_request_payload(
                query=query,
                jsonl_path=normalized.get("jsonl_path"),
            )
            trace.append(
                self._build_trace_record(
                    subagent_type="distill_request_loader",
                    objective="Resolve distillation source request.",
                    inputs={"query": query, "jsonl_path": normalized.get("jsonl_path")},
                    outputs=distill_outputs,
                )
            )
            source_payload.update(distill_outputs)
        elif intent == "review_thesis":
            loader_plan, loader_invocations = self._build_single_node_collect_plan(
                intent=intent,
                node_id="review_target_loader",
                capability="review_target_loading",
                objective="Load prior run as review target.",
                inputs={"constraints": constraints, "query": query},
                task_name="collect:review_target_loader",
                agent_path="collector/review_target_loader",
            )
            subagent_plans.append(loader_plan.model_dump(mode="json"))
            review_outputs, loader_trace = self._execute_subagent_plan(
                plan=loader_plan,
                invocations=loader_invocations,
            )
            trace.extend(loader_trace)
            review_payload = dict(review_outputs.get("review_target_loader") or {})
            source_payload.update(
                {
                    "title": "review_thesis input request",
                    "source_type": source_type_override or "review_request",
                    "review_target_run_id": review_payload["review_target_run_id"],
                    "review_source_entries": review_payload["review_source_entries"],
                    "review_source_result": review_payload["review_source_result"],
                    "symbol": review_payload.get("symbol"),
                }
            )
            sector = review_payload.get("sector")
            resolved_symbol = review_payload.get("symbol")
            followup_nodes: list[SubagentPlanNode] = []
            followup_invocations: dict[str, SubagentInvocation] = {}
            if sector:
                source_payload["sector"] = sector
                followup_nodes.append(
                    self._build_catalog_plan_node(
                        intent=intent,
                        capability="sector_snapshot",
                        node_id="review_sector_snapshot_collector",
                        objective="Collect sector snapshot for posterior review.",
                        cache_key="review_sector_snapshot_collector",
                        notes=["review_followup"],
                    )
                )
                selected_sector_entry = self._select_collect_catalog_entry(
                    intent=intent,
                    capability="sector_snapshot",
                )
                followup_invocations["review_sector_snapshot_collector"] = SubagentInvocation(
                    subagent_type=selected_sector_entry.subagent_type,
                    objective="Collect sector snapshot for posterior review.",
                    inputs={"query": sector, "include_policy_snapshot": bool(constraints.get("include_policy_snapshot"))},
                    context={
                        "task_name": "collect:review:sector_snapshot_collector",
                        "agent_path": "collector/review/sector_snapshot_collector",
                    },
                )
            if resolved_symbol:
                source_payload["symbol"] = resolved_symbol
                requested_snapshot_types = self._merge_snapshot_types(
                    constraints.get("snapshot_types")
                    or ["market", "fundamental", "notice"],
                    collection_strategy.get("hint_augmented_snapshot_types") or [],
                )
                followup_nodes.append(
                    self._build_catalog_plan_node(
                        intent=intent,
                        capability="stock_snapshot_bundle",
                        node_id="review_stock_snapshot_collector",
                        objective="Collect stock snapshots for posterior review.",
                        cache_key="review_stock_snapshot_collector",
                        max_attempts=2,
                        notes=["review_followup"],
                    )
                )
                selected_stock_entry = self._select_collect_catalog_entry(
                    intent=intent,
                    capability="stock_snapshot_bundle",
                )
                followup_invocations["review_stock_snapshot_collector"] = SubagentInvocation(
                    subagent_type=selected_stock_entry.subagent_type,
                    objective="Collect stock snapshots for posterior review.",
                    inputs={
                        "query": resolved_symbol,
                        "symbol": resolved_symbol,
                        "snapshot_types": requested_snapshot_types,
                        "allow_partial": bool(collection_strategy.get("hint_augmented_snapshot_types")),
                    },
                    context={
                        "task_name": "collect:review:stock_snapshot_collector",
                        "agent_path": "collector/review/stock_snapshot_collector",
                    },
                )
            if followup_nodes:
                followup_plan = SubagentPlan(
                    intent=intent,
                    phase="collect",
                    strategy="catalog",
                    nodes=followup_nodes,
                    notes=["review_followup_plan"],
                    catalog_trace={
                        "stage": "collect",
                        "selection_mode": "catalog",
                        "node_ids": [node.node_id for node in followup_nodes],
                    },
                )
                subagent_plans.append(followup_plan.model_dump(mode="json"))
                followup_outputs, followup_trace = self._execute_subagent_plan(
                    plan=followup_plan,
                    invocations=followup_invocations,
                    max_workers=len(followup_nodes),
                )
                trace.extend(followup_trace)
                if sector:
                    snapshot_payloads.append(
                        dict((followup_outputs.get("review_sector_snapshot_collector") or {}).get("snapshot") or {})
                    )
                if resolved_symbol:
                    snapshot_payloads.extend(
                        list((followup_outputs.get("review_stock_snapshot_collector") or {}).get("snapshots") or [])
                    )
        else:
            plan, invocations = self._build_single_node_collect_plan(
                intent=intent,
                node_id="default_stock_snapshot_collector",
                capability="stock_snapshot_bundle",
                objective="Collect default market snapshot.",
                inputs={
                    "query": query,
                    "symbol": symbol,
                    "snapshot_types": ["market"],
                },
                task_name="collect:default:stock_snapshot_collector",
                agent_path="collector/default/stock_snapshot_collector",
                max_attempts=2,
            )
            subagent_plans.append(plan.model_dump(mode="json"))
            stock_outputs, plan_trace = self._execute_subagent_plan(
                plan=plan,
                invocations=invocations,
            )
            trace.extend(plan_trace)
            snapshot_payloads.extend(list((stock_outputs.get("default_stock_snapshot_collector") or {}).get("snapshots") or []))

        if child_thread_ids:
            source_payload["child_thread_ids"] = sorted(set(child_thread_ids))
        return {
            "sources": [source_payload],
            "snapshots": snapshot_payloads,
            "subagent_trace": trace,
            "subagent_plans": subagent_plans,
        }

    def _run_strategy_controlled_collect(
        self,
        *,
        intent: str,
        query: str,
        constraints: dict[str, Any],
    ) -> dict:
        planning_context_hints = self._normalize_planning_context_hints(constraints.get("planning_context_hints"))
        preprocess_plan = self._build_collect_preprocess_plan(
            intent=intent,
            query=query,
            planning_context_hints=planning_context_hints,
            investment_cycle=constraints.get("investment_cycle"),
            constraints=constraints,
        )
        final_plan, _base_results, patch_rows, final_results = self.phase_controller.execute_patch_cycle(
            plan=preprocess_plan,
            dag_id=f"collect_{intent}",
            base_max_workers=1,
            final_max_workers=self._collect_strategy_final_max_workers(
                intent=intent,
                query=query,
                constraints=constraints,
            ),
            build_invocation_factory=lambda current_plan: self._build_collect_phase_invocations(plan=current_plan),
            patch_builder=lambda base_results: self._build_collect_replan_patch(
                intent=intent,
                query=query,
                constraints=constraints,
                base_results=base_results,
            ),
            replan_builder=lambda current_plan, patch, base_results: self._rebuild_collect_plan(
                plan=current_plan,
                intent=intent,
                query=query,
                constraints=constraints,
                planning_context_hints=planning_context_hints,
                patch=patch,
                base_results=base_results,
            ),
            candidate_plan_variants_builder=lambda rule_final_plan, patch, base_results: self._build_collect_candidate_plan_variants(
                rule_final_plan=rule_final_plan,
                intent=intent,
                query=query,
                constraints=constraints,
                planning_context_hints=planning_context_hints,
                patch=patch,
                base_results=base_results,
            ),
        )
        return self._finalize_strategy_controlled_collect(
            intent=intent,
            query=query,
            constraints=constraints,
            final_plan=final_plan,
            final_results=final_results,
            patch_rows=patch_rows,
        )

    def _build_collect_preprocess_plan(
        self,
        *,
        intent: str,
        query: str,
        planning_context_hints: dict[str, str],
        investment_cycle: str | None,
        constraints: dict[str, Any] | None = None,
    ) -> SubagentPlan:
        query_node = self._build_collect_plan_node_with_inputs(
            intent=intent,
            capability="query_normalization",
            node_id="query_normalizer",
            objective="Extract structured collection targets from the user query.",
            inputs={"query": query},
            cache_key="query_normalizer",
            notes=["phase=preprocess"],
        )
        strategy_node = self._build_collect_plan_node_with_inputs(
            intent=intent,
            capability="collection_strategy",
            node_id="collection_strategy_resolver",
            objective="Resolve planning-aware collection strategy and data augmentation hints.",
            inputs={
                "intent": intent,
                "query": query,
                "planning_context_hints": dict(planning_context_hints or {}),
                "investment_cycle": investment_cycle,
            },
            depends_on=["query_normalizer"],
            cache_key="collection_strategy_resolver",
            notes=["phase=preprocess"],
        )
        nodes = [query_node, strategy_node]
        selected_subagent_types = {
            "query_normalizer": query_node.subagent_type,
            "collection_strategy_resolver": strategy_node.subagent_type,
        }
        if intent == "review_thesis":
            review_loader_node = self._build_collect_plan_node_with_inputs(
                intent=intent,
                capability="review_target_loading",
                node_id="review_target_loader",
                objective="Load prior run as review target.",
                inputs={"constraints": dict(constraints or {}), "query": query},
                cache_key="review_target_loader",
                notes=["phase=preprocess"],
            )
            nodes.append(review_loader_node)
            selected_subagent_types["review_target_loader"] = review_loader_node.subagent_type
        return SubagentPlan(
            intent=intent,
            phase="collect",
            strategy="catalog",
            nodes=nodes,
            notes=["collect_preprocess_plan"],
            catalog_trace={
                "stage": "collect",
                "selection_mode": "catalog",
                "selected_subagent_types": selected_subagent_types,
            },
        )

    def _build_collect_replan_patch(
        self,
        *,
        intent: str,
        query: str,
        constraints: dict[str, Any],
        base_results,
    ) -> SubagentGraphPatch:
        normalized = dict(base_results["query_normalizer"].outputs or {})
        strategy = dict(((base_results["collection_strategy_resolver"].outputs or {}).get("collection_strategy") or {}))
        if intent == "stock_check":
            snapshot_types = self._merge_snapshot_types(
                constraints.get("snapshot_types") or ["market", "fundamental", "notice"],
                strategy.get("hint_augmented_snapshot_types") or [],
            )
            allow_partial = bool(constraints.get("allow_partial_snapshot_types")) or bool(
                strategy.get("hint_augmented_snapshot_types")
            )
            return self.phase_policy.build_stock_check_replan_patch(
                snapshot_types=snapshot_types,
                allow_partial=allow_partial,
            )
        if intent == "stock_compare":
            snapshot_types = self._merge_snapshot_types(
                constraints.get("snapshot_types") or ["market", "fundamental", "notice"],
                strategy.get("hint_augmented_snapshot_types") or [],
            )
            allow_partial = bool(constraints.get("allow_partial_snapshot_types")) or bool(
                strategy.get("hint_augmented_snapshot_types")
            )
            compare_symbols = self._resolved_compare_symbols(
                query=query,
                constraints=constraints,
                normalized=normalized,
            )
            return self.phase_policy.build_stock_compare_replan_patch(
                snapshot_types=snapshot_types,
                allow_partial=allow_partial,
                symbols=compare_symbols,
            )
        if intent == "sector_research":
            include_policy_snapshot = bool(constraints.get("include_policy_snapshot")) or bool(
                strategy.get("include_policy_snapshot")
            )
            return self.phase_policy.build_sector_research_replan_patch(
                include_policy_snapshot=include_policy_snapshot,
            )
        if intent == "stock_screen":
            sector_query = str(
                constraints.get("sector_query")
                or normalized.get("sector_query")
                or ""
            ).strip() or None
            top_n = int(constraints.get("candidate_pool_size") or 8)
            return self.phase_policy.build_stock_screen_replan_patch(
                sector_query=sector_query,
                top_n=top_n,
            )
        if intent == "review_thesis":
            review_payload = dict((base_results["review_target_loader"].outputs or {}) if base_results.get("review_target_loader") else {})
            sector = str(review_payload.get("sector") or "").strip() or None
            resolved_symbol = str(review_payload.get("symbol") or "").strip() or None
            followup_mode = self._review_followup_mode(
                sector=sector,
                symbol=resolved_symbol,
            )
            snapshot_types = self._merge_snapshot_types(
                constraints.get("snapshot_types") or ["market", "fundamental", "notice"],
                strategy.get("hint_augmented_snapshot_types") or [],
            )
            allow_partial = bool(strategy.get("hint_augmented_snapshot_types"))
            include_policy_snapshot = bool(constraints.get("include_policy_snapshot")) or bool(
                strategy.get("include_policy_snapshot")
            )
            return self.phase_policy.build_review_thesis_replan_patch(
                followup_mode=followup_mode,
                sector=sector,
                symbol=resolved_symbol,
                snapshot_types=snapshot_types,
                allow_partial=allow_partial,
                include_policy_snapshot=include_policy_snapshot,
            )
        raise ValueError(f"unsupported strategy-controlled collector intent: {intent}")

    def _rebuild_collect_plan(
        self,
        *,
        plan: SubagentPlan,
        intent: str,
        query: str,
        constraints: dict[str, Any],
        planning_context_hints: dict[str, str],
        patch: SubagentGraphPatch,
        base_results,
    ) -> SubagentPlan:
        _ = plan
        normalized = dict(base_results["query_normalizer"].outputs or {})
        metadata = dict((patch.operations[0].metadata or {}) if patch.operations else {})
        preprocess_plan = self._build_collect_preprocess_plan(
            intent=intent,
            query=query,
            planning_context_hints=planning_context_hints,
            investment_cycle=constraints.get("investment_cycle") or normalized.get("investment_cycle"),
            constraints=constraints,
        )
        nodes = [node.model_copy(deep=True) for node in preprocess_plan.nodes]
        notes = list(preprocess_plan.notes or [])
        if intent == "stock_check":
            symbol = str(constraints.get("symbol") or normalized.get("symbol") or "").strip() or None
            snapshot_types = [
                item for item in str(metadata.get("snapshot_types") or "").split(",") if str(item).strip()
            ] or ["market", "fundamental", "notice"]
            allow_partial = str(metadata.get("allow_partial") or "").lower() == "true"
            nodes.append(
                self._build_collect_plan_node_with_inputs(
                    intent=intent,
                    capability="stock_snapshot_bundle",
                    node_id="stock_snapshot_collector",
                    objective="Collect stock-level verified snapshots.",
                    inputs={
                        "query": query,
                        "symbol": symbol,
                        "snapshot_types": snapshot_types,
                        "allow_partial": allow_partial,
                    },
                    depends_on=["collection_strategy_resolver"],
                    cache_key="stock_snapshot_collector",
                    max_attempts=2,
                    notes=["phase=collect_execution", f"snapshot_types={','.join(snapshot_types)}"],
                )
            )
            notes.extend(
                [
                    "collect_stock_check_replanned_from_strategy",
                    f"snapshot_types={','.join(snapshot_types)}",
                ]
            )
        elif intent == "stock_compare":
            snapshot_types = [
                item for item in str(metadata.get("snapshot_types") or "").split(",") if str(item).strip()
            ] or ["market", "fundamental", "notice"]
            allow_partial = str(metadata.get("allow_partial") or "").lower() == "true"
            compare_symbols = self._resolved_compare_symbols(
                query=query,
                constraints=constraints,
                normalized=normalized,
                metadata=metadata,
            )
            if not compare_symbols:
                raise ValueError("stock_compare requires at least one compare symbol for replanned collection")
            for index, compare_symbol in enumerate(compare_symbols):
                nodes.append(
                    self._build_collect_plan_node_with_inputs(
                        intent=intent,
                        capability="stock_snapshot_bundle",
                        node_id=f"compare_stock_snapshot_collector:{index}:{compare_symbol}",
                        objective="Collect stock-level verified snapshots for comparison targets.",
                        inputs={
                            "query": compare_symbol,
                            "symbol": compare_symbol,
                            "snapshot_types": snapshot_types,
                            "allow_partial": allow_partial,
                        },
                        depends_on=["collection_strategy_resolver"],
                        cache_key=f"compare_stock_snapshot_collector:{compare_symbol}",
                        max_attempts=2,
                        notes=[
                            "phase=collect_execution",
                            f"compare_symbol={compare_symbol}",
                            f"snapshot_types={','.join(snapshot_types)}",
                        ],
                    )
                )
            notes.extend(
                [
                    "collect_stock_compare_replanned_from_strategy",
                    f"compare_symbol_count={len(compare_symbols)}",
                    f"snapshot_types={','.join(snapshot_types)}",
                ]
            )
        elif intent == "sector_research":
            include_policy_snapshot = str(metadata.get("include_policy_snapshot") or "").lower() == "true"
            nodes.append(
                self._build_collect_plan_node_with_inputs(
                    intent=intent,
                    capability="sector_snapshot",
                    node_id="sector_snapshot_collector",
                    objective="Collect sector-level verified snapshot.",
                    inputs={
                        "query": str(constraints.get("sector_query") or normalized.get("sector_query") or query),
                        "include_policy_snapshot": include_policy_snapshot,
                    },
                    depends_on=["collection_strategy_resolver"],
                    cache_key="sector_snapshot_collector",
                    notes=["phase=collect_execution", f"include_policy_snapshot={str(include_policy_snapshot).lower()}"],
                )
            )
            notes.extend(
                [
                    "collect_sector_research_replanned_from_strategy",
                    f"include_policy_snapshot={str(include_policy_snapshot).lower()}",
                ]
            )
        elif intent == "stock_screen":
            sector_query = str(metadata.get("sector_query") or "").strip() or None
            top_n = int(str(metadata.get("top_n") or "8") or 8)
            nodes.append(
                self._build_collect_plan_node_with_inputs(
                    intent=intent,
                    capability="candidate_universe",
                    node_id="candidate_universe_builder",
                    objective="Build candidate universe for stock screening.",
                    inputs={
                        "query": query,
                        "sector_query": sector_query,
                        "top_n": top_n,
                    },
                    depends_on=["collection_strategy_resolver"],
                    cache_key="candidate_universe_builder",
                    notes=[
                        "phase=collect_execution",
                        f"universe_mode={'sector_seeded' if sector_query else 'market_slice'}",
                        f"top_n={top_n}",
                    ],
                )
            )
            notes.extend(
                [
                    "collect_stock_screen_replanned_from_strategy",
                    f"universe_mode={'sector_seeded' if sector_query else 'market_slice'}",
                    f"top_n={top_n}",
                ]
            )
        elif intent == "review_thesis":
            review_payload = dict((base_results["review_target_loader"].outputs or {}) if base_results.get("review_target_loader") else {})
            followup_mode = str(metadata.get("followup_mode") or "").strip() or self._review_followup_mode(
                sector=str(review_payload.get("sector") or "").strip() or None,
                symbol=str(review_payload.get("symbol") or "").strip() or None,
            )
            include_policy_snapshot = str(metadata.get("include_policy_snapshot") or "").lower() == "true"
            sector = str(metadata.get("sector") or review_payload.get("sector") or "").strip() or None
            resolved_symbol = str(metadata.get("symbol") or review_payload.get("symbol") or "").strip() or None
            snapshot_types = [
                item for item in str(metadata.get("snapshot_types") or "").split(",") if str(item).strip()
            ] or ["market", "fundamental", "notice"]
            allow_partial = str(metadata.get("allow_partial") or "").lower() == "true"
            if followup_mode in {"sector_review_followup", "combined_review_followup"} and sector:
                nodes.append(
                    self._build_collect_plan_node_with_inputs(
                        intent=intent,
                        capability="sector_snapshot",
                        node_id="review_sector_snapshot_collector",
                        objective="Collect sector snapshot for posterior review.",
                        inputs={
                            "query": sector,
                            "include_policy_snapshot": include_policy_snapshot,
                        },
                        depends_on=["collection_strategy_resolver", "review_target_loader"],
                        cache_key="review_sector_snapshot_collector",
                        notes=[
                            "phase=collect_execution",
                            "review_followup",
                            f"include_policy_snapshot={str(include_policy_snapshot).lower()}",
                        ],
                    )
                )
            if followup_mode in {"stock_review_followup", "combined_review_followup"} and resolved_symbol:
                nodes.append(
                    self._build_collect_plan_node_with_inputs(
                        intent=intent,
                        capability="stock_snapshot_bundle",
                        node_id="review_stock_snapshot_collector",
                        objective="Collect stock snapshots for posterior review.",
                        inputs={
                            "query": resolved_symbol,
                            "symbol": resolved_symbol,
                            "snapshot_types": snapshot_types,
                            "allow_partial": allow_partial,
                        },
                        depends_on=["collection_strategy_resolver", "review_target_loader"],
                        cache_key="review_stock_snapshot_collector",
                        max_attempts=2,
                        notes=[
                            "phase=collect_execution",
                            "review_followup",
                            f"snapshot_types={','.join(snapshot_types)}",
                        ],
                    )
                )
            notes.extend(
                [
                    "collect_review_thesis_replanned_from_strategy",
                    f"followup_mode={followup_mode}",
                ]
            )
        else:
            raise ValueError(f"unsupported strategy-controlled collector intent: {intent}")
        return SubagentPlan(
            intent=intent,
            phase="collect",
            strategy="catalog",
            nodes=nodes,
            notes=self._unique_notes(notes),
            catalog_trace={
                **dict(preprocess_plan.catalog_trace or {}),
                "replanned_from_strategy": True,
                "final_node_ids": [
                    node.node_id
                    for node in nodes
                    if node.node_id not in {"query_normalizer", "collection_strategy_resolver", "review_target_loader"}
                ],
            },
        )

    def _build_collect_candidate_plan_variants(
        self,
        *,
        rule_final_plan: SubagentPlan,
        intent: str,
        query: str,
        constraints: dict[str, Any],
        planning_context_hints: dict[str, str],
        patch: SubagentGraphPatch | None,
        base_results,
    ) -> dict[str, SubagentPlan]:
        normalized = dict(base_results["query_normalizer"].outputs or {})
        strategy = dict(((base_results["collection_strategy_resolver"].outputs or {}).get("collection_strategy") or {}))
        preprocess_plan = self._build_collect_preprocess_plan(
            intent=intent,
            query=query,
            planning_context_hints=planning_context_hints,
            investment_cycle=constraints.get("investment_cycle") or normalized.get("investment_cycle"),
            constraints=constraints,
        )
        variants: dict[str, SubagentPlan] = {}
        if intent == "stock_check":
            base_snapshot_types = self._merge_snapshot_types(
                constraints.get("snapshot_types") or ["market", "fundamental", "notice"],
                [],
            )
            augmented_snapshot_types = self._merge_snapshot_types(
                base_snapshot_types,
                strategy.get("hint_augmented_snapshot_types") or [],
            )
            rule_variant_id = (
                "augmented_snapshot_bundle"
                if augmented_snapshot_types != base_snapshot_types
                and self._stock_check_plan_snapshot_types(rule_final_plan) == augmented_snapshot_types
                else "baseline_snapshot_bundle"
            )
            variants[rule_variant_id] = rule_final_plan
            if augmented_snapshot_types != base_snapshot_types:
                for variant_id, snapshot_types in {
                    "baseline_snapshot_bundle": base_snapshot_types,
                    "augmented_snapshot_bundle": augmented_snapshot_types,
                }.items():
                    if variant_id in variants:
                        continue
                    variants[variant_id] = self._rebuild_collect_plan(
                        plan=preprocess_plan,
                        intent=intent,
                        query=query,
                        constraints=constraints,
                        planning_context_hints=planning_context_hints,
                        patch=self.phase_policy.build_stock_check_replan_patch(
                            snapshot_types=snapshot_types,
                            allow_partial=variant_id == "augmented_snapshot_bundle",
                        ),
                        base_results=base_results,
                    )
            return variants
        if intent == "stock_compare":
            base_snapshot_types = self._merge_snapshot_types(
                constraints.get("snapshot_types") or ["market", "fundamental", "notice"],
                [],
            )
            augmented_snapshot_types = self._merge_snapshot_types(
                base_snapshot_types,
                strategy.get("hint_augmented_snapshot_types") or [],
            )
            rule_variant_id = (
                "augmented_compare_snapshot_bundle"
                if augmented_snapshot_types != base_snapshot_types
                and self._stock_compare_plan_snapshot_types(rule_final_plan) == augmented_snapshot_types
                else "baseline_compare_snapshot_bundle"
            )
            variants[rule_variant_id] = rule_final_plan
            if augmented_snapshot_types != base_snapshot_types:
                compare_symbols = self._resolved_compare_symbols(
                    query=query,
                    constraints=constraints,
                    normalized=normalized,
                )
                for variant_id, snapshot_types in {
                    "baseline_compare_snapshot_bundle": base_snapshot_types,
                    "augmented_compare_snapshot_bundle": augmented_snapshot_types,
                }.items():
                    if variant_id in variants:
                        continue
                    variants[variant_id] = self._rebuild_collect_plan(
                        plan=preprocess_plan,
                        intent=intent,
                        query=query,
                        constraints=constraints,
                        planning_context_hints=planning_context_hints,
                        patch=self.phase_policy.build_stock_compare_replan_patch(
                            snapshot_types=snapshot_types,
                            allow_partial=variant_id == "augmented_compare_snapshot_bundle",
                            symbols=compare_symbols,
                        ),
                        base_results=base_results,
                    )
            return variants
        if intent == "sector_research":
            include_policy = self._sector_research_plan_includes_policy(rule_final_plan)
            variants["sector_plus_policy" if include_policy else "sector_only"] = rule_final_plan
            for variant_id, flag in {"sector_only": False, "sector_plus_policy": True}.items():
                if variant_id in variants:
                    continue
                variants[variant_id] = self._rebuild_collect_plan(
                    plan=preprocess_plan,
                    intent=intent,
                    query=query,
                    constraints=constraints,
                    planning_context_hints=planning_context_hints,
                    patch=self.phase_policy.build_sector_research_replan_patch(include_policy_snapshot=flag),
                    base_results=base_results,
                )
            return variants
        if intent == "stock_screen":
            sector_query = str(constraints.get("sector_query") or normalized.get("sector_query") or "").strip() or None
            rule_variant_id = "sector_seeded_universe" if self._stock_screen_plan_sector_query(rule_final_plan) else "market_slice_universe"
            variants[rule_variant_id] = rule_final_plan
            if sector_query:
                for variant_id, current_sector_query in {
                    "sector_seeded_universe": sector_query,
                    "market_slice_universe": None,
                }.items():
                    if variant_id in variants:
                        continue
                    variants[variant_id] = self._rebuild_collect_plan(
                        plan=preprocess_plan,
                        intent=intent,
                        query=query,
                        constraints=constraints,
                        planning_context_hints=planning_context_hints,
                        patch=self.phase_policy.build_stock_screen_replan_patch(
                            sector_query=current_sector_query,
                            top_n=int(constraints.get("candidate_pool_size") or 8),
                        ),
                        base_results=base_results,
                    )
            return variants
        if intent == "review_thesis":
            review_payload = dict((base_results["review_target_loader"].outputs or {}) if base_results.get("review_target_loader") else {})
            sector = str(review_payload.get("sector") or "").strip() or None
            resolved_symbol = str(review_payload.get("symbol") or "").strip() or None
            followup_mode = self._review_followup_mode_from_plan(rule_final_plan)
            variants[followup_mode] = rule_final_plan
            if sector and resolved_symbol:
                snapshot_types = self._review_stock_snapshot_types_from_plan(rule_final_plan) or self._merge_snapshot_types(
                    constraints.get("snapshot_types") or ["market", "fundamental", "notice"],
                    strategy.get("hint_augmented_snapshot_types") or [],
                )
                include_policy_snapshot = self._review_plan_includes_policy(rule_final_plan)
                for variant_id in [
                    "combined_review_followup",
                    "sector_review_followup",
                    "stock_review_followup",
                ]:
                    if variant_id in variants:
                        continue
                    variants[variant_id] = self._rebuild_collect_plan(
                        plan=preprocess_plan,
                        intent=intent,
                        query=query,
                        constraints=constraints,
                        planning_context_hints=planning_context_hints,
                        patch=self.phase_policy.build_review_thesis_replan_patch(
                            followup_mode=variant_id,
                            sector=sector,
                            symbol=resolved_symbol,
                            snapshot_types=snapshot_types,
                            allow_partial=bool(strategy.get("hint_augmented_snapshot_types")),
                            include_policy_snapshot=include_policy_snapshot,
                        ),
                        base_results=base_results,
                    )
            return variants
        return {self._resolve_collect_variant_id(intent=intent, plan=rule_final_plan): rule_final_plan}

    def _finalize_strategy_controlled_collect(
        self,
        *,
        intent: str,
        query: str,
        constraints: dict[str, Any],
        final_plan: SubagentPlan,
        final_results,
        patch_rows: list[dict[str, Any]],
    ) -> dict:
        normalized = dict(final_results["query_normalizer"].outputs or {})
        collection_strategy = dict(((final_results["collection_strategy_resolver"].outputs or {}).get("collection_strategy") or {}))
        planning_context_hints = self._normalize_planning_context_hints(constraints.get("planning_context_hints"))
        trace_rows = self._annotate_collect_trace_rows(
            self.phase_controller.trace_from_results(results=final_results)
        )
        source_type_override = str(constraints.get("source_type") or "").strip() or None
        source_payload = {
            "title": f"{intent} input query",
            "content": query,
            "source_type": source_type_override or ("screen_request" if intent == "stock_screen" else "user_query"),
            "symbol": str(constraints.get("symbol") or normalized.get("symbol") or "").strip() or None,
            "collection_strategy": collection_strategy,
        }
        symbols = [str(item).strip() for item in list(constraints.get("symbols") or normalized.get("symbols") or []) if str(item).strip()]
        if symbols:
            source_payload["symbols"] = symbols
        if planning_context_hints:
            source_payload["planning_context_hints"] = planning_context_hints
        snapshots: list[dict] = []
        if intent == "stock_check":
            snapshots = list((final_results["stock_snapshot_collector"].outputs or {}).get("snapshots") or [])
        elif intent == "stock_compare":
            compare_symbols = self._stock_compare_plan_symbols(final_plan) or self._resolved_compare_symbols(
                query=query,
                constraints=constraints,
                normalized=normalized,
            )
            source_payload.update(
                {
                    "title": "stock_compare input request",
                    "source_type": "compare_request",
                    "symbols": compare_symbols,
                    "compare_targets": self._build_compare_targets(query=query, symbols=compare_symbols),
                }
            )
            child_thread_ids: list[str] = []
            for node in final_plan.nodes:
                if not node.node_id.startswith("compare_stock_snapshot_collector:"):
                    continue
                node_result = final_results.get(node.node_id)
                if node_result is None:
                    continue
                snapshots.extend(list((node_result.outputs or {}).get("snapshots") or []))
                child_thread_id = str((node_result.metadata or {}).get("child_thread_id") or "").strip()
                if child_thread_id:
                    child_thread_ids.append(child_thread_id)
            if child_thread_ids:
                source_payload["child_thread_ids"] = sorted(set(child_thread_ids))
        elif intent == "sector_research":
            sector_outputs = dict(final_results["sector_snapshot_collector"].outputs or {})
            sector_snapshot = dict(sector_outputs.get("snapshot") or {})
            source_payload["sector"] = sector_snapshot.get("sector")
            source_payload["theme"] = sector_snapshot.get("theme")
            if sector_outputs.get("related_snapshots"):
                source_payload["include_policy_snapshot"] = True
            if sector_snapshot:
                snapshots.append(sector_snapshot)
            snapshots.extend(list(sector_outputs.get("related_snapshots") or []))
        elif intent == "stock_screen":
            universe_outputs = dict(final_results["candidate_universe_builder"].outputs or {})
            source_payload.update(
                {
                    "source_type": "screen_request",
                    "investment_cycle": collection_strategy.get("resolved_investment_cycle") or constraints.get("investment_cycle") or normalized.get("investment_cycle") or "mid_term",
                    "investment_cycle_assumption": (
                        str(collection_strategy.get("cycle_assumption") or "").strip()
                        or ("default_mid_term" if not constraints.get("investment_cycle") and not normalized.get("investment_cycle") else "explicit")
                    ),
                    "candidate_universe": universe_outputs.get("candidate_universe"),
                    "sector_context": universe_outputs.get("sector_context"),
                }
            )
            sector_context = universe_outputs.get("sector_context") or {}
            sector_snapshot = sector_context.get("sector_snapshot")
            if sector_snapshot:
                source_payload["sector"] = sector_snapshot.get("sector")
                source_payload["theme"] = sector_snapshot.get("theme")
                snapshots.append(sector_snapshot)
        elif intent == "review_thesis":
            review_payload = dict((final_results["review_target_loader"].outputs or {}) if final_results.get("review_target_loader") else {})
            source_payload.update(
                {
                    "title": "review_thesis input request",
                    "source_type": source_type_override or "review_request",
                    "review_target_run_id": review_payload.get("review_target_run_id"),
                    "review_source_entries": review_payload.get("review_source_entries"),
                    "review_source_result": review_payload.get("review_source_result"),
                    "symbol": review_payload.get("symbol"),
                }
            )
            sector_outputs = dict((final_results.get("review_sector_snapshot_collector").outputs or {}) if final_results.get("review_sector_snapshot_collector") else {})
            sector_snapshot = dict(sector_outputs.get("snapshot") or {})
            if sector_snapshot:
                source_payload["sector"] = sector_snapshot.get("sector") or review_payload.get("sector")
                source_payload["theme"] = sector_snapshot.get("theme")
                snapshots.append(sector_snapshot)
                snapshots.extend(list(sector_outputs.get("related_snapshots") or []))
                if sector_outputs.get("related_snapshots"):
                    source_payload["include_policy_snapshot"] = True
            elif review_payload.get("sector"):
                source_payload["sector"] = review_payload.get("sector")
            stock_outputs = dict((final_results.get("review_stock_snapshot_collector").outputs or {}) if final_results.get("review_stock_snapshot_collector") else {})
            if stock_outputs:
                source_payload["symbol"] = review_payload.get("symbol")
                snapshots.extend(list(stock_outputs.get("snapshots") or []))
        return {
            "sources": [source_payload],
            "snapshots": snapshots,
            "subagent_trace": trace_rows,
            "subagent_plans": [final_plan.model_dump(mode="json")],
            "subagent_plan_patches": patch_rows,
        }

    def _build_collect_phase_invocations(self, *, plan: SubagentPlan) -> dict[str, dict[str, Any]]:
        runtime_nodes: dict[str, dict[str, Any]] = {}
        for node in plan.nodes:
            invocation = self._build_collect_phase_invocation(node=node)
            invocation_context = dict(invocation.context or {})
            runtime_nodes[node.node_id] = {
                "builder": lambda results, *, current_invocation=invocation, current_node_id=node.node_id: self._collect_plan_invocation_for_results(
                    node_id=current_node_id,
                    invocation=current_invocation,
                    results=results,
                ),
                "metadata": self.phase_controller.build_metadata(
                    task_name=str(invocation_context.get("task_name") or node.node_id),
                    agent_path=str(invocation_context.get("agent_path") or node.subagent_type),
                    objective=node.objective,
                    phase=str(invocation_context.get("phase") or "collect"),
                    extras={
                        "child_thread_id": str(invocation_context.get("child_thread_id") or "").strip() or None,
                        "child_turn_id": str(invocation_context.get("child_turn_id") or "").strip() or None,
                    },
                ),
            }
        return runtime_nodes

    def _build_collect_phase_invocation(self, *, node: SubagentPlanNode) -> SubagentInvocation:
        task_name, agent_path = self._collect_node_path(node_id=node.node_id, subagent_type=node.subagent_type)
        if node.node_id.startswith("compare_stock_snapshot_collector:"):
            symbol = str(node.inputs.get("symbol") or "").strip()
            child_thread, child_turn = self.spawn_child_thread(
                intent="collector_compare_target",
                objective=f"collect:compare:{symbol}",
                agent=self.agent_name,
                phase="collect_compare_target",
            )
            return self._build_stock_snapshot_invocation(
                objective=str(node.objective or "Collect stock-level verified snapshots for comparison targets."),
                query=str(node.inputs.get("query") or symbol),
                symbol=symbol or None,
                snapshot_types=[str(item).strip() for item in list(node.inputs.get("snapshot_types") or []) if str(item).strip()],
                allow_partial=bool(node.inputs.get("allow_partial")),
                task_name=task_name,
                agent_path=agent_path,
                child_thread=child_thread,
                child_turn=child_turn,
                phase="collect_compare_target",
            )
        return self.phase_controller.build_invocation(
            subagent_type=node.subagent_type,
            objective=node.objective,
            inputs=dict(node.inputs or {}),
            task_name=task_name,
            agent_path=agent_path,
            phase="collect",
        )

    def _collect_plan_invocation_for_results(
        self,
        *,
        node_id: str,
        invocation: SubagentInvocation,
        results: dict[str, Any],
    ) -> SubagentInvocation:
        if node_id != "collection_strategy_resolver":
            return invocation
        query_outputs = dict((results.get("query_normalizer").outputs or {}) if results.get("query_normalizer") else {})
        inputs = dict(invocation.inputs or {})
        resolved_cycle = str(query_outputs.get("investment_cycle") or "").strip() or None
        if resolved_cycle and not str(inputs.get("investment_cycle") or "").strip():
            inputs["investment_cycle"] = resolved_cycle
        return invocation.model_copy(update={"inputs": inputs})

    @staticmethod
    def _collect_node_path(*, node_id: str, subagent_type: str) -> tuple[str, str]:
        mapping = {
            "query_normalizer": ("collect:query_normalizer", "collector/query_normalizer"),
            "collection_strategy_resolver": (
                "collect:strategy:collection_strategy_resolver",
                "collector/strategy/collection_strategy_resolver",
            ),
            "stock_snapshot_collector": ("collect:stock_snapshot_collector", "collector/stock_snapshot_collector"),
            "sector_snapshot_collector": ("collect:sector_snapshot_collector", "collector/sector_snapshot_collector"),
            "candidate_universe_builder": ("collect:candidate_universe_builder", "collector/candidate_universe_builder"),
            "review_target_loader": ("collect:review_target_loader", "collector/review_target_loader"),
            "review_sector_snapshot_collector": (
                "collect:review:sector_snapshot_collector",
                "collector/review/sector_snapshot_collector",
            ),
            "review_stock_snapshot_collector": (
                "collect:review:stock_snapshot_collector",
                "collector/review/stock_snapshot_collector",
            ),
        }
        if node_id.startswith("compare_stock_snapshot_collector:"):
            symbol = node_id.rsplit(":", 1)[-1]
            return (
                f"collect:compare:{symbol}:stock_snapshot_collector",
                f"collector/compare/{symbol}/stock_snapshot_collector",
            )
        return mapping.get(node_id, (f"collect:{node_id}", f"collector/{subagent_type}"))

    def _build_collect_plan_node_with_inputs(
        self,
        *,
        intent: str,
        capability: str,
        node_id: str,
        objective: str,
        inputs: dict[str, Any],
        cache_key: str,
        depends_on: list[str] | None = None,
        max_attempts: int = 1,
        notes: list[str] | None = None,
    ) -> SubagentPlanNode:
        selected = self._select_collect_catalog_entry(intent=intent, capability=capability)
        return self.phase_controller.build_catalog_plan_node(
            node_id=node_id,
            subagent_type=selected.subagent_type,
            objective=objective,
            inputs=dict(inputs),
            tags=["catalog_selected", capability],
            depends_on=depends_on,
            max_attempts=max_attempts,
            replayable=True,
            cache_scope="run",
            cache_key=cache_key,
            notes=notes,
        )

    @staticmethod
    def _stock_check_plan_snapshot_types(plan: SubagentPlan) -> list[str]:
        for node in plan.nodes:
            if node.node_id == "stock_snapshot_collector":
                return [str(item).strip() for item in list(node.inputs.get("snapshot_types") or []) if str(item).strip()]
        return []

    @staticmethod
    def _sector_research_plan_includes_policy(plan: SubagentPlan) -> bool:
        for node in plan.nodes:
            if node.node_id == "sector_snapshot_collector":
                return bool(node.inputs.get("include_policy_snapshot"))
        return False

    @staticmethod
    def _stock_screen_plan_sector_query(plan: SubagentPlan) -> str | None:
        for node in plan.nodes:
            if node.node_id == "candidate_universe_builder":
                value = str(node.inputs.get("sector_query") or "").strip()
                return value or None
        return None

    @staticmethod
    def _stock_compare_plan_snapshot_types(plan: SubagentPlan) -> list[str]:
        for node in plan.nodes:
            if node.node_id.startswith("compare_stock_snapshot_collector:"):
                return [str(item).strip() for item in list(node.inputs.get("snapshot_types") or []) if str(item).strip()]
        return []

    @staticmethod
    def _stock_compare_plan_symbols(plan: SubagentPlan) -> list[str]:
        symbols: list[str] = []
        for node in plan.nodes:
            if not node.node_id.startswith("compare_stock_snapshot_collector:"):
                continue
            symbol = str(node.inputs.get("symbol") or "").strip()
            if symbol and symbol not in symbols:
                symbols.append(symbol)
        return symbols

    @staticmethod
    def _review_plan_includes_policy(plan: SubagentPlan) -> bool:
        for node in plan.nodes:
            if node.node_id == "review_sector_snapshot_collector":
                return bool(node.inputs.get("include_policy_snapshot"))
        return False

    @staticmethod
    def _review_stock_snapshot_types_from_plan(plan: SubagentPlan) -> list[str]:
        for node in plan.nodes:
            if node.node_id == "review_stock_snapshot_collector":
                return [str(item).strip() for item in list(node.inputs.get("snapshot_types") or []) if str(item).strip()]
        return []

    @staticmethod
    def _review_followup_mode(*, sector: str | None, symbol: str | None) -> str:
        if sector and symbol:
            return "combined_review_followup"
        if sector:
            return "sector_review_followup"
        if symbol:
            return "stock_review_followup"
        return "loader_only_review_context"

    @classmethod
    def _review_followup_mode_from_plan(cls, plan: SubagentPlan) -> str:
        has_sector = any(node.node_id == "review_sector_snapshot_collector" for node in plan.nodes)
        has_stock = any(node.node_id == "review_stock_snapshot_collector" for node in plan.nodes)
        return cls._review_followup_mode(
            sector="1" if has_sector else None,
            symbol="1" if has_stock else None,
        )

    def _resolved_compare_symbols(
        self,
        *,
        query: str,
        constraints: dict[str, Any],
        normalized: dict[str, Any],
        metadata: dict[str, str] | None = None,
    ) -> list[str]:
        metadata = dict(metadata or {})
        resolved: list[str] = []
        raw_symbols: list[str] = []
        if str(metadata.get("symbols") or "").strip():
            raw_symbols.extend(str(metadata.get("symbols") or "").split(","))
        raw_symbols.extend(list(constraints.get("symbols") or normalized.get("symbols") or []))
        symbol = str(metadata.get("symbol") or constraints.get("symbol") or normalized.get("symbol") or "").strip()
        if symbol:
            raw_symbols.append(symbol)
        for item in raw_symbols:
            candidate = str(item or "").strip()
            if candidate and candidate not in resolved:
                resolved.append(candidate)
        if resolved:
            return resolved
        extracted = self._extract_symbols(query)
        return [item for item in extracted if item]

    def _collect_strategy_final_max_workers(
        self,
        *,
        intent: str,
        query: str,
        constraints: dict[str, Any],
    ) -> int:
        if intent == "stock_compare":
            estimated = len(
                self._resolved_compare_symbols(
                    query=query,
                    constraints=constraints,
                    normalized={},
                )
            )
            return max(estimated, 1)
        if intent == "review_thesis":
            return 2
        return 1

    def _resolve_collect_variant_id(self, *, intent: str, plan: SubagentPlan) -> str:
        if intent == "stock_check":
            return "augmented_snapshot_bundle" if len(self._stock_check_plan_snapshot_types(plan)) > 3 else "baseline_snapshot_bundle"
        if intent == "stock_compare":
            return (
                "augmented_compare_snapshot_bundle"
                if len(self._stock_compare_plan_snapshot_types(plan)) > 3
                else "baseline_compare_snapshot_bundle"
            )
        if intent == "sector_research":
            return "sector_plus_policy" if self._sector_research_plan_includes_policy(plan) else "sector_only"
        if intent == "stock_screen":
            return "sector_seeded_universe" if self._stock_screen_plan_sector_query(plan) else "market_slice_universe"
        if intent == "review_thesis":
            return self._review_followup_mode_from_plan(plan)
        return "rule_replanned"

    def _select_collect_catalog_entry(
        self,
        *,
        intent: str,
        capability: str,
        preferred_subagent_type: str | None = None,
    ) -> SubagentCatalogEntry:
        return self.phase_controller.select_catalog_entry(
            stage="collect",
            intent=intent,
            capability=capability,
            preferred_subagent_type=preferred_subagent_type,
        )

    @staticmethod
    def _cost_rank(value: str | None) -> int:
        return SubagentPhaseController._cost_rank(value)

    @staticmethod
    def _latency_rank(value: str | None) -> int:
        return SubagentPhaseController._latency_rank(value)

    def _build_catalog_plan_node(
        self,
        *,
        intent: str,
        capability: str,
        node_id: str,
        objective: str,
        cache_key: str,
        depends_on: list[str] | None = None,
        failure_mode: str = "raise",
        allow_partial_dependencies: bool = False,
        max_attempts: int = 1,
        notes: list[str] | None = None,
    ) -> SubagentPlanNode:
        selected = self._select_collect_catalog_entry(intent=intent, capability=capability)
        return self.phase_controller.build_catalog_plan_node(
            node_id=node_id,
            subagent_type=selected.subagent_type,
            objective=objective,
            inputs={},
            tags=["catalog_selected", capability],
            depends_on=depends_on,
            failure_mode=failure_mode,  # type: ignore[arg-type]
            allow_partial_dependencies=allow_partial_dependencies,
            max_attempts=max_attempts,
            replayable=True,
            cache_scope="run",
            cache_key=cache_key,
            notes=notes,
        )

    def _build_single_node_collect_plan(
        self,
        *,
        intent: str,
        node_id: str,
        capability: str,
        objective: str,
        inputs: dict[str, Any],
        task_name: str,
        agent_path: str,
        max_attempts: int = 1,
    ) -> tuple[SubagentPlan, dict[str, SubagentInvocation]]:
        selected = self._select_collect_catalog_entry(intent=intent, capability=capability)
        candidates = self.registry.catalog_entries(stage="collect", intent=intent, capability=capability)
        node = self.phase_controller.build_catalog_plan_node(
            node_id=node_id,
            subagent_type=selected.subagent_type,
            objective=objective,
            inputs=dict(inputs),
            tags=["catalog_selected", capability],
            max_attempts=max_attempts,
            replayable=True,
            cache_scope="run",
            cache_key=node_id,
        )
        plan = SubagentPlan(
            intent=intent,
            phase="collect",
            strategy="catalog",
            nodes=[node],
            notes=[f"capability={capability}"],
            catalog_trace={
                "stage": "collect",
                "selection_mode": "catalog",
                "capability": capability,
                "candidate_subagent_types": [item.subagent_type for item in candidates] or [selected.subagent_type],
                "selected_subagent_type": selected.subagent_type,
            },
        )
        invocations = {
            node_id: self.phase_controller.build_invocation(
                subagent_type=selected.subagent_type,
                objective=objective,
                inputs=dict(inputs),
                task_name=task_name,
                agent_path=agent_path,
                phase="collect",
            )
        }
        return plan, invocations

    def _build_stock_snapshot_job_plan(
        self,
        *,
        intent: str,
        jobs: list[tuple[str, object | None, SubagentInvocation]],
        task_name_prefix: str,
        agent_path_prefix: str,
    ) -> tuple[SubagentPlan, dict[str, SubagentInvocation]]:
        selected = self._select_collect_catalog_entry(intent=intent, capability="stock_snapshot_bundle")
        candidate_entries = self.registry.catalog_entries(
            stage="collect",
            intent=intent,
            capability="stock_snapshot_bundle",
        )
        nodes: list[SubagentPlanNode] = []
        invocations: dict[str, SubagentInvocation] = {}
        for index, (symbol, child_thread, invocation) in enumerate(jobs):
            node_id = f"stock_snapshot_collector:{index}:{symbol}"
            nodes.append(
                self.phase_controller.build_catalog_plan_node(
                    node_id=node_id,
                    subagent_type=selected.subagent_type,
                    objective=str(invocation.objective or "Collect stock-level verified snapshots."),
                    inputs=dict(invocation.inputs or {}),
                    tags=["catalog_selected", "stock_snapshot_bundle", "parallel_fanout"],
                    max_attempts=2,
                    replayable=True,
                    cache_scope="run",
                    cache_key=f"stock_snapshot_collector:{symbol}",
                    notes=[f"fanout_symbol={symbol}"],
                )
            )
            invocation_context = dict(invocation.context or {})
            invocations[node_id] = invocation.model_copy(
                update={
                    "subagent_type": selected.subagent_type,
                    "context": {
                        "task_name": str(invocation_context.get("task_name") or f"{task_name_prefix}:{symbol}:stock_snapshot_collector"),
                        "agent_path": str(invocation_context.get("agent_path") or f"{agent_path_prefix}/{symbol}/stock_snapshot_collector"),
                        "child_thread_id": getattr(child_thread, "thread_id", None),
                        "child_turn_id": str(invocation_context.get("child_turn_id") or "").strip() or None,
                        "phase": str(invocation_context.get("phase") or "collect"),
                    },
                }
            )
        plan = SubagentPlan(
            intent=intent,
            phase="collect",
            strategy="catalog",
            nodes=nodes,
            notes=["parallel_stock_snapshot_fanout"],
            catalog_trace={
                "stage": "collect",
                "selection_mode": "catalog",
                "capability": "stock_snapshot_bundle",
                "candidate_subagent_types": [item.subagent_type for item in candidate_entries] or [selected.subagent_type],
                "selected_subagent_type": selected.subagent_type,
                "fanout_count": len(nodes),
            },
        )
        return plan, invocations

    def _execute_subagent_plan(
        self,
        *,
        plan: SubagentPlan,
        invocations: dict[str, SubagentInvocation],
        max_workers: int | None = None,
        dag_id: str | None = None,
    ) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
        runtime_nodes = self._build_runtime_plan_invocations(
            plan=plan,
            invocations=invocations,
        )
        results = self.phase_controller.execute_plan(
            plan=plan,
            build_invocation=runtime_nodes,
            max_workers=max_workers,
            dag_id=dag_id or f"subagent_plan:{plan.plan_id}",
        )
        outputs_by_node: dict[str, dict[str, Any]] = {}
        trace_rows: list[dict[str, Any]] = []
        for plan_node in plan.nodes:
            invocation = invocations[plan_node.node_id]
            result = results[plan_node.node_id]
            if result.record is None or result.outputs is None:
                raise RuntimeError(f"collector plan node missing outputs: {plan_node.node_id}")
            outputs_by_node[plan_node.node_id] = dict(result.outputs)
            trace_row = self._trace_record_from_dag_result(
                result=result,
                fallback_subagent_type=invocation.subagent_type,
                fallback_objective=str(invocation.objective or plan_node.objective or ""),
                fallback_task_name=str((invocation.context or {}).get("task_name") or plan_node.node_id),
                fallback_agent_path=str((invocation.context or {}).get("agent_path") or invocation.subagent_type),
                phase=str((invocation.context or {}).get("phase") or plan.phase or "collect"),
            )
            trace_row["plan_id"] = plan.plan_id
            trace_row["plan_node_id"] = plan_node.node_id
            trace_row["subagent_plan_strategy"] = plan.strategy
            child_thread_id = str(trace_row.get("child_thread_id") or "").strip()
            if child_thread_id:
                notes = list(trace_row.get("notes") or [])
                child_thread_note = f"child_thread={child_thread_id}"
                if child_thread_note not in notes:
                    notes.append(child_thread_note)
                trace_row["notes"] = notes
            trace_rows.append(trace_row)
        return outputs_by_node, trace_rows

    def _build_runtime_plan_invocations(
        self,
        *,
        plan: SubagentPlan,
        invocations: dict[str, SubagentInvocation],
    ) -> dict[str, dict[str, Any]]:
        runtime_nodes: dict[str, dict[str, Any]] = {}
        for plan_node in plan.nodes:
            invocation = invocations.get(plan_node.node_id)
            if invocation is None:
                raise KeyError(f"missing invocation for subagent plan node: {plan_node.node_id}")
            invocation_context = dict(invocation.context or {})
            metadata = self.phase_controller.build_metadata(
                task_name=str(invocation_context.get("task_name") or plan_node.node_id),
                agent_path=str(invocation_context.get("agent_path") or plan_node.subagent_type),
                objective=str(invocation.objective or plan_node.objective or ""),
                phase=str(invocation_context.get("phase") or plan.phase or "collect"),
                extras={
                    "child_thread_id": str(invocation_context.get("child_thread_id") or "").strip() or None,
                    "child_turn_id": str(invocation_context.get("child_turn_id") or "").strip() or None,
                },
            )
            runtime_nodes[plan_node.node_id] = {
                "builder": lambda _results, *, current_invocation=invocation: current_invocation,
                "metadata": metadata,
            }
        return runtime_nodes

    def _execute_single_subagent_node(
        self,
        *,
        node_id: str,
        invocation: SubagentInvocation,
        max_attempts: int = 1,
    ) -> tuple[dict, dict]:
        single_plan = SubagentPlan(
            intent="collect",
            phase=str((invocation.context or {}).get("phase") or "collect"),
            strategy="rule",
            nodes=[
                self.phase_controller.build_catalog_plan_node(
                    node_id=node_id,
                    subagent_type=invocation.subagent_type,
                    objective=str(invocation.objective or node_id),
                    inputs=dict(invocation.inputs or {}),
                    max_attempts=max_attempts,
                    cache_key=node_id,
                    tags=["single_node"],
                )
            ],
            notes=["single_node_execution"],
        )
        outputs_by_node, trace_rows = self._execute_subagent_plan(
            plan=single_plan,
            invocations={node_id: invocation},
            max_workers=1,
        )
        return dict(outputs_by_node[node_id]), dict(trace_rows[0])

    def enrich_stock_screen_candidates(
        self,
        *,
        candidates: list[dict],
        snapshot_types: list[str] | None = None,
    ) -> dict:
        trace = []
        snapshots: list[dict] = []
        requested_snapshot_types = snapshot_types or ["market", "fundamental", "notice", "lhb", "capital_flow", "sentiment", "events"]
        seen_symbols: set[str] = set()
        enrichment_jobs: list[tuple[str, object | None, SubagentInvocation]] = []
        for candidate in candidates:
            symbol = str(candidate.get("symbol") or "").strip()
            if not symbol or symbol in seen_symbols:
                continue
            child_thread, child_turn = self.spawn_child_thread(
                intent="collector_screen_candidate",
                objective=f"collect:screen_candidate:{symbol}",
                agent=self.agent_name,
                phase="collect_screen_candidate",
            )
            enrichment_jobs.append(
                (
                    symbol,
                    child_thread,
                    self._build_stock_snapshot_invocation(
                        objective="Collect deep stock snapshots for shortlisted screen candidates.",
                        query=f"{symbol} {str(candidate.get('name') or '').strip()}".strip(),
                        symbol=symbol,
                        snapshot_types=requested_snapshot_types,
                        allow_partial=True,
                        task_name=f"collect:screen_candidate:{symbol}:stock_snapshot_collector",
                        agent_path=f"collector/screen/{symbol}/stock_snapshot_collector",
                        child_thread=child_thread,
                        child_turn=child_turn,
                        phase="collect_screen_candidate",
                    ),
                )
            )
            seen_symbols.add(symbol)
        trace_rows, snapshot_rows, child_thread_ids = self._run_stock_snapshot_jobs(
            enrichment_jobs,
            intent="stock_screen",
            max_workers=len(enrichment_jobs),
            dag_id="screen_enrichment_stock_snapshot_jobs",
        )
        trace.extend(trace_rows)
        snapshots.extend(snapshot_rows)

        return {
            "snapshots": snapshots,
            "subagent_trace": trace,
            "child_thread_ids": sorted(set(child_thread_ids)),
            "notes": [
                f"enriched_candidate_count={len(seen_symbols)}",
                f"snapshot_types={','.join(requested_snapshot_types)}",
                *self._child_thread_notes(trace),
            ],
        }

    def _build_stock_snapshot_invocation(
        self,
        *,
        objective: str,
        query: str,
        symbol: str | None,
        snapshot_types: list[str],
        allow_partial: bool,
        task_name: str,
        agent_path: str,
        child_thread=None,
        child_turn=None,
        phase: str | None = None,
    ) -> SubagentInvocation:
        context = {
            "task_name": task_name,
            "agent_path": agent_path,
            "child_thread_id": getattr(child_thread, "thread_id", None),
            "child_turn_id": getattr(child_turn, "turn_id", None),
        }
        if phase:
            context["phase"] = phase
        return SubagentInvocation(
            subagent_type="stock_snapshot_collector",
            objective=objective,
            inputs={
                "query": query,
                "symbol": symbol,
                "snapshot_types": snapshot_types,
                "allow_partial": allow_partial,
            },
            context=context,
        )

    def _run_stock_snapshot_jobs(
        self,
        jobs: list[tuple[str, object | None, SubagentInvocation]],
        *,
        intent: str,
        max_workers: int | None = None,
        dag_id: str,
    ) -> tuple[list[dict], list[dict], list[str]]:
        if not jobs:
            return [], [], []
        plan, invocations = self._build_stock_snapshot_job_plan(
            intent=intent,
            jobs=jobs,
            task_name_prefix="collect",
            agent_path_prefix="collector",
        )
        outputs_by_node, trace_rows = self._execute_subagent_plan(
            plan=plan,
            invocations=invocations,
            max_workers=max_workers,
            dag_id=dag_id,
        )
        snapshots: list[dict] = []
        child_thread_ids: list[str] = []
        for node in plan.nodes:
            snapshots.extend(list((outputs_by_node.get(node.node_id) or {}).get("snapshots") or []))
        for trace_record in trace_rows:
            child_thread_id = str(trace_record.get("child_thread_id") or "").strip()
            if child_thread_id:
                child_thread_ids.append(child_thread_id)
        return trace_rows, snapshots, child_thread_ids

    def _trace_record_from_dag_result(
        self,
        *,
        result,
        fallback_subagent_type: str,
        fallback_objective: str,
        fallback_task_name: str,
        fallback_agent_path: str,
        phase: str,
    ) -> dict:
        record = result.record
        metadata = dict(result.metadata or {})
        notes = list((record.notes if record is not None else []) or [])
        if result.error_summary and f"error={result.error_summary}" not in notes:
            notes.append(f"error={result.error_summary}")
        payload = {
            "subagent_type": str((record.subagent_type if record is not None else fallback_subagent_type) or fallback_subagent_type),
            "objective": str((record.objective if record is not None else fallback_objective) or fallback_objective),
            "status": result.status,
            "task_name": str(metadata.get("task_name") or fallback_task_name),
            "agent_path": str(metadata.get("agent_path") or fallback_agent_path),
            "phase": str(metadata.get("phase") or phase),
            "agent": self.agent_name,
            "notes": notes,
        }
        if result.invocation_id:
            payload["invocation_id"] = result.invocation_id
        child_thread_id = str(metadata.get("child_thread_id") or "").strip()
        child_turn_id = str(metadata.get("child_turn_id") or "").strip()
        if child_thread_id:
            payload["child_thread_id"] = child_thread_id
        if child_turn_id:
            payload["child_turn_id"] = child_turn_id
        return payload

    def _normalize_query(self, *, query: str) -> dict:
        return {
            "symbol": primary_stock_symbol(query) or extract_symbol(query),
            "symbols": self._extract_symbols(query),
            "jsonl_path": extract_jsonl_path(query),
            "sector_query": extract_sector_query(query),
            "investment_cycle": extract_investment_cycle(query),
        }

    def _build_distill_request_payload(self, *, query: str, jsonl_path: str | None) -> dict:
        return {
            "source_type": "distill_request",
            "path": jsonl_path,
            "content": query,
            "title": "distill_kol_logic input request" if jsonl_path else "distill request",
        }

    def _build_trace_record(
        self,
        *,
        subagent_type: str,
        objective: str,
        inputs: dict,
        outputs: dict,
    ) -> dict:
        notes = [str(item) for item in outputs.get("notes") or []] if isinstance(outputs.get("notes"), list) else []
        record = SubagentExecutionRecord(
            subagent_type=subagent_type,
            objective=objective,
            inputs=inputs,
            output_keys=sorted(outputs.keys()),
            notes=notes,
        )
        return record.model_dump(mode="json")

    def _extract_symbols(self, query: str) -> list[str]:
        symbols = extract_stock_symbols(query)
        if symbols:
            return symbols
        symbol = extract_symbol(query)
        return [symbol] if symbol else []

    def _build_compare_targets(self, *, query: str, symbols: list[str]) -> list[dict]:
        targets: list[dict] = []
        for symbol in symbols:
            name = self._name_for_symbol(symbol, query=query)
            targets.append({"symbol": symbol, "name": name})
        return targets

    def _name_for_symbol(self, symbol: str, *, query: str) -> str:
        return stock_name_for_symbol(symbol, query=query)

    def _normalize_planning_context_hints(self, raw_hints) -> dict[str, str]:
        return normalize_planning_context_hints(raw_hints)

    def _merge_snapshot_types(self, base_snapshot_types, extra_snapshot_types) -> list[str]:
        merged: list[str] = []
        for item in list(base_snapshot_types or []) + list(extra_snapshot_types or []):
            snapshot_type = str(item or "").strip()
            if snapshot_type and snapshot_type not in merged:
                merged.append(snapshot_type)
        return merged

    def _child_thread_notes(self, trace_rows: list[dict]) -> list[str]:
        child_thread_ids = sorted(
            {
                str(note).split("=", 1)[1].strip()
                for row in trace_rows
                for note in list(row.get("notes") or [])
                if isinstance(note, str) and note.startswith("child_thread=") and note.split("=", 1)[1].strip()
            }
        )
        if not child_thread_ids:
            return []
        return [f"child_threads={','.join(child_thread_ids)}"]

    def _annotate_collect_trace_rows(self, trace_rows: list[dict]) -> list[dict]:
        annotated: list[dict] = []
        for row in trace_rows:
            updated = dict(row)
            child_thread_id = str(updated.get("child_thread_id") or "").strip()
            if child_thread_id:
                notes = [str(item) for item in list(updated.get("notes") or [])]
                child_thread_note = f"child_thread={child_thread_id}"
                if child_thread_note not in notes:
                    notes.append(child_thread_note)
                updated["notes"] = notes
            annotated.append(updated)
        return annotated

    @staticmethod
    def _unique_notes(notes: list[str]) -> list[str]:
        unique: list[str] = []
        for note in notes:
            normalized = str(note or "").strip()
            if not normalized or normalized in unique:
                continue
            unique.append(normalized)
        return unique

    def _derive_collection_strategy(
        self,
        *,
        intent: str,
        query: str,
        planning_context_hints: dict[str, str],
        investment_cycle,
    ) -> dict:
        context = build_planning_context_view(
            query=query,
            planning_context_hints=planning_context_hints,
        )
        text = context.combined_text
        hint_augmented_snapshot_types: list[str] = []
        notes: list[str] = []
        preferred_methodology = None
        include_policy_snapshot = False
        resolved_investment_cycle = investment_cycle
        cycle_assumption = "explicit" if investment_cycle else None
        focus_text = combine_focus_text(
            context.query_text,
            context.recommended_next_step,
            context.open_question,
            context.previous_summary,
            context.thread_topic,
        )
        preferred_methodology, focus_source = derive_methodology_focus(
            cashflow_text=combine_focus_text(context.query_text, context.recommended_next_step),
            comparison_text=focus_text,
            planning_context_hints=planning_context_hints,
        )

        if preferred_methodology == "cashflow_resilience":
            hint_augmented_snapshot_types.append("capital_flow")
            notes.append("focus=cashflow_resilience")
        elif preferred_methodology == "comps":
            notes.append("focus=comps")
        elif preferred_methodology == "dcf":
            notes.append("focus=dcf")
        if preferred_methodology and focus_source == "methodology_memory":
            notes.append(f"focus={preferred_methodology}")
            notes.append("focus_source=methodology_memory")

        if has_momentum_focus(text):
            hint_augmented_snapshot_types.append("sentiment")
            if not resolved_investment_cycle:
                resolved_investment_cycle = "short_term"
                cycle_assumption = "planning_context"
            notes.append("focus=sentiment")
        elif has_long_term_bias(text) and not resolved_investment_cycle:
            resolved_investment_cycle = "long_term"
            cycle_assumption = "planning_context"
        elif has_mid_term_bias(text) and not resolved_investment_cycle:
            resolved_investment_cycle = "mid_term"
            cycle_assumption = "planning_context"

        if has_policy_focus(text):
            include_policy_snapshot = True
            if intent in {"stock_check", "stock_compare", "review_thesis"}:
                hint_augmented_snapshot_types.append("events")
            notes.append("focus=policy_signals")

        if has_notice_focus(text):
            notes.append("focus=notice_review")

        return {
            "preferred_methodology": preferred_methodology,
            "hint_augmented_snapshot_types": self._merge_snapshot_types([], hint_augmented_snapshot_types),
            "include_policy_snapshot": include_policy_snapshot,
            "resolved_investment_cycle": resolved_investment_cycle,
            "cycle_assumption": cycle_assumption,
            "notes": notes,
        }
