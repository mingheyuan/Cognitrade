from __future__ import annotations

from research_system.cli.chat_state_checkpoint_support import (
    apply_session_patch_and_checkpoint as checkpoint_apply_session_patch_and_checkpoint,
    persist_thread_checkpoint as checkpoint_persist_thread_checkpoint,
)
from research_system.cli.chat_state_mutation_support import (
    apply_session_checkpoint_to_state as mutation_apply_session_checkpoint_to_state,
    apply_session_state_patch_to_state as mutation_apply_session_state_patch_to_state,
    has_session_state_patch as mutation_has_session_state_patch,
    session_state_snapshot as mutation_session_state_snapshot,
)
from research_system.cli.chat_state_patch_builders import (
    build_new_thread_session_patch as builder_build_new_thread_session_patch,
    build_research_result_session_patch as builder_build_research_result_session_patch,
    build_show_last_session_patch as builder_build_show_last_session_patch,
    clear_recent_session_patch as builder_clear_recent_session_patch,
    mirror_session_state_patch as builder_mirror_session_state_patch,
)
from research_system.cli.chat_updates import SessionStatePatch
from research_system.runtime.settings import Settings


def apply_session_state_patch_to_state(state, patch: SessionStatePatch) -> None:
    mutation_apply_session_state_patch_to_state(state, patch)


def build_research_result_session_patch(
    *,
    payload: object,
    query: str,
    effective_intent: str | None,
    review_target_run_id: str | None = None,
    pending_patch: SessionStatePatch | None = None,
) -> SessionStatePatch:
    return builder_build_research_result_session_patch(
        payload=payload,
        query=query,
        effective_intent=effective_intent,
        review_target_run_id=review_target_run_id,
        pending_patch=pending_patch,
    )


def clear_recent_session_patch(*, include_defaults: bool = False) -> SessionStatePatch:
    return builder_clear_recent_session_patch(include_defaults=include_defaults)


def build_new_thread_session_patch(*, thread_id: str) -> SessionStatePatch:
    return builder_build_new_thread_session_patch(thread_id=thread_id)


def mirror_session_state_patch(state, *, thread_id: str | None = None) -> SessionStatePatch:
    return builder_mirror_session_state_patch(state, thread_id=thread_id)


def build_show_last_session_patch(state) -> SessionStatePatch:
    return builder_build_show_last_session_patch(state)


def session_state_snapshot(state) -> dict[str, object]:
    return mutation_session_state_snapshot(state)


def apply_session_checkpoint_to_state(*, state, checkpoint: dict[str, Any]) -> None:
    mutation_apply_session_checkpoint_to_state(state=state, checkpoint=checkpoint)


def persist_thread_checkpoint(*, settings: Settings, state) -> dict[str, Any]:
    return checkpoint_persist_thread_checkpoint(
        settings=settings,
        state=state,
        session_state_snapshot_fn=session_state_snapshot,
    )


def apply_session_patch_and_checkpoint(
    *,
    settings: Settings,
    state,
    patch: SessionStatePatch,
) -> None:
    checkpoint_apply_session_patch_and_checkpoint(
        settings=settings,
        state=state,
        patch=patch,
        apply_session_state_patch_to_state_fn=apply_session_state_patch_to_state,
        has_session_state_patch_fn=has_session_state_patch,
        persist_thread_checkpoint_fn=persist_thread_checkpoint,
    )


def has_session_state_patch(patch: SessionStatePatch) -> bool:
    return mutation_has_session_state_patch(patch)
