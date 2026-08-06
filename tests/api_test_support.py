from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_system.schemas.protocol import ProgressState, RequestEnvelope, ResponseEnvelope
from research_system.schemas.runtime import RunEvent


API_TOKEN = "test-api-token"
REQUEST_ID = "01J00000000000000000000001"
RUN_ID = "01J00000000000000000000002"
RERUN_ID = "01J00000000000000000000003"
THREAD_ID = "01J00000000000000000000004"
INVOCATION_ID = "01J00000000000000000000005"
EVENT_ID = "01J00000000000000000000006"
NOW = datetime(2026, 7, 10, 8, 30, tzinfo=timezone.utc)


def auth_headers(token: str = API_TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def completed_response(
    *,
    request_id: str = REQUEST_ID,
    run_id: str = RUN_ID,
    status: str = "completed",
    intent: str = "stock_check",
) -> ResponseEnvelope:
    result = None
    errors: list[str] = []
    if status == "completed":
        result = {
            "intent": intent,
            "report": {
                "conclusion": "deterministic test conclusion",
                "evidence": [],
                "risk": "deterministic test risk",
                "degraded": False,
            },
        }
    elif status == "failed":
        errors = ["test_failure"]
    return ResponseEnvelope(
        request_id=request_id,
        run_id=run_id,
        intent=intent,
        status=status,  # type: ignore[arg-type]
        message=f"Run {status}",
        progress=ProgressState(
            current_phase="report" if status == "completed" else "collect",
            active_agent="reporter" if status == "completed" else "collector",
            latest_event=f"run_{status}",
            status_message=f"Run {status}",
            percent=100 if status == "completed" else 50,
            updated_at=NOW,
        ),
        result=result,
        errors=errors,
        created_at=NOW,
        updated_at=NOW,
    )


def run_event(
    *,
    event_id: str = EVENT_ID,
    event_type: str = "agent_progress",
    run_id: str = RUN_ID,
) -> RunEvent:
    return RunEvent(
        event_id=event_id,
        request_id=REQUEST_ID,
        run_id=run_id,
        intent="stock_check",
        event_type=event_type,
        phase="collect",
        agent="collector",
        message=f"event {event_type}",
        payload={"percent": 50},
        artifacts=[],
        created_at=NOW,
    )


@dataclass(frozen=True)
class RecordedCall:
    name: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


class FakeRunService:
    """Deterministic run-service fake for API adapter tests."""

    def __init__(
        self,
        *,
        outputs: dict[str, Any] | None = None,
        errors: dict[str, Exception] | None = None,
    ) -> None:
        self.outputs = self._default_outputs()
        self.outputs.update(outputs or {})
        self.errors = dict(errors or {})
        self.calls: list[RecordedCall] = []
        self.closed = False

    @staticmethod
    def _default_outputs() -> dict[str, Any]:
        return {
            "run": completed_response(),
            "history": [
                {"run_id": RUN_ID, "status": "completed", "intent": "stock_check"},
                {"run_id": RERUN_ID, "status": "failed", "intent": "stock_compare"},
            ],
            "replay": {"run_id": RUN_ID, "request": {}, "events": []},
            "artifacts": {"run_id": RUN_ID, "artifacts": []},
            "events": {"run_id": RUN_ID, "events": [], "event_counts": {"events": 0}},
            "trace": {"run_id": RUN_ID, "subagent_trace": [], "dynamic_trace": []},
            "steps": {"run_id": RUN_ID, "steps": [], "step_counts": 0},
            "metrics": {"run_id": RUN_ID, "counts": {}, "rates": {}},
            "thread": {"run_id": RUN_ID, "thread_id": THREAD_ID, "scope_thread_ids": [THREAD_ID]},
            "thread_replay": {"run_id": RUN_ID, "thread_id": THREAD_ID, "events": []},
            "thread_metrics": {"run_id": RUN_ID, "thread_id": THREAD_ID, "counts": {}},
            "subagent": {"run_id": RUN_ID, "invocation_id": INVOCATION_ID, "events": []},
            "resume": {
                "run_id": RUN_ID,
                "status": "failed",
                "resumable": False,
                "checkpoint": None,
                "recovery_guidance": {"status": "resume_unavailable"},
                "resume_from": None,
            },
            "cancel": {
                "run_id": RUN_ID,
                "status": "cancelled",
                "message": "Cancellation requested.",
                "control_state": {},
                "cancellation_requested": True,
            },
            "rerun_from": completed_response(run_id=RERUN_ID),
        }

    def _call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(RecordedCall(name=name, args=args, kwargs=dict(kwargs)))
        error = self.errors.get(name)
        if error is not None:
            raise error
        output = self.outputs[name]
        if callable(output):
            return output(*args, **kwargs)
        return output

    def calls_for(self, name: str) -> list[RecordedCall]:
        return [call for call in self.calls if call.name == name]

    def run(self, request: RequestEnvelope) -> ResponseEnvelope:
        return self._call("run", request)

    def history(self) -> list[dict[str, Any]]:
        return self._call("history")

    def replay(self, run_id: str) -> dict[str, Any]:
        return self._call("replay", run_id)

    def artifacts(
        self,
        run_id: str,
        *,
        artifact_type: str | None = None,
        status: str | None = "active",
    ) -> dict[str, Any]:
        return self._call(
            "artifacts",
            run_id,
            artifact_type=artifact_type,
            status=status,
        )

    def events(self, run_id: str) -> dict[str, Any]:
        return self._call("events", run_id)

    def trace(self, run_id: str) -> dict[str, Any]:
        return self._call("trace", run_id)

    def steps(self, run_id: str) -> dict[str, Any]:
        return self._call("steps", run_id)

    def metrics(self, run_id: str) -> dict[str, Any]:
        return self._call("metrics", run_id)

    def thread(self, run_id: str, thread_id: str) -> dict[str, Any]:
        return self._call("thread", run_id, thread_id)

    def thread_replay(self, run_id: str, thread_id: str) -> dict[str, Any]:
        return self._call("thread_replay", run_id, thread_id)

    def thread_metrics(self, run_id: str, thread_id: str) -> dict[str, Any]:
        return self._call("thread_metrics", run_id, thread_id)

    def subagent(self, run_id: str, invocation_id: str) -> dict[str, Any]:
        return self._call("subagent", run_id, invocation_id)

    def resume(self, run_id: str) -> dict[str, Any]:
        return self._call("resume", run_id)

    def cancel(self, run_id: str) -> dict[str, Any]:
        return self._call("cancel", run_id)

    def rerun_from(self, run_id: str, *, from_node: str) -> ResponseEnvelope:
        return self._call("rerun_from", run_id, from_node=from_node)

    def close(self) -> None:
        self.closed = True


class FakeRunServiceFactory:
    def __init__(self, service: FakeRunService) -> None:
        self.service = service
        self.agent_os_seen: list[Any] = []

    def __call__(self, agent_os: Any) -> FakeRunService:
        self.agent_os_seen.append(agent_os)
        return self.service


class FakeEventSource:
    def __init__(self, rows: list[RunEvent] | None = None) -> None:
        self.rows = list(rows or [])
        self.prepare_calls: list[tuple[str, str | None]] = []
        self.stream_calls: list[tuple[str, int]] = []
        self.closed = False

    def prepare(self, run_id: str, *, last_event_id: str | None = None) -> int:
        self.prepare_calls.append((run_id, last_event_id))
        if last_event_id:
            for index, row in enumerate(self.rows):
                if row.event_id == last_event_id:
                    return index + 1
        return 0

    async def stream(self, run_id: str, *, cursor: int = 0):
        self.stream_calls.append((run_id, cursor))
        try:
            for row in self.rows[max(0, cursor) :]:
                payload = row.model_dump(mode="json")
                data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                yield f"id: {row.event_id}\nevent: {row.event_type}\ndata: {data}\n\n"
        finally:
            self.closed = True


class FakeSystemService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.status_payload: dict[str, Any] = {"total_runs": 0, "status_counts": {}, "recent_runs": []}
        self.doctor_payload: dict[str, Any] = {"status": "completed", "checks": []}
        self.checks_payload: dict[str, Any] = {"status": "completed", "checks": []}

    def status(self) -> dict[str, Any]:
        self.calls.append(("status", (), {}))
        return self.status_payload

    def doctor(self) -> dict[str, Any]:
        self.calls.append(("doctor", (), {}))
        return self.doctor_payload

    def checks(self, repo_root: Path) -> dict[str, Any]:
        self.calls.append(("checks", (repo_root,), {}))
        return self.checks_payload
