from __future__ import annotations

import re

from domain_packs.finance_ashare.tools.snapshot_collector import collect_snapshot_bundle, collect_snapshots
from research_system.adapters.vendors.akshare_utils import coerce_numeric
from research_system.services.methodologies import (
    CashflowResilienceService,
    CompsService,
    DCFService,
)
from research_system.runtime.core.artifact_board import ArtifactBoard
from research_system.schemas.artifacts import ArtifactDraft


_SCREEN_STYLE_CRITERIA = {
    "value": (
        ("valuation_evidence", "valuation evidence", ("pe", "pb", "ps", "valuation")),
        ("liquidity", "reported turnover", ("amount",)),
    ),
    "growth": (
        ("growth_evidence", "reported growth evidence", ("revenue_growth", "profit_growth", "growth")),
        ("momentum", "reported price change", ("pct_change",)),
    ),
    "quality": (
        ("cashflow_or_profitability", "cash-flow or profitability evidence", ("operating_cash_flow", "roe", "net_profit")),
        ("fundamental_evidence", "financial abstract", ("financial_abstract",)),
    ),
    "gulp": (
        ("growth_evidence", "reported growth evidence", ("revenue_growth", "profit_growth", "growth")),
        ("valuation_evidence", "valuation evidence", ("pe", "pb", "ps", "valuation")),
        ("liquidity", "reported turnover", ("amount",)),
    ),
    "short": (
        ("market_risk", "reported price change", ("pct_change",)),
        ("disclosure_risk", "disclosure evidence", ("notice_count",)),
    ),
}
_STOCK_CODE_PATTERN = re.compile(r"\b(\d{6})\b")
_SAFE_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_BANK_METRIC_WEIGHTS = {
    "pb": 0.20,
    "roe": 0.16,
    "asset_liability_ratio": 0.08,
    "capital_adequacy_ratio": 0.05,
    "core_tier1_capital_adequacy_ratio": 0.05,
    "net_interest_spread": 0.05,
    "nonperforming_loan_ratio": 0.07,
    "provision_coverage_ratio": 0.06,
    "dividend_yield_proxy": 0.08,
    "revenue_yoy": 0.04,
    "net_profit_yoy": 0.06,
    "interest_margin_proxy": 0.04,
}
_BANK_METRIC_DIRECTION = {
    "pb": "lower",
    "roe": "higher",
    "asset_liability_ratio": "lower",
    "capital_adequacy_ratio": "higher",
    "core_tier1_capital_adequacy_ratio": "higher",
    "net_interest_spread": "higher",
    "nonperforming_loan_ratio": "lower",
    "provision_coverage_ratio": "higher",
    "dividend_yield_proxy": "higher",
    "revenue_yoy": "higher",
    "net_profit_yoy": "higher",
    "interest_margin_proxy": "higher",
}


def normalize_query(context):
    request = context.require_artifact("user_request")
    query = str(request.content.get("query") or request.content.get("text") or "").strip()
    symbols = _unique_strings(request.content.get("symbols") or [])
    for symbol in _STOCK_CODE_PATTERN.findall(query):
        if symbol not in symbols:
            symbols.append(symbol)
    return ArtifactDraft(
        artifact_type="normalized_query",
        content={"query": query, "symbols": symbols},
        parent_artifact_ids=[request.artifact_id],
        confidence=1.0,
    )


def collect_snapshot(context):
    query = context.require_artifact("normalized_query")
    query_text = str(query.content.get("query") or "")
    symbols = _unique_strings(query.content.get("symbols") or [])
    inputs = dict(context.node.inputs or {})
    intent = str(inputs.get("intent") or "").strip()
    sector = str(inputs.get("sector") or inputs.get("sector_query") or "").strip() or None
    snapshots = list(query.content.get("snapshots") or inputs.get("snapshots") or [])
    requested_types = _requested_snapshot_types(inputs=inputs, sector=sector)
    review_target_run_id = str(inputs.get("review_target_run_id") or "").strip() or None
    target_artifacts = _review_target_artifacts(context, review_target_run_id) if intent == "review_thesis" else []
    if intent == "review_thesis" and not symbols:
        symbols = _target_symbols(target_artifacts)
    candidate_universe: list[dict] = []
    collection: dict = {
        "universe_source": "user_supplied" if symbols else "unavailable",
        "candidate_count": len(symbols),
        "requested_snapshot_types": requested_types,
        "collected_snapshot_types": [],
        "enrichment_snapshot_types": [],
        "enrichment_targets": [],
        "degraded": False,
    }
    provider_source_refs: list[dict] = []
    if not snapshots:
        provider_arguments = {
            "query": query_text,
            "symbols": symbols,
            "snapshot_types": requested_types,
            "intent": intent,
            "sector": sector,
            "candidate_pool_size": int(inputs.get("candidate_pool_size") or 8),
            "enrichment_top_n": int(inputs.get("enrichment_top_n") or 2),
        }
        call_tool = getattr(context, "call_tool", None)
        if callable(call_tool):
            tool_result = call_tool("financial_data_provider", provider_arguments)
            if tool_result.status != "completed":
                raise RuntimeError(tool_result.error or "financial_data_provider failed")
            collected = tool_result.output
            provider_source_refs = list(tool_result.source_refs)
        else:
            # Direct Pack unit tests may use a minimal context; production Graph
            # execution always takes the governed Tool Registry branch above.
            direct_snapshots = _collect_provider_snapshots(
                query=query_text,
                symbols=symbols,
                snapshot_types=requested_types,
                sector=sector,
                cancellation=getattr(context, "cancellation", None),
            )
            collected = {
                "snapshots": direct_snapshots,
                "symbols": symbols,
                "candidate_universe": [],
                "collection": collection,
            }
        snapshots = list(collected.get("snapshots") or [])
        symbols = _unique_strings([*symbols, *(collected.get("symbols") or [])])
        candidate_universe = [
            dict(row) for row in collected.get("candidate_universe") or [] if isinstance(row, dict)
        ]
        collection = dict(collected.get("collection") or collection)
    for snapshot in snapshots:
        if isinstance(snapshot, dict):
            symbol = str(snapshot.get("symbol") or "").strip()
            if symbol and symbol not in symbols:
                symbols.append(symbol)
    if not candidate_universe:
        candidate_universe = _universe_from_snapshots(snapshots, symbols)
    collection["collected_snapshot_types"] = list(
        dict.fromkeys(
            str(snapshot.get("snapshot_type") or "")
            for snapshot in snapshots
            if isinstance(snapshot, dict) and snapshot.get("snapshot_type")
        )
    )
    collection["degraded"] = not bool(snapshots)
    source_refs = list(inputs.get("source_refs") or [])
    if not source_refs:
        source_refs = (
            provider_source_refs
            or _snapshot_source_refs(snapshots)
            or [{"type": "user_supplied", "id": query.artifact_id}]
        )
    return ArtifactDraft(
        artifact_type="stock_snapshot_bundle",
        content={
            "query": query_text,
            "symbols": symbols,
            "sector": sector,
            "snapshots": snapshots,
            "candidate_universe": candidate_universe,
            "collection": collection,
            "review_target_run_id": review_target_run_id,
            "review_target_artifact_ids": [artifact.artifact_id for artifact in target_artifacts],
        },
        source_refs=source_refs,
        parent_artifact_ids=[query.artifact_id],
        confidence=0.8,
    )


