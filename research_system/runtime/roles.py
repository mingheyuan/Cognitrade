from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from research_system.runtime.core.id_utils import generate_ulid
from research_system.runtime.storage.jsonl import read_json, write_json
from research_system.schemas.subagents import RoleContract
from research_system.schemas.turn_runtime import ChildTurnSnapshot


class RoleExecutorAdapter(Protocol):
    def execute(self, *, role: RoleContract, context: Any, input_artifacts: list[Any]) -> Any:
        ...


class RoleRegistry:
    """Small contract registry; execution remains owned by Graph/ChildTurn."""

    def __init__(self) -> None:
        self._roles: dict[str, RoleContract] = {}

    def register(self, role: RoleContract) -> RoleContract:
        if role.role_id in self._roles:
            raise ValueError(f"role already registered: {role.role_id}")
        self._roles[role.role_id] = role
        return role

    def replace(self, role: RoleContract) -> RoleContract:
        self._roles[role.role_id] = role
        return role

    def get(self, role_id: str) -> RoleContract:
        try:
            return self._roles[role_id]
        except KeyError as exc:
            raise KeyError(f"unknown role contract: {role_id}") from exc

    def list(self) -> list[RoleContract]:
        return list(self._roles.values())

    def remove(self, role_id: str) -> None:
        self._roles.pop(role_id, None)

    def validate_tool(self, role_id: str, tool_name: str) -> None:
        role = self.get(role_id)
        if role.allowed_tools and tool_name not in role.allowed_tools:
            raise PermissionError(f"tool is not allowed by role contract: {tool_name}")
        if tool_name in role.denied_tools:
            raise PermissionError(f"tool is denied by role contract: {tool_name}")


class ChildTurnRuntime:
    """Persist child Turn projections under the parent's Run."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.path = self.root / "child_turns.json"

    def list(self) -> list[ChildTurnSnapshot]:
        payload = read_json(self.path)
        if not isinstance(payload, list):
            return []
        return [ChildTurnSnapshot.model_validate(item) for item in payload]

    def spawn(
        self,
        *,
        parent_turn_id: str,
        parent_run_id: str,
        role_id: str,
        objective: str | None = None,
        parent_node_id: str | None = None,
        relation: str = "child",
    ) -> ChildTurnSnapshot:
        existing = self.list()
        child = ChildTurnSnapshot(
            child_turn_id=generate_ulid(),
            child_thread_id=generate_ulid(),
            parent_turn_id=parent_turn_id,
            parent_run_id=parent_run_id,
            parent_node_id=parent_node_id,
            role_id=role_id,
            relation=relation,  # type: ignore[arg-type]
            objective=objective,
        )
        self._persist([*existing, child])
        return child

    def update(
        self,
        child_turn_id: str,
        *,
        status: str | None = None,
        artifact_ids: list[str] | None = None,
        continuation: dict[str, Any] | None = None,
        sequence: int | None = None,
        last_event_id: str | None = None,
    ) -> ChildTurnSnapshot:
        rows = self.list()
        for index, row in enumerate(rows):
            if row.child_turn_id != child_turn_id:
                continue
            update: dict[str, Any] = {"updated_at": datetime.now(timezone.utc)}
            if status is not None:
                update["status"] = status
            if artifact_ids is not None:
                update["artifact_ids"] = list(artifact_ids)
            if continuation is not None:
                update["continuation"] = continuation
            if sequence is not None:
                update["sequence"] = max(row.sequence, int(sequence))
            if last_event_id is not None:
                update["last_event_id"] = last_event_id
            rows[index] = row.model_copy(update=update)
            self._persist(rows)
            return rows[index]
        raise KeyError(f"unknown child Turn: {child_turn_id}")

    def project_graph(self, *, parent_turn_id: str, parent_run_id: str, snapshot: Any) -> list[ChildTurnSnapshot]:
        """Project Graph subagent nodes as stable child Turns idempotently."""
        rows = self.list()
        by_node = {row.parent_node_id: row for row in rows if row.parent_node_id}
        status_map = {
            "pending": "ready",
            "waiting": "paused",
            "running": "running",
            "completed": "completed",
            "skipped": "completed_partial",
            "degraded": "completed_partial",
            "failed": "failed",
            "cancelled": "cancelled",
            "timed_out": "timed_out",
            "gated": "gated",
        }
        for node in snapshot.graph.nodes:
            if node.node_kind != "subagent":
                continue
            state = snapshot.node_states.get(node.node_id)
            status = status_map.get(str(getattr(state, "status", node.status)), "ready")
            child = by_node.get(node.node_id)
            if child is None:
                child = ChildTurnSnapshot(
                    child_turn_id=f"{parent_turn_id}:{node.node_id}",
                    child_thread_id=f"{parent_run_id}:{node.node_id}",
                    parent_turn_id=parent_turn_id,
                    parent_run_id=parent_run_id,
                    parent_node_id=node.node_id,
                    role_id=str(node.executor or node.capability or node.node_id),
                    objective=node.objective,
                    status=status,  # type: ignore[arg-type]
                    artifact_ids=list(getattr(state, "output_artifact_ids", []) or []),
                )
                rows.append(child)
                by_node[node.node_id] = child
            else:
                index = rows.index(child)
                rows[index] = child.model_copy(
                    update={
                        "status": status,
                        "artifact_ids": list(getattr(state, "output_artifact_ids", []) or []),
                        "updated_at": datetime.now(timezone.utc),
                    }
                )
        self._persist(rows)
        return rows

    def _persist(self, rows: list[ChildTurnSnapshot]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        write_json(self.path, [row.model_dump(mode="json") for row in rows])


__all__ = ["ChildTurnRuntime", "RoleExecutorAdapter", "RoleRegistry"]
