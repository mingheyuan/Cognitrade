from __future__ import annotations

from typing import Any, Callable


class RuntimeControlStageRecorder:
    def __init__(
        self,
        *,
        runtime_binding_getter: Callable[[], dict[str, Any]] | None = None,
        emit_event: Callable[..., None] | None = None,
    ) -> None:
        self.runtime_binding_getter = runtime_binding_getter
        self.emit_event = emit_event

    def record(
        self,
        *,
        event_type: str,
        message: str,
        payload: dict[str, Any],
        phase: str | None = None,
        agent: str | None = None,
    ) -> None:
        binding = (
            dict(self.runtime_binding_getter() or {})
            if callable(self.runtime_binding_getter)
            else {}
        )
        context = binding.get("context")
        support = binding.get("support")
        request_id = str(binding.get("request_id") or "").strip()
        intent = str(binding.get("intent") or payload.get("intent") or "").strip()
        if context is None or not request_id or not intent:
            return
        row = {"event": event_type, **dict(payload or {})}
        runtime_rows = getattr(context, "runtime_control_stage_decisions", None)
        if not isinstance(runtime_rows, list):
            try:
                setattr(context, "runtime_control_stage_decisions", [])
            except Exception:
                runtime_rows = None
            else:
                runtime_rows = getattr(context, "runtime_control_stage_decisions", None)
        if isinstance(runtime_rows, list):
            runtime_rows.append(dict(row))
        dynamic_trace = getattr(context, "dynamic_trace", None)
        if isinstance(dynamic_trace, list):
            dynamic_trace.append(dict(row))
        if callable(self.emit_event):
            self.emit_event(
                event_type=event_type,
                message=message,
                payload=dict(row),
                phase=phase,
                agent=agent,
            )
            return
        support_emit_event = getattr(support, "emit_event", None)
        if callable(support_emit_event):
            support_emit_event(
                context,
                request_id=request_id,
                intent=intent,
                event_type=event_type,
                phase=phase,
                agent=agent,
                message=message,
                payload=dict(row),
            )


__all__ = ["RuntimeControlStageRecorder"]