def select_evidence(context):
    snapshots = context.require_artifact("stock_snapshot_bundle")
    return ArtifactDraft(
        artifact_type="evidence_set",
        content={
            "items": list(snapshots.content.get("snapshots") or []),
            "query": snapshots.content.get("query"),
        },
        source_refs=list(snapshots.source_refs),
        parent_artifact_ids=[snapshots.artifact_id],
        confidence=snapshots.confidence,
    )


def analyze_financials(context):
    snapshots = context.require_artifact("stock_snapshot_bundle")
    inputs = dict(context.node.inputs or {})
    grouped = _snapshots_by_symbol(list(snapshots.content.get("snapshots") or []))
    symbols = _unique_strings(snapshots.content.get("symbols") or [])
    candidates = [
        _analysis_candidate(symbol=symbol, snapshots=grouped.get(symbol, {}))
        for symbol in symbols
        if grouped.get(symbol)
    ]
    cycle = _investment_cycle(inputs)
    comps_service = CompsService()
    dcf_service = DCFService()
    cashflow_service = CashflowResilienceService()
    methodologies: list[dict] = []
    for candidate in candidates:
        symbol = str(candidate["symbol"])
        peers = [peer for peer in candidates if peer["symbol"] != symbol]
        market_snapshot = grouped[symbol].get("market")
        fundamental_snapshot = grouped[symbol].get("fundamental")
        methodologies.extend(
            [
                comps_service.build_for_candidate(
                    candidate=candidate,
                    peers=peers,
                    cycle=cycle,
                    sector_context={"sector": candidate.get("sector")},
                ),
                dcf_service.build_for_stock(
                    symbol=symbol,
                    fundamental_snapshot=fundamental_snapshot,
                    market_snapshot=market_snapshot,
                    cycle=cycle,
                ),
                cashflow_service.build_for_stock(
                    symbol=symbol,
                    fundamental_snapshot=fundamental_snapshot,
                    market_snapshot=market_snapshot,
                    cycle=cycle,
                ),
            ]
        )
    scorecard = _bank_compare_scorecard(candidates)
    scorecards = [scorecard] if scorecard else []
    return ArtifactDraft(
        artifact_type="financial_analysis",
        content={
            "symbols": [candidate["symbol"] for candidate in candidates],
            "methodologies": methodologies,
            "scorecards": scorecards,
            "summary": _analysis_summary(methodologies=methodologies, scorecards=scorecards),
        },
        source_refs=list(snapshots.source_refs),
        parent_artifact_ids=[snapshots.artifact_id],
        confidence=_analysis_confidence(methodologies),
        risk_flags=["methodology_assumption_driven"] if methodologies else ["financial_evidence_missing"],
    )


def compose_report(context):
    evidence = context.require_artifact("evidence_set")
    analysis = context.require_artifact("financial_analysis")
    items = list(evidence.content.get("items") or [])
    report_evidence = [_report_evidence_item(item, index) for index, item in enumerate(items)]
    methodologies = list(analysis.content.get("methodologies") or [])
    scorecards = list(analysis.content.get("scorecards") or [])
    assessment = _build_assessment(methodologies)
    available = [item for item in report_evidence if not item["metadata"].get("degraded")]
    degraded = [item for item in report_evidence if item["metadata"].get("degraded")]
    if not report_evidence:
        conclusion = "No verifiable market or fundamental snapshots were available for this request."
        risks = ["No financial evidence was collected, so no investment conclusion can be made."]
        follow_up = ["Provide one or more six-digit A-share codes, or supply source snapshots."]
    else:
        summaries = [str(item["summary"]) for item in available[:4] if item.get("summary")]
        evidence_summary = "; ".join(summaries) or "snapshot details are available below."
        conclusion = f"Preliminary assessment: {assessment['summary']} Evidence: {evidence_summary}"
        methodology_summary = str(analysis.content.get("summary") or "")
        if methodology_summary:
            conclusion = f"{conclusion} Methodology: {methodology_summary}"
        risks = ["Evidence may be incomplete or stale."]
        if degraded:
            risks.append(f"{len(degraded)} provider snapshot(s) were degraded; inspect the evidence source details.")
        methodology_warnings = _methodology_warnings(methodologies)
        if methodology_warnings:
            risks.append("Model assumptions or incomplete inputs: " + ", ".join(methodology_warnings[:6]))
        follow_up = ["Review the cited snapshots and refresh material filings and market data before acting."]
    return ArtifactDraft(
        artifact_type="research_report",
        content={
            "conclusion": conclusion,
            "assessment": assessment,
            "evidence": report_evidence,
            "methodologies": methodologies,
            "scorecard": scorecards[0] if scorecards else None,
            "risk": risks,
            "follow_up": follow_up,
        },
        source_refs=list(evidence.source_refs),
        parent_artifact_ids=[evidence.artifact_id, analysis.artifact_id],
        confidence=min(
            _artifact_confidence(evidence.confidence),
            _artifact_confidence(analysis.confidence),
        ),
        risk_flags=["decision_support_only"],
    )


def _build_assessment(methodologies: list[dict]) -> dict:
    """Summarize observable model outputs without turning them into a recommendation."""

    by_symbol: dict[str, dict[str, dict]] = {}
    for methodology in methodologies:
        if not isinstance(methodology, dict):
            continue
        symbol = str(methodology.get("target_symbol") or "unknown")
        kind = str(methodology.get("methodology_type") or "methodology")
        by_symbol.setdefault(symbol, {})[kind] = methodology

    factors: list[dict] = []
    summaries: list[str] = []
    for symbol, views in by_symbol.items():
        signals: list[str] = []
        dcf = views.get("dcf") or {}
        dcf_outputs = dcf.get("outputs") if isinstance(dcf.get("outputs"), dict) else {}
        gap = _number(dcf_outputs.get("market_to_intrinsic_gap"))
        if gap is not None:
            if gap <= -0.2:
                signal = "DCF proxy is above the latest market price"
            elif gap >= 0.2:
                signal = "DCF proxy is below the latest market price"
            else:
                signal = "DCF proxy is close to the latest market price"
            signals.append(signal)
            factors.append({
                "symbol": symbol,
                "methodology": "DCF",
                "signal": signal,
                "value": gap,
                "confidence": dcf.get("confidence"),
                "basis": "market_to_intrinsic_gap",
                "warnings": list(dcf.get("warnings") or []),
            })

        resilience = views.get("cashflow_resilience") or {}
        resilience_outputs = resilience.get("outputs") if isinstance(resilience.get("outputs"), dict) else {}
        cashflow_state = str(resilience_outputs.get("cashflow_state") or "")
        debt_state = str(resilience_outputs.get("debt_capacity_state") or "")
        if cashflow_state or debt_state:
            signal = ", ".join(
                value for value in (
                    f"cashflow {cashflow_state}" if cashflow_state else "",
                    f"debt capacity {debt_state}" if debt_state else "",
                ) if value
            )
            signals.append(signal)
            factors.append({
                "symbol": symbol,
                "methodology": "Cashflow resilience",
                "signal": signal,
                "value": resilience_outputs.get("resilience_score"),
                "confidence": resilience.get("confidence"),
                "basis": "cashflow_state/debt_capacity_state",
                "warnings": list(resilience.get("warnings") or []),
            })

        comps = views.get("comps") or {}
        comps_outputs = comps.get("outputs") if isinstance(comps.get("outputs"), dict) else {}
        relative_view = str(comps_outputs.get("relative_view") or "")
        if relative_view:
            signals.append(f"relative view {relative_view}")
            factors.append({
                "symbol": symbol,
                "methodology": "Comps",
                "signal": relative_view,
                "value": comps_outputs.get("score_gap_vs_peer_median"),
                "confidence": comps.get("confidence"),
                "basis": "relative_view",
                "warnings": list(comps.get("warnings") or []),
            })

        if signals:
            summaries.append(f"{symbol}: " + "; ".join(signals))

    if not summaries:
        summary = "No methodology-based assessment was available from the collected evidence."
    else:
        summary = (
            "; ".join(summaries)
            + ". This is a preliminary, assumption-sensitive evidence view, not an investment recommendation."
        )
    return {"status": "preliminary", "summary": summary, "factors": factors}


