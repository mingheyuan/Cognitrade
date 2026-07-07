from __future__ import annotations

from pathlib import Path

from research_system.agents._subagent_dag import SubagentDagExecutor, SubagentDagNodeSpec
from research_system.schemas.subagents import SubagentExecutionRecord, SubagentInvocation
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

    def __init__(self, skill_runtime: SkillRuntime, runs_root: Path | None = None) -> None:
        self._skill_runtime = skill_runtime
        self.runs_root = runs_root
        self.registry = SubagentRegistry(
            [
                QueryNormalizerSubagent(normalize_query=self._normalize_query),
                StockSnapshotCollectorSubagent(skill_runtime),
                SectorSnapshotCollectorSubagent(skill_runtime),
                CandidateUniverseBuilderSubagent(skill_runtime),
                ReviewTargetLoaderSubagent(runs_root),
            ]
        )

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
            }
        trace = []
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
            stock_outputs, trace_record = self._execute_single_subagent_node(
                node_id="stock_snapshot_collector",
                invocation=SubagentInvocation(
                    subagent_type="stock_snapshot_collector",
                    objective="Collect stock-level verified snapshots.",
                    inputs={
                        "query": query,
                        "symbol": symbol,
                        "snapshot_types": requested_snapshot_types,
                        "allow_partial": bool(constraints.get("allow_partial_snapshot_types")) or bool(collection_strategy.get("hint_augmented_snapshot_types")),
                    },
                    context={
                        "task_name": "collect:stock_snapshot_collector",
                        "agent_path": "collector/stock_snapshot_collector",
                    },
                ),
                max_attempts=2,
            )
            trace.append(trace_record)
            snapshot_payloads.extend(stock_outputs["snapshots"])
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
            compare_trace_rows, compare_snapshots, compare_child_thread_ids = self._run_stock_snapshot_jobs(
                compare_jobs,
                max_workers=len(compare_jobs),
            )
            trace.extend(compare_trace_rows)
            snapshot_payloads.extend(compare_snapshots)
            child_thread_ids.extend(compare_child_thread_ids)
        elif intent == "sector_research":
            include_policy_snapshot = bool(constraints.get("include_policy_snapshot")) or bool(collection_strategy.get("include_policy_snapshot"))
            sector_outputs, trace_record = self._execute_single_subagent_node(
                node_id="sector_snapshot_collector",
                invocation=SubagentInvocation(
                    subagent_type="sector_snapshot_collector",
                    objective="Collect sector-level verified snapshot.",
                    inputs={"query": query, "include_policy_snapshot": include_policy_snapshot},
                    context={
                        "task_name": "collect:sector_snapshot_collector",
                        "agent_path": "collector/sector_snapshot_collector",
                    },
                ),
            )
            trace.append(trace_record)
            sector_snapshot = sector_outputs["snapshot"]
            source_payload["sector"] = sector_snapshot.get("sector")
            source_payload["theme"] = sector_snapshot.get("theme")
            if include_policy_snapshot:
                source_payload["include_policy_snapshot"] = True
            snapshot_payloads.append(sector_snapshot)
            if include_policy_snapshot:
                snapshot_payloads.extend(list(sector_outputs.get("related_snapshots") or []))
        elif intent == "stock_screen":
            candidate_pool_size = int(constraints.get("candidate_pool_size") or 8)
            resolved_cycle = investment_cycle or constraints.get("investment_cycle") or "mid_term"
            universe_outputs, trace_record = self._execute_single_subagent_node(
                node_id="candidate_universe_builder",
                invocation=SubagentInvocation(
                    subagent_type="candidate_universe_builder",
                    objective="Build candidate universe for stock screening.",
                    inputs={
                        "query": query,
                        "sector_query": sector_query,
                        "top_n": candidate_pool_size,
                    },
                    context={
                        "task_name": "collect:candidate_universe_builder",
                        "agent_path": "collector/candidate_universe_builder",
                    },
                ),
            )
            trace.append(trace_record)
            source_payload.update(
                {
                    "title": "stock_screen input request",
                    "source_type": "screen_request",
                    "investment_cycle": collection_strategy.get("resolved_investment_cycle") or resolved_cycle,
                    "investment_cycle_assumption": (
                        str(collection_strategy.get("cycle_assumption") or "").strip()
                        or ("default_mid_term" if not investment_cycle and not constraints.get("investment_cycle") else "explicit")
                    ),
                    "candidate_universe": universe_outputs["candidate_universe"],
                    "sector_context": universe_outputs.get("sector_context"),
                }
            )
            sector_context = universe_outputs.get("sector_context")
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
            review_payload, trace_record = self._execute_single_subagent_node(
                node_id="review_target_loader",
                invocation=SubagentInvocation(
                    subagent_type="review_target_loader",
                    objective="Load prior run as review target.",
                    inputs={"constraints": constraints, "query": query},
                    context={
                        "task_name": "collect:review_target_loader",
                        "agent_path": "collector/review_target_loader",
                    },
                ),
            )
            trace.append(trace_record)
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
            if sector:
                source_payload["sector"] = sector
                sector_outputs, trace_record = self._execute_single_subagent_node(
                    node_id="review_sector_snapshot_collector",
                    invocation=SubagentInvocation(
                        subagent_type="sector_snapshot_collector",
                        objective="Collect sector snapshot for posterior review.",
                        inputs={"query": sector, "include_policy_snapshot": bool(constraints.get("include_policy_snapshot"))},
                        context={
                            "task_name": "collect:review:sector_snapshot_collector",
                            "agent_path": "collector/review/sector_snapshot_collector",
                        },
                    ),
                )
                trace.append(trace_record)
                snapshot_payloads.append(sector_outputs["snapshot"])
            if resolved_symbol:
                source_payload["symbol"] = resolved_symbol
                requested_snapshot_types = self._merge_snapshot_types(
                    constraints.get("snapshot_types")
                    or ["market", "fundamental", "notice"],
                    collection_strategy.get("hint_augmented_snapshot_types") or [],
                )
                stock_outputs, trace_record = self._execute_single_subagent_node(
                    node_id="review_stock_snapshot_collector",
                    invocation=SubagentInvocation(
                        subagent_type="stock_snapshot_collector",
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
                    ),
                    max_attempts=2,
                )
                trace.append(trace_record)
                snapshot_payloads.extend(stock_outputs["snapshots"])
        else:
            stock_outputs, trace_record = self._execute_single_subagent_node(
                node_id="default_stock_snapshot_collector",
                invocation=SubagentInvocation(
                    subagent_type="stock_snapshot_collector",
                    objective="Collect default market snapshot.",
                    inputs={
                        "query": query,
                        "symbol": symbol,
                        "snapshot_types": ["market"],
                    },
                    context={
                        "task_name": "collect:default:stock_snapshot_collector",
                        "agent_path": "collector/default/stock_snapshot_collector",
                    },
                ),
                max_attempts=2,
            )
            trace.append(trace_record)
            snapshot_payloads.extend(stock_outputs["snapshots"])

        if child_thread_ids:
            source_payload["child_thread_ids"] = sorted(set(child_thread_ids))
        return {
            "sources": [source_payload],
            "snapshots": snapshot_payloads,
            "subagent_trace": trace,
        }

    def _execute_subagent_dag(
        self,
        nodes: list[SubagentDagNodeSpec],
        *,
        max_workers: int | None = None,
    ) -> dict:
        executor = SubagentDagExecutor(
            invoke_subagent=self._invoke_subagent,
            emit_event=self._emit_runtime_event,
        )
        return executor.execute(nodes, max_workers=max_workers)

    def _execute_single_subagent_node(
        self,
        *,
        node_id: str,
        invocation: SubagentInvocation,
        max_attempts: int = 1,
    ) -> tuple[dict, dict]:
        results = self._execute_subagent_dag(
            [
                SubagentDagNodeSpec(
                    node_id=node_id,
                    build_invocation=lambda _results, *, current_invocation=invocation: current_invocation,
                    max_attempts=max_attempts,
                    metadata={
                        "task_name": str((invocation.context or {}).get("task_name") or node_id),
                        "agent_path": str((invocation.context or {}).get("agent_path") or node_id),
                        "phase": str((invocation.context or {}).get("phase") or "collect"),
                    },
                )
            ],
            max_workers=1,
        )
        result = results[node_id]
        if result.record is None or result.outputs is None:
            raise RuntimeError(f"collector dag node missing outputs: {node_id}")
        return dict(result.outputs), self._trace_record_from_dag_result(
            result=result,
            fallback_subagent_type=invocation.subagent_type,
            fallback_objective=str(invocation.objective or ""),
            fallback_task_name=str((invocation.context or {}).get("task_name") or node_id),
            fallback_agent_path=str((invocation.context or {}).get("agent_path") or node_id),
            phase=str((invocation.context or {}).get("phase") or "collect"),
        )

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
            max_workers=len(enrichment_jobs),
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
        max_workers: int | None = None,
    ) -> tuple[list[dict], list[dict], list[str]]:
        if not jobs:
            return [], [], []
        trace_rows: list[dict] = []
        snapshots: list[dict] = []
        child_thread_ids: list[str] = []
        executor = SubagentDagExecutor(
            invoke_subagent=self._invoke_subagent,
            emit_event=self._emit_runtime_event,
        )
        nodes: list[SubagentDagNodeSpec] = []
        for index, (symbol, child_thread, invocation) in enumerate(jobs):
            nodes.append(
                SubagentDagNodeSpec(
                    node_id=f"stock_snapshot_collector:{index}:{symbol}",
                    build_invocation=lambda _results, *, current_invocation=invocation: current_invocation,
                    max_attempts=2,
                    metadata={
                        "task_name": str((invocation.context or {}).get("task_name") or "collect:stock_snapshot_collector"),
                        "agent_path": str((invocation.context or {}).get("agent_path") or "collector/stock_snapshot_collector"),
                        "child_thread_id": getattr(child_thread, "thread_id", None),
                        "child_turn_id": str((invocation.context or {}).get("child_turn_id") or "").strip() or None,
                        "phase": str((invocation.context or {}).get("phase") or "collect"),
                    },
                )
            )
        results = executor.execute(nodes, max_workers=max_workers)
        for (symbol, child_thread, _), node in zip(jobs, nodes, strict=False):
            result = results[node.node_id]
            if result.record is None or result.outputs is None:
                raise RuntimeError(f"stock snapshot dag node missing outputs: {symbol}")
            metadata = dict(result.metadata or {})
            trace_record = self._trace_record_from_dag_result(
                result=result,
                fallback_subagent_type="stock_snapshot_collector",
                fallback_objective="Collect stock-level verified snapshots.",
                fallback_task_name=str(metadata.get("task_name") or "collect:stock_snapshot_collector"),
                fallback_agent_path=str(metadata.get("agent_path") or "collector/stock_snapshot_collector"),
                phase=str(metadata.get("phase") or "collect"),
            )
            if child_thread is not None:
                trace_record.setdefault("notes", []).append(f"child_thread={child_thread.thread_id}")
                child_thread_ids.append(child_thread.thread_id)
            trace_rows.append(trace_record)
            snapshots.extend(list(result.outputs.get("snapshots") or []))
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
