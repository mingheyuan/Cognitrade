from __future__ import annotations

import ast
from pathlib import Path

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from research_system.agents._subagent_invoker import RuntimeBoundSubagentInvoker
from research_system.runtime.core.execution_policy import RuntimeDegradeReason
from research_system.runtime.providers.provider_registry import OpenAIProvider
from research_system.services.methodologies.planning.planning_context import (
    build_planning_context_view,
    combine_focus_text,
    derive_methodology_focus,
    has_cashflow_focus,
    has_momentum_focus,
    has_policy_focus,
    normalize_planning_context_hints,
)
from research_system.services.reporting.reporting_utils import strategy_follow_up
from research_system.schemas.review import ClaimVerdictBreakdown, PosteriorReviewSummary
from research_system.schemas.subagents import SubagentInvocation
from research_system.subagents.registry import SubagentRegistry
from research_system.subagents.reporting import (
    FallbackReportComposerSubagent,
    LLMErrorReportComposerSubagent,
    LLMReportComposerSubagent,
)


class ReportPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conclusion: str
    evidence_ids: list[str] = Field(default_factory=list)
    risk: str = Field(validation_alias=AliasChoices("risk", "risk_statement"))
    follow_up: str | None = Field(
        default=None,
        validation_alias=AliasChoices("follow_up", "follow_up_suggestion"),
    )
    degraded_note: str | None = None


REPORT_EXECUTION_AUTO = "auto"
REPORT_EXECUTION_FALLBACK = "fallback"