def compose_screening_report(context):
    screening = context.require_artifact("screening_result")
    candidates = list(screening.content.get("screened_candidates") or [])
    watchlist = list(screening.content.get("watchlist_candidates") or [])
    scorecard = dict(screening.content.get("scorecard") or {})
    evidence = [
        {
            "evidence_id": f"screen-{index + 1}",
            "source_type": "screening_result",
            "title": f"{item.get('symbol') or 'candidate'} screening evidence",
            "summary": str(item.get("why_it_made_the_list") or "Candidate was ranked from available evidence."),
            "confidence": screening.confidence,
            "metadata": {
                "score": item.get("score"),
                "missing_evidence": list(item.get("missing_evidence") or []),
            },
        }
        for index, item in enumerate(candidates)
        if isinstance(item, dict)
    ]
    if candidates:
        conclusion = f"Screening produced {len(candidates)} core candidate(s) and {len(watchlist)} watchlist candidate(s)."
    else:
        conclusion = "No candidate met the evidence and scoring requirements for this screening universe."
    risks = [
        str(item.get("main_risk") or "Professional review is required before acting.")
        for item in candidates
        if isinstance(item, dict)
    ] or ["No eligible candidate was produced from the supplied universe."]
    return ArtifactDraft(
        artifact_type="research_report",
        content={
            "conclusion": conclusion,
            "evidence": evidence,
            "risk": risks,
            "follow_up": ["Refresh the candidate universe and verify material disclosures before acting."],
            "delivery_type": "stock_screen",
            "candidates": [*candidates, *watchlist],
            "screening": screening.content,
            "scorecard": scorecard,
            "screening_scorecard": scorecard,
            "methodologies": [dict(screening.content.get("methodology") or {})],
        },
        source_refs=list(screening.source_refs),
        parent_artifact_ids=[screening.artifact_id],
        confidence=screening.confidence,
        risk_flags=["decision_support_only", "screening_requires_professional_review"],
    )


def compose_sector_report(context):
    analysis = context.require_artifact("sector_analysis")
    sector = str(analysis.content.get("sector") or "the requested sector")
    findings = list(analysis.content.get("findings") or [])
    heatmap = list(analysis.content.get("sector_heatmap") or [])
    evidence = [
        _report_evidence_item(item, index)
        for index, item in enumerate(findings)
        if isinstance(item, dict)
    ]
    return ArtifactDraft(
        artifact_type="research_report",
        content={
            "conclusion": (
                f"Sector research assembled {len(findings)} traceable evidence item(s) and "
                f"{len(heatmap)} source-derived heatmap row(s) for {sector}."
            ),
            "evidence": evidence,
            "risk": list(analysis.content.get("risks") or ["Sector evidence requires refresh before acting."]),
            "follow_up": ["Review constituent, policy, and catalyst evidence before acting on the sector view."],
            "delivery_type": "sector_research",
            "sector_analysis": analysis.content,
            "sector_heatmap": heatmap,
        },
        source_refs=list(analysis.source_refs),
        parent_artifact_ids=[analysis.artifact_id],
        confidence=analysis.confidence,
        risk_flags=["decision_support_only", "sector_analysis_requires_professional_review"],
    )


def compose_thesis_review_report(context):
    review = context.require_artifact("thesis_review")
    verdict = str(review.content.get("overall_verdict") or review.content.get("verdict") or "inconclusive")
    findings = list(review.content.get("findings") or [])
    evidence = [
        {
            "evidence_id": f"thesis-{index + 1}",
            "source_type": "thesis_review",
            "title": str(item.get("claim") or item.get("artifact_type") or "Thesis finding"),
            "summary": str(item.get("summary") or item.get("verdict") or "Review finding"),
            "confidence": review.confidence,
            "metadata": {"verdict": item.get("verdict")},
        }
        for index, item in enumerate(findings)
        if isinstance(item, dict)
    ]
    weaknesses = [
        str(item.get("summary") or item.get("claim") or item.get("verdict") or "Unresolved thesis weakness")
        if isinstance(item, dict)
        else str(item)
        for item in review.content.get("weaknesses") or []
    ]
    return ArtifactDraft(
        artifact_type="research_report",
        content={
            "conclusion": f"Thesis review verdict: {verdict}.",
            "evidence": evidence,
            "risk": weaknesses or ["Independent evidence remains limited."],
            "follow_up": list(review.content.get("follow_up") or []),
            "delivery_type": "review_thesis",
            "thesis_review": review.content,
            "review_summary": {
                "overall_verdict": verdict,
                "review_target_run_id": review.content.get("review_target_run_id"),
                "review_target_found": review.content.get("review_target_found"),
                "target_artifact_count": len(review.content.get("review_target_artifact_ids") or []),
                "fresh_evidence_count": dict(review.content.get("evidence_comparison") or {}).get("fresh_evidence_count"),
            },
        },
        source_refs=list(review.source_refs),
        parent_artifact_ids=[review.artifact_id],
        confidence=review.confidence,
        risk_flags=["decision_support_only", "thesis_requires_professional_review"],
    )


