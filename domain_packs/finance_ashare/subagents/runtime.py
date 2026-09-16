from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import date
from typing import Any

from domain_packs.finance_ashare.subagents.fund_methodology import build_fund_analysis
from domain_packs.finance_ashare.subagents.uzi_392_analysis import (
    execute_native_analysis,
)
from domain_packs.finance_ashare.subagents.uzi_392_native_commands import (
    execute_panel_only,
    execute_scan_trap,
    execute_segmental_model,
)
from domain_packs.finance_ashare.subagents.uzi_392_native_methods import (
    execute_native_method,
)
from domain_packs.finance_ashare.subagents.uzi_methodology import (
    build_quick_scan,
    select_profile,
)
from domain_packs.finance_ashare.subagents.uzi_portfolio_contract import (
    normalize_portfolio_holdings,
    result_ticker,
    result_ticker_identities,
    ticker_identity,
)
from domain_packs.finance_ashare.tools.snapshot_collector import collect_snapshot_bundle
from research_system.adapters.vendors.akshare_utils import coerce_numeric
from research_system.market_data.report_inputs import (
    ReportInputError,
    validate_bundle_only_input,
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
    portfolio_holdings = list(
        request.content.get("portfolio_holdings")
        or inputs.get("portfolio_holdings")
        or []
    )
    for holding in portfolio_holdings:
        if not isinstance(holding, dict):
            continue
        ticker = str(holding.get("ticker") or holding.get("symbol") or "").strip()
        if ticker and ticker not in symbols:
            symbols.append(ticker)
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
            "portfolio_holdings": portfolio_holdings,
            "portfolio_name": str(
                request.content.get("portfolio_name")
                or inputs.get("portfolio_name")
                or ""
            ).strip() or None,
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
            "model_updates": dict(
                request.content.get("model_updates")
                or inputs.get("model_updates")
                or {}
            ) or None,
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
        from domain_packs.finance_ashare.skills.fund_snapshot.skill import (
            FundSnapshotSkill,
        )

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
    request_artifacts = _context_artifacts(context, "user_request")
    request_content = (
        dict(request_artifacts[-1].content or {})
        if request_artifacts
        else {}
    )
    intent = str(inputs.get("intent") or "").strip()
    sector = str(inputs.get("sector") or inputs.get("sector_query") or "").strip() or None
    snapshots = list(
        query.content.get("snapshots")
        or request_content.get("snapshots")
        or inputs.get("snapshots")
        or []
    )
    data_source_mode = str(inputs.get("data_source_mode") or "").strip().lower()
    if data_source_mode == "bundle_only":
        _assert_frozen_workflow_identity(context)
        # This is a second, in-graph hard gate.  Even if a caller bypasses API
        # preflight, formal reports can only consume the exact frozen Bundle and
        # can never fall through to ``financial_data_provider``.
        validate_bundle_only_input(
            snapshots=[row for row in snapshots if isinstance(row, dict)],
            manifest=(
                request_content.get("report_data_manifest")
                if isinstance(request_content.get("report_data_manifest"), dict)
                else inputs.get("report_data_manifest")
                if isinstance(inputs.get("report_data_manifest"), dict)
                else None
            ),
            bundle=(
                request_content.get("market_data_bundle")
                if isinstance(request_content.get("market_data_bundle"), dict)
                else inputs.get("market_data_bundle")
                if isinstance(inputs.get("market_data_bundle"), dict)
                else None
            ),
            symbols=symbols,
        )
    requested_types = _requested_snapshot_types(inputs=inputs, sector=sector)
    collection_profile = _collection_research_profile(inputs)
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
    if data_source_mode == "bundle_only":
        manifest = dict(
            request_content.get("report_data_manifest")
            or inputs.get("report_data_manifest")
            or {}
        )
        collection.update(
            {
                "data_source_mode": "bundle_only",
                "manifest_id": manifest.get("manifest_id"),
                "manifest_hash": manifest.get("manifest_hash"),
                "as_of_date": manifest.get("as_of_date"),
                "universe_source": "frozen_bundle",
            }
        )
    provider_source_refs: list[dict] = []
    if not snapshots:
        provider_arguments = {
            "query": query_text,
            "symbols": symbols,
            "snapshot_types": requested_types,
            "intent": intent,
            "sector": sector,
            "candidate_pool_size": _integer_or_default(inputs.get("candidate_pool_size"), 8),
            "enrichment_top_n": _integer_or_default(inputs.get("enrichment_top_n"), 2),
            "skip_enrichment": bool(inputs.get("skip_enrichment")),
            "research_profile": collection_profile,
            "no_resume": bool(inputs.get("no_resume")),
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
            collected = _collect_provider_snapshots(
                query=query_text,
                symbols=symbols,
                snapshot_types=requested_types,
                intent=intent,
                sector=sector,
                research_profile=collection_profile,
                candidate_pool_size=_integer_or_default(inputs.get("candidate_pool_size"), 8),
                enrichment_top_n=_integer_or_default(inputs.get("enrichment_top_n"), 2),
                skip_enrichment=bool(inputs.get("skip_enrichment")),
                no_resume=bool(inputs.get("no_resume")),
                cancellation=getattr(context, "cancellation", None),
            )
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
            intent=intent,
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
    symbols = _unique_tickers(symbols)
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
    source_refs = list(
        request_content.get("source_refs")
        or inputs.get("source_refs")
        or []
    )
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
    query_manifest = (
        snapshot.get("query_manifest")
        if isinstance(snapshot.get("query_manifest"), dict)
        else {}
    )
    degradation = (
        query_manifest.get("degradation")
        if isinstance(query_manifest.get("degradation"), dict)
        else {}
    )
    values = [
        *list(snapshot.get("degrade_reasons") or []),
        snapshot.get("degrade_reason"),
        *list(facts.get("data_gap") or []),
        degradation.get("reason_code"),
        degradation.get("message"),
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
    intent: str,
) -> list[dict[str, Any]]:
    """Materialize explicit data-gap facts for requested but unavailable inputs."""

    rows = list(snapshots)
    symbol_root_intents = {
        "stock_check",
        "uzi_analyze_stock",
        "stock_compare",
        "uzi_portfolio_batch",
    }

    def is_sector_scoped(snapshot_type: str) -> bool:
        if snapshot_type == "sector":
            return True
        return (
            snapshot_type in {"policy", "macro"}
            and intent not in symbol_root_intents
        )

    def target_key(snapshot_type: str, row: dict[str, Any]) -> str:
        if is_sector_scoped(snapshot_type):
            return str(row.get("sector") or sector or "").strip()
        return ticker_identity(row.get("symbol"))

    for snapshot_type in _unique_strings(requested_types):
        sector_scoped = is_sector_scoped(snapshot_type)
        targets = [None] if sector_scoped else (symbols or [None])
        for symbol in targets:
            expected_target = (
                str(sector or "").strip()
                if sector_scoped
                else ticker_identity(symbol)
            )
            if any(
                str(row.get("snapshot_type") or "").strip() == snapshot_type
                and target_key(snapshot_type, row) == expected_target
                for row in rows
            ):
                continue
            rows.append(
                {
                    "snapshot_type": snapshot_type,
                    "symbol": None if sector_scoped else symbol,
                    "sector": sector if sector_scoped else None,
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
    has_symbol = bool(str(snapshot.get("symbol") or "").strip())
    scope = "symbol" if has_symbol else "sector"
    target = str(
        snapshot.get("symbol")
        if has_symbol
        else snapshot.get("sector") or "unscoped"
    ).strip()
    return f"{scope}:{target}:{snapshot_type}"


def _snapshot_artifact_content(snapshot: dict[str, Any]) -> dict[str, Any]:
    facts = dict(snapshot.get("facts")) if isinstance(snapshot.get("facts"), dict) else {}
    # Sector providers historically returned ``theme`` beside ``facts``.  The
    # independent Snapshot artifact is the analyzer's canonical input, so keep
    # that provider observation inside the schema-backed facts envelope.
    if snapshot.get("theme") is not None and "theme" not in facts:
        facts["theme"] = snapshot.get("theme")
    source = snapshot.get("source") if isinstance(snapshot.get("source"), dict) else {}
    query_manifest = (
        dict(snapshot.get("query_manifest"))
        if isinstance(snapshot.get("query_manifest"), dict)
        else {}
    )
    degradation = (
        dict(query_manifest.get("degradation"))
        if isinstance(query_manifest.get("degradation"), dict)
        else {}
    )
    snapshot_type = str(snapshot.get("snapshot_type") or "snapshot").strip()
    scope = "symbol" if snapshot.get("symbol") else (
        "sector" if snapshot.get("sector") or snapshot_type in {"sector", "policy", "macro"} else "symbol"
    )
    return {
        "snapshot_type": snapshot_type,
        # Keep data-centre identity through the independent Artifact hop. The
        # Artifact's own ID/hash identifies a projection, not the source row.
        **{key: snapshot[key] for key in ("snapshot_id", "content_hash")
           if snapshot.get(key)},
        "dimension_id": str(
            snapshot.get("dimension_id") or snapshot.get("dimension") or ""
        )
        or None,
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
        "query_manifest": query_manifest,
        "scoring_policy": {
            "excluded": degradation.get("excluded_from_scoring") is True,
            "reason_code": degradation.get("reason_code"),
            "reason": degradation.get("message"),
        },
        "facts": facts,
        "source": source,
        "source_trace": [
            dict(item)
            for item in list(snapshot.get("source_trace") or [])
            if isinstance(item, dict)
        ],
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
    # These are already materialized from the exact frozen evidence bindings.
    # Preserve the archived content, hashes and uses; synthetic provenance only
    # supplements them. Never resolve live evidence or download a URL here.
    refs = [deepcopy(ref) for ref in snapshot.get("source_refs") or [] if isinstance(ref, dict)]
    refs.append({"type": "snapshot", "provider": provider, "id": identifier})
    for trace in list(snapshot.get("source_trace") or []):
        if not isinstance(trace, dict):
            continue
        trace_provider = str(
            trace.get("provider_id") or trace.get("source_id") or provider
        ).strip()
        trace_id = str(trace.get("source_id") or trace_provider).strip()
        if not trace_id:
            continue
        refs.append(
            {
                "type": "snapshot_source",
                "provider": trace_provider or provider,
                "id": trace_id,
            }
        )
    return _unique_source_refs(refs)


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
        source_refs.extend(row["source_refs"])
        symbol = str(content.get("symbol") or "").strip()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
        if not query:
            query = str(content.get("query") or "")

    symbols = _unique_tickers([*symbols, *[str(row.get("symbol") or "") for row in rows]])
    return rows, symbols, query, _unique_source_refs(source_refs), list(dict.fromkeys(parent_ids))


def _unique_tickers(symbols: list[str]) -> list[str]:
    """Keep presentation spelling while matching only market-aware aliases."""
    result: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        identity = ticker_identity(symbol)
        if identity and identity not in seen:
            seen.add(identity)
            result.append(symbol)
    return result


def _assert_frozen_workflow_identity(context) -> None:
    """Recheck identity and historical safety before executing cached inputs."""
    requests = _context_artifacts(context, "user_request")
    inputs = dict(context.node.inputs or {})
    request = dict(requests[-1].content or {}) if requests else {}
    manifest = request.get("report_data_manifest") or inputs.get("report_data_manifest") or {}
    pins = dict(manifest.get("workflow_pins") or {}) if isinstance(manifest, dict) else {}
    from research_system.strategies.runtime_pins import deployed_prompt_hash

    if all(pins.get(key) for key in ("workflow_commit", "model_id", "prompt_hash")) and pins["prompt_hash"] != deployed_prompt_hash():
        raise ReportInputError(
            "report_workflow_version_mismatch",
            "冻结报告执行版本与当前部署不一致；请使用原运行环境或创建报告修订，不能原地替换输入或复用旧缓存。",
        )
    # Legacy inputs retain compatibility, not a waiver for explicit future
    # prices. Recovery can skip the collector, so guard the fixed input here.
    from research_system.market_data.commodity_pit import (
        commodity_empty_query_errors,
        commodity_fact_date_errors,
    )

    for item in manifest.get("items", []) if isinstance(manifest, dict) else []:
        try:
            as_of = date.fromisoformat(str((item.get("bundle") or {}).get("as_of_date") or manifest.get("as_of_date")))
        except (TypeError, ValueError):
            continue  # Structural manifest validation owns missing dates.
        for row in item.get("snapshots") or []:
            errors = commodity_fact_date_errors(
                row.get("facts") or {},
                dimension=str(row.get("dimension_id") or row.get("snapshot_type") or ""),
                as_of_date=as_of,
            )
            if errors:
                raise ReportInputError(
                    "bundle_snapshot_future_price_fact",
                    "冻结输入含截止日之后的商品价格事实，必须补采并创建修订，不能恢复旧模型阶段：" + errors[0],
                )
            if commodity_empty_query_errors(
                row.get("query_manifest") or {},
                dimension=str(row.get("dimension_id") or row.get("snapshot_type") or ""),
                as_of_date=as_of,
            ):
                raise ReportInputError(
                    "bundle_snapshot_unproven_empty_result",
                    "冻结历史商品空结果没有实际合约查询证明，需补采并创建修订，不能恢复旧模型阶段。",
                )


def select_evidence(context):
    rows, symbols, query, source_refs, parent_ids = _snapshot_context(context)
    return ArtifactDraft(
        artifact_type="evidence_set",
        content={"items": rows, "symbols": symbols, "query": query},
        source_refs=source_refs,
        parent_artifact_ids=parent_ids,
        confidence=0.8 if rows else 0.0,
    )


def compose_uzi_quick_scan(context):
    rows, symbols, _query, source_refs, parent_ids = _snapshot_context(context)
    content = build_quick_scan(rows, symbols)
    return ArtifactDraft(
        artifact_type="quick_scan",
        content=content,
        source_refs=source_refs,
        parent_artifact_ids=parent_ids,
        confidence=float(content.get("confidence") or 0.0),
        risk_flags=list(content.get("risk_flags") or []),
        metadata={
            "quality_status": content.get("quality_status"),
            "methodology_version": content.get("methodology_version"),
            "source_commit": content.get("source_commit"),
            "output_format": "markdown",
        },
    )


def _frozen_quant_auxiliary_inputs(context) -> dict[str, dict[str, Any]] | None:
    """Read only the artifact-backed frozen recording, never current evidence."""
    from research_system.market_data.report_inputs import ReportInputError

    inputs = dict(context.node.inputs or {})
    request_artifact = (_context_artifacts(context, "user_request") or [None])[-1]
    request = dict(request_artifact.content or {}) if request_artifact is not None else {}
    manifest = request.get("report_data_manifest") or inputs.get("report_data_manifest") or {}
    if not (manifest.get("workflow_pins") or {}).get("native_quant_auxiliary_version"):
        return None
    result = {}
    for item in manifest.get("items") or []:
        binding = (item.get("context") or {}).get("native_quant_auxiliary")
        recording = item.get("native_quant_auxiliary_recording")
        symbol = str(item.get("symbol") or "").strip()
        if not symbol or not isinstance(binding, dict) or not isinstance(recording, dict):
            raise ReportInputError("native_quant_binding_missing", "冻结辅助输入缺失，需创建数据修订；报告不能临时补取")
        if symbol in result:
            raise ReportInputError("native_quant_binding_ambiguous", "同一股票存在多个辅助输入绑定")
        result[symbol] = {"binding": binding, "recording": recording}
    return result


def execute_uzi_native_full_analysis(context):
    # Also guard restored/cached Snapshot nodes before starting model work.
    _assert_frozen_workflow_identity(context)
    rows, symbols, _query, source_refs, parent_ids = _snapshot_context(context)
    inputs = dict(context.node.inputs or {})
    profile = select_profile(inputs)
    normalized = (_context_artifacts(context, "normalized_query") or [None])[-1]
    holding_context = (
        dict((normalized.content or {}).get("holding_context") or {})
        if normalized is not None
        else {}
    )
    bundle = (_context_artifacts(context, "stock_snapshot_bundle") or [None])[-1]
    recovery_history = []
    if bundle is not None:
        recovery = dict((bundle.content or {}).get("collection") or {}).get("uzi_recovery")
        if isinstance(recovery, dict):
            recovery_history = list(recovery.get("attempts") or [])
        parent_ids = list(dict.fromkeys([*parent_ids, bundle.artifact_id]))
        source_refs = _unique_source_refs([*source_refs, *list(bundle.source_refs)])
    progress_emitter = getattr(context, "progress_emitter", None)
    highest_progress = 0

    def emit_progress(progress: dict[str, Any]) -> None:
        nonlocal highest_progress
        if not callable(progress_emitter):
            return
        requested_percent = max(0, min(99, int(progress.get("progress_percent") or 0)))
        if progress.get("stage") == "analysis_cycle":
            # A new stock/recovery cycle has its own validated-stage ledger.
            # Do not keep a previous cycle's 90% while repeating its research.
            highest_progress = requested_percent
        highest_progress = max(highest_progress, requested_percent)
        progress_emitter({
            "node_id": context.node.node_id,
            "phase": "uzi_native_analysis",
            "status": "running",
            "progress_source": "runtime",
            "current_action": str(progress.get("current_action") or "正在执行 UZI 结构化分析"),
            "next_action": str(progress.get("next_action") or "继续重算并执行机械自审"),
            "progress_percent": highest_progress,
            "stage_index": progress.get("stage_index"),
            "stage_total": progress.get("stage_total"),
            "completed_stages": progress.get("completed_stages"),
            "stage_phase": progress.get("stage_phase"),
            **{key: progress[key] for key in (
                "progress_scope", "workflow_phase", "workflow_completed_stages", "workflow_total_stages",
            ) if key in progress},
            "uzi_stage": progress.get("stage"),
            "analysis_cycle": progress.get("cycle"),
            "request_phase": progress.get("request_phase"),
            "request_attempt": progress.get("request_attempt"),
            "request_timing": progress.get("request_timing"),
            "cache_hit": progress.get("cache_hit"),
            "cache_reason": progress.get("cache_reason"),
        })

    content = execute_native_analysis(
        rows,
        symbols,
        profile=str(profile.get("selected") or ""),
        school=str(inputs.get("school") or "").strip().upper() or None,
        llm_provider=getattr(context, "llm_provider", None),
        cancellation=getattr(context, "cancellation", None),
        deadline_at=getattr(context, "deadline_at", None),
        recovery_history=recovery_history,
        progress_callback=emit_progress,
        full_agent_workflow=(
            dict(profile.get("agent_workflow") or {}).get("mode") == "full"
        ),
        auxiliary_inputs=_frozen_quant_auxiliary_inputs(context),
    )
    content["holding_context"] = holding_context or None
    if normalized is not None:
        parent_ids = list(dict.fromkeys([*parent_ids, normalized.artifact_id]))
        source_refs = _unique_source_refs([*source_refs, *list(normalized.source_refs)])
    if holding_context.get("source_fund_artifact_id"):
        source_refs = _unique_source_refs([
            *source_refs,
            {
                "type": "fund_snapshot_artifact",
                "id": str(holding_context["source_fund_artifact_id"]),
                "locator": str(holding_context.get("source_fund_run_id") or ""),
            },
        ])
    quality_status = str(content.get("quality_status") or "quality_gate_blocked")
    return ArtifactDraft(
        artifact_type="uzi_native_analysis",
        content=content,
        source_refs=source_refs,
        parent_artifact_ids=parent_ids,
        confidence=(
            0.86
            if quality_status == "available"
            else 0.5
            if quality_status == "degraded"
            else 0.0
        ),
        risk_flags=[
            str(issue.get("issue") or issue.get("dim") or "critical")
            for result in list(content.get("results") or [])
            for issue in list((result.get("self_review") or {}).get("issues") or [])
            if isinstance(issue, dict) and issue.get("severity") == "critical"
        ],
        metadata={
            "quality_status": content.get("quality_status"),
            "methodology_version": content.get("methodology_version"),
            "source_commit": content.get("source_commit"),
            "report_allowed": content.get("report_allowed"),
        },
    )


def _uzi_review_layers(result: dict[str, Any]) -> dict[str, Any]:
    """Expose check ownership without reinterpreting legacy combined counts.

    Keep large roleplay execution payloads in the source analysis artifact. A
    delivery carries only the independently recorded check summaries.
    """
    review = result.get("self_review") or {}
    layers = {}
    for key in ("native_python_review", "native_skill_completion", "execution_status", "frozen_input_safety"):
        value = review.get(key)
        layers[key] = (
            deepcopy({name: item for name, item in value.items()
                      if name not in {"roleplay", "qualitative_execution"}})
            if isinstance(value, dict) else {"status": "not_recorded"}
        )
    dependencies = review.get("input_dependencies")
    if isinstance(dependencies, dict):
        # Exact requests stay in the source analysis artifact, not every panel.
        layers["input_dependencies"] = deepcopy({
            name: value for name, value in dependencies.items() if name != "missing_requests"
        })
    return layers


def compose_uzi_native_delivery(context):
    analysis = context.require_artifact("uzi_native_analysis")
    content = dict(analysis.content or {})
    results = [row for row in list(content.get("results") or []) if isinstance(row, dict)]
    first = results[0] if results else {}
    synthesis = dict(first.get("synthesis") or {})
    review = dict(first.get("self_review") or {})
    critical_issues = [
        row
        for result in results
        for row in list((result.get("self_review") or {}).get("issues") or [])
        if isinstance(row, dict) and row.get("severity") == "critical"
    ]
    warning_issues = [
        row
        for result in results
        for row in list((result.get("self_review") or {}).get("issues") or [])
        if isinstance(row, dict) and row.get("severity") == "warning"
    ]
    allowed = content.get("report_allowed") is True
    holding_context = dict(content.get("holding_context") or {})
    conclusion = str(
        (synthesis.get("dashboard") or {}).get("core_conclusion")
        or synthesis.get("verdict_label")
        or (
            "报告交付检查未通过，请分别查看原生自审、研究完成度与执行状态；HTML/PDF 已禁用。"
            if not allowed
            else "报告交付检查已通过；原生自审与研究完成度分别记录。"
        )
    )
    report_content = {
        "conclusion": conclusion,
        "assessment": {
            "status": "uzi_native_passed" if allowed else "uzi_native_quality_gate_blocked",
            "review_scope": "combined_delivery_gate",
            "summary": conclusion,
            "factors": [
                {
                    "symbol": row.get("symbol"),
                    "overall_score": (row.get("synthesis") or {}).get("overall_score"),
                    "verdict": (row.get("synthesis") or {}).get("verdict_label"),
                    "critical_count": (row.get("self_review") or {}).get("critical_count"),
                    "warning_count": (row.get("self_review") or {}).get("warning_count"),
                    "review_layers": _uzi_review_layers(row),
                }
                for row in results
            ],
        },
        "evidence": [
            {
                "artifact_id": analysis.artifact_id,
                "artifact_type": "uzi_native_analysis",
                "source_refs": list(analysis.source_refs),
            }
        ],
        "risk": [str(row.get("issue") or "") for row in [*critical_issues, *warning_issues]],
        "follow_up": [str(row.get("suggested_fix") or "") for row in critical_issues],
        "quality_status": (
            str(content.get("quality_status") or "degraded")
            if allowed
            else "quality_gate_blocked"
        ),
        "delivery_type": "uzi_native_3_9_4",
        "native_analysis_artifact_id": analysis.artifact_id,
        "report_allowed": allowed,
        "critical_count": int(content.get("critical_count") or 0),
        "warning_count": int(content.get("warning_count") or review.get("warning_count") or 0),
        "holding_context": holding_context or None,
    }
    return ArtifactDraft(
        artifact_type="research_report",
        content=report_content,
        source_refs=list(analysis.source_refs),
        parent_artifact_ids=[analysis.artifact_id],
        confidence=analysis.confidence if allowed else 0.0,
        risk_flags=list(analysis.risk_flags),
        metadata={
            "quality_status": report_content["quality_status"],
            "methodology_version": content.get("methodology_version"),
            "source_commit": content.get("source_commit"),
            "report_allowed": allowed,
        },
    )


def compose_stock_comparison_delivery(context):
    """Bind gate-passed analyses to the pinned UZI ``--versus`` renderer."""

    analysis = context.require_artifact("uzi_native_analysis")
    content = dict(analysis.content or {})
    results = [row for row in list(content.get("results") or []) if isinstance(row, dict)]
    factors = [
        {
            "symbol": str(row.get("symbol") or ""),
            "name": str((row.get("synthesis") or {}).get("name") or ""),
            "overall_score": (row.get("synthesis") or {}).get("overall_score"),
            "verdict": (row.get("synthesis") or {}).get("verdict_label"),
            "critical_count": int((row.get("self_review") or {}).get("critical_count") or 0),
            "warning_count": int((row.get("self_review") or {}).get("warning_count") or 0),
            "report_allowed": row.get("report_allowed") is True,
            "review_layers": _uzi_review_layers(row),
        }
        for row in results
    ]
    critical_issues = [
        issue
        for row in results
        for issue in list((row.get("self_review") or {}).get("issues") or [])
        if isinstance(issue, dict) and issue.get("severity") == "critical"
    ]
    warning_issues = [
        issue
        for row in results
        for issue in list((row.get("self_review") or {}).get("issues") or [])
        if isinstance(issue, dict) and issue.get("severity") == "warning"
    ]
    successful_results = [row for row in results if row.get("report_allowed") is True]
    failed_results = [
        _uzi_aggregate_failed_result(row)
        for row in results
        if row.get("report_allowed") is not True
    ]
    # Pinned UZI ``run_versus`` skips failed/cache-missing symbols and renders
    # as soon as at least two bundles remain.  Aggregate delivery therefore
    # gates the loaded subset, not every requested symbol.
    allowed = len(successful_results) >= 2
    loaded_warning_count = sum(
        int((row.get("self_review") or {}).get("warning_count") or 0)
        for row in successful_results
    )
    ranked = sorted(
        [row for row in factors if row.get("report_allowed") is True],
        key=lambda row: (
            row.get("overall_score") is not None,
            float(row.get("overall_score") or 0),
        ),
        reverse=True,
    )
    if allowed:
        conclusion = "；".join(
            f"{row['name'] or row['symbol']}：{row['verdict'] or '未给出结论'}"
            + (f"（{float(row['overall_score']):g} 分）" if row.get("overall_score") is not None else "")
            for row in ranked
        ) or "UZI 比较研究已完成。"
        if failed_results:
            conclusion += f"；成功载入 {len(successful_results)}/{len(results)} 个标的，其余标的已跳过。"
    else:
        conclusion = f"仅成功载入 {len(successful_results)}/{len(results)} 个标的；UZI --versus 至少需要 2 个。"
    report_content = {
        "conclusion": conclusion,
        "assessment": {
            "status": "completed" if allowed else "insufficient_data",
            "review_scope": "combined_delivery_gate",
            "summary": conclusion,
            "factors": factors,
            "loaded": len(successful_results),
            "requested": len(results),
            "failed": failed_results,
        },
        "evidence": [{
            "artifact_id": analysis.artifact_id,
            "artifact_type": "uzi_native_analysis",
            "source_refs": list(analysis.source_refs),
        }],
        "risk": [
            str(issue.get("message") or issue.get("issue") or "")
            for issue in [*critical_issues, *warning_issues]
            if str(issue.get("message") or issue.get("issue") or "").strip()
        ],
        "follow_up": [
            str(issue.get("suggested_fix") or "")
            for issue in critical_issues
            if str(issue.get("suggested_fix") or "").strip()
        ],
        "delivery_type": "uzi_versus_3_9_4",
        "quality_status": (
            "degraded"
            if allowed
            and any(row.get("quality_status") == "degraded" for row in successful_results)
            else "available"
            if allowed
            else "data_gap"
        ),
        "native_analysis_artifact_id": analysis.artifact_id,
        "report_allowed": allowed,
        # Aggregate runners model unavailable constituents as ``failed`` rather
        # than leaking their self-review criticals into the successful subset.
        "critical_count": 0,
        "warning_count": loaded_warning_count,
        "comparison": {
            "ranked_results": ranked,
            "source_commit": content.get("source_commit"),
            "loaded": len(successful_results),
            "failed": failed_results,
        },
    }
    return ArtifactDraft(
        artifact_type="research_report",
        content=report_content,
        source_refs=list(analysis.source_refs),
        parent_artifact_ids=[analysis.artifact_id],
        confidence=analysis.confidence if allowed else 0.0,
        risk_flags=list(analysis.risk_flags),
        metadata={
            "quality_status": report_content["quality_status"],
            "methodology_version": content.get("methodology_version"),
            "source_commit": content.get("source_commit"),
            "report_allowed": allowed,
        },
    )


def compose_uzi_portfolio_delivery(context):
    """Bind explicit holdings and native analyses to UZI ``--portfolio``."""

    query = context.require_artifact("normalized_query")
    analysis = context.require_artifact("uzi_native_analysis")
    content = dict(analysis.content or {})
    query_content = dict(query.content or {})
    results = [row for row in list(content.get("results") or []) if isinstance(row, dict)]
    holdings = normalize_portfolio_holdings([
        dict(row)
        for row in list(query_content.get("portfolio_holdings") or [])
        if isinstance(row, dict)
    ])
    matched: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    used_result_indexes: set[int] = set()
    for holding in holdings:
        holding_key = ticker_identity(holding.get("ticker") or holding.get("symbol"))
        match_index = next(
            (
                index
                for index, result in enumerate(results)
                if holding_key and holding_key in result_ticker_identities(result)
            ),
            None,
        )
        if match_index is None:
            matched.append((holding, None))
            continue
        used_result_indexes.add(match_index)
        result = results[match_index]
        resolved_ticker = result_ticker(result)
        if resolved_ticker:
            holding["resolved_ticker"] = resolved_ticker
        matched.append((holding, result))
    portfolio_results = [result for _holding, result in matched if result is not None]
    critical_issues = [
        issue
        for row in portfolio_results
        for issue in list((row.get("self_review") or {}).get("issues") or [])
        if isinstance(issue, dict) and issue.get("severity") == "critical"
    ]
    warning_issues = [
        issue
        for row in portfolio_results
        for issue in list((row.get("self_review") or {}).get("issues") or [])
        if isinstance(issue, dict) and issue.get("severity") == "warning"
    ]
    successful_results = [
        result
        for _holding, result in matched
        if result is not None and result.get("report_allowed") is True
    ]
    failed_results = []
    for holding, result in matched:
        if result is None:
            failed_results.append({
                "ticker": str(holding.get("ticker") or holding.get("symbol") or ""),
                "weight": holding.get("weight"),
                "note": str(holding.get("note") or ""),
                "reason": "analysis_result_missing",
            })
        elif result.get("report_allowed") is not True:
            failed_results.append(_uzi_aggregate_failed_result(result, holding=holding))
    # Pinned UZI ``run_portfolio`` emits a report whenever at least one
    # constituent bundle is available and records all other rows as failed.
    allowed = len(successful_results) >= 1
    loaded_warning_count = sum(
        int((row.get("self_review") or {}).get("warning_count") or 0)
        for row in successful_results
    )
    portfolio_name = str(query_content.get("portfolio_name") or "UZI Portfolio").strip() or "UZI Portfolio"
    conclusion = (
        f"{portfolio_name} 的 UZI 原生组合批量研究已完成；成功 {len(successful_results)}/{len(holdings) or len(results)}。"
        if allowed
        else f"{portfolio_name} 没有可用成分；UZI --portfolio 至少需要 1 个成功结果。"
    )
    report_content = {
        "conclusion": conclusion,
        "assessment": {
            "status": "completed" if allowed else "insufficient_data",
            "review_scope": "combined_delivery_gate",
            "summary": conclusion,
            "factors": [
                {
                    "symbol": row.get("symbol"),
                    "overall_score": (row.get("synthesis") or {}).get("overall_score"),
                    "verdict": (row.get("synthesis") or {}).get("verdict_label"),
                    "report_allowed": row.get("report_allowed") is True,
                    "review_layers": _uzi_review_layers(row),
                }
                for row in portfolio_results
            ],
            "loaded": len(successful_results),
            "requested": len(holdings) or len(results),
            "failed": failed_results,
            "ignored_analysis_symbols": [
                result_ticker(result)
                for index, result in enumerate(results)
                if index not in used_result_indexes
            ],
        },
        "evidence": [{
            "artifact_id": analysis.artifact_id,
            "artifact_type": "uzi_native_analysis",
            "source_refs": list(analysis.source_refs),
        }],
        "risk": [str(row.get("issue") or "") for row in [*critical_issues, *warning_issues]],
        "follow_up": [str(row.get("suggested_fix") or "") for row in critical_issues],
        "delivery_type": "uzi_portfolio_3_9_4",
        "quality_status": (
            "degraded"
            if allowed
            and any(row.get("quality_status") == "degraded" for row in successful_results)
            else "available"
            if allowed
            else "data_gap"
        ),
        "native_analysis_artifact_id": analysis.artifact_id,
        "report_allowed": allowed,
        "critical_count": 0,
        "warning_count": loaded_warning_count,
        "portfolio_name": portfolio_name,
        "portfolio_holdings": holdings,
        "research_profile": content.get("profile"),
        "school": content.get("school"),
    }
    return ArtifactDraft(
        artifact_type="research_report",
        content=report_content,
        source_refs=_unique_source_refs([*list(query.source_refs), *list(analysis.source_refs)]),
        parent_artifact_ids=[query.artifact_id, analysis.artifact_id],
        confidence=analysis.confidence if allowed else 0.0,
        risk_flags=list(analysis.risk_flags),
        metadata={
            "quality_status": report_content["quality_status"],
            "methodology_version": content.get("methodology_version"),
            "source_commit": content.get("source_commit"),
            "report_allowed": allowed,
        },
    )


def _uzi_aggregate_failed_result(
    result: dict[str, Any],
    *,
    holding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the auditable failure row used by UZI aggregate runners."""

    raw = result.get("raw") if isinstance(result.get("raw"), dict) else {}
    review = result.get("self_review") if isinstance(result.get("self_review"), dict) else {}
    issues = [row for row in list(review.get("issues") or []) if isinstance(row, dict)]
    first_issue = next(
        (
            str(row.get("issue") or row.get("message") or "").strip()
            for row in issues
            if str(row.get("issue") or row.get("message") or "").strip()
        ),
        "",
    )
    failed = {
        "ticker": str(
            (holding or {}).get("ticker")
            or (holding or {}).get("symbol")
            or raw.get("ticker")
            or result.get("symbol")
            or ""
        ),
        "critical_count": int(review.get("critical_count") or 0),
        "warning_count": int(review.get("warning_count") or 0),
        "reason": first_issue or str(result.get("computation_error") or "analysis_unavailable"),
        "review_layers": _uzi_review_layers(result),
    }
    if holding is not None:
        failed["weight"] = holding.get("weight")
        failed["note"] = str(holding.get("note") or "")
    return failed


def _evaluate_uzi_native_method(context, method_id: str) -> ArtifactDraft:
    inputs = dict(context.node.inputs or {})
    if method_id in {"returns_attribution", "holding_rebalance", "portfolio_rebalance"}:
        query = context.require_artifact("normalized_query")
        native_inputs = {**dict(query.content or {}), **inputs}
        content = execute_native_method(method_id, [], [], native_inputs)
        source_refs = list(query.source_refs)
        parent_ids = [query.artifact_id]
    else:
        rows, symbols, _query, source_refs, parent_ids = _snapshot_context(context)
        content = execute_native_method(method_id, rows, symbols, inputs)
    quality = str(content.get("quality_status") or "data_gap")
    return ArtifactDraft(
        artifact_type="uzi_native_method_result",
        content=content,
        source_refs=source_refs,
        parent_artifact_ids=parent_ids,
        confidence=0.82 if quality == "available" else 0.5 if quality == "degraded" else 0.0,
        risk_flags=list(content.get("data_gap") or []),
        metadata={
            "quality_status": quality,
            "methodology_version": "3.9.4-source-parity",
            "source_commit": content.get("source_commit"),
            "native_method_id": method_id,
        },
    )


def execute_uzi_native_dcf(context):
    return _evaluate_uzi_native_method(context, "dcf")


def execute_uzi_native_comps(context):
    return _evaluate_uzi_native_method(context, "comps")


def execute_uzi_native_three_statement(context):
    return _evaluate_uzi_native_method(context, "three_statement")


def execute_uzi_native_quick_lbo(context):
    return _evaluate_uzi_native_method(context, "quick_lbo")


def execute_uzi_native_accretion_dilution(context):
    return _evaluate_uzi_native_method(context, "accretion_dilution")


def execute_uzi_native_initiation_report(context):
    return _evaluate_uzi_native_method(context, "initiation_report")


def execute_uzi_native_earnings_review(context):
    return _evaluate_uzi_native_method(context, "earnings_review")


def execute_uzi_native_catalyst_calendar(context):
    return _evaluate_uzi_native_method(context, "catalyst_calendar")


def execute_uzi_native_thesis_tracker(context):
    return _evaluate_uzi_native_method(context, "thesis_tracker")


def execute_uzi_native_morning_note(context):
    return _evaluate_uzi_native_method(context, "morning_note")


def execute_uzi_native_quantitative_screen(context):
    return _evaluate_uzi_native_method(context, "quantitative_screen")


def execute_uzi_native_sector_review(context):
    return _evaluate_uzi_native_method(context, "sector_review")


def execute_uzi_native_ic_memo(context):
    return _evaluate_uzi_native_method(context, "ic_memo")


def execute_uzi_native_porter_bcg(context):
    return _evaluate_uzi_native_method(context, "porter_bcg")


def execute_uzi_native_dd_checklist(context):
    return _evaluate_uzi_native_method(context, "dd_checklist")


def execute_uzi_native_unit_economics(context):
    return _evaluate_uzi_native_method(context, "unit_economics")


def execute_uzi_native_value_creation_plan(context):
    return _evaluate_uzi_native_method(context, "value_creation_plan")


def execute_uzi_native_portfolio_rebalance(context):
    return _evaluate_uzi_native_method(context, "portfolio_rebalance")


def execute_uzi_native_ai_readiness(context):
    return _evaluate_uzi_native_method(context, "ai_readiness")


def execute_uzi_native_earnings_preview(context):
    return _evaluate_uzi_native_method(context, "earnings_preview")


def execute_uzi_native_model_update(context):
    return _evaluate_uzi_native_method(context, "model_update")


def execute_uzi_native_returns_attribution(context):
    return _evaluate_uzi_native_method(context, "returns_attribution")


def execute_uzi_native_holding_rebalance(context):
    return _evaluate_uzi_native_method(context, "holding_rebalance")


def _uzi_native_command_artifact(
    *,
    content: dict,
    source_refs: list[dict],
    parent_ids: list[str],
) -> ArtifactDraft:
    quality = str(content.get("quality_status") or "data_gap")
    return ArtifactDraft(
        artifact_type="uzi_native_command_result",
        content=content,
        source_refs=source_refs,
        parent_artifact_ids=parent_ids,
        confidence=0.82 if quality == "available" else 0.5 if quality == "degraded" else 0.0,
        risk_flags=list(content.get("data_gap") or []),
        metadata={
            "quality_status": quality,
            "methodology_version": "3.9.4-source-parity",
            "source_commit": content.get("source_commit"),
            "native_command_id": content.get("command_id"),
            "phase": content.get("phase"),
        },
    )


def execute_uzi_command_panel_only(context):
    rows, symbols, _query, source_refs, parent_ids = _snapshot_context(context)
    return _uzi_native_command_artifact(
        content=execute_panel_only(rows, symbols),
        source_refs=source_refs,
        parent_ids=parent_ids,
    )


def execute_uzi_command_scan_trap(context):
    rows, symbols, query, source_refs, parent_ids = _snapshot_context(context)
    return _uzi_native_command_artifact(
        content=execute_scan_trap(rows, symbols, query=query),
        source_refs=source_refs,
        parent_ids=parent_ids,
    )


def execute_uzi_command_segmental_model(context):
    rows, symbols, _query, source_refs, parent_ids = _snapshot_context(context)
    return _uzi_native_command_artifact(
        content=execute_segmental_model(rows, symbols, dict(context.node.inputs or {})),
        source_refs=source_refs,
        parent_ids=parent_ids,
    )


def evaluate_research_safety_gate(context):
    reasons: list[str] = []
    if not context.artifact.source_refs:
        reasons.append("research report has no source references")
    if "critical_data_gap" in context.artifact.risk_flags:
        reasons.append("critical financial input is missing")
    return {
        "verdict": "fail" if reasons else "pass",
        "reason": "; ".join(reasons) if reasons else "research delivery passed source and safety checks",
        "evidence_refs": list(context.artifact.source_refs),
        "score": float(context.artifact.confidence or 0.0),
    }


def evaluate_uzi_native_report_gate(context):
    """Fail closed when a native UZI report still has any critical issue."""

    content = context.artifact.content if isinstance(context.artifact.content, dict) else {}
    if content.get("delivery_type") not in {
        "uzi_native_3_9_4",
        "uzi_versus_3_9_4",
        "uzi_portfolio_3_9_4",
    }:
        return {
            "verdict": "pass",
            "reason": "not a native UZI analyze-stock delivery",
            "evidence_refs": list(context.artifact.source_refs),
            "score": float(context.artifact.confidence or 0.0),
        }
    critical = int(content.get("critical_count") or 0)
    allowed = content.get("report_allowed") is True
    if not allowed or critical:
        return {
            "verdict": "fail",
            "reason": f"UZI native 关键质量门禁已阻止报告导出（critical_count={critical}）",
            "evidence_refs": list(context.artifact.source_refs),
            "score": 0.0,
        }
    return {
        "verdict": "pass",
        "reason": "UZI native self-review passed with critical_count=0",
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


def _artifact_confidence(value: object) -> float:
    numeric = _number(value)
    return numeric if numeric is not None else 0.2


def _required_investment_cycle(inputs: dict) -> str:
    value = str(inputs.get("investment_cycle") or "").strip()
    if value not in {"short_term", "mid_term", "long_term"}:
        raise ValueError("investment_cycle is required; choose short_term, mid_term or long_term")
    return value


def _number(value: object) -> float | None:
    return coerce_numeric(value)


def _integer_or_default(value: object, default: int) -> int:
    """Apply a default without mistaking the valid integer zero for absence."""
    if value is None or value == "":
        return default
    return int(value)


_UZI_COMMAND_COLLECTION_PROFILES = {
    "quick_scan": "lite",
    "uzi_panel_only": "lite",
    "uzi_initiate": "deep",
    "uzi_ic_memo": "deep",
}


def _collection_research_profile(inputs: dict) -> str | None:
    explicit = str(inputs.get("research_profile") or "").strip().lower()
    if explicit:
        return explicit
    return _UZI_COMMAND_COLLECTION_PROFILES.get(str(inputs.get("intent") or "").strip())


def _requested_snapshot_types(*, inputs: dict, sector: str | None) -> list[str]:
    intent = str(inputs.get("intent") or "").strip()
    if intent in {
        "stock_check",
        "uzi_analyze_stock",
        "stock_compare",
        "uzi_portfolio_batch",
    }:
        # UZI 3.9.4's root v3 path always collects the complete registry.
        # ``snapshot_types`` on these intents is a presentation hint, not a
        # license to starve the native scoring/report stages of dimensions.
        return _unique_strings(select_profile(inputs).get("snapshot_types") or [])
    if intent == "quick_scan":
        # UZI /quick-scan is a fixed four-dimension command, not a profile.
        return ["fundamental", "kline", "valuation", "trap"]
    if intent in {"uzi_initiate", "uzi_ic_memo", "uzi_earnings_preview"}:
        # These commands explicitly require full raw/22-dimensional evidence
        # in the pinned command definitions.
        profile = _UZI_COMMAND_COLLECTION_PROFILES.get(intent, "medium")
        return _unique_strings(
            select_profile({"research_profile": profile}).get("snapshot_types") or []
        )
    native_command_snapshot_types: dict[str, tuple[str, ...]] = {
        "uzi_panel_only": ("market", "fundamental"),
        "uzi_scan_trap": ("trap",),
        "uzi_segmental_model": (
            "market", "fundamental", "chain", "events", "research", "moat", "policy",
        ),
        "uzi_dcf": ("market", "fundamental"),
        "uzi_comps": ("market", "fundamental", "valuation", "similar_stocks"),
        "uzi_lbo": ("fundamental",),
        "uzi_earnings": ("fundamental", "research"),
        "uzi_catalysts": ("events",),
        "uzi_thesis": ("market", "fundamental", "kline", "valuation"),
        "uzi_screen": (),
        "uzi_dd": (
            "market", "fundamental", "industry", "peers", "chain", "governance",
            "moat", "policy", "sentiment", "events", "trap",
        ),
        "uzi_ai_readiness": (
            "market", "fundamental", "chain", "industry", "governance",
            "policy", "moat", "events", "sentiment", "trap",
        ),
        "uzi_model_update": ("market", "fundamental", "valuation", "similar_stocks"),
        "uzi_three_statement_method": ("market", "fundamental", "valuation"),
        "uzi_accretion_dilution_method": ("market", "fundamental"),
        "uzi_morning_note_method": (
            "market", "valuation", "kline", "capital_flow", "lhb", "sentiment",
        ),
        "uzi_sector_review_method": ("market", "industry", "peers", "chain"),
        "uzi_porter_bcg_method": ("market", "industry", "moat"),
        "uzi_unit_economics_method": ("market", "fundamental"),
        "uzi_value_creation_plan_method": ("market", "fundamental", "industry"),
    }
    if intent == "uzi_screen":
        all_assumptions = inputs.get("method_assumptions")
        screen_assumptions = (
            all_assumptions.get("quantitative_screen")
            if isinstance(all_assumptions, dict)
            and isinstance(all_assumptions.get("quantitative_screen"), dict)
            else {}
        )
        style = str(screen_assumptions.get("style") or "quality")
        return list(
            {
                "value": ("market", "fundamental", "valuation"),
                "growth": ("fundamental",),
                "quality": ("fundamental", "moat"),
                "gulp": ("market", "fundamental", "kline", "valuation"),
                "short": ("market", "fundamental", "valuation"),
            }.get(style, ("fundamental", "moat"))
        )
    if intent in native_command_snapshot_types:
        return list(native_command_snapshot_types[intent])
    requested_methods = _unique_strings(inputs.get("requested_methods") or [])
    method_snapshot_types: dict[str, tuple[str, ...]] = {
        "ai_readiness": (
            "market", "fundamental", "chain", "industry", "governance",
            "policy", "moat", "events", "sentiment", "trap",
        ),
        "earnings_preview": ("market", "fundamental", "research", "events", "kline"),
        "model_update": ("market", "fundamental", "valuation", "peers"),
        "three_statement": ("market", "fundamental", "valuation"),
        "quick_lbo": ("fundamental",),
        "lbo": ("fundamental",),
        "accretion_dilution": ("market", "fundamental"),
        "ma_accretion": ("fundamental",),
        "segmental": ("fundamental",),
        "initiation_report": ("market", "fundamental", "chain", "industry", "events", "valuation"),
        "earnings_review": ("fundamental", "research"),
        "catalyst_calendar": ("events",),
        "thesis_tracker": ("market", "fundamental", "kline", "valuation"),
        "morning_note": ("market", "valuation", "kline", "capital_flow", "lhb", "sentiment"),
        "quantitative_screen": ("market", "fundamental", "kline", "valuation", "moat"),
        "sector_review": ("market", "industry", "peers", "chain"),
        "ic_memo": ("market", "fundamental", "chain", "industry", "events", "valuation", "governance", "trap"),
        "porter_bcg": ("market", "industry", "moat"),
        "dd_checklist": ("market", "fundamental", "industry", "peers", "chain", "governance", "moat", "policy", "sentiment", "events", "trap"),
        "unit_economics": ("fundamental",),
        "value_creation_plan": ("market", "fundamental", "industry"),
    }
    all_assumptions = inputs.get("method_assumptions")
    screen_assumptions = (
        all_assumptions.get("quantitative_screen")
        if isinstance(all_assumptions, dict)
        and isinstance(all_assumptions.get("quantitative_screen"), dict)
        else {}
    )
    screen_style = str(screen_assumptions.get("style") or "quality")
    screen_types = {
        "value": ("market", "fundamental", "valuation"),
        "growth": ("fundamental",),
        "quality": ("fundamental", "moat"),
        "gulp": ("market", "fundamental", "kline", "valuation"),
        "short": ("market", "fundamental", "valuation"),
    }.get(screen_style, ("fundamental", "moat"))
    method_required = _unique_strings(
        [
            snapshot_type
            for method in requested_methods
            for snapshot_type in (
                screen_types
                if method == "quantitative_screen"
                else method_snapshot_types.get(method, ())
            )
        ]
    )
    explicit = inputs.get("snapshot_types")
    if isinstance(explicit, list) and explicit:
        return _unique_strings([*explicit, *method_required])
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
    intent: str,
    sector: str | None,
    research_profile: str | None,
    candidate_pool_size: int,
    enrichment_top_n: int,
    skip_enrichment: bool,
    no_resume: bool,
    cancellation,
) -> dict[str, Any]:
    bundle = collect_snapshot_bundle(
        query=query,
        symbols=symbols,
        snapshot_types=snapshot_types,
        intent=intent,
        sector=sector,
        candidate_pool_size=candidate_pool_size,
        enrichment_top_n=enrichment_top_n,
        skip_enrichment=skip_enrichment,
        no_resume=no_resume,
        cancellation=cancellation,
        research_profile=research_profile,
    )
    return dict(bundle)


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
    if bundle is not None:
        parent_ids = list(dict.fromkeys([*parent_ids, bundle.artifact_id]))
        source_refs = _unique_source_refs([*source_refs, *list(bundle.source_refs)])
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
    top_k = max(_integer_or_default(inputs.get("top_k"), 5), 1)
    watchlist_top_k = max(_integer_or_default(inputs.get("watchlist_top_k"), 2), 0)
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
    if bundle is not None:
        parent_ids = list(dict.fromkeys([*parent_ids, bundle.artifact_id]))
        source_refs = _unique_source_refs([*source_refs, *list(bundle.source_refs)])
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
        candidates.extend(
            snapshot.get("symbol")
            for snapshot in content.get("snapshots") or content.get("items") or []
            if isinstance(snapshot, dict)
        )
        for key in ("screened_candidates", "watchlist_candidates"):
            candidates.extend(
                candidate.get("symbol") or candidate.get("code")
                for candidate in content.get(key) or []
                if isinstance(candidate, dict)
            )
        screening = content.get("screening") if isinstance(content.get("screening"), dict) else {}
        for key in ("screened_candidates", "watchlist_candidates"):
            candidates.extend(
                candidate.get("symbol") or candidate.get("code")
                for candidate in screening.get(key) or []
                if isinstance(candidate, dict)
            )
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
    seen: set[str] = set()
    for source_ref in source_refs:
        if not isinstance(source_ref, dict):
            continue
        source_type = str(source_ref.get("type") or "")
        has_identity = any(source_ref.get(key) for key in ("id", "uri", "locator"))
        # One proof/URL can support several uses and findings. Only identical
        # references are duplicates; never discard differing relation metadata.
        key = json.dumps(source_ref, sort_keys=True, ensure_ascii=False, default=str)
        if source_type and has_identity and key not in seen:
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
