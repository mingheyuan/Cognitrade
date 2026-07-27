from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from research_system.runtime.core.id_utils import generate_ulid
from research_system.runtime.event_store import append_canonical_event
from research_system.runtime.storage.jsonl import read_json, write_json
from research_system.schemas.events import EventEnvelope
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

    def storage_root(self, child_turn_id: str) -> Path:
        """Return a portable filesystem root for a logical ChildTurn ID."""
        return self.root / "child_turns" / str(child_turn_id).replace(":", "__")

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
        role_contract: dict[str, Any] | None = None,
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
            role_contract=dict(role_contract or {}),
        )
        self._persist([*existing, child])
        return child

    def update(
        self,
        child_turn_id: str,
        *,
        status: str | None = None,
        artifact_ids: list[str] | None = None,
        usage: dict[str, Any] | None = None,
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
            if usage is not None:
                update["usage"] = dict(usage)
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
            role_id = str(node.executor or node.capability or node.node_id)
            metadata = dict(node.metadata or {})
            try:
                max_model_turns = max(int(metadata.get("max_model_turns") or 1), 1)
            except (TypeError, ValueError):
                max_model_turns = 1
            try:
                timeout_seconds = max(int(float(metadata.get("timeout_seconds") or 30)), 1)
            except (TypeError, ValueError):
                timeout_seconds = 30
            role_contract = {
                "role_id": role_id,
                "role_version": f"graph:{snapshot.graph.revision}",
                "display_name": role_id,
                "objective": node.objective or role_id,
                "allowed_tools": list(node.tools),
                "denied_tools": list(metadata.get("denied_tools") or []),
                "allowed_permissions": list(node.permissions),
                "max_model_turns": max_model_turns,
                "timeout_seconds": timeout_seconds,
                "requires_approval": bool(
                    metadata.get("requires_approval")
                    or node.side_effect_level in snapshot.graph.governance.high_risk_side_effect_levels
                ),
                "source": "pack",
            }
            state = snapshot.node_states.get(node.node_id)
            status = status_map.get(str(getattr(state, "status", node.status)), "ready")
            usage = getattr(state, "usage", None)
            usage_payload = (
                usage.model_dump(mode="json")
                if usage is not None and callable(getattr(usage, "model_dump", None))
                else {}
            )
            existing = by_node.get(node.node_id)
            if existing is None:
                child = ChildTurnSnapshot(
                    child_turn_id=f"{parent_turn_id}:{node.node_id}",
                    child_thread_id=f"{parent_run_id}:{node.node_id}",
                    parent_turn_id=parent_turn_id,
                    parent_run_id=parent_run_id,
                    parent_node_id=node.node_id,
                    role_id=role_id,
                    objective=node.objective,
                    status=status,  # type: ignore[arg-type]
                    role_contract=role_contract,
                    artifact_ids=list(getattr(state, "output_artifact_ids", []) or []),
                    usage=usage_payload,
                )
                event_type = "child_turn_projected"
                changed = True
            else:
                child = existing.model_copy(
                    update={
                        "status": status,
                        "role_contract": role_contract,
                        "artifact_ids": list(getattr(state, "output_artifact_ids", []) or []),
                        "usage": usage_payload,
                        "updated_at": datetime.now(timezone.utc),
                    }
                )
                event_type = "child_turn_status_changed"
                changed = (
                    child.status != existing.status
                    or child.artifact_ids != existing.artifact_ids
                    or child.usage != usage_payload
                )
            if changed:
                child = self._append_projection_event(
                    child,
                    snapshot=snapshot,
                    node_id=node.node_id,
                    event_type=event_type,
                )
            self._persist_child_checkpoint(child, snapshot=snapshot)
            if existing is None:
                rows.append(child)
            else:
                rows[rows.index(existing)] = child
            by_node[node.node_id] = child
        self._persist(rows)
        return rows

    def _persist_child_checkpoint(self, child: ChildTurnSnapshot, *, snapshot: Any) -> None:
        child_root = self.storage_root(child.child_turn_id)
        child_root.mkdir(parents=True, exist_ok=True)
        write_json(child_root / "child_turn.json", child.model_dump(mode="json"))
        write_json(
            child_root / "task_state.json",
            {
                "task_type": "regular",
                "execution_mode": "graph",
                "transcript": [],
                "graph_state_ref": str(self.root / "execution_snapshot.json"),
                "continuation": child.continuation,
                "usage": dict(child.usage),
            },
        )

    def _append_projection_event(
        self,
        child: ChildTurnSnapshot,
        *,
        snapshot: Any,
        node_id: str,
        event_type: str,
    ) -> ChildTurnSnapshot:
        event = EventEnvelope(
            event_type=event_type,
            run_id=child.parent_run_id,
            thread_id=child.child_thread_id,
            request_id=str(snapshot.request_id),
            turn_id=child.child_turn_id,
            parent_turn_id=child.parent_turn_id,
            task_type="regular",
            execution_mode="graph",
            sequence=0,
            actor="graph_child",
            node_id=node_id,
            payload={
                "status": child.status,
                "artifact_ids": list(child.artifact_ids),
                "usage": dict(child.usage),
            },
        ).model_dump(mode="json")
        event = append_canonical_event(
            self.storage_root(child.child_turn_id) / "execution_events.jsonl",
            event,
        )
        return child.model_copy(
            update={
                "sequence": event["sequence"],
                "last_event_id": event["event_id"],
                "updated_at": datetime.now(timezone.utc),
            }
        )

    def _persist(self, rows: list[ChildTurnSnapshot]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        write_json(self.path, [row.model_dump(mode="json") for row in rows])


__all__ = ["ChildTurnRuntime", "RoleExecutorAdapter", "RoleRegistry"]
