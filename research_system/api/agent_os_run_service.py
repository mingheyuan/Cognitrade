from __future__ import annotations

from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from threading import RLock
from typing import Any

from research_system.runtime.agent_os import AgentOS
from research_system.runtime.storage.jsonl import read_json, read_jsonl, write_json
from research_system.runtime.core.id_utils import generate_ulid
from research_system.schemas.core_runtime import MainAgentState
from research_system.schemas.execution_runtime import ExecutionRunResult, ExecutionRunSnapshot
from research_system.schemas.protocol import ProgressState, RequestEnvelope, ResponseEnvelope


class AgentOSRunService:
    """Versioned API adapter backed exclusively by canonical Agent OS state."""

    identity_scoped = True
    _LEASE_FILE = "agent_os_worker_lease.json"

    def __init__(self, agent_os: AgentOS) -> None:
        self.agent_os = agent_os
        self._background_executor = ThreadPoolExecutor(
            max_workers=max(1, int(getattr(agent_os.settings, "max_child_threads_per_turn", 4)))
        )
        self._background_futures: dict[str, Future] = {}
        self._background_lock = RLock()
        self._worker_id = f"api-worker:{generate_ulid()}"
        self._chat_completion_callback = None
        self._fund_holding_reports = None
        self._lease_seconds = max(
            float(getattr(agent_os.settings, "run_timeout_seconds", 300.0)) * 2.0,
            60.0,
        )
        self._recover_pending_runs()

    def bind_chat_completion(self, callback) -> None:
        """Bind an optional transport callback for completed background runs."""
        self._chat_completion_callback = callback

    def bind_fund_holding_reports(self, service) -> None:
        """Bind the Pack-specific batch facade without changing Agent OS execution."""
        self._fund_holding_reports = service

    def run(self, request: RequestEnvelope, *, run_id: str | None = None) -> ResponseEnvelope:
        if bool((request.constraints or {}).get("background")):
            return self.submit_background(request)
        return self.agent_os.handle_request(request, run_id=run_id)

    def submit_background(self, request: RequestEnvelope) -> ResponseEnvelope:
        run_id = generate_ulid()
        self.agent_os.prepare_run(
            request,
            run_id=run_id,
            intent=request.intent or "agent_os.pending",
        )
        self._submit_background_worker(run_id, request)
        return ResponseEnvelope(
            request_id=request.request_id,
            run_id=run_id,
            mode="core",
            intent=request.intent,
            status="running",
            message="Agent OS accepted the request and started a background run.",
            progress=ProgressState(
                current_phase="request_intake",
                active_agent="main_agent",
                latest_event="run_accepted",
                status_message="Agent OS run accepted; follow execution events for progress.",
                percent=0,
            ),
            result={
                "protocol": "agent_os",
                "mode": "pending",
                "background": True,
            },
        )

    def close(self) -> None:
        # A worker may have completed the Turn state transition but still be
        # releasing its lease in ``finally``. Wait here so callers can safely
        # dispose the RunStore (notably on Windows, where an open lease file
        # prevents directory cleanup).
        self._background_executor.shutdown(wait=True, cancel_futures=False)

    def _forget_background(self, run_id: str) -> None:
        with self._background_lock:
            self._background_futures.pop(run_id, None)

    def _submit_background_worker(self, run_id: str, request: RequestEnvelope) -> None:
        if not self._claim_background_lease(run_id):
            return
        future = self._background_executor.submit(self._run_background, run_id, request)
        with self._background_lock:
            self._background_futures[run_id] = future
        future.add_done_callback(lambda _future, key=run_id: self._forget_background(key))

    def _recover_pending_runs(self) -> None:
        runs_root = self.agent_os.settings.runs_root
        if not runs_root.exists():
            return
        for run_root in sorted(runs_root.iterdir()):
            if not run_root.is_dir() or read_json(run_root / "response.json") is not None:
                continue
            if read_json(run_root / "agent_os_pending.json") is None:
                continue
            request_payload = read_json(run_root / "request.json") or {}
            constraints = request_payload.get("constraints") if isinstance(request_payload, dict) else None
            if isinstance(constraints, dict) and constraints.get("fund_holding_report_child") is True:
                # The dedicated holding-report coordinator owns recovery and its
                # independent concurrency limit for these child Runs.
                continue
            request_payload: dict[str, Any] = {}
            try:
                request_payload = read_json(run_root / "request.json")
                request = RequestEnvelope.model_validate(request_payload or {})
                self.agent_os.adopt_prepared_run(run_root.name)
                self._submit_background_worker(run_root.name, request)
            except Exception:
                self._mark_background_failed(
                    run_root.name,
                    request_id=str((request_payload or {}).get("request_id") or "unknown"),
                    error="background reservation could not be recovered",
                )

    def _run_background(self, run_id: str, request: RequestEnvelope):
        try:
            detail = self.agent_os.inspect_run(run_id)
            if detail.get("mode") == "pending":
                response = self.agent_os.handle_request(request, run_id=run_id)
                self._persist_background_response(response)
                return response
            if detail.get("mode") == "core":
                state = MainAgentState.model_validate(detail["snapshot"])
                if state.status in {"completed", "completed_partial", "failed", "cancelled", "timed_out"}:
                    return None
                core_request, decision = self.agent_os._load_core_request(run_id)
                result, response = self.agent_os._resume_core_task(
                    request=core_request,
                    decision=decision,
                    state=state,
                )
                self._persist_background_response(response)
                return response
            snapshot = ExecutionRunSnapshot.model_validate(detail["snapshot"])
            if snapshot.status in {"completed", "completed_partial", "failed", "cancelled", "timed_out", "gated"}:
                return None
            result = self.agent_os.resume(run_id)
            request = self._request_envelope(run_id)
            response = self._response(request, result)
            self._persist_background_response(response)
            return response
        except Exception as exc:
            self._mark_background_failed(
                run_id,
                request_id=request.request_id,
                error=f"{exc.__class__.__name__}: {exc}",
            )
            return None
        finally:
            self._release_background_lease(run_id)

    def _persist_background_response(self, response: ResponseEnvelope) -> None:
        # TurnRunner.finalize owns canonical response persistence.  This
        # adapter only publishes the completion to Chat/background observers.
        callback = self._chat_completion_callback
        if callback is None or response.status == "running":
            return
        try:
            root = self.agent_os.run_store.load_run_paths(str(response.run_id)).root
            request_payload = read_json(root / "request.json")
            if not isinstance(request_payload, dict):
                return
            constraints = request_payload.get("constraints")
            if not isinstance(constraints, dict) or not constraints.get("background"):
                return
            slots = request_payload.get("slots")
            slots = slots if isinstance(slots, dict) else {}
            thread_id = str(
                request_payload.get("thread_id")
                or slots.get("chat_thread_id")
                or ""
            ).strip()
            if thread_id:
                callback(thread_id=thread_id, response=response)
        except Exception:
            # A transport persistence failure must not change the Agent OS run result.
            return

    def _claim_background_lease(self, run_id: str) -> bool:
        try:
            root = self.agent_os.run_store.load_run_paths(run_id).root
        except FileNotFoundError:
            return False
        lease_path = root / self._LEASE_FILE
        now = datetime.now(timezone.utc)
        existing = read_json(lease_path)
        if isinstance(existing, dict):
            expires_at = self._parse_lease_time(existing.get("expires_at"))
            if expires_at is not None and expires_at > now:
                return False
            try:
                lease_path.unlink()
            except FileNotFoundError:
                pass
        payload = {
            "worker_id": self._worker_id,
            "claimed_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=self._lease_seconds)).isoformat(),
        }
        try:
            descriptor = os.open(
                lease_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except FileExistsError:
            return False
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=True, indent=2)
                handle.write("\n")
        except Exception:
            lease_path.unlink(missing_ok=True)
            raise
        return True

    def _release_background_lease(self, run_id: str) -> None:
        try:
            root = self.agent_os.run_store.load_run_paths(run_id).root
        except FileNotFoundError:
            return
        lease_path = root / self._LEASE_FILE
        lease = read_json(lease_path)
        if isinstance(lease, dict) and lease.get("worker_id") == self._worker_id:
            lease_path.unlink(missing_ok=True)

    @staticmethod
    def _parse_lease_time(value: object) -> datetime | None:
        try:
            parsed = datetime.fromisoformat(str(value or ""))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _decorate_history_row(
        self,
        run_id: str,
        detail: dict[str, Any],
        row: dict[str, Any],
    ) -> dict[str, Any]:
        message = self._history_message(run_id, detail)
        status = str(row.get("status") or "unknown")
        return {
            **row,
            "turn_id": (detail.get("turn") or {}).get("turn_id"),
            "task_type": (detail.get("turn") or {}).get("task_type") or (detail.get("task") or {}).get("task_type"),
            "execution_mode": (detail.get("turn") or {}).get("execution_mode") or (detail.get("task") or {}).get("execution_mode"),
            "task_selection": (detail.get("turn") or {}).get("task_selection") or {},
            "child_turn_ids": [
                item.get("child_turn_id")
                for item in list((detail.get("turn") or {}).get("child_turns") or [])
                if isinstance(item, dict) and item.get("child_turn_id")
            ],
            "message": message,
            "display_summary": f"{status}: {message}" if message else f"{status}: Agent OS run",
        }

    def _history_message(self, run_id: str, detail: dict[str, Any]) -> str:
        state = detail.get("snapshot") or {}
        if detail.get("mode") == "core":
            message = str(state.get("assistant_message") or "").strip()
            if message:
                return message
        if detail.get("mode") == "pending":
            message = str(state.get("clarification_question") or "").strip()
            if message:
                return message
        for artifact in reversed(detail.get("artifacts") or []):
            if not isinstance(artifact, dict):
                continue
            content = artifact.get("content")
            if not isinstance(content, dict):
                continue
            artifact_type = str(artifact.get("artifact_type") or "")
            if artifact_type == "assistant_message" and content.get("message"):
                return str(content["message"]).strip()
            if artifact_type == "research_report":
                report_message = content.get("conclusion") or content.get("summary")
                if report_message:
                    return str(report_message).strip()
        response = read_json(self.agent_os.run_store.load_run_paths(run_id).root / "response.json")
        if isinstance(response, dict) and response.get("message"):
            return str(response["message"]).strip()
        request = detail.get("request") or {}
        return str(request.get("user_query") or "").strip()

    def _mark_background_failed(self, run_id: str, *, request_id: str, error: str) -> None:
        try:
            root = self.agent_os.run_store.load_run_paths(run_id).root
        except FileNotFoundError:
            return
        write_json(
            root / "run_state.json",
            {
                "run_id": run_id,
                "request_id": request_id,
                "status": "failed",
                "error": error,
            },
        )
        write_json(
            root / "response.json",
            ResponseEnvelope(
                request_id=request_id,
                run_id=run_id,
                status="failed",
                message="Agent OS background execution failed before completion.",
                progress=ProgressState(
                    current_phase="background_recovery",
                    active_agent="main_agent",
                    latest_event="background_failed",
                    status_message=error,
                    percent=100,
                ),
                errors=[error],
            ).model_dump(mode="json"),
        )

    def history(
        self,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for row in self.agent_os.run_store.list_runs():
            run_id = str(row.get("run_id") or "").strip()
            if not run_id:
                continue
            if not self._run_matches_scope(run_id, user_id=user_id, tenant_id=tenant_id):
                continue
            holding_reports = self._fund_holding_reports
            if holding_reports is not None and holding_reports.is_hidden_child(run_id):
                continue
            if holding_reports is not None and holding_reports.is_batch(run_id):
                rows.append(holding_reports.history_row(run_id))
                continue
            try:
                detail = self.agent_os.inspect_run(run_id)
            except (FileNotFoundError, KeyError, ValueError):
                continue
            if detail.get("mode") == "pending":
                state = dict(detail.get("snapshot") or {})
                rows.append(self._decorate_history_row(run_id, detail,
                    {
                        "run_id": run_id,
                        "request_id": state.get("request_id"),
                        "intent": state.get("intent") or "agent_os.pending",
                        "domain_id": None,
                        "mode": "pending",
                        "status": state.get("status", "received"),
                        "graph_id": None,
                        "graph_revision": None,
                        "updated_at": state.get("updated_at"),
                    }
                ))
                continue
            if detail.get("mode") == "core":
                state = MainAgentState.model_validate(detail["snapshot"])
                rows.append(self._decorate_history_row(run_id, detail,
                    {
                        "run_id": run_id,
                        "request_id": state.request_id,
                        "intent": state.intent or "core.conversation.answer",
                        "domain_id": None,
                        "mode": "core",
                        "status": state.status,
                        "graph_id": None,
                        "graph_revision": None,
                        "updated_at": state.updated_at,
                    }
                ))
                continue
            snapshot = ExecutionRunSnapshot.model_validate(detail["snapshot"])
            rows.append(self._decorate_history_row(run_id, detail,
                {
                    "run_id": run_id,
                    "request_id": snapshot.request_id,
                    "intent": self._intent(snapshot),
                    "domain_id": snapshot.graph.domain_id,
                    "mode": "graph",
                    "status": snapshot.status,
                    "graph_id": snapshot.graph.graph_id,
                    "graph_revision": snapshot.graph.revision,
                    "updated_at": snapshot.updated_at,
                }
            ))
        return rows

    def replay(
        self,
        run_id: str,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        self._assert_run_scope(run_id, user_id=user_id, tenant_id=tenant_id)
        holding_reports = self._fund_holding_reports
        if holding_reports is not None and holding_reports.is_batch(run_id):
            detail = holding_reports.run_detail(
                run_id,
                user_id=user_id,
                tenant_id=tenant_id,
            )
            return {**detail, "run_id": run_id, "events": self._execution_events(run_id)}
        detail = self.agent_os.inspect_run(run_id)
        if detail.get("mode") == "pending":
            return {
                "run_id": run_id,
                "mode": "pending",
                "request": detail.get("request"),
                "snapshot": detail.get("snapshot"),
                "artifacts": [],
                "task": detail.get("task"),
                "events": self._execution_events(run_id),
            }
        if detail.get("mode") == "core":
            return {
                "run_id": run_id,
                "mode": "core",
                "request": detail.get("request"),
                "snapshot": detail.get("snapshot"),
                "artifacts": detail.get("artifacts") or [],
                "turn": detail.get("turn"),
                "task": detail.get("task"),
                "events": self._execution_events(run_id),
            }
        snapshot = self.agent_os.replay(run_id)
        return {
            "run_id": run_id,
            "request": detail.get("request"),
            "snapshot": snapshot.model_dump(mode="json"),
            "turn": detail.get("turn"),
            "task": detail.get("task"),
            "artifacts": detail.get("artifacts") or [],
            "events": self._execution_events(run_id),
        }

    def detail(
        self,
        run_id: str,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        self._assert_run_scope(run_id, user_id=user_id, tenant_id=tenant_id)
        holding_reports = self._fund_holding_reports
        if holding_reports is not None and holding_reports.is_batch(run_id):
            return holding_reports.run_detail(
                run_id,
                user_id=user_id,
                tenant_id=tenant_id,
            )
        return self.agent_os.inspect_run(run_id)

    def artifacts(
        self,
        run_id: str,
        *,
        artifact_type: str | None = None,
        status: str | None = "active",
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        self._assert_run_scope(run_id, user_id=user_id, tenant_id=tenant_id)
        holding_reports = self._fund_holding_reports
        if holding_reports is not None and holding_reports.is_batch(run_id):
            return holding_reports.artifacts(
                run_id,
                artifact_type=artifact_type,
                status=status,
                user_id=user_id,
                tenant_id=tenant_id,
            )
        detail = self.agent_os.inspect_run(run_id)
        artifacts = list(detail.get("artifacts") or [])
        if artifact_type:
            artifacts = [
                artifact
                for artifact in artifacts
                if str(artifact.get("artifact_type") or "") == artifact_type
            ]
        if status:
            artifacts = [
                artifact
                for artifact in artifacts
                if str(artifact.get("status") or "") == status
            ]
        return {
            "run_id": run_id,
            "mode": detail.get("mode", "graph"),
            "status": (detail.get("turn") or {}).get("status")
            or (detail.get("snapshot") or {}).get("status"),
            "turn": detail.get("turn"),
            "artifacts": artifacts,
        }

    def events(
        self,
        run_id: str,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        self._assert_run_scope(run_id, user_id=user_id, tenant_id=tenant_id)
        holding_reports = self._fund_holding_reports
        if holding_reports is not None and holding_reports.is_batch(run_id):
            state = holding_reports.detail(run_id, user_id=user_id, tenant_id=tenant_id)
            events = self._execution_events(run_id)
            return {
                "run_id": run_id,
                "mode": "fund_holding_batch",
                "status": state["status"],
                "turn": None,
                "task_selection": {},
                "events": events,
                "event_counts": {
                    "events": len(events),
                    "distinct_event_types": len({str(event.get("event_type") or "") for event in events}),
                },
            }
        detail = self.agent_os.inspect_run(run_id)
        events = self._execution_events(run_id)
        turn = detail.get("turn") or {}
        status = (
            detail.get("snapshot", {}).get("status")
            if detail.get("mode") == "pending"
            else str(turn.get("status") or "")
            if turn
            else MainAgentState.model_validate(detail["snapshot"]).status
            if detail.get("mode") == "core"
            else self._snapshot(run_id).status
        )
        return {
            "run_id": run_id,
            "mode": detail.get("mode", "graph"),
            "status": status,
            "turn": turn,
            "task_selection": turn.get("task_selection") or {},
            "events": events,
            "event_counts": {
                "events": len(events),
                "distinct_event_types": len(
                    {str(event.get("event_type") or "") for event in events}
                ),
            },
        }

    def trace(
        self,
        run_id: str,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        self._assert_run_scope(run_id, user_id=user_id, tenant_id=tenant_id)
        holding_reports = self._fund_holding_reports
        if holding_reports is not None and holding_reports.is_batch(run_id):
            return holding_reports.trace(run_id, user_id=user_id, tenant_id=tenant_id)
        detail = self.agent_os.inspect_run(run_id)
        if detail.get("mode") == "pending":
            return {
                "run_id": run_id,
                "mode": "pending",
                "request": detail.get("request"),
                "snapshot": detail.get("snapshot"),
                "turn": detail.get("turn"),
                "artifacts": [],
                "dynamic_trace": self._execution_events(run_id),
                "event_counts": {"events": len(self._execution_events(run_id)), "graph_events": 0},
            }
        if detail.get("mode") == "core":
            events = self._execution_events(run_id)
            return {
                "run_id": run_id,
                "mode": "core",
                "request": detail.get("request"),
                "snapshot": detail.get("snapshot"),
                "turn": detail.get("turn"),
                "artifacts": detail.get("artifacts") or [],
                "dynamic_trace": events,
                "event_counts": {"events": len(events), "graph_events": 0},
            }
        snapshot = self._snapshot(run_id)
        events = self._execution_events(run_id)
        return {
            "run_id": run_id,
            "request": detail.get("request"),
            "snapshot": snapshot.model_dump(mode="json"),
            "turn": detail.get("turn"),
            "artifacts": detail.get("artifacts") or [],
            "dynamic_trace": events,
            "event_counts": {
                "events": len(events),
                "graph_events": len(events),
            },
        }

    def steps(
        self,
        run_id: str,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        self._assert_run_scope(run_id, user_id=user_id, tenant_id=tenant_id)
        holding_reports = self._fund_holding_reports
        if holding_reports is not None and holding_reports.is_batch(run_id):
            return holding_reports.steps(run_id, user_id=user_id, tenant_id=tenant_id)
        detail = self.agent_os.inspect_run(run_id)
        if detail.get("mode") == "pending":
            return {
                "run_id": run_id,
                "mode": "pending",
                "graph_id": None,
                "domain_id": None,
                "intent": (detail.get("snapshot") or {}).get("intent"),
                "strategy": "background_pending",
                "revision": None,
                "notes": [],
                "status": (detail.get("snapshot") or {}).get("status", "received"),
                "steps": [],
                "step_counts": {"total": 0, "active": 0, "completed": 0, "pending": 0},
                "status_counts": {},
            }
        if detail.get("mode") == "core":
            state = MainAgentState.model_validate(detail["snapshot"])
            turn = detail.get("turn") or {}
            status = str(turn.get("status") or state.status)
            row = {
                "step_index": 1,
                "node_id": "main_agent",
                "node_kind": "agent",
                "capability": "core.conversation.answer",
                "executor": "main_agent",
                "status": status,
                "depends_on": [],
                "tags": ["core", "main_agent"],
                "input_artifact_types": ["request"],
                "output_artifact_types": ["assistant_message"],
                "turn_id": turn.get("turn_id"),
                "task_type": turn.get("task_type") or "regular",
                "execution_mode": turn.get("execution_mode") or state.mode,
            }
            return {
                "run_id": run_id,
                "mode": "core",
                "graph_id": None,
                "domain_id": None,
                "intent": state.intent or "core.conversation.answer",
                "strategy": "core_agent_loop",
                "revision": None,
                "notes": [],
                "status": status,
                "steps": [row],
                "step_counts": {
                    "total": 1,
                    "active": 1 if status == "running" else 0,
                    "completed": 1 if status == "completed" else 0,
                    "pending": 0,
                },
                "status_counts": {status: 1},
                "turn": turn,
                "task_selection": turn.get("task_selection") or {},
            }
        snapshot = self._snapshot(run_id)
        rows = [
            {
                "step_index": index,
                "node_id": node.node_id,
                "node_kind": node.node_kind,
                "capability": node.capability,
                "executor": node.executor,
                "objective": node.objective,
                "status": (
                    snapshot.node_states[node.node_id].status
                    if node.node_id in snapshot.node_states
                    else node.status
                ),
                "depends_on": list(node.depends_on),
                "tags": list(node.tags),
                "input_artifact_types": [port.artifact_type for port in node.consumes],
                "output_artifact_types": [port.artifact_type for port in node.produces],
                "turn_id": (detail.get("turn") or {}).get("turn_id"),
                "task_type": (detail.get("turn") or {}).get("task_type") or "regular",
                "execution_mode": (detail.get("turn") or {}).get("execution_mode") or "graph",
            }
            for index, node in enumerate(snapshot.graph.nodes, start=1)
        ]
        status_counts = Counter(row["status"] for row in rows)
        return {
            "run_id": run_id,
            "turn": detail.get("turn"),
            "graph_id": snapshot.graph.graph_id,
            "domain_id": snapshot.graph.domain_id,
            "intent": self._intent(snapshot),
            "strategy": snapshot.graph.strategy,
            "revision": snapshot.graph.revision,
            "notes": list(snapshot.graph.notes),
            "status": snapshot.status,
            "steps": rows,
            "step_counts": {
                "total": len(rows),
                "active": status_counts.get("running", 0),
                "completed": status_counts.get("completed", 0),
                "pending": status_counts.get("pending", 0),
            },
            "status_counts": dict(status_counts),
            "task_selection": (detail.get("turn") or {}).get("task_selection") or {},
        }

    def metrics(
        self,
        run_id: str,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        self._assert_run_scope(run_id, user_id=user_id, tenant_id=tenant_id)
        holding_reports = self._fund_holding_reports
        if holding_reports is not None and holding_reports.is_batch(run_id):
            return holding_reports.metrics(run_id, user_id=user_id, tenant_id=tenant_id)
        detail = self.agent_os.inspect_run(run_id)
        event_counts = Counter(
            str(event.get("event_type") or "")
            for event in self._execution_events(run_id)
        )
        if detail.get("mode") == "pending":
            return {
                "run_id": run_id,
                "mode": "pending",
                "status": (detail.get("snapshot") or {}).get("status", "received"),
                "counts": {
                    "nodes": 0,
                    "subagents": 0,
                    "completed_nodes": 0,
                    "failed_nodes": 0,
                    "artifacts": 0,
                    "gates": 0,
                    "tool_calls": 0,
                },
                "tool_call_count": 0,
                "usage": {"tokens": 0, "cost": 0.0, "duration_seconds": 0.0},
                "node_status_counts": {},
                "event_type_counts": dict(event_counts),
                "graph_event_counts": {},
            }
        if detail.get("mode") == "core":
            state = MainAgentState.model_validate(detail["snapshot"])
            turn = detail.get("turn") or {}
            status = str(turn.get("status") or state.status)
            return {
                "run_id": run_id,
                "mode": "core",
                "status": status,
                "turn": turn,
                "counts": {
                    "nodes": 0,
                    "subagents": 0,
                    "completed_nodes": 0,
                    "failed_nodes": 1 if status == "failed" else 0,
                    "artifacts": len(detail.get("artifacts") or []),
                    "gates": 0,
                    "tool_calls": len(state.tool_call_ids),
                },
                "tool_call_count": len(state.tool_call_ids),
                "usage": {"tokens": 0, "cost": 0.0, "duration_seconds": 0.0},
                "node_status_counts": {},
                "event_type_counts": dict(event_counts),
                "graph_event_counts": {},
            }
        snapshot = self._snapshot(run_id)
        node_status_counts = Counter(
            state.status for state in snapshot.node_states.values()
        )
        return {
            "run_id": run_id,
            "status": snapshot.status,
            "counts": {
                "nodes": len(snapshot.node_states),
                "subagents": sum(
                    1 for node in snapshot.graph.nodes
                    if node.node_kind == "subagent"
                ),
                "completed_nodes": node_status_counts.get("completed", 0),
                "failed_nodes": node_status_counts.get("failed", 0),
                "artifacts": len(self.agent_os.inspect_run(run_id).get("artifacts") or []),
                "gates": len(snapshot.gate_evaluations),
                "tool_calls": snapshot.tool_call_count,
            },
            "tool_call_count": snapshot.tool_call_count,
            "usage": snapshot.usage.model_dump(mode="json"),
            "node_status_counts": dict(node_status_counts),
            "event_type_counts": dict(event_counts),
            "graph_event_counts": dict(event_counts),
        }

    def thread(
        self,
        run_id: str,
        thread_id: str,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        self._assert_run_scope(run_id, user_id=user_id, tenant_id=tenant_id)
        holding_reports = self._fund_holding_reports
        if holding_reports is not None and holding_reports.is_batch(run_id):
            state = holding_reports.detail(run_id, user_id=user_id, tenant_id=tenant_id)
            return {
                "run_id": run_id,
                "thread_id": thread_id,
                "mode": "fund_holding_batch",
                "scope": "fund_holding_batch",
                "scope_thread_ids": [thread_id],
                "snapshot": state,
            }
        detail = self.agent_os.inspect_run(run_id)
        if detail.get("mode") == "pending":
            request = detail.get("request") or {}
            expected_thread = request.get("thread_id") or (request.get("slots") or {}).get("chat_thread_id")
            if expected_thread and thread_id != expected_thread:
                raise KeyError(f"thread not found: {thread_id}")
            return {
                "run_id": run_id,
                "thread_id": expected_thread or thread_id,
                "scope": "background_pending",
                "scope_thread_ids": [expected_thread or thread_id],
                "snapshot": detail.get("snapshot"),
                "artifacts": [],
                "events": self._execution_events(run_id),
            }
        if detail.get("mode") == "core":
            state = MainAgentState.model_validate(detail["snapshot"])
            turn = detail.get("turn") or {}
            if thread_id != state.thread_id:
                raise KeyError(f"thread not found: {thread_id}")
            return {
                "run_id": run_id,
                "thread_id": thread_id,
                "scope": "core_agent_loop",
                "scope_thread_ids": [thread_id],
                "snapshot": state.model_dump(mode="json"),
                "turn": turn,
                "artifacts": detail.get("artifacts") or [],
                "events": self._execution_events(run_id),
            }
        snapshot = self._snapshot(run_id)
        if thread_id != snapshot.thread_id:
            raise KeyError(f"thread not found: {thread_id}")
        return self._thread_payload(run_id, snapshot)

    def thread_replay(
        self,
        run_id: str,
        thread_id: str,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        return self.thread(
            run_id,
            thread_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )

    def thread_metrics(
        self,
        run_id: str,
        thread_id: str,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        self._assert_run_scope(run_id, user_id=user_id, tenant_id=tenant_id)
        holding_reports = self._fund_holding_reports
        if holding_reports is not None and holding_reports.is_batch(run_id):
            payload = holding_reports.metrics(run_id, user_id=user_id, tenant_id=tenant_id)
            payload.update({"thread_id": thread_id, "scope": "fund_holding_batch"})
            return payload
        detail = self.agent_os.inspect_run(run_id)
        if detail.get("mode") == "core":
            state = MainAgentState.model_validate(detail["snapshot"])
            if thread_id != state.thread_id:
                raise KeyError(f"thread not found: {thread_id}")
            payload = self.metrics(run_id, user_id=user_id, tenant_id=tenant_id)
            payload.update(
                {
                    "thread_id": thread_id,
                    "scope": "core_agent_loop",
                    "scope_thread_ids": [thread_id],
                }
            )
            return payload
        snapshot = self._snapshot(run_id)
        if thread_id != snapshot.thread_id:
            raise KeyError(f"thread not found: {thread_id}")
        payload = self.metrics(run_id, user_id=user_id, tenant_id=tenant_id)
        payload.update(
            {
                "thread_id": thread_id,
                "scope": "execution_graph",
                "scope_thread_ids": [thread_id],
            }
        )
        return payload

    def subagent(
        self,
        run_id: str,
        invocation_id: str,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        self._assert_run_scope(run_id, user_id=user_id, tenant_id=tenant_id)
        detail = self.agent_os.inspect_run(run_id)
        if detail.get("mode") == "core":
            raise KeyError(f"subagent node not found: {invocation_id}")
        snapshot = self._snapshot(run_id)
        node = next(
            (node for node in snapshot.graph.nodes if node.node_id == invocation_id),
            None,
        )
        if node is None or node.node_kind != "subagent":
            raise KeyError(f"subagent node not found: {invocation_id}")
        state = snapshot.node_states[invocation_id]
        events = [
            event
            for event in self._execution_events(run_id)
            if str(event.get("node_id") or "") == invocation_id
        ]
        return {
            "run_id": run_id,
            "invocation_id": invocation_id,
            "node_id": node.node_id,
            "executor": node.executor,
            "capability": node.capability,
            "status": state.status,
            "attempt_count": state.attempt_count,
            "input_artifact_ids": list(state.input_artifact_ids),
            "output_artifact_ids": list(state.output_artifact_ids),
            "error": state.error,
            "events": events,
        }

    def resume(
        self,
        run_id: str,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        self._assert_run_scope(run_id, user_id=user_id, tenant_id=tenant_id)
        holding_reports = self._fund_holding_reports
        if holding_reports is not None and holding_reports.is_batch(run_id):
            state = holding_reports.detail(run_id, user_id=user_id, tenant_id=tenant_id)
            return {
                "run_id": run_id,
                "mode": "fund_holding_batch",
                "status": state["status"],
                "resumable": any(item["status"] in {"failed", "cancelled"} for item in state["holdings"]),
                "resume_from": {
                    "child_run_ids": [
                        item.get("child_run_id")
                        for item in state["holdings"]
                        if item["status"] in {"failed", "cancelled"}
                    ]
                },
            }
        detail = self.agent_os.inspect_run(run_id)
        if detail.get("mode") == "pending":
            return {
                "run_id": run_id,
                "mode": "pending",
                "status": (detail.get("snapshot") or {}).get("status", "received"),
                "resumable": True,
                "resume_from": None,
            }
        if detail.get("mode") == "core":
            state = MainAgentState.model_validate(detail["snapshot"])
            turn_status = str((detail.get("turn") or {}).get("status") or "")
            if state.status == "paused" or turn_status in {
                "running",
                "paused",
                "waiting_approval",
                "gated",
                "timed_out",
                "failed",
                "cancelled",
            }:
                result = self.agent_os.resume(run_id)
                return {
                    "run_id": run_id,
                    "mode": "core",
                    "status": result.status,
                    "resumable": False,
                    "resume_from": None,
                    "message": result.message,
                    "artifact_ids": list(result.artifact_ids),
                }
            return {
                "run_id": run_id,
                "mode": "core",
                "status": state.status,
                "resumable": False,
                "resume_from": None,
            }
        snapshot = self._snapshot(run_id)
        return {
            "run_id": run_id,
            "status": snapshot.status,
            "resumable": snapshot.status in {
                "running",
                "paused",
                "waiting_approval",
                "gated",
                "timed_out",
                "failed",
                "cancelled",
            },
            "resume_from": {
                "graph_revision": snapshot.graph.revision,
                "pending_nodes": [
                    node_id
                    for node_id, state in snapshot.node_states.items()
                    if state.status in {"pending", "waiting", "running"}
                ],
            },
        }

    def cancel(
        self,
        run_id: str,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        self._assert_run_scope(run_id, user_id=user_id, tenant_id=tenant_id)
        holding_reports = self._fund_holding_reports
        if holding_reports is not None and holding_reports.is_batch(run_id):
            state = holding_reports.cancel(run_id, user_id=user_id, tenant_id=tenant_id)
            return {
                "run_id": run_id,
                "mode": "fund_holding_batch",
                "status": state["status"],
                "message": "Cancellation requested",
                "cancellation_requested": True,
            }
        cancelled = self.agent_os.cancel(run_id)
        detail = self.agent_os.inspect_run(run_id)
        if detail.get("mode") == "core":
            state = MainAgentState.model_validate(detail["snapshot"])
            return {
                "run_id": run_id,
                "mode": "core",
                "status": state.status,
                "message": "Cancellation requested" if cancelled else "Run is not active",
                "cancellation_requested": cancelled,
            }
        snapshot = self._snapshot(run_id)
        return {
            "run_id": run_id,
            "status": snapshot.status,
            "message": "Cancellation requested" if cancelled else "Run is not active",
            "cancellation_requested": cancelled,
        }

    def pause(
        self,
        run_id: str,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        self._assert_run_scope(run_id, user_id=user_id, tenant_id=tenant_id)
        paused = self.agent_os.pause(run_id)
        detail = self.agent_os.inspect_run(run_id)
        turn = detail.get("turn") or {}
        return {
            "run_id": run_id,
            "status": turn.get("status") or (detail.get("snapshot") or {}).get("status"),
            "message": "Pause requested" if paused else "Run is not active",
            "pause_requested": paused,
        }

    def rerun_from(
        self,
        run_id: str,
        *,
        from_node: str,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> ResponseEnvelope:
        self._assert_run_scope(run_id, user_id=user_id, tenant_id=tenant_id)
        request = self._request_envelope(run_id)
        return self._response(request, self.agent_os.rerun_from(run_id, from_node))

    def _response(
        self,
        request: RequestEnvelope,
        result: ExecutionRunResult,
    ) -> ResponseEnvelope:
        snapshot = result.snapshot
        response_status = snapshot.status
        artifacts = self.agent_os.inspect_run(snapshot.run_id).get("artifacts") or []
        final_ids = set(result.final_artifact_ids)
        return ResponseEnvelope(
            request_id=snapshot.request_id,
            run_id=snapshot.run_id,
            intent=self._intent(snapshot),
            status=response_status,
            message=f"Agent OS run {snapshot.status}",
            progress=ProgressState(
                current_phase="execution_graph",
                latest_event=f"graph_run_{snapshot.status}",
                status_message=f"Agent OS run {snapshot.status}",
                percent=100 if snapshot.status in {"completed", "completed_partial", "failed", "cancelled", "timed_out", "gated"} else 0,
            ),
            result={
                "protocol": "agent_os",
                "graph_id": snapshot.graph.graph_id,
                "graph_revision": snapshot.graph.revision,
                "artifact_ids": list(result.artifact_ids),
                "final_artifact_ids": list(result.final_artifact_ids),
                "tool_call_count": snapshot.tool_call_count,
            },
            artifacts=[
                artifact for artifact in artifacts
                if str(artifact.get("artifact_id") or "") in final_ids
                or artifact.get("artifact_type") == "assistant_message"
            ],
        )

    def _snapshot(self, run_id: str) -> ExecutionRunSnapshot:
        detail = self.agent_os.inspect_run(run_id)
        if detail.get("mode") == "core":
            raise ValueError("core Agent Loop runs do not have an ExecutionGraph snapshot")
        return ExecutionRunSnapshot.model_validate(detail["snapshot"])

    def _run_matches_scope(
        self,
        run_id: str,
        *,
        user_id: str | None,
        tenant_id: str | None,
    ) -> bool:
        if user_id is None and tenant_id is None:
            return True
        try:
            request = read_json(self.agent_os.run_store.load_run_paths(run_id).request)
        except FileNotFoundError:
            return False
        if not isinstance(request, dict):
            return False
        if (
            request.get("user_id") in (None, "")
            and request.get("tenant_id") in (None, "")
            and user_id == "api_token"
            and tenant_id == "api_token"
        ):
            return True
        if user_id is not None and request.get("user_id") != user_id:
            return False
        if tenant_id is not None and request.get("tenant_id") != tenant_id:
            return False
        return True

    def _assert_run_scope(
        self,
        run_id: str,
        *,
        user_id: str | None,
        tenant_id: str | None,
    ) -> None:
        if not self._run_matches_scope(
            run_id,
            user_id=user_id,
            tenant_id=tenant_id,
        ):
            # Do not disclose whether a run exists in another principal's scope.
            raise FileNotFoundError(f"run not found: {run_id}")

    def _request_envelope(self, run_id: str) -> RequestEnvelope:
        root = self.agent_os.run_store.load_run_paths(run_id).root
        payload = read_json(root / "request.json")
        if not isinstance(payload, dict):
            raise ValueError(f"missing request payload: {run_id}")
        return RequestEnvelope.model_validate(payload)

    def _execution_events(self, run_id: str) -> list[dict[str, Any]]:
        root = self.agent_os.run_store.load_run_paths(run_id).root
        return [
            row for row in read_jsonl(root / "execution_events.jsonl")
            if isinstance(row, dict)
        ]

    def _thread_payload(
        self,
        run_id: str,
        snapshot: ExecutionRunSnapshot,
    ) -> dict[str, Any]:
        detail = self.agent_os.inspect_run(run_id)
        return {
            "run_id": run_id,
            "thread_id": snapshot.thread_id,
            "scope": "execution_graph",
            "scope_thread_ids": [snapshot.thread_id],
            "snapshot": snapshot.model_dump(mode="json"),
            "artifacts": detail.get("artifacts") or [],
            "events": self._execution_events(run_id),
        }

    @staticmethod
    def _intent(snapshot: ExecutionRunSnapshot) -> str:
        return str(
            snapshot.graph.metadata.get("intent")
            or snapshot.graph.intent
            or f"domain:{snapshot.graph.domain_id}"
        )


__all__ = ["AgentOSRunService"]
