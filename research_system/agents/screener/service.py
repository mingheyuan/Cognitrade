from __future__ import annotations

from pathlib import Path
from typing import Any

from research_system.agents._subagent_dag import SubagentDagNodeResult
from research_system.agents._subagent_phase_controller import SubagentPhaseController
from research_system.agents._subagent_phase_policy import ScreenerPhasePolicy
from research_system.agents._subagent_invoker import RuntimeBoundSubagentInvoker
from research_system.runtime.platform.settings import Settings
from research_system.runtime.providers.provider_registry import OpenAIProvider
from research_system.schemas.subagents import (
    SubagentCatalogEntry,
    SubagentGraphPatch,
    SubagentInvocation,
    SubagentPlan,
    SubagentPlanNode,
)
from research_system.services.methodologies.valuation.comps_service import CompsService
from research_system.services.methodologies.planning.planning_context import (
    build_planning_context_view,
    derive_methodology_focus,
    has_cashflow_focus,
    has_long_term_bias,
    has_momentum_focus,
    normalize_planning_context_hints,
)
from research_system.subagents.registry import SubagentRegistry
from research_system.subagents.screening import (
    ScreeningExecutionSubagent,
    ScreeningStrategyResolverSubagent,
)


class ScreenerService(RuntimeBoundSubagentInvoker):
    agent_name = "screener"

    def __init__(
        self,
        comps_service: CompsService | None = None,
        runs_root: Path | None = None,
        llm_provider: OpenAIProvider | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.comps_service = comps_service or CompsService()
        self.runs_root = runs_root
        self.llm_provider = llm_provider
        self.settings = settings
        self.registry = SubagentRegistry(
            [
                ScreeningStrategyResolverSubagent(
                    derive_screening_strategy=self._derive_screening_strategy,
                ),
                ScreeningExecutionSubagent(
                    execute_screening_pipeline=self._execute_screening_pipeline,
                ),
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
        self.phase_policy = ScreenerPhasePolicy()

    def run(
        self,
        *,
        query: str,
        source_payloads: list[dict],
        snapshot_payloads: list[dict],
        constraints: dict | None = None,
    ) -> dict:
        with self.local_run_scope(
            intent="stock_screen",
            query=query,
            phase="screen",
            title="screener",
            objective="Rank candidates, score watchlists, and decide enrichment.",
        ):
            return self._run_impl(
                query=query,
                source_payloads=source_payloads,
                snapshot_payloads=snapshot_payloads,
                constraints=constraints,
            )

    def _run_impl(
        self,
        *,
        query: str,
        source_payloads: list[dict],
        snapshot_payloads: list[dict],
        constraints: dict | None = None,
    ) -> dict:
        constraints = constraints or {}
        source_payload = source_payloads[0] if source_payloads else {}
        planning_context_hints = self._normalize_planning_context_hints(
            constraints.get("planning_context_hints")
            or source_payload.get("planning_context_hints")
        )
        rerun_start_node = self._subagent_dag_start_from_node_id(dag_id="screen")
        requires_direct_plan = bool(
            rerun_start_node and rerun_start_node != "screening_strategy_resolver"
        )
        if requires_direct_plan:
            strategy = self._derive_screening_strategy(
                query=query,
                source_payload=source_payload,
                planning_context_hints=planning_context_hints,
                explicit_cycle=constraints.get("investment_cycle"),
                explicit_methodology=constraints.get("prefer_methodology"),
            )
            final_plan = self._build_screening_subagent_plan(
                query=query,
                source_payload=source_payload,
                planning_context_hints=planning_context_hints,
                explicit_cycle=constraints.get("investment_cycle"),
                explicit_methodology=constraints.get("prefer_methodology"),
                methodology_enabled=self._screening_strategy_methodology_enabled(strategy),
            )
            patch_rows: list[dict[str, Any]] = []
            final_results = self._execute_subagent_plan(
                plan=final_plan,
                max_workers=1,
                dag_id="screen",
                build_invocation=self._build_screening_plan_invocations(
                    plan=final_plan,
                    query=query,
                    source_payload=source_payload,
                    snapshot_payloads=snapshot_payloads,
                    constraints=constraints,
                    planning_context_hints=planning_context_hints,
                ),
            )
        else:
            base_plan = self._build_screening_subagent_plan(
                query=query,
                source_payload=source_payload,
                planning_context_hints=planning_context_hints,
                explicit_cycle=constraints.get("investment_cycle"),
                explicit_methodology=constraints.get("prefer_methodology"),
                methodology_enabled=None,
            )
            final_plan, _base_results, patch_rows, final_results = self.phase_controller.execute_patch_cycle(
                plan=base_plan,
                dag_id="screen",
                base_max_workers=1,
                final_max_workers=1,
                build_invocation_factory=lambda current_plan: self._build_screening_plan_invocations(
                    plan=current_plan,
                    query=query,
                    source_payload=source_payload,
                    snapshot_payloads=snapshot_payloads,
                    constraints=constraints,
                    planning_context_hints=planning_context_hints,
                ),
                patch_builder=lambda base_results: self._build_screening_replan_patch(base_results=base_results),
                replan_builder=lambda current_plan, patch, base_results: self._rebuild_screening_subagent_plan(
                    plan=current_plan,
                    query=query,
                    source_payload=source_payload,
                    planning_context_hints=planning_context_hints,
                    constraints=constraints,
                    patch=patch,
                    base_results=base_results,
                ),
                candidate_plan_variants_builder=lambda rule_final_plan, patch, base_results: self._build_screening_candidate_plan_variants(
                    rule_final_plan=rule_final_plan,
                    query=query,
                    source_payload=source_payload,
                    planning_context_hints=planning_context_hints,
                    constraints=constraints,
                    patch=patch,
                    base_results=base_results,
                ),
            )
        execution_outputs = dict((final_results.get("screening_executor").outputs or {}) if final_results.get("screening_executor") else {})
        return {
            **execution_outputs,
            "subagent_trace": self.phase_controller.trace_from_results(results=final_results),
            "subagent_plans": [final_plan.model_dump(mode="json")],
            "subagent_plan_patches": patch_rows,
        }

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

    def _select_screen_catalog_entry(
        self,
        *,
        capability: str,
        preferred_subagent_type: str | None = None,
    ) -> SubagentCatalogEntry:
        return self.phase_controller.select_catalog_entry(
            stage="screen",
            intent="stock_screen",
            capability=capability,
            preferred_subagent_type=preferred_subagent_type,
        )

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

    def _build_screening_subagent_plan(
        self,
        *,
        query: str,
        source_payload: dict,
        planning_context_hints: dict[str, str],
        explicit_cycle,
        explicit_methodology,
        methodology_enabled: bool | None,
    ) -> SubagentPlan:
        strategy_entry = self._select_screen_catalog_entry(
            capability="screening_strategy",
            preferred_subagent_type="screening_strategy_resolver",
        )
        nodes = [
            self._build_catalog_plan_node(
                node_id="screening_strategy_resolver",
                subagent_type=strategy_entry.subagent_type,
                objective="Resolve screening cycle and methodology strategy.",
                inputs={
                    "query": query,
                    "planning_context_hints": dict(planning_context_hints or {}),
                    "explicit_cycle": explicit_cycle,
                    "explicit_methodology": explicit_methodology,
                    "candidate_count": len(list(source_payload.get("candidate_universe") or [])),
                },
                cache_key="screening_strategy_resolver",
                notes=["screen_phase=strategy"],
            )
        ]
        selected_subagent_types = {
            "screening_strategy_resolver": strategy_entry.subagent_type,
        }
        if methodology_enabled is not None:
            execution_entry = self._select_screen_catalog_entry(
                capability="screening_execution",
                preferred_subagent_type="screening_execution",
            )
            nodes.append(
                self._build_catalog_plan_node(
                    node_id="screening_executor",
                    subagent_type=execution_entry.subagent_type,
                    objective="Score candidates, attach methodology outputs, and decide enrichment targets.",
                    inputs={"methodology_enabled": bool(methodology_enabled)},
                    depends_on=["screening_strategy_resolver"],
                    cache_key="screening_executor",
                    notes=[
                        "screen_phase=execution",
                        f"methodology_enabled={str(bool(methodology_enabled)).lower()}",
                    ],
                )
            )
            selected_subagent_types["screening_executor"] = execution_entry.subagent_type
        return SubagentPlan(
            intent="stock_screen",
            phase="screen",
            strategy="catalog",
            nodes=nodes,
            notes=["screening_catalog_plan"] if methodology_enabled is not None else ["screening_strategy_base_plan"],
            catalog_trace={
                "stage": "screen",
                "selection_mode": "catalog",
                "selected_subagent_types": selected_subagent_types,
            },
        )

    def _build_screening_replan_patch(
        self,
        *,
        base_results: dict[str, SubagentDagNodeResult],
    ) -> SubagentGraphPatch:
        strategy = dict(
            (base_results["screening_strategy_resolver"].outputs or {}).get("screening_strategy") or {}
        )
        preferred_methodology = str(strategy.get("preferred_methodology") or "comps").strip() or "comps"
        resolved_investment_cycle = str(strategy.get("resolved_investment_cycle") or "mid_term").strip() or "mid_term"
        methodology_enabled = self._screening_strategy_methodology_enabled(strategy)
        return self.phase_policy.build_screening_replan_patch(
            preferred_methodology=preferred_methodology,
            resolved_investment_cycle=resolved_investment_cycle,
            methodology_enabled=methodology_enabled,
        )

    def _rebuild_screening_subagent_plan(
        self,
        *,
        plan: SubagentPlan,
        query: str,
        source_payload: dict,
        planning_context_hints: dict[str, str],
        constraints: dict[str, Any],
        patch: SubagentGraphPatch,
        base_results: dict[str, SubagentDagNodeResult],
        methodology_enabled_override: bool | None = None,
    ) -> SubagentPlan:
        strategy = dict(
            (base_results["screening_strategy_resolver"].outputs or {}).get("screening_strategy") or {}
        )
        methodology_enabled = (
            bool(methodology_enabled_override)
            if methodology_enabled_override is not None
            else self._screening_patch_methodology_enabled(patch=patch, strategy=strategy)
        )
        replanned = self._build_screening_subagent_plan(
            query=query,
            source_payload=source_payload,
            planning_context_hints=planning_context_hints,
            explicit_cycle=constraints.get("investment_cycle"),
            explicit_methodology=constraints.get("prefer_methodology"),
            methodology_enabled=methodology_enabled,
        )
        replanned.notes = self._unique_notes(
            [
                *list(replanned.notes or []),
                "screening_graph_replanned_from_strategy",
                f"preferred_methodology={str(strategy.get('preferred_methodology') or 'comps')}",
                f"resolved_investment_cycle={str(strategy.get('resolved_investment_cycle') or 'mid_term')}",
                f"methodology_enabled={str(bool(methodology_enabled)).lower()}",
            ]
        )
        replanned.catalog_trace = {
            **dict(replanned.catalog_trace or {}),
            "replanned_from_strategy": True,
            "methodology_enabled": bool(methodology_enabled),
            "resolved_investment_cycle": str(strategy.get("resolved_investment_cycle") or "mid_term"),
        }
        return replanned

    def _build_screening_candidate_plan_variants(
        self,
        *,
        rule_final_plan: SubagentPlan,
        query: str,
        source_payload: dict,
        planning_context_hints: dict[str, str],
        constraints: dict[str, Any],
        patch: SubagentGraphPatch,
        base_results: dict[str, SubagentDagNodeResult],
    ) -> dict[str, SubagentPlan]:
        strategy = dict(
            (base_results["screening_strategy_resolver"].outputs or {}).get("screening_strategy") or {}
        )
        rule_methodology_enabled = self._screening_plan_methodology_enabled(rule_final_plan)
        variants: dict[str, SubagentPlan] = {}
        if rule_methodology_enabled:
            comps_variant = rule_final_plan
            methodology_disabled_variant = self._rebuild_screening_subagent_plan(
                plan=self._build_screening_subagent_plan(
                    query=query,
                    source_payload=source_payload,
                    planning_context_hints=planning_context_hints,
                    explicit_cycle=constraints.get("investment_cycle"),
                    explicit_methodology=constraints.get("prefer_methodology"),
                    methodology_enabled=None,
                ),
                query=query,
                source_payload=source_payload,
                planning_context_hints=planning_context_hints,
                constraints=constraints,
                patch=patch,
                base_results=base_results,
                methodology_enabled_override=False,
            )
        else:
            methodology_disabled_variant = rule_final_plan
            comps_variant = self._rebuild_screening_subagent_plan(
                plan=self._build_screening_subagent_plan(
                    query=query,
                    source_payload=source_payload,
                    planning_context_hints=planning_context_hints,
                    explicit_cycle=constraints.get("investment_cycle"),
                    explicit_methodology=constraints.get("prefer_methodology"),
                    methodology_enabled=None,
                ),
                query=query,
                source_payload=source_payload,
                planning_context_hints=planning_context_hints,
                constraints=constraints,
                patch=patch,
                base_results=base_results,
                methodology_enabled_override=True,
            )
        comps_variant.notes = self._unique_notes(
            [
                *list(comps_variant.notes or []),
                "screening_candidate_variant",
                "screening_variant=comps_screening_variant",
                f"preferred_methodology={str(strategy.get('preferred_methodology') or 'comps')}",
            ]
        )
        methodology_disabled_variant.notes = self._unique_notes(
            [
                *list(methodology_disabled_variant.notes or []),
                "screening_candidate_variant",
                "screening_variant=methodology_disabled_variant",
                f"preferred_methodology={str(strategy.get('preferred_methodology') or 'comps')}",
            ]
        )
        variants["comps_screening_variant"] = comps_variant
        variants["methodology_disabled_variant"] = methodology_disabled_variant
        return variants

    def _build_screening_plan_invocations(
        self,
        *,
        plan: SubagentPlan,
        query: str,
        source_payload: dict,
        snapshot_payloads: list[dict],
        constraints: dict[str, Any],
        planning_context_hints: dict[str, str],
    ) -> dict[str, dict[str, Any]]:
        runtime_nodes: dict[str, dict[str, Any]] = {
            "screening_strategy_resolver": {
                "builder": lambda _results: self._build_subagent_invocation(
                    subagent_type="screening_strategy_resolver",
                    objective="Resolve screening cycle and methodology strategy.",
                    inputs={
                        "query": query,
                        "source_payload": source_payload,
                        "planning_context_hints": dict(planning_context_hints or {}),
                        "explicit_cycle": constraints.get("investment_cycle"),
                        "explicit_methodology": constraints.get("prefer_methodology"),
                    },
                    task_name="screen:strategy:screening_strategy_resolver",
                    agent_path="screener/strategy/screening_strategy_resolver",
                ),
                "metadata": self._dag_metadata(
                    task_name="screen:strategy:screening_strategy_resolver",
                    agent_path="screener/strategy/screening_strategy_resolver",
                    objective="Resolve screening cycle and methodology strategy.",
                ),
            }
        }
        plan_node_map = {node.node_id: node for node in plan.nodes}
        execution_node = plan_node_map.get("screening_executor")
        if execution_node is not None:
            runtime_nodes["screening_executor"] = {
                "builder": lambda results, *, methodology_enabled=bool(execution_node.inputs.get("methodology_enabled", True)): self._build_subagent_invocation(
                    subagent_type=execution_node.subagent_type,
                    objective="Score candidates, attach methodology outputs, and decide enrichment targets.",
                    inputs={
                        "query": query,
                        "source_payload": source_payload,
                        "snapshot_payloads": snapshot_payloads,
                        "constraints": dict(constraints or {}),
                        "screening_strategy": dict(
                            (results["screening_strategy_resolver"].outputs or {}).get("screening_strategy") or {}
                        ),
                        "methodology_enabled": methodology_enabled,
                    },
                    task_name="screen:execution:screening_execution",
                    agent_path="screener/execution/screening_execution",
                ),
                "metadata": self._dag_metadata(
                    task_name="screen:execution:screening_execution",
                    agent_path="screener/execution/screening_execution",
                    objective="Score candidates, attach methodology outputs, and decide enrichment targets.",
                ),
            }
        return {
            node_id: runtime_nodes[node_id]
            for node_id in [node.node_id for node in plan.nodes]
            if node_id in runtime_nodes
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
        return self.phase_controller.build_invocation(
            subagent_type=subagent_type,
            objective=objective,
            inputs=inputs,
            task_name=task_name,
            agent_path=agent_path,
            phase="screen",
        )

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
            phase="screen",
        )

    @staticmethod
    def _screening_strategy_methodology_enabled(strategy: dict[str, Any]) -> bool:
        preferred_methodology = str(strategy.get("preferred_methodology") or "comps").strip().lower()
        return preferred_methodology == "comps"

    def _screening_patch_methodology_enabled(
        self,
        *,
        patch: SubagentGraphPatch,
        strategy: dict[str, Any],
    ) -> bool:
        for operation in list(patch.operations or []):
            metadata = dict(operation.metadata or {})
            raw_flag = str(metadata.get("methodology_enabled") or "").strip().lower()
            if raw_flag in {"true", "false"}:
                return raw_flag == "true"
            selected_plan_variant = str(metadata.get("selected_plan_variant") or "").strip()
            if selected_plan_variant == "comps_screening_variant":
                return True
            if selected_plan_variant == "methodology_disabled_variant":
                return False
        return self._screening_strategy_methodology_enabled(strategy)

    @staticmethod
    def _screening_plan_methodology_enabled(plan: SubagentPlan) -> bool:
        for node in list(plan.nodes or []):
            if node.node_id == "screening_executor":
                return bool(node.inputs.get("methodology_enabled", True))
        return True

    @staticmethod
    def _unique_notes(notes: list[str]) -> list[str]:
        unique: list[str] = []
        for note in notes:
            normalized = str(note or "").strip()
            if not normalized or normalized in unique:
                continue
            unique.append(normalized)
        return unique

    def _execute_screening_pipeline(
        self,
        *,
        query: str,
        source_payload: dict,
        snapshot_payloads: list[dict],
        constraints: dict | None = None,
        screening_strategy: dict[str, Any] | None = None,
        methodology_enabled: bool = True,
    ) -> dict:
        constraints = constraints or {}
        universe = list(source_payload.get("candidate_universe") or [])
        sector_context = source_payload.get("sector_context")
        planning_context_hints = self._normalize_planning_context_hints(
            constraints.get("planning_context_hints")
            or source_payload.get("planning_context_hints")
        )
        strategy = dict(screening_strategy or {}) or self._derive_screening_strategy(
            query=query,
            source_payload=source_payload,
            planning_context_hints=planning_context_hints,
            explicit_cycle=constraints.get("investment_cycle"),
            explicit_methodology=constraints.get("prefer_methodology"),
        )
        cycle = str(strategy["resolved_investment_cycle"] or "mid_term")
        cycle_assumption = str(strategy["investment_cycle_assumption"] or "explicit")
        skip_enrichment = bool(constraints.get("skip_enrichment"))
        enrichment_top_n = int(constraints.get("enrichment_top_n") or 2)
        watchlist_top_k = int(constraints.get("watchlist_top_k") or 2)
        min_score_value = constraints.get("min_score")
        min_score = float(min_score_value) if min_score_value is not None else self._default_min_score(cycle)
        watchlist_min_score_value = constraints.get("watchlist_min_score")
        watchlist_min_score = (
            float(watchlist_min_score_value)
            if watchlist_min_score_value is not None
            else self._default_watchlist_min_score(cycle)
        )

        snapshot_by_symbol: dict[str, dict[str, dict]] = {}
        for snapshot in snapshot_payloads:
            symbol = str(snapshot.get("symbol") or "").strip()
            snapshot_type = str(snapshot.get("snapshot_type") or "").strip()
            if not symbol or not snapshot_type:
                continue
            snapshot_by_symbol.setdefault(symbol, {})[snapshot_type] = snapshot

        candidates: list[dict[str, Any]] = []
        for item in universe:
            symbol = str(item.get("symbol") or "").strip()
            if not symbol:
                continue
            snapshots = snapshot_by_symbol.get(symbol, {})
            market_snapshot = snapshots.get("market")
            fundamental_snapshot = snapshots.get("fundamental")
            notice_snapshot = snapshots.get("notice")
            lhb_snapshot = snapshots.get("lhb")
            capital_flow_snapshot = snapshots.get("capital_flow")
            sentiment_snapshot = snapshots.get("sentiment")
            events_snapshot = snapshots.get("events")

            price = self._latest_price(market_snapshot, fallback=item.get("latest_price"))
            pct_change = self._latest_pct_change(market_snapshot, fallback=item.get("latest_pct_change"))
            amount = self._latest_amount(market_snapshot, fallback=item.get("latest_amount"))
            notice_count = self._notice_count(notice_snapshot)
            has_financial_abstract = self._has_financial_abstract(fundamental_snapshot)
            capital_flow_score = self._capital_flow_score(capital_flow_snapshot)
            sentiment_score = self._sentiment_score(sentiment_snapshot)
            lhb_score = self._lhb_score(lhb_snapshot)
            event_score = self._event_score(events_snapshot)
            revenue_proxy = self._revenue_proxy(fundamental_snapshot, fallback=item.get("revenue_proxy"))
            missing_evidence = self._missing_evidence(
                market_snapshot=market_snapshot,
                fundamental_snapshot=fundamental_snapshot,
                notice_snapshot=notice_snapshot,
            )
            degraded_sources = self._degraded_sources(
                market_snapshot=market_snapshot,
                fundamental_snapshot=fundamental_snapshot,
                notice_snapshot=notice_snapshot,
                lhb_snapshot=lhb_snapshot,
                capital_flow_snapshot=capital_flow_snapshot,
                sentiment_snapshot=sentiment_snapshot,
                events_snapshot=events_snapshot,
            )
            deep_checked = not missing_evidence and bool(market_snapshot and fundamental_snapshot and notice_snapshot)
            score_breakdown = {
                "liquidity": self._liquidity_score(amount),
                "momentum": self._momentum_score(pct_change, cycle=cycle),
                "fundamental": 1.0 if has_financial_abstract else 0.0,
                "notice": self._notice_score(notice_count),
                "capital_flow": capital_flow_score,
                "sentiment": sentiment_score,
                "lhb": lhb_score,
                "events": event_score,
                "deep_check_bonus": 0.5 if deep_checked else 0.0,
                "evidence_penalty": -0.75 * len(missing_evidence),
            }
            score = round(sum(score_breakdown.values()), 3)
            rationale = self._candidate_rationale(
                name=str(item.get("name") or symbol),
                pct_change=pct_change,
                amount=amount,
                notice_count=notice_count,
                has_financial_abstract=has_financial_abstract,
                sector_context=sector_context,
                capital_flow_score=capital_flow_score,
                sentiment_score=sentiment_score,
                lhb_score=lhb_score,
                event_score=event_score,
            )
            risk = self._candidate_risk(
                degraded_sources=degraded_sources,
                missing_evidence=missing_evidence,
                pct_change=pct_change,
                capital_flow_score=capital_flow_score,
                sentiment_score=sentiment_score,
            )
            candidates.append(
                {
                    "symbol": symbol,
                    "name": str(item.get("name") or symbol),
                    "sector": item.get("sector"),
                    "theme": item.get("theme"),
                    "latest_price": price,
                    "latest_pct_change": pct_change,
                    "latest_amount": amount,
                    "revenue_proxy": revenue_proxy,
                    "notice_count": notice_count,
                    "has_financial_abstract": has_financial_abstract,
                    "score": score,
                    "score_breakdown": score_breakdown,
                    "why_it_made_the_list": rationale,
                    "main_risk": risk,
                    "missing_evidence": missing_evidence,
                    "degraded_sources": degraded_sources,
                    "deep_checked": deep_checked,
                    "capital_flow_score": capital_flow_score,
                    "sentiment_score": sentiment_score,
                    "lhb_score": lhb_score,
                    "event_score": event_score,
                    "methodology": {},
                }
            )

        candidates.sort(
            key=lambda item: (
                item.get("score") or 0.0,
                item.get("latest_amount") or 0.0,
                item.get("latest_pct_change") or -999.0,
            ),
            reverse=True,
        )
        filtered_candidates = [item for item in candidates if (item.get("score") or 0.0) >= min_score]
        watchlist_pool = [
            item
            for item in candidates
            if watchlist_min_score <= (item.get("score") or 0.0) < min_score
        ]
        top_candidates = filtered_candidates[: int(constraints.get("top_k") or 5)]
        watchlist_candidates = watchlist_pool[:watchlist_top_k]
        for item in top_candidates:
            item["candidate_tier"] = "core"
        for item in watchlist_candidates:
            item["candidate_tier"] = "watchlist"
        prefer_methodology = str(strategy["preferred_methodology"] or "comps").strip() or "comps"
        methodology_notes = self._attach_comps_methodology(
            candidates=top_candidates + watchlist_candidates,
            peer_universe=candidates,
            cycle=cycle,
            sector_context=sector_context,
            enabled=bool(methodology_enabled and prefer_methodology == "comps"),
        )
        enrichment_targets = []
        if not skip_enrichment:
            enrichment_pool = top_candidates + watchlist_candidates
            enrichment_targets = [item for item in enrichment_pool[:enrichment_top_n] if not item.get("deep_checked")]
        deep_checked_count = sum(1 for item in top_candidates if item.get("deep_checked"))
        watchlist_deep_checked_count = sum(1 for item in watchlist_candidates if item.get("deep_checked"))
        scorecard = {
            "investment_cycle": cycle,
            "investment_cycle_assumption": cycle_assumption,
            "candidate_count": len(candidates),
            "eligible_candidate_count": len(filtered_candidates),
            "top_candidate_count": len(top_candidates),
            "watchlist_candidate_count": len(watchlist_candidates),
            "deep_checked_count": deep_checked_count,
            "watchlist_deep_checked_count": watchlist_deep_checked_count,
            "enrichment_target_count": len(enrichment_targets),
            "min_score": min_score,
            "watchlist_min_score": watchlist_min_score,
            "preferred_methodology": prefer_methodology,
            "methodology_notes": methodology_notes,
            "average_score": round(sum(item.get("score") or 0.0 for item in top_candidates) / len(top_candidates), 3)
            if top_candidates
            else 0.0,
            "sector_context_present": bool(sector_context),
            "summary": self._summary(
                top_candidates=top_candidates,
                watchlist_candidates=watchlist_candidates,
                sector_context=sector_context,
                cycle=cycle,
            ),
            "planning_context_hints": planning_context_hints or None,
            "screening_strategy": strategy,
        }
        return {
            "screened_candidates": top_candidates,
            "watchlist_candidates": watchlist_candidates,
            "enrichment_targets": enrichment_targets,
            "scorecard": scorecard,
            "screening_notes": [
                f"investment_cycle={cycle}",
                f"investment_cycle_assumption={cycle_assumption}",
                f"candidate_count={len(candidates)}",
                f"eligible_candidate_count={len(filtered_candidates)}",
                f"top_candidate_count={len(top_candidates)}",
                f"watchlist_candidate_count={len(watchlist_candidates)}",
                f"deep_checked_count={deep_checked_count}",
                f"watchlist_deep_checked_count={watchlist_deep_checked_count}",
                f"enrichment_target_count={len(enrichment_targets)}",
                f"min_score={min_score}",
                f"watchlist_min_score={watchlist_min_score}",
                f"preferred_methodology={prefer_methodology}",
                *list(strategy.get("notes") or []),
                *methodology_notes,
            ],
        }

    def _normalize_planning_context_hints(self, raw_hints) -> dict[str, str]:
        return normalize_planning_context_hints(raw_hints)

    def _derive_screening_strategy(
        self,
        *,
        query: str,
        source_payload: dict,
        planning_context_hints: dict[str, str],
        explicit_cycle,
        explicit_methodology,
    ) -> dict[str, Any]:
        context = build_planning_context_view(
            query=query,
            planning_context_hints=planning_context_hints,
        )
        text = context.combined_text
        preferred_methodology_from_context, methodology_source = derive_methodology_focus(
            cashflow_text=context.query_text,
            comparison_text=text,
            planning_context_hints=planning_context_hints,
        )
        base_cycle = source_payload.get("investment_cycle")
        if explicit_cycle:
            resolved_cycle = str(explicit_cycle).strip()
            cycle_assumption = "explicit"
        elif base_cycle:
            resolved_cycle = str(base_cycle).strip()
            cycle_assumption = str(source_payload.get("investment_cycle_assumption") or "explicit")
        elif has_momentum_focus(text):
            resolved_cycle = "short_term"
            cycle_assumption = "planning_context"
        elif has_long_term_bias(text):
            resolved_cycle = "long_term"
            cycle_assumption = "planning_context"
        else:
            resolved_cycle = "mid_term"
            cycle_assumption = "default_mid_term"

        if explicit_methodology:
            preferred_methodology = str(explicit_methodology).strip() or "comps"
            methodology_source = "explicit"
        elif preferred_methodology_from_context:
            preferred_methodology = preferred_methodology_from_context
        else:
            preferred_methodology = "comps"
            methodology_source = "default"

        notes = [
            f"screening_strategy_cycle={resolved_cycle}",
            f"screening_strategy_methodology={preferred_methodology}",
            f"screening_strategy_methodology_source={methodology_source}",
        ]
        if planning_context_hints:
            notes.append("screening_strategy_has_context_hints=true")
        return {
            "resolved_investment_cycle": resolved_cycle,
            "investment_cycle_assumption": cycle_assumption,
            "preferred_methodology": preferred_methodology,
            "methodology_source": methodology_source,
            "notes": notes,
        }

    def _latest_price(self, market_snapshot: dict | None, fallback: Any = None) -> float | None:
        facts = (market_snapshot or {}).get("facts") or {}
        overview = facts.get("market_overview") or {}
        spot = overview.get("spot") or {}
        value = self._to_float(spot.get("latest_price"))
        if value is None:
            value = self._to_float(spot.get("最新价"))
        if value is None:
            value = self._to_float(spot.get("鏈€鏂颁环"))
        return value if value is not None else self._to_float(fallback)

    def _latest_pct_change(self, market_snapshot: dict | None, fallback: Any = None) -> float | None:
        facts = (market_snapshot or {}).get("facts") or {}
        overview = facts.get("market_overview") or {}
        spot = overview.get("spot") or {}
        value = self._to_float(spot.get("pct_change"))
        if value is None:
            value = self._to_float(spot.get("涨跌幅"))
        if value is None:
            value = self._to_float(spot.get("娑ㄨ穼骞?"))
        return value if value is not None else self._to_float(fallback)

    def _latest_amount(self, market_snapshot: dict | None, fallback: Any = None) -> float | None:
        facts = (market_snapshot or {}).get("facts") or {}
        overview = facts.get("market_overview") or {}
        spot = overview.get("spot") or {}
        value = self._to_float(spot.get("amount"))
        if value is None:
            value = self._to_float(spot.get("成交额"))
        if value is None:
            value = self._to_float(spot.get("鎴愪氦棰?"))
        return value if value is not None else self._to_float(fallback)

    def _notice_count(self, notice_snapshot: dict | None) -> int:
        facts = (notice_snapshot or {}).get("facts") or {}
        value = facts.get("notice_count")
        try:
            return int(value or 0)
        except Exception:
            return 0

    def _has_financial_abstract(self, fundamental_snapshot: dict | None) -> bool:
        facts = (fundamental_snapshot or {}).get("facts") or {}
        abstract = facts.get("financial_abstract") or {}
        return bool(abstract)

    def _revenue_proxy(self, fundamental_snapshot: dict | None, fallback: Any = None) -> float | None:
        facts = (fundamental_snapshot or {}).get("facts") or {}
        abstract = facts.get("financial_abstract") or {}
        for key in ("营业总收入", "营业收入", "营收", "总营收"):
            value = self._to_float(abstract.get(key))
            if value is not None:
                return value
        for item_key, item_value in abstract.items():
            key_text = str(item_key)
            if any(token in key_text for token in ("营业", "营收", "收入")):
                value = self._to_float(item_value)
                if value is not None:
                    return value
        return self._to_float(fallback)

    def _attach_comps_methodology(
        self,
        *,
        candidates: list[dict[str, Any]],
        peer_universe: list[dict[str, Any]],
        cycle: str,
        sector_context: dict | None,
        enabled: bool,
    ) -> list[str]:
        if not enabled:
            return ["methodology_comps=disabled"]

        notes: list[str] = []
        for candidate in candidates:
            peers = [
                peer
                for peer in peer_universe
                if str(peer.get("symbol") or "").strip()
                and str(peer.get("symbol") or "").strip() != str(candidate.get("symbol") or "").strip()
            ]
            comps_result = self.comps_service.build_for_candidate(
                candidate=candidate,
                peers=peers,
                cycle=cycle,
                sector_context=sector_context,
            )
            candidate.setdefault("methodology", {})["comps"] = comps_result
            candidate["valuation_band"] = comps_result.get("valuation_band") or {}
            candidate["valuation_view"] = ((comps_result.get("outputs") or {}).get("relative_view"))
            notes.append(
                f"comps:{candidate.get('symbol')}={((comps_result.get('outputs') or {}).get('relative_view') or 'insufficient_peer_signal')}"
            )
        if not notes:
            return ["methodology_comps=no_candidates"]
        return notes

    def _missing_evidence(
        self,
        *,
        market_snapshot: dict | None,
        fundamental_snapshot: dict | None,
        notice_snapshot: dict | None,
    ) -> list[str]:
        missing: list[str] = []
        if market_snapshot is None or market_snapshot.get("degraded"):
            missing.append("market_snapshot")
        if fundamental_snapshot is None or fundamental_snapshot.get("degraded"):
            missing.append("fundamental_snapshot")
        if notice_snapshot is None or notice_snapshot.get("degraded"):
            missing.append("notice_snapshot")
        return missing

    def _degraded_sources(
        self,
        *,
        market_snapshot: dict | None,
        fundamental_snapshot: dict | None,
        notice_snapshot: dict | None,
        lhb_snapshot: dict | None,
        capital_flow_snapshot: dict | None,
        sentiment_snapshot: dict | None,
        events_snapshot: dict | None,
    ) -> list[str]:
        degraded: list[str] = []
        for name, snapshot in (
            ("market_snapshot", market_snapshot),
            ("fundamental_snapshot", fundamental_snapshot),
            ("notice_snapshot", notice_snapshot),
            ("lhb_snapshot", lhb_snapshot),
            ("capital_flow_snapshot", capital_flow_snapshot),
            ("sentiment_snapshot", sentiment_snapshot),
            ("events_snapshot", events_snapshot),
        ):
            if snapshot is not None and snapshot.get("degraded"):
                degraded.append(name)
        return degraded

    def _liquidity_score(self, amount: float | None) -> float:
        if amount is None:
            return 0.0
        if amount >= 2_000_000_000:
            return 3.0
        if amount >= 1_000_000_000:
            return 2.5
        if amount >= 500_000_000:
            return 2.0
        if amount >= 200_000_000:
            return 1.0
        return 0.25

    def _momentum_score(self, pct_change: float | None, *, cycle: str) -> float:
        if pct_change is None:
            return 0.0
        if cycle == "short_term":
            return max(min(pct_change / 2.0, 2.0), -2.0)
        if cycle == "long_term":
            return max(min(pct_change / 4.0, 1.5), -1.5)
        return max(min(pct_change / 3.0, 1.75), -1.75)

    def _notice_score(self, notice_count: int) -> float:
        if notice_count >= 5:
            return 1.5
        if notice_count >= 3:
            return 1.0
        if notice_count >= 1:
            return 0.5
        return 0.0

    def _capital_flow_score(self, snapshot: dict | None) -> float:
        facts = (snapshot or {}).get("facts") or {}
        latest = facts.get("latest_flow") or {}
        score = 0.0
        for key, value in latest.items():
            key_text = str(key)
            if "净流入" not in key_text and "净额" not in key_text and "flow" not in key_text.lower():
                continue
            numeric = self._to_float(value)
            if numeric is None:
                continue
            if numeric > 0:
                score += 0.5
            elif numeric < 0:
                score -= 0.35
            break
        if facts.get("main_flow_rank"):
            score += 0.35
        return round(score, 3)

    def _sentiment_score(self, snapshot: dict | None) -> float:
        facts = (snapshot or {}).get("facts") or {}
        latest_rank = facts.get("hot_rank_latest") or []
        detail = facts.get("hot_rank_detail") or []
        keywords = facts.get("hot_keywords") or []
        score = 0.0
        if latest_rank:
            score += 0.4
        if detail:
            score += 0.25
        if keywords:
            score += 0.2
        if not latest_rank and not detail:
            return 0.0
        return round(score, 3)

    def _lhb_score(self, snapshot: dict | None) -> float:
        facts = (snapshot or {}).get("facts") or {}
        buy_total = self._to_float(facts.get("buy_top_total"))
        sell_total = self._to_float(facts.get("sell_top_total"))
        if buy_total is None and sell_total is None:
            return 0.0
        if buy_total is not None and sell_total is not None:
            if buy_total > sell_total:
                return 0.35
            if buy_total < sell_total:
                return -0.15
        return 0.1

    def _event_score(self, snapshot: dict | None) -> float:
        facts = (snapshot or {}).get("facts") or {}
        event_keywords = facts.get("event_keywords") or []
        event_notices = facts.get("event_notices") or []
        score = 0.0
        if event_keywords:
            score += min(len(event_keywords) * 0.15, 0.45)
        if event_notices:
            score += 0.15
        return round(score, 3)

    def _candidate_rationale(
        self,
        *,
        name: str,
        pct_change: float | None,
        amount: float | None,
        notice_count: int,
        has_financial_abstract: bool,
        sector_context: dict | None,
        capital_flow_score: float,
        sentiment_score: float,
        lhb_score: float,
        event_score: float,
    ) -> str:
        reasons: list[str] = []
        if pct_change is not None:
            if pct_change > 0:
                reasons.append(f"最新涨跌幅 {pct_change}%")
            else:
                reasons.append(f"最新涨跌幅 {pct_change}%，但仍保留在观察池中")
        if amount is not None:
            reasons.append(f"成交额约 {round(amount / 100000000, 2)} 亿元")
        if notice_count > 0:
            reasons.append(f"近30天公告 {notice_count} 条")
        if has_financial_abstract:
            reasons.append("财务摘要可用")
        if sector_context and sector_context.get("sector"):
            reasons.append(f"来自 {sector_context.get('sector')} 相关候选池")
        if capital_flow_score > 0:
            reasons.append("资金流向维度偏强")
        if sentiment_score > 0:
            reasons.append("热度/人气维度有正向信号")
        if lhb_score > 0:
            reasons.append("龙虎榜维度显示活跃迹象")
        if event_score > 0:
            reasons.append("近30天存在事件催化线索")
        return f"{name}: " + "，".join(reasons) if reasons else f"{name}: 证据较少，仅保留为备选"

    def _candidate_risk(
        self,
        *,
        degraded_sources: list[str],
        missing_evidence: list[str],
        pct_change: float | None,
        capital_flow_score: float,
        sentiment_score: float,
    ) -> str:
        reasons: list[str] = []
        if degraded_sources:
            reasons.append(f"数据源降级: {', '.join(degraded_sources)}")
        if missing_evidence:
            reasons.append(f"关键证据缺失: {', '.join(missing_evidence)}")
        if pct_change is not None and pct_change < -4.0:
            reasons.append("短期价格承压较明显")
        if capital_flow_score < 0:
            reasons.append("资金流向维度偏弱")
        if sentiment_score < 0:
            reasons.append("热度/人气维度偏冷")
        return "，".join(reasons) if reasons else "暂无明显结构性风险，但仍需人工复核"

    def _summary(self, *, top_candidates: list[dict], watchlist_candidates: list[dict], sector_context: dict | None, cycle: str) -> str:
        if not top_candidates and not watchlist_candidates:
            return "未筛出满足最小证据要求的候选。"
        names = [str(item.get("name") or item.get("symbol") or "") for item in top_candidates[:3]]
        watch_names = [str(item.get("name") or item.get("symbol") or "") for item in watchlist_candidates[:3]]
        prefix = f"{cycle} 候选已生成"
        if sector_context and sector_context.get("sector"):
            prefix += f"，主题偏向 {sector_context.get('sector')}"
        if top_candidates and watchlist_candidates:
            return (
                f"{prefix}，优先关注: {', '.join(name for name in names if name)}；"
                f"次级观察: {', '.join(name for name in watch_names if name)}"
            )
        if top_candidates:
            return f"{prefix}，优先关注: {', '.join(name for name in names if name)}"
        return f"{prefix}，当前仅形成次级观察名单: {', '.join(name for name in watch_names if name)}"

    def _to_float(self, value: Any) -> float | None:
        if value in (None, "", False):
            return None
        try:
            return float(value)
        except Exception:
            return None

    def _default_min_score(self, cycle: str) -> float:
        if cycle == "short_term":
            return 1.0
        if cycle == "long_term":
            return 0.5
        return 0.75

    def _default_watchlist_min_score(self, cycle: str) -> float:
        if cycle == "short_term":
            return 0.0
        if cycle == "long_term":
            return 0.0
        return 0.25
