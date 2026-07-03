from __future__ import annotations

from datetime import date
from typing import Any

from research_system.services.planning_context import normalize_planning_context_hints
from research_system.services.review_focus_support import build_review_focus_notes, resolve_review_focus


class ReviewerStrategySupport:
    def __init__(self, review_policy: dict[str, Any]) -> None:
        self.review_policy = review_policy

    def normalize_planning_context_hints(self, raw_hints) -> dict[str, str]:
        return normalize_planning_context_hints(raw_hints)

    def derive_review_strategy(
        self,
        *,
        query: str,
        planning_context_hints: dict[str, str],
    ) -> dict[str, Any]:
        strategy: dict[str, Any] = {
            "preferred_methodology": None,
            "focus_area": None,
            "requires_market_snapshot": False,
            "requires_fundamental_snapshot": False,
            "requires_notice_or_policy_signal": False,
            "notes": [],
        }
        focus = resolve_review_focus(
            query=query,
            planning_context_hints=planning_context_hints,
            include_policy_focus=True,
            include_momentum_focus=True,
        )
        strategy.update(
            preferred_methodology=focus["preferred_methodology"],
            focus_area=focus["focus_area"],
            requires_market_snapshot=bool(focus["requires_market_snapshot"]),
            requires_fundamental_snapshot=bool(focus["requires_fundamental_snapshot"]),
            requires_notice_or_policy_signal=bool(focus["requires_notice_or_policy_signal"]),
        )
        strategy["notes"] = build_review_focus_notes(
            note_prefix="review",
            focus_area=strategy.get("focus_area"),
            preferred_methodology=strategy.get("preferred_methodology"),
            focus_source=focus.get("focus_source"),
        )
        return strategy

    def validate_review_strategy_requirements(
        self,
        *,
        review_strategy: dict[str, Any],
        latest_market_snapshot: dict | None,
        latest_fundamental_snapshot: dict | None,
        latest_notice_snapshot: dict | None,
        policy_signals: list[dict],
        regulatory_signals: list[dict],
    ) -> dict[str, Any]:
        notes: list[str] = []
        review_required = False
        if review_strategy.get("requires_fundamental_snapshot") and latest_fundamental_snapshot is None:
            review_required = True
            notes.append("missing_fundamental_snapshot_for_review_focus")
        if review_strategy.get("requires_market_snapshot") and latest_market_snapshot is None:
            review_required = True
            notes.append("missing_market_snapshot_for_review_focus")
        if review_strategy.get("requires_notice_or_policy_signal"):
            has_notice = latest_notice_snapshot is not None
            has_policy_signal = bool(policy_signals or regulatory_signals)
            if not has_notice and not has_policy_signal:
                review_required = True
                notes.append("missing_notice_or_policy_signal_for_review_focus")
        return {"review_required": review_required, "notes": notes}

    def resolve_benchmark(
        self,
        *,
        latest_sector_snapshot: dict | None,
        latest_market_snapshot: dict | None,
        default_benchmark_name: str,
        default_benchmark_symbol: str,
    ) -> dict[str, Any]:
        facts = (latest_sector_snapshot or {}).get("facts") or {}
        benchmark = facts.get("benchmark") or {}
        scope = self.resolve_benchmark_scope(
            latest_sector_snapshot=latest_sector_snapshot,
            latest_market_snapshot=latest_market_snapshot,
        )
        sector_name = str((latest_sector_snapshot or {}).get("sector") or "")
        benchmark_name = self.resolve_benchmark_name(
            benchmark_scope=scope,
            sector_name=sector_name,
            default_benchmark_name=default_benchmark_name,
        )
        benchmark_symbol = self.resolve_benchmark_symbol(
            benchmark_scope=scope,
            latest_market_snapshot=latest_market_snapshot,
            default_benchmark_symbol=default_benchmark_symbol,
        )
        return {
            "benchmark_scope": scope,
            "benchmark_name": benchmark_name,
            "benchmark_symbol": benchmark_symbol,
            "observed_benchmark_metric": self.benchmark_pct_change(benchmark=benchmark),
        }

    def parse_anchor_date(self, *, candidates: list[dict], snapshot_date: str) -> str:
        if snapshot_date:
            return snapshot_date
        return date.today().isoformat()

    def resolve_benchmark_scope(
        self,
        *,
        latest_sector_snapshot: dict | None,
        latest_market_snapshot: dict | None,
    ) -> str:
        if latest_market_snapshot is not None and latest_sector_snapshot is not None:
            return "stock_vs_sector"
        if latest_sector_snapshot is not None:
            return "sector_vs_hs300"
        if latest_market_snapshot is not None:
            return "stock_absolute"
        return str(self.review_policy.get("benchmark_resolution") or "sector")

    def resolve_benchmark_name(
        self,
        *,
        benchmark_scope: str,
        sector_name: str,
        default_benchmark_name: str,
    ) -> str:
        if benchmark_scope == "stock_vs_sector":
            return sector_name or "sector"
        return default_benchmark_name

    def resolve_benchmark_symbol(
        self,
        *,
        benchmark_scope: str,
        latest_market_snapshot: dict | None,
        default_benchmark_symbol: str,
    ) -> str:
        if benchmark_scope == "stock_absolute":
            return str((latest_market_snapshot or {}).get("symbol") or default_benchmark_symbol)
        return default_benchmark_symbol

    def benchmark_pct_change(self, *, benchmark: dict) -> float | None:
        latest_hist = benchmark.get("latest_hist") or {}
        return self.to_float(latest_hist.get("pct_change"))

    def market_pct_change(self, snapshot: dict | None) -> float | None:
        if snapshot is None:
            return None
        facts = snapshot.get("facts") or {}
        latest_bar = facts.get("latest_bar") or {}
        for key in ("pct_change", "change_percent", "涨跌幅"):
            value = self.to_float(latest_bar.get(key))
            if value is not None:
                return value
        return None

    def notice_signal_count(self, snapshot: dict | None) -> int | None:
        if snapshot is None:
            return None
        facts = snapshot.get("facts") or {}
        notices = facts.get("recent_notices") or facts.get("notices") or []
        if isinstance(notices, list):
            return len(notices)
        return None

    def to_float(self, value: Any) -> float | None:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except (TypeError, ValueError):
            return None
