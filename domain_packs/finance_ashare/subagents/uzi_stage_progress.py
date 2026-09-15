"""Run-local counts for parallel, validated UZI analysis stages."""
import logging
from threading import RLock

from domain_packs.finance_ashare.subagents.uzi_llm_support import (
    EXHAUSTED_MODEL_FAILURES,
)


def notify_stage_progress(callback, payload):
    """A display observer cannot change the native research result."""
    if callable(callback):
        try:
            callback(payload)
        except Exception:
            logging.getLogger("uvicorn.error").warning("UZI stage progress callback failed", exc_info=True)


def failed_stage_next_action(failure):
    """Describe recovery eligibility, never promise a retry that is not queued."""
    kind = failure.get("kind")
    if kind == "llm_cancelled":
        return "取消已请求；等待在途阶段退出并保留已完成结果"
    if kind == "source_gap":
        return "回到数据中心显式补证，固定新输入后再恢复受影响阶段"
    if kind == "llm_provider_unavailable" and failure.get("provider_retryable") is False:
        return "模型请求不可重试；保留服务错误原因，处理后再恢复"
    if kind in EXHAUSTED_MODEL_FAILURES:
        return "本阶段请求或恢复预算已结束；保留失败原因，不自动重跑整股"
    if kind == "agent_content_invalid":
        return "保留校验问题；自审后按 suggested_fix 和剩余预算决定是否局部修复"
    return "保留具体执行故障，修复原因后再恢复；不自动重跑整股"


class WorkflowStageProgress:
    """One stock/cycle's validated stage count, not elapsed-time estimation.

    Reserve preparation and delivery outside 8–90%. Local phase counts remain
    separate; an HTTP response alone cannot complete a research/roleplay stage.
    """

    def __init__(self, callback, *, totals):
        self.callback = callback
        self.totals = totals
        self.completed = dict.fromkeys(totals, 0)
        self.lock = RLock()

    @property
    def percent(self):
        return 8 + round(sum(self.completed.values()) / max(sum(self.totals.values()), 1) * 82)

    def phase(self, phase):
        def notify(payload):
            with self.lock:
                if "completed_stages" in payload:
                    self.completed[phase] = max(self.completed[phase], min(
                        int(payload["completed_stages"]), self.totals[phase]))
                if callable(self.callback):
                    notify_stage_progress(self.callback, {
                        "current_action": f"UZI {phase}：{payload.get('stage', '')}",
                        "next_action": "按既有预算执行当前阶段，模型返回后仍需校验",
                        **payload,
                        "progress_scope": "stock_agent_stages",
                        "workflow_phase": phase,
                        "workflow_completed_stages": sum(self.completed.values()),
                        "workflow_total_stages": sum(self.totals.values()),
                        "progress_percent": self.percent,
                    })
        return notify


class AnalysisStageProgress:
    """Serialize notifications and count a stage only after validation.

    Stage ordinal is routing metadata, not completion. Model responses,
    invalid cache entries and repair attempts never increment this ledger.
    Each analysis invocation has its own ledger, including recovery cycles.
    """

    def __init__(self, callback, *, total: int, cycle: int):
        self.callback = callback
        self.total = total
        self.cycle = cycle
        self.completed: set[str] = set()
        self.lock = RLock()

    def __call__(self, payload):
        with self.lock:
            if callable(self.callback):
                self.callback({
                    **payload,
                    "stage_total": self.total,
                    "completed_stages": len(self.completed),
                    "progress_percent": 8 + round(len(self.completed) / max(self.total, 1) * 70),
                })

    def complete(self, stage: str, *, ordinal: int, cache_hit: bool):
        with self.lock:
            if stage in self.completed:
                return
            self.completed.add(stage)
            self({
                "current_action": f"UZI 第 {self.cycle} 轮已完成 {len(self.completed)}/{self.total}：{stage}",
                "next_action": "继续剩余结构化阶段及最终自审",
                "stage": stage,
                "stage_index": ordinal,
                "stage_phase": "validated",
                "cycle": self.cycle,
                "cache_hit": cache_hit,
                **({"cache_reason": "content_profile_source_prompt_match"} if cache_hit else {}),
            })
