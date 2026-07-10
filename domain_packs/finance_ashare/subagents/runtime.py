from __future__ import annotations

from research_system.schemas.artifacts import ArtifactDraft


def normalize_query(context):
    request = context.require_artifact("user_request")
    query = str(request.content.get("query") or request.content.get("text") or "").strip()
    return ArtifactDraft(
        artifact_type="normalized_query",
        content={"query": query, "symbols": list(request.content.get("symbols") or [])},
        parent_artifact_ids=[request.artifact_id],
        confidence=1.0,
    )


def collect_snapshot(context):
    query = context.require_artifact("normalized_query")
    snapshots = list(query.content.get("snapshots") or context.node.inputs.get("snapshots") or [])
    source_refs = list(context.node.inputs.get("source_refs") or [])
    if not source_refs:
        source_refs = [{"type": "user_supplied", "id": query.artifact_id}]
    return ArtifactDraft(
        artifact_type="stock_snapshot_bundle",
        content={
            "query": query.content.get("query"),
            "symbols": list(query.content.get("symbols") or []),
            "snapshots": snapshots,
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


def compose_report(context):
    evidence = context.require_artifact("evidence_set")
    items = list(evidence.content.get("items") or [])
    return ArtifactDraft(
        artifact_type="research_report",
        content={
            "conclusion": "Evidence collected; a professional conclusion requires interpreting the supplied facts.",
            "evidence": items,
            "risk": ["Evidence may be incomplete or stale."],
            "follow_up": ["Refresh material filings and market data before acting."],
        },
        source_refs=list(evidence.source_refs),
        parent_artifact_ids=[evidence.artifact_id],
        confidence=evidence.confidence,
        risk_flags=["decision_support_only"],
    )


def screen_stocks(context):
    snapshots = context.require_artifact("stock_snapshot_bundle")
    inputs = dict(context.node.inputs or {})
    rows = list(snapshots.content.get("snapshots") or [])
    grouped: dict[str, dict[str, dict]] = {}
    for row in rows:
        if not isinstance(row, dict) or not row.get("symbol"):
            continue
        grouped.setdefault(str(row["symbol"]), {})[str(row.get("snapshot_type") or "unknown")] = row
    candidates = []
    for symbol, by_type in grouped.items():
        market = ((by_type.get("market") or {}).get("facts") or {}).get("market_overview") or {}
        spot = market.get("spot") or {}
        fundamental = ((by_type.get("fundamental") or {}).get("facts") or {}).get("financial_abstract") or {}
        notice = ((by_type.get("notice") or {}).get("facts") or {})
        pct_change = _to_float(spot.get("pct_change"))
        amount = _to_float(spot.get("amount"))
        notice_count = int(_to_float(notice.get("notice_count")) or 0)
        missing = [
            label for label, present in (
                ("market", bool(by_type.get("market"))),
                ("fundamental", bool(by_type.get("fundamental"))),
                ("notice", bool(by_type.get("notice"))),
            ) if not present
        ]
        breakdown = {
            "liquidity": min((amount or 0.0) / 1_000_000_000, 1.0),
            "momentum": max(min((pct_change or 0.0) / 10.0, 1.0), -1.0),
            "fundamental": 1.0 if fundamental else 0.0,
            "notice": min(notice_count * 0.1, 0.5),
            "evidence_penalty": -0.75 * len(missing),
        }
        score = round(sum(breakdown.values()), 3)
        candidates.append({
            "symbol": symbol,
            "name": str((by_type.get("market") or {}).get("name") or symbol),
            "latest_price": _to_float(spot.get("latest_price")),
            "latest_pct_change": pct_change,
            "latest_amount": amount,
            "notice_count": notice_count,
            "has_financial_abstract": bool(fundamental),
            "score": score,
            "score_breakdown": breakdown,
            "missing_evidence": missing,
            "degraded_sources": [],
            "deep_checked": not missing,
            "why_it_made_the_list": "Evidence-backed candidate with available market, fundamental, and notice data.",
            "main_risk": "; ".join(f"missing evidence: {item}" for item in missing) or "Requires professional review before acting.",
            "methodology": {},
        })
    candidates.sort(key=lambda item: (item["score"], item.get("latest_amount") or 0.0), reverse=True)
    cycle = str(inputs.get("investment_cycle") or "mid_term")
    min_score = {"short_term": 0.0, "mid_term": 0.0, "long_term": 0.15}.get(cycle, 0.0)
    top_k = max(int(inputs.get("top_k") or 5), 1)
    watchlist_top_k = max(int(inputs.get("watchlist_top_k") or 2), 1)
    screened = [item for item in candidates if item["score"] >= min_score][:top_k]
    watchlist = [item for item in candidates if item not in screened and item["score"] >= min_score - 0.75][:watchlist_top_k]
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
        "enrichment_target_count": 0,
        "preferred_methodology": str(inputs.get("prefer_methodology") or "comps"),
        "screening_strategy": {
            "methodology_source": "intent_input" if inputs.get("prefer_methodology") else "domain_pack_policy",
            "investment_cycle": cycle,
        },
        "summary": f"Screened {len(candidates)} candidates; {len(screened)} eligible.",
    }
    return ArtifactDraft(
        artifact_type="screening_result",
        content={
            "screened_candidates": screened,
            "watchlist_candidates": watchlist,
            "enrichment_targets": [],
            "scorecard": scorecard,
            "screening_notes": ["screening_strategy=domain_pack_policy"],
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


def research_sector(context):
    snapshots = context.require_artifact("stock_snapshot_bundle")
    content = snapshots.content
    inputs = dict(context.node.inputs or {})
    rows = [row for row in list(content.get("snapshots") or []) if isinstance(row, dict)]
    sector_rows = [row for row in rows if row.get("snapshot_type") == "sector"]
    sector_snapshot = sector_rows[0] if sector_rows else {}
    facts = sector_snapshot.get("facts") or {}
    sector = inputs.get("sector") or content.get("sector") or sector_snapshot.get("sector") or facts.get("sector") or content.get("query")
    theme = content.get("theme") or sector_snapshot.get("theme") or facts.get("theme")
    related = [row for row in rows if row not in sector_rows]
    risks = ["Sector evidence may be incomplete or stale."]
    if not related:
        risks.append("No related sector or constituent snapshots were supplied.")
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
                "policy_snapshot_requested" if inputs.get("include_policy_snapshot") else "policy_snapshot_not_requested",
            ],
        },
        source_refs=list(snapshots.source_refs),
        parent_artifact_ids=[snapshots.artifact_id],
        confidence=snapshots.confidence,
        risk_flags=["sector_analysis_requires_source_refresh"],
    )


