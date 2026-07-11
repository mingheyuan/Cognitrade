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

    identity_scoped = True

    def __init__(self, settings: Settings, *, lock: RLockType | None = None) -> None:
        self.settings = settings
        # ChatThreadStore updates index.json with a read-modify-write cycle. Keep a
        # process-wide lock even when the API dependency creates multiple services.
        self._lock = lock or _CHAT_STORE_LOCK

    def create_thread(
        self,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, str]:
        with self._lock:
            state = ChatSessionState()
            thread_id = start_new_session_thread(settings=self.settings, state=state)
            defaults = {
                key: value
                for key, value in {
                    "user_id": user_id,
                    "tenant_id": tenant_id,
                }.items()
                if value not in (None, "")
            }
            if defaults:
                ChatThreadStore(self.settings).ensure_thread_metadata(
                    thread_id=thread_id,
                    defaults=defaults,
                )
            return {"thread_id": thread_id}

    def list_threads(
        self,
        *,
        limit: int = 10,
        scope: str = "active",
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = list_chat_threads(
                settings=self.settings,
                limit=limit,
                scope=scope,
            )
            if user_id is None and tenant_id is None:
                return rows
            store = ChatThreadStore(self.settings)
            return [
                row
                for row in rows
                if self._metadata_matches_scope(
                    store.load_thread_metadata(thread_id=str(row.get("thread_id") or "")) or {},
                    user_id=user_id,
                    tenant_id=tenant_id,
                )
            ]

    def get_thread(
        self,
        thread_id: str,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            # read_session_thread currently normalizes missing metadata to `{}`
            # before its existence check, so perform an explicit store preflight.
            self._assert_thread_scope(thread_id, user_id=user_id, tenant_id=tenant_id)
            payload = read_session_thread(
                settings=self.settings,
                state=ChatSessionState(),
                thread_id=thread_id,
            )
            if payload is None:
                raise self._not_found(thread_id)
            return payload

    def preview_thread(
        self,
        thread_id: str,
        *,
        turn_limit: int = 3,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._assert_thread_scope(thread_id, user_id=user_id, tenant_id=tenant_id)
            return thread_preview_payload(
                settings=self.settings,
                thread_id=thread_id,
                turn_limit=turn_limit,
            )

    def rename_thread(
        self,
        thread_id: str,
        *,
        thread_label: str,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._assert_thread_scope(thread_id, user_id=user_id, tenant_id=tenant_id)
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

    def fork_thread(
        self,
        thread_id: str,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, str]:
        with self._lock:
            self._assert_thread_scope(thread_id, user_id=user_id, tenant_id=tenant_id)
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

    def archive_thread(
        self,
        thread_id: str,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._assert_thread_scope(thread_id, user_id=user_id, tenant_id=tenant_id)
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

    def unarchive_thread(
        self,
        thread_id: str,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._assert_thread_scope(thread_id, user_id=user_id, tenant_id=tenant_id)
            self._ensure_index_record(thread_id)
            payload = unarchive_session_thread(
                settings=self.settings,
                thread_id=thread_id,
            )
            if payload is None:
                raise self._not_found(thread_id)
            return payload

    def compact_thread(
        self,
        thread_id: str,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._assert_thread_scope(thread_id, user_id=user_id, tenant_id=tenant_id)
            state = self._hydrated_state(thread_id)
            return compact_session_thread(settings=self.settings, state=state)

    def submit_research(
        self,
        thread_id: str,
        request: RequestEnvelope,
        *,
        run_service,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> ResponseEnvelope:
        with self._lock:
            self._assert_thread_scope(thread_id, user_id=user_id, tenant_id=tenant_id)
            state = self._hydrated_state(thread_id)
            effective_request = request.model_copy(
                update={
                    "thread_id": thread_id,
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
            pending_run_id = self._pending_clarification_run_id(thread_id)
            if pending_run_id:
                response = run_service.run(effective_request, run_id=pending_run_id)
            else:
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
                    "clarification_pending": response.status == "needs_clarification",
                    "final_artifact_ids": list(
                        ((response.result or {}).get("final_artifact_ids") or [])
                    ),
                },
                recorded_at=recorded_at,
            )
            return response

    def record_run_completion(
        self,
        *,
        thread_id: str,
        response: ResponseEnvelope,
    ) -> None:
        """Append the final result of a background Agent OS run to its thread."""
        if response.status == "running":
            return
        with self._lock:
            self._assert_thread_scope(thread_id, user_id=None, tenant_id=None)
            store = ChatThreadStore(self.settings)
            existing = store.read_all_turns(thread_id=thread_id)
            for row in reversed(existing):
                if str(row.get("role") or "") != "assistant":
                    continue
                if str(row.get("run_id") or "") != str(response.run_id or ""):
                    continue
                metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
                if metadata.get("background_completed"):
                    return
                break
            recorded_at = response.updated_at.isoformat()
            store.append_turn(
                thread_id=thread_id,
                role="assistant",
                content=self._result_summary(response),
                turn_type="research_result",
                intent=response.intent,
                run_id=response.run_id,
                metadata={
                    "status": response.status,
                    "background_completed": True,
                    "clarification_pending": response.status == "needs_clarification",
                    "final_artifact_ids": list(
                        ((response.result or {}).get("final_artifact_ids") or [])
                    ),
                },
                recorded_at=recorded_at,
            )

    def _pending_clarification_run_id(self, thread_id: str) -> str | None:
        """Return only the latest assistant turn's pending clarification Run."""
        rows = ChatThreadStore(self.settings).read_all_turns(thread_id=thread_id)
        for row in reversed(rows):
            if str(row.get("role") or "") != "assistant":
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            if str(metadata.get("status") or "") != "needs_clarification":
                return None
            run_id = str(row.get("run_id") or "").strip()
            return run_id or None
        return None

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

    def _assert_thread_scope(
        self,
        thread_id: str,
        *,
        user_id: str | None,
        tenant_id: str | None,
    ) -> None:
        self._require_thread(thread_id)
        if user_id is None and tenant_id is None:
            return
        metadata = ChatThreadStore(self.settings).load_thread_metadata(thread_id=thread_id) or {}
        if self._metadata_matches_scope(
            metadata,
            user_id=user_id,
            tenant_id=tenant_id,
        ):
            return
        raise self._not_found(thread_id)

    @staticmethod
    def _metadata_matches_scope(
        metadata: dict[str, Any],
        *,
        user_id: str | None,
        tenant_id: str | None,
    ) -> bool:
        if user_id is None and tenant_id is None:
            return True
        stored_user_id = str(metadata.get("user_id") or "").strip() or None
        stored_tenant_id = str(metadata.get("tenant_id") or "").strip() or None
        if stored_user_id is None and stored_tenant_id is None:
            return user_id == "api_token" and tenant_id == "api_token"
        if user_id is not None and stored_user_id != user_id:
            return False
        if tenant_id is not None and stored_tenant_id != tenant_id:
            return False
        return True

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
