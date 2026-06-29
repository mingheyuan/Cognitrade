from __future__ import annotations

from pathlib import Path

from research_system.schemas.subagents import SubagentInvocation
from research_system.runtime.skill_runtime import SkillRuntime
from research_system.subagents.collector import (
    CandidateUniverseBuilderSubagent,
    DistillRequestLoaderSubagent,
    QueryNormalizerSubagent,
    ReviewTargetLoaderSubagent,
    SectorSnapshotCollectorSubagent,
    StockSnapshotCollectorSubagent,
)
from research_system.subagents.registry import SubagentRegistry


class CollectorService:
    agent_name = "collector"

    def __init__(self, skill_runtime: SkillRuntime, runs_root: Path | None = None) -> None:
        self.skill_runtime = skill_runtime
        self.runs_root = runs_root
        self.registry = SubagentRegistry(
            [
                QueryNormalizerSubagent(),
                StockSnapshotCollectorSubagent(skill_runtime),
                SectorSnapshotCollectorSubagent(skill_runtime),
                CandidateUniverseBuilderSubagent(skill_runtime),
                DistillRequestLoaderSubagent(),
                ReviewTargetLoaderSubagent(runs_root),
            ]
        )

    def run(self, *, intent: str, query: str, constraints: dict | None = None) -> dict:
        constraints = constraints or {}
        if "[wait_external]" in query:
            return {
                "requires_wait": True,
                "sources": [],
                "snapshots": [],
                "subagent_trace": [],
            }
        trace = []
        normalized, record = self.registry.invoke(
            SubagentInvocation(
                subagent_type="query_normalizer",
                objective="Extract structured query targets.",
                inputs={"query": query},
            )
        )
        trace.append(record.model_dump(mode="json"))
        symbol = str(constraints.get("symbol") or normalized.get("symbol") or "").strip() or None
        sector_query = str(constraints.get("sector_query") or normalized.get("sector_query") or "").strip() or None
        investment_cycle = constraints.get("investment_cycle") or normalized.get("investment_cycle")
        source_type_override = str(constraints.get("source_type") or "").strip() or None
        snapshot_payloads = []
        source_payload = {
            "title": f"{intent} input query",
            "content": query,
            "source_type": source_type_override or "user_query",
            "symbol": symbol,
        }

        if intent == "stock_check":
            requested_snapshot_types = list(constraints.get("snapshot_types") or ["market", "fundamental", "notice"])
            stock_outputs, record = self.registry.invoke(
                SubagentInvocation(
                    subagent_type="stock_snapshot_collector",
                    objective="Collect stock-level verified snapshots.",
                    inputs={
                        "query": query,
                        "symbol": symbol,
                        "snapshot_types": requested_snapshot_types,
                    },
                )
            )
            trace.append(record.model_dump(mode="json"))
            snapshot_payloads.extend(stock_outputs["snapshots"])
        elif intent == "sector_research":
            sector_outputs, record = self.registry.invoke(
                SubagentInvocation(
                    subagent_type="sector_snapshot_collector",
                    objective="Collect sector-level verified snapshot.",
                    inputs={"query": query},
                )
            )
            trace.append(record.model_dump(mode="json"))
            sector_snapshot = sector_outputs["snapshot"]
            source_payload["sector"] = sector_snapshot.get("sector")
            source_payload["theme"] = sector_snapshot.get("theme")
            snapshot_payloads.append(sector_snapshot)
        elif intent == "stock_screen":
            candidate_pool_size = int(constraints.get("candidate_pool_size") or 8)
            resolved_cycle = investment_cycle or constraints.get("investment_cycle") or "mid_term"
            universe_outputs, record = self.registry.invoke(
                SubagentInvocation(
                    subagent_type="candidate_universe_builder",
                    objective="Build candidate universe for stock screening.",
                    inputs={
                        "query": query,
                        "sector_query": sector_query,
                        "top_n": candidate_pool_size,
                    },
                )
            )
            trace.append(record.model_dump(mode="json"))
            source_payload.update(
                {
                    "title": "stock_screen input request",
                    "source_type": "screen_request",
                    "investment_cycle": resolved_cycle,
                    "investment_cycle_assumption": "default_mid_term" if not investment_cycle and not constraints.get("investment_cycle") else "explicit",
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
            distill_outputs, record = self.registry.invoke(
                SubagentInvocation(
                    subagent_type="distill_request_loader",
                    objective="Resolve distillation source request.",
                    inputs={"query": query, "jsonl_path": normalized.get("jsonl_path")},
                )
            )
            trace.append(record.model_dump(mode="json"))
            source_payload.update(distill_outputs)
        elif intent == "review_thesis":
            review_payload, record = self.registry.invoke(
                SubagentInvocation(
                    subagent_type="review_target_loader",
                    objective="Load prior run as review target.",
                    inputs={"constraints": constraints, "query": query},
                )
            )
            trace.append(record.model_dump(mode="json"))
            source_payload.update(
                {
                    "title": "review_thesis input request",
                    "source_type": source_type_override or "review_request",
                    "review_target_run_id": review_payload["review_target_run_id"],
                    "review_source_entries": review_payload["review_source_entries"],
                    "review_source_result": review_payload["review_source_result"],
                }
            )
            sector = review_payload.get("sector")
            resolved_symbol = review_payload.get("symbol")
            if sector:
                source_payload["sector"] = sector
                sector_outputs, record = self.registry.invoke(
                    SubagentInvocation(
                        subagent_type="sector_snapshot_collector",
                        objective="Collect sector snapshot for posterior review.",
                        inputs={"query": sector},
                    )
                )
                trace.append(record.model_dump(mode="json"))
                snapshot_payloads.append(sector_outputs["snapshot"])
            if resolved_symbol:
                source_payload["symbol"] = resolved_symbol
                stock_outputs, record = self.registry.invoke(
                    SubagentInvocation(
                        subagent_type="stock_snapshot_collector",
                        objective="Collect stock snapshots for posterior review.",
                        inputs={
                            "query": resolved_symbol,
                            "symbol": resolved_symbol,
                            "snapshot_types": ["market", "fundamental", "notice"],
                        },
                    )
                )
                trace.append(record.model_dump(mode="json"))
                snapshot_payloads.extend(stock_outputs["snapshots"])
        else:
            stock_outputs, record = self.registry.invoke(
                SubagentInvocation(
                    subagent_type="stock_snapshot_collector",
                    objective="Collect default market snapshot.",
                    inputs={
                        "query": query,
                        "symbol": symbol,
                        "snapshot_types": ["market"],
                    },
                )
            )
            trace.append(record.model_dump(mode="json"))
            snapshot_payloads.extend(stock_outputs["snapshots"])

        return {
            "sources": [source_payload],
            "snapshots": snapshot_payloads,
            "subagent_trace": trace,
        }

    def enrich_stock_screen_candidates(
        self,
        *,
        candidates: list[dict],
        snapshot_types: list[str] | None = None,
    ) -> dict:
        trace = []
        snapshots: list[dict] = []
        requested_snapshot_types = snapshot_types or ["market", "fundamental", "notice"]
        seen_symbols: set[str] = set()

        for candidate in candidates:
            symbol = str(candidate.get("symbol") or "").strip()
            if not symbol or symbol in seen_symbols:
                continue
            stock_outputs, record = self.registry.invoke(
                SubagentInvocation(
                    subagent_type="stock_snapshot_collector",
                    objective="Collect deep stock snapshots for shortlisted screen candidates.",
                    inputs={
                        "query": f"{symbol} {str(candidate.get('name') or '').strip()}".strip(),
                        "symbol": symbol,
                        "snapshot_types": requested_snapshot_types,
                    },
                )
            )
            trace.append(record.model_dump(mode="json"))
            snapshots.extend(stock_outputs["snapshots"])
            seen_symbols.add(symbol)

        return {
            "snapshots": snapshots,
            "subagent_trace": trace,
            "notes": [
                f"enriched_candidate_count={len(seen_symbols)}",
                f"snapshot_types={','.join(requested_snapshot_types)}",
            ],
        }