def review_thesis(context):
    evidence = context.require_artifact("evidence_set")
    inputs = dict(context.node.inputs or {})
    items = list(evidence.content.get("items") or [])
    verdicts = [str(item.get("verdict") or "").lower() for item in items if isinstance(item, dict)]
    overall_verdict = (
        "insufficient_evidence" if not items
        else "confirmed" if verdicts and all(item == "confirmed" for item in verdicts)
        else "rejected" if verdicts and all(item == "rejected" for item in verdicts)
        else "inconclusive"
    )
    strengths = [item for item in items if isinstance(item, dict) and str(item.get("verdict") or "").lower() == "confirmed"]
    weaknesses = [item for item in items if isinstance(item, dict) and str(item.get("verdict") or "").lower() != "confirmed"]
    return ArtifactDraft(
        artifact_type="thesis_review",
        content={
            "verdict": overall_verdict,
            "overall_verdict": overall_verdict,
            "findings": items,
            "strengths": strengths,
            "weaknesses": weaknesses or (["No independently verified thesis evidence was supplied."] if not items else []),
            "follow_up": [
                "Refresh filings, valuation, and risk evidence before acting.",
                *([f"Review focus: {inputs['review_focus']}"] if inputs.get("review_focus") else []),
            ],
        },
        source_refs=list(evidence.source_refs),
        parent_artifact_ids=[evidence.artifact_id],
        confidence=evidence.confidence,
        risk_flags=["decision_support_only"],
    )


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
