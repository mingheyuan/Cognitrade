from __future__ import annotations

from research_system.adapters.vendors.akshare_utils import coerce_numeric


class OrchestratorMethodologyBankHighlightSupport:
    def build_candidate_highlights(self, metric_breakdown: dict[str, float | None]) -> list[str]:
        highlights: list[str] = []
        if metric_breakdown.get("peer_roe_3y_avg") is not None:
            highlights.append(f"同业ROE={metric_breakdown['peer_roe_3y_avg']:.2%}")
        asset_quality_highlight = self.bank_asset_quality_highlight(metric_breakdown)
        if asset_quality_highlight:
            highlights.append(asset_quality_highlight)
        net_interest_spread_highlight = self.bank_net_interest_spread_highlight(metric_breakdown)
        if net_interest_spread_highlight:
            highlights.append(net_interest_spread_highlight)
        capital_buffer_highlight = self.bank_capital_buffer_highlight(metric_breakdown)
        if capital_buffer_highlight:
            highlights.append(capital_buffer_highlight)
        if metric_breakdown.get("peer_pb_mrq") is not None:
            highlights.append(f"同业PB={metric_breakdown['peer_pb_mrq']:.2f}")
        if metric_breakdown.get("roe") is not None:
            highlights.append(f"ROE={metric_breakdown['roe']:.2%}")
        if metric_breakdown.get("dividend_yield_proxy") is not None:
            highlights.append(f"股息率代理={metric_breakdown['dividend_yield_proxy']:.2%}")
        if metric_breakdown.get("peer_net_profit_growth_ttm") is not None:
            highlights.append(f"同业净利润增速={metric_breakdown['peer_net_profit_growth_ttm']:.2%}")
        if metric_breakdown.get("net_profit_yoy") is not None:
            highlights.append(f"利润增速={metric_breakdown['net_profit_yoy']:.2%}")
        if metric_breakdown.get("interest_margin_proxy") is not None:
            highlights.append(f"净利率代理={metric_breakdown['interest_margin_proxy']:.2f}")
        if metric_breakdown.get("provision_coverage_proxy") is not None:
            highlights.append(f"拨备覆盖代理={metric_breakdown['provision_coverage_proxy']:.2f}")
        if metric_breakdown.get("asset_liability_ratio") is not None:
            highlights.append(f"资产负债率={metric_breakdown['asset_liability_ratio']:.2%}")
        return highlights[:4]

    def bank_asset_quality_highlight(self, metrics: dict[str, float | None]) -> str | None:
        npl = coerce_numeric(metrics.get("nonperforming_loan_ratio"))
        provision = coerce_numeric(metrics.get("provision_coverage_ratio"))
        parts: list[str] = []
        if npl is not None:
            parts.append(f"NPL={npl:.2%}")
        if provision is not None:
            parts.append(f"拨备覆盖={provision:.2%}")
        if not parts:
            return None
        return "资产质量=" + "/".join(parts[:2])

    def bank_capital_buffer_highlight(self, metrics: dict[str, float | None]) -> str | None:
        capital = coerce_numeric(metrics.get("capital_adequacy_ratio"))
        core_tier1 = coerce_numeric(metrics.get("core_tier1_capital_adequacy_ratio"))
        parts: list[str] = []
        if capital is not None:
            parts.append(f"资本充足率={capital:.2%}")
        if core_tier1 is not None:
            parts.append(f"核心一级={core_tier1:.2%}")
        if not parts:
            return None
        return "资本缓冲=" + "/".join(parts[:2])

    def bank_net_interest_spread_highlight(self, metrics: dict[str, float | None]) -> str | None:
        net_interest_spread = coerce_numeric(metrics.get("net_interest_spread"))
        if net_interest_spread is None:
            return None
        return f"净息差={net_interest_spread:.2%}"
