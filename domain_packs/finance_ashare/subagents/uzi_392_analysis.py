"""Complete in-memory UZI 3.9.4 analysis on ArtifactBoard evidence.

The module executes the pinned upstream scoring, panel, synthesis, institutional
methods and mechanical self-review functions.  It does not read or write an
UZI cache and it never invokes an external UZI installation.  Agent OS supplies
the only model provider; every generated statement is grounded against the
frozen snapshot payload before it is admitted to ``agent_analysis``.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import importlib
import json
import math
from pathlib import Path
import re
from threading import Event
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from domain_packs.finance_ashare.subagents.uzi_392_native_methods import build_uzi_raw
from domain_packs.finance_ashare.subagents.uzi_agent_roleplay import (
    build_native_panel_overrides,
)
from domain_packs.finance_ashare.subagents.uzi_qualitative_deep_dive import (
    apply_qualitative_deep_dive,
)
from domain_packs.finance_ashare.subagents.uzi_llm_support import parse_uzi_structured
from domain_packs.finance_ashare.subagents.uzi_panel_registry import panel_for_profile
from domain_packs.finance_ashare.vendor.uzi_3_9_2.runtime import (
    UZI_SOURCE_COMMIT,
    Uzi392FetcherRuntime,
)


_DIMENSION_KEYS = tuple(f"{index}_{name}" for index, name in enumerate((
    "basic",
    "financials",
    "kline",
    "macro",
    "peers",
    "chain",
    "research",
    "industry",
    "materials",
    "futures",
    "valuation",
    "governance",
    "capital_flow",
    "policy",
    "moat",
    "events",
    "lhb",
    "sentiment",
    "trap",
    "contests",
)))
_QUALITATIVE_KEYS = frozenset({
    "3_macro", "7_industry", "8_materials", "9_futures", "13_policy", "15_events",
})

_PROFILE_REASONING_EFFORT = {
    "lite": "low",
    "medium": "high",
    "deep": "xhigh",
}


def _profile_dimension_keys(profile: str) -> tuple[str, ...]:
    """Return the exact upstream fetcher contract for one explicit profile.

    Agent OS runs different research profiles concurrently, so the native
    boundary must never infer this value from process-wide ``UZI_DEPTH``.
    """

    _initialize_native_imports()
    native_profile = importlib.import_module("lib.analysis_profile").get_profile(profile)
    enabled = set(native_profile.fetchers_enabled)
    return tuple(key for key in _DIMENSION_KEYS if key in enabled)


class GroundedText(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=20, max_length=2_000)
    evidence_paths: list[str] = Field(min_length=1, max_length=8)


class GroundedRound(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=10, max_length=1_000)
    evidence_paths: list[str] = Field(min_length=1, max_length=6)


class AgentGreatDivide(BaseModel):
    model_config = ConfigDict(extra="forbid")

    punchline: str = Field(min_length=10, max_length=500)
    bull_say_rounds: list[GroundedRound] = Field(min_length=3, max_length=3)
    bear_say_rounds: list[GroundedRound] = Field(min_length=3, max_length=3)


class GroundedRisk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=10, max_length=800)
    evidence_paths: list[str] = Field(min_length=1, max_length=6)


class AgentBuyZone(BaseModel):
    model_config = ConfigDict(extra="forbid")

    price: float = Field(gt=0)
    rationale: str = Field(min_length=8, max_length=800)
    basis_fields: list[str] = Field(min_length=1, max_length=6)
    calculation: str = Field(min_length=8, max_length=500)


class AgentBuyZones(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: AgentBuyZone
    growth: AgentBuyZone
    technical: AgentBuyZone
    youzi: AgentBuyZone


class AgentNarrative(BaseModel):
    model_config = ConfigDict(extra="forbid")

    core_conclusion: str = Field(min_length=20, max_length=2_000)
    conclusion_evidence_paths: list[str] = Field(min_length=1, max_length=10)
    risks: list[GroundedRisk] = Field(min_length=3, max_length=12)
    buy_zones: AgentBuyZones


class AgentAnalysisPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_reviewed: Literal[True]
    dim_commentary: dict[str, GroundedText] = Field(min_length=1)
    panel_insights: str = Field(min_length=30, max_length=2_000)
    panel_evidence_paths: list[str] = Field(min_length=1, max_length=10)
    great_divide_override: AgentGreatDivide | None = None
    narrative_override: AgentNarrative
    data_gap_acknowledged: dict[str, str]

    @model_validator(mode="after")
    def reject_unknown_dimensions(self) -> "AgentAnalysisPayload":
        actual = set(self.dim_commentary)
        unknown = actual - set(_DIMENSION_KEYS)
        if unknown:
            raise ValueError(
                f"dim_commentary contains unknown dimensions: {sorted(unknown)}"
            )
        return self


def _profile_native_panel(panel: dict[str, Any], profile: str) -> dict[str, Any]:
    """Project the upstream rule-engine panel to UZI's profile seat contract.

    The pinned generator evaluates the complete registry in one pure call.
    UZI's profile contract then selects 10/51/66 seats.  Recompute every
    aggregate from the selected seats so no full-panel statistic leaks into a
    Lite or Medium report.
    """

    allowed_ids = {
        str(row.get("id") or "")
        for row in panel_for_profile(profile)
        if str(row.get("id") or "")
    }
    investors = [
        dict(row)
        for row in list(panel.get("investors") or [])
        if isinstance(row, dict) and str(row.get("investor_id") or "") in allowed_ids
    ]
    if len(investors) != len(allowed_ids):
        present = {str(row.get("investor_id") or "") for row in investors}
        raise ValueError(
            f"native panel is missing profile seats: {sorted(allowed_ids - present)}"
        )
    if len(investors) == len(list(panel.get("investors") or [])):
        return dict(panel)

    vote_keys = {
        "强烈买入": "strongly_buy",
        "买入": "buy",
        "关注": "watch",
        "观望": "wait",
        "回避": "avoid",
        "不适合": "skip",
    }
    vote_distribution = {
        key: 0
        for key in ("strongly_buy", "buy", "watch", "wait", "avoid", "n_a", "skip")
    }
    signal_distribution = {
        key: 0 for key in ("bullish", "neutral", "bearish", "skip")
    }
    for investor in investors:
        vote_key = vote_keys.get(str(investor.get("verdict") or ""), "n_a")
        vote_distribution[vote_key] += 1
        signal = str(investor.get("signal") or "skip")
        signal_distribution[signal if signal in signal_distribution else "skip"] += 1

    neutral_weight = 0.6
    score_weight = 0.65
    vote_weight = 0.35
    polarize_k = 1.3

    def polarize(value: float) -> float:
        return max(0.0, min(100.0, 50.0 + (value - 50.0) * polarize_k))

    active = [row for row in investors if row.get("signal") != "skip"]
    bullish = signal_distribution["bullish"]
    neutral = signal_distribution["neutral"]
    bearish = signal_distribution["bearish"]
    score_mean = (
        sum(float(row.get("score") or 0.0) for row in active) / len(active)
        if active
        else 50.0
    )
    vote_weighted = (
        (bullish + neutral_weight * neutral) / max(len(active), 1) * 100.0
    )
    consensus_raw = score_weight * score_mean + vote_weight * vote_weighted
    consensus = polarize(consensus_raw)

    existing_schools = dict(panel.get("school_scores") or {})

    def school_verdict(value: float) -> str:
        if value >= 80:
            return "重仓"
        if value >= 65:
            return "买入"
        if value >= 50:
            return "关注"
        if value >= 35:
            return "谨慎"
        return "回避"

    schools: dict[str, dict[str, Any]] = {}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for investor in investors:
        grouped.setdefault(str(investor.get("group") or "?"), []).append(investor)
    for group in sorted(grouped):
        members = grouped[group]
        active_members = [row for row in members if row.get("signal") != "skip"]
        counts = Counter(str(row.get("signal") or "skip") for row in members)
        if active_members:
            group_score_mean = sum(
                float(row.get("score") or 0.0) for row in active_members
            ) / len(active_members)
            group_vote = (
                counts["bullish"] + neutral_weight * counts["neutral"]
            ) / len(active_members) * 100.0
            group_consensus = polarize(
                score_weight * group_score_mean + vote_weight * group_vote
            )
        else:
            group_score_mean = 0.0
            group_vote = 0.0
            group_consensus = 0.0
        dominant_signal = (
            max(
                ("bullish", "neutral", "bearish"),
                key=lambda signal: counts[signal],
            )
            if active_members
            else "skip"
        )
        metadata = dict(existing_schools.get(group) or {})
        schools[group] = {
            "group": group,
            "label": str(metadata.get("label") or group),
            "desc": str(metadata.get("desc") or ""),
            "n_members": len(members),
            "n_active": len(active_members),
            "consensus": round(group_consensus, 1),
            "avg_score": round(group_score_mean, 1),
            "vote_consensus": round(group_vote, 1),
            "score_mean": round(group_score_mean, 1),
            "verdict": school_verdict(group_consensus) if active_members else "不适合",
            "bullish": counts["bullish"],
            "neutral": counts["neutral"],
            "bearish": counts["bearish"],
            "skip": counts["skip"],
            "dominant_signal": dominant_signal,
        }

    return {
        "ticker": panel.get("ticker"),
        "panel_consensus": round(consensus, 1),
        "vote_distribution": vote_distribution,
        "signal_distribution": signal_distribution,
        "investors": investors,
        "school_scores": schools,
        "consensus_formula": {
            "version": "v2.15.5 · polarize(0.65*score_mean + 0.35*vote_weighted, k=1.3)",
            "score_weight": score_weight,
            "vote_weight": vote_weight,
            "neutral_weight": neutral_weight,
            "polarize_k": polarize_k,
            "score_mean": round(score_mean, 2),
            "vote_weighted": round(vote_weighted, 2),
            "consensus_raw": round(consensus_raw, 2),
            "consensus_final": round(consensus, 2),
            "bullish": bullish,
            "neutral_weighted": round(neutral * neutral_weight, 2),
            "bearish": bearish,
            "skip": signal_distribution["skip"],
            "active": len(active),
        },
    }


def execute_native_analysis(
    snapshot_rows: list[dict[str, Any]],
    symbols: list[str],
    *,
    profile: str,
    llm_provider: Any | None,
    cancellation: Event | None = None,
    recovery_history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run the complete pinned pipeline for each requested symbol."""

    selected_profile = str(profile or "").strip().lower()
    if selected_profile not in {"lite", "medium", "deep"}:
        raise ValueError("research_profile is required; choose lite, medium or deep")
    _initialize_native_imports()
    market_router = importlib.import_module("lib.market_router")
    unique_symbols: list[str] = []
    seen_symbols: set[str] = set()
    for symbol in symbols:
        normalized = str(symbol or "").strip()
        if not normalized:
            continue
        try:
            canonical = market_router.parse_ticker(normalized).full
        except (TypeError, ValueError):
            canonical = normalized
        if canonical in seen_symbols:
            continue
        seen_symbols.add(canonical)
        unique_symbols.append(normalized)
    results = [
        _execute_symbol(
            snapshot_rows,
            symbol,
            profile=selected_profile,
            llm_provider=llm_provider,
            cancellation=cancellation,
            recovery_history=list(recovery_history or []),
        )
        for symbol in unique_symbols
    ]
    critical = sum(int((row.get("self_review") or {}).get("critical_count") or 0) for row in results)
    warnings = sum(int((row.get("self_review") or {}).get("warning_count") or 0) for row in results)
    combined_recovery_history = [
        dict(row)
        for result in results
        for row in list(result.get("recovery_history") or [])
        if isinstance(row, dict)
    ]
    return {
        "source_commit": UZI_SOURCE_COMMIT,
        "methodology_version": "3.9.4-source-parity",
        "profile": selected_profile,
        "results": results,
        "critical_count": critical,
        "warning_count": warnings,
        "report_allowed": bool(results) and critical == 0 and all(row.get("report_allowed") is True for row in results),
        "quality_status": "available" if results and critical == 0 else "quality_gate_blocked",
        "recovery_history": combined_recovery_history,
    }


