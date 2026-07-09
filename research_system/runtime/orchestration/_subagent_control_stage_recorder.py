from __future__ import annotations

from typing import Any, Callable


class SubagentControlStageRecorder:
    def __init__(
        self,
        *,
        runtime_binding_getter: Callable[[], dict[str, Any]] | None = None,
        emit_event: Callable[..., None] | None = None,
        runtime_control_stage_recorder=None,
    ) -> None:
        self.runtime_binding_getter = runtime_binding_getter
        self.emit_event = emit_event
        self.runtime_control_stage_recorder = runtime_control_stage_recorder

    def record(
        self,
        *,
        event_type: str,
        message: str,
        payload: dict[str, Any],
        phase: str | None = None,
        agent: str | None = None,
        extra_context_attrs: list[str] | None = None,
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
        self._append_context_row(
            context=context,
            attr_name="subagent_control_stage_decisions",
            row=row,
        )
        for attr_name in list(extra_context_attrs or []):
            self._append_context_row(
                context=context,
                attr_name=str(attr_name),
                row=row,
            )
        if self.runtime_control_stage_recorder is not None:
            self.runtime_control_stage_recorder.record(
                event_type=event_type,
                message=message,
                payload=row,
                phase=phase,
                agent=agent,
            )
            return
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

    @staticmethod
    def _append_context_row(*, context, attr_name: str, row: dict[str, Any]) -> None:
        rows = getattr(context, attr_name, None)
        if not isinstance(rows, list):
            try:
                setattr(context, attr_name, [])
            except Exception:
                rows = None
            else:
                rows = getattr(context, attr_name, None)
        if isinstance(rows, list):
            rows.append(dict(row))


__all__ = ["SubagentControlStageRecorder"]
