from __future__ import annotations

import re

from domain_packs.finance_ashare.tools.snapshot_collector import collect_snapshot_bundle, collect_snapshots
from domain_packs.finance_ashare.subagents.fund_methodology import build_fund_analysis
from domain_packs.finance_ashare.subagents.uzi_methodology import (
    UZI_METHOD_VERSION,
    build_delivery_conclusion,
    build_features,
    build_panel_review,
    build_presentation,
    build_risk_scan,
    build_valuation_model,
    select_profile,
)
from domain_packs.finance_ashare.subagents.uzi_agent_roleplay import apply_deep_agent_roleplay
from domain_packs.finance_ashare.subagents.uzi_qualitative_deep_dive import apply_qualitative_deep_dive
from domain_packs.finance_ashare.subagents.uzi_advanced_methodology import (
    build_deep_analysis,
    build_dimension_coverage,
    build_institutional_analysis,
    build_research_workflow,
)
from domain_packs.finance_ashare.subagents.uzi_tier1_methodology import (
    build_ai_readiness,
    build_earnings_preview,
    build_holding_rebalance,
    build_model_update,
    build_portfolio_rebalance,
    build_returns_attribution,
)
from domain_packs.finance_ashare.subagents.uzi_method_products import (
    METHOD_ARTIFACT_TYPES,
    build_method_product,
)
from research_system.adapters.vendors.akshare_utils import coerce_numeric
from research_system.services.methodologies import (
    CashflowResilienceService,
    CompsService,
    DCFService,
)
from research_system.runtime.core.artifact_board import ArtifactBoard
from research_system.schemas.artifacts import ArtifactDraft
from research_system.schemas.execution_runtime import NodeExecutionOutput


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
    inputs = dict(context.node.inputs or {})
    fund_codes = _unique_strings(
        request.content.get("fund_codes")
        or inputs.get("fund_codes")
        or inputs.get("funds")
        or []
    )
    for symbol in _STOCK_CODE_PATTERN.findall(query):
        if symbol not in fund_codes and symbol not in symbols:
            symbols.append(symbol)
    return ArtifactDraft(
        artifact_type="normalized_query",
        content={
            "query": query,
            "symbols": symbols,
            "fund_codes": fund_codes,
            "fund_category": str(
                request.content.get("fund_category")
                or inputs.get("fund_category")
                or ""
            ).strip()
            or None,
            "portfolio_holdings": list(
                request.content.get("portfolio_holdings")
                or inputs.get("portfolio_holdings")
                or []
            ),
            "benchmark_return": (
                request.content.get("benchmark_return")
                if request.content.get("benchmark_return") is not None
                else inputs.get("benchmark_return")
            ),
            "rebalance_targets": dict(
                request.content.get("rebalance_targets")
                or inputs.get("rebalance_targets")
                or {}
            ) or None,
            "drift_threshold": (
                request.content.get("drift_threshold")
                if request.content.get("drift_threshold") is not None
                else inputs.get("drift_threshold", 5.0)
            ),
            "fee_model": dict(
                request.content.get("fee_model")
                or inputs.get("fee_model")
                or {}
            ) or None,
            "asset_class_targets": dict(
                request.content.get("asset_class_targets")
                or inputs.get("asset_class_targets")
                or {}
            ) or None,
            "requested_methods": _unique_strings(
                request.content.get("requested_methods")
                or inputs.get("requested_methods")
                or []
            ),
            "method_assumptions": dict(
                request.content.get("method_assumptions")
                or inputs.get("method_assumptions")
                or {}
            ) or None,
            "holding_context": dict(
                request.content.get("holding_context")
                or inputs.get("holding_context")
                or {}
            ) or None,
        },
        source_refs=list(request.source_refs),
        parent_artifact_ids=[request.artifact_id],
        confidence=1.0,
    )


def collect_fund_snapshot(context):
    query_artifact = context.require_artifact("normalized_query")
    inputs = dict(context.node.inputs or {})
    arguments = {
        "query": str(query_artifact.content.get("query") or ""),
        "fund_codes": _unique_strings(
            query_artifact.content.get("fund_codes")
            or inputs.get("fund_codes")
            or []
        ),
        "fund_category": str(
            query_artifact.content.get("fund_category")
            or inputs.get("fund_category")
            or ""
        ).strip()
        or None,
        "candidate_pool_size": int(inputs.get("candidate_pool_size") or 8),
        "investment_cycle": _required_investment_cycle(inputs),
    }
    call_tool = getattr(context, "call_tool", None)
    if callable(call_tool):
        tool_result = call_tool("fund_data_provider", arguments)
        if tool_result.status != "completed":
            raise RuntimeError(tool_result.error or "fund_data_provider failed")
        payload = dict(tool_result.output or {})
        governed_source_refs = list(tool_result.source_refs or [])
    else:
        # Direct Pack unit tests can use a minimal execution context. Production
        # Graph execution always invokes the governed Pack tool above.
        from domain_packs.finance_ashare.skills.fund_snapshot.skill import FundSnapshotSkill

        payload = FundSnapshotSkill().run(**arguments)
        governed_source_refs = []
    source_refs = [
        dict(row)
        for row in payload.pop("source_refs", [])
        if isinstance(row, dict)
    ]
    source_refs = governed_source_refs or source_refs
    quality_status = str(payload.get("quality_status") or "data_gap")
    return ArtifactDraft(
        artifact_type="fund_snapshot",
        content=payload,
        source_refs=source_refs,
        parent_artifact_ids=[query_artifact.artifact_id],
        confidence=0.8 if quality_status == "available" else 0.45 if payload.get("funds") else 0.0,
        risk_flags=list(payload.get("data_gap") or []),
        metadata={"quality_status": quality_status},
    )


def analyze_funds(context):
    snapshot = context.require_artifact("fund_snapshot")
    content = build_fund_analysis(dict(snapshot.content or {}))
    quality_status = str(content.get("quality_status") or "data_gap")
    candidates = list(content.get("candidates") or [])
    coverage = [
        float(row.get("score_coverage") or 0.0)
        for row in candidates
        if isinstance(row, dict)
    ]
    confidence = round(sum(coverage) / len(coverage), 4) if coverage else 0.0
    return ArtifactDraft(
        artifact_type="fund_analysis",
        content=content,
        source_refs=list(snapshot.source_refs),
        parent_artifact_ids=[snapshot.artifact_id],
        confidence=confidence,
        risk_flags=list(content.get("data_gap") or []),
        metadata={
            "quality_status": quality_status,
            "methodology_version": content.get("methodology_version"),
        },
    )


