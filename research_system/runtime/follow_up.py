from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from threading import get_ident
from typing import Any, Callable, Iterator

from research_system.runtime.core.id_utils import generate_ulid
from research_system.runtime.storage.jsonl import read_json, write_json
from research_system.schemas.turn_runtime import (
    FollowUpConfig,
    FollowupTaskRequest,
    FollowupTaskResult,
    PendingInputItem,
    TaskSnapshot,
)


@contextmanager
def mailbox_lease(
    root: Path,
    run_id: str,
    *,
    lease_seconds: int = 30,
) -> Iterator[None]:
    """Serialize mailbox mutations across workers without a process lock."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    lease_path = root / "turn_mailbox_lease.json"
    now = datetime.now(timezone.utc)
    existing = read_json(lease_path)
    if lease_path.exists():
        if not isinstance(existing, dict):
            raise ValueError(f"Turn mailbox lease is unreadable: {run_id}")
        try:
            expires_at = datetime.fromisoformat(str(existing.get("expires_at") or ""))
        except ValueError:
            expires_at = None
        if expires_at is not None:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            else:
                expires_at = expires_at.astimezone(timezone.utc)
        if expires_at is not None and expires_at > now:
            raise ValueError(f"Turn mailbox is being updated by another worker: {run_id}")
        lease_path.unlink(missing_ok=True)

    owner = f"{os.getpid()}:{get_ident()}:{generate_ulid()}"
    expires_at = now + timedelta(seconds=max(1, int(lease_seconds)))
    payload = {
        "owner": owner,
        "run_id": run_id,
        "claimed_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
    }
    try:
        descriptor = os.open(lease_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ValueError(f"Turn mailbox is being updated by another worker: {run_id}") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, indent=2)
            handle.write("\n")
    except Exception:
        lease_path.unlink(missing_ok=True)
        raise
    try:
        yield
    finally:
        persisted = read_json(lease_path)
        if isinstance(persisted, dict) and persisted.get("owner") == owner:
            lease_path.unlink(missing_ok=True)


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
        self._baseline = self.state.model_copy(deep=True)
        if self.state.active_turn_id != self.turn_id:
            self.promote_next_turn(self.turn_id)
            with mailbox_lease(self.root, self.run_id):
                self._refresh_unlocked()
                self.state = self.state.model_copy(update={"active_turn_id": self.turn_id})
                self._persist_unlocked()

    def bind_execution(self, *, task_type: str, execution_mode: str) -> TaskSnapshot:
        with mailbox_lease(self.root, self.run_id):
            self._refresh_unlocked()
            self.state = self.state.model_copy(
                update={"task_type": task_type, "execution_mode": execution_mode}
            )
            return self._persist_unlocked()

    def _load(self) -> TaskSnapshot:
        payload = read_json(self.path) or read_json(self.legacy_path)
        if isinstance(payload, dict):
            return TaskSnapshot.model_validate(payload)
        return TaskSnapshot()

    def persist(self) -> TaskSnapshot:
        with mailbox_lease(self.root, self.run_id):
            latest = self._load()
            dirty = {
                field_name: getattr(self.state, field_name)
                for field_name in TaskSnapshot.model_fields
                if getattr(self.state, field_name) != getattr(self._baseline, field_name)
            }
            self.state = latest.model_copy(update=dirty)
            return self._persist_unlocked()

    def _persist_unlocked(self) -> TaskSnapshot:
        self.state = self.state.model_copy(update={"updated_at": _now()})
        write_json(self.path, self.state.model_dump(mode="json"))
        write_json(self.legacy_path, self.state.model_dump(mode="json"))
        self._baseline = self.state.model_copy(deep=True)
        return self.state

    def _refresh_unlocked(self) -> None:
        payload = read_json(self.path) or read_json(self.legacy_path)
        if isinstance(payload, dict):
            latest = TaskSnapshot.model_validate(payload)
            self.state = latest
            self._baseline = latest.model_copy(deep=True)

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
        with mailbox_lease(self.root, self.run_id):
            self._refresh_unlocked()
            self.state = self.state.model_copy(
                update={
                    "stop_hook_active": active,
                    "hook_iteration": iteration,
                    "continuation": continuation,
                }
            )
            return self._persist_unlocked()

    def record_model_follow_up(self) -> TaskSnapshot:
        with mailbox_lease(self.root, self.run_id):
            self._refresh_unlocked()
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
            return self._persist_unlocked()

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
        with mailbox_lease(self.root, self.run_id):
            self._refresh_unlocked()
            return self._enqueue_unlocked(
                source=source,
                kind=kind,
                text=text,
                operation_id=operation_id,
                input_id=input_id,
                trigger_turn=trigger_turn,
                delivery_phase=delivery_phase,
                target_turn_id=target_turn_id,
            )

    def _enqueue_unlocked(
        self,
        *,
        source: str,
        kind: str,
        text: str,
        operation_id: str | None,
        input_id: str | None,
        trigger_turn: bool,
        delivery_phase: str,
        target_turn_id: str | None,
    ) -> PendingInputItem:
        if not self.config.enabled or not self.config.allow_pending_input:
            raise RuntimeError("pending input is disabled for this Turn")
        normalized_operation = str(operation_id or generate_ulid()).strip()
        receipt = self.state.operation_receipts.get(normalized_operation)
        if receipt is not None:
            if receipt.get("kind") != "pending_input" or not isinstance(
                receipt.get("item"), dict
            ):
                raise ValueError(
                    f"operation_id already belongs to another follow-up operation: "
                    f"{normalized_operation}"
                )
            item = PendingInputItem.model_validate(receipt["item"])
            self._emit(
                "followup_duplicate",
                input_id=item.input_id,
                operation_id=item.operation_id,
                source=item.source,
                delivery_phase=receipt.get("delivery_phase") or item.delivery_phase,
                receipt_status=receipt.get("status") or "delivered",
            )
            return item
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
        self._persist_unlocked()
        self._emit(
            "followup_deferred" if item.delivery_phase == "next_turn" else "followup_queued",
            input_id=item.input_id,
            operation_id=item.operation_id,
            source=item.source,
            delivery_phase=item.delivery_phase,
        )
        return item

    def record_operation_result(
        self,
        operation_id: str,
        result: FollowupTaskResult,
    ) -> TaskSnapshot:
        """Persist the final result of a follow-up operation.

        The receipt is deliberately separate from ``pending_inputs``.  Mailbox
        delivery removes an input, but retrying the same operation must still
        return the original result rather than enqueueing new work.
        """
        normalized_operation = str(operation_id or "").strip()
        if not normalized_operation:
            raise ValueError("operation_id is required for a follow-up receipt")
        with mailbox_lease(self.root, self.run_id):
            self._refresh_unlocked()
            existing = self.state.operation_receipts.get(normalized_operation)
            if existing is not None and existing.get("kind") != "followup_task":
                raise ValueError(
                    f"operation_id already belongs to another follow-up operation: "
                    f"{normalized_operation}"
                )
            receipts = dict(self.state.operation_receipts)
            receipts[normalized_operation] = {
                **dict(existing or {}),
                "kind": "followup_task",
                "status": "completed",
                "result": result.model_dump(mode="json"),
                "updated_at": _now().isoformat(),
            }
            self.state = self.state.model_copy(update={"operation_receipts": receipts})
            return self._persist_unlocked()

    def followup_task(
        self,
        request: FollowupTaskRequest,
        *,
        known_child_turn_ids: set[str] | None = None,
    ) -> FollowupTaskResult:
        """Queue work for an existing non-root child without mutating its state."""
        with mailbox_lease(self.root, self.run_id):
            self._refresh_unlocked()
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
            receipt = self.state.operation_receipts.get(request.operation_id)
            if receipt is not None:
                if receipt.get("kind") != "followup_task" or not isinstance(
                    receipt.get("result"), dict
                ):
                    return FollowupTaskResult(
                        target_child_turn_id=request.target_child_turn_id,
                        accepted=False,
                        delivery="rejected",
                        reason="operation_id belongs to another follow-up operation",
                    )
                stored = FollowupTaskResult.model_validate(receipt["result"])
                duplicate = stored.model_copy(update={"duplicate": True})
                self._emit(
                    "followup_duplicate",
                    operation_id=request.operation_id,
                    target_child_turn_id=duplicate.target_child_turn_id,
                    target_turn_id=duplicate.target_turn_id,
                    source="child",
                    receipt_status=receipt.get("status") or "completed",
                )
                return duplicate
            item = self._enqueue_unlocked(
                source="child",
                kind="inter_agent_communication",
                text=request.message,
                operation_id=request.operation_id,
                input_id=None,
                trigger_turn=request.trigger_turn,
                delivery_phase="current_turn",
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
            result = FollowupTaskResult(
                target_child_turn_id=request.target_child_turn_id,
                accepted=True,
                delivery=delivery,
                target_turn_id=item.target_turn_id,
                duplicate=False,
            )
            self._record_operation_receipt_unlocked(
                request.operation_id,
                kind="followup_task",
                result=result,
            )
            return result

    def current_inputs(self) -> list[PendingInputItem]:
        if not self.config.enabled or not self.config.allow_pending_input:
            return []
        return [
            item
            for item in self.state.pending_inputs
            if item.delivery_phase == "current_turn"
            and (item.target_turn_id in {None, self.turn_id})
        ]

    def promote_next_turn(self, target_turn_id: str | None = None) -> list[PendingInputItem]:
        """Promote queue-only inputs when a new logical Turn starts."""
        with mailbox_lease(self.root, self.run_id):
            self._refresh_unlocked()
            target = str(target_turn_id or self.turn_id).strip() or self.turn_id
            promoted: list[PendingInputItem] = []
            updated_items: list[PendingInputItem] = []
            for item in self.state.pending_inputs:
                if item.delivery_phase != "next_turn":
                    updated_items.append(item)
                    continue
                updated = item.model_copy(
                    update={
                        "delivery_phase": "current_turn",
                        "target_turn_id": target,
                    }
                )
                promoted.append(updated)
                updated_items.append(updated)
            if not promoted:
                return []
            self.state = self.state.model_copy(update={"pending_inputs": updated_items})
            self._persist_unlocked()
            for item in promoted:
                self._emit(
                    "followup_promoted",
                    input_id=item.input_id,
                    operation_id=item.operation_id,
                    source=item.source,
                    delivery_phase="current_turn",
                    target_turn_id=target,
                )
            return promoted

    def retarget_operation(self, operation_id: str, target_turn_id: str) -> PendingInputItem:
        """Move one queued input to a newly-created ChildTurn continuation."""
        with mailbox_lease(self.root, self.run_id):
            self._refresh_unlocked()
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
                self._persist_unlocked()
                return updated
            raise KeyError(f"follow-up operation not found: {operation_id}")

    def deliver_current(self) -> list[PendingInputItem]:
        with mailbox_lease(self.root, self.run_id):
            self._refresh_unlocked()
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
                    "operation_receipts": {
                        **dict(self.state.operation_receipts),
                        **{
                            item.operation_id: self._delivery_receipt_unlocked(item)
                            for item in items
                        },
                    },
                }
            )
            self._persist_unlocked()
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
        with mailbox_lease(self.root, self.run_id):
            self._refresh_unlocked()
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
                    "operation_receipts": {
                        **dict(self.state.operation_receipts),
                        **{
                            item.operation_id: self._delivery_receipt_unlocked(item)
                            for item in items
                        },
                    },
                }
            )
            self._persist_unlocked()
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
        with mailbox_lease(self.root, self.run_id):
            self._refresh_unlocked()
            self.state = self.state.model_copy(
                update={
                    "transcript": list(transcript),
                    "last_assistant_message": message or self.state.last_assistant_message,
                }
            )
            return self._persist_unlocked()

    def _emit(self, event_type: str, **payload: Any) -> None:
        if self._emitter is not None:
            self._emitter(event_type, {"run_id": self.run_id, "turn_id": self.turn_id, **payload})

    def _record_operation_receipt_unlocked(
        self,
        operation_id: str,
        *,
        kind: str,
        result: FollowupTaskResult,
    ) -> None:
        receipts = dict(self.state.operation_receipts)
        receipts[str(operation_id)] = {
            "kind": kind,
            "status": "queued",
            "result": result.model_dump(mode="json"),
            "updated_at": _now().isoformat(),
        }
        self.state = self.state.model_copy(update={"operation_receipts": receipts})
        self._persist_unlocked()

    def _delivery_receipt_unlocked(self, item: PendingInputItem) -> dict[str, Any]:
        """Mark delivery without erasing a higher-level follow-up result."""
        existing = self.state.operation_receipts.get(item.operation_id)
        if existing is not None and existing.get("kind") == "followup_task":
            return {
                **dict(existing),
                "delivery_status": "delivered",
                "delivered_input_id": item.input_id,
                "updated_at": _now().isoformat(),
            }
        return {
            "kind": "pending_input",
            "status": "delivered",
            "item": item.model_dump(mode="json"),
            "delivery_phase": item.delivery_phase,
            "updated_at": _now().isoformat(),
        }


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


__all__ = ["FollowUpRuntime", "mailbox_lease"]
