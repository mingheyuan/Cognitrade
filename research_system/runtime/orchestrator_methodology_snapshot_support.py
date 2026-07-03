from __future__ import annotations

from research_system.runtime.orchestrator_methodology_snapshot_candidate_support import (
    OrchestratorMethodologySnapshotCandidateSupport,
)
from research_system.runtime.orchestrator_methodology_snapshot_fields import (
    OrchestratorMethodologySnapshotFields,
)
from research_system.runtime.orchestrator_methodology_snapshot_grouping import (
    OrchestratorMethodologySnapshotGrouping,
)


class OrchestratorMethodologySnapshotSupport:
    def __init__(self) -> None:
        self.grouping = OrchestratorMethodologySnapshotGrouping()
        self.fields = OrchestratorMethodologySnapshotFields()
        self.candidate_support = OrchestratorMethodologySnapshotCandidateSupport(fields=self.fields)

    def group_snapshots_by_symbol(self, context):
        return self.grouping.group_snapshots_by_symbol(context)

    def methodology_candidate_payload(self, **kwargs) -> dict:
        return self.candidate_support.methodology_candidate_payload(**kwargs)

    def methodology_market_price(self, market_snapshot: dict | None) -> float | None:
        return self.fields.methodology_market_price(market_snapshot)

    def methodology_market_pct_change(self, market_snapshot: dict | None) -> float | None:
        return self.fields.methodology_market_pct_change(market_snapshot)

    def methodology_market_amount(self, market_snapshot: dict | None) -> float | None:
        return self.fields.methodology_market_amount(market_snapshot)

    def methodology_market_pb(self, market_snapshot: dict | None) -> float | None:
        return self.fields.methodology_market_pb(market_snapshot)

    def methodology_market_pe_ttm(self, market_snapshot: dict | None) -> float | None:
        return self.fields.methodology_market_pe_ttm(market_snapshot)

    def methodology_market_cap(self, market_snapshot: dict | None) -> float | None:
        return self.fields.methodology_market_cap(market_snapshot)

    def methodology_market_float_market_cap(self, market_snapshot: dict | None) -> float | None:
        return self.fields.methodology_market_float_market_cap(market_snapshot)

    def methodology_market_total_shares(self, market_snapshot: dict | None) -> float | None:
        return self.fields.methodology_market_total_shares(market_snapshot)

    def methodology_market_float_shares(self, market_snapshot: dict | None) -> float | None:
        return self.fields.methodology_market_float_shares(market_snapshot)

    def methodology_revenue_proxy(self, fundamental_snapshot: dict | None) -> float | None:
        return self.fields.methodology_revenue_proxy(fundamental_snapshot)

    def banking_metrics(self, fundamental_snapshot: dict | None) -> dict:
        return self.fields.banking_metrics(fundamental_snapshot)
