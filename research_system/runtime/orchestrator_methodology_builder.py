from __future__ import annotations


class OrchestratorMethodologyBuilder:
    def __init__(
        self,
        *,
        comps_service,
        dcf_service,
        cashflow_resilience_service,
        snapshot_support,
        entry_factory,
    ) -> None:
        self.comps_service = comps_service
        self.dcf_service = dcf_service
        self.cashflow_resilience_service = cashflow_resilience_service
        self.snapshot_support = snapshot_support
        self.entry_factory = entry_factory

    def infer_investment_cycle(self, query: str) -> str:
        lowered = query.lower()
        if "閻厾鍤?" in query or "short" in lowered:
            return "short_term"
        if "闂€鎸庢埂" in query or "long" in lowered:
            return "long_term"
        return "mid_term"

    def build_entries_for_symbol(
        self,
        *,
        context,
        request,
        phase: str,
        agent: str,
        intent: str,
        symbol: str,
        candidate_name: str,
        sector,
        cycle: str,
        market_snapshot: dict | None,
        fundamental_snapshot: dict | None,
        peers: list[dict],
    ) -> list:
        comps_payload = self.comps_service.build_for_candidate(
            candidate={
                "symbol": symbol,
                "name": candidate_name,
                "sector": sector,
                "latest_price": self.snapshot_support.methodology_market_price(market_snapshot),
                "latest_pct_change": self.snapshot_support.methodology_market_pct_change(market_snapshot),
                "latest_amount": self.snapshot_support.methodology_market_amount(market_snapshot),
                "pb": self.snapshot_support.methodology_market_pb(market_snapshot),
                "pe_ttm": self.snapshot_support.methodology_market_pe_ttm(market_snapshot),
                "market_cap": self.snapshot_support.methodology_market_cap(market_snapshot),
                "float_market_cap": self.snapshot_support.methodology_market_float_market_cap(market_snapshot),
                "revenue_proxy": self.snapshot_support.methodology_revenue_proxy(fundamental_snapshot),
                "score": 0.0,
            },
            peers=peers,
            cycle=cycle,
            sector_context={"sector": sector},
        )
        dcf_payload = self.dcf_service.build_for_stock(
            symbol=symbol,
            fundamental_snapshot=fundamental_snapshot,
            market_snapshot=market_snapshot,
            cycle=cycle,
        )
        resilience_payload = self.cashflow_resilience_service.build_for_stock(
            symbol=symbol,
            fundamental_snapshot=fundamental_snapshot,
            market_snapshot=market_snapshot,
            cycle=cycle,
        )
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
                source_refs=[{"type": f"{intent}_methodology", "run_id": context.run_id}],
            ),
            self.entry_factory.methodology_entry(
                context=context,
                request=request,
                phase=phase,
                agent=agent,
                methodology_type="dcf",
                title=f"methodology_dcf:{symbol}",
                payload=dcf_payload,
                target_symbol=symbol,
                source_refs=[{"type": f"{intent}_methodology", "run_id": context.run_id}],
            ),
            self.entry_factory.methodology_entry(
                context=context,
                request=request,
                phase=phase,
                agent=agent,
                methodology_type="cashflow_resilience",
                title=f"methodology_cashflow_resilience:{symbol}",
                payload=resilience_payload,
                target_symbol=symbol,
                source_refs=[{"type": f"{intent}_methodology", "run_id": context.run_id}],
            ),
        ]

    def build_review_entries(
        self,
        *,
        context,
        request,
        intent: str,
        phase: str,
        agent: str,
        symbol: str,
        cycle: str,
        latest_market_snapshot: dict | None,
        latest_fundamental_snapshot: dict | None,
    ) -> list:
        dcf_payload = self.dcf_service.build_for_stock(
            symbol=symbol,
            fundamental_snapshot=latest_fundamental_snapshot,
            market_snapshot=latest_market_snapshot,
            cycle=cycle,
        )
        resilience_payload = self.cashflow_resilience_service.build_for_stock(
            symbol=symbol,
            fundamental_snapshot=latest_fundamental_snapshot,
            market_snapshot=latest_market_snapshot,
            cycle=cycle,
        )
        return [
            self.entry_factory.methodology_entry(
                context=context,
                request=request,
                phase=phase,
                agent=agent,
                methodology_type="dcf",
                title=f"methodology_dcf:{symbol}",
                payload=dcf_payload,
                target_symbol=symbol,
                source_refs=[{"type": f"{intent}_review_methodology", "run_id": context.run_id}],
            ),
            self.entry_factory.methodology_entry(
                context=context,
                request=request,
                phase=phase,
                agent=agent,
                methodology_type="cashflow_resilience",
                title=f"methodology_cashflow_resilience:{symbol}",
                payload=resilience_payload,
                target_symbol=symbol,
                source_refs=[{"type": f"{intent}_review_methodology", "run_id": context.run_id}],
            ),
        ]
