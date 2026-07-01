from __future__ import annotations

from typing import Any

from research_system.services.methodologies.comps_service import CompsService
from research_system.services.planning_context import (
    build_planning_context_view,
    derive_methodology_focus,
    has_cashflow_focus,
    has_long_term_bias,
    has_momentum_focus,
    normalize_planning_context_hints,
)


class ScreenerService:
    agent_name = "screener"

    def __init__(self, comps_service: CompsService | None = None) -> None:
        self.comps_service = comps_service or CompsService()

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
        planning_context_hints = self._normalize_planning_context_hints(
            constraints.get("planning_context_hints")
            or source_payload.get("planning_context_hints")
        )
        strategy = self._derive_screening_strategy(
            query=query,
            source_payload=source_payload,
            planning_context_hints=planning_context_hints,
            explicit_cycle=constraints.get("investment_cycle"),
            explicit_methodology=constraints.get("prefer_methodology"),
        )
        cycle = str(strategy["resolved_investment_cycle"] or "mid_term")
        cycle_assumption = str(strategy["investment_cycle_assumption"] or "explicit")
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
            lhb_snapshot = snapshots.get("lhb")
            capital_flow_snapshot = snapshots.get("capital_flow")
            sentiment_snapshot = snapshots.get("sentiment")
            events_snapshot = snapshots.get("events")

            price = self._latest_price(market_snapshot, fallback=item.get("latest_price"))
            pct_change = self._latest_pct_change(market_snapshot, fallback=item.get("latest_pct_change"))
            amount = self._latest_amount(market_snapshot, fallback=item.get("latest_amount"))
            notice_count = self._notice_count(notice_snapshot)
            has_financial_abstract = self._has_financial_abstract(fundamental_snapshot)
            capital_flow_score = self._capital_flow_score(capital_flow_snapshot)
            sentiment_score = self._sentiment_score(sentiment_snapshot)
            lhb_score = self._lhb_score(lhb_snapshot)
            event_score = self._event_score(events_snapshot)
            revenue_proxy = self._revenue_proxy(fundamental_snapshot, fallback=item.get("revenue_proxy"))
            missing_evidence = self._missing_evidence(
                market_snapshot=market_snapshot,
                fundamental_snapshot=fundamental_snapshot,
                notice_snapshot=notice_snapshot,
            )
            degraded_sources = self._degraded_sources(
                market_snapshot=market_snapshot,
                fundamental_snapshot=fundamental_snapshot,
                notice_snapshot=notice_snapshot,
                lhb_snapshot=lhb_snapshot,
                capital_flow_snapshot=capital_flow_snapshot,
                sentiment_snapshot=sentiment_snapshot,
                events_snapshot=events_snapshot,
            )
            deep_checked = not missing_evidence and bool(market_snapshot and fundamental_snapshot and notice_snapshot)
            score_breakdown = {
                "liquidity": self._liquidity_score(amount),
                "momentum": self._momentum_score(pct_change, cycle=cycle),
                "fundamental": 1.0 if has_financial_abstract else 0.0,
                "notice": self._notice_score(notice_count),
                "capital_flow": capital_flow_score,
                "sentiment": sentiment_score,
                "lhb": lhb_score,
                "events": event_score,
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
                capital_flow_score=capital_flow_score,
                sentiment_score=sentiment_score,
                lhb_score=lhb_score,
                event_score=event_score,
            )
            risk = self._candidate_risk(
                degraded_sources=degraded_sources,
                missing_evidence=missing_evidence,
                pct_change=pct_change,
                capital_flow_score=capital_flow_score,
                sentiment_score=sentiment_score,
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
                    "revenue_proxy": revenue_proxy,
                    "notice_count": notice_count,
                    "has_financial_abstract": has_financial_abstract,
                    "score": score,
                    "score_breakdown": score_breakdown,
                    "why_it_made_the_list": rationale,
                    "main_risk": risk,
                    "missing_evidence": missing_evidence,
                    "degraded_sources": degraded_sources,
                    "deep_checked": deep_checked,
                    "capital_flow_score": capital_flow_score,
                    "sentiment_score": sentiment_score,
                    "lhb_score": lhb_score,
                    "event_score": event_score,
                    "methodology": {},
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
        prefer_methodology = str(strategy["preferred_methodology"] or "comps").strip() or "comps"
        methodology_notes = self._attach_comps_methodology(
            candidates=top_candidates + watchlist_candidates,
            peer_universe=candidates,
            cycle=cycle,
            sector_context=sector_context,
            enabled=prefer_methodology == "comps",
        )
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
            "preferred_methodology": prefer_methodology,
            "methodology_notes": methodology_notes,
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
            "planning_context_hints": planning_context_hints or None,
            "screening_strategy": strategy,
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
                f"preferred_methodology={prefer_methodology}",
                *list(strategy.get("notes") or []),
                *methodology_notes,
            ],
        }

    def _normalize_planning_context_hints(self, raw_hints) -> dict[str, str]:
        return normalize_planning_context_hints(raw_hints)

    def _derive_screening_strategy(
        self,
        *,
        query: str,
        source_payload: dict,
        planning_context_hints: dict[str, str],
        explicit_cycle,
        explicit_methodology,
    ) -> dict[str, Any]:
        context = build_planning_context_view(
            query=query,
            planning_context_hints=planning_context_hints,
        )
        text = context.combined_text
        preferred_methodology_from_context, methodology_source = derive_methodology_focus(
            cashflow_text=context.query_text,
            comparison_text=text,
            planning_context_hints=planning_context_hints,
        )
        base_cycle = source_payload.get("investment_cycle")
        if explicit_cycle:
            resolved_cycle = str(explicit_cycle).strip()
            cycle_assumption = "explicit"
        elif base_cycle:
            resolved_cycle = str(base_cycle).strip()
            cycle_assumption = str(source_payload.get("investment_cycle_assumption") or "explicit")
        elif has_momentum_focus(text):
            resolved_cycle = "short_term"
            cycle_assumption = "planning_context"
        elif has_long_term_bias(text):
            resolved_cycle = "long_term"
            cycle_assumption = "planning_context"
        else:
            resolved_cycle = "mid_term"
            cycle_assumption = "default_mid_term"

        if explicit_methodology:
            preferred_methodology = str(explicit_methodology).strip() or "comps"
            methodology_source = "explicit"
        elif preferred_methodology_from_context:
            preferred_methodology = preferred_methodology_from_context
        else:
            preferred_methodology = "comps"
            methodology_source = "default"

        notes = [
            f"screening_strategy_cycle={resolved_cycle}",
            f"screening_strategy_methodology={preferred_methodology}",
            f"screening_strategy_methodology_source={methodology_source}",
        ]
        if planning_context_hints:
            notes.append("screening_strategy_has_context_hints=true")
        return {
            "resolved_investment_cycle": resolved_cycle,
            "investment_cycle_assumption": cycle_assumption,
            "preferred_methodology": preferred_methodology,
            "methodology_source": methodology_source,
            "notes": notes,
        }

    def _latest_price(self, market_snapshot: dict | None, fallback: Any = None) -> float | None:
        facts = (market_snapshot or {}).get("facts") or {}
        overview = facts.get("market_overview") or {}
        spot = overview.get("spot") or {}
        value = self._to_float(spot.get("latest_price"))
        if value is None:
            value = self._to_float(spot.get("最新价"))
        if value is None:
            value = self._to_float(spot.get("鏈€鏂颁环"))
        return value if value is not None else self._to_float(fallback)

    def _latest_pct_change(self, market_snapshot: dict | None, fallback: Any = None) -> float | None:
        facts = (market_snapshot or {}).get("facts") or {}
        overview = facts.get("market_overview") or {}
        spot = overview.get("spot") or {}
        value = self._to_float(spot.get("pct_change"))
        if value is None:
            value = self._to_float(spot.get("涨跌幅"))
        if value is None:
            value = self._to_float(spot.get("娑ㄨ穼骞?"))
        return value if value is not None else self._to_float(fallback)

    def _latest_amount(self, market_snapshot: dict | None, fallback: Any = None) -> float | None:
        facts = (market_snapshot or {}).get("facts") or {}
        overview = facts.get("market_overview") or {}
        spot = overview.get("spot") or {}
        value = self._to_float(spot.get("amount"))
        if value is None:
            value = self._to_float(spot.get("成交额"))
        if value is None:
            value = self._to_float(spot.get("鎴愪氦棰?"))
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

    def _revenue_proxy(self, fundamental_snapshot: dict | None, fallback: Any = None) -> float | None:
        facts = (fundamental_snapshot or {}).get("facts") or {}
        abstract = facts.get("financial_abstract") or {}
        for key in ("营业总收入", "营业收入", "营收", "总营收"):
            value = self._to_float(abstract.get(key))
            if value is not None:
                return value
        for item_key, item_value in abstract.items():
            key_text = str(item_key)
            if any(token in key_text for token in ("营业", "营收", "收入")):
                value = self._to_float(item_value)
                if value is not None:
                    return value
        return self._to_float(fallback)

    def _attach_comps_methodology(
        self,
        *,
        candidates: list[dict[str, Any]],
        peer_universe: list[dict[str, Any]],
        cycle: str,
        sector_context: dict | None,
        enabled: bool,
    ) -> list[str]:
        if not enabled:
            return ["methodology_comps=disabled"]

        notes: list[str] = []
        for candidate in candidates:
            peers = [
                peer
                for peer in peer_universe
                if str(peer.get("symbol") or "").strip()
                and str(peer.get("symbol") or "").strip() != str(candidate.get("symbol") or "").strip()
            ]
            comps_result = self.comps_service.build_for_candidate(
                candidate=candidate,
                peers=peers,
                cycle=cycle,
                sector_context=sector_context,
            )
            candidate.setdefault("methodology", {})["comps"] = comps_result
            candidate["valuation_band"] = comps_result.get("valuation_band") or {}
            candidate["valuation_view"] = ((comps_result.get("outputs") or {}).get("relative_view"))
            notes.append(
                f"comps:{candidate.get('symbol')}={((comps_result.get('outputs') or {}).get('relative_view') or 'insufficient_peer_signal')}"
            )
        if not notes:
            return ["methodology_comps=no_candidates"]
        return notes

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
        lhb_snapshot: dict | None,
        capital_flow_snapshot: dict | None,
        sentiment_snapshot: dict | None,
        events_snapshot: dict | None,
    ) -> list[str]:
        degraded: list[str] = []
        for name, snapshot in (
            ("market_snapshot", market_snapshot),
            ("fundamental_snapshot", fundamental_snapshot),
            ("notice_snapshot", notice_snapshot),
            ("lhb_snapshot", lhb_snapshot),
            ("capital_flow_snapshot", capital_flow_snapshot),
            ("sentiment_snapshot", sentiment_snapshot),
            ("events_snapshot", events_snapshot),
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

    def _capital_flow_score(self, snapshot: dict | None) -> float:
        facts = (snapshot or {}).get("facts") or {}
        latest = facts.get("latest_flow") or {}
        score = 0.0
        for key, value in latest.items():
            key_text = str(key)
            if "净流入" not in key_text and "净额" not in key_text and "flow" not in key_text.lower():
                continue
            numeric = self._to_float(value)
            if numeric is None:
                continue
            if numeric > 0:
                score += 0.5
            elif numeric < 0:
                score -= 0.35
            break
        if facts.get("main_flow_rank"):
            score += 0.35
        return round(score, 3)

    def _sentiment_score(self, snapshot: dict | None) -> float:
        facts = (snapshot or {}).get("facts") or {}
        latest_rank = facts.get("hot_rank_latest") or []
        detail = facts.get("hot_rank_detail") or []
        keywords = facts.get("hot_keywords") or []
        score = 0.0
        if latest_rank:
            score += 0.4
        if detail:
            score += 0.25
        if keywords:
            score += 0.2
        if not latest_rank and not detail:
            return 0.0
        return round(score, 3)

    def _lhb_score(self, snapshot: dict | None) -> float:
        facts = (snapshot or {}).get("facts") or {}
        buy_total = self._to_float(facts.get("buy_top_total"))
        sell_total = self._to_float(facts.get("sell_top_total"))
        if buy_total is None and sell_total is None:
            return 0.0
        if buy_total is not None and sell_total is not None:
            if buy_total > sell_total:
                return 0.35
            if buy_total < sell_total:
                return -0.15
        return 0.1

    def _event_score(self, snapshot: dict | None) -> float:
        facts = (snapshot or {}).get("facts") or {}
        event_keywords = facts.get("event_keywords") or []
        event_notices = facts.get("event_notices") or []
        score = 0.0
        if event_keywords:
            score += min(len(event_keywords) * 0.15, 0.45)
        if event_notices:
            score += 0.15
        return round(score, 3)

    def _candidate_rationale(
        self,
        *,
        name: str,
        pct_change: float | None,
        amount: float | None,
        notice_count: int,
        has_financial_abstract: bool,
        sector_context: dict | None,
        capital_flow_score: float,
        sentiment_score: float,
        lhb_score: float,
        event_score: float,
    ) -> str:
        reasons: list[str] = []
        if pct_change is not None:
            if pct_change > 0:
                reasons.append(f"最新涨跌幅 {pct_change}%")
            else:
                reasons.append(f"最新涨跌幅 {pct_change}%，但仍保留在观察池中")
        if amount is not None:
            reasons.append(f"成交额约 {round(amount / 100000000, 2)} 亿元")
        if notice_count > 0:
            reasons.append(f"近30天公告 {notice_count} 条")
        if has_financial_abstract:
            reasons.append("财务摘要可用")
        if sector_context and sector_context.get("sector"):
            reasons.append(f"来自 {sector_context.get('sector')} 相关候选池")
        if capital_flow_score > 0:
            reasons.append("资金流向维度偏强")
        if sentiment_score > 0:
            reasons.append("热度/人气维度有正向信号")
        if lhb_score > 0:
            reasons.append("龙虎榜维度显示活跃迹象")
        if event_score > 0:
            reasons.append("近30天存在事件催化线索")
        return f"{name}: " + "，".join(reasons) if reasons else f"{name}: 证据较少，仅保留为备选"

    def _candidate_risk(
        self,
        *,
        degraded_sources: list[str],
        missing_evidence: list[str],
        pct_change: float | None,
        capital_flow_score: float,
        sentiment_score: float,
    ) -> str:
        reasons: list[str] = []
        if degraded_sources:
            reasons.append(f"数据源降级: {', '.join(degraded_sources)}")
        if missing_evidence:
            reasons.append(f"关键证据缺失: {', '.join(missing_evidence)}")
        if pct_change is not None and pct_change < -4.0:
            reasons.append("短期价格承压较明显")
        if capital_flow_score < 0:
            reasons.append("资金流向维度偏弱")
        if sentiment_score < 0:
            reasons.append("热度/人气维度偏冷")
        return "，".join(reasons) if reasons else "暂无明显结构性风险，但仍需人工复核"

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
