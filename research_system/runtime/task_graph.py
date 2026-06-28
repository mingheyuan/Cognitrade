from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TaskNode:
    node_id: str
    phase: str
    agent: str


@dataclass(frozen=True)
class TaskGraph:
    intent: str
    nodes: tuple[TaskNode, ...]

    @property
    def node_ids(self) -> list[str]:
        return [node.node_id for node in self.nodes]


def build_task_graph(intent: str) -> TaskGraph:
    if intent == "stock_check":
        return TaskGraph(
            intent=intent,
            nodes=(
                TaskNode("collect", "collect", "collector"),
                TaskNode("retrieve", "retrieve", "retriever"),
                TaskNode("report", "report", "reporter"),
            ),
        )
    if intent == "sector_research":
        return TaskGraph(
            intent=intent,
            nodes=(
                TaskNode("collect", "collect", "collector"),
                TaskNode("retrieve", "retrieve", "retriever"),
                TaskNode("report", "report", "reporter"),
            ),
        )
    if intent == "distill_kol_logic":
        return TaskGraph(
            intent=intent,
            nodes=(
                TaskNode("collect", "collect", "collector"),
                TaskNode("distill", "distill", "distiller"),
                TaskNode("critic", "review", "critic"),
                TaskNode("retrieve", "retrieve", "retriever"),
                TaskNode("report", "report", "reporter"),
            ),
        )
    if intent == "review_thesis":
        return TaskGraph(
            intent=intent,
            nodes=(
                TaskNode("collect", "collect", "collector"),
                TaskNode("review", "review", "reviewer"),
                TaskNode("retrieve", "retrieve", "retriever"),
                TaskNode("report", "report", "reporter"),
            ),
        )
    raise ValueError(f"unsupported intent for current phase: {intent}")
