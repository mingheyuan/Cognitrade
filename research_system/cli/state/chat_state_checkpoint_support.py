from __future__ import annotations

from research_system.cli.memory.chat_thread_store import ChatThreadStore
from research_system.cli.state.chat_updates import SESSION_PATCH_UNSET, SessionStatePatch
from research_system.runtime.platform.settings import Settings


def persist_thread_checkpoint(*, settings: Settings, state, session_state_snapshot_fn) -> dict[str, object]:
    thread_id = str(getattr(state, "thread_id", None) or "").strip()
    if not thread_id:
        raise ValueError("thread_id is required to persist a thread checkpoint")
    return ChatThreadStore(settings).write_thread_checkpoint(
        thread_id=thread_id,
        session_state=session_state_snapshot_fn(state),
    )


def apply_session_patch_and_checkpoint(
    *,
    settings: Settings,
    state,
    patch: SessionStatePatch,
    apply_session_state_patch_to_state_fn,
    has_session_state_patch_fn,
    persist_thread_checkpoint_fn,
) -> None:
    apply_session_state_patch_to_state_fn(state, patch)
    if has_session_state_patch_fn(patch):
        if not getattr(state, "thread_id", None) and patch.thread_id is not SESSION_PATCH_UNSET:
            state.thread_id = str(patch.thread_id or "").strip() or None
        if getattr(state, "thread_id", None):
            persist_thread_checkpoint_fn(settings=settings, state=state)
