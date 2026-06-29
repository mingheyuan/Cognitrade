from __future__ import annotations

from typing import Any


class ScreenerService:
    agent_name = "screener"

    def run(
        self,
        *,
        query: str,
        source_payloads: list[dict],
        snapshot_payloads: list[dict],
        constraints: dict | None = None,
    ) -> dict:
        constraints = constraints or {}
        source_payload = source_payloads[0] if source_payloads else {}
        universe = list(source_payload.get("candidate_universe") or [])
        sector_context = source_payload.get("sector_context")
        cycle = str(source_payload.get("investment_cycle") or constraints.get("investment_cycle") or "mid_term")
        cycle_assumption = str(source_payload.get("investment_cycle_assumption") or "explicit")
        skip_enrichment = bool(constraints.get("skip_enrichment"))
        enrichment_top_n = int(constraints.get("enrichment_top_n") or 2)
        watchlist_top_k = int(constraints.get("watchlist_top_k") or 2)
        min_score_value = constraints.get("min_score")
        min_score = float(min_score_value) if min_score_value is not None else self._default_min_score(cycle)
        watchlist_min_score_value = constraints.get("watchlist_min_score")
        watchlist_min_score = (
            float(watchlist_min_score_value)
            if watchlist_min_score_value is not None
            else self._default_watchlist_min_score(cycle)
        )

        snapshot_by_symbol: dict[str, dict[str, dict]] = {}
        for snapshot in snapshot_payloads:
            symbol = str(snapshot.get("symbol") or "").strip()
            snapshot_type = str(snapshot.get("snapshot_type") or "").strip()
            if not symbol or not snapshot_type:
                continue
            snapshot_by_symbol.setdefault(symbol, {})[snapshot_type] = snapshot

        candidates: list[dict[str, Any]] = []
        for item in universe:
            symbol = str(item.get("symbol") or "").strip()
            if not symbol:
                continue
            snapshots = snapshot_by_symbol.get(symbol, {})
            market_snapshot = snapshots.get("market")
            fundamental_snapshot = snapshots.get("fundamental")
            notice_snapshot = snapshots.get("notice")

            price = self._latest_price(market_snapshot, fallback=item.get("latest_price"))
            pct_change = self._latest_pct_change(market_snapshot, fallback=item.get("latest_pct_change"))
            amount = self._latest_amount(market_snapshot, fallback=item.get("latest_amount"))
            notice_count = self._notice_count(notice_snapshot)
            has_financial_abstract = self._has_financial_abstract(fundamental_snapshot)
            missing_evidence = self._missing_evidence(
                market_snapshot=market_snapshot,
                fundamental_snapshot=fundamental_snapshot,
                notice_snapshot=notice_snapshot,
            )
            degraded_sources = self._degraded_sources(
                market_snapshot=market_snapshot,
                fundamental_snapshot=fundamental_snapshot,
                notice_snapshot=notice_snapshot,
            )
            deep_checked = not missing_evidence and bool(market_snapshot and fundamental_snapshot and notice_snapshot)
            score_breakdown = {
                "liquidity": self._liquidity_score(amount),
                "momentum": self._momentum_score(pct_change, cycle=cycle),
                "fundamental": 1.0 if has_financial_abstract else 0.0,
                "notice": self._notice_score(notice_count),
                "deep_check_bonus": 0.5 if deep_checked else 0.0,
                "evidence_penalty": -0.75 * len(missing_evidence),
            }
            score = round(sum(score_breakdown.values()), 3)
            rationale = self._candidate_rationale(
                name=str(item.get("name") or symbol),
                pct_change=pct_change,
                amount=amount,
                notice_count=notice_count,
                has_financial_abstract=has_financial_abstract,
                sector_context=sector_context,
            )
            risk = self._candidate_risk(
                degraded_sources=degraded_sources,
                missing_evidence=missing_evidence,
                pct_change=pct_change,
            )
            candidates.append(
                {
                    "symbol": symbol,
                    "name": str(item.get("name") or symbol),
                    "sector": item.get("sector"),
                    "theme": item.get("theme"),
                    "latest_price": price,
                    "latest_pct_change": pct_change,
                    "latest_amount": amount,
                    "notice_count": notice_count,
                    "has_financial_abstract": has_financial_abstract,
                    "score": score,
                    "score_breakdown": score_breakdown,
                    "why_it_made_the_list": rationale,
                    "main_risk": risk,
                    "missing_evidence": missing_evidence,
                    "degraded_sources": degraded_sources,
                    "deep_checked": deep_checked,
                }
            )

        candidates.sort(
            key=lambda item: (
                item.get("score") or 0.0,
                item.get("latest_amount") or 0.0,
                item.get("latest_pct_change") or -999.0,
            ),
            reverse=True,
        )
        filtered_candidates = [item for item in candidates if (item.get("score") or 0.0) >= min_score]
        watchlist_pool = [
            item
            for item in candidates
            if watchlist_min_score <= (item.get("score") or 0.0) < min_score
        ]
        top_candidates = filtered_candidates[: int(constraints.get("top_k") or 5)]
        watchlist_candidates = watchlist_pool[:watchlist_top_k]
        for item in top_candidates:
            item["candidate_tier"] = "core"
        for item in watchlist_candidates:
            item["candidate_tier"] = "watchlist"
        enrichment_targets = []
        if not skip_enrichment:
            enrichment_pool = top_candidates + watchlist_candidates
            enrichment_targets = [item for item in enrichment_pool[:enrichment_top_n] if not item.get("deep_checked")]
        deep_checked_count = sum(1 for item in top_candidates if item.get("deep_checked"))
        watchlist_deep_checked_count = sum(1 for item in watchlist_candidates if item.get("deep_checked"))
        scorecard = {
            "investment_cycle": cycle,
            "investment_cycle_assumption": cycle_assumption,
            "candidate_count": len(candidates),
            "eligible_candidate_count": len(filtered_candidates),
            "top_candidate_count": len(top_candidates),
            "watchlist_candidate_count": len(watchlist_candidates),
            "deep_checked_count": deep_checked_count,
            "watchlist_deep_checked_count": watchlist_deep_checked_count,
            "enrichment_target_count": len(enrichment_targets),
            "min_score": min_score,
            "watchlist_min_score": watchlist_min_score,
            "average_score": round(sum(item.get("score") or 0.0 for item in top_candidates) / len(top_candidates), 3)
            if top_candidates
            else 0.0,
            "sector_context_present": bool(sector_context),
            "summary": self._summary(
                top_candidates=top_candidates,
                watchlist_candidates=watchlist_candidates,
                sector_context=sector_context,
                cycle=cycle,
            ),
        }
        return {
            "screened_candidates": top_candidates,
            "watchlist_candidates": watchlist_candidates,
            "enrichment_targets": enrichment_targets,
            "scorecard": scorecard,
            "screening_notes": [
                f"investment_cycle={cycle}",
                f"investment_cycle_assumption={cycle_assumption}",
                f"candidate_count={len(candidates)}",
                f"eligible_candidate_count={len(filtered_candidates)}",
                f"top_candidate_count={len(top_candidates)}",
                f"watchlist_candidate_count={len(watchlist_candidates)}",
                f"deep_checked_count={deep_checked_count}",
                f"watchlist_deep_checked_count={watchlist_deep_checked_count}",
                f"enrichment_target_count={len(enrichment_targets)}",
                f"min_score={min_score}",
                f"watchlist_min_score={watchlist_min_score}",
            ],
        }

    def _latest_price(self, market_snapshot: dict | None, fallback: Any = None) -> float | None:
        facts = (market_snapshot or {}).get("facts") or {}
        overview = facts.get("market_overview") or {}
        spot = overview.get("spot") or {}
        value = self._to_float(spot.get("最新价"))
        return value if value is not None else self._to_float(fallback)

    def _latest_pct_change(self, market_snapshot: dict | None, fallback: Any = None) -> float | None:
        facts = (market_snapshot or {}).get("facts") or {}
        overview = facts.get("market_overview") or {}
        spot = overview.get("spot") or {}
        value = self._to_float(spot.get("涨跌幅"))
        return value if value is not None else self._to_float(fallback)

    def _latest_amount(self, market_snapshot: dict | None, fallback: Any = None) -> float | None:
        facts = (market_snapshot or {}).get("facts") or {}
        overview = facts.get("market_overview") or {}
        spot = overview.get("spot") or {}
        value = self._to_float(spot.get("成交额"))
        return value if value is not None else self._to_float(fallback)

    def _notice_count(self, notice_snapshot: dict | None) -> int:
        facts = (notice_snapshot or {}).get("facts") or {}
        value = facts.get("notice_count")
        try:
            return int(value or 0)
        except Exception:
            return 0

    def _has_financial_abstract(self, fundamental_snapshot: dict | None) -> bool:
        facts = (fundamental_snapshot or {}).get("facts") or {}
        abstract = facts.get("financial_abstract") or {}
        return bool(abstract)

    def _missing_evidence(
        self,
        *,
        market_snapshot: dict | None,
        fundamental_snapshot: dict | None,
        notice_snapshot: dict | None,
    ) -> list[str]:
        missing: list[str] = []
        if market_snapshot is None or market_snapshot.get("degraded"):
            missing.append("market_snapshot")
        if fundamental_snapshot is None or fundamental_snapshot.get("degraded"):
            missing.append("fundamental_snapshot")
        if notice_snapshot is None or notice_snapshot.get("degraded"):
            missing.append("notice_snapshot")
        return missing

    def _degraded_sources(
        self,
        *,
        market_snapshot: dict | None,
        fundamental_snapshot: dict | None,
        notice_snapshot: dict | None,
    ) -> list[str]:
        degraded: list[str] = []
        for name, snapshot in (
            ("market_snapshot", market_snapshot),
            ("fundamental_snapshot", fundamental_snapshot),
            ("notice_snapshot", notice_snapshot),
        ):
            if snapshot is not None and snapshot.get("degraded"):
                degraded.append(name)
        return degraded

    def _liquidity_score(self, amount: float | None) -> float:
        if amount is None:
            return 0.0
        if amount >= 2_000_000_000:
            return 3.0
        if amount >= 1_000_000_000:
            return 2.5
        if amount >= 500_000_000:
            return 2.0
        if amount >= 200_000_000:
            return 1.0
        return 0.25

    def _momentum_score(self, pct_change: float | None, *, cycle: str) -> float:
        if pct_change is None:
            return 0.0
        if cycle == "short_term":
            return max(min(pct_change / 2.0, 2.0), -2.0)
        if cycle == "long_term":
            return max(min(pct_change / 4.0, 1.5), -1.5)
        return max(min(pct_change / 3.0, 1.75), -1.75)

    def _notice_score(self, notice_count: int) -> float:
        if notice_count >= 5:
            return 1.5
        if notice_count >= 3:
            return 1.0
        if notice_count >= 1:
            return 0.5
        return 0.0

    def _candidate_rationale(
        self,
        *,
        name: str,
        pct_change: float | None,
        amount: float | None,
        notice_count: int,
        has_financial_abstract: bool,
        sector_context: dict | None,
    ) -> str:
        reasons: list[str] = []
        if pct_change is not None:
            if pct_change > 0:
                reasons.append(f"最新涨跌幅为 {pct_change}%")
            else:
                reasons.append(f"最新涨跌幅为 {pct_change}% ，但仍保留在观察池中")
        if amount is not None:
            reasons.append(f"成交额约 {round(amount / 100000000, 2)} 亿元")
        if notice_count > 0:
            reasons.append(f"近 30 天公告 {notice_count} 条")
        if has_financial_abstract:
            reasons.append("财务摘要可用")
        if sector_context and sector_context.get("sector"):
            reasons.append(f"来自 {sector_context.get('sector')} 相关候选池")
        return f"{name}: " + "，".join(reasons) if reasons else f"{name}: 证据较少，仅保留为备选"

    def _candidate_risk(self, *, degraded_sources: list[str], missing_evidence: list[str], pct_change: float | None) -> str:
        reasons: list[str] = []
        if degraded_sources:
            reasons.append(f"数据源降级: {', '.join(degraded_sources)}")
        if missing_evidence:
            reasons.append(f"关键证据缺失: {', '.join(missing_evidence)}")
        if pct_change is not None and pct_change < -4.0:
            reasons.append("短期价格承压较明显")
        return "；".join(reasons) if reasons else "暂无明显结构性风险，但仍需人工复核"

    def _summary(self, *, top_candidates: list[dict], watchlist_candidates: list[dict], sector_context: dict | None, cycle: str) -> str:
        if not top_candidates and not watchlist_candidates:
            return "未筛出满足最小证据要求的候选。"
        names = [str(item.get("name") or item.get("symbol") or "") for item in top_candidates[:3]]
        watch_names = [str(item.get("name") or item.get("symbol") or "") for item in watchlist_candidates[:3]]
        prefix = f"{cycle} 候选已生成"
        if sector_context and sector_context.get("sector"):
            prefix += f"，主题偏向 {sector_context.get('sector')}"
        if top_candidates and watchlist_candidates:
            return (
                f"{prefix}，优先关注: {', '.join(name for name in names if name)}；"
                f"次级观察: {', '.join(name for name in watch_names if name)}"
            )
        if top_candidates:
            return f"{prefix}，优先关注: {', '.join(name for name in names if name)}"
        return f"{prefix}，当前仅形成次级观察名单: {', '.join(name for name in watch_names if name)}"

    def _to_float(self, value: Any) -> float | None:
        if value in (None, "", False):
            return None
        try:
            return float(value)
        except Exception:
            return None

    def _default_min_score(self, cycle: str) -> float:
        if cycle == "short_term":
            return 1.0
        if cycle == "long_term":
            return 0.5
        return 0.75

    def _default_watchlist_min_score(self, cycle: str) -> float:
        if cycle == "short_term":
            return 0.0
        if cycle == "long_term":
            return 0.0
        return 0.25
