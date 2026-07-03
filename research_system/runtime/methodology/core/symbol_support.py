from __future__ import annotations

from research_system.runtime.orchestration.orchestrator_context import RuntimeContext
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.runtime import BlackboardEntry


class OrchestratorMethodologySymbolSupport:
    def __init__(self, *, builder, entry_factory) -> None:
        self.builder = builder
        self.entry_factory = entry_factory

    def build_candidate_methodology_entries(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        phase: str,
        agent: str,
        candidate: dict,
        source_type: str,
    ) -> list[BlackboardEntry]:
        methodology = candidate.get("methodology") or {}
        comps_payload = methodology.get("comps")
        if not isinstance(comps_payload, dict):
            return []
        symbol = str(candidate.get("symbol") or "").strip()
        if not symbol:
            return []
        return [
            self.entry_factory.methodology_entry(
                context=context,
                request=request,
                phase=phase,
                agent=agent,
                methodology_type="comps",
                title=f"methodology_comps:{symbol}",
                payload=comps_payload,
                target_symbol=symbol,
                source_refs=[{"type": source_type, "symbol": symbol, "run_id": context.run_id}],
            )
        ]

    def build_stock_methodology_entries(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        intent: str,
        phase: str,
        agent: str,
        source_records: list,
        snapshots: dict[str, dict],
    ) -> list[BlackboardEntry]:
        request_entry = next(
            (
                entry for entry in source_records
                if str((entry.payload or {}).get("source_type") or "") in {"user_query", "stock_check_replenish", "review_request"}
            ),
            None,
        )
        symbol = str(((request_entry.payload if request_entry else {}) or {}).get("symbol") or "").strip()
        if not symbol:
            return []
        market_snapshot = snapshots.get("market")
        fundamental_snapshot = snapshots.get("fundamental")
        if market_snapshot is None and fundamental_snapshot is None:
            return []
        cycle = self.builder.infer_investment_cycle(request.user_query)
        return self.builder.build_entries_for_symbol(
            context=context,
            request=request,
            phase=phase,
            agent=agent,
            intent=intent,
            symbol=symbol,
            candidate_name=symbol,
            sector=((request_entry.payload if request_entry else {}) or {}).get("sector"),
            cycle=cycle,
            market_snapshot=market_snapshot,
            fundamental_snapshot=fundamental_snapshot,
            peers=[],
        )

    def build_review_methodology_entries(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        intent: str,
        phase: str,
        agent: str,
        review_request: dict,
        latest_market_snapshot: dict | None,
        latest_fundamental_snapshot: dict | None,
    ) -> list[BlackboardEntry]:
        symbol = str(review_request.get("symbol") or "").strip()
        if not symbol:
            return []
        cycle = self.builder.infer_investment_cycle(request.user_query)
        return self.builder.build_review_entries(
            context=context,
            request=request,
            intent=intent,
            phase=phase,
            agent=agent,
            symbol=symbol,
            cycle=cycle,
            latest_market_snapshot=latest_market_snapshot,
            latest_fundamental_snapshot=latest_fundamental_snapshot,
        )
