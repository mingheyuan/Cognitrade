from __future__ import annotations

import json

from research_system.cli.state.chat_updates import (
    SessionStatePatch,
    ShellStatePatch,
    meta_update,
    session_patch_update,
    state_patch_update,
)
from research_system.services.payloads.runs.run_payloads import (
    latest_run_context,
    render_labeled_field_line,
    render_run_row_text,
)


def emit_run_context_feed(
    run_id: str | None,
    status: str | None,
    *,
    emit_text,
    output_writer=None,
) -> None:
    if run_id is not None or status is not None:
        emit_text(
            output_writer,
            state_patch_update(
                ShellStatePatch(
                    current_run_id=run_id,
                    run_status_label=status,
                )
            ),
        )
    if run_id is not None:
        emit_text(
            output_writer,
            session_patch_update(
                SessionStatePatch(
                    last_run_id=run_id,
                )
            ),
        )


def emit_run_detail_fields(
    fields: list[tuple[str, str]],
    *,
    emit_text,
    output_writer=None,
) -> None:
    run_id = None
    status = None
    for label, value in fields:
        line = render_labeled_field_line(label, value)
        if label == "run_id":
            run_id = value
            emit_text(
                output_writer,
                meta_update(
                    line,
                    state_patch=ShellStatePatch(current_run_id=value),
                    session_patch=SessionStatePatch(last_run_id=value),
                ),
            )
            continue
        if label == "status":
            status = value
            emit_text(
                output_writer,
                meta_update(
                    line,
                    state_patch=ShellStatePatch(run_status_label=value),
                ),
            )
            continue
        emit_text(output_writer, meta_update(line))
    if run_id is None or status is None:
        emit_run_context_feed(run_id, status, emit_text=emit_text, output_writer=output_writer)


def print_rows(rows: list[object], *, emit_text, output_writer=None) -> None:
    if not rows:
        emit_text(output_writer, meta_update("No runs."))
        return
    for row in rows:
        if not isinstance(row, dict):
            emit_text(output_writer, meta_update(json.dumps(row, ensure_ascii=True)))
            continue
        emit_text(output_writer, meta_update(render_run_row_text(row)))
    latest_run_id, latest_status = latest_run_context(rows)
    if latest_run_id is not None or latest_status is not None:
        emit_run_context_feed(
            latest_run_id,
            latest_status,
            emit_text=emit_text,
            output_writer=output_writer,
        )
