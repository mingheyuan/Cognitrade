from __future__ import annotations

from typing import Any

from research_system.adapters.vendors.akshare_utils import coerce_numeric
from research_system.schemas.valuation import DCFModelResult, ValuationBand


class DCFService:
    methodology_type = "dcf"

    def build_for_stock(
        self,
        *,
        symbol: str,
        fundamental_snapshot: dict | None,
        market_snapshot: dict | None,
        cycle: str,
    ) -> dict[str, Any]:
        market_price = self._latest_price(market_snapshot)
        revenue = self._extract_metric(
            fundamental_snapshot,
            "营业总收入",
            "营业收入",
            "营收",
            "总营收",
        )
        net_profit = self._extract_metric(
            fundamental_snapshot,
            "归母净利润",
            "净利润",
            "扣非净利润",
        )
        total_assets = self._extract_info_metric(
            fundamental_snapshot,
            "总资产",
            "资产总计",
        )
        total_liabilities = self._extract_info_metric(
            fundamental_snapshot,
            "总负债",
            "负债合计",
        )

        warnings: list[str] = []
        if revenue is None:
            warnings.append("revenue_missing")
        if net_profit is None:
            warnings.append("net_profit_missing")
        if total_assets is None:
            warnings.append("total_assets_missing")
        if market_price is None:
            warnings.append("market_price_missing")

        normalized_revenue = revenue / 100000000 if revenue is not None and revenue > 1000000 else revenue
        normalized_profit = net_profit / 100000000 if net_profit is not None and net_profit > 1000000 else net_profit
        normalized_assets = total_assets / 100000000 if total_assets is not None and total_assets > 1000000 else total_assets
        normalized_liabilities = total_liabilities / 100000000 if total_liabilities is not None and total_liabilities > 1000000 else total_liabilities

        base_growth = 0.08 if cycle == "long_term" else 0.05
        margin = None
        if normalized_revenue not in (None, 0) and normalized_profit is not None:
            margin = normalized_profit / normalized_revenue
        if margin is None:
            margin = 0.1
            warnings.append("margin_assumed")

        wacc = 0.11 if cycle == "long_term" else 0.12
        terminal_growth = 0.03 if cycle == "long_term" else 0.02
        capex_ratio = 0.04 if normalized_assets is not None else 0.05
        working_capital_ratio = 0.02
        years = 5

        base_fcf = None
        if normalized_revenue is not None:
            base_fcf = normalized_revenue * margin * (1.0 - capex_ratio - working_capital_ratio)
        if base_fcf is None:
            warnings.append("fcf_proxy_missing")

        present_value = None
        terminal_value = None
        intrinsic_value = None
        if base_fcf is not None:
            cash_flows: list[float] = []
            current_fcf = base_fcf
            for _ in range(years):
                current_fcf = current_fcf * (1.0 + base_growth)
                cash_flows.append(current_fcf)
            present_value = sum(value / ((1.0 + wacc) ** index) for index, value in enumerate(cash_flows, start=1))
            if wacc > terminal_growth:
                terminal_value = (cash_flows[-1] * (1.0 + terminal_growth)) / (wacc - terminal_growth)
                terminal_value = terminal_value / ((1.0 + wacc) ** years)
            debt_adjustment = normalized_liabilities or 0.0
            intrinsic_value = (present_value or 0.0) + (terminal_value or 0.0) - debt_adjustment * 0.1

        valuation_band = self._valuation_band(
            market_price=market_price,
            intrinsic_value=intrinsic_value,
            confidence_inputs_ready=not warnings,
        )
        confidence = self._confidence(warnings=warnings, data_points=[normalized_revenue, normalized_profit, market_price])

        result = DCFModelResult(
            target_symbol=symbol,
            inputs={
                "cycle": cycle,
                "latest_price": market_price,
                "revenue_proxy": normalized_revenue,
                "net_profit_proxy": normalized_profit,
                "total_assets_proxy": normalized_assets,
                "total_liabilities_proxy": normalized_liabilities,
            },
            assumptions={
                "base_growth": base_growth,
                "margin": round(margin, 4) if margin is not None else None,
                "wacc": wacc,
                "terminal_growth": terminal_growth,
                "capex_ratio": capex_ratio,
                "working_capital_ratio": working_capital_ratio,
                "projection_years": years,
            },
            outputs={
                "base_fcf_proxy": round(base_fcf, 4) if base_fcf is not None else None,
                "present_value": round(present_value, 4) if present_value is not None else None,
                "terminal_value": round(terminal_value, 4) if terminal_value is not None else None,
                "intrinsic_value_proxy": round(intrinsic_value, 4) if intrinsic_value is not None else None,
                "market_to_intrinsic_gap": round(((market_price - intrinsic_value) / intrinsic_value), 4)
                if market_price is not None and intrinsic_value not in (None, 0)
                else None,
            },
            sensitivity={
                "wacc_terminal_growth_grid": {
                    "wacc_minus_100bps": round((intrinsic_value or 0.0) * 1.12, 4) if intrinsic_value is not None else None,
                    "base_case": round(intrinsic_value, 4) if intrinsic_value is not None else None,
                    "wacc_plus_100bps": round((intrinsic_value or 0.0) * 0.9, 4) if intrinsic_value is not None else None,
                }
            },
            warnings=warnings,
            evidence_refs=[{"type": "stock_snapshot", "symbol": symbol}],
            confidence=confidence,
            assumption_tags=["light_dcf", cycle, "assumption_driven"],
            sensitivity_tags=["wacc", "terminal_growth", "margin", "growth"],
            valuation_band=valuation_band,
        )
        return result.model_dump(mode="json")

    def _valuation_band(self, *, market_price: float | None, intrinsic_value: float | None, confidence_inputs_ready: bool) -> ValuationBand:
        if market_price is None and intrinsic_value is None:
            return ValuationBand()
        base = intrinsic_value if intrinsic_value is not None else market_price
        if base is None:
            return ValuationBand()
        spread = 0.12 if confidence_inputs_ready else 0.2
        return ValuationBand(
            low=round(base * (1.0 - spread), 4),
            base=round(base, 4),
            high=round(base * (1.0 + spread), 4),
        )

    def _confidence(self, *, warnings: list[str], data_points: list[float | None]) -> float:
        present_count = sum(1 for item in data_points if item is not None)
        confidence = 0.2 + present_count * 0.15 - min(len(warnings), 4) * 0.05
        return round(max(min(confidence, 0.82), 0.1), 4)

    def _latest_price(self, market_snapshot: dict | None) -> float | None:
        facts = (market_snapshot or {}).get("facts") or {}
        overview = facts.get("market_overview") or {}
        spot = overview.get("spot") or overview
        for key in ("latest_price", "最新价", "鏈€鏂颁环", "閺堚偓閺傞鐜?"):
            value = self._to_float(spot.get(key))
            if value is not None:
                return value
        latest_bar = (facts.get("market_hist") or {}).get("latest_bar") or {}
        for key in ("close", "收盘", "收盘价"):
            value = self._to_float(latest_bar.get(key))
            if value is not None:
                return value
        return None

    def _extract_metric(self, snapshot: dict | None, *keys: str) -> float | None:
        facts = (snapshot or {}).get("facts") or {}
        abstract = facts.get("financial_abstract") or {}
        for key in keys:
            value = self._to_float(abstract.get(key))
            if value is not None:
                return value
        for item_key, item_value in abstract.items():
            key_text = str(item_key)
            if any(token in key_text for token in keys):
                value = self._to_float(item_value)
                if value is not None:
                    return value
        return None

    def _extract_info_metric(self, snapshot: dict | None, *keys: str) -> float | None:
        facts = (snapshot or {}).get("facts") or {}
        info = facts.get("info") or {}
        for key in keys:
            value = self._to_float(info.get(key))
            if value is not None:
                return value
        for item_key, item_value in info.items():
            key_text = str(item_key)
            if any(token in key_text for token in keys):
                value = self._to_float(item_value)
                if value is not None:
                    return value
        return None

    def _to_float(self, value: Any) -> float | None:
        return coerce_numeric(value)