def _execute_symbol(
    snapshot_rows: list[dict[str, Any]],
    symbol: str,
    *,
    profile: str,
    llm_provider: Any | None,
    cancellation: Event | None,
    recovery_history: list[dict[str, Any]],
) -> dict[str, Any]:
    _initialize_native_imports()
    raw = build_uzi_raw(snapshot_rows, symbol)
    raw["fetched_at"] = datetime.now(timezone.utc).isoformat()
    data_integrity = importlib.import_module("lib.data_integrity")
    enabled_dimension_keys = set(_profile_dimension_keys(profile))
    integrity = _profile_integrity_report(
        raw,
        data_integrity.validate(raw),
        enabled_dimension_keys=enabled_dimension_keys,
    )
    raw["_integrity"] = integrity
    recovery_tasks = data_integrity.generate_recovery_tasks(raw, integrity)

    computation_error: str | None = None
    try:
        features = importlib.import_module("lib.stock_features").extract_features(
            raw,
            raw.get("dimensions", {}),
        )
        deep_methods = importlib.import_module("compute_deep_methods")
        dim20 = deep_methods.compute_dim_20(features, raw)
        raw["dimensions"]["20_valuation_models"] = dim20
        dim21 = deep_methods.compute_dim_21(features, raw, dim20.get("data"))
        raw["dimensions"]["21_research_workflow"] = dim21
        dim22 = deep_methods.compute_dim_22(features, raw, dim20.get("data"), dim21.get("data"))
        # Upstream currently inserts a fabricated ¥10,000 sample position.
        # Keep every other exact output but reject that demo value at the
        # ArtifactBoard boundary; portfolio analysis needs explicit positions.
        dim22_data = dict(dim22.get("data") or {})
        dim22_data["portfolio_rebalance"] = {
            "quality_status": "data_gap",
            "data_gap": ["explicit_portfolio_positions_required"],
            "result": None,
        }
        dim22["data"] = dim22_data
        raw["dimensions"]["22_deep_methods"] = dim22
        score_fns = importlib.import_module("lib.pipeline.score_fns")
        dimensions = score_fns.score_dimensions(raw)
        panel = _profile_native_panel(
            score_fns.generate_panel(dimensions, raw),
            profile,
        )
    except Exception as exc:
        computation_error = f"{exc.__class__.__name__}:{str(exc)[:500]}"
        features = {}
        dimensions = {}
        panel = {}

    agent_analysis: dict[str, Any] | None = None
    agent_issues: list[dict[str, Any]] = []
    roleplay = {"required": profile == "deep", "status": "not_requested", "agent_reviewed": False}
    qualitative_execution = {"required": profile in {"medium", "deep"}, "status": "not_requested", "agent_reviewed": False}
    synthesis: dict[str, Any] = {}
    review: dict[str, Any] = {}
    review_feedback: list[dict[str, Any]] = []
    seen_critical_signatures: set[tuple[tuple[str, str, str], ...]] = set()
    analysis_cycle = 0
    while True:
        analysis_cycle += 1
        agent_analysis = None
        agent_issues = []
        roleplay = {"required": profile == "deep", "status": "not_requested", "agent_reviewed": False}
        qualitative_execution = {"required": profile in {"medium", "deep"}, "status": "not_requested", "agent_reviewed": False}
        if not computation_error:
            try:
                narrative = _generate_agent_analysis(
                    raw,
                    dimensions,
                    panel,
                    profile=profile,
                    llm_provider=llm_provider,
                    recovery_tasks=recovery_tasks,
                    review_feedback=review_feedback,
                )
                if narrative is not None:
                    agent_analysis = narrative
                    if profile == "deep":
                        try:
                            roleplay_result = build_native_panel_overrides(
                                panel,
                                features=features,
                                llm_provider=llm_provider,
                                cancellation=cancellation,
                            )
                            roleplay = dict(roleplay_result.get("roleplay") or {})
                            agent_analysis["per_investor_override"] = dict(
                                roleplay_result.get("per_investor_override") or {}
                            )
                        except Exception as exc:
                            roleplay = {
                                "required": True,
                                "status": "failed",
                                "agent_reviewed": False,
                                "error": f"{exc.__class__.__name__}:{str(exc)[:300]}",
                            }
                    if profile in {"medium", "deep"}:
                        try:
                            qualitative = apply_qualitative_deep_dive(
                                {},
                                snapshot_rows=snapshot_rows,
                                llm_provider=llm_provider,
                                cancellation=cancellation,
                            )
                            qualitative_execution = dict(qualitative.get("qualitative_agent_execution") or {})
                            agent_analysis["qualitative_deep_dive"] = dict(
                                qualitative.get("qualitative_deep_dive") or {}
                            )
                            _apply_qualitative_commentary(agent_analysis)
                        except Exception as exc:
                            qualitative_execution = {
                                "required": True,
                                "status": "failed",
                                "agent_reviewed": False,
                                "error": f"{exc.__class__.__name__}:{str(exc)[:300]}",
                            }
                    agent_issues = _validate_agent_analysis(agent_analysis)
            except Exception as exc:
                failure_kind = _agent_failure_kind(exc)
                agent_issues = [{
                    "severity": "error",
                    "field": "agent_analysis",
                    "message": (
                        f"Agent 分析生成失败：{exc.__class__.__name__}: "
                        f"{_safe_error_detail(exc)}"
                    ),
                    "suggestion": "按自审问题修正结构化 Agent 分析后重算",
                    "failure_kind": failure_kind,
                }]

        if computation_error:
            synthesis = {}
        else:
            synthesis = importlib.import_module("lib.pipeline.score_fns").generate_synthesis(
                raw,
                dimensions,
                panel,
                agent_analysis=agent_analysis,
            )
            synthesis["data_gaps"] = _data_gap_document(
                recovery_tasks,
                agent_analysis,
                integrity,
            )

        review = run_native_self_review(
            symbol=symbol,
            profile=profile,
            raw=raw,
            dimensions=dimensions,
            panel=panel,
            synthesis=synthesis,
            agent_analysis=agent_analysis,
            agent_validation_issues=agent_issues,
            roleplay=roleplay,
            qualitative_execution=qualitative_execution,
            computation_error=computation_error,
        )
        critical_rows = [
            row for row in list(review.get("issues") or [])
            if isinstance(row, dict) and row.get("severity") == "critical"
        ]
        recovery_history.append({
            "stage": "agent_recompute_self_review",
            "symbol": symbol,
            "cycle": analysis_cycle,
            "critical_count": len(critical_rows),
            "status": "passed" if not critical_rows else "quality_gate_blocked",
            "suggested_fixes": [str(row.get("suggested_fix") or "") for row in critical_rows],
        })
        if not critical_rows:
            break
        signature = tuple(sorted(
            (
                str(row.get("category") or ""),
                str(row.get("dim") or ""),
                str(row.get("suggested_fix") or ""),
            )
            for row in critical_rows
        ))
        if signature in seen_critical_signatures:
            recovery_history[-1]["exhausted_reason"] = "same_critical_signature_after_recompute"
            break
        seen_critical_signatures.add(signature)
        if not _agent_repairable(critical_rows, llm_provider):
            recovery_history[-1]["exhausted_reason"] = "no_remaining_agent_recovery_action"
            break
        review_feedback = critical_rows

    _merge_self_review_warnings(synthesis, review)
    return {
        "symbol": symbol,
        "raw": raw,
        "dimensions": dimensions,
        "panel": panel,
        "agent_analysis": agent_analysis,
        "synthesis": synthesis,
        "integrity": integrity,
        "recovery_tasks": recovery_tasks,
        "recovery_history": recovery_history,
        "roleplay": roleplay,
        "qualitative_execution": qualitative_execution,
        "agent_validation_issues": agent_issues,
        "self_review": review,
        "report_allowed": review["critical_count"] == 0,
        "quality_status": "available" if review["critical_count"] == 0 else "quality_gate_blocked",
    }


