from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_system.runtime.storage.jsonl import read_json, read_jsonl, write_json
from research_system.schemas.turn_runtime import (
    TaskSelection,
    TaskSnapshot,
    TurnConfig,
    TurnSnapshot,
)

MIGRATION_ID = "canonical_run_state_v1"
RUN_STATUSES = {
    "received",
    "ready",
    "running",
    "paused",
    "waiting_approval",
    "completed",
    "completed_partial",
    "gated",
    "failed",
    "cancelled",
    "timed_out",
}


def migrate_workspace(workspace_root: Path, *, dry_run: bool = False) -> dict[str, Any]:
    workspace_root = workspace_root.resolve()
    runs_root = workspace_root / "runs"
    report: dict[str, Any] = {
        "migration_id": MIGRATION_ID,
        "workspace_root": str(workspace_root),
        "scanned_roots": 0,
        "created_turn_states": 0,
        "created_task_states": 0,
        "created_requests": 0,
        "removed_aliases": 0,
        "normalized_task_usage": 0,
        "conflicts": [],
        "dry_run": dry_run,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    if runs_root.exists():
        for root in _state_roots(runs_root):
            report["scanned_roots"] += 1
            _migrate_state_root(root, report=report, dry_run=dry_run)

    marker = workspace_root / ".migrations" / f"{MIGRATION_ID}.json"
    if not dry_run:
        marker.parent.mkdir(parents=True, exist_ok=True)
        write_json(marker, report)
    return report


def _state_roots(runs_root: Path) -> list[Path]:
    names = {
        "request.json",
        "agent_os_request.json",
        "run_state.json",
        "main_agent_state.json",
        "execution_snapshot.json",
        "turn_state.json",
        "turn.json",
        "task_state.json",
        "task.json",
    }
    roots = {
        path.parent
        for path in runs_root.rglob("*")
        if path.is_file() and path.name in names
    }
    return sorted(roots)


def _migrate_state_root(root: Path, *, report: dict[str, Any], dry_run: bool) -> None:
    request = read_json(root / "request.json")
    intake = read_json(root / "agent_os_request.json")
    if not isinstance(request, dict) and isinstance(intake, dict):
        nested_request = intake.get("request")
        if isinstance(nested_request, dict):
            request = nested_request
            report["created_requests"] += 1
            if not dry_run:
                write_json(root / "request.json", request)

    turn = _canonical_or_alias(
        root,
        canonical_name="turn_state.json",
        alias_name="turn.json",
        report=report,
        dry_run=dry_run,
    )
    if not isinstance(turn, dict) and isinstance(request, dict):
        turn = _synthesize_turn(root, request=request, intake=intake)
        TurnSnapshot.model_validate(turn)
        report["created_turn_states"] += 1
        if not dry_run:
            write_json(root / "turn_state.json", turn)
    elif isinstance(turn, dict):
        TurnSnapshot.model_validate(turn)

    task = _canonical_or_alias(
        root,
        canonical_name="task_state.json",
        alias_name="task.json",
        report=report,
        dry_run=dry_run,
    )
    if isinstance(task, dict):
        task = _normalize_legacy_task_usage(
            root,
            task=task,
            report=report,
            dry_run=dry_run,
        )
    if not isinstance(task, dict) and isinstance(turn, dict):
        task = _synthesize_task(root, turn=turn)
        TaskSnapshot.model_validate(task)
        report["created_task_states"] += 1
        if not dry_run:
            write_json(root / "task_state.json", task)
    elif isinstance(task, dict):
        TaskSnapshot.model_validate(task)


def _normalize_legacy_task_usage(
    root: Path,
    *,
    task: dict[str, Any],
    report: dict[str, Any],
    dry_run: bool,
) -> dict[str, Any]:
    """Remove a legacy duplicate only when its canonical audit copy matches."""

    if "usage" not in task:
        return task
    child_turn = read_json(root / "child_turn.json")
    child_usage = child_turn.get("usage") if isinstance(child_turn, dict) else None
    task_usage = task.get("usage")
    if child_usage != task_usage:
        raise ValueError(
            f"cannot normalize task usage at {root}: "
            "child_turn.json is missing an identical audit copy"
        )
    normalized = dict(task)
    normalized.pop("usage", None)
    TaskSnapshot.model_validate(normalized)
    report["normalized_task_usage"] += 1
    if not dry_run:
        write_json(root / "task_state.json", normalized)
    return normalized


def _canonical_or_alias(
    root: Path,
    *,
    canonical_name: str,
    alias_name: str,
    report: dict[str, Any],
    dry_run: bool,
) -> dict[str, Any] | None:
    canonical_path = root / canonical_name
    alias_path = root / alias_name
    canonical = read_json(canonical_path)
    alias = read_json(alias_path)
    if not isinstance(canonical, dict) and isinstance(alias, dict):
        canonical = alias
        counter = (
            "created_turn_states"
            if canonical_name == "turn_state.json"
            else "created_task_states"
        )
        report[counter] += 1
        if not dry_run:
            write_json(canonical_path, canonical)
    elif isinstance(canonical, dict) and isinstance(alias, dict) and canonical != alias:
        report["conflicts"].append(
            {
                "root": str(root),
                "canonical": canonical_name,
                "alias": alias_name,
                "resolution": "kept_canonical",
            }
        )
    if alias_path.exists():
        report["removed_aliases"] += 1
        if not dry_run:
            alias_path.unlink()
    return canonical if isinstance(canonical, dict) else None


def _synthesize_turn(
    root: Path,
    *,
    request: dict[str, Any],
    intake: dict[str, Any] | None,
) -> dict[str, Any]:
    main_state = read_json(root / "main_agent_state.json")
    graph_state = read_json(root / "execution_snapshot.json")
    run_state = read_json(root / "run_state.json")
    response = read_json(root / "response.json")
    state_candidates = [
        item
        for item in (response, main_state, graph_state, run_state)
        if isinstance(item, dict)
    ]
    mode = "graph" if isinstance(graph_state, dict) else str(
        (main_state or {}).get("mode")
        or (run_state or {}).get("mode")
        or (intake or {}).get("mode")
        or "core"
    )
    if mode not in {"core", "task", "graph"}:
        mode = "core"
    raw_status = next(
        (
            str(item.get("status"))
            for item in state_candidates
            if str(item.get("status") or "") in RUN_STATUSES
        ),
        "received",
    )
    selection = TaskSelection(
        execution_mode=mode,
        graph_required=mode == "graph",
        requires_persistence=True,
        reason_codes=["workspace_migration"],
    )
    events = read_jsonl(root / "execution_events.jsonl")
    latest_event = max(
        (item for item in events if isinstance(item, dict)),
        key=lambda item: int(item.get("sequence") or 0),
        default={},
    )
    run_id = str(
        (run_state or {}).get("run_id")
        or (main_state or {}).get("run_id")
        or (graph_state or {}).get("run_id")
        or root.name
    )
    thread_id = str(
        request.get("thread_id")
        or (main_state or {}).get("thread_id")
        or (graph_state or {}).get("thread_id")
        or (intake or {}).get("thread_id")
        or f"run:{run_id}"
    )
    task_state_path = root / "task_state.json"
    return TurnSnapshot(
        turn_id=str((run_state or {}).get("active_turn_id") or f"turn_{run_id}"),
        run_id=run_id,
        thread_id=thread_id,
        request_id=str(request.get("request_id") or (run_state or {}).get("request_id") or run_id),
        user_id=request.get("user_id"),
        tenant_id=request.get("tenant_id"),
        execution_mode=mode,
        status=raw_status,
        config=TurnConfig(execution_mode=mode, selection=selection),
        task_selection=selection,
        usage=dict((graph_state or {}).get("usage") or {}),
        sequence=int(latest_event.get("sequence") or 0),
        task_state_ref=str(task_state_path),
        graph_state_ref=str(root / "execution_snapshot.json") if isinstance(graph_state, dict) else None,
        last_event_id=str(latest_event.get("event_id") or "").strip() or None,
        error=str(
            (main_state or {}).get("error")
            or (run_state or {}).get("error_summary")
            or ""
        ).strip() or None,
    ).model_dump(mode="json")


def _synthesize_task(root: Path, *, turn: dict[str, Any]) -> dict[str, Any]:
    main_state = read_json(root / "main_agent_state.json")
    return TaskSnapshot(
        task_type=str(turn.get("task_type") or "regular"),
        execution_mode=str(turn.get("execution_mode") or "core"),
        active_turn_id=str(turn.get("turn_id") or "") or None,
        transcript=list((main_state or {}).get("transcript") or []),
        follow_up_count=int((main_state or {}).get("follow_up_count") or 0),
        pending_approval_ids=list((main_state or {}).get("pending_approval_ids") or []),
        stop_hook_active=bool((main_state or {}).get("stop_hook_active")),
        hook_iteration=int((main_state or {}).get("hook_iteration") or 0),
        last_assistant_message=(main_state or {}).get("assistant_message"),
        continuation=(main_state or {}).get("continuation"),
        graph_state_ref=turn.get("graph_state_ref"),
    ).model_dump(mode="json")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate Cognitrade Run data to canonical request/turn/task filenames."
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "workspace",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    report = migrate_workspace(args.workspace, dry_run=args.dry_run)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