def compose_fund_report(context):
    analysis = context.require_artifact("fund_analysis")
    content = dict(analysis.content or {})
    analysis_mode = str(content.get("analysis_mode") or "comparison")
    candidates = [
        dict(row)
        for row in content.get("candidates") or []
        if isinstance(row, dict)
    ]
    recommendations = [
        dict(row)
        for row in content.get("recommendations") or []
        if isinstance(row, dict)
    ]
    if analysis_mode == "single_fund" and candidates:
        candidate = candidates[0]
        metrics = dict(candidate.get("metrics") or {})
        returns = dict(candidate.get("returns_pct") or {})
        annualized_label = (
            "近三年年化收益"
            if _number(returns.get("year_3")) is not None
            else "成立以来年化收益"
        )
        observed = []
        for label, key in (
            ("近一年收益", "return_1y"),
            (annualized_label, "return_3y_annualized"),
            ("近一年相对基准", "benchmark_excess_1y"),
            ("样本期最大回撤", "max_drawdown"),
            ("年化波动", "volatility"),
        ):
            value = _number(metrics.get(key))
            if value is not None:
                observed.append(f"{label} {value:+.2f}%")
        metric_summary = "，".join(observed) or "已采集字段不足以形成风险收益摘要"
        conclusion = (
            f"已完成{candidate.get('fund_name')}（{candidate.get('fund_code')}）的单基金分析："
            f"{metric_summary}。本次不执行横向排名。"
        )
    elif recommendations:
        leaders = "、".join(
            f"{row.get('fund_name')}（{row.get('fund_code')}，{float(row.get('score') or 0):.1f}分）"
            for row in recommendations[:3]
        )
        conclusion = f"基于同类真实收益、benchmark 超额收益与风险数据，当前证据门槛内的优先候选为：{leaders}。"
    else:
        conclusion = "当前真实数据不足以形成可审计的基金优先候选，结果保留为数据缺口。"
    provider_errors = [
        f"{row.get('stage')}: {row.get('error_type')}"
        for row in content.get("provider_errors") or []
        if isinstance(row, dict)
    ]
    candidate_gaps = [
        str(gap)
        for candidate in candidates
        for gap in candidate.get("data_gap") or []
        if str(gap).strip()
    ]
    risks = [
        *list(content.get("data_gap") or []),
        *candidate_gaps,
        *provider_errors,
        (
            "历史收益不代表未来表现；本次为单基金独立分析，不包含同类排名。"
            if analysis_mode == "single_fund"
            else "历史收益不代表未来表现；基金比较只在同类样本内有效。"
        ),
    ]
    evidence = [
        {
            "fund_code": row.get("fund_code"),
            "fund_name": row.get("fund_name"),
            "comparison_group": row.get("comparison_group"),
            "rank": row.get("rank"),
            "score": row.get("score"),
            "score_coverage": row.get("score_coverage"),
            "returns_pct": row.get("returns_pct"),
            "risk": row.get("risk"),
            "holdings_period": row.get("holdings_period"),
            "top10_concentration_pct": row.get("top10_concentration_pct"),
            "asset_allocation": row.get("asset_allocation"),
            "asset_allocation_period": row.get("asset_allocation_period"),
            "bond_holdings": row.get("bond_holdings"),
            "target_fund_holdings": row.get("target_fund_holdings"),
            "target_fund_period": row.get("target_fund_period"),
            "target_fund_source_ref": row.get("target_fund_source_ref"),
            "lookthrough_holdings": row.get("lookthrough_holdings"),
            "lookthrough_period": row.get("lookthrough_period"),
            "holdings_coverage": row.get("holdings_coverage"),
            "benchmark_definition": row.get("benchmark_definition"),
            "benchmark_performance": row.get("benchmark_performance"),
            "manager_tenures": row.get("manager_tenures"),
            "scale_change": row.get("scale_change"),
            "data_gap": row.get("data_gap"),
        }
        for row in candidates
    ]
    return ArtifactDraft(
        artifact_type="research_report",
        content={
            "conclusion": conclusion,
            "evidence": evidence,
            "risk": list(dict.fromkeys(str(item) for item in risks if str(item).strip())),
            "follow_up": [
                *(
                    ["补齐已标记的真实数据缺口后重新运行。"]
                    if content.get("data_gap") or candidate_gaps or provider_errors
                    else []
                ),
                "结合自身风险承受能力、持有期限和申赎规则做最终决策。",
            ],
            "fund_analysis": content,
            "quality_status": content.get("quality_status"),
        },
        source_refs=list(analysis.source_refs),
        parent_artifact_ids=[analysis.artifact_id],
        confidence=analysis.confidence,
        risk_flags=list(dict.fromkeys(str(item) for item in risks if str(item).strip())),
        metadata={"quality_status": content.get("quality_status")},
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
    governed_tool_path = callable(getattr(context, "call_tool", None))
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
            "research_profile": str(inputs.get("research_profile") or "").strip() or None,
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
    snapshots = [
        _normalize_snapshot_record(snapshot)
        for snapshot in snapshots
        if isinstance(snapshot, dict)
    ]
    if governed_tool_path:
        snapshots = _ensure_requested_snapshot_records(
            snapshots,
            requested_types=requested_types,
            symbols=symbols,
            sector=sector,
        )
    quality_counts: dict[str, int] = {}
    for snapshot in snapshots:
        quality = _snapshot_quality(snapshot)
        quality_counts[quality] = quality_counts.get(quality, 0) + 1
    if quality_counts.get("available", 0) == 0:
        collection_quality = "data_gap"
    elif any(key in quality_counts for key in ("degraded", "data_gap")):
        collection_quality = "degraded"
    else:
        collection_quality = "available"
    collection["quality_status"] = collection_quality
    collection["quality_counts"] = quality_counts
    collection["degraded"] = collection_quality != "available"
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
    collection.setdefault("quality_status", "data_gap" if not snapshots else "available")
    collection["degraded"] = str(collection.get("quality_status") or "") in {"degraded", "data_gap"}
    source_refs = list(inputs.get("source_refs") or [])
    if not source_refs:
        source_refs = (
            provider_source_refs
            or _snapshot_source_refs(snapshots)
            or [{"type": "user_supplied", "id": query.artifact_id}]
        )
    bundle_content = {
        "query": query_text,
        "symbols": symbols,
        "sector": sector,
        "snapshots": snapshots,
        "candidate_universe": candidate_universe,
        "collection": collection,
        "review_target_run_id": review_target_run_id,
        "review_target_artifact_ids": [artifact.artifact_id for artifact in target_artifacts],
        "snapshot_keys": [
            _snapshot_key(snapshot)
            for snapshot in snapshots
            if isinstance(snapshot, dict) and snapshot.get("snapshot_type")
        ],
    }
    snapshot_drafts = [
        ArtifactDraft(
            artifact_type="snapshot",
            content=_snapshot_artifact_content(snapshot),
            source_refs=_snapshot_artifact_source_refs(snapshot) or source_refs,
            parent_artifact_ids=[query.artifact_id],
            confidence=0.45 if _snapshot_quality(snapshot) in {"degraded", "data_gap"} else 0.8,
            risk_flags=list(_snapshot_quality_reasons(snapshot)),
            metadata={
                "snapshot_type": str(snapshot.get("snapshot_type") or ""),
                "scope": str(_snapshot_artifact_content(snapshot).get("scope") or "symbol"),
                "quality_status": _snapshot_quality(snapshot),
            },
        )
        for snapshot in snapshots
        if isinstance(snapshot, dict) and snapshot.get("snapshot_type")
    ]
    bundle_draft = ArtifactDraft(
        artifact_type="stock_snapshot_bundle",
        content=bundle_content,
        source_refs=source_refs,
        parent_artifact_ids=[query.artifact_id],
        confidence=0.8 if str(collection.get("quality_status")) == "available" else 0.45,
        risk_flags=list(collection.get("degrade_reasons") or []),
        metadata={"quality_status": str(collection.get("quality_status") or "data_gap")},
    )
    return NodeExecutionOutput(artifacts=[*snapshot_drafts, bundle_draft])


def _snapshot_quality(snapshot: dict[str, Any]) -> str:
    facts = snapshot.get("facts") if isinstance(snapshot.get("facts"), dict) else {}
    quality = str(
        snapshot.get("quality_status")
        or snapshot.get("status")
        or facts.get("availability")
        or ""
    ).strip().lower()
    if quality not in {"available", "degraded", "data_gap", "assumption_only"}:
        quality = "degraded" if snapshot.get("degraded") else "available"
    if snapshot.get("degraded") and quality == "available":
        quality = "degraded"
    return quality


def _normalize_snapshot_record(snapshot: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(snapshot)
    quality = _snapshot_quality(normalized)
    normalized["quality_status"] = quality
    if quality in {"degraded", "data_gap"}:
        normalized["degraded"] = True
    normalized.setdefault("degrade_reasons", _snapshot_quality_reasons(normalized))
    normalized.setdefault("data_period", normalized.get("snapshot_date"))
    return normalized


def _snapshot_quality_reasons(snapshot: dict[str, Any]) -> list[str]:
    facts = snapshot.get("facts") if isinstance(snapshot.get("facts"), dict) else {}
    values = [
        *list(snapshot.get("degrade_reasons") or []),
        snapshot.get("degrade_reason"),
        *list(facts.get("data_gap") or []),
    ]
    reasons: list[str] = []
    for value in values:
        if value is None:
            continue
        reason = str(value).strip()
        if reason and reason not in reasons:
            reasons.append(reason)
    return reasons


def _ensure_requested_snapshot_records(
    snapshots: list[dict[str, Any]],
    *,
    requested_types: list[str],
    symbols: list[str],
    sector: str | None,
) -> list[dict[str, Any]]:
    """Materialize explicit data-gap facts for requested but unavailable inputs."""

    rows = list(snapshots)
    sector_scoped = {"sector", "policy", "macro"}

    def target_key(snapshot_type: str, row: dict[str, Any]) -> str:
        if snapshot_type in sector_scoped:
            return str(row.get("sector") or sector or "").strip()
        return str(row.get("symbol") or "").strip()

    for snapshot_type in _unique_strings(requested_types):
        targets = [None] if snapshot_type in sector_scoped else (symbols or [None])
        for symbol in targets:
            expected_target = str(sector or "").strip() if snapshot_type in sector_scoped else str(symbol or "").strip()
            if any(
                str(row.get("snapshot_type") or "").strip() == snapshot_type
                and target_key(snapshot_type, row) == expected_target
                for row in rows
            ):
                continue
            rows.append(
                {
                    "snapshot_type": snapshot_type,
                    "symbol": None if snapshot_type in sector_scoped else symbol,
                    "sector": sector if snapshot_type in sector_scoped else None,
                    "facts": {"availability": "data_gap"},
                    "quality_status": "data_gap",
                    "missing_fields": ["provider_snapshot_missing"],
                    "degrade_reasons": ["provider_snapshot_missing"],
                    "source": {"provider_id": "financial_data_provider"},
                }
            )
    return rows


def _snapshot_key(snapshot: dict[str, Any]) -> str:
    snapshot_type = str(snapshot.get("snapshot_type") or "snapshot").strip()
    scope = "sector" if snapshot.get("sector") else "symbol"
    target = str(snapshot.get("sector") or snapshot.get("symbol") or "unscoped").strip()
    return f"{scope}:{target}:{snapshot_type}"


def _snapshot_artifact_content(snapshot: dict[str, Any]) -> dict[str, Any]:
    facts = snapshot.get("facts") if isinstance(snapshot.get("facts"), dict) else {}
    source = snapshot.get("source") if isinstance(snapshot.get("source"), dict) else {}
    snapshot_type = str(snapshot.get("snapshot_type") or "snapshot").strip()
    scope = "sector" if snapshot.get("sector") else (
        "sector" if snapshot_type in {"sector", "policy", "macro"} else "symbol"
    )
    return {
        "snapshot_type": snapshot_type,
        "scope": scope,
        "symbol": str(snapshot.get("symbol") or "") or None,
        "sector": str(snapshot.get("sector") or "") or None,
        "query": str(snapshot.get("query") or "") or None,
        "snapshot_date": str(snapshot.get("snapshot_date") or "") or None,
        "data_period": str(
            snapshot.get("data_period") or snapshot.get("snapshot_date") or ""
        ) or None,
        "retrieved_at": str(snapshot.get("retrieved_at") or "") or None,
        "quality_status": _snapshot_quality(snapshot),
        "missing_fields": [
            str(item).strip()
            for item in list(snapshot.get("missing_fields") or [])
            if str(item).strip()
        ],
        "degrade_reasons": _snapshot_quality_reasons(snapshot),
        "facts": facts,
        "source": source,
        "source_refs": _snapshot_artifact_source_refs(snapshot),
    }


def _snapshot_artifact_source_refs(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    source = snapshot.get("source") if isinstance(snapshot.get("source"), dict) else {}
    provider = str(
        source.get("provider_id")
        or source.get("source_id")
        or source.get("vendor")
        or snapshot.get("snapshot_type")
        or "finance_pack"
    ).strip()
    identifier = _snapshot_key(snapshot)
    return [{"type": "snapshot", "provider": provider, "id": identifier}]


def _with_quality_status(content: dict[str, Any]) -> dict[str, Any]:
    """Attach the canonical quality status to Pack methodology content."""

    result = dict(content)
    declared = str(result.get("quality_status") or "").strip().lower()
    if declared in {"available", "degraded", "data_gap", "assumption_only"}:
        return result
    statuses: list[str] = []
    gaps = 0
    assumptions = 0

    def visit(value: object) -> None:
        nonlocal gaps, assumptions
        if isinstance(value, dict):
            status = str(value.get("status") or "").strip().lower()
            if status in {"available", "degraded", "data_gap", "assumption_only"}:
                statuses.append(status)
            gaps += len(list(value.get("data_gap") or []))
            assumptions += len(list(value.get("assumption_flags") or []))
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(result)
    if "data_gap" in statuses and not any(status == "available" for status in statuses):
        quality = "data_gap"
    elif "assumption_only" in statuses and not any(status == "available" for status in statuses):
        quality = "assumption_only"
    elif gaps or assumptions or "degraded" in statuses or "data_gap" in statuses:
        quality = "degraded"
    else:
        quality = "available"
    result["quality_status"] = quality
    return result


def _context_artifacts(context, artifact_type: str) -> list:
    reader = getattr(context, "artifacts", None)
    if callable(reader):
        return list(reader(artifact_type))
    try:
        return [context.require_artifact(artifact_type)]
    except Exception:
        return []


def _snapshot_context(context) -> tuple[list[dict], list[str], str, list[dict], list[str]]:
    """Read the independent Snapshot artifacts used as the shared data plane."""
    artifacts = _context_artifacts(context, "snapshot")
    rows: list[dict] = []
    source_refs: list[dict] = []
    parent_ids: list[str] = []
    query = str((context.node.inputs or {}).get("query") or "")
    symbols: list[str] = []
    for artifact in artifacts:
        content = dict(artifact.content or {})
        snapshot_type = str(
            content.get("snapshot_type") or content.get("dimension") or "snapshot"
        ).strip()
        row = {
            **content,
            "snapshot_type": snapshot_type,
            "quality_status": content.get("quality_status") or artifact.metadata.get("quality_status"),
            "source_refs": list(
                content.get("source_refs")
                if isinstance(content.get("source_refs"), list)
                else artifact.source_refs or []
            ),
        }
        rows.append(row)
        parent_ids.append(artifact.artifact_id)
        source_refs.extend(artifact.source_refs)
        symbol = str(content.get("symbol") or "").strip()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
        if not query:
            query = str(content.get("query") or "")

    symbols = _unique_strings([*symbols, *[str(row.get("symbol") or "") for row in rows]])
    return rows, symbols, query, _unique_source_refs(source_refs), list(dict.fromkeys(parent_ids))


def select_evidence(context):
    rows, symbols, query, source_refs, parent_ids = _snapshot_context(context)
    return ArtifactDraft(
        artifact_type="evidence_set",
        content={"items": rows, "symbols": symbols, "query": query},
        source_refs=source_refs,
        parent_artifact_ids=parent_ids,
        confidence=0.8 if rows else 0.0,
    )


def analyze_financials(context):
    rows, symbols, query, source_refs, parent_ids = _snapshot_context(context)
    inputs = dict(context.node.inputs or {})
    grouped = _snapshots_by_symbol(rows)
    candidates = [
        _analysis_candidate(symbol=symbol, snapshots=grouped.get(symbol, {}))
        for symbol in symbols
        if grouped.get(symbol)
    ]
    cycle = _required_investment_cycle(inputs)
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
    analysis_content = _with_quality_status(
        {
            "symbols": [candidate["symbol"] for candidate in candidates],
            "methodologies": methodologies,
            "scorecards": scorecards,
            "summary": _analysis_summary(methodologies=methodologies, scorecards=scorecards),
        }
    )
    return ArtifactDraft(
        artifact_type="financial_analysis",
        content=analysis_content,
        source_refs=source_refs,
        parent_artifact_ids=parent_ids,
        confidence=_analysis_confidence(methodologies),
        risk_flags=["methodology_assumption_driven"] if methodologies else ["financial_evidence_missing"],
    )


def evaluate_uzi_valuation(context):
    rows, symbols, query, source_refs, parent_ids = _snapshot_context(context)
    inputs = dict(context.node.inputs or {})
    profile = select_profile(inputs)
    content = build_valuation_model(
        rows,
        symbols,
        profile,
    )
    content = _with_quality_status(content)
    models = list(content.get("models") or [])
    warnings = [
        warning
        for model in models
        for warning in [
            *list(model.get("warnings") or []),
            *list(model.get("data_gap") or []),
            *list(model.get("assumption_flags") or []),
        ]
    ]
    confidence = (
        round(sum(float(model.get("confidence") or 0.0) for model in models) / len(models), 4)
        if models
        else 0.0
    )
    return ArtifactDraft(
        artifact_type="valuation_model",
        content=content,
        source_refs=source_refs,
        parent_artifact_ids=parent_ids,
        confidence=confidence,
        risk_flags=list(dict.fromkeys(warnings)),
        metadata={"profile": profile, "methodology_version": content["methodology_version"], "source_commit": profile.get("source_commit")},
    )


def evaluate_uzi_panel(context):
    rows, symbols, query, source_refs, parent_ids = _snapshot_context(context)
    inputs = dict(context.node.inputs or {})
    profile = select_profile(inputs)
    content = build_panel_review(
        rows,
        symbols,
        profile,
    )
    if profile["selected"] == "deep":
        features = build_features(rows, symbols[0]) if symbols else {}
        content = apply_deep_agent_roleplay(
            content,
            features=features,
            llm_provider=getattr(context, "llm_provider", None),
            cancellation=getattr(context, "cancellation", None),
        )
    content = _with_quality_status(content)
    return ArtifactDraft(
        artifact_type="investor_panel_review",
        content=content,
        source_refs=source_refs,
        parent_artifact_ids=parent_ids,
        confidence=float(content.get("confidence") or 0.0),
        risk_flags=list(content.get("risk_flags") or []),
        metadata={"profile": profile, "methodology_version": content["methodology_version"], "source_commit": profile.get("source_commit")},
    )


def evaluate_uzi_risk(context):
    rows, symbols, query, source_refs, parent_ids = _snapshot_context(context)
    inputs = dict(context.node.inputs or {})
    profile = select_profile(inputs)
    content = build_risk_scan(
        query,
        rows,
        symbols,
        profile,
    )
    content = _with_quality_status(content)
    return ArtifactDraft(
        artifact_type="risk_scan",
        content=content,
        source_refs=source_refs,
        parent_artifact_ids=parent_ids,
        confidence=float(content.get("confidence") or 0.0),
        risk_flags=list(content.get("risk_flags") or []),
        metadata={"profile": profile, "methodology_version": content["methodology_version"], "source_commit": profile.get("source_commit")},
    )


def evaluate_uzi_institutional(context):
    rows, symbols, query, source_refs, parent_ids = _snapshot_context(context)
    valuation = (context.artifacts("valuation_model") or [None])[-1]
    inputs = dict(context.node.inputs or {})
    profile = select_profile(inputs)
    content = build_institutional_analysis(
        rows,
        symbols,
        valuation.content if valuation is not None else None,
        profile,
        inputs,
    )
    content = _with_quality_status(content)
    if valuation is not None:
        parent_ids.append(valuation.artifact_id)
        source_refs = _unique_source_refs([*source_refs, *valuation.source_refs])
    return ArtifactDraft(
        artifact_type="institutional_analysis",
        content=content,
        source_refs=source_refs,
        parent_artifact_ids=parent_ids,
        confidence=float(content.get("confidence") or 0.0),
        risk_flags=list(content.get("risk_flags") or []),
        metadata={"profile": profile, "methodology_version": content["methodology_version"], "source_commit": profile.get("source_commit")},
    )


def evaluate_uzi_workflow(context):
    rows, symbols, query, source_refs, parent_ids = _snapshot_context(context)
    valuation = (context.artifacts("valuation_model") or [None])[-1]
    inputs = dict(context.node.inputs or {})
    profile = select_profile(inputs)
    content = build_research_workflow(
        rows,
        symbols,
        valuation.content if valuation is not None else None,
        profile,
        inputs,
    )
    content = _with_quality_status(content)
    if valuation is not None:
        parent_ids.append(valuation.artifact_id)
        source_refs = _unique_source_refs([*source_refs, *valuation.source_refs])
    return ArtifactDraft(
        artifact_type="research_workflow",
        content=content,
        source_refs=source_refs,
        parent_artifact_ids=parent_ids,
        confidence=float(content.get("confidence") or 0.0),
        risk_flags=list(content.get("risk_flags") or []),
        metadata={"profile": profile, "methodology_version": content["methodology_version"], "source_commit": profile.get("source_commit")},
    )


def evaluate_uzi_deep(context):
    rows, symbols, query, source_refs, parent_ids = _snapshot_context(context)
    institutional = context.require_artifact("institutional_analysis")
    workflow = context.require_artifact("research_workflow")
    risk_scan = (context.artifacts("risk_scan") or [None])[-1]
    inputs = dict(context.node.inputs or {})
    profile = select_profile(inputs)
    content = build_deep_analysis(
        rows,
        symbols,
        institutional.content,
        workflow.content,
        profile,
        inputs,
        risk_scan.content if risk_scan is not None else None,
    )
    content = apply_qualitative_deep_dive(
        content,
        snapshot_rows=rows,
        llm_provider=getattr(context, "llm_provider", None),
        cancellation=getattr(context, "cancellation", None),
    )
    content = _with_quality_status(content)
    return ArtifactDraft(
        artifact_type="deep_analysis",
        content=content,
        source_refs=_unique_source_refs(
            [*source_refs, *institutional.source_refs, *workflow.source_refs]
        ),
        parent_artifact_ids=[*parent_ids, institutional.artifact_id, workflow.artifact_id],
        confidence=float(content.get("confidence") or 0.0),
        risk_flags=list(content.get("risk_flags") or []),
        metadata={"profile": profile, "methodology_version": content["methodology_version"], "source_commit": profile.get("source_commit")},
    )


def evaluate_uzi_dimension_coverage(context):
    rows, symbols, query, source_refs, parent_ids = _snapshot_context(context)
    institutional = (context.artifacts("institutional_analysis") or [None])[-1]
    workflow = (context.artifacts("research_workflow") or [None])[-1]
    deep = (context.artifacts("deep_analysis") or [None])[-1]
    inputs = dict(context.node.inputs or {})
    profile = select_profile(inputs)
    content = build_dimension_coverage(
        rows,
        symbols,
        profile,
        institutional_available=_artifact_has_usable_result(institutional),
        workflow_available=_artifact_has_usable_result(workflow),
        deep_available=_artifact_has_usable_result(deep),
    )
    content = _with_quality_status(content)
    parents = list(parent_ids)
    for artifact in (institutional, workflow, deep):
        if artifact is not None:
            parents.append(artifact.artifact_id)
            source_refs = _unique_source_refs([*source_refs, *artifact.source_refs])
    return ArtifactDraft(
        artifact_type="uzi_dimension_coverage",
        content=content,
        source_refs=source_refs,
        parent_artifact_ids=parents,
        confidence=float(content.get("confidence") or 0.0),
        risk_flags=list(content.get("risk_flags") or []),
        metadata={"profile": profile, "methodology_version": content["methodology_version"], "source_commit": profile.get("source_commit")},
    )


def evaluate_uzi_ai_readiness(context):
    rows, symbols, _, source_refs, parent_ids = _snapshot_context(context)
    content = build_ai_readiness(rows, symbols)
    return _tier1_snapshot_artifact(
        artifact_type="ai_readiness",
        content=content,
        source_refs=source_refs,
        parent_ids=parent_ids,
    )


def evaluate_uzi_earnings_preview(context):
    rows, symbols, _, source_refs, parent_ids = _snapshot_context(context)
    content = build_earnings_preview(rows, symbols)
    return _tier1_snapshot_artifact(
        artifact_type="earnings_preview",
        content=content,
        source_refs=source_refs,
        parent_ids=parent_ids,
    )


def evaluate_uzi_model_update(context):
    rows, symbols, _, source_refs, parent_ids = _snapshot_context(context)
    valuation = (context.artifacts("valuation_model") or [None])[-1]
    inputs = dict(context.node.inputs or {})
    content = build_model_update(
        rows,
        symbols,
        inputs.get("model_updates") if isinstance(inputs.get("model_updates"), dict) else None,
        valuation.content if valuation is not None else None,
    )
    if valuation is not None:
        parent_ids.append(valuation.artifact_id)
        source_refs = _unique_source_refs([*source_refs, *valuation.source_refs])
    return _tier1_snapshot_artifact(
        artifact_type="model_update",
        content=content,
        source_refs=source_refs,
        parent_ids=parent_ids,
    )


def _evaluate_uzi_method_product(context, method_id: str) -> ArtifactDraft:
    rows, symbols, _, source_refs, parent_ids = _snapshot_context(context)
    inputs = dict(context.node.inputs or {})
    valuation = (context.artifacts("valuation_model") or [None])[-1]
    risk = (context.artifacts("risk_scan") or [None])[-1]
    content = build_method_product(
        method_id,
        rows,
        symbols,
        inputs,
        valuation.content if valuation is not None else None,
        risk.content if risk is not None else None,
    )
    for artifact in (valuation, risk):
        if artifact is not None:
            parent_ids.append(artifact.artifact_id)
            source_refs = _unique_source_refs([*source_refs, *artifact.source_refs])
    return _tier1_snapshot_artifact(
        artifact_type=METHOD_ARTIFACT_TYPES[method_id],
        content=content,
        source_refs=source_refs,
        parent_ids=list(dict.fromkeys(parent_ids)),
    )


def evaluate_uzi_three_statement(context):
    return _evaluate_uzi_method_product(context, "three_statement")


def evaluate_uzi_quick_lbo(context):
    return _evaluate_uzi_method_product(context, "quick_lbo")


def evaluate_uzi_accretion_dilution(context):
    return _evaluate_uzi_method_product(context, "accretion_dilution")


def evaluate_uzi_segmental(context):
    return _evaluate_uzi_method_product(context, "segmental")


def evaluate_uzi_initiation_report(context):
    return _evaluate_uzi_method_product(context, "initiation_report")


def evaluate_uzi_earnings_review(context):
    return _evaluate_uzi_method_product(context, "earnings_review")


def evaluate_uzi_catalyst_calendar(context):
    return _evaluate_uzi_method_product(context, "catalyst_calendar")


def evaluate_uzi_thesis_tracker(context):
    return _evaluate_uzi_method_product(context, "thesis_tracker")


def evaluate_uzi_morning_note(context):
    return _evaluate_uzi_method_product(context, "morning_note")


def evaluate_uzi_ic_memo(context):
    return _evaluate_uzi_method_product(context, "ic_memo")


def evaluate_uzi_porter_bcg(context):
    return _evaluate_uzi_method_product(context, "porter_bcg")


def evaluate_uzi_dd_checklist(context):
    return _evaluate_uzi_method_product(context, "dd_checklist")


def evaluate_uzi_unit_economics(context):
    return _evaluate_uzi_method_product(context, "unit_economics")


def evaluate_uzi_value_creation_plan(context):
    return _evaluate_uzi_method_product(context, "value_creation_plan")


def evaluate_uzi_returns_attribution(context):
    query = context.require_artifact("normalized_query")
    inputs = dict(context.node.inputs or {})
    holdings = list(
        query.content.get("portfolio_holdings")
        or inputs.get("portfolio_holdings")
        or []
    )
    benchmark_return = (
        query.content.get("benchmark_return")
        if query.content.get("benchmark_return") is not None
        else inputs.get("benchmark_return")
    )
    content = build_returns_attribution(holdings, benchmark_return)
    return _tier1_request_artifact(
        artifact_type="returns_attribution",
        content=content,
        query=query,
    )


def evaluate_uzi_holding_rebalance(context):
    query = context.require_artifact("normalized_query")
    inputs = dict(context.node.inputs or {})
    holdings = list(
        query.content.get("portfolio_holdings")
        or inputs.get("portfolio_holdings")
        or []
    )
    targets = query.content.get("rebalance_targets") or inputs.get("rebalance_targets")
    fee_model = query.content.get("fee_model") or inputs.get("fee_model")
    drift_threshold = (
        query.content.get("drift_threshold")
        if query.content.get("drift_threshold") is not None
        else inputs.get("drift_threshold", 5.0)
    )
    content = build_holding_rebalance(
        holdings,
        targets if isinstance(targets, dict) else None,
        drift_threshold,
        fee_model if isinstance(fee_model, dict) else None,
    )
    return _tier1_request_artifact(
        artifact_type="holding_rebalance",
        content=content,
        query=query,
    )


def evaluate_uzi_portfolio_rebalance(context):
    query = context.require_artifact("normalized_query")
    inputs = dict(context.node.inputs or {})
    holdings = list(
        query.content.get("portfolio_holdings")
        or inputs.get("portfolio_holdings")
        or []
    )
    targets = query.content.get("asset_class_targets") or inputs.get("asset_class_targets")
    drift_threshold = (
        query.content.get("drift_threshold")
        if query.content.get("drift_threshold") is not None
        else inputs.get("drift_threshold", 5.0)
    )
    content = build_portfolio_rebalance(
        holdings,
        targets if isinstance(targets, dict) else None,
        drift_threshold,
    )
    return _tier1_request_artifact(
        artifact_type="portfolio_rebalance",
        content=content,
        query=query,
    )


def _tier1_snapshot_artifact(
    *,
    artifact_type: str,
    content: dict,
    source_refs: list[dict],
    parent_ids: list[str],
) -> ArtifactDraft:
    quality = str(content.get("quality_status") or "data_gap")
    gaps = list(content.get("data_gap") or [])
    assumptions = list(content.get("assumption_flags") or [])
    confidence = 0.0 if quality == "data_gap" else 0.55 if quality in {"degraded", "assumption_only"} else 0.82
    return ArtifactDraft(
        artifact_type=artifact_type,
        content=content,
        source_refs=source_refs,
        parent_artifact_ids=parent_ids,
        confidence=confidence,
        risk_flags=list(dict.fromkeys([*gaps, *assumptions])),
        metadata={
            "quality_status": quality,
            "methodology_version": content.get("methodology_version"),
        },
    )


def _tier1_request_artifact(*, artifact_type: str, content: dict, query) -> ArtifactDraft:
    return _tier1_snapshot_artifact(
        artifact_type=artifact_type,
        content=content,
        source_refs=list(query.source_refs),
        parent_ids=[query.artifact_id],
    )


def compose_uzi_report(context):
    normalized_query = context.require_artifact("normalized_query")
    evidence = context.require_artifact("evidence_set")
    valuation = context.require_artifact("valuation_model")
    panel = (context.artifacts("investor_panel_review") or [None])[-1]
    risk = (context.artifacts("risk_scan") or [None])[-1]
    institutional = (context.artifacts("institutional_analysis") or [None])[-1]
    workflow = (context.artifacts("research_workflow") or [None])[-1]
    deep = (context.artifacts("deep_analysis") or [None])[-1]
    dimension_coverage = (context.artifacts("uzi_dimension_coverage") or [None])[-1]
    snapshot_bundle = (context.artifacts("stock_snapshot_bundle") or [None])[-1]
    inputs = dict(context.node.inputs or {})
    normalized_content = dict(normalized_query.content or {})
    holding_context = dict(normalized_content.get("holding_context") or {})
    profile = select_profile(inputs)
    evidence_items = list(evidence.content.get("items") or [])
    data_pipeline = (
        dict((snapshot_bundle.content or {}).get("collection") or {})
        if snapshot_bundle is not None
        else {}
    )
    base_content = {
        "conclusion": "",
        "assessment": {
            "status": "pack_methodology",
            "summary": "UZI Finance Pack methods are presented from the current ArtifactBoard.",
            "factors": [],
        },
        "evidence": [
            _report_evidence_item(item, index)
            for index, item in enumerate(evidence_items)
        ],
        "methodologies": [],
        "scorecard": None,
        "risk": [],
        "follow_up": [],
    }
    base_source_refs = list(evidence.source_refs)
    base_confidence = evidence.confidence
    base_risk_flags = ["decision_support_only"]
    panel_content = (
        dict(panel.content)
        if panel is not None
        else {
            "methodology_version": UZI_METHOD_VERSION,
            "profile": profile["selected"],
            "scope": "not_requested",
            "panel_version": "uzi-adapted-1",
            "votes": [],
            "distribution": {},
            "average_score": None,
            "disagreement": {"exists": False, "summary": "本次请求未要求 Panel 方法论视角。"},
            "confidence": 0.0,
            "risk_flags": ["panel_not_requested"],
        }
    )
    risk_content = (
        dict(risk.content)
        if risk is not None
        else {
            "methodology_version": UZI_METHOD_VERSION,
            "profile": profile["selected"],
            "risk_level": "data_gap",
            "signals": [],
            "signals_hit_count": 0,
            "recommendation": "本次请求未要求风险扫描，不能据此判断外部风险。",
            "confidence": 0.0,
            "risk_flags": ["risk_scan_not_requested"],
        }
    )
    report_content = dict(base_content)
    report_content.update(
        {
            "uzi_profile": profile,
            "valuation_model": dict(valuation.content),
            "investor_panel_review": panel_content,
            "risk_scan": risk_content,
            "institutional_analysis": dict(institutional.content) if institutional is not None else None,
            "research_workflow": dict(workflow.content) if workflow is not None else None,
            "deep_analysis": dict(deep.content) if deep is not None else None,
            "uzi_dimension_coverage": dict(dimension_coverage.content) if dimension_coverage is not None else None,
            "uzi_data_pipeline": data_pipeline or None,
            "presentation_artifact_type": "report_presentation",
            "holding_context": holding_context or None,
        }
    )
    report_content["conclusion"] = build_delivery_conclusion(
        valuation.content,
        panel_content,
        risk_content,
    )
    report_content["risk"] = [
        "证据完整性和时间新鲜度仍需在使用前复核。",
        str(risk_content.get("recommendation") or "风险扫描未返回建议。"),
        *[
            f"模型输入缺口：{warning}"
            for model in list(valuation.content.get("models") or [])
            for warning in list(model.get("warnings") or [])
        ],
    ]
    report_content["follow_up"] = [
        "复核引用的快照、公告和方法论假设后再使用研究结果。",
        "如需提高结论可靠性，补充同行、公告和外部风险核验数据。",
    ]
    source_refs = _unique_source_refs(
        [
            *base_source_refs,
            *list(valuation.source_refs),
            *(list(panel.source_refs) if panel is not None else []),
            *(list(risk.source_refs) if risk is not None else []),
            *(list(institutional.source_refs) if institutional is not None else []),
            *(list(workflow.source_refs) if workflow is not None else []),
            *(list(deep.source_refs) if deep is not None else []),
            *(list(dimension_coverage.source_refs) if dimension_coverage is not None else []),
            *(list(snapshot_bundle.source_refs) if snapshot_bundle is not None else []),
        ]
    )
    if holding_context.get("source_fund_artifact_id"):
        source_refs = _unique_source_refs(
            [
                *source_refs,
                {
                    "type": "fund_snapshot_artifact",
                    "id": str(holding_context["source_fund_artifact_id"]),
                    "locator": str(holding_context.get("source_fund_run_id") or ""),
                },
            ]
        )
    parents = [
        normalized_query.artifact_id,
        evidence.artifact_id,
        valuation.artifact_id,
        *( [panel.artifact_id] if panel is not None else [] ),
        *( [risk.artifact_id] if risk is not None else [] ),
        *( [institutional.artifact_id] if institutional is not None else [] ),
        *( [workflow.artifact_id] if workflow is not None else [] ),
        *( [deep.artifact_id] if deep is not None else [] ),
        *( [dimension_coverage.artifact_id] if dimension_coverage is not None else [] ),
        *( [snapshot_bundle.artifact_id] if snapshot_bundle is not None else [] ),
    ]
    confidence_values = [
        _artifact_confidence(base_confidence),
        _artifact_confidence(valuation.confidence),
    ]
    if panel is not None:
        confidence_values.append(_artifact_confidence(panel.confidence))
    if risk is not None:
        confidence_values.append(_artifact_confidence(risk.confidence))
    if institutional is not None:
        confidence_values.append(_artifact_confidence(institutional.confidence))
    if workflow is not None:
        confidence_values.append(_artifact_confidence(workflow.confidence))
    if deep is not None:
        confidence_values.append(_artifact_confidence(deep.confidence))
    if dimension_coverage is not None:
        confidence_values.append(_artifact_confidence(dimension_coverage.confidence))
    report = ArtifactDraft(
        artifact_type="research_report",
        content=report_content,
        source_refs=source_refs,
        parent_artifact_ids=parents,
        confidence=min(confidence_values),
        risk_flags=list(
            dict.fromkeys(
                [
                    *base_risk_flags,
                    *valuation.risk_flags,
                    *(list(panel.risk_flags) if panel is not None else ["panel_not_requested"]),
                    *(list(risk.risk_flags) if risk is not None else ["risk_scan_not_requested"]),
                    *(list(institutional.risk_flags) if institutional is not None else []),
                    *(list(workflow.risk_flags) if workflow is not None else []),
                    *(list(deep.risk_flags) if deep is not None else []),
                    *(list(dimension_coverage.risk_flags) if dimension_coverage is not None else []),
                ]
            )
        ),
        metadata={"profile": profile, "methodology_version": "3.9.2-adapted", "source_commit": profile.get("source_commit")},
    )
    presentation_content = build_presentation(
        report_content,
        valuation.content,
        panel_content,
        risk_content,
        profile,
        advanced={
            "institutional_analysis": institutional.content if institutional is not None else None,
            "research_workflow": workflow.content if workflow is not None else None,
            "deep_analysis": deep.content if deep is not None else None,
            "uzi_dimension_coverage": dimension_coverage.content if dimension_coverage is not None else None,
            "uzi_data_pipeline": data_pipeline or None,
        },
        snapshot_rows=evidence_items,
        data_pipeline=data_pipeline,
    )
    presentation_content["source_artifact_ids"] = parents
    presentation = ArtifactDraft(
        artifact_type="report_presentation",
        content=presentation_content,
        source_refs=source_refs,
        parent_artifact_ids=parents,
        confidence=report.confidence,
        risk_flags=list(report.risk_flags),
        metadata={"profile": profile, "methodology_version": "3.9.2-adapted", "source_commit": profile.get("source_commit")},
    )
    return [report, presentation]


def evaluate_research_safety_gate(context):
    content = context.artifact.content if isinstance(context.artifact.content, dict) else {}
    risk_scan = content.get("risk_scan") if isinstance(content.get("risk_scan"), dict) else {}
    reasons: list[str] = []
    if not context.artifact.source_refs:
        reasons.append("research report has no source references")
    if str(risk_scan.get("risk_level") or "") == "high":
        reasons.append("risk scan detected multiple promotion or rumor signals")
    if "critical_data_gap" in context.artifact.risk_flags:
        reasons.append("critical financial input is missing")
    return {
        "verdict": "fail" if reasons else "pass",
        "reason": "; ".join(reasons) if reasons else "research delivery passed source and safety checks",
        "evidence_refs": list(context.artifact.source_refs),
        "score": float(context.artifact.confidence or 0.0),
    }


def evaluate_methodology_quality_gate(context):
    """Validate method output quality without turning a data gap into a fact."""

    content = context.artifact.content if isinstance(context.artifact.content, dict) else {}
    quality = str(
        content.get("quality_status")
        or context.artifact.metadata.get("quality_status")
        or "available"
    ).strip().lower()
    if quality not in {"available", "degraded", "data_gap", "assumption_only"}:
        quality = "degraded" if list(content.get("data_gap") or []) else "available"
    allowed = {
        str(item).strip().lower()
        for item in list(context.config.get("allowed_quality_statuses") or ["available"])
    }
    reasons: list[str] = []
    if quality not in allowed:
        reasons.append(f"quality status {quality} is not allowed by the method contract")
    if bool(context.config.get("require_source_refs", True)) and not context.artifact.source_refs:
        reasons.append("method artifact has no source references")
    contract = content.get("method_input_contract")
    if isinstance(contract, dict) and contract.get("status") == "data_gap":
        missing = ", ".join(str(item) for item in list(contract.get("missing_fields") or [])[:6])
        if "data_gap" not in allowed:
            reasons.append(f"required method inputs are missing: {missing or 'unspecified'}")
    if reasons:
        return {
            "verdict": "fail",
            "reason": "; ".join(reasons),
            "evidence_refs": list(context.artifact.source_refs),
            "score": float(context.artifact.confidence or 0.0),
        }
    qualifier = {
        "available": "method output passed with complete declared inputs",
        "degraded": "method output passed with explicit degraded inputs",
        "assumption_only": "method output passed as an explicit assumption-only view",
        "data_gap": "method output passed as an explicit data-gap delivery",
    }[quality]
    return {
        "verdict": "pass",
        "reason": qualifier,
        "evidence_refs": list(context.artifact.source_refs),
        "score": float(context.artifact.confidence or 0.0),
    }


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
            "conclusion": _sector_pack_summary(
                sector=sector,
                analysis=analysis.content,
                findings=findings,
                heatmap=heatmap,
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


def _sector_pack_summary(*, sector: str, analysis: dict, findings: list, heatmap: list) -> str:
    """Provide a factual Pack fallback when Main Agent synthesis is unavailable."""

    fragments: list[str] = []
    for row in heatmap:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or sector).strip()
        score = _to_float(row.get("score"))
        if score is not None:
            fragments.append(f"{name} 热力评分 {score:g}")

    breadth = analysis.get("breadth") if isinstance(analysis.get("breadth"), dict) else {}
    if breadth:
        fragments.append(f"覆盖度字段 {len(breadth)} 项")

    relative_strength = (
        analysis.get("relative_strength")
        if isinstance(analysis.get("relative_strength"), dict)
        else {}
    )
    if relative_strength:
        fragments.append(f"相对强弱字段 {len(relative_strength)} 项")

    catalysts = analysis.get("catalysts") if isinstance(analysis.get("catalysts"), dict) else {}
    policy_signal_count = sum(
        len(value) for key, value in catalysts.items()
        if key in {"policy_signals", "regulatory_signals"} and isinstance(value, list)
    )
    if policy_signal_count:
        fragments.append(f"政策或监管信号 {policy_signal_count} 条")

    degraded_count = sum(
        1 for item in findings
        if isinstance(item, dict) and bool(item.get("degraded"))
    )
    if degraded_count:
        fragments.append(f"降级数据源 {degraded_count} 个")

    if not fragments:
        return f"{sector}：当前证据未形成可解释的板块信号，已保留 {len(findings)} 条来源快照供复核。"
    return f"{sector}：" + "；".join(fragments) + "。"


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


def _artifact_has_usable_result(artifact: object) -> bool:
    if artifact is None:
        return False
    content = getattr(artifact, "content", None)
    if not isinstance(content, dict):
        return False
    quality = str(content.get("quality_status") or content.get("status") or "").strip().lower()
    return quality in {"available", "degraded", "assumption_only"}


def _methodology_warnings(methodologies: list[dict]) -> list[str]:
    warnings: list[str] = []
    for methodology in methodologies:
        if not isinstance(methodology, dict):
            continue
        for warning in [
            *list(methodology.get("warnings") or []),
            *list(methodology.get("data_gap") or []),
            *list(methodology.get("assumption_flags") or []),
        ]:
            text = str(warning)
            if text and text not in warnings:
                warnings.append(text)
    return warnings


def _required_investment_cycle(inputs: dict) -> str:
    value = str(inputs.get("investment_cycle") or "").strip()
    if value not in {"short_term", "mid_term", "long_term"}:
        raise ValueError("investment_cycle is required; choose short_term, mid_term or long_term")
    return value


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
    requested_methods = _unique_strings(inputs.get("requested_methods") or [])
    method_snapshot_types: dict[str, tuple[str, ...]] = {
        "ai_readiness": ("market", "chain", "industry", "events", "research", "moat"),
        "earnings_preview": ("market", "fundamental", "research", "events", "kline"),
        "model_update": ("market", "fundamental", "valuation", "peers"),
        "three_statement": ("fundamental",),
        "quick_lbo": ("fundamental",),
        "lbo": ("fundamental",),
        "accretion_dilution": ("fundamental",),
        "ma_accretion": ("fundamental",),
        "segmental": ("fundamental",),
        "initiation_report": ("market", "fundamental", "chain", "industry", "events", "valuation"),
        "earnings_review": ("fundamental", "research"),
        "catalyst_calendar": ("events",),
        "thesis_tracker": ("market", "fundamental", "valuation"),
        "morning_note": ("market", "capital_flow", "sentiment", "events"),
        "ic_memo": ("market", "fundamental", "chain", "industry", "events", "valuation", "governance", "trap"),
        "porter_bcg": ("moat", "industry", "peers", "chain"),
        "dd_checklist": ("fundamental", "valuation", "industry", "peers", "chain", "governance", "notice", "policy", "sentiment", "events", "trap"),
        "unit_economics": ("fundamental",),
        "value_creation_plan": ("fundamental",),
    }
    method_required = _unique_strings(
        [
            snapshot_type
            for method in requested_methods
            for snapshot_type in method_snapshot_types.get(method, ())
        ]
    )
    explicit = inputs.get("snapshot_types")
    if isinstance(explicit, list) and explicit:
        return _unique_strings([*explicit, *method_required])
    intent = str(inputs.get("intent") or "").strip()
    if intent == "sector_research":
        return [
            "sector",
            *(["policy"] if inputs.get("include_policy_snapshot") is not False else []),
        ]
    if intent == "stock_screen":
        return ["sector", "market", "fundamental", "notice"] if sector else ["market", "fundamental", "notice"]
    return _unique_strings([
        *(select_profile(inputs).get("snapshot_types") or [
            "market", "fundamental", "notice", "capital_flow", "sentiment", "events", "lhb"
        ]),
        *method_required,
    ])


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
    rows, _symbols, _query, source_refs, parent_ids = _snapshot_context(context)
    inputs = dict(context.node.inputs or {})
    bundle = (_context_artifacts(context, "stock_snapshot_bundle") or [None])[-1]
    bundle_content = dict(bundle.content) if bundle is not None else {}
    universe = {
        str(row.get("symbol") or ""): dict(row)
        for row in bundle_content.get("candidate_universe") or []
        if isinstance(row, dict) and row.get("symbol")
    }
    collection = dict(bundle_content.get("collection") or {})
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
    cycle = _required_investment_cycle(inputs)
    cycle_min_score = {"short_term": 0.0, "mid_term": 0.0, "long_term": 0.15}[cycle]
    min_score = _to_float(inputs.get("min_score"))
    min_score = cycle_min_score if min_score is None else min_score
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
        "investment_cycle_assumption": "explicit",
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
        source_refs=source_refs,
        parent_artifact_ids=parent_ids,
        confidence=0.8 if rows else 0.0,
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
    rows, _symbols, query, source_refs, parent_ids = _snapshot_context(context)
    bundle = (_context_artifacts(context, "stock_snapshot_bundle") or [None])[-1]
    content = dict(bundle.content) if bundle is not None else {}
    inputs = dict(context.node.inputs or {})
    rows = [row for row in rows if isinstance(row, dict)]
    sector_rows = [row for row in rows if row.get("snapshot_type") == "sector"]
    policy_rows = [row for row in rows if row.get("snapshot_type") == "policy"]
    usable_rows = [row for row in rows if _snapshot_quality(row) != "data_gap"]
    usable_sector_rows = [row for row in sector_rows if _snapshot_quality(row) != "data_gap"]
    usable_policy_rows = [row for row in policy_rows if _snapshot_quality(row) != "data_gap"]
    sector_snapshot = sector_rows[0] if sector_rows else {}
    facts = sector_snapshot.get("facts") or {}
    sector = inputs.get("sector") or content.get("sector") or sector_snapshot.get("sector") or facts.get("sector") or content.get("query") or query
    theme = content.get("theme") or sector_snapshot.get("theme") or facts.get("theme")
    related = [row for row in usable_rows if row not in usable_sector_rows]
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
        source_count=len(usable_rows),
        related_count=len(related),
    )
    include_policy = inputs.get("include_policy_snapshot") is not False
    risks = ["Sector evidence may be incomplete or stale."]
    if not related:
        risks.append("No related sector or constituent snapshots were supplied.")
    if include_policy and not usable_policy_rows:
        risks.append("The requested policy snapshot was unavailable.")
    return ArtifactDraft(
        artifact_type="sector_analysis",
        content={
            "sector": sector,
            "theme": theme,
            "findings": rows,
            "related_snapshots": related,
            "risks": risks,
                "source_count": len(usable_rows),
            "analysis_notes": [
                "sector_snapshot_present" if sector_rows else "missing_sector_snapshot",
                "policy_snapshot_requested" if include_policy else "policy_snapshot_not_requested",
                "policy_snapshot_present" if usable_policy_rows else "missing_policy_snapshot",
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
        source_refs=source_refs,
        parent_artifact_ids=parent_ids,
        confidence=0.8 if rows else 0.0,
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
        author = str(row.get("author") or row.get("user_name") or "").strip() or None
        published_at = str(row.get("published_at") or row.get("created_at") or "").strip() or None
        records.append(
            {
                "record_id": record_id,
                "text": text,
                "author": author,
                "published_at": published_at,
                "tags": _unique_strings(row.get("tags") or []),
                "dimensions": _unique_strings(row.get("dimensions") or []),
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
