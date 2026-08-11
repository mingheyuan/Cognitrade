from __future__ import annotations

from typing import Any

from domain_packs.finance_ashare.tools.snapshot_collector import (
    collect_snapshot_bundle,
    normalize_json_value,
)


def _integer_or_default(value: object, default: int) -> int:
    if value is None or value == "":
        return default
    return int(value)


def invoke(arguments: dict[str, Any], context) -> dict[str, Any]:
    """Execute the Finance Pack data provider through the shared Tool Registry."""
    progress_emitter = getattr(context, "progress_emitter", None)

    def emit_progress(progress: dict[str, Any]) -> None:
        if not callable(progress_emitter):
            return
        completed = int(progress.get("completed") or 0)
        total = max(int(progress.get("total") or 0), 1)
        snapshot_type = str(progress.get("snapshot_type") or "").strip()
        phase = str(progress.get("phase") or "collecting")
        label = _SNAPSHOT_LABELS.get(snapshot_type, snapshot_type or "真实数据")
        if phase == "completed":
            current_action = f"已完成 {completed}/{total} 类真实数据：{label}"
        elif phase == "cache_hit":
            current_action = f"已复用 {completed}/{total} 类缓存：{label}"
        else:
            current_action = f"正在采集 {completed + 1}/{total} 类真实数据：{label}"
        progress_emitter({
            "node_id": str(getattr(context, "actor", "")).removeprefix("subagent:") or None,
            "phase": "snapshot_collection",
            "status": "running",
            "progress_source": "runtime",
            "current_action": current_action,
            "next_action": "继续采集其余真实来源并冻结到 ArtifactBoard",
            "progress_percent": min(99, round(completed / total * 100)),
            "stage_index": completed,
            "stage_total": total,
            "snapshot_type": snapshot_type or None,
        })

    payload = normalize_json_value(
        collect_snapshot_bundle(
            query=str(arguments.get("query") or ""),
            symbols=list(arguments.get("symbols") or []),
            snapshot_types=list(arguments.get("snapshot_types") or []),
            intent=str(arguments.get("intent") or "stock_check"),
            sector=str(arguments.get("sector") or "").strip() or None,
            candidate_pool_size=_integer_or_default(arguments.get("candidate_pool_size"), 8),
            enrichment_top_n=_integer_or_default(arguments.get("enrichment_top_n"), 2),
            skip_enrichment=bool(arguments.get("skip_enrichment")),
            no_resume=bool(arguments.get("no_resume")),
            cancellation=context.cancellation,
            cache_root=getattr(context, "cache_root", None),
            research_profile=str(arguments.get("research_profile") or "").strip() or None,
            progress_callback=emit_progress,
        )
    )
    if not isinstance(payload, dict):
        raise TypeError("financial data provider payload must normalize to an object")
    source_refs: list[dict[str, Any]] = []
    for row in list(payload.get("snapshots") or []):
        if not isinstance(row, dict):
            continue
        source = row.get("source") if isinstance(row.get("source"), dict) else {}
        provider = str(
            source.get("provider_id")
            or source.get("source_id")
            or row.get("snapshot_type")
            or "financial_data_provider"
        )
        identifier = ":".join(
            item
            for item in (
                str(row.get("snapshot_type") or "snapshot"),
                str(row.get("symbol") or row.get("sector") or "unscoped"),
            )
            if item
        )
        source_refs.append(
            {
                "type": "financial_data_provider",
                "provider_id": provider,
                "id": identifier,
            }
        )
    payload["source_refs"] = source_refs or [
        {"type": "financial_data_provider", "id": "empty_result"}
    ]
    return payload


_SNAPSHOT_LABELS = {
    "market": "基础行情",
    "fundamental": "财务基本面",
    "kline": "历史 K 线",
    "macro": "宏观环境",
    "peers": "同行对比",
    "chain": "产业链",
    "research": "机构研报",
    "industry": "行业数据",
    "materials": "原材料",
    "futures": "期货映射",
    "valuation": "估值数据",
    "governance": "公司治理",
    "capital_flow": "资金流",
    "policy": "政策信息",
    "moat": "竞争壁垒",
    "events": "公司事件",
    "lhb": "龙虎榜",
    "sentiment": "市场情绪",
    "trap": "风险排查",
    "contests": "竞赛与榜单",
}


__all__ = ["invoke"]