def _snapshots_by_symbol(rows: list[object]) -> dict[str, dict[str, dict]]:
    grouped: dict[str, dict[str, dict]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip()
        snapshot_type = str(row.get("snapshot_type") or "").strip()
        if symbol and snapshot_type:
            grouped.setdefault(symbol, {})[snapshot_type] = row
    return grouped


def _analysis_candidate(*, symbol: str, snapshots: dict[str, dict]) -> dict:
    market_snapshot = snapshots.get("market") or {}
    fundamental_snapshot = snapshots.get("fundamental") or {}
    market = _market_overview(market_snapshot)
    fundamental_facts = _facts(fundamental_snapshot)
    banking_metrics = fundamental_facts.get("banking_metrics")
    return {
        "symbol": symbol,
        "name": str(market.get("name") or symbol),
        "sector": str(market.get("sector") or ""),
        "latest_price": _number(market.get("latest_price")),
        "latest_pct_change": _number(market.get("pct_change")),
        "latest_amount": _number(market.get("amount")),
        "pb": _number(market.get("pb")),
        "pe_ttm": _number(market.get("pe_ttm")),
        "market_cap": _number(market.get("market_cap")),
        "float_market_cap": _number(market.get("float_market_cap")),
        "market_total_shares": _number(market.get("total_shares")),
        "revenue_proxy": _revenue_proxy(fundamental_snapshot),
        "banking_metrics": dict(banking_metrics) if isinstance(banking_metrics, dict) else {},
    }


def _bank_compare_scorecard(candidates: list[dict]) -> dict | None:
    banks = [candidate for candidate in candidates if _is_bank_candidate(candidate)]
    if len(banks) < 2:
        return None
    metric_series = {
        metric: [_number(_bank_metrics(candidate).get(metric)) for candidate in banks]
        for metric in _BANK_METRIC_WEIGHTS
    }
    rows: list[dict] = []
    for candidate in banks:
        metrics = _bank_metrics(candidate)
        score = 0.0
        weight_used = 0.0
        breakdown: dict[str, float | None] = {}
        for metric, weight in _BANK_METRIC_WEIGHTS.items():
            value = _number(metrics.get(metric))
            breakdown[metric] = value
            series = [item for item in metric_series[metric] if item is not None]
            if value is None or len(series) < 2:
                continue
            percentile = _bank_percentile(
                value=value,
                series=series,
                preference=_BANK_METRIC_DIRECTION[metric],
            )
            if percentile is not None:
                score += percentile * weight
                weight_used += weight
        rows.append(
            {
                "symbol": candidate["symbol"],
                "name": candidate["name"],
                "score": round(score / weight_used, 4) if weight_used else 0.0,
                "weight_coverage": round(weight_used, 4),
                "latest_price": candidate.get("latest_price"),
                "pb": candidate.get("pb"),
                "pe_ttm": candidate.get("pe_ttm"),
                "banking_metrics": breakdown,
                "missing_metrics": sorted(key for key, value in breakdown.items() if value is None),
            }
        )
    rows.sort(key=lambda row: (row["score"], row["weight_coverage"]), reverse=True)
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    return {
        "scorecard_type": "bank_compare_scorecard",
        "candidate_count": len(rows),
        "ranked_candidates": rows,
        "metric_weights": _BANK_METRIC_WEIGHTS,
        "summary": "Bank comparison ranks peers using available same-basis banking metrics and valuation anchors.",
    }


def _bank_metrics(candidate: dict) -> dict:
    metrics = dict(candidate.get("banking_metrics") or {})
    metrics["pb"] = _number(candidate.get("pb"))
    latest_price = _number(candidate.get("latest_price"))
    total_shares = _number(candidate.get("market_total_shares"))
    dividend_cash = _number(metrics.get("pay_dividends_profits_interest_cash"))
    if dividend_cash not in (None, 0) and latest_price not in (None, 0) and total_shares not in (None, 0):
        metrics["dividend_yield_proxy"] = dividend_cash / total_shares / latest_price
    return metrics


def _is_bank_candidate(candidate: dict) -> bool:
    metrics = dict(candidate.get("banking_metrics") or {})
    return bool(metrics.get("is_bank_like")) or any(
        key in metrics
        for key in ("roe", "nonperforming_loan_ratio", "net_interest_spread", "capital_adequacy_ratio")
    )


def _bank_percentile(*, value: float, series: list[float], preference: str) -> float | None:
    if not series:
        return None
    percentile = sum(1 for item in sorted(series) if item <= value) / len(series)
    return round(1.0 - percentile if preference == "lower" else percentile, 4)


def _analysis_summary(*, methodologies: list[dict], scorecards: list[dict]) -> str:
    if not methodologies:
        return "No methodology model could be built because financial snapshots were unavailable."
    symbols = {item.get("target_symbol") for item in methodologies}
    summary = f"Built {len(methodologies)} methodology view(s) across {len(symbols)} symbol(s)."
    if scorecards:
        summary += f" {scorecards[0]['summary']}"
    return summary


def _analysis_confidence(methodologies: list[dict]) -> float:
    values = [_number(item.get("confidence")) for item in methodologies if isinstance(item, dict)]
    usable = [value for value in values if value is not None]
    return round(sum(usable) / len(usable), 4) if usable else 0.2


def _artifact_confidence(value: object) -> float:
    numeric = _number(value)
    return numeric if numeric is not None else 0.2


def _methodology_warnings(methodologies: list[dict]) -> list[str]:
    warnings: list[str] = []
    for methodology in methodologies:
        if not isinstance(methodology, dict):
            continue
        for warning in list(methodology.get("warnings") or []):
            text = str(warning)
            if text and text not in warnings:
                warnings.append(text)
    return warnings


def _investment_cycle(inputs: dict) -> str:
    value = str(inputs.get("investment_cycle") or "mid_term")
    return value if value in {"short_term", "mid_term", "long_term"} else "mid_term"


def _facts(snapshot: dict) -> dict:
    facts = snapshot.get("facts") if isinstance(snapshot.get("facts"), dict) else {}
    return facts


def _market_overview(snapshot: dict) -> dict:
    overview = _facts(snapshot).get("market_overview")
    return overview if isinstance(overview, dict) else {}


def _revenue_proxy(snapshot: dict) -> float | None:
    facts = _facts(snapshot)
    banking = facts.get("banking_metrics") if isinstance(facts.get("banking_metrics"), dict) else {}
    for key in ("interest_income", "deposits_and_deposits", "customer_loan_advance_net_addition"):
        value = _number(banking.get(key))
        if value is not None:
            return value
    abstract = facts.get("financial_abstract") if isinstance(facts.get("financial_abstract"), dict) else {}
    for key, value in abstract.items():
        if "revenue" in str(key).lower() or "income" in str(key).lower():
            numeric = _number(value)
            if numeric is not None:
                return numeric
    return None


def _number(value: object) -> float | None:
    return coerce_numeric(value)


def _requested_snapshot_types(*, inputs: dict, sector: str | None) -> list[str]:
    explicit = inputs.get("snapshot_types")
    if isinstance(explicit, list) and explicit:
        return _unique_strings(explicit)
    intent = str(inputs.get("intent") or "").strip()
    if intent == "sector_research":
        return [
            "sector",
            *(["policy"] if inputs.get("include_policy_snapshot") is not False else []),
        ]
    if intent == "stock_screen":
        return ["sector", "market", "fundamental", "notice"] if sector else ["market", "fundamental", "notice"]
    return ["market", "fundamental", "notice", "capital_flow", "sentiment", "events", "lhb"]


def _universe_from_snapshots(snapshots: list[object], symbols: list[str]) -> list[dict]:
    names: dict[str, str] = {}
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        symbol = str(snapshot.get("symbol") or "").strip()
        if not symbol:
            continue
        facts = snapshot.get("facts") if isinstance(snapshot.get("facts"), dict) else {}
        info = facts.get("info") if isinstance(facts.get("info"), dict) else {}
        names[symbol] = str(
            snapshot.get("name")
            or info.get("股票简称")
            or info.get("名称")
            or names.get(symbol)
            or symbol
        )
    return [
        {"symbol": symbol, "name": names.get(symbol, symbol), "source": "snapshot_bundle"}
        for symbol in symbols
    ]


def _collect_provider_snapshots(
    *,
    query: str,
    symbols: list[str],
    snapshot_types: list[str],
    sector: str | None,
    cancellation,
) -> list[dict]:
    return collect_snapshots(
        query=query,
        symbols=symbols,
        snapshot_types=snapshot_types,
        sector=sector,
        cancellation=cancellation,
    )


def _collect_missing_snapshot_types(
    *,
    query: str,
    symbols: list[str],
    sector: str | None,
    snapshots: list[object],
    requested_types: list[str],
    cancellation,
) -> list[object]:
    available = {
        (str(row.get("snapshot_type") or ""), str(row.get("symbol") or ""))
        for row in snapshots
        if isinstance(row, dict)
    }
    missing = [
        snapshot_type
        for snapshot_type in requested_types
        if snapshot_type in {"sector", "policy"}
        and (snapshot_type, "") not in available
        or snapshot_type not in {"sector", "policy"}
        and any((snapshot_type, symbol) not in available for symbol in symbols)
    ]
    if not missing:
        return snapshots
    return [
        *snapshots,
        *_collect_provider_snapshots(
            query=query,
            symbols=symbols,
            snapshot_types=missing,
            sector=sector,
            cancellation=cancellation,
        ),
    ]


def _snapshot_source_refs(snapshots: list[object]) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    seen: set[str] = set()
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        source = snapshot.get("source") if isinstance(snapshot.get("source"), dict) else {}
        symbol = str(snapshot.get("symbol") or "unknown")
        snapshot_type = str(snapshot.get("snapshot_type") or "snapshot")
        provider = str(source.get("provider_id") or source.get("source_id") or "financial_data_provider")
        reference_id = f"{provider}:{snapshot_type}:{symbol}"
        if reference_id in seen:
            continue
        seen.add(reference_id)
        refs.append({"type": "financial_data_provider", "id": reference_id})
    return refs


def _report_evidence_item(item: object, index: int) -> dict:
    snapshot = item if isinstance(item, dict) else {"value": item}
    snapshot_type = str(snapshot.get("snapshot_type") or "snapshot")
    symbol = str(snapshot.get("symbol") or "unknown")
    source = snapshot.get("source") if isinstance(snapshot.get("source"), dict) else {}
    facts = snapshot.get("facts") if isinstance(snapshot.get("facts"), dict) else {}
    degraded = bool(snapshot.get("degraded"))
    return {
        "evidence_id": f"{symbol}-{snapshot_type}-{index + 1}",
        "source_type": snapshot_type,
        "source_name": str(source.get("vendor") or source.get("source_id") or "financial_data_provider"),
        "title": f"{symbol} {snapshot_type} snapshot",
        "summary": _snapshot_summary(symbol=symbol, snapshot_type=snapshot_type, facts=facts, degraded=degraded),
        "published_at": snapshot.get("snapshot_date"),
        "confidence": 0.45 if degraded else 0.8,
        "metadata": {
            "degraded": degraded,
            "degrade_reason": snapshot.get("degrade_reason"),
            "missing_fields": list(snapshot.get("missing_fields") or []),
            "source_trace": list(snapshot.get("source_trace") or []),
        },
    }


def _snapshot_summary(*, symbol: str, snapshot_type: str, facts: dict, degraded: bool) -> str:
    if degraded:
        return f"{symbol} {snapshot_type} data source was unavailable."
    if snapshot_type == "market":
        overview = facts.get("market_overview") if isinstance(facts.get("market_overview"), dict) else {}
        values = [
            ("price", overview.get("latest_price")),
            ("change", overview.get("pct_change")),
            ("PE(TTM)", overview.get("pe_ttm")),
            ("PB", overview.get("pb")),
        ]
        metrics = [f"{label}={value}" for label, value in values if value is not None]
        return f"{symbol} market snapshot" + (f" ({', '.join(metrics)})" if metrics else "")
    if snapshot_type == "fundamental":
        abstract = facts.get("financial_abstract") if isinstance(facts.get("financial_abstract"), dict) else {}
        return f"{symbol} fundamental snapshot with {len(abstract)} reported financial fields"
    return f"{symbol} {snapshot_type} snapshot"


def _unique_strings(values: object) -> list[str]:
    if not isinstance(values, list):
        values = [values]
    result: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def screen_stocks(context):
    snapshots = context.require_artifact("stock_snapshot_bundle")
    inputs = dict(context.node.inputs or {})
    rows = list(snapshots.content.get("snapshots") or [])
    universe = {
        str(row.get("symbol") or ""): dict(row)
        for row in snapshots.content.get("candidate_universe") or []
        if isinstance(row, dict) and row.get("symbol")
    }
    collection = dict(snapshots.content.get("collection") or {})
    enrichment_target_symbols = _unique_strings(collection.get("enrichment_targets") or [])
    enrichment_types = _unique_strings(collection.get("enrichment_snapshot_types") or [])
    grouped: dict[str, dict[str, dict]] = {}
    for row in rows:
        if not isinstance(row, dict) or not row.get("symbol"):
            continue
        grouped.setdefault(str(row["symbol"]), {})[str(row.get("snapshot_type") or "unknown")] = row
    requested_style = str(
        inputs.get("screen_style") or inputs.get("screening_style") or "quality"
    ).strip().lower()
    screen_style = requested_style if requested_style in _SCREEN_STYLE_CRITERIA else "quality"
    candidates = []
    for symbol, by_type in grouped.items():
        market = ((by_type.get("market") or {}).get("facts") or {}).get("market_overview") or {}
        spot = market.get("spot") or {}
        fundamental = ((by_type.get("fundamental") or {}).get("facts") or {}).get("financial_abstract") or {}
        notice = ((by_type.get("notice") or {}).get("facts") or {})
        universe_row = universe.get(symbol) or {}
        pct_change = _to_float(
            market.get("pct_change")
            if market.get("pct_change") is not None
            else spot.get("pct_change") or spot.get("涨跌幅") or universe_row.get("pct_change")
        )
        amount = _to_float(
            market.get("amount")
            if market.get("amount") is not None
            else spot.get("amount") or spot.get("成交额") or universe_row.get("amount")
        )
        notice_count = int(_to_float(notice.get("notice_count")) or 0)
        missing = [
            label for label, present in (
                ("market", bool(by_type.get("market"))),
                ("fundamental", bool(by_type.get("fundamental"))),
                ("notice", bool(by_type.get("notice"))),
            ) if not present
        ]
        methodology = _candidate_methodology(
            screen_style=screen_style,
            market=market,
            fundamental=fundamental,
            notice=notice,
            missing_evidence=missing,
        )
        breakdown = {
            "liquidity": min((amount or 0.0) / 1_000_000_000, 1.0),
            "momentum": max(min((pct_change or 0.0) / 10.0, 1.0), -1.0),
            "fundamental": 1.0 if fundamental else 0.0,
            "notice": min(notice_count * 0.1, 0.5),
            "enrichment_coverage": round(
                0.05
                * sum(
                    1
                    for snapshot_type in enrichment_types
                    if by_type.get(snapshot_type) and not by_type[snapshot_type].get("degraded")
                ),
                3,
            ),
            "evidence_penalty": -0.75 * len(missing),
        }
        score = round(sum(breakdown.values()), 3)
        degraded_sources = [
            snapshot_type
            for snapshot_type, snapshot in by_type.items()
            if isinstance(snapshot, dict) and snapshot.get("degraded")
        ]
        deep_checked = bool(enrichment_types) and all(
            by_type.get(snapshot_type) and not by_type[snapshot_type].get("degraded")
            for snapshot_type in enrichment_types
        )
        available_types = sorted(by_type)
        candidates.append({
            "symbol": symbol,
            "name": str(
                universe_row.get("name")
                or (by_type.get("market") or {}).get("name")
                or symbol
            ),
            "latest_price": _to_float(
                market.get("latest_price")
                if market.get("latest_price") is not None
                else spot.get("latest_price") or spot.get("最新价") or universe_row.get("latest_price")
            ),
            "latest_pct_change": pct_change,
            "latest_amount": amount,
            "notice_count": notice_count,
            "has_financial_abstract": bool(fundamental),
            "sector": str(
                market.get("sector")
                or fundamental.get("sector")
                or (by_type.get("market") or {}).get("sector")
                or ""
            ),
            "score": score,
            "score_breakdown": breakdown,
            "missing_evidence": missing,
            "degraded_sources": degraded_sources,
            "deep_checked": deep_checked,
            "enrichment_requested": symbol in enrichment_target_symbols,
            "evidence_snapshot_types": available_types,
            "why_it_made_the_list": (
                f"Ranked from {len(available_types)} provider-backed snapshot type(s): "
                + ", ".join(available_types)
                + "."
            ),
            "main_risk": (
                "; ".join(
                    [*(f"missing evidence: {item}" for item in missing), *(f"degraded source: {item}" for item in degraded_sources)]
                )
                or "Requires professional review before acting."
            ),
            "methodology": methodology,
        })
    candidates.sort(key=lambda item: (item["score"], item.get("latest_amount") or 0.0), reverse=True)
    cycle = str(inputs.get("investment_cycle") or "mid_term")
    default_min_score = {"short_term": 0.0, "mid_term": 0.0, "long_term": 0.15}.get(cycle, 0.0)
    min_score = _to_float(inputs.get("min_score"))
    min_score = default_min_score if min_score is None else min_score
    top_k = max(int(inputs.get("top_k") or 5), 1)
    watchlist_top_k = max(int(inputs.get("watchlist_top_k") or 2), 1)
    watchlist_min_score = _to_float(inputs.get("watchlist_min_score"))
    watchlist_min_score = min_score - 0.75 if watchlist_min_score is None else watchlist_min_score
    screened = [item for item in candidates if item["score"] >= min_score][:top_k]
    watchlist = [item for item in candidates if item not in screened and item["score"] >= watchlist_min_score][:watchlist_top_k]
    for rank, item in enumerate(candidates, start=1):
        item["rank"] = rank
    for item in screened:
        item["candidate_tier"] = "core"
    for item in watchlist:
        item["candidate_tier"] = "watchlist"
    scorecard = {
        "investment_cycle": cycle,
        "investment_cycle_assumption": "explicit" if inputs.get("investment_cycle") else "default_mid_term",
        "candidate_count": len(candidates),
        "eligible_candidate_count": len(screened),
        "top_candidate_count": len(screened),
        "watchlist_candidate_count": len(watchlist),
        "deep_checked_count": sum(1 for item in screened if item["deep_checked"]),
        "watchlist_deep_checked_count": sum(1 for item in watchlist if item["deep_checked"]),
        "enrichment_target_count": len(enrichment_target_symbols),
        "candidate_universe_source": collection.get("universe_source"),
        "requested_snapshot_types": list(collection.get("requested_snapshot_types") or []),
        "collected_snapshot_types": list(collection.get("collected_snapshot_types") or []),
        "preferred_methodology": str(inputs.get("prefer_methodology") or "comps"),
        "screening_strategy": {
            "methodology_source": "intent_input" if inputs.get("prefer_methodology") else "domain_pack_policy",
            "investment_cycle": cycle,
            "screen_style": screen_style,
        },
        "summary": f"Screened {len(candidates)} candidates; {len(screened)} eligible.",
    }
    return ArtifactDraft(
        artifact_type="screening_result",
        content={
            "screened_candidates": screened,
            "watchlist_candidates": watchlist,
            "enrichment_targets": enrichment_target_symbols,
            "scorecard": scorecard,
            "screening_notes": [
                "screening_strategy=domain_pack_policy",
                "missing evidence is retained as a data gap and is never neutralized",
                f"candidate_universe_source={collection.get('universe_source') or 'unknown'}",
                f"enrichment_targets={len(enrichment_target_symbols)}",
            ],
            "methodology": {
                "screen_style": screen_style,
                "requested_screen_style": requested_style,
                "criteria": [
                    {"id": criterion_id, "label": label}
                    for criterion_id, label, _fields in _SCREEN_STYLE_CRITERIA[screen_style]
                ],
            },
        },
        source_refs=list(snapshots.source_refs),
        parent_artifact_ids=[snapshots.artifact_id],
        confidence=snapshots.confidence,
        risk_flags=["screening_requires_fresh_market_data"],
    )


def _to_float(value):
    try:
        return None if value in (None, "", False) else float(value)
    except (TypeError, ValueError):
        return None


def _candidate_methodology(*, screen_style, market, fundamental, notice, missing_evidence):
    values = {
        "amount": market.get("amount") or (market.get("spot") or {}).get("amount"),
        "pct_change": market.get("pct_change") or (market.get("spot") or {}).get("pct_change"),
        "notice_count": notice.get("notice_count"),
        "financial_abstract": fundamental,
        **{str(key): value for key, value in market.items()},
        **{str(key): value for key, value in fundamental.items()},
    }
    criteria = []
    for criterion_id, label, field_names in _SCREEN_STYLE_CRITERIA[screen_style]:
        matched = next(
            (field_name for field_name in field_names if _has_reported_value(values.get(field_name))),
            None,
        )
        criteria.append(
            {
                "id": criterion_id,
                "label": label,
                "status": "pass" if matched else "missing",
                "source_field": matched,
            }
        )
    criteria.append(
        {
            "id": "evidence_completeness",
            "label": "market, fundamental, and disclosure coverage",
            "status": "pass" if not missing_evidence else "fail",
            "source_field": None if missing_evidence else "artifact_bundle",
        }
    )
    evaluated = [item for item in criteria if item["status"] != "missing"]
    passed = [item for item in evaluated if item["status"] == "pass"]
    return {
        "screen_style": screen_style,
        "criteria": criteria,
        "evaluated_criterion_count": len(evaluated),
        "passed_criterion_count": len(passed),
        "pass_rate": round(len(passed) / len(evaluated), 3) if evaluated else None,
        "data_gaps": [item["id"] for item in criteria if item["status"] == "missing"],
    }


def _has_reported_value(value):
    return value not in (None, "", [], {})


def research_sector(context):
    snapshots = context.require_artifact("stock_snapshot_bundle")
    content = snapshots.content
    inputs = dict(context.node.inputs or {})
    rows = [row for row in list(content.get("snapshots") or []) if isinstance(row, dict)]
    sector_rows = [row for row in rows if row.get("snapshot_type") == "sector"]
    policy_rows = [row for row in rows if row.get("snapshot_type") == "policy"]
    sector_snapshot = sector_rows[0] if sector_rows else {}
    facts = sector_snapshot.get("facts") or {}
    sector = inputs.get("sector") or content.get("sector") or sector_snapshot.get("sector") or facts.get("sector") or content.get("query")
    theme = content.get("theme") or sector_snapshot.get("theme") or facts.get("theme")
    related = [row for row in rows if row not in sector_rows]
    constituents = [
        dict(row)
        for row in facts.get("constituent_breadth_source") or facts.get("top_constituents") or []
        if isinstance(row, dict)
    ]
    sector_heatmap = _sector_heatmap(
        sector=sector,
        theme=theme,
        facts=facts,
        constituents=constituents,
        source_count=len(rows),
        related_count=len(related),
    )
    include_policy = inputs.get("include_policy_snapshot") is not False
    risks = ["Sector evidence may be incomplete or stale."]
    if not related:
        risks.append("No related sector or constituent snapshots were supplied.")
    if include_policy and not policy_rows:
        risks.append("The requested policy snapshot was unavailable.")
    return ArtifactDraft(
        artifact_type="sector_analysis",
        content={
            "sector": sector,
            "theme": theme,
            "findings": rows,
            "related_snapshots": related,
            "risks": risks,
            "source_count": len(rows),
            "analysis_notes": [
                "sector_snapshot_present" if sector_rows else "missing_sector_snapshot",
                "policy_snapshot_requested" if include_policy else "policy_snapshot_not_requested",
                "policy_snapshot_present" if policy_rows else "missing_policy_snapshot",
            ],
            "sector_heatmap": sector_heatmap,
            "constituents": constituents,
            "breadth": dict(facts.get("breadth") or {}),
            "relative_strength": dict(facts.get("relative_strength") or {}),
            "signal_scorecard": dict(facts.get("signal_scorecard") or {}),
            "catalysts": {
                "theme_news": list(facts.get("theme_news") or []),
                "policy_signals": list(facts.get("policy_signals") or []),
                "regulatory_signals": list(facts.get("regulatory_signals") or []),
                "samples": list(facts.get("catalyst_samples") or []),
            },
            "policy_snapshots": policy_rows,
        },
        source_refs=list(snapshots.source_refs),
        parent_artifact_ids=[snapshots.artifact_id],
        confidence=snapshots.confidence,
        risk_flags=["sector_analysis_requires_source_refresh"],
    )


def _sector_heatmap(
    *,
    sector: object,
    theme: object,
    facts: dict,
    constituents: list[dict],
    source_count: int,
    related_count: int,
) -> list[dict]:
    rows: list[dict] = []
    latest_hist = facts.get("latest_hist") if isinstance(facts.get("latest_hist"), dict) else {}
    sector_change = _to_float(latest_hist.get("pct_change"))
    source_fact_score = _to_float(
        facts.get("research_heat") or facts.get("heat_score") or facts.get("evidence_coverage")
    )
    if sector:
        sector_row = {
            "name": str(sector),
            "score": source_fact_score if source_fact_score is not None else _normalized_change_score(sector_change),
            "score_basis": "source_fact" if source_fact_score is not None else "sector_latest_pct_change",
            "source_count": source_count,
            "related_snapshot_count": related_count,
            "theme": theme,
        }
        if source_fact_score is None:
            sector_row["raw_value"] = sector_change
        rows.append(sector_row)
    ranked = sorted(
        constituents,
        key=lambda row: _to_float(row.get("pct_change")) if _to_float(row.get("pct_change")) is not None else -10_000.0,
        reverse=True,
    )
    for constituent in ranked[:6]:
        change = _to_float(constituent.get("pct_change"))
        rows.append(
            {
                "name": str(constituent.get("name") or constituent.get("code") or "constituent"),
                "symbol": str(constituent.get("code") or constituent.get("symbol") or ""),
                "score": _normalized_change_score(change),
                "raw_value": change,
                "score_basis": "constituent_latest_pct_change",
                "source_count": 1,
                "theme": theme,
            }
        )
    return rows


def _normalized_change_score(value: float | None) -> float:
    if value is None:
        return 50.0
    return round(max(0.0, min(100.0, 50.0 + value * 5.0)), 2)


def review_thesis(context):
    evidence = context.require_artifact("evidence_set")
    inputs = dict(context.node.inputs or {})
    items = list(evidence.content.get("items") or [])
    review_target_run_id = str(inputs.get("review_target_run_id") or "").strip() or None
    target_artifacts = _review_target_artifacts(context, review_target_run_id)
    target_findings = _target_findings(target_artifacts)
    current_findings = _current_evidence_findings(items)
    findings = [*target_findings, *current_findings]
    explicit_verdicts = [
        str(item.get("verdict") or "").lower()
        for item in items
        if isinstance(item, dict) and str(item.get("verdict") or "").lower() in {"confirmed", "rejected"}
    ]
    if review_target_run_id and not target_artifacts:
        overall_verdict = "target_not_found"
    elif not items:
        overall_verdict = "insufficient_fresh_evidence"
    elif explicit_verdicts and all(item == "confirmed" for item in explicit_verdicts):
        overall_verdict = "confirmed"
    elif explicit_verdicts and all(item == "rejected" for item in explicit_verdicts):
        overall_verdict = "rejected"
    else:
        overall_verdict = "inconclusive"
    strengths = [
        item
        for item in findings
        if isinstance(item, dict) and str(item.get("verdict") or "").lower() in {"confirmed", "available"}
    ]
    weaknesses = [
        item
        for item in findings
        if isinstance(item, dict) and str(item.get("verdict") or "").lower() in {"rejected", "degraded", "unverified"}
    ]
    source_refs = [*list(evidence.source_refs)]
    if review_target_run_id:
        source_refs.append({"type": "execution_run", "id": review_target_run_id})
    source_refs = _unique_source_refs(source_refs)
    return ArtifactDraft(
        artifact_type="thesis_review",
        content={
            "verdict": overall_verdict,
            "overall_verdict": overall_verdict,
            "findings": findings,
            "strengths": strengths,
            "weaknesses": weaknesses or (["No independently verified thesis evidence was supplied."] if not items else []),
            "review_target_run_id": review_target_run_id,
            "review_target_artifact_ids": [artifact.artifact_id for artifact in target_artifacts],
            "review_target_found": bool(target_artifacts),
            "target_thesis": target_findings,
            "evidence_comparison": {
                "target_artifact_count": len(target_artifacts),
                "fresh_evidence_count": len(items),
                "fresh_snapshot_types": list(
                    dict.fromkeys(
                        str(item.get("snapshot_type") or "")
                        for item in items
                        if isinstance(item, dict) and item.get("snapshot_type")
                    )
                ),
                "fresh_symbols": list(
                    dict.fromkeys(
                        str(item.get("symbol") or "")
                        for item in items
                        if isinstance(item, dict) and item.get("symbol")
                    )
                ),
            },
            "follow_up": [
                "Refresh filings, valuation, and risk evidence before acting.",
                *([f"Review focus: {inputs['review_focus']}"] if inputs.get("review_focus") else []),
            ],
        },
        source_refs=source_refs,
        parent_artifact_ids=[evidence.artifact_id],
        confidence=min(_artifact_confidence(evidence.confidence), 0.85 if target_artifacts else 0.4),
        risk_flags=[
            "decision_support_only",
            *(["review_target_not_found"] if review_target_run_id and not target_artifacts else []),
            *(["fresh_evidence_missing"] if not items else []),
        ],
    )


def _review_target_artifacts(context, review_target_run_id: str | None):
    board = getattr(context, "artifact_board", None)
    if (
        not review_target_run_id
        or not _SAFE_RUN_ID_PATTERN.fullmatch(review_target_run_id)
        or board is None
    ):
        return []
    current_run_id = str(getattr(context, "run_id", "") or "")
    if review_target_run_id == current_run_id:
        return []
    target_path = board.path.parent.parent / review_target_run_id / "artifact_board.jsonl"
    if not target_path.is_file():
        return []
    return [
        artifact
        for artifact in ArtifactBoard(target_path).list_artifacts(thread_id=None)
        if artifact.status == "active"
        and artifact.artifact_type in {
            "research_report",
            "stock_snapshot_bundle",
            "evidence_set",
            "financial_analysis",
            "sector_analysis",
            "screening_result",
            "thesis_review",
        }
    ]


def _target_symbols(artifacts) -> list[str]:
    symbols: list[str] = []
    for artifact in artifacts:
        content = artifact.content if isinstance(artifact.content, dict) else {}
        candidates: list[object] = [*(content.get("symbols") or [])]
        for snapshot in content.get("snapshots") or content.get("items") or []:
            if isinstance(snapshot, dict):
                candidates.append(snapshot.get("symbol"))
        for key in ("screened_candidates", "watchlist_candidates"):
            for candidate in content.get(key) or []:
                if isinstance(candidate, dict):
                    candidates.append(candidate.get("symbol") or candidate.get("code"))
        screening = content.get("screening") if isinstance(content.get("screening"), dict) else {}
        for key in ("screened_candidates", "watchlist_candidates"):
            for candidate in screening.get(key) or []:
                if isinstance(candidate, dict):
                    candidates.append(candidate.get("symbol") or candidate.get("code"))
        for value in candidates:
            symbol = str(value or "").strip()
            if _STOCK_CODE_PATTERN.fullmatch(symbol) and symbol not in symbols:
                symbols.append(symbol)
    return symbols


def _target_findings(artifacts) -> list[dict]:
    findings: list[dict] = []
    for artifact in artifacts:
        content = artifact.content if isinstance(artifact.content, dict) else {}
        summary = str(
            content.get("conclusion")
            or content.get("summary")
            or content.get("verdict")
            or "Prior run artifact available for review."
        )
        findings.append(
            {
                "claim": f"Prior {artifact.artifact_type}",
                "artifact_id": artifact.artifact_id,
                "artifact_type": artifact.artifact_type,
                "summary": summary,
                "verdict": "prior_claim",
            }
        )
    return findings


def _current_evidence_findings(items: list[object]) -> list[dict]:
    findings: list[dict] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        explicit = str(item.get("verdict") or "").strip().lower()
        if explicit:
            findings.append(dict(item))
            continue
        snapshot_type = str(item.get("snapshot_type") or "evidence")
        symbol = str(item.get("symbol") or "target")
        facts = item.get("facts") if isinstance(item.get("facts"), dict) else {}
        findings.append(
            {
                "claim": f"Fresh {symbol} {snapshot_type} evidence",
                "artifact_type": snapshot_type,
                "summary": _snapshot_summary(
                    symbol=symbol,
                    snapshot_type=snapshot_type,
                    facts=facts,
                    degraded=bool(item.get("degraded")),
                ),
                "verdict": "degraded" if item.get("degraded") else "available",
                "evidence_index": index,
            }
        )
    return findings


def _unique_source_refs(source_refs: list[dict]) -> list[dict]:
    result: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for source_ref in source_refs:
        source_type = str(source_ref.get("type") or "")
        source_id = str(source_ref.get("id") or "")
        key = (source_type, source_id)
        if source_type and source_id and key not in seen:
            seen.add(key)
            result.append(source_ref)
    return result


def collect_distillation_sources(context):
    query = context.require_artifact("normalized_query")
    inputs = dict(context.node.inputs or {})
    raw_records = (
        inputs.get("source_records")
        or inputs.get("records")
        or inputs.get("statuses")
        or inputs.get("items")
        or []
    )
    if isinstance(raw_records, dict):
        raw_records = [raw_records]
    records = []
    source_refs = []
    for index, raw in enumerate(raw_records):
        row = dict(raw) if isinstance(raw, dict) else {"text": str(raw)}
        record_id = str(row.get("record_id") or row.get("id") or f"record-{index + 1}")
        text = str(row.get("text") or row.get("content") or row.get("body") or "").strip()
        if not text:
            continue
        records.append(
            {
                "record_id": record_id,
                "text": text,
                "author": row.get("author") or row.get("user_name"),
                "published_at": row.get("published_at") or row.get("created_at"),
                "tags": list(row.get("tags") or []),
                "dimensions": list(row.get("dimensions") or []),
            }
        )
        source_refs.append({"type": "source_record", "id": record_id})
    if not records and query.content.get("query"):
        records.append(
            {
                "record_id": "request-query",
                "text": str(query.content["query"]),
                "author": None,
                "published_at": None,
                "tags": [],
                "dimensions": [],
            }
        )
        source_refs.append({"type": "user_request", "id": query.artifact_id})
    return ArtifactDraft(
        artifact_type="source_record_set",
        content={"query": str(query.content.get("query") or ""), "records": records},
        source_refs=source_refs,
        parent_artifact_ids=[query.artifact_id],
        confidence=1.0 if raw_records else 0.6,
        risk_flags=[] if raw_records else ["source_records_missing"],
    )


def distill_kol_logic(context):
    source_set = context.require_artifact("source_record_set")
    records = list(source_set.content.get("records") or [])
    dimensions = []
    for record in records:
        if not isinstance(record, dict):
            continue
        text = str(record.get("text") or "").strip()
        labels = list(record.get("dimensions") or record.get("tags") or [])
        label = str(labels[0] if labels else "investment_logic")
        dimensions.append(
            {
                "dimension_name": label,
                "category": "finance_research",
                "claim": text,
                "source_record_ids": [str(record.get("record_id") or "unknown")],
                "confidence": 0.75 if labels else 0.6,
                "attribution": record.get("author"),
            }
        )
    major_risks = []
    if not records:
        major_risks.append("no_source_records")
    if any(not item.get("attribution") for item in dimensions):
        major_risks.append("missing_attribution")
    return ArtifactDraft(
        artifact_type="distillation_result",
        content={
            "dimensions": dimensions,
            "summary": {
                "source_record_count": len(records),
                "dimension_count": len(dimensions),
                "major_risks": major_risks,
                "method": "domain_pack_structured_distillation",
            },
        },
        source_refs=list(source_set.source_refs),
        parent_artifact_ids=[source_set.artifact_id],
        confidence=min((item["confidence"] for item in dimensions), default=0.0),
        risk_flags=major_risks,
    )


def review_distillation(context):
    distillation = context.require_artifact("distillation_result")
    dimensions = list(distillation.content.get("dimensions") or [])
    findings = []
    for item in dimensions:
        if not isinstance(item, dict):
            continue
        if not item.get("attribution"):
            findings.append(
                {
                    "finding_type": "missing_attribution",
                    "severity": "medium",
                    "dimension_name": item.get("dimension_name"),
                    "reason": "The distilled claim has no attributable author.",
                }
            )
        if float(item.get("confidence") or 0.0) < 0.7:
            findings.append(
                {
                    "finding_type": "low_confidence",
                    "severity": "medium",
                    "dimension_name": item.get("dimension_name"),
                    "reason": "The distilled claim requires stronger source evidence.",
                }
            )
    verdict = "accepted" if dimensions and not findings else "needs_review"
    return ArtifactDraft(
        artifact_type="distillation_review",
        content={
            "verdict": verdict,
            "findings": findings,
            "distillation": dict(distillation.content),
        },
        source_refs=list(distillation.source_refs),
        parent_artifact_ids=[distillation.artifact_id],
        confidence=distillation.confidence,
        risk_flags=[str(item["finding_type"]) for item in findings],
    )


def compose_distillation_report(context):
    review = context.require_artifact("distillation_review")
    distillation = dict(review.content.get("distillation") or {})
    findings = list(review.content.get("findings") or [])
    dimensions = list(distillation.get("dimensions") or [])
    return ArtifactDraft(
        artifact_type="research_report",
        content={
            "conclusion": f"Distilled {len(dimensions)} investment-logic dimensions; review verdict: {review.content.get('verdict')}.",
            "evidence": dimensions,
            "risk": findings or ["Distilled logic remains decision support and requires source verification."],
            "follow_up": ["Verify attribution and refresh the original source records before reuse."],
        },
        source_refs=list(review.source_refs),
        parent_artifact_ids=[review.artifact_id],
        confidence=review.confidence,
        risk_flags=["decision_support_only", *list(review.risk_flags)],
    )
