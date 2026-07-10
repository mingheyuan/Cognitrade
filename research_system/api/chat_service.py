from __future__ import annotations

from _thread import RLock as RLockType
from threading import RLock
from typing import Any

from research_system.api.errors import ApiError
from research_system.cli.memory.chat_memory import (
    archive_session_thread,
    compact_session_thread,
    fork_session_thread,
    hydrate_session_from_persistent_memory,
    list_chat_threads,
    read_session_thread,
    rename_session_thread,
    start_new_session_thread,
    thread_preview_payload,
    unarchive_session_thread,
)
from research_system.cli.memory.chat_thread_store import ChatThreadStore
from research_system.cli.memory.chat_memory import build_thread_context
from research_system.cli.state.chat_state_models import ChatSessionState
from research_system.runtime.platform.settings import Settings
from research_system.schemas.protocol import RequestEnvelope, ResponseEnvelope


_CHAT_STORE_LOCK = RLock()


class ChatService:
    """Transport-neutral facade over the CLI chat thread persistence helpers."""

    def __init__(self, settings: Settings, *, lock: RLockType | None = None) -> None:
        self.settings = settings
        # ChatThreadStore updates index.json with a read-modify-write cycle. Keep a
        # process-wide lock even when the API dependency creates multiple services.
        self._lock = lock or _CHAT_STORE_LOCK

    def create_thread(self) -> dict[str, str]:
        with self._lock:
            state = ChatSessionState()
            thread_id = start_new_session_thread(settings=self.settings, state=state)
            return {"thread_id": thread_id}

    def list_threads(self, *, limit: int = 10, scope: str = "active") -> list[dict[str, Any]]:
        with self._lock:
            return list_chat_threads(
                settings=self.settings,
                limit=limit,
                scope=scope,
            )

    def get_thread(self, thread_id: str) -> dict[str, Any]:
        with self._lock:
            # read_session_thread currently normalizes missing metadata to `{}`
            # before its existence check, so perform an explicit store preflight.
            self._require_thread(thread_id)
            payload = read_session_thread(
                settings=self.settings,
                state=ChatSessionState(),
                thread_id=thread_id,
            )
            if payload is None:
                raise self._not_found(thread_id)
            return payload

    def preview_thread(self, thread_id: str, *, turn_limit: int = 3) -> dict[str, Any]:
        with self._lock:
            self._require_thread(thread_id)
            return thread_preview_payload(
                settings=self.settings,
                thread_id=thread_id,
                turn_limit=turn_limit,
            )

    def rename_thread(self, thread_id: str, *, thread_label: str) -> dict[str, Any]:
        with self._lock:
            self._require_thread(thread_id)
            payload = rename_session_thread(
                settings=self.settings,
                state=ChatSessionState(thread_id=thread_id),
                thread_label=thread_label,
            )
            if payload is None:
                raise ApiError(
                    status_code=422,
                    code="invalid_thread_label",
                    message="thread_label must not be empty",
                )
            return payload

    def fork_thread(self, thread_id: str) -> dict[str, str]:
        with self._lock:
            self._require_thread(thread_id)
            state = self._hydrated_state(thread_id)
            child_thread_id = fork_session_thread(
                settings=self.settings,
                state=state,
                source_thread_id=thread_id,
            )
            return {
                "thread_id": child_thread_id,
                "parent_thread_id": thread_id,
            }

    def archive_thread(self, thread_id: str) -> dict[str, Any]:
        with self._lock:
            self._require_thread(thread_id)
            self._ensure_index_record(thread_id)
            # REST has no implicit active session. Passing a state without a current
            # thread prevents the CLI lifecycle helper from creating a replacement.
            payload = archive_session_thread(
                settings=self.settings,
                state=ChatSessionState(),
                thread_id=thread_id,
            )
            if payload is None:
                raise self._not_found(thread_id)
            payload["next_thread_id"] = None
            return payload

    def unarchive_thread(self, thread_id: str) -> dict[str, Any]:
        with self._lock:
            self._require_thread(thread_id)
            self._ensure_index_record(thread_id)
            payload = unarchive_session_thread(
                settings=self.settings,
                thread_id=thread_id,
            )
            if payload is None:
                raise self._not_found(thread_id)
            return payload

    def compact_thread(self, thread_id: str) -> dict[str, Any]:
        with self._lock:
            self._require_thread(thread_id)
            state = self._hydrated_state(thread_id)
            return compact_session_thread(settings=self.settings, state=state)

    def submit_research(
        self,
        thread_id: str,
        request: RequestEnvelope,
        *,
        run_service,
    ) -> ResponseEnvelope:
        with self._lock:
            self._require_thread(thread_id)
            state = self._hydrated_state(thread_id)
            effective_request = request.model_copy(
                update={
                    "conversation_context": [
                        *build_thread_context(settings=self.settings, state=state),
                        *list(request.conversation_context),
                    ],
                    "slots": {
                        **dict(request.slots),
                        "chat_thread_id": thread_id,
                    },
                }
            )
            response = run_service.run(effective_request)
            store = ChatThreadStore(self.settings)
            recorded_at = response.updated_at.isoformat()
            store.append_turn(
                thread_id=thread_id,
                role="user",
                content=effective_request.user_query,
                turn_type="research_query",
                intent=effective_request.intent,
                run_id=response.run_id,
                metadata={"request_id": effective_request.request_id},
                recorded_at=recorded_at,
            )
            store.append_turn(
                thread_id=thread_id,
                role="assistant",
                content=self._result_summary(response),
                turn_type="research_result",
                intent=response.intent,
                run_id=response.run_id,
                metadata={
                    "status": response.status,
                    "final_artifact_ids": list(
                        ((response.result or {}).get("final_artifact_ids") or [])
                    ),
                },
                recorded_at=recorded_at,
            )
            return response

    def _require_thread(self, thread_id: str) -> None:
        store = ChatThreadStore(self.settings)
        if store.has_thread(thread_id=thread_id):
            return
        # Match read_session_thread's index recovery behavior: a thread can still
        # be considered present when its index entry is missing but a record exists.
        records = (
            store.load_thread_checkpoint(thread_id=thread_id),
            store.load_thread_metadata(thread_id=thread_id),
            store.load_thread_summary(thread_id=thread_id),
        )
        if any(isinstance(record, dict) for record in records):
            return
        raise self._not_found(thread_id)

    def _ensure_index_record(self, thread_id: str) -> None:
        store = ChatThreadStore(self.settings)
        if not store.has_thread(thread_id=thread_id):
            store.ensure_thread(thread_id)

    def _hydrated_state(self, thread_id: str) -> ChatSessionState:
        state = ChatSessionState(thread_id=thread_id)
        store = ChatThreadStore(self.settings)
        checkpoint = store.load_thread_checkpoint(thread_id=thread_id)
        has_checkpoint = isinstance(checkpoint, dict) and isinstance(
            checkpoint.get("session_state"),
            dict,
        )
        has_turns = bool(store.read_recent_turns(thread_id=thread_id, limit=1))
        if has_checkpoint or has_turns:
            hydrate_session_from_persistent_memory(settings=self.settings, state=state)
        # The hydration fallback can consult global memory. The API operation must
        # always remain scoped to the path thread, even when that fallback is used.
        state.thread_id = thread_id
        return state

    @staticmethod
    def _result_summary(response: ResponseEnvelope) -> str:
        for artifact in response.artifacts:
            content = artifact.get("content") if isinstance(artifact, dict) else None
            if not isinstance(content, dict):
                continue
            conclusion = str(content.get("conclusion") or "").strip()
            if conclusion:
                return conclusion
        return response.message

    @staticmethod
    def _not_found(thread_id: str) -> ApiError:
        return ApiError(
            status_code=404,
            code="chat_thread_not_found",
            message=f"chat thread not found: {thread_id}",
        )


__all__ = ["ChatService"]
