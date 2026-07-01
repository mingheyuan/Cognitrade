from __future__ import annotations

from typing import Any

from research_system.schemas.valuation import CashflowResilienceResult, ValuationBand


class CashflowResilienceService:
    methodology_type = "cashflow_resilience"

    def build_for_stock(
        self,
        *,
        symbol: str,
        fundamental_snapshot: dict | None,
        market_snapshot: dict | None,
        cycle: str,
    ) -> dict[str, Any]:
        revenue = self._extract_metric(
            fundamental_snapshot,
            "营业总收入",
            "营业收入",
            "营收",
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
        latest_price = self._latest_price(market_snapshot)

        warnings: list[str] = []
        if revenue is None:
            warnings.append("revenue_missing")
        if net_profit is None:
            warnings.append("net_profit_missing")
        if total_assets is None:
            warnings.append("total_assets_missing")
        if total_liabilities is None:
            warnings.append("total_liabilities_missing")

        normalized_revenue = revenue / 100000000 if revenue is not None and revenue > 1000000 else revenue
        normalized_profit = net_profit / 100000000 if net_profit is not None and net_profit > 1000000 else net_profit
        normalized_assets = total_assets / 100000000 if total_assets is not None and total_assets > 1000000 else total_assets
        normalized_liabilities = total_liabilities / 100000000 if total_liabilities is not None and total_liabilities > 1000000 else total_liabilities

        margin = None
        leverage_ratio = None
        if normalized_revenue not in (None, 0) and normalized_profit is not None:
            margin = normalized_profit / normalized_revenue
        if normalized_assets not in (None, 0) and normalized_liabilities is not None:
            leverage_ratio = normalized_liabilities / normalized_assets

        if margin is None:
            margin = 0.08
            warnings.append("margin_assumed")
        if leverage_ratio is None:
            leverage_ratio = 0.45
            warnings.append("leverage_assumed")

        stress_revenue_drop = 0.12 if cycle == "long_term" else 0.08
        stressed_margin = max(margin - 0.03, -0.02)
        resilience_score = max(0.0, 1.0 - stress_revenue_drop - max(leverage_ratio - 0.4, 0.0))
        debt_capacity_state = "comfortable" if leverage_ratio <= 0.45 else "watch" if leverage_ratio <= 0.65 else "stressed"
        cashflow_state = "resilient" if stressed_margin > 0.08 else "mixed" if stressed_margin > 0.02 else "fragile"

        warnings.extend(
            note for note, active in (
                ("debt_capacity_watch", debt_capacity_state == "watch"),
                ("debt_capacity_stressed", debt_capacity_state == "stressed"),
                ("cashflow_fragile", cashflow_state == "fragile"),
            )
            if active and note not in warnings
        )

        result = CashflowResilienceResult(
            target_symbol=symbol,
            inputs={
                "cycle": cycle,
                "latest_price": latest_price,
                "revenue_proxy": normalized_revenue,
                "net_profit_proxy": normalized_profit,
                "total_assets_proxy": normalized_assets,
                "total_liabilities_proxy": normalized_liabilities,
            },
            assumptions={
                "stress_revenue_drop": stress_revenue_drop,
                "stressed_margin_delta": -0.03,
                "baseline_margin": round(margin, 4),
                "baseline_leverage_ratio": round(leverage_ratio, 4),
            },
            outputs={
                "cashflow_state": cashflow_state,
                "debt_capacity_state": debt_capacity_state,
                "resilience_score": round(resilience_score, 4),
                "stressed_margin": round(stressed_margin, 4),
            },
            sensitivity={
                "stress_cases": {
                    "mild": round(max(0.0, resilience_score + 0.08), 4),
                    "base": round(resilience_score, 4),
                    "severe": round(max(0.0, resilience_score - 0.12), 4),
                }
            },
            warnings=warnings,
            evidence_refs=[{"type": "stock_snapshot", "symbol": symbol}],
            confidence=self._confidence(warnings=warnings, inputs=[normalized_revenue, normalized_profit, normalized_assets, normalized_liabilities]),
            assumption_tags=["resilience", cycle, "stress_test"],
            sensitivity_tags=["revenue_drop", "margin", "leverage"],
            valuation_band=ValuationBand(base=latest_price),
        )
        return result.model_dump(mode="json")

    def _confidence(self, *, warnings: list[str], inputs: list[float | None]) -> float:
        present = sum(1 for item in inputs if item is not None)
        confidence = 0.22 + present * 0.12 - min(len(warnings), 5) * 0.04
        return round(max(min(confidence, 0.8), 0.12), 4)

    def _latest_price(self, market_snapshot: dict | None) -> float | None:
        facts = (market_snapshot or {}).get("facts") or {}
        overview = facts.get("market_overview") or {}
        spot = overview.get("spot") or overview
        for key in ("latest_price", "最新价", "鏈€鏂颁环", "閺堚偓閺傞鐜?"):
            value = self._to_float(spot.get(key))
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
        if value in (None, "", False):
            return None
        try:
            return float(value)
        except Exception:
            return None
