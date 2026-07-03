from __future__ import annotations

from research_system.runtime.methodology.banking.bank_scorecard import (
    OrchestratorMethodologyBankScorecardSupport,
)
from research_system.runtime.methodology.core.builder import (
    OrchestratorMethodologyBuilder,
)
from research_system.runtime.methodology.core.compare_support import (
    OrchestratorMethodologyCompareSupport,
)
from research_system.runtime.methodology.core.entry_factory import (
    OrchestratorMethodologyEntryFactory,
)
from research_system.runtime.methodology.snapshots.snapshot_support import (
    OrchestratorMethodologySnapshotSupport,
)
from research_system.runtime.methodology.core.symbol_support import (
    OrchestratorMethodologySymbolSupport,
)
from research_system.runtime.orchestration.orchestrator_context import RuntimeContext
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.runtime import BlackboardEntry


class OrchestratorMethodologySupport:
    def __init__(
        self,
        *,
        comps_service,
        dcf_service,
        cashflow_resilience_service,
    ) -> None:
        self.comps_service = comps_service
        self.dcf_service = dcf_service
        self.cashflow_resilience_service = cashflow_resilience_service
        self.snapshot_support = OrchestratorMethodologySnapshotSupport()
        self.bank_scorecard_support = OrchestratorMethodologyBankScorecardSupport()
        self.entry_factory = OrchestratorMethodologyEntryFactory()
        self.builder = OrchestratorMethodologyBuilder(
            comps_service=comps_service,
            dcf_service=dcf_service,
            cashflow_resilience_service=cashflow_resilience_service,
            snapshot_support=self.snapshot_support,
            entry_factory=self.entry_factory,
        )
        self.compare_support = OrchestratorMethodologyCompareSupport(
            builder=self.builder,
            snapshot_support=self.snapshot_support,
            bank_scorecard_support=self.bank_scorecard_support,
        )
        self.symbol_support = OrchestratorMethodologySymbolSupport(
            builder=self.builder,
            entry_factory=self.entry_factory,
        )

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
        return self.symbol_support.build_candidate_methodology_entries(
            context=context,
            request=request,
            phase=phase,
            agent=agent,
            candidate=candidate,
            source_type=source_type,
        )

    def build_stock_methodology_entries(
        self,
        *,
        context: RuntimeContext,
        request: RequestEnvelope,
        intent: str,
        phase: str,
        agent: str,
    ) -> list[BlackboardEntry]:
        source_records = [
            entry for entry in context.blackboard.list_entries()
            if entry.kind == "source_record"
        ]
        snapshots = {
            str(entry.payload.get("snapshot_type") or ""): entry.payload
            for entry in context.blackboard.list_entries()
            if entry.kind == "verified_snapshot"
        }
        compare_request_entry = next(
            (
                entry for entry in source_records
                if str((entry.payload or {}).get("source_type") or "") == "compare_request"
            ),
            None,
        )
        if compare_request_entry is not None:
            return self.compare_support.build_stock_compare_entries(
                context=context,
                request=request,
                intent=intent,
                phase=phase,
                agent=agent,
                compare_request_entry=compare_request_entry,
            )
        return self.symbol_support.build_stock_methodology_entries(
            context=context,
            request=request,
            intent=intent,
            phase=phase,
            agent=agent,
            source_records=source_records,
            snapshots=snapshots,
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
        return self.symbol_support.build_review_methodology_entries(
            context=context,
            request=request,
            intent=intent,
            phase=phase,
            agent=agent,
            review_request=review_request,
            latest_market_snapshot=latest_market_snapshot,
            latest_fundamental_snapshot=latest_fundamental_snapshot,
        )
