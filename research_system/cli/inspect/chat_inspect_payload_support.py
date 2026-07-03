from __future__ import annotations

from research_system.cli.state.chat_types import TuiSession


def load_trace_payload_for_session(
    session: TuiSession,
    *,
    run_id: str | None,
    execute_cli_command,
    emit_warning_update,
) -> dict | None:
    resolved_run_id = str(run_id or "").strip()
    payload = session.trace_payload
    if isinstance(payload, dict):
        cached_run_id = str(payload.get("run_id") or "").strip()
        if cached_run_id and cached_run_id == str(resolved_run_id or cached_run_id):
            return payload
    if not resolved_run_id:
        return payload if isinstance(payload, dict) else None
    try:
        payload = execute_cli_command(session.settings, command="trace", run_id=resolved_run_id)
    except Exception as exc:  # pragma: no cover - defensive UI fallback
        emit_warning_update(session, f"trace load failed: {exc}")
        return None
    if (
        isinstance(payload, dict)
        and "subagent_trace" in payload
        and "dynamic_trace" in payload
        and "wait_trace" in payload
    ):
        session.trace_payload = payload
        return payload
    return None


def load_events_payload_for_session(
    session: TuiSession,
    *,
    run_id: str | None,
    execute_cli_command,
    emit_warning_update,
) -> dict | None:
    resolved_run_id = str(run_id or "").strip()
    payload = session.events_payload
    if isinstance(payload, dict):
        cached_run_id = str(payload.get("run_id") or "").strip()
        if cached_run_id and cached_run_id == str(resolved_run_id or cached_run_id):
            return payload
    if not resolved_run_id:
        return payload if isinstance(payload, dict) else None
    try:
        payload = execute_cli_command(session.settings, command="events", run_id=resolved_run_id)
    except Exception as exc:  # pragma: no cover - defensive UI fallback
        emit_warning_update(session, f"events load failed: {exc}")
        return None
    if isinstance(payload, dict) and "events" in payload and "event_counts" in payload:
        session.events_payload = payload
        return payload
    return None
