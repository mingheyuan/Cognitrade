from __future__ import annotations

from pathlib import Path

from research_system.runtime.display import build_display_summary
from research_system.runtime.id_utils import generate_ulid
from research_system.runtime.jsonl import read_json, write_json
from research_system.schemas.protocol import RequestEnvelope
from research_system.schemas.runtime import RunStateSnapshot


class RunPaths:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.request = root / "request.json"
        self.task_plan = root / "task_plan.json"
        self.run_state = root / "run_state.json"
        self.run_control = root / "run_control.json"
        self.checkpoint = root / "checkpoint.json"
        self.events = root / "events.jsonl"
        self.logs = root / "logs.jsonl"
        self.blackboard = root / "blackboard.jsonl"
        self.mailbox = root / "mailbox" / "messages.jsonl"
        self.artifacts = root / "artifacts"
        self.result = root / "result.json"
        self.summary = root / "summary.json"


class RunStore:
    def __init__(self, runs_root: Path) -> None:
        self.runs_root = runs_root
        self.runs_root.mkdir(parents=True, exist_ok=True)

    def create_run(self, request: RequestEnvelope, intent: str) -> tuple[str, RunPaths, RunStateSnapshot]:
        run_id = generate_ulid()
        root = self.runs_root / run_id
        paths = RunPaths(root)
        paths.root.mkdir(parents=True, exist_ok=True)
        paths.artifacts.mkdir(parents=True, exist_ok=True)
        paths.mailbox.parent.mkdir(parents=True, exist_ok=True)
        write_json(paths.request, request.model_dump(mode="json"))
        snapshot = RunStateSnapshot(
            run_id=run_id,
            request_id=request.request_id,
            intent=intent,
            status="received",
        )
        write_json(paths.run_state, snapshot.model_dump(mode="json"))
        return run_id, paths, snapshot

    def list_runs(self) -> list[dict]:
        rows: list[dict] = []
        for run_dir in sorted(self.runs_root.iterdir(), reverse=True):
            if not run_dir.is_dir():
                continue
            summary_payload = read_json(run_dir / "summary.json")
            result_payload = read_json(run_dir / "result.json")
            payload = summary_payload or read_json(run_dir / "run_state.json")
            if payload is None:
                payload = {
                    "status": "unknown",
                    "message": "run metadata unavailable",
                }
            payload["run_id"] = run_dir.name
            display_summary = build_display_summary(
                result_payload=result_payload,
                summary_payload=summary_payload or payload,
            )
            if display_summary:
                payload["display_summary"] = display_summary
            rows.append(payload)
        return rows

    def load_run_paths(self, run_id: str) -> RunPaths:
        root = self.runs_root / run_id
        if not root.exists():
            raise FileNotFoundError(f"run not found: {run_id}")
        return RunPaths(root)
