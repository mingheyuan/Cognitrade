from __future__ import annotations

from research_system.runtime.core.control_plane import RunControlPlane
from research_system.runtime.core.event_stream import EventStream
from research_system.runtime.history.session.session_history import SessionHistory
from research_system.runtime.storage.runs.checkpoint_store import CheckpointStore
from research_system.runtime.storage.jsonl import write_json
from research_system.schemas.runtime import RunEvent


class OrchestratorRunAdminSupport:
    def __init__(self, host) -> None:
        self.host = host

    def replay(self, run_id: str) -> dict:
        paths = self.host.run_store.load_run_paths(run_id)
        return SessionHistory(paths.root).replay()

    def trace(self, run_id: str) -> dict:
        paths = self.host.run_store.load_run_paths(run_id)
        return SessionHistory(paths.root).trace()

    def events(self, run_id: str) -> dict:
        paths = self.host.run_store.load_run_paths(run_id)
        return SessionHistory(paths.root).events()

    def metrics(self, run_id: str) -> dict:
        paths = self.host.run_store.load_run_paths(run_id)
        return SessionHistory(paths.root).metrics()

    def thread(self, run_id: str, thread_id: str) -> dict:
        paths = self.host.run_store.load_run_paths(run_id)
        return SessionHistory(paths.root).thread(thread_id)

    def thread_replay(self, run_id: str, thread_id: str) -> dict:
        paths = self.host.run_store.load_run_paths(run_id)
        return SessionHistory(paths.root).thread_replay(thread_id)

    def thread_metrics(self, run_id: str, thread_id: str) -> dict:
        paths = self.host.run_store.load_run_paths(run_id)
        return SessionHistory(paths.root).thread_metrics(thread_id)

    def subagent(self, run_id: str, invocation_id: str) -> dict:
        paths = self.host.run_store.load_run_paths(run_id)
        return SessionHistory(paths.root).subagent(invocation_id)

    def steps(self, run_id: str) -> dict:
        paths = self.host.run_store.load_run_paths(run_id)
        return SessionHistory(paths.root).steps()

    def history(self) -> list[dict]:
        return self.host.run_store.list_runs()

    def resume(self, run_id: str) -> dict:
        paths = self.host.run_store.load_run_paths(run_id)
        run_state_payload = self.host.load_json_payload(paths.run_state) or {}
        checkpoint = CheckpointStore(paths.checkpoint).load()
        control_state = RunControlPlane(paths.run_control).load()
        control_payload = control_state.to_dict() if control_state is not None else None
        status = run_state_payload.get("status")
        resumable = status in {"failed", "timed_out", "cancelled"} and checkpoint is not None
        return {
            "run_id": run_id,
            "status": status,
            "resumable": resumable,
            "checkpoint": checkpoint.model_dump(mode="json") if checkpoint else None,
            "control_state": control_payload,
            "recovery_guidance": self._build_recovery_guidance(
                status=status,
                checkpoint=checkpoint,
                control_payload=control_payload,
            ),
            "resume_from": {
                "current_phase": checkpoint.current_phase if checkpoint else None,
                "active_agent": checkpoint.active_agent if checkpoint else None,
                "completed_nodes": checkpoint.completed_nodes if checkpoint else [],
                "pending_nodes": checkpoint.pending_nodes if checkpoint else [],
            }
            if checkpoint
            else None,
        }

    def cancel(self, run_id: str) -> dict:
        paths = self.host.run_store.load_run_paths(run_id)
        control_plane = RunControlPlane(paths.run_control)
        run_state_payload = self.host.load_json_payload(paths.run_state)
        status = run_state_payload.get("status") if isinstance(run_state_payload, dict) else None
        active_run = status in {"received", "identifying_intent", "ready", "running", "waiting_external", "gated"}
        control_state = (
            control_plane.request_cancellation(reason="manual_cancel")
            if active_run
            else control_plane.load()
        )
        if active_run and run_state_payload:
            run_state_payload["status"] = "cancelled"
            write_json(paths.run_state, run_state_payload)
        if active_run:
            event_stream = EventStream(paths.events)
            event_stream.emit(
                RunEvent(
                    request_id=(self.host.load_json_payload(paths.request) or {}).get("request_id", "unknown_request"),
                    run_id=run_id,
                    intent=(run_state_payload or {}).get("intent", "unknown_intent"),
                    event_type="run_cancelled",
                    message="Cancellation requested",
                    payload={"reason": "manual_cancel"},
                )
            )
        payload_status = "cancelled" if active_run else str(status or "unknown")
        payload_message = "Cancellation requested." if active_run else "Run is already terminal; no cancellation was applied."
        payload = {
            "run_id": run_id,
            "status": payload_status,
            "message": payload_message,
            "control_state": control_state.to_dict() if control_state else None,
            "cancellation_requested": active_run,
        }
        if active_run:
            write_json(paths.summary, payload)
        return payload

    @staticmethod
    def _build_recovery_guidance(
        *,
        status,
        checkpoint,
        control_payload: dict | None,
    ) -> dict:
        guidance = {
            "status": "resume_unavailable" if checkpoint is None else "resume_ready",
            "reason": "checkpoint_missing" if checkpoint is None else "checkpoint_available",
            "suggested_next_step": (
                "resume is unavailable because no checkpoint was persisted"
                if checkpoint is None
                else "resume from the persisted execution checkpoint"
            ),
        }
        if str(status or "").strip() == "cancelled" and bool((control_payload or {}).get("cancellation_requested")):
            guidance.update(
                {
                    "status": "resume_after_manual_cancel",
                    "reason": "run was cancelled manually after a persisted checkpoint",
                    "suggested_next_step": "resume from checkpoint if the cancellation was only used as an inspection stop",
                }
            )
        return guidance
