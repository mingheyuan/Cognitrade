"""Read-only repair instructions at the report → data-center boundary.

This module creates no jobs. A consumer must explicitly submit collection,
confirm publication, then request an input revision before report execution.
"""
from datetime import date
from typing import Any

from domain_packs.finance_ashare.subagents.uzi_recovery import native_recovery
from research_system.market_data.historical import capability_for_dimension
from research_system.market_data.instruments import SecurityIdentityResolver
from research_system.market_data.research_requests import ResearchDocumentRequest


def report_repair_plan(content: dict[str, Any], *, symbol: str, as_of: date,
                       today: date) -> dict[str, Any]:
    recovery = native_recovery(content)
    pending = {}
    for result in content.get("results") or []:
        for row in (result.get("qualitative_execution") or {}).get("pending_evidence") or []:
            try:
                request = ResearchDocumentRequest(dimension=row.get("dimension"), url=row.get("url"))
            except (ValueError, AttributeError):
                continue
            pending[(request.dimension, request.url)] = request.model_dump()
    document_requests = list(pending.values())[:12]
    pending_dimensions = {row["dimension"] for row in document_requests}
    dimensions = sorted(set(recovery["dimensions"]) | pending_dimensions)
    research_gaps = {
        str(gap).split(":", 1)[0] for result in content.get("results") or []
        for gap in (result.get("qualitative_execution") or {}).get("input_gaps") or []
        if str(gap).endswith(":frozen_citations_unavailable")
    }
    rows, collectable = [], []
    for dimension in dimensions:
        capability = capability_for_dimension(dimension)
        can_collect = as_of == today or capability.can_reconstruct or dimension in pending_dimensions
        markers = [
            (result.get("raw") or {}).get("dimensions", {}).get(dimension, {})
            for result in content.get("results") or []
        ]
        missing_original = any((row.get("_market_data_observation") or {}).get("reason_code")
                               == "native_empty_input_not_retained" for row in markers)
        rows.append({
            "dimension": dimension,
            "action": "collect_data" if can_collect else "historical_archive_required",
            "retryable": can_collect,
            "historical_strategy": capability.strategy,
            "reason": "new_source_requires_verification" if dimension in pending_dimensions else
                      "native_empty_input_not_retained" if missing_original else
                      "frozen_citations_unavailable" if dimension in research_gaps else "native_data_gap",
            "limitation": None if can_collect else "现有接口没有此维度的历史恢复能力；需要当时的可信归档，不能用当前数据填入历史日期，也不能保证原生自审允许降级交付。",
        })
        if can_collect:
            collectable.append(dimension)
    request = None
    if collectable:
        request = {
            "symbols": [SecurityIdentityResolver().resolve_stock(symbol).symbol], "asset_family": "equity",
            "start_date": as_of.isoformat(), "end_date": as_of.isoformat(),
            "dimensions": collectable, "mode": "missing_or_stale" if document_requests else "force_all",
            "max_concurrency": 5, "publish_when_complete": False,
            "include_research_archive": bool(set(collectable) & research_gaps or document_requests),
        }
        if document_requests:
            request["research_document_requests"] = document_requests
    return {
        "symbol": symbol, "as_of_date": as_of.isoformat(), "recovery": recovery,
        "dimensions": rows, "collection_request": request,
        "pending_evidence": document_requests,
        "collection_requires_confirmation": True,
        "report_retry": {"symbols": [symbol], "refresh_inputs": bool(collectable), "confirmed": False},
        "fund_holdings_recollection": False,
        "notes": ["研究允许补充资料；旧输入版本不可变，不代表研究范围不可扩展。",
                  "新 URL 只是待核实线索，不保证原文可用或历史可见，不能直接当报告证据。",
                  "采集结束后先确认新 Bundle，再显式修订报告输入；本接口不启动任何采集或模型调用。",
                  "可重试仅表示有数据源路径，不保证返回非空或通过原生自审。"],
    }
