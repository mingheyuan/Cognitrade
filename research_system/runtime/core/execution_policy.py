from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from threading import Lock


class RuntimeDegradeReason(str, Enum):
    LLM_UNAVAILABLE = "llm_unavailable"
    TURN_LLM_BUDGET_EXHAUSTED = "turn_llm_budget_exhausted"
    TURN_SUBAGENT_BUDGET_EXHAUSTED = "turn_subagent_budget_exhausted"
    TURN_CHILD_THREAD_BUDGET_EXHAUSTED = "turn_child_thread_budget_exhausted"
    LLM_REQUEST_TIMEOUT = "llm_request_timeout"
    LLM_EMPTY_RESPONSE = "llm_empty_response"
    MEMORY_SERVICE_UNAVAILABLE = "memory_service_unavailable"
    QDRANT_UNAVAILABLE = "qdrant_unavailable"
    RUNTIME_FALLBACK = "runtime_fallback"


def normalize_degrade_reason(reason: object) -> str | None:
    if isinstance(reason, RuntimeDegradeReason):
        return reason.value
    token = str(reason or "").strip()
    return token or None


def split_degrade_reasons(*reasons: object) -> list[str]:
    parts: list[str] = []
    for reason in reasons:
        normalized = normalize_degrade_reason(reason)
        if not normalized:
            continue
        for token in normalized.split(";"):
            value = token.strip()
            if value and value not in parts:
                parts.append(value)
    return parts


def combine_degrade_reasons(*reasons: object) -> str | None:
    parts = split_degrade_reasons(*reasons)
    return ";".join(parts) or None


def derive_llm_error_reason(error: Exception) -> str:
    error_text = str(error)
    if RuntimeDegradeReason.LLM_REQUEST_TIMEOUT.value in error_text:
        return RuntimeDegradeReason.LLM_REQUEST_TIMEOUT.value
    return f"llm_error:{error.__class__.__name__}"


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


__all__ = [
    "RuntimeDegradation",
    "RuntimeDegradeReason",
    "RuntimeExecutionPolicy",
    "combine_degrade_reasons",
    "derive_llm_error_reason",
    "normalize_degrade_reason",
    "split_degrade_reasons",
]