class ReporterService(RuntimeBoundSubagentInvoker):
    agent_name = "reporter"

    def __init__(self, llm_provider: OpenAIProvider, runs_root: Path | None = None) -> None:
        self.llm_provider = llm_provider
        self.runs_root = runs_root
        self.registry = SubagentRegistry(
            [
                FallbackReportComposerSubagent(
                    review_verdict_by_claim_type_payload=self._review_verdict_by_claim_type_payload,
                    screening_digest=self._screening_digest,
                    compare_targets_payload=self._compare_targets_payload,
                    compare_digest=self._compare_digest,
                ),
                LLMReportComposerSubagent(
                    llm_provider=self.llm_provider,
                    schema=ReportPayload,
                    normalize_review_conclusion=self._normalize_review_conclusion,
                ),
                LLMErrorReportComposerSubagent(),
            ]
        )

    def run(
        self,
        *,
        intent: str,
        query: str,
        evidences: list[dict],
        retrieval_summary: str | None = None,
        planning_context_hints: dict[str, str] | None = None,
        execution_mode: str = REPORT_EXECUTION_AUTO,
        runtime_degraded_note: str | None = None,
    ) -> dict:
        with self.local_run_scope(
            intent=intent,
            query=query,
            phase="report",
            title="reporter",
            objective="Compose the final report from selected evidence.",
        ):
            return self._run_impl(
                intent=intent,
                query=query,
                evidences=evidences,
                retrieval_summary=retrieval_summary,
                planning_context_hints=planning_context_hints,
                execution_mode=execution_mode,
                runtime_degraded_note=runtime_degraded_note,
            )

    def _run_impl(
        self,
        *,
        intent: str,
        query: str,
        evidences: list[dict],
        retrieval_summary: str | None = None,
        planning_context_hints: dict[str, str] | None = None,
        execution_mode: str = REPORT_EXECUTION_AUTO,
        runtime_degraded_note: str | None = None,
    ) -> dict:
        trace: list[dict] = []
        force_fallback = execution_mode == REPORT_EXECUTION_FALLBACK
        normalized_planning_hints = self._normalize_planning_context_hints(planning_context_hints)
        report_strategy = self._derive_report_strategy(
            query=query,
            retrieval_summary=retrieval_summary,
            planning_context_hints=normalized_planning_hints,
        )
        trace.append(
            self._build_trace_record(
                subagent_type="report_strategy_resolver",
                objective="Resolve report strategy from retrieval summary and planning context.",
                status="completed",
                task_name="report:strategy:report_strategy_resolver",
                agent_path="reporter/strategy/report_strategy_resolver",
                notes=self._report_strategy_trace_notes(report_strategy),
            )
        )
        review_summary_payload = None
        screening_scorecard_payload = self._screening_scorecard_payload(evidences) if intent == "stock_screen" else None
        screening_candidates_payload = self._screening_candidates_payload(evidences) if intent == "stock_screen" else []
        methodology_payloads = self._methodology_payloads(evidences) if intent in {"stock_screen", "stock_check", "stock_compare", "review_thesis"} else []
        if intent in {"review_thesis", "stock_screen"}:
            review_summary_payload = self._review_summary_payload(evidences)
        bank_compare_scorecard = self._bank_compare_scorecard_payload(methodology_payloads) if intent == "stock_compare" else None
        if intent == "stock_compare" and bank_compare_scorecard:
            payload = self._deterministic_stock_compare_report(
                evidences=evidences,
                methodology_payloads=methodology_payloads,
                report_strategy=report_strategy,
            )
            trace.append(
                self._build_trace_record(
                    subagent_type="bank_compare_report_composer",
                    objective="Compose deterministic bank compare report from same-basis scorecard evidence.",
                    status="completed",
                    task_name="report:deterministic:bank_compare_report_composer",
                    agent_path="reporter/deterministic/bank_compare_report_composer",
                    notes=[
                        f"candidate_count={int(bank_compare_scorecard.get('candidate_count') or 0)}",
                        f"ranked_candidates={len(list(bank_compare_scorecard.get('ranked_candidates') or []))}",
                    ],
                )
            )
            payload["subagent_trace"] = trace
            return payload
        if force_fallback:
            degraded_note = runtime_degraded_note or RuntimeDegradeReason.LLM_UNAVAILABLE.value
            scorecard_digest = None
            scorecard_payload = None
            if intent == "distill_kol_logic":
                scorecard_digest = self._distillation_scorecard_digest(evidences)
                scorecard_payload = self._distillation_scorecard_payload(evidences)
            fallback_outputs, _ = self._invoke_subagent(
                SubagentInvocation(
                    subagent_type="fallback_report_composer",
                    objective="Compose degraded fallback report.",
                    inputs={
                        "intent": intent,
                        "query": query,
                        "evidences": evidences,
                        "review_summary_payload": review_summary_payload,
                        "scorecard_digest": scorecard_digest,
                        "scorecard_payload": scorecard_payload,
                        "screening_scorecard_payload": screening_scorecard_payload,
                        "screening_candidates_payload": screening_candidates_payload,
                        "methodology_payloads": methodology_payloads,
                        "report_strategy": report_strategy,
                        "degraded_note": degraded_note,
                    },
                    context={
                        "task_name": "report:fallback:fallback_report_composer",
                        "agent_path": "reporter/fallback/fallback_report_composer",
                    },
                )
            )
            trace.append(
                self._build_trace_record(
                    subagent_type="fallback_report_composer",
                    objective="Compose degraded fallback report.",
                    status="completed",
                    task_name="report:fallback:fallback_report_composer",
                    agent_path="reporter/fallback/fallback_report_composer",
                    notes=[
                        f"evidence_count={len(evidences)}",
                        f"degraded_note={degraded_note}",
                        f"execution_mode={execution_mode}",
                    ],
                )
            )
            outputs = dict(fallback_outputs)
            outputs["subagent_trace"] = trace
            return outputs

        scorecard_digest = None
        scorecard_payload = None
        if intent == "distill_kol_logic":
            scorecard_digest = self._distillation_scorecard_digest(evidences)
            scorecard_payload = self._distillation_scorecard_payload(evidences)
        llm_outputs, _ = self._invoke_subagent(
            SubagentInvocation(
                subagent_type="llm_report_composer",
                objective="Compose final report via LLM.",
                inputs={
                    "intent": intent,
                    "query": query,
                    "evidences": evidences,
                    "retrieval_summary": retrieval_summary,
                    "review_summary_payload": review_summary_payload,
                    "report_strategy": report_strategy,
                },
                context={
                    "task_name": "report:llm:llm_report_composer",
                    "agent_path": "reporter/llm/llm_report_composer",
                },
                requires_llm=True,
                fallback_subagent_type="fallback_report_composer",
                fallback_objective="Compose degraded fallback report.",
                fallback_inputs={
                    "intent": intent,
                    "query": query,
                    "evidences": evidences,
                    "review_summary_payload": review_summary_payload,
                    "scorecard_digest": scorecard_digest,
                    "scorecard_payload": scorecard_payload,
                    "screening_scorecard_payload": screening_scorecard_payload,
                    "screening_candidates_payload": screening_candidates_payload,
                    "methodology_payloads": methodology_payloads,
                    "report_strategy": report_strategy,
                    "degraded_note": runtime_degraded_note,
                },
                fallback_context={
                    "task_name": "report:fallback:fallback_report_composer",
                    "agent_path": "reporter/fallback/fallback_report_composer",
                },
                error_fallback_subagent_type="llm_error_report_composer",
                error_fallback_objective="Compose degraded report after LLM error.",
                error_fallback_inputs={
                    "intent": intent,
                    "query": query,
                    "evidences": evidences,
                    "review_summary_payload": review_summary_payload,
                    "scorecard_digest": scorecard_digest,
                    "scorecard_payload": scorecard_payload,
                    "screening_scorecard_payload": screening_scorecard_payload,
                    "screening_candidates_payload": screening_candidates_payload,
                    "screening_digest": self._screening_digest(screening_scorecard_payload, screening_candidates_payload) if intent == "stock_screen" else None,
                    "methodology_payloads": methodology_payloads,
                    "report_strategy": report_strategy,
                },
                error_fallback_context={
                    "task_name": "report:llm:error:llm_error_report_composer",
                    "agent_path": "reporter/llm/error/llm_error_report_composer",
                },
            )
        )
        degrade_reason = str(llm_outputs.get("degrade_reason") or "").strip()
        if "selected_evidence" not in llm_outputs and "evidence" in llm_outputs:
            if degrade_reason.startswith("llm_error:"):
                trace.append(
                    self._build_trace_record(
                        subagent_type="llm_report_composer",
                        objective="Compose final report via LLM.",
                        status="failed",
                        task_name="report:llm:llm_report_composer",
                        agent_path="reporter/llm/llm_report_composer",
                        notes=[f"error_name={degrade_reason.split(':', 1)[1] or 'LLMProviderError'}"],
                    )
                )
                trace.append(
                    self._build_trace_record(
                        subagent_type="llm_error_report_composer",
                        objective="Compose degraded report after LLM error.",
                        status="completed",
                        task_name="report:llm:error:llm_error_report_composer",
                        agent_path="reporter/llm/error/llm_error_report_composer",
                        notes=[f"degrade_reason={degrade_reason}"],
                    )
                )
            else:
                trace.append(
                    self._build_trace_record(
                        subagent_type="fallback_report_composer",
                        objective="Compose degraded fallback report.",
                        status="completed",
                        task_name="report:fallback:fallback_report_composer",
                        agent_path="reporter/fallback/fallback_report_composer",
                        notes=[
                            f"evidence_count={len(evidences)}",
                            f"degraded_note={degrade_reason or runtime_degraded_note or RuntimeDegradeReason.RUNTIME_FALLBACK.value}",
                            "execution_mode=runtime_fallback",
                        ],
                    )
                )
            outputs = dict(llm_outputs)
            outputs["subagent_trace"] = trace
            return outputs
        if degrade_reason.startswith("llm_error:"):
            trace.append(
                self._build_trace_record(
                    subagent_type="llm_error_report_composer",
                    objective="Compose degraded report after LLM error.",
                    status="completed",
                    task_name="report:llm:error:llm_error_report_composer",
                    agent_path="reporter/llm/error/llm_error_report_composer",
                    notes=[f"degrade_reason={llm_outputs.get('degrade_reason')}"],
                )
            )
            outputs = dict(llm_outputs)
            outputs["subagent_trace"] = trace
            return outputs
        selected_evidence = list(llm_outputs.get("selected_evidence") or [])
        screening_scorecard = self._screening_scorecard_payload(selected_evidence) if intent == "stock_screen" else None
        screening_candidates = self._screening_candidates_payload(selected_evidence) if intent == "stock_screen" else None
        trace.append(
            self._build_trace_record(
                subagent_type="llm_report_composer",
                objective="Compose final report via LLM.",
                status="completed",
                task_name="report:llm:llm_report_composer",
                agent_path="reporter/llm/llm_report_composer",
                notes=[
                    f"selected_evidence={len(selected_evidence)}",
                    f"degraded_note={str(llm_outputs.get('degraded_note') or 'none')}",
                ],
            )
        )
        return {
            "conclusion": llm_outputs["conclusion"],
            "evidence": selected_evidence[:5],
            "scorecard": self._distillation_scorecard_payload(selected_evidence) if intent == "distill_kol_logic" else None,
            "screening_scorecard": screening_scorecard,
            "candidates": screening_candidates,
            "methodologies": methodology_payloads or None,
            "review_summary": review_summary_payload,
            "risk": llm_outputs["risk"],
            "follow_up": self._merge_follow_up(llm_outputs["follow_up"], report_strategy),
            "degraded": bool(llm_outputs.get("degraded") or llm_outputs.get("degraded_note")),
            "degrade_reason": str(
                llm_outputs.get("degrade_reason")
                or llm_outputs.get("degraded_note")
                or ""
            ).strip()
            or None,
            "degraded_note": llm_outputs["degraded_note"],
            "subagent_trace": trace,
        }

    def _deterministic_stock_compare_report(
        self,
        *,
        evidences: list[dict],
        methodology_payloads: list[dict],
        report_strategy: dict[str, str | list[str] | None],
    ) -> dict:
        scorecard = self._bank_compare_scorecard_payload(methodology_payloads) or {}
        compare_digest = self._compare_digest(
            compare_targets=self._compare_targets_payload(evidences),
            methodology_payloads=methodology_payloads,
        )
        missing_metrics = [str(item) for item in list(scorecard.get("missing_metrics") or []) if str(item).strip()]
        risk = (
            "当前排序基于同口径银行股估值与经营质量快照，适合作为优先跟踪顺序，"
            "但仍不替代完整财报建模、资产质量穿透和行业景气判断。"
        )
        if missing_metrics:
            risk += f" 当前仍缺少部分关键字段：{', '.join(missing_metrics[:5])}。"
        follow_up = "继续补齐银行股同口径核心字段，尤其是净息差、不良率、拨备覆盖率、资本充足率和股息率，再做最终定序。"
        return {
            "conclusion": compare_digest,
            "evidence": evidences[:8],
            "methodologies": methodology_payloads or None,
            "review_summary": None,
            "risk": risk,
            "follow_up": self._merge_follow_up(follow_up, report_strategy),
            "degraded_note": None,
        }

    def _normalize_planning_context_hints(self, raw_hints) -> dict[str, str]:
        return normalize_planning_context_hints(raw_hints)

    def _build_trace_record(
        self,
        *,
        subagent_type: str,
        objective: str,
        status: str,
        task_name: str,
        agent_path: str,
        notes: list[str] | None = None,
    ) -> dict:
        return {
            "subagent_type": subagent_type,
            "objective": objective,
            "status": status,
            "task_name": task_name,
            "agent_path": agent_path,
            "notes": list(notes or []),
        }

    def _report_strategy_trace_notes(self, report_strategy: dict[str, str | list[str] | None]) -> list[str]:
        notes: list[str] = []
        focus_area = str(report_strategy.get("focus_area") or "").strip()
        preferred_methodology = str(report_strategy.get("preferred_methodology") or "").strip()
        if focus_area:
            notes.append(f"focus_area={focus_area}")
        if preferred_methodology:
            notes.append(f"preferred_methodology={preferred_methodology}")
        strategy_notes = [str(item).strip() for item in list(report_strategy.get("notes") or []) if str(item).strip()]
        notes.extend(strategy_notes)
        if not notes:
            notes.append("report_strategy=default")
        return notes

    def _derive_report_strategy(
        self,
        *,
        query: str,
        retrieval_summary: str | None,
        planning_context_hints: dict[str, str],
    ) -> dict[str, str | list[str] | None]:
        strategy: dict[str, str | list[str] | None] = {
            "focus_area": None,
            "preferred_methodology": None,
            "notes": [],
        }
        if not planning_context_hints:
            return strategy
        context = build_planning_context_view(
            query=query,
            planning_context_hints=planning_context_hints,
            extra_texts=[str(retrieval_summary or "")],
        )
        lowered = combine_focus_text(
            context.query_text,
            str(retrieval_summary or "").strip().lower(),
            context.recommended_next_step,
            context.open_question,
            context.previous_summary,
            context.thread_topic,
        )
        preferred_methodology, focus_source = derive_methodology_focus(
            cashflow_text=lowered,
            comparison_text=lowered,
            planning_context_hints=planning_context_hints,
        )
        if preferred_methodology == "cashflow_resilience":
            strategy["focus_area"] = "cashflow_resilience"
            strategy["preferred_methodology"] = preferred_methodology
        elif preferred_methodology == "comps":
            strategy["focus_area"] = "same_basis_compare"
            strategy["preferred_methodology"] = preferred_methodology
        elif preferred_methodology == "dcf":
            strategy["focus_area"] = "valuation"
            strategy["preferred_methodology"] = preferred_methodology
        elif has_policy_focus(lowered):
            strategy["focus_area"] = "catalyst_validation"
        elif has_momentum_focus(lowered):
            strategy["focus_area"] = "momentum_validation"
        if strategy["focus_area"]:
            strategy["notes"] = [f"report_focus={strategy['focus_area']}"]
            if focus_source:
                strategy["notes"].append(f"report_focus_source={focus_source}")
        return strategy

    def _strategy_follow_up(self, report_strategy: dict[str, str | list[str] | None]) -> str | None:
        return strategy_follow_up(report_strategy)

    def _merge_follow_up(
        self,
        base_follow_up: str | None,
        report_strategy: dict[str, str | list[str] | None],
    ) -> str | None:
        strategy_follow_up = self._strategy_follow_up(report_strategy)
        if not strategy_follow_up:
            return base_follow_up
        base_text = str(base_follow_up or "").strip()
        if not base_text:
            return strategy_follow_up
        if strategy_follow_up.lower() in base_text.lower():
            return base_text
        return f"{base_text} {strategy_follow_up}"

    def _compare_targets_payload(self, evidences: list[dict]) -> list[dict]:
        for item in evidences:
            hint = item.get("retrieval_hint") or {}
            if hint.get("hint_type") == "compare_request":
                targets = hint.get("compare_targets") or []
                if isinstance(targets, list):
                    return [
                        {
                            "symbol": str(target.get("symbol") or "").strip(),
                            "name": str(target.get("name") or target.get("symbol") or "").strip(),
                        }
                        for target in targets
                        if isinstance(target, dict) and str(target.get("symbol") or "").strip()
                    ]
            metadata = item.get("metadata") or {}
            if str(metadata.get("source_type") or "") != "compare_request":
                continue
            summary = str(item.get("summary") or "")
            lines = summary.splitlines()
            if len(lines) < 2:
                continue
            tail = lines[-1].strip()
            import ast
            try:
                parsed = ast.literal_eval(tail)
            except Exception:
                parsed = None
            if not isinstance(parsed, dict):
                continue
            targets = parsed.get("compare_targets") or []
            if isinstance(targets, list):
                return [
                    {
                        "symbol": str(target.get("symbol") or "").strip(),
                        "name": str(target.get("name") or target.get("symbol") or "").strip(),
                    }
                    for target in targets
                    if isinstance(target, dict) and str(target.get("symbol") or "").strip()
                ]
        return []

    def _compare_digest(self, *, compare_targets: list[dict], methodology_payloads: list[dict]) -> str:
        scorecard = self._bank_compare_scorecard_payload(methodology_payloads)
        if isinstance(scorecard, dict):
            digest = self._bank_compare_digest(scorecard)
            if digest:
                return digest
        if not compare_targets:
            return "当前比较请求缺少可识别的目标股票，无法形成横向结论。"
        methodology_by_symbol: dict[str, dict[str, dict]] = {}
        for item in methodology_payloads:
            title = str(item.get("title") or "")
            symbol = title.split(":", 1)[1].strip() if ":" in title else ""
            if not symbol:
                continue
            methodology_by_symbol.setdefault(symbol, {})[str(item.get("methodology_type") or "")] = item

        lines = [f"已按同一研究口径对 {len(compare_targets)} 只股票做对比。"]
        scored_targets: list[tuple[float, str, str]] = []
        for target in compare_targets:
            symbol = str(target.get("symbol") or "").strip()
            name = str(target.get("name") or symbol).strip() or symbol
            methods = methodology_by_symbol.get(symbol, {})
            comps = methods.get("comps")
            dcf = methods.get("dcf")
            resilience = methods.get("cashflow_resilience")
            notes: list[str] = []
            score = 0.0
            if comps:
                view = self._methodology_output_value(comps, "relative_view")
                if view:
                    notes.append(f"comps={view}")
                    if view == "relative_discount_like":
                        score += 2.0
                    elif view == "growth_supported_neutral":
                        score += 1.0
                    elif view == "relative_fair_like":
                        score += 0.5
                    elif view == "relative_premium_like":
                        score -= 1.0
            if dcf:
                gap = self._methodology_output_float(dcf, "market_to_intrinsic_gap")
                if gap is not None:
                    notes.append(f"dcf_gap={gap:.2%}")
                    if gap <= -0.1:
                        score += 1.5
                    elif gap >= 0.1:
                        score -= 1.0
            if resilience:
                debt_state = self._methodology_output_value(resilience, "debt_capacity_state")
                cashflow_state = self._methodology_output_value(resilience, "cashflow_state")
                if debt_state:
                    notes.append(f"debt={debt_state}")
                    if debt_state == "comfortable":
                        score += 0.8
                    elif debt_state == "stressed":
                        score -= 1.2
                if cashflow_state:
                    notes.append(f"cashflow={cashflow_state}")
                    if cashflow_state == "resilient":
                        score += 0.8
                    elif cashflow_state == "fragile":
                        score -= 1.0
            if not notes:
                notes.append("methodology_signal=limited")
            lines.append(f"{name}（{symbol}）：{'；'.join(notes)}。")
            scored_targets.append((score, symbol, name))

        available_signal_count = sum(1 for item in scored_targets if item[0] != 0.0)
        if len(scored_targets) >= 2 and available_signal_count > 0:
            winner = max(scored_targets, key=lambda item: item[0])
            if winner[0] > 0:
                lines.append(f"基于当前相对估值、轻量 DCF 和现金流韧性信号，暂时更占优的是 {winner[2]}（{winner[1]}）。")
            else:
                lines.append("当前方法论信号没有形成明显领先者，更适合作为并列观察名单。")
        else:
            lines.append("当前证据更多是单票快照，仍需补充更完整估值和行业景气数据后再排序。")
        return " ".join(lines)

    def _bank_compare_scorecard_payload(self, items: list[dict]) -> dict | None:
        for item in items:
            source_type = str(item.get("source_type") or item.get("metadata", {}).get("source_type") or "")
            if source_type != "bank_compare_scorecard":
                continue
            summary = str(item.get("summary") or "")
            tail = summary.split("\n", 1)[1] if "\n" in summary else summary
            try:
                parsed = ast.literal_eval(tail.strip())
            except Exception:
                parsed = None
            if isinstance(parsed, dict):
                return parsed
        return None

    def _bank_compare_digest(self, scorecard: dict) -> str | None:
        ranked_candidates = list(scorecard.get("ranked_candidates") or [])
        if not ranked_candidates:
            return None
        top_tier = ranked_candidates[:3]
        metric_weights = dict(scorecard.get("metric_weights") or {})
        valuation_weight = float(metric_weights.get("pb") or 0.0)
        lines = [
            f"基于同口径银行股指标与估值锚点，本次共比较 {int(scorecard.get('candidate_count') or len(ranked_candidates))} 只银行股。",
            f"当前更值得优先跟踪的第一梯队是：{', '.join(str(item.get('name') or item.get('symbol') or '').strip() for item in top_tier if str(item.get('name') or item.get('symbol') or '').strip())}。",
        ]
        if valuation_weight > 0:
            lines.append(f"本轮排序已把 PB 纳入核心权重，权重约为 {valuation_weight:.0%}，不再只依赖经营质量代理指标。")
        for item in top_tier:
            name = str(item.get("name") or item.get("symbol") or "").strip()
            symbol = str(item.get("symbol") or "").strip()
            if not name:
                continue
            detail = f"{name}（{symbol}）"
            if item.get("score") is not None:
                detail += f" score={float(item.get('score')):.2f}"
            if item.get("pb") is not None:
                detail += f"，PB={float(item.get('pb')):.2f}"
            highlights = [str(x) for x in list(item.get("highlights") or []) if str(x).strip()]
            if highlights:
                detail += f"，核心指标：{'；'.join(highlights[:3])}"
            lines.append(detail + "。")
        leader = top_tier[0]
        leader_name = str(leader.get("name") or leader.get("symbol") or "").strip()
        if leader_name:
            lines.append(f"当前综合得分领先的是 {leader_name}，但这仍然是基于同口径估值与经营质量的快速排序，不替代完整财报建模。")
        missing_metrics = [str(item) for item in list(scorecard.get("missing_metrics") or []) if str(item).strip()]
        if missing_metrics:
            lines.append(f"仍有缺口指标：{', '.join(missing_metrics[:5])}，补齐后再做最终定序。")
        candidate_gap_digest = self._bank_compare_candidate_gap_digest(top_tier)
        if candidate_gap_digest:
            lines.append(candidate_gap_digest)
        return " ".join(lines)

    def _bank_compare_candidate_gap_digest(self, ranked_candidates: list[dict]) -> str | None:
        gap_parts: list[str] = []
        for item in ranked_candidates:
            name = str(item.get("name") or item.get("symbol") or "").strip()
            missing = [str(metric) for metric in list(item.get("missing_metrics") or []) if str(metric).strip()]
            if not name or not missing:
                continue
            gap_parts.append(f"{name} 缺少 {', '.join(missing[:3])}")
        if not gap_parts:
            return None
        return "个别银行仍有字段缺口：" + "；".join(gap_parts) + "。"

    def _methodology_output_value(self, methodology_payload: dict, key: str) -> str | None:
        result = self._parse_methodology_result(methodology_payload)
        outputs = result.get("outputs") or {}
        value = outputs.get(key)
        if value in (None, ""):
            return None
        return str(value)

    def _methodology_output_float(self, methodology_payload: dict, key: str) -> float | None:
        value = self._methodology_output_value(methodology_payload, key)
        if value is None:
            return None
        try:
            return float(value)
        except Exception:
            return None

    def _parse_methodology_result(self, methodology_payload: dict) -> dict:
        summary = str(methodology_payload.get("summary") or "")
        lines = summary.splitlines()
        payload: dict = {}
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("outputs="):
                try:
                    payload["outputs"] = ast.literal_eval(stripped.split("=", 1)[1].strip())
                except Exception:
                    payload["outputs"] = {}
            elif stripped.startswith("assumptions="):
                try:
                    payload["assumptions"] = ast.literal_eval(stripped.split("=", 1)[1].strip())
                except Exception:
                    payload["assumptions"] = {}
            elif stripped.startswith("warnings="):
                try:
                    payload["warnings"] = ast.literal_eval(stripped.split("=", 1)[1].strip())
                except Exception:
                    payload["warnings"] = []
        return payload

    def _distillation_attribution_digest(self, evidences: list[dict]) -> str:
        counts = {
            "author": 0,
            "reply": 0,
            "repost": 0,
            "reported": 0,
            "quoted": 0,
            "uncertain": 0,
        }
        for item in evidences:
            metadata = item.get("metadata") or {}
            if str(metadata.get("source_type") or "") != "logic_block":
                continue
            summary = str(item.get("summary") or "")
            marker = "speaker_type="
            if marker not in summary:
                continue
            speaker_type = summary.split(marker, 1)[1].split()[0].strip()
            if speaker_type in counts:
                counts[speaker_type] += 1

        non_zero = [f"{name}={count}" for name, count in counts.items() if count > 0]
        if not non_zero:
            return "Attribution mix unavailable from selected evidence."
        return "Attribution mix: " + ", ".join(non_zero) + "."

    def _distillation_scorecard_digest(self, evidences: list[dict]) -> str:
        for item in evidences:
            metadata = item.get("metadata") or {}
            if str(metadata.get("source_type") or "") != "distillation_scorecard":
                continue
            summary = str(item.get("summary") or "")
            if "\n" in summary:
                _, tail = summary.split("\n", 1)
                return f"Scorecard: {tail}"
            return f"Scorecard: {summary}"
        return self._distillation_attribution_digest(evidences)

    def _distillation_scorecard_payload(self, evidences: list[dict]) -> dict | None:
        for item in evidences:
            metadata = item.get("metadata") or {}
            if str(metadata.get("source_type") or "") != "distillation_scorecard":
                continue
            summary = str(item.get("summary") or "")
            if "\n" not in summary:
                return None
            _, tail = summary.split("\n", 1)
            import ast

            try:
                parsed = ast.literal_eval(tail)
            except Exception:
                return None
            return parsed if isinstance(parsed, dict) else None
        return None

    def _review_verdict_by_claim_type(self, review_findings: list[dict]) -> dict[str, ClaimVerdictBreakdown]:
        buckets: dict[str, ClaimVerdictBreakdown] = {}
        for item in review_findings:
            summary = str(item.get("summary") or "")
            if "claim_type=" not in summary or "verdict=" not in summary:
                continue
            claim_type = summary.split("claim_type=", 1)[1].split()[0].strip()
            verdict = summary.split("verdict=", 1)[1].split()[0].strip()
            if claim_type not in buckets:
                buckets[claim_type] = ClaimVerdictBreakdown()
            current = buckets[claim_type]
            if verdict == "confirmed":
                current.confirmed += 1
            elif verdict == "rejected":
                current.rejected += 1
            else:
                current.inconclusive += 1
        return buckets

    def _review_verdict_by_claim_type_payload(self, review_findings: list[dict]) -> dict[str, dict[str, int]]:
        return {
            claim_type: breakdown.model_dump(mode="json")
            for claim_type, breakdown in self._review_verdict_by_claim_type(review_findings).items()
        }

    def _review_benchmark_scope_counts(self, review_findings: list[dict]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in review_findings:
            summary = str(item.get("summary") or "")
            if "scope=" not in summary:
                continue
            scope = summary.split("scope=", 1)[1].split()[0].strip()
            counts[scope] = counts.get(scope, 0) + 1
        return counts

    def _review_benchmark_scope_explanations(self, benchmark_scope_counts: dict[str, int]) -> dict[str, str]:
        explanations: dict[str, str] = {}
        for scope in benchmark_scope_counts:
            if scope == "stock_vs_sector":
                explanations[scope] = "个股方向主张按个股相对所属板块表现复核。"
            elif scope == "sector_vs_hs300":
                explanations[scope] = "板块方向主张按板块相对 HS300 表现复核。"
            elif scope == "stock_absolute":
                explanations[scope] = "缺少板块基准时，个股方向主张按个股自身涨跌做最小复核。"
            else:
                explanations[scope] = "按当前 review policy 的默认 benchmark 口径复核。"
        return explanations

    def _review_overall_verdict(
        self,
        *,
        finding_count: int,
        confirmed: int,
        rejected: int,
        inconclusive: int,
    ) -> str:
        if finding_count == 0:
            return "no_findings"
        if rejected > 0 and confirmed == 0:
            return "rejected"
        if rejected > 0 and confirmed > 0:
            return "mixed"
        if inconclusive > 0 and confirmed == 0:
            return "inconclusive"
        if confirmed > 0 and inconclusive > 0:
            return "confirmed_with_caveats"
        if confirmed > 0:
            return "confirmed"
        return "inconclusive"

    def _review_summary_text(
        self,
        *,
        overall_verdict: str,
        finding_count: int,
        confirmed: int,
        rejected: int,
        inconclusive: int,
        benchmark_scope_counts: dict[str, int],
    ) -> str:
        scope_labels = ",".join(sorted(benchmark_scope_counts.keys())) if benchmark_scope_counts else "no_benchmark"
        return (
            f"overall_verdict={overall_verdict}; findings={finding_count}; "
            f"confirmed={confirmed}; rejected={rejected}; inconclusive={inconclusive}; "
            f"benchmark_scopes={scope_labels}"
        )

    def _normalize_review_conclusion(self, *, conclusion: str, review_summary_payload: dict | None) -> str:
        if not review_summary_payload:
            return conclusion
        overall_verdict = str(review_summary_payload.get("overall_verdict") or "").strip()
        if not overall_verdict:
            return conclusion
        if f"overall_verdict={overall_verdict}" in conclusion:
            return conclusion
        return f"overall_verdict={overall_verdict}. {conclusion}"

    def _review_summary_payload(self, evidences: list[dict]) -> dict | None:
        for item in evidences:
            metadata = item.get("metadata") or {}
            if str(metadata.get("source_type") or "") != "review_summary":
                continue
            summary = str(item.get("summary") or "")
            if "\n" not in summary:
                continue
            _, tail = summary.split("\n", 1)
            import ast
            import json

            try:
                parsed = ast.literal_eval(tail)
            except Exception:
                try:
                    parsed = json.loads(tail)
                except Exception:
                    normalized_tail = (
                        tail.replace(": true", ": True")
                        .replace(": false", ": False")
                        .replace(": null", ": None")
                    )
                    try:
                        parsed = ast.literal_eval(normalized_tail)
                    except Exception:
                        parsed = None
            if isinstance(parsed, dict):
                return parsed
        review_findings = [
            item for item in evidences
            if str((item.get("metadata") or {}).get("source_type") or "") == "review_finding"
        ]
        if not review_findings:
            return None
        confirmed = sum(1 for item in review_findings if "verdict=confirmed" in str(item.get("summary") or ""))
        rejected = sum(1 for item in review_findings if "verdict=rejected" in str(item.get("summary") or ""))
        inconclusive = sum(1 for item in review_findings if "verdict=inconclusive" in str(item.get("summary") or ""))
        claim_type_counts: dict[str, int] = {}
        verdict_by_claim_type = self._review_verdict_by_claim_type(review_findings)
        benchmark_scope_counts = self._review_benchmark_scope_counts(review_findings)
        for claim_type, breakdown in verdict_by_claim_type.items():
            claim_type_counts[claim_type] = breakdown.confirmed + breakdown.rejected + breakdown.inconclusive
        overall_verdict = self._review_overall_verdict(
            finding_count=len(review_findings),
            confirmed=confirmed,
            rejected=rejected,
            inconclusive=inconclusive,
        )
        summary = PosteriorReviewSummary(
            review_required=False,
            finding_count=len(review_findings),
            reviewable_claim_count=len(review_findings),
            claim_type_counts=claim_type_counts,
            benchmark_scope_counts=benchmark_scope_counts,
            benchmark_scope_explanations=self._review_benchmark_scope_explanations(benchmark_scope_counts),
            verdict_by_claim_type=verdict_by_claim_type,
            overall_verdict=overall_verdict,
            summary_text=self._review_summary_text(
                overall_verdict=overall_verdict,
                finding_count=len(review_findings),
                confirmed=confirmed,
                rejected=rejected,
                inconclusive=inconclusive,
                benchmark_scope_counts=benchmark_scope_counts,
            ),
            confirmed=confirmed,
            rejected=rejected,
            inconclusive=inconclusive,
            notes=[],
        )
        return summary.model_dump(mode="json")

    def _screening_scorecard_payload(self, evidences: list[dict]) -> dict | None:
        for item in evidences:
            metadata = item.get("metadata") or {}
            if str(metadata.get("source_type") or "") != "screening_scorecard":
                continue
            summary = str(item.get("summary") or "")
            if "\n" not in summary:
                return None
            _, tail = summary.split("\n", 1)
            import ast

            try:
                parsed = ast.literal_eval(tail)
            except Exception:
                return None
            return parsed if isinstance(parsed, dict) else None
        return None

    def _screening_candidates_payload(self, evidences: list[dict]) -> list[dict]:
        candidates: list[dict] = []
        for item in evidences:
            metadata = item.get("metadata") or {}
            source_type = str(metadata.get("source_type") or "")
            if source_type not in {"screen_candidate", "watchlist_candidate"}:
                continue
            summary = str(item.get("summary") or "")
            title = str(item.get("title") or "")
            symbol = title.split(":", 1)[1].strip() if ":" in title else title
            parsed_candidate = self._parse_screen_candidate_summary(summary)
            name = str(parsed_candidate.get("name") or symbol)
            rationale = str(parsed_candidate.get("why_it_made_the_list") or "")
            risk = str(parsed_candidate.get("main_risk") or "")
            score = parsed_candidate.get("score")
            missing_evidence = parsed_candidate.get("missing_evidence") or ""
            deep_checked = bool(parsed_candidate.get("deep_checked"))
            candidate = {
                "symbol": symbol,
                "name": name,
                "title": title,
                "summary": summary,
                "score": score,
                "why_it_made_the_list": rationale,
                "main_risk": risk,
                "missing_evidence": missing_evidence,
                "deep_checked": deep_checked,
                "candidate_tier": "watchlist" if source_type == "watchlist_candidate" else "core",
            }
            candidates.append(candidate)
        return candidates

    def _methodology_payloads(self, evidences: list[dict]) -> list[dict]:
        payloads: list[dict] = []
        for item in evidences:
            metadata = item.get("metadata") or {}
            source_type = str(metadata.get("source_type") or "")
            if not source_type.startswith("methodology_") and source_type != "bank_compare_scorecard":
                continue
            payloads.append(
                {
                    "title": str(item.get("title") or ""),
                    "source_type": source_type,
                    "methodology_type": metadata.get("methodology_type"),
                    "valuation_band_label": metadata.get("valuation_band_label"),
                    "summary": str(item.get("summary") or ""),
                }
            )
        return payloads

    def _parse_screen_candidate_summary(self, summary: str) -> dict:
        import ast

        lines = summary.splitlines()
        if len(lines) >= 2:
            tail = lines[-1].strip()
            if tail.startswith("{") and tail.endswith("}"):
                try:
                    parsed = ast.literal_eval(tail)
                except Exception:
                    parsed = None
                if isinstance(parsed, dict):
                    return parsed

        parsed: dict = {}
        for line in lines:
            if line.startswith("screen_candidate symbol=") or line.startswith("watchlist_candidate symbol="):
                parts = line.split()
                for part in parts:
                    if "=" not in part:
                        continue
                    key, value = part.split("=", 1)
                    if key == "symbol":
                        parsed["symbol"] = value.strip()
                    elif key == "name":
                        parsed["name"] = value.strip()
            elif line.startswith("why="):
                parsed["why_it_made_the_list"] = line.split("=", 1)[1].strip()
            elif line.startswith("risk="):
                parsed["main_risk"] = line.split("=", 1)[1].strip()
            elif line.startswith("score="):
                parsed["score"] = line.split("=", 1)[1].split()[0].strip()
            elif line.startswith("deep_checked="):
                parsed["deep_checked"] = line.split("=", 1)[1].strip().lower() == "true"
            elif line.startswith("missing_evidence="):
                parsed["missing_evidence"] = line.split("=", 1)[1].strip()
        return parsed

    def _screening_digest(self, screening_scorecard_payload: dict | None, screening_candidates_payload: list[dict]) -> str:
        if not screening_candidates_payload:
            return "未筛出满足最小条件的候选，当前更适合继续观察而不是直接给出标的。"
        core_candidates = [item for item in screening_candidates_payload if str(item.get("candidate_tier") or "core") == "core"]
        watchlist_candidates = [item for item in screening_candidates_payload if str(item.get("candidate_tier") or "") == "watchlist"]
        top_names = [str(item.get("name") or item.get("symbol") or "") for item in core_candidates[:3] if str(item.get("name") or item.get("symbol") or "").strip()]
        watch_names = [str(item.get("name") or item.get("symbol") or "") for item in watchlist_candidates[:3] if str(item.get("name") or item.get("symbol") or "").strip()]
        cycle = str((screening_scorecard_payload or {}).get("investment_cycle") or "mid_term")
        summary = str((screening_scorecard_payload or {}).get("summary") or "").strip()
        deep_checked_count = int((screening_scorecard_payload or {}).get("deep_checked_count") or 0)
        first = (core_candidates or watchlist_candidates or screening_candidates_payload)[0]
        leader = str(first.get("name") or first.get("symbol") or "").strip()
        leader_reason = str(first.get("why_it_made_the_list") or "").strip()
        leader_risk = str(first.get("main_risk") or "").strip()
        lines = [f"当前更适合作为 {cycle} 观察名单，而不是直接交易指令。"]
        if top_names:
            lines.append(f"优先关注：{', '.join(top_names)}。")
        if watch_names:
            lines.append(f"次级观察：{', '.join(watch_names)}。")
        if summary:
            lines.append(summary)
        if deep_checked_count > 0:
            lines.append(f"其中有 {deep_checked_count} 只候选已经完成市场、财务和公告的深度核查。")
        else:
            lines.append("当前候选仍以轻量筛选为主，尚未完成系统性的深度核查。")
        if leader and leader_reason:
            lines.append(f"其中 {leader} 的入选理由是：{leader_reason}。")
        if leader_risk:
            lines.append(f"主要风险是：{leader_risk}。")
        return " ".join(lines)
