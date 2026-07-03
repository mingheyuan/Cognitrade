from __future__ import annotations

from typing import Any

from research_system.adapters.vendors.akshare_utils import coerce_numeric
from research_system.schemas.valuation import CompsModelResult, ValuationBand, ValuationPeer


class CompsService:
    methodology_type = "comps"

    def build_for_candidate(
        self,
        *,
        candidate: dict[str, Any],
        peers: list[dict[str, Any]],
        cycle: str,
        sector_context: dict | None = None,
    ) -> dict[str, Any]:
        target_symbol = str(candidate.get("symbol") or "").strip() or "unknown"
        target_revenue = self._to_float(candidate.get("revenue_proxy"))
        warnings: list[str] = []
        peer_models: list[ValuationPeer] = []

        ranked = sorted(
            peers,
            key=lambda item: (
                self._peer_similarity_score(candidate=candidate, peer=item, cycle=cycle),
                self._to_float(item.get("latest_amount")) or 0.0,
            ),
            reverse=True,
        )
        selected_peers = ranked[:5]
        if not selected_peers:
            warnings.append("peer_group_missing")

        peer_scores = []
        peer_revenues = []
        target_score = self._to_float(candidate.get("score")) or 0.0
        for peer in selected_peers:
            peer_score = self._peer_similarity_score(candidate=candidate, peer=peer, cycle=cycle)
            peer_revenue = self._to_float(peer.get("revenue_proxy"))
            peer_scores.append(peer_score)
            if peer_revenue is not None:
                peer_revenues.append(peer_revenue)
            peer_models.append(
                ValuationPeer(
                    symbol=str(peer.get("symbol") or ""),
                    name=str(peer.get("name") or "") or None,
                    sector=str(peer.get("sector") or "") or None,
                    theme=str(peer.get("theme") or "") or None,
                    latest_price=self._to_float(peer.get("latest_price")),
                    latest_pct_change=self._to_float(peer.get("latest_pct_change")),
                    latest_amount=self._to_float(peer.get("latest_amount")),
                    revenue_proxy=peer_revenue,
                    peer_score=round(peer_score, 4),
                    percentile=self._relative_percentile(
                        value=self._to_float(peer.get("latest_amount")),
                        series=[self._to_float(item.get("latest_amount")) for item in peers],
                    ),
                    notes=self._peer_notes(candidate=candidate, peer=peer),
                )
            )

        median_peer_score = self._median(peer_scores)
        score_gap = round(target_score - median_peer_score, 4) if median_peer_score is not None else None
        target_amount = self._to_float(candidate.get("latest_amount"))
        liquidity_percentile = self._relative_percentile(
            value=target_amount,
            series=[self._to_float(item.get("latest_amount")) for item in peers] + [target_amount],
        )
        revenue_percentile = self._relative_percentile(
            value=target_revenue,
            series=peer_revenues + ([target_revenue] if target_revenue is not None else []),
        )

        if liquidity_percentile is None:
            warnings.append("liquidity_percentile_missing")
        if target_revenue is None:
            warnings.append("revenue_proxy_missing")
        elif revenue_percentile is None:
            warnings.append("revenue_percentile_missing")

        valuation_band = self._valuation_band(
            latest_price=self._to_float(candidate.get("latest_price")),
            score_gap=score_gap,
            revenue_percentile=revenue_percentile,
        )
        confidence = self._confidence(peer_count=len(peer_models), warnings=warnings, revenue_available=target_revenue is not None)

        result = CompsModelResult(
            target_symbol=target_symbol,
            inputs={
                "cycle": cycle,
                "sector": candidate.get("sector") or (sector_context or {}).get("sector"),
                "theme": candidate.get("theme") or (sector_context or {}).get("theme"),
                "latest_price": self._to_float(candidate.get("latest_price")),
                "latest_amount": target_amount,
                "revenue_proxy": target_revenue,
            },
            assumptions={
                "peer_selection_basis": ["sector", "theme", "liquidity", "cycle"],
                "peer_count": len(peer_models),
                "median_peer_score": median_peer_score,
            },
            outputs={
                "score_gap_vs_peer_median": score_gap,
                "liquidity_percentile": liquidity_percentile,
                "revenue_percentile": revenue_percentile,
                "relative_view": self._relative_view(score_gap=score_gap, revenue_percentile=revenue_percentile),
            },
            sensitivity={
                "score_gap_bands": {
                    "cheap_like": score_gap is not None and score_gap >= 0.75,
                    "neutral_like": score_gap is not None and -0.25 <= score_gap < 0.75,
                    "expensive_like": score_gap is not None and score_gap < -0.25,
                }
            },
            warnings=warnings,
            evidence_refs=[{"type": "candidate_screening", "symbol": target_symbol}],
            confidence=confidence,
            peer_group=peer_models,
            peer_group_tags=self._peer_group_tags(candidate=candidate, sector_context=sector_context),
            assumption_tags=["relative_valuation", cycle],
            sensitivity_tags=["score_gap", "liquidity_percentile", "revenue_percentile"],
            valuation_band=valuation_band,
        )
        return result.model_dump(mode="json")

    def _peer_similarity_score(self, *, candidate: dict[str, Any], peer: dict[str, Any], cycle: str) -> float:
        score = 0.0
        if str(candidate.get("sector") or "") and str(candidate.get("sector") or "") == str(peer.get("sector") or ""):
            score += 1.0
        if str(candidate.get("theme") or "") and str(candidate.get("theme") or "") == str(peer.get("theme") or ""):
            score += 0.75
        candidate_amount = self._to_float(candidate.get("latest_amount"))
        peer_amount = self._to_float(peer.get("latest_amount"))
        if candidate_amount is not None and peer_amount is not None:
            ratio = min(candidate_amount, peer_amount) / max(candidate_amount, peer_amount) if max(candidate_amount, peer_amount) > 0 else 0.0
            score += ratio
        if cycle == "long_term":
            candidate_revenue = self._to_float(candidate.get("revenue_proxy"))
            peer_revenue = self._to_float(peer.get("revenue_proxy"))
            if candidate_revenue is not None and peer_revenue is not None:
                ratio = min(candidate_revenue, peer_revenue) / max(candidate_revenue, peer_revenue) if max(candidate_revenue, peer_revenue) > 0 else 0.0
                score += ratio
        return score

    def _peer_notes(self, *, candidate: dict[str, Any], peer: dict[str, Any]) -> list[str]:
        notes: list[str] = []
        if str(candidate.get("sector") or "") == str(peer.get("sector") or ""):
            notes.append("same_sector")
        if str(candidate.get("theme") or "") == str(peer.get("theme") or ""):
            notes.append("same_theme")
        candidate_amount = self._to_float(candidate.get("latest_amount"))
        peer_amount = self._to_float(peer.get("latest_amount"))
        if candidate_amount is not None and peer_amount is not None:
            if peer_amount > candidate_amount * 1.5:
                notes.append("peer_more_liquid")
            elif peer_amount < candidate_amount * 0.67:
                notes.append("peer_less_liquid")
        return notes

    def _relative_view(self, *, score_gap: float | None, revenue_percentile: float | None) -> str:
        if score_gap is None:
            return "insufficient_peer_signal"
        if score_gap >= 0.75:
            return "relative_discount_like"
        if score_gap <= -0.25:
            return "relative_premium_like"
        if revenue_percentile is not None and revenue_percentile >= 0.7:
            return "growth_supported_neutral"
        return "relative_fair_like"

    def _valuation_band(self, *, latest_price: float | None, score_gap: float | None, revenue_percentile: float | None) -> ValuationBand:
        if latest_price is None:
            return ValuationBand()
        premium = 0.0
        if score_gap is not None:
            premium += max(min(score_gap * 0.08, 0.2), -0.15)
        if revenue_percentile is not None:
            premium += (revenue_percentile - 0.5) * 0.08
        base = latest_price * (1.0 + premium)
        return ValuationBand(
            low=round(base * 0.9, 4),
            base=round(base, 4),
            high=round(base * 1.1, 4),
        )

    def _peer_group_tags(self, *, candidate: dict[str, Any], sector_context: dict | None) -> list[str]:
        tags: list[str] = []
        for value in (
            candidate.get("sector"),
            candidate.get("theme"),
            (sector_context or {}).get("sector"),
            (sector_context or {}).get("theme"),
        ):
            text = str(value or "").strip()
            if text and text not in tags:
                tags.append(text)
        if not tags:
            tags.append("broad_market")
        return tags

    def _confidence(self, *, peer_count: int, warnings: list[str], revenue_available: bool) -> float:
        confidence = 0.35
        confidence += min(peer_count, 5) * 0.08
        if revenue_available:
            confidence += 0.1
        confidence -= min(len(warnings), 4) * 0.06
        return round(max(min(confidence, 0.9), 0.15), 4)

    def _median(self, values: list[float]) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        mid = len(ordered) // 2
        if len(ordered) % 2 == 1:
            return round(ordered[mid], 4)
        return round((ordered[mid - 1] + ordered[mid]) / 2.0, 4)

    def _relative_percentile(self, *, value: float | None, series: list[float | None]) -> float | None:
        if value is None:
            return None
        normalized = sorted(item for item in series if item is not None)
        if not normalized:
            return None
        below = sum(1 for item in normalized if item <= value)
        return round(below / len(normalized), 4)

    def _to_float(self, value: Any) -> float | None:
        return coerce_numeric(value)
