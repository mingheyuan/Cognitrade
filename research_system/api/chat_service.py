from __future__ import annotations

from _thread import RLock as RLockType
from threading import RLock
from typing import Any

from research_system.api.chat_store import ChatThreadStore
from research_system.api.errors import ApiError
from research_system.runtime.platform.settings import Settings
from research_system.schemas.protocol import RequestEnvelope, ResponseEnvelope


_CHAT_STORE_LOCK = RLock()


class ChatService:
    """Transport-neutral service for the versioned chat API."""

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
            thread_id = ChatThreadStore(self.settings).create_thread(
                user_id=user_id,
                tenant_id=tenant_id,
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
            store = ChatThreadStore(self.settings)
            rows = store.list_threads(
                limit=limit,
                scope=scope,
            )
            if user_id is None and tenant_id is None:
                return rows
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
            self._assert_thread_scope(thread_id, user_id=user_id, tenant_id=tenant_id)
            return ChatThreadStore(self.settings).thread_detail(thread_id)

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
            return ChatThreadStore(self.settings).preview_thread(
                thread_id,
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
            return ChatThreadStore(self.settings).rename_thread(
                thread_id,
                thread_label,
            )

    def fork_thread(
        self,
        thread_id: str,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, str]:
        with self._lock:
            self._assert_thread_scope(thread_id, user_id=user_id, tenant_id=tenant_id)
            child_thread_id = ChatThreadStore(self.settings).fork_thread(thread_id)
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
            return ChatThreadStore(self.settings).archive_thread(thread_id)

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
            return ChatThreadStore(self.settings).unarchive_thread(thread_id)

    def compact_thread(
        self,
        thread_id: str,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._assert_thread_scope(thread_id, user_id=user_id, tenant_id=tenant_id)
            return ChatThreadStore(self.settings).compact_thread(thread_id)

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
            store = ChatThreadStore(self.settings)
            effective_request = request.model_copy(
                update={
                    "thread_id": thread_id,
                    "conversation_context": [
                        *store.build_context(thread_id),
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
        if store.thread_exists(thread_id):
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
