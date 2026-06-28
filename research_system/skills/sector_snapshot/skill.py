from __future__ import annotations

from datetime import date
from typing import Any

from research_system.adapters.vendors.akshare_catalyst_adapter import AkShareCatalystAdapter
from research_system.adapters.vendors.akshare_sector_adapter import AkShareSectorAdapter
from research_system.schemas.snapshots import SnapshotSource, VerifiedSnapshot


class SectorSnapshotSkill:
    skill_name = "sector_snapshot_skill"

    def __init__(self) -> None:
        self.adapter = AkShareSectorAdapter()
        self.catalyst_adapter = AkShareCatalystAdapter()

    def run(self, *, query: str) -> dict[str, Any]:
        snapshot_date = date.today().isoformat()
        try:
            resolved = self.adapter.resolve_sector(query=query)
            facts = self.adapter.get_sector_snapshot(
                query=query,
                sector_name=resolved["sector_name"],
                sector_type=resolved["sector_type"],
            )
            benchmark = self.adapter.get_benchmark_snapshot(
                benchmark_code="000300",
                benchmark_name="HS300",
                start_date=facts["start_date"],
                end_date=facts["end_date"],
            )
            facts["benchmark"] = benchmark
            facts["relative_strength"] = self.adapter.build_relative_strength(
                sector_recent_hist=facts.get("recent_hist", []),
                benchmark_recent_hist=benchmark.get("recent_hist", []),
            )
            facts["catalyst_samples"] = self._build_catalyst_samples(facts.get("top_constituents", []))
            facts["theme_news"] = self.catalyst_adapter.get_theme_news(
                theme=resolved["sector_name"],
                constituents=facts.get("top_constituents", []),
                limit=6,
            )
            facts["policy_signals"] = self.catalyst_adapter.get_policy_signals(
                theme=resolved["sector_name"],
                constituents=facts.get("top_constituents", []),
                limit=5,
            )
            facts["regulatory_signals"] = self.catalyst_adapter.get_regulatory_signals(
                theme=resolved["sector_name"],
                constituents=facts.get("top_constituents", []),
                limit=6,
            )
            facts["breadth"] = self._build_breadth_metrics(facts=facts)
            facts["signal_scorecard"] = self._build_signal_scorecard(
                facts=facts,
                theme=resolved["sector_name"],
            )
            facts["catalyst_digest"] = self.catalyst_adapter.build_catalyst_digest(
                theme=resolved["sector_name"],
                constituents=facts.get("top_constituents", []),
                catalyst_samples=facts.get("catalyst_samples", []),
                theme_news=facts.get("theme_news", []),
                policy_signals=facts.get("policy_signals", []),
                regulatory_signals=facts.get("regulatory_signals", []),
            )
            return VerifiedSnapshot(
                snapshot_type="sector",
                query=query,
                snapshot_date=snapshot_date,
                sector=resolved["sector_name"],
                theme=resolved["sector_type"],
                facts=facts,
                source=SnapshotSource(skill=self.skill_name, vendor=self.adapter.vendor_name, from_cache=False),
            ).model_dump(mode="json")
        except Exception as exc:
            return VerifiedSnapshot(
                snapshot_type="sector",
                query=query,
                snapshot_date=snapshot_date,
                facts={"availability": "degraded", "note": str(exc)},
                source=SnapshotSource(skill=self.skill_name, vendor=self.adapter.vendor_name, from_cache=False),
                degraded=True,
                degrade_reason="vendor_failure",
            ).model_dump(mode="json")

    def _build_catalyst_samples(self, constituents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        samples: list[dict[str, Any]] = []
        for item in constituents[:3]:
            symbol = str(item.get("code") or "").strip()
            name = str(item.get("name") or symbol).strip()
            if not symbol:
                continue
            sample = {"symbol": symbol, "name": name}
            try:
                sample["recent_news"] = self.catalyst_adapter.get_symbol_news(symbol=symbol, limit=2)
            except Exception as exc:
                sample["recent_news"] = [{"availability": "degraded", "note": str(exc)}]
            try:
                sample["recent_reports"] = self.catalyst_adapter.get_symbol_research_reports(symbol=symbol, limit=2)
            except Exception as exc:
                sample["recent_reports"] = [{"availability": "degraded", "note": str(exc)}]
            samples.append(sample)
        return samples

    def _build_signal_scorecard(self, *, facts: dict[str, Any], theme: str) -> dict[str, Any]:
        latest_hist = facts.get("latest_hist") or {}
        relative_strength = facts.get("relative_strength") or {}
        policy_signals = facts.get("policy_signals") or []
        regulatory_signals = facts.get("regulatory_signals") or []
        catalyst_samples = facts.get("catalyst_samples") or []
        breadth = facts.get("breadth") or {}

        latest_pct_change = self._to_float(latest_hist.get("pct_change"))
        latest_turnover_rate = self._to_float(latest_hist.get("turnover_rate"))
        latest_excess = self._to_float(relative_strength.get("latest_excess_pct_change"))

        market_tone = "neutral"
        if latest_pct_change is not None:
            if latest_pct_change >= 2.0:
                market_tone = "strong"
            elif latest_pct_change <= -2.0:
                market_tone = "weak"

        benchmark_view = "neutral"
        if latest_excess is not None:
            if latest_excess >= 1.0:
                benchmark_view = "outperform"
            elif latest_excess <= -1.0:
                benchmark_view = "underperform"

        turnover_state = "normal"
        if latest_turnover_rate is not None:
            if latest_turnover_rate >= 2.5:
                turnover_state = "active"
            elif latest_turnover_rate <= 1.0:
                turnover_state = "cold"

        breadth_state = self._derive_breadth_state(breadth=breadth)

        policy_strength = self._signal_strength(
            values=[self._to_float(item.get("policy_priority")) for item in policy_signals],
            strong_threshold=4.0,
        )
        regulatory_strength = self._signal_strength(
            values=[self._to_float(item.get("signal_priority")) for item in regulatory_signals],
            strong_threshold=4.0,
        )

        sample_strength = "present" if catalyst_samples else "missing"
        setup_label = self._derive_setup_label(
            market_tone=market_tone,
            benchmark_view=benchmark_view,
            breadth_state=breadth_state,
            policy_strength=policy_strength,
            regulatory_strength=regulatory_strength,
        )

        return {
            "theme": theme,
            "market_tone": market_tone,
            "benchmark_view": benchmark_view,
            "turnover_state": turnover_state,
            "breadth_state": breadth_state,
            "policy_signal_strength": policy_strength,
            "regulatory_signal_strength": regulatory_strength,
            "sample_signal_strength": sample_strength,
            "setup_label": setup_label,
            "headline_counts": {
                "theme_news": len(facts.get("theme_news") or []),
                "policy_signals": len(policy_signals),
                "regulatory_signals": len(regulatory_signals),
                "catalyst_samples": len(catalyst_samples),
            },
            "breadth": breadth,
            "summary": self._build_scorecard_summary(
                market_tone=market_tone,
                benchmark_view=benchmark_view,
                turnover_state=turnover_state,
                breadth_state=breadth_state,
                policy_strength=policy_strength,
                regulatory_strength=regulatory_strength,
                setup_label=setup_label,
            ),
        }

    def _signal_strength(self, *, values: list[float | None], strong_threshold: float) -> str:
        normalized = [value for value in values if value is not None]
        if not normalized:
            return "missing"
        avg_value = sum(normalized) / len(normalized)
        if avg_value >= strong_threshold:
            return "strong"
        if avg_value >= strong_threshold - 1.0:
            return "moderate"
        return "weak"

    def _derive_setup_label(
        self,
        *,
        market_tone: str,
        benchmark_view: str,
        breadth_state: str,
        policy_strength: str,
        regulatory_strength: str,
    ) -> str:
        catalyst_live = policy_strength in {"strong", "moderate"} or regulatory_strength in {"strong", "moderate"}
        if market_tone == "weak" and breadth_state in {"broad_weak", "narrow_weak"} and catalyst_live:
            return "weak_tape_but_catalysts_alive"
        if market_tone == "weak" and breadth_state == "broad_strong" and catalyst_live:
            return "index_weak_but_breadth_resilient"
        if market_tone == "strong" and benchmark_view == "outperform":
            return "momentum_expansion"
        if market_tone == "strong" and breadth_state == "broad_strong":
            return "breadth_confirmed_strength"
        if benchmark_view == "underperform" and not catalyst_live:
            return "weak_without_support"
        return "mixed_setup"

    def _build_scorecard_summary(
        self,
        *,
        market_tone: str,
        benchmark_view: str,
        turnover_state: str,
        breadth_state: str,
        policy_strength: str,
        regulatory_strength: str,
        setup_label: str,
    ) -> str:
        return (
            f"setup={setup_label}; market_tone={market_tone}; benchmark_view={benchmark_view}; "
            f"turnover_state={turnover_state}; breadth_state={breadth_state}; "
            f"policy_signal_strength={policy_strength}; "
            f"regulatory_signal_strength={regulatory_strength}"
        )

    def _build_breadth_metrics(self, *, facts: dict[str, Any]) -> dict[str, Any]:
        constituents = facts.get("constituent_breadth_source") or facts.get("top_constituents") or []
        pct_changes = []
        turnover_rates = []
        positive = 0
        negative = 0
        flat = 0

        ranked_constituents = []
        for item in constituents:
            pct_change = self._to_float(item.get("pct_change"))
            turnover_rate = self._to_float(item.get("turnover_rate"))
            if pct_change is not None:
                pct_changes.append(pct_change)
                if pct_change > 0:
                    positive += 1
                elif pct_change < 0:
                    negative += 1
                else:
                    flat += 1
            if turnover_rate is not None:
                turnover_rates.append(turnover_rate)
            ranked_constituents.append(
                {
                    "code": item.get("code"),
                    "name": item.get("name"),
                    "pct_change": pct_change,
                    "turnover_rate": turnover_rate,
                }
            )

        ranked_constituents = [item for item in ranked_constituents if item.get("pct_change") is not None]
        ranked_constituents.sort(key=lambda item: item.get("pct_change") or 0.0, reverse=True)

        breadth_count = len(ranked_constituents)
        up_ratio = round(positive / breadth_count, 4) if breadth_count else None
        down_ratio = round(negative / breadth_count, 4) if breadth_count else None

        return {
            "sample_size": breadth_count,
            "up_count": positive,
            "down_count": negative,
            "flat_count": flat,
            "up_ratio": up_ratio,
            "down_ratio": down_ratio,
            "median_pct_change": self._median(pct_changes),
            "mean_pct_change": self._mean(pct_changes),
            "average_turnover_rate": self._mean(turnover_rates),
            "top3_average_pct_change": self._mean([item.get("pct_change") for item in ranked_constituents[:3]]),
            "bottom3_average_pct_change": self._mean([item.get("pct_change") for item in ranked_constituents[-3:]]),
            "best_constituents": ranked_constituents[:3],
            "worst_constituents": list(reversed(ranked_constituents[-3:])),
        }

    def _derive_breadth_state(self, *, breadth: dict[str, Any]) -> str:
        up_ratio = self._to_float(breadth.get("up_ratio"))
        median_pct_change = self._to_float(breadth.get("median_pct_change"))
        if up_ratio is None or median_pct_change is None:
            return "unknown"
        if up_ratio >= 0.6 and median_pct_change > 0:
            return "broad_strong"
        if up_ratio <= 0.4 and median_pct_change < 0:
            return "broad_weak"
        if median_pct_change < 0:
            return "narrow_weak"
        if median_pct_change > 0:
            return "narrow_strong"
        return "mixed"

    def _mean(self, values: list[float | None]) -> float | None:
        normalized = [value for value in values if value is not None]
        if not normalized:
            return None
        return round(sum(normalized) / len(normalized), 4)

    def _median(self, values: list[float | None]) -> float | None:
        normalized = sorted(value for value in values if value is not None)
        if not normalized:
            return None
        mid = len(normalized) // 2
        if len(normalized) % 2 == 1:
            return round(normalized[mid], 4)
        return round((normalized[mid - 1] + normalized[mid]) / 2, 4)

    def _to_float(self, value: Any) -> float | None:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except (TypeError, ValueError):
            return None
