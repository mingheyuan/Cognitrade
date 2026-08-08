"""Complete in-memory UZI 3.9.2 analysis on ArtifactBoard evidence.

The module executes the pinned upstream scoring, panel, synthesis, institutional
methods and mechanical self-review functions.  It does not read or write an
UZI cache and it never invokes an external UZI installation.  Agent OS supplies
the only model provider; every generated statement is grounded against the
frozen snapshot payload before it is admitted to ``agent_analysis``.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import importlib
import math
from pathlib import Path
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
    dim_commentary: dict[str, GroundedText]
    panel_insights: str = Field(min_length=30, max_length=2_000)
    panel_evidence_paths: list[str] = Field(min_length=1, max_length=10)
    great_divide_override: AgentGreatDivide
    narrative_override: AgentNarrative
    data_gap_acknowledged: dict[str, str]

    @model_validator(mode="after")
    def require_all_dimensions(self) -> "AgentAnalysisPayload":
        actual = set(self.dim_commentary)
        expected = set(_DIMENSION_KEYS)
        if actual != expected:
            raise ValueError(
                f"dim_commentary must cover all 20 dimensions; "
                f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
            )
        return self


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
        "methodology_version": "3.9.2-source-parity",
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
    integrity = data_integrity.validate(raw)
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
        panel = score_fns.generate_panel(dimensions, raw)
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
                agent_issues = [{
                    "severity": "error",
                    "field": "agent_analysis",
                    "message": f"Agent 分析生成失败：{exc.__class__.__name__}",
                    "suggestion": "按自审问题修正结构化 Agent 分析后重算",
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
    llm_provider: Any | None,
    recovery_tasks: list[dict[str, Any]],
    review_feedback: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if llm_provider is None or not bool(getattr(llm_provider, "available", True)):
        return None
    frozen = {
        "raw": _compact(raw),
        "dimension_scores": _compact(dimensions),
        "panel": _compact(panel),
        "recovery_tasks": _compact(recovery_tasks),
    }
    allowed_paths = set(_leaf_paths(frozen))
    numeric_paths = {
        path
        for path in allowed_paths
        if _path_number(frozen, path) is not None
    }
    system_prompt = (
        "你是 UZI 3.9.2 完整研究链的 Agent 分析阶段。只允许使用 FROZEN_ARTIFACTBOARD_EVIDENCE；"
        "禁止模拟、补造或把缺失值改成中位值。20 个维度必须逐项评论，存在数据时引用精确 evidence_paths，"
        "不存在时明确说明缺口并引用对应 recovery/data_gap 路径。买入区间只能由 numeric_basis_fields 中真实数值"
        "通过 calculation 明示计算得到；若证据不足，结构化输出应失败并让报告停在质量门。"
        f"UZI_SOURCE_COMMIT={UZI_SOURCE_COMMIT}\n"
        f"FROZEN_ARTIFACTBOARD_EVIDENCE={frozen}\n"
        f"ALLOWED_EVIDENCE_PATHS={sorted(allowed_paths)}\n"
        f"NUMERIC_BASIS_FIELDS={sorted(numeric_paths)}"
    )
    user_prompt = (
        "生成完整 agent_analysis。dim_commentary 的 key 必须且只能是 0_basic 到 19_contests 的 20 个精确键；"
        "每条至少 20 字。panel_insights 至少 30 字。多空各三轮。至少三条风险。"
        "buy_zones 必须含 value/growth/technical/youzi，basis_fields 只能取 NUMERIC_BASIS_FIELDS；"
        "calculation 写清真实数值如何导出 price。data_gap_acknowledged 只记录已经穷尽的真实恢复尝试，不能写已解决。"
        f"上一轮机械自审 critical 与 suggested_fix={review_feedback}。必须逐条修复；不得通过改写措辞隐藏问题。"
    )
    parsed = llm_provider.parse(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        schema=AgentAnalysisPayload,
        temperature=0.1,
    )
    payload = parsed if isinstance(parsed, AgentAnalysisPayload) else AgentAnalysisPayload.model_validate(parsed)
    _validate_grounding(payload, allowed_paths=allowed_paths, numeric_paths=numeric_paths)
    return _to_upstream_agent_analysis(payload)


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
    return {
        "agent_reviewed": True,
        "dim_commentary": {
            key: row.text
            for key, row in payload.dim_commentary.items()
        },
        "panel_insights": payload.panel_insights,
        "great_divide_override": {
            "punchline": payload.great_divide_override.punchline,
            "bull_say_rounds": [row.text for row in payload.great_divide_override.bull_say_rounds],
            "bear_say_rounds": [row.text for row in payload.great_divide_override.bear_say_rounds],
        },
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
    repairable_dims = {"agent_analysis", "panel", "qualitative_deep_dive"}
    repairable_categories = {"self-check", "consistency", "agent-os-boundary"}
    return any(
        str(row.get("dim") or "") in repairable_dims
        or str(row.get("category") or "") in repairable_categories
        for row in critical_rows
    )


def run_native_self_review(
    *,
    symbol: str,
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
            issues.extend(row.to_dict() for row in (check(ctx) or []))
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
                "schema_error",
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
    coverage = float((raw.get("_integrity") or {}).get("coverage_pct") or 0.0)
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
