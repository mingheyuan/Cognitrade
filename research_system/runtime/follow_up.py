from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from research_system.runtime.core.id_utils import generate_ulid
from research_system.runtime.storage.jsonl import read_json, write_json
from research_system.schemas.turn_runtime import (
    FollowUpConfig,
    FollowupTaskRequest,
    FollowupTaskResult,
    PendingInputItem,
    TaskSnapshot,
)


class FollowUpRuntime:
    """Mailbox and continuation state for one root Turn."""

    def __init__(
        self,
        *,
        root: Path,
        run_id: str,
        turn_id: str,
        config: FollowUpConfig | None = None,
        emitter: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.root = root
        self.run_id = run_id
        self.turn_id = turn_id
        self.config = config or FollowUpConfig()
        self._emitter = emitter
        self.path = root / "task_state.json"
        self.legacy_path = root / "task.json"
        self.state = self._load()

    def bind_execution(self, *, task_type: str, execution_mode: str) -> TaskSnapshot:
        self.state = self.state.model_copy(
            update={"task_type": task_type, "execution_mode": execution_mode}
        )
        return self.persist()

    def _load(self) -> TaskSnapshot:
        payload = read_json(self.path) or read_json(self.legacy_path)
        if isinstance(payload, dict):
            return TaskSnapshot.model_validate(payload)
        return TaskSnapshot()

    def persist(self) -> TaskSnapshot:
        self.state = self.state.model_copy(update={"updated_at": _now()})
        write_json(self.path, self.state.model_dump(mode="json"))
        write_json(self.legacy_path, self.state.model_dump(mode="json"))
        return self.state

    @property
    def follow_up_count(self) -> int:
        return self.state.follow_up_count

    @property
    def stop_hook_active(self) -> bool:
        return self.state.stop_hook_active

    def set_stop_hook_state(
        self,
        *,
        active: bool,
        iteration: int,
        continuation: dict[str, Any] | None = None,
    ) -> TaskSnapshot:
        self.state = self.state.model_copy(
            update={
                "stop_hook_active": active,
                "hook_iteration": iteration,
                "continuation": continuation,
            }
        )
        return self.persist()

    def record_model_follow_up(self) -> TaskSnapshot:
        if not self.config.enabled or not self.config.allow_model_follow_up:
            return self.state
        if self.state.follow_up_count >= self.config.max_follow_up_count:
            raise RuntimeError("follow-up limit exceeded")
        self.state = self.state.model_copy(
            update={"follow_up_count": self.state.follow_up_count + 1}
        )
        self._emit(
            "followup_requested",
            source="model",
            follow_up_count=self.state.follow_up_count,
            operation_id=generate_ulid(),
        )
        return self.persist()

    def has_current_inputs(self) -> bool:
        return bool(self.current_inputs())

    def _increment_follow_up(self) -> None:
        if self.state.follow_up_count >= self.config.max_follow_up_count:
            raise RuntimeError("follow-up limit exceeded")
        self.state = self.state.model_copy(
            update={"follow_up_count": self.state.follow_up_count + 1}
        )

    def enqueue(
        self,
        *,
        source: str,
        kind: str,
        text: str,
        operation_id: str | None = None,
        input_id: str | None = None,
        trigger_turn: bool = False,
        delivery_phase: str = "current_turn",
        target_turn_id: str | None = None,
    ) -> PendingInputItem:
        if not self.config.enabled or not self.config.allow_pending_input:
            raise RuntimeError("pending input is disabled for this Turn")
        normalized_operation = str(operation_id or generate_ulid()).strip()
        existing = next(
            (
                item
                for item in self.state.pending_inputs
                if item.operation_id == normalized_operation
                or (input_id and item.input_id == input_id)
            ),
            None,
        )
        if existing is not None:
            self._emit(
                "followup_duplicate",
                input_id=existing.input_id,
                operation_id=existing.operation_id,
                source=existing.source,
            )
            return existing
        item = PendingInputItem(
            input_id=input_id or generate_ulid(),
            source=source,  # type: ignore[arg-type]
            kind=kind,  # type: ignore[arg-type]
            text=str(text),
            target_turn_id=target_turn_id or self.turn_id,
            trigger_turn=trigger_turn,
            operation_id=normalized_operation,
            delivery_phase=("next_turn" if delivery_phase == "next_turn" else "current_turn"),
        )
        self.state = self.state.model_copy(
            update={"pending_inputs": [*self.state.pending_inputs, item]}
        )
        self.persist()
        self._emit(
            "followup_deferred" if item.delivery_phase == "next_turn" else "followup_queued",
            input_id=item.input_id,
            operation_id=item.operation_id,
            source=item.source,
            delivery_phase=item.delivery_phase,
        )
        return item

    def followup_task(
        self,
        request: FollowupTaskRequest,
        *,
        known_child_turn_ids: set[str] | None = None,
    ) -> FollowupTaskResult:
        """Queue work for an existing non-root child without mutating its state."""
        if not self.config.enabled or not self.config.allow_child_followup_task:
            return FollowupTaskResult(
                target_child_turn_id=request.target_child_turn_id,
                accepted=False,
                delivery="rejected",
                reason="child follow-up task is disabled for this Turn",
            )
        if request.target_child_turn_id == self.turn_id:
            return FollowupTaskResult(
                target_child_turn_id=request.target_child_turn_id,
                accepted=False,
                delivery="rejected",
                reason="followup_task cannot target the root Turn",
            )
        if known_child_turn_ids is not None and request.target_child_turn_id not in known_child_turn_ids:
            return FollowupTaskResult(
                target_child_turn_id=request.target_child_turn_id,
                accepted=False,
                delivery="rejected",
                reason="target child Turn is not known to the parent",
            )
        existing = next(
            (
                item
                for item in self.state.pending_inputs
                if item.operation_id == request.operation_id
            ),
            None,
        )
        if existing is not None:
            self._emit(
                "followup_duplicate",
                input_id=existing.input_id,
                operation_id=existing.operation_id,
                source=existing.source,
                target_child_turn_id=request.target_child_turn_id,
            )
            return FollowupTaskResult(
                target_child_turn_id=request.target_child_turn_id,
                accepted=True,
                delivery=("triggered_new_turn" if existing.trigger_turn else "queued"),
                target_turn_id=existing.target_turn_id,
                duplicate=True,
            )
        item = self.enqueue(
            source="child",
            kind="inter_agent_communication",
            text=request.message,
            operation_id=request.operation_id,
            trigger_turn=request.trigger_turn,
            target_turn_id=request.target_child_turn_id,
        )
        delivery = "triggered_new_turn" if request.trigger_turn else "queued"
        self._emit(
            "followup_task_queued",
            target_child_turn_id=request.target_child_turn_id,
            target_agent_id=request.target_agent_id,
            operation_id=request.operation_id,
            delivery=delivery,
        )
        return FollowupTaskResult(
            target_child_turn_id=request.target_child_turn_id,
            accepted=True,
            delivery=delivery,
            target_turn_id=request.target_child_turn_id,
            duplicate=False,
        )

    def current_inputs(self) -> list[PendingInputItem]:
        if not self.config.enabled or not self.config.allow_pending_input:
            return []
        return [
            item
            for item in self.state.pending_inputs
            if item.delivery_phase == "current_turn"
            and (item.target_turn_id in {None, self.turn_id})
        ]

    def retarget_operation(self, operation_id: str, target_turn_id: str) -> PendingInputItem:
        """Move one queued input to a newly-created ChildTurn continuation."""
        for index, item in enumerate(self.state.pending_inputs):
            if item.operation_id != operation_id:
                continue
            updated = item.model_copy(update={"target_turn_id": target_turn_id})
            self.state = self.state.model_copy(
                update={
                    "pending_inputs": [
                        *self.state.pending_inputs[:index],
                        updated,
                        *self.state.pending_inputs[index + 1 :],
                    ]
                }
            )
            self.persist()
            return updated
        raise KeyError(f"follow-up operation not found: {operation_id}")

    def deliver_current(self) -> list[PendingInputItem]:
        items = self.current_inputs()
        if not items:
            return []
        ids = {item.input_id for item in items}
        self._increment_follow_up()
        self.state = self.state.model_copy(
            update={
                "pending_inputs": [
                    item for item in self.state.pending_inputs if item.input_id not in ids
                ],
            }
        )
        self.persist()
        for item in items:
            self._emit(
                "followup_delivered",
                input_id=item.input_id,
                operation_id=item.operation_id,
                source=item.source,
                delivery_phase="current_turn",
            )
        return items

    def deliver_for_turn(self, target_turn_id: str) -> list[PendingInputItem]:
        """Deliver only mailbox items addressed to a projected ChildTurn."""
        target = str(target_turn_id or "").strip()
        if not target:
            return []
        items = [
            item
            for item in self.state.pending_inputs
            if item.delivery_phase == "current_turn" and item.target_turn_id == target
        ]
        if not items:
            return []
        ids = {item.input_id for item in items}
        self._increment_follow_up()
        self.state = self.state.model_copy(
            update={
                "pending_inputs": [
                    item for item in self.state.pending_inputs if item.input_id not in ids
                ],
            }
        )
        self.persist()
        for item in items:
            self._emit(
                "followup_delivered",
                input_id=item.input_id,
                operation_id=item.operation_id,
                source=item.source,
                delivery_phase="current_turn",
                target_turn_id=target,
            )
        return items

    def update_transcript(self, transcript: list[dict[str, Any]], *, message: str | None = None) -> TaskSnapshot:
        self.state = self.state.model_copy(
            update={
                "transcript": list(transcript),
                "last_assistant_message": message or self.state.last_assistant_message,
            }
        )
        return self.persist()

    def _emit(self, event_type: str, **payload: Any) -> None:
        if self._emitter is not None:
            self._emitter(event_type, {"run_id": self.run_id, "turn_id": self.turn_id, **payload})


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


__all__ = ["FollowUpRuntime"]