def _generate_agent_analysis(
    raw: dict[str, Any],
    dimensions: dict[str, Any],
    panel: dict[str, Any],
    *,
    profile: str,
    llm_provider: Any | None,
    recovery_tasks: list[dict[str, Any]],
    review_feedback: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if llm_provider is None or not bool(getattr(llm_provider, "available", True)):
        return None
    expected_dimensions = _profile_dimension_keys(profile)
    native_profile = importlib.import_module("lib.analysis_profile").get_profile(profile)
    frozen = {
        "raw": _compact(raw),
        "dimension_scores": _compact(dimensions),
        "panel": _compact_panel_for_agent(panel),
        "recovery_tasks": _compact(recovery_tasks),
    }
    allowed_paths = set(_leaf_paths(frozen))
    numeric_paths = {
        path
        for path in allowed_paths
        if _path_number(frozen, path) is not None
    }
    frozen_json = json.dumps(
        frozen,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    schema_json = json.dumps(
        AgentAnalysisPayload.model_json_schema(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    system_prompt = (
        "你是 UZI 3.9.4 完整研究链的 Agent 分析阶段。只允许使用 FROZEN_ARTIFACTBOARD_EVIDENCE；"
        f"当前显式研究档位为 {profile}。禁止模拟、补造或把缺失值改成中位值。"
        f"本档启用的 {len(expected_dimensions)} 个维度必须逐项评论，存在数据时引用精确 evidence_paths，"
        "不存在时明确说明缺口并引用对应 recovery/data_gap 路径。evidence_paths 使用冻结 JSON 的点号/数组下标"
        "叶子路径，例如 raw.dimensions.0_basic.data.current_price；返回后会由机械校验逐条核对。买入区间只能由"
        "冻结 JSON 中真实的数值叶子路径"
        "通过 calculation 明示计算得到；若证据不足，结构化输出应失败并让报告停在质量门。"
        f"UZI_SOURCE_COMMIT={UZI_SOURCE_COMMIT}\n"
        f"FROZEN_ARTIFACTBOARD_EVIDENCE={frozen_json}\n"
        f"REQUIRED_OUTPUT_JSON_SCHEMA={schema_json}"
    )
    expected_dimension_text = ",".join(expected_dimensions)
    debate_instruction = (
        "great_divide_override 必须包含多空各三轮。"
        if native_profile.enable_bull_bear_debate
        else "当前档位不启用结构化多空辩论，不要返回 great_divide_override。"
    )
    user_prompt = (
        "生成当前档位完整 agent_analysis。dim_commentary 的 key 必须且只能是："
        f"{expected_dimension_text}；每条至少 20 字。panel_insights 至少 30 字。"
        f"{debate_instruction}至少三条风险。"
        "buy_zones 必须含 value/growth/technical/youzi，basis_fields 只能填写冻结 JSON 中真实数值的精确叶子路径；"
        "calculation 写清真实数值如何导出 price。data_gap_acknowledged 只记录已经穷尽的真实恢复尝试，不能写已解决。"
        "严格遵守 REQUIRED_OUTPUT_JSON_SCHEMA，只返回一个 JSON 对象。"
        f"上一轮机械自审 critical 与 suggested_fix="
        f"{json.dumps(review_feedback, ensure_ascii=False, separators=(',', ':'))}。"
        "必须逐条修复；不得通过改写措辞隐藏问题。"
    )
    parsed = parse_uzi_structured(
        llm_provider,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        schema=AgentAnalysisPayload,
        temperature=0.1,
        reasoning_effort=_PROFILE_REASONING_EFFORT[profile],
    )
    payload = parsed if isinstance(parsed, AgentAnalysisPayload) else AgentAnalysisPayload.model_validate(parsed)
    _validate_profile_payload(
        payload,
        expected_dimensions=expected_dimensions,
        debate_required=bool(native_profile.enable_bull_bear_debate),
    )
    _normalize_payload_paths(payload, allowed_paths=allowed_paths)
    _validate_grounding(payload, allowed_paths=allowed_paths, numeric_paths=numeric_paths)
    return _to_upstream_agent_analysis(payload)


def _validate_profile_payload(
    payload: AgentAnalysisPayload,
    *,
    expected_dimensions: tuple[str, ...],
    debate_required: bool,
) -> None:
    actual = set(payload.dim_commentary)
    expected = set(expected_dimensions)
    if actual != expected:
        raise ValueError(
            "dim_commentary does not match the selected UZI profile; "
            f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        )
    if debate_required and payload.great_divide_override is None:
        raise ValueError("great_divide_override is required for the deep UZI profile")
    if not debate_required and payload.great_divide_override is not None:
        raise ValueError("great_divide_override is disabled for this UZI profile")


def _normalize_payload_paths(
    payload: AgentAnalysisPayload,
    *,
    allowed_paths: set[str] | None = None,
) -> None:
    """Canonicalize paths and expand real JSON containers to leaf evidence.

    Structured models occasionally cite a real list/dict container such as
    ``roe_history`` instead of one of its concrete children.  UZI's evidence
    contract remains leaf-only, so mechanically replace only those ancestors
    that have real descendants in the frozen ArtifactBoard payload.  Unknown
    paths are preserved and still fail the grounding gate.
    """

    def normalize(paths: list[str]) -> list[str]:
        normalized: list[str] = []
        for raw_path in paths:
            path = _canonical_evidence_path(raw_path)
            if allowed_paths is None or path in allowed_paths:
                candidates = [path]
            else:
                object_prefix = f"{path}."
                array_prefix = f"{path}["
                candidates = sorted(
                    candidate
                    for candidate in allowed_paths
                    if candidate.startswith(object_prefix)
                    or candidate.startswith(array_prefix)
                )
                if not candidates:
                    candidates = [path]
            for candidate in candidates:
                if candidate not in normalized:
                    normalized.append(candidate)
        return normalized[:12]

    for row in payload.dim_commentary.values():
        row.evidence_paths = normalize(row.evidence_paths)
    payload.panel_evidence_paths = normalize(payload.panel_evidence_paths)
    payload.narrative_override.conclusion_evidence_paths = normalize(
        payload.narrative_override.conclusion_evidence_paths
    )
    if payload.great_divide_override is not None:
        for row in payload.great_divide_override.bull_say_rounds:
            row.evidence_paths = normalize(row.evidence_paths)
        for row in payload.great_divide_override.bear_say_rounds:
            row.evidence_paths = normalize(row.evidence_paths)
    for row in payload.narrative_override.risks:
        row.evidence_paths = normalize(row.evidence_paths)
    for name in ("value", "growth", "technical", "youzi"):
        zone = getattr(payload.narrative_override.buy_zones, name)
        zone.basis_fields = normalize(zone.basis_fields)


def _canonical_evidence_path(path: str) -> str:
    return re.sub(r"\.(\d+)(?=\.|$)", r"[\1]", str(path).strip())


def _safe_error_detail(exc: Exception) -> str:
    detail = str(exc).strip() or "no_detail"
    detail = re.sub(
        r"(?i)(api[_-]?key|authorization|bearer|token|cookie)(\s*[:=]\s*)[^\s,;]+",
        r"\1\2<redacted>",
        detail,
    )
    return detail[:600]


def _agent_failure_kind(exc: Exception) -> str:
    detail = f"{exc.__class__.__name__}:{exc}".lower()
    if "llm_request_timeout" in detail or "timed out" in detail or "timeout" in detail:
        return "llm_transport_timeout"
    if any(
        marker in detail
        for marker in (
            "llm_request_failed",
            "upstream_error",
            "connectionerror",
            "connection aborted",
            "remote end closed",
            "service unavailable",
            "bad gateway",
            "error code: 502",
            "error code: 503",
            "error code: 504",
        )
    ):
        return "llm_provider_unavailable"
    return "agent_content_invalid"


def _validate_grounding(
    payload: AgentAnalysisPayload,
    *,
    allowed_paths: set[str],
    numeric_paths: set[str],
) -> None:
    paths: list[str] = []
    for row in payload.dim_commentary.values():
        paths.extend(row.evidence_paths)
    paths.extend(payload.panel_evidence_paths)
    paths.extend(payload.narrative_override.conclusion_evidence_paths)
    if payload.great_divide_override is not None:
        for row in payload.great_divide_override.bull_say_rounds:
            paths.extend(row.evidence_paths)
        for row in payload.great_divide_override.bear_say_rounds:
            paths.extend(row.evidence_paths)
    for row in payload.narrative_override.risks:
        paths.extend(row.evidence_paths)
    unknown = sorted(set(paths) - allowed_paths)
    if unknown:
        raise ValueError(f"agent_analysis cited unknown evidence paths: {unknown[:20]}")
    for name in ("value", "growth", "technical", "youzi"):
        zone = getattr(payload.narrative_override.buy_zones, name)
        unknown_numeric = sorted(set(zone.basis_fields) - numeric_paths)
        if unknown_numeric:
            raise ValueError(f"buy_zones.{name} cited non-numeric or unknown basis fields: {unknown_numeric}")
        if not math.isfinite(zone.price):
            raise ValueError(f"buy_zones.{name}.price must be finite")


def _to_upstream_agent_analysis(payload: AgentAnalysisPayload) -> dict[str, Any]:
    result = {
        "agent_reviewed": True,
        "dim_commentary": {
            key: row.text
            for key, row in payload.dim_commentary.items()
        },
        "panel_insights": payload.panel_insights,
        "narrative_override": {
            "core_conclusion": payload.narrative_override.core_conclusion,
            "risks": [row.text for row in payload.narrative_override.risks],
            "buy_zones": {
                name: {
                    "price": getattr(payload.narrative_override.buy_zones, name).price,
                    "rationale": getattr(payload.narrative_override.buy_zones, name).rationale,
                }
                for name in ("value", "growth", "technical", "youzi")
            },
        },
        "data_gap_acknowledged": dict(payload.data_gap_acknowledged),
        "grounding": {
            "dim_commentary": {
                key: list(row.evidence_paths)
                for key, row in payload.dim_commentary.items()
            },
            "panel_insights": list(payload.panel_evidence_paths),
            "buy_zones": {
                name: {
                    "basis_fields": list(getattr(payload.narrative_override.buy_zones, name).basis_fields),
                    "calculation": getattr(payload.narrative_override.buy_zones, name).calculation,
                }
                for name in ("value", "growth", "technical", "youzi")
            },
        },
    }
    if payload.great_divide_override is not None:
        result["great_divide_override"] = {
            "punchline": payload.great_divide_override.punchline,
            "bull_say_rounds": [
                row.text for row in payload.great_divide_override.bull_say_rounds
            ],
            "bear_say_rounds": [
                row.text for row in payload.great_divide_override.bear_say_rounds
            ],
        }
    return result


def _apply_qualitative_commentary(agent_analysis: dict[str, Any]) -> None:
    qualitative = dict(agent_analysis.get("qualitative_deep_dive") or {})
    commentary = dict(agent_analysis.get("dim_commentary") or {})
    for key in _QUALITATIVE_KEYS:
        row = qualitative.get(key)
        if not isinstance(row, dict) or not row.get("evidence"):
            continue
        urls = [str(item.get("url") or "") for item in row.get("evidence") or [] if isinstance(item, dict)]
        commentary[key] = f"{str(row.get('conclusion') or '').strip()} 证据：{'；'.join(urls)}"
    agent_analysis["dim_commentary"] = commentary


def _validate_agent_analysis(agent_analysis: dict[str, Any]) -> list[dict[str, Any]]:
    issues = importlib.import_module("lib.agent_analysis_validator").validate(agent_analysis)
    return [
        {
            "severity": str(issue.severity),
            "field": str(issue.field),
            "message": str(issue.message),
            "suggestion": str(issue.suggestion),
        }
        for issue in issues
    ]


def _agent_repairable(
    critical_rows: list[dict[str, Any]],
    llm_provider: Any | None,
) -> bool:
    """Return true only when another Agent pass can materially address an issue."""

    if llm_provider is None or not bool(getattr(llm_provider, "available", True)):
        return False
    terminal_provider_failure = any(
        str(row.get("evidence") or "")
        in {"llm_transport_timeout", "llm_provider_unavailable"}
        for row in critical_rows
        if str(row.get("dim") or "") == "agent_analysis"
    )
    for row in critical_rows:
        dim = str(row.get("dim") or "")
        category = str(row.get("category") or "")
        evidence = str(row.get("evidence") or "")
        issue = str(row.get("issue") or "")
        if dim == "qualitative_deep_dive":
            # A model retry cannot create URLs that were absent from the
            # frozen ArtifactBoard.  UZI requires returning to real-source
            # recovery for these states, not regenerating the same narrative.
            if any(
                marker in evidence
                for marker in (
                    "evidence_preflight_failed",
                    "requires_two_cited_sources",
                    "required_but_unavailable",
                )
            ):
                continue
            return True
        if dim == "agent_analysis" and terminal_provider_failure:
            continue
        if dim in {"agent_analysis", "panel"}:
            return True
        if category == "consistency" and "数据" not in issue:
            return True
    return False


def _issue_dimension_number(issue: dict[str, Any]) -> int | None:
    prefix = str(issue.get("dim") or "").split("_", 1)[0]
    try:
        return int(prefix)
    except ValueError:
        return None


def _profile_integrity_coverage(
    raw: dict[str, Any],
    *,
    enabled_dimension_keys: set[str],
) -> tuple[float, list[dict[str, Any]]]:
    """Run UZI's exact integrity predicates against the selected profile."""

    data_integrity = importlib.import_module("lib.data_integrity")
    raw_dimensions = dict(raw.get("dimensions") or {})
    total = 0
    passed = 0
    missing: list[dict[str, Any]] = []
    for dimension_key, path, label, critical in data_integrity.CRITICAL_CHECKS:
        if dimension_key not in enabled_dimension_keys:
            continue
        total += 1
        dimension = raw_dimensions.get(dimension_key) or {}
        data = dimension.get("data") or {}
        value = data_integrity._get(data, path)
        if data_integrity._is_missing(value):
            missing.append(
                {
                    "dim": dimension_key,
                    "field": path,
                    "label": label,
                    "critical": bool(critical),
                }
            )
        else:
            passed += 1
    coverage = round(passed / total * 100.0, 0) if total else 100.0
    return coverage, [row for row in missing if row["critical"]]


def _profile_integrity_report(
    raw: dict[str, Any],
    report: dict[str, Any],
    *,
    enabled_dimension_keys: set[str],
) -> dict[str, Any]:
    """Scope UZI's integrity/recovery worklist to fetchers the user selected."""

    scoped = deepcopy(report)
    for key in ("missing_critical", "missing_optional", "missing_enrichment", "fallback_dims"):
        scoped[key] = [
            dict(row)
            for row in list(report.get(key) or [])
            if isinstance(row, dict) and str(row.get("dim") or "") in enabled_dimension_keys
        ]
    data_integrity = importlib.import_module("lib.data_integrity")
    total_checks = sum(
        1
        for dimension_key, _path, _label, _critical in data_integrity.CRITICAL_CHECKS
        if dimension_key in enabled_dimension_keys
    )
    missing_checks = len(scoped["missing_critical"]) + len(scoped["missing_optional"])
    passed_checks = max(total_checks - missing_checks, 0)
    scoped["total_checks"] = total_checks
    scoped["passed_checks"] = passed_checks
    scoped["coverage_pct"] = (
        round(passed_checks / total_checks * 100.0, 0) if total_checks else 100.0
    )
    scoped["critical_missing"] = bool(scoped["missing_critical"])
    scoped["ok"] = (
        not scoped["critical_missing"] and len(scoped["missing_enrichment"]) < 7
    )
    scoped["profile_enabled_dimensions"] = sorted(enabled_dimension_keys)
    return scoped


def _profile_coverage_issues(
    raw: dict[str, Any],
    *,
    enabled_dimension_keys: set[str],
) -> list[dict[str, Any]]:
    coverage, missing_critical = _profile_integrity_coverage(
        raw,
        enabled_dimension_keys=enabled_dimension_keys,
    )
    if coverage >= 60:
        return []
    severity = "critical" if coverage < 40 else "warning"
    return [
        {
            "severity": severity,
            "category": "data",
            "dim": "overall",
            "issue": f"数据完整性仅 {coverage:.0f}%（< 60% 不该出报告）",
            "evidence": (
                f"coverage_pct={coverage}, missing_critical={missing_critical[:3]}"
            ),
            "suggested_fix": "Agent OS 按当前档位恢复 missing_critical 维度后重新自审",
        }
    ]


def _profile_empty_dimension_issues(
    raw: dict[str, Any],
    *,
    enabled_dimension_keys: set[str],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    dimensions = dict(raw.get("dimensions") or {})
    for key in sorted(enabled_dimension_keys):
        dimension = dimensions.get(key)
        if not isinstance(dimension, dict):
            continue
        data = dimension.get("data")
        if data not in (None, {}, []):
            continue
        is_timeout = bool(dimension.get("_timeout"))
        error = str(dimension.get("error") or "")
        issues.append(
            {
                "severity": "warning" if is_timeout or error else "critical",
                "category": "data",
                "dim": key,
                "issue": (
                    f"维度 {key} data 为空"
                    + (" (timeout)" if is_timeout else (" (crash)" if error else ""))
                ),
                "evidence": f"_timeout={is_timeout}, error={error[:60]}",
                "suggested_fix": "Agent OS 按当前档位恢复该真实数据源后重新自审",
            }
        )
    return issues


def run_native_self_review(
    *,
    symbol: str,
    profile: str,
    raw: dict[str, Any],
    dimensions: dict[str, Any],
    panel: dict[str, Any],
    synthesis: dict[str, Any],
    agent_analysis: dict[str, Any] | None,
    agent_validation_issues: list[dict[str, Any]],
    roleplay: dict[str, Any],
    qualitative_execution: dict[str, Any],
    computation_error: str | None,
) -> dict[str, Any]:
    """Run exact UZI checks plus non-bypassable Agent OS boundary checks."""

    review_module = importlib.import_module("lib.self_review")
    enabled_dimension_keys = set(_profile_dimension_keys(profile))
    enabled_dimension_numbers = {
        int(key.split("_", 1)[0]) for key in enabled_dimension_keys
    }
    ctx = {
        "ticker": symbol,
        "market": raw.get("market", "A"),
        "raw": raw,
        "syn": synthesis,
        "panel": panel,
        "ag": agent_analysis,
        "dims": raw.get("dimensions") or {},
    }
    issues: list[dict[str, Any]] = []
    for check in review_module.CHECKS:
        try:
            if check.__name__ == "check_coverage_threshold":
                issues.extend(
                    _profile_coverage_issues(
                        raw,
                        enabled_dimension_keys=enabled_dimension_keys,
                    )
                )
                continue
            if check.__name__ == "check_empty_dims":
                issues.extend(
                    _profile_empty_dimension_issues(
                        raw,
                        enabled_dimension_keys=enabled_dimension_keys,
                    )
                )
                continue
            rows = [row.to_dict() for row in (check(ctx) or [])]
            if check.__name__ == "check_all_dims_exist":
                rows = [
                    row
                    for row in rows
                    if _issue_dimension_number(row) in enabled_dimension_numbers
                ]
            if check.__name__ == "check_agent_analysis_exists" and len(enabled_dimension_keys) < 15:
                rows = [
                    row
                    for row in rows
                    if not str(row.get("issue") or "").startswith("agent 仅覆盖")
                ]
            issues.extend(rows)
        except Exception as exc:
            issues.append({
                "severity": "warning",
                "category": "self-check",
                "dim": "review-engine",
                "issue": f"check {check.__name__} 自身失败: {exc.__class__.__name__}: {str(exc)[:100]}",
                "evidence": "",
                "suggested_fix": "修复自审函数后重新运行",
            })

    def add_critical(dim: str, issue: str, evidence: str, fix: str) -> None:
        issues.append({
            "severity": "critical",
            "category": "agent-os-boundary",
            "dim": dim,
            "issue": issue,
            "evidence": evidence,
            "suggested_fix": fix,
        })

    if computation_error:
        add_critical("pipeline", "UZI 原生计算链失败", computation_error, "恢复缺失数据或修复计算异常后重算")
    if not agent_analysis or agent_analysis.get("agent_reviewed") is not True:
        add_critical("agent_analysis", "Agent 分析缺失或未确认", "agent_reviewed!=true", "完成 Agent 分析后重算与自审")
    for issue in agent_validation_issues:
        if issue.get("severity") == "error":
            add_critical(
                str(issue.get("field") or "agent_analysis"),
                str(issue.get("message") or "agent_analysis schema error"),
                str(issue.get("failure_kind") or "agent_content_invalid"),
                str(issue.get("suggestion") or "修正 Agent 分析结构后重算"),
            )
    if roleplay.get("required") and (
        roleplay.get("status") != "completed" or roleplay.get("agent_reviewed") is not True
    ):
        add_critical("panel", "Deep 评委角色扮演未完整完成", str(roleplay), "完成全部评委后重新合成")
    if qualitative_execution.get("required") and (
        qualitative_execution.get("status") != "completed"
        or qualitative_execution.get("agent_reviewed") is not True
    ):
        add_critical(
            "qualitative_deep_dive",
            "六维定性研究未满足 UZI 证据门",
            str(qualitative_execution),
            "补充每维至少两个真实 URL 并完成三组分析后重算",
        )
    # Environment variables such as CI/UZI_CLI_ONLY must never downgrade the
    # production report gate.  Reinstate the two upstream critical invariants.
    coverage, _missing_critical = _profile_integrity_coverage(
        raw,
        enabled_dimension_keys=enabled_dimension_keys,
    )
    if coverage < 40:
        add_critical("overall", "数据完整性低于 40%", f"coverage_pct={coverage}", "按 suggested_fix 穷尽真实来源后重算")

    deduped: list[dict[str, Any]] = []
    seen = set()
    for issue in issues:
        key = (
            issue.get("severity"), issue.get("dim"), issue.get("issue"), issue.get("evidence"),
        )
        if key not in seen:
            seen.add(key)
            deduped.append(issue)
    critical = sum(row.get("severity") == "critical" for row in deduped)
    warning = sum(row.get("severity") == "warning" for row in deduped)
    info = sum(row.get("severity") == "info" for row in deduped)
    return {
        "ticker": symbol,
        "market": raw.get("market", "A"),
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "critical_count": critical,
        "warning_count": warning,
        "info_count": info,
        "passed": critical == 0,
        "issues": deduped,
        "checks_run": [check.__name__ for check in review_module.CHECKS],
        "profile": profile,
        "enabled_dimensions": list(_profile_dimension_keys(profile)),
        "profile_coverage_pct": coverage,
        "gate_policy": "critical_blocks_html_pdf;warning_allowed;no_environment_bypass",
    }


def _data_gap_document(
    tasks: list[dict[str, Any]],
    agent_analysis: dict[str, Any] | None,
    integrity: dict[str, Any],
) -> dict[str, Any]:
    acks = dict((agent_analysis or {}).get("data_gap_acknowledged") or {})
    rendered = []
    for task in tasks:
        row = deepcopy(task)
        key = f"{row.get('dim')}.{row.get('field')}"
        if key in acks or row.get("dim") in acks:
            row["status"] = "acknowledged"
            row["agent_note"] = acks.get(key, acks.get(row.get("dim"), ""))
        rendered.append(row)
    return {
        "coverage_pct": integrity.get("coverage_pct", 0),
        "total_gaps": len(rendered),
        "unresolved": sum(row.get("status") == "pending" for row in rendered),
        "tasks": rendered,
    }


def _merge_self_review_warnings(
    synthesis: dict[str, Any],
    review: dict[str, Any],
) -> None:
    """Feed allowed warnings into UZI's own orange data-gap banner."""

    if not synthesis:
        return
    data_gaps = dict(synthesis.get("data_gaps") or {})
    tasks = [dict(row) for row in list(data_gaps.get("tasks") or []) if isinstance(row, dict)]
    for issue in list(review.get("issues") or []):
        if not isinstance(issue, dict) or issue.get("severity") != "warning":
            continue
        tasks.append({
            "dim": str(issue.get("dim") or "self_review"),
            "field": "self_review_warning",
            "label": str(issue.get("issue") or "UZI 自审警告"),
            "severity": "warning",
            "suggested_actions": [str(issue.get("suggested_fix") or "复核该警告")],
            "status": "warning",
        })
    data_gaps["tasks"] = tasks
    data_gaps["total_gaps"] = len(tasks)
    data_gaps["unresolved"] = sum(row.get("status") == "pending" for row in tasks)
    synthesis["data_gaps"] = data_gaps


def _compact(value: Any, *, depth: int = 0) -> Any:
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        return value[:1_200]
    if depth >= 5:
        return None
    if isinstance(value, dict):
        return {
            str(key): _compact(item, depth=depth + 1)
            for key, item in list(value.items())[:80]
            if key not in {"_uzi_raw_result"}
        }
    if isinstance(value, list):
        return [_compact(item, depth=depth + 1) for item in value[:30]]
    return str(value)[:500]


def _compact_panel_for_agent(panel: dict[str, Any]) -> dict[str, Any]:
    """Keep every UZI seat while removing duplicated rule prose.

    The generic list compactor intentionally caps arbitrary arrays, but UZI's
    panel is a completeness contract: truncating at 30 silently drops valid
    investors.  Preserve all seats and their auditable decision fields while
    reducing pass/fail rule objects to stable rule identifiers.
    """

    member_key = "investors" if isinstance(panel.get("investors"), list) else "votes"
    compact = {
        str(key): _compact(value)
        for key, value in panel.items()
        if key not in {"votes", "investors", "per_investor_override"}
    }
    votes = []
    for row in list(panel.get(member_key) or []):
        if not isinstance(row, dict):
            continue
        vote = {
            key: _compact(row.get(key))
            for key in (
                "investor_id", "name", "perspective_label", "school", "group",
                "signal", "confidence", "score", "verdict", "headline",
                "weight_pass", "weight_total", "pass_count", "fail_count",
                "skipped_rule_count", "skip_reason",
                "ideal_price", "period", "time_horizon", "position_sizing",
            )
            if key in row
        }
        for source_keys, target_key in (
            (("pass_rules", "pass"), "pass_rule_ids"),
            (("fail_rules", "fail"), "fail_rule_ids"),
        ):
            source_rows = next(
                (list(row.get(source_key) or []) for source_key in source_keys if row.get(source_key)),
                [],
            )
            vote[target_key] = [
                str(item.get("rule_id") or item.get("id") or item.get("name") or "")
                for item in source_rows
                if isinstance(item, dict)
                and str(item.get("rule_id") or item.get("id") or item.get("name") or "")
            ]
        votes.append(vote)
    compact[member_key] = votes
    overrides = panel.get("per_investor_override")
    if isinstance(overrides, dict):
        compact["per_investor_override"] = {
            str(key): _compact(value)
            for key, value in overrides.items()
        }
    compact["seat_count"] = len(votes)
    return compact


def _leaf_paths(value: Any, prefix: str = "") -> list[str]:
    if isinstance(value, dict):
        paths: list[str] = []
        for key, item in value.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            paths.extend(_leaf_paths(item, child))
        return paths or ([prefix] if prefix else [])
    if isinstance(value, list):
        paths = []
        for index, item in enumerate(value):
            child = f"{prefix}[{index}]"
            paths.extend(_leaf_paths(item, child))
        return paths or ([prefix] if prefix else [])
    return [prefix] if prefix else []


def _path_number(root: Any, path: str) -> float | None:
    current = root
    for token in path.replace("]", "").replace("[", ".").split("."):
        if token == "":
            continue
        if isinstance(current, list):
            try:
                current = current[int(token)]
            except (ValueError, IndexError):
                return None
        elif isinstance(current, dict):
            current = current.get(token)
        else:
            return None
    if isinstance(current, bool):
        return None
    try:
        number = float(current)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _initialize_native_imports() -> None:
    Uzi392FetcherRuntime(workspace_root=Path(__file__).resolve().parents[3] / "workspace")


__all__ = [
    "AgentAnalysisPayload",
    "execute_native_analysis",
    "run_native_self_review",
]
