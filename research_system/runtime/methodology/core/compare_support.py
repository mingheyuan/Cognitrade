from __future__ import annotations

from research_system.runtime.orchestration.orchestrator_context import RuntimeContext
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.runtime import BlackboardEntry


class OrchestratorMethodologyCompareSupport:
    def __init__(self, *, builder, snapshot_support, bank_scorecard_support) -> None:
        self.builder = builder
        self.snapshot_support = snapshot_support
        self.bank_scorecard_support = bank_scorecard_support

    def build_stock_compare_entries(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        intent: str,
        phase: str,
        agent: str,
        compare_request_entry,
    ) -> list[BlackboardEntry]:
        compare_targets = list(((compare_request_entry.payload if compare_request_entry else {}) or {}).get("compare_targets") or [])
        snapshot_groups = self.snapshot_support.group_snapshots_by_symbol(context)
        peer_candidates = [
            self.snapshot_support.methodology_candidate_payload(
                symbol=str(target.get("symbol") or "").strip(),
                name=str(target.get("name") or target.get("symbol") or "").strip(),
                snapshots=snapshot_groups.get(str(target.get("symbol") or "").strip(), {}),
                sector=((compare_request_entry.payload if compare_request_entry else {}) or {}).get("sector"),
            )
            for target in compare_targets
            if str(target.get("symbol") or "").strip()
        ]
        cycle = self.builder.infer_investment_cycle(request.user_query)
        entries: list[BlackboardEntry] = []
        for candidate in peer_candidates:
            symbol = str(candidate.get("symbol") or "").strip()
            if not symbol:
                continue
            candidate_snapshots = snapshot_groups.get(symbol, {})
            market_snapshot = candidate_snapshots.get("market")
            fundamental_snapshot = candidate_snapshots.get("fundamental")
            if market_snapshot is None and fundamental_snapshot is None:
                continue
            peers = [peer for peer in peer_candidates if str(peer.get("symbol") or "").strip() != symbol]
            entries.extend(
                self.builder.build_entries_for_symbol(
                    context=context,
                    request=request,
                    phase=phase,
                    agent=agent,
                    intent=intent,
                    symbol=symbol,
                    candidate_name=str(candidate.get("name") or symbol),
                    sector=((compare_request_entry.payload if compare_request_entry else {}) or {}).get("sector"),
                    cycle=cycle,
                    market_snapshot=market_snapshot,
                    fundamental_snapshot=fundamental_snapshot,
                    peers=peers,
                )
            )
        scorecard_entry = self.bank_scorecard_support.build_bank_compare_scorecard_entry(
            context=context,
            request=request,
            phase=phase,
            agent=agent,
            compare_targets=compare_targets,
            peer_candidates=peer_candidates,
        )
        if scorecard_entry is not None:
            entries.append(scorecard_entry)
        return entries
