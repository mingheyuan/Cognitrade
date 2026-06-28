from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from research_system.schemas.runtime import RunEvent


DisplayKind = Literal["thinking", "status", "event"]


@dataclass(frozen=True)
class DisplayChunk:
    kind: DisplayKind
    text: str


@dataclass
class PresenterState:
    phase_path: list[str] = field(default_factory=list)
    gates: dict[str, str] = field(default_factory=dict)


class ChatStreamPresenter:
    def __init__(self, *, thinking_enabled: bool = True, events_mode: str = "compact") -> None:
        self.thinking_enabled = thinking_enabled
        self.events_mode = events_mode
        self.state = PresenterState()

    def on_event(self, event: RunEvent) -> list[DisplayChunk]:
        chunks: list[DisplayChunk] = []

        if self.events_mode == "full":
            chunks.append(DisplayChunk(kind="event", text=self._raw_event_text(event)))

        if event.event_type == "intent_identified":
            if self.thinking_enabled:
                chunks.append(DisplayChunk(kind="thinking", text=self._intent_text(event.intent)))
            return chunks

        if event.event_type == "phase_started" and event.phase:
            if event.phase not in self.state.phase_path and event.phase != "gate":
                self.state.phase_path.append(event.phase)
                chunks.append(DisplayChunk(kind="status", text=" > ".join(self.state.phase_path)))
            if self.thinking_enabled:
                thought = self._phase_thinking(intent=event.intent, phase=event.phase)
                if thought:
                    chunks.append(DisplayChunk(kind="thinking", text=thought))
            return chunks

        if event.event_type == "review_requested":
            if self.thinking_enabled:
                chunks.append(DisplayChunk(kind="thinking", text=self._review_requested_text(event.agent)))
            return chunks

        if event.event_type == "review_completed":
            if self.thinking_enabled:
                chunks.append(DisplayChunk(kind="thinking", text=self._review_completed_text(event.agent, event.payload or {})))
            return chunks

        if event.event_type == "phase_completed" and event.phase == "gate":
            gate_type, verdict = self._parse_gate_message(event.message)
            if gate_type and verdict:
                self.state.gates[gate_type] = verdict
                if self.events_mode != "off" or verdict != "pass":
                    chunks.append(DisplayChunk(kind="status", text=self._gate_summary()))
                if self.thinking_enabled and verdict in {"needs_review", "fail"}:
                    chunks.append(DisplayChunk(kind="thinking", text=self._gate_thinking(gate_type, verdict)))
            return chunks

        if event.event_type == "run_completed":
            if self.thinking_enabled:
                chunks.append(DisplayChunk(kind="thinking", text="证据和结论已经整理完成。"))
            return chunks

        if event.event_type == "run_failed":
            if self.thinking_enabled:
                chunks.append(DisplayChunk(kind="thinking", text="本次运行失败，建议回放 run 或调整问题后重试。"))
            return chunks

        if event.event_type == "run_timed_out":
            if self.thinking_enabled:
                chunks.append(DisplayChunk(kind="thinking", text="本次运行超时，已经停止。"))
            return chunks

        if event.event_type == "run_cancelled":
            if self.thinking_enabled:
                chunks.append(DisplayChunk(kind="thinking", text="当前任务已停止。"))
            return chunks

        return chunks

    def _raw_event_text(self, event: RunEvent) -> str:
        tags: list[str] = [event.event_type]
        if event.phase:
            tags.append(event.phase)
        if event.agent:
            tags.append(event.agent)
        return f"[{'/'.join(tags)}] {event.message}"

    def _intent_text(self, intent: str) -> str:
        labels = {
            "stock_check": "这是一个单票研究请求，我会先核验行情、基本面和近期公告。",
            "sector_research": "这是一个板块/主题研究请求，我会先看板块走势、成分股和近期催化。",
            "distill_kol_logic": "这是一个观点蒸馏请求，我会先整理原始文本，再抽取逻辑块和风险。",
            "review_thesis": "这是一个后验复核请求，我会回看旧结论并用最新证据做验证。",
        }
        return labels.get(intent, f"我会按 `{intent}` 这条链路继续处理。")

    def _phase_thinking(self, *, intent: str, phase: str) -> str | None:
        if phase == "collect":
            if intent == "stock_check":
                return "我先收集个股快照，确认价格、基本面和公告证据是否齐全。"
            if intent == "sector_research":
                return "我先拉板块快照，确认走势、广度、基准对照和催化线索。"
            if intent == "distill_kol_logic":
                return "我先读取原始文本，准备拆分观点、维度和引用归属。"
            if intent == "review_thesis":
                return "我先读取目标 run 的证据和结果，再补最新快照做复核。"
            return "我先收集执行这次请求所需的基础证据。"
        if phase == "distill":
            return "我在把原始内容压缩成可复用的逻辑块，并标出风险和归属。"
        if phase == "review":
            return "我在做抽检或后验复核，确认哪些判断成立、哪些还不够稳。"
        if phase == "retrieve":
            return "我在从当前 run 和历史证据里挑最相关的材料，避免遗漏关键上下文。"
        if phase == "report":
            return "我开始整理最终回答，优先保留可核验的结论、风险和后续动作。"
        return None

    def _review_requested_text(self, agent: str | None) -> str:
        if agent == "critic":
            return "我在抽检蒸馏结果，重点看低置信度、引用归属和重复观点。"
        if agent == "reviewer":
            return "我在做后验复核，检查方向、催化和时间窗口是否被最新证据支持。"
        return "我在做额外复核，确认关键判断是否可靠。"

    def _review_completed_text(self, agent: str | None, payload: dict) -> str:
        finding_count = payload.get("finding_count")
        if agent == "critic":
            return f"蒸馏抽检完成。当前发现 {finding_count or 0} 条需要注意的问题。"
        if agent == "reviewer":
            return f"后验复核完成。当前整理出 {finding_count or 0} 条复核结论。"
        return "复核步骤已完成。"

    def _parse_gate_message(self, message: str) -> tuple[str | None, str | None]:
        if ":" not in message:
            return None, None
        gate_type, verdict = message.split(":", 1)
        return gate_type.strip(), verdict.strip()

    def _gate_summary(self) -> str:
        if not self.state.gates:
            return "gate: pending"
        ordered = [f"{gate} {verdict}" for gate, verdict in sorted(self.state.gates.items())]
        return "gate: " + " | ".join(ordered)

    def _gate_thinking(self, gate_type: str, verdict: str) -> str:
        if verdict == "needs_review":
            return f"{gate_type} 提示还需要人工复核，我会保留这个风险。"
        return f"{gate_type} 没通过，当前结果不能直接当作可靠结论。"
