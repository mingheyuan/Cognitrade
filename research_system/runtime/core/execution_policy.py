from __future__ import annotations

from dataclasses import dataclass
from threading import Lock


@dataclass
class RuntimeDegradation:
    component: str
    reason: str
    recoverable: bool = True


class RuntimeExecutionPolicy:
    def __init__(
        self,
        *,
        max_llm_calls_per_turn: int = 1,
        max_subagent_calls_per_turn: int = 32,
        max_child_threads_per_turn: int = 8,
    ) -> None:
        self.max_llm_calls_per_turn = max(1, int(max_llm_calls_per_turn))
        self.max_subagent_calls_per_turn = max(1, int(max_subagent_calls_per_turn))
        self.max_child_threads_per_turn = max(1, int(max_child_threads_per_turn))
        self._lock = Lock()
        self._llm_calls_by_turn: dict[str, int] = {}
        self._subagent_calls_by_turn: dict[str, int] = {}
        self._child_threads_by_turn: dict[str, int] = {}

    def can_use_llm(self, *, turn_id: str) -> bool:
        with self._lock:
            return self._llm_calls_by_turn.get(turn_id, 0) < self.max_llm_calls_per_turn

    def reserve_llm_call(self, *, turn_id: str) -> bool:
        with self._lock:
            current = self._llm_calls_by_turn.get(turn_id, 0)
            if current >= self.max_llm_calls_per_turn:
                return False
            self._llm_calls_by_turn[turn_id] = current + 1
            return True

    def can_use_subagent(self, *, turn_id: str) -> bool:
        with self._lock:
            return self._subagent_calls_by_turn.get(turn_id, 0) < self.max_subagent_calls_per_turn

    def reserve_subagent_call(self, *, turn_id: str) -> bool:
        with self._lock:
            current = self._subagent_calls_by_turn.get(turn_id, 0)
            if current >= self.max_subagent_calls_per_turn:
                return False
            self._subagent_calls_by_turn[turn_id] = current + 1
            return True

    def can_spawn_child_thread(self, *, turn_id: str) -> bool:
        with self._lock:
            return self._child_threads_by_turn.get(turn_id, 0) < self.max_child_threads_per_turn

    def reserve_child_thread(self, *, turn_id: str) -> bool:
        with self._lock:
            current = self._child_threads_by_turn.get(turn_id, 0)
            if current >= self.max_child_threads_per_turn:
                return False
            self._child_threads_by_turn[turn_id] = current + 1
            return True

    def release_turn(self, *, turn_id: str) -> None:
        with self._lock:
            self._llm_calls_by_turn.pop(turn_id, None)
            self._subagent_calls_by_turn.pop(turn_id, None)
            self._child_threads_by_turn.pop(turn_id, None)


__all__ = ["RuntimeDegradation", "RuntimeExecutionPolicy"]
