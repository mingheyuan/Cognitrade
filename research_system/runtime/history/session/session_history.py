from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from research_system.runtime.history.session.steps.step_builder import SessionHistoryStepBuilder
from research_system.runtime.history.session.trace.trace_rebuilder import SessionHistoryTraceRebuilder
from research_system.runtime.presentation.display import build_display_summary
from research_system.runtime.storage.jsonl import read_json, read_jsonl, write_json


class SessionHistory:
    def __init__(self, run_root: Path) -> None:
        self.run_root = run_root
        self.trace_rebuilder = SessionHistoryTraceRebuilder()
        self.step_builder = SessionHistoryStepBuilder()

    def replay(self) -> dict[str, Any]:
        result_payload = read_json(self.run_root / "result.json")
        summary_payload = read_json(self.run_root / "summary.json")
        return {
            "run_id": self.run_root.name,
            "request": read_json(self.run_root / "request.json"),
            "events": read_jsonl(self.run_root / "events.jsonl"),
            "blackboard": read_jsonl(self.run_root / "blackboard.jsonl"),
            "threads": self.thread_states(),
            "thread_tree": self.thread_tree(),
            "result": result_payload,
            "summary": summary_payload,
            "display_summary": build_display_summary(
                result_payload=result_payload,
                summary_payload=summary_payload,
            ),
        }

    def trace(self) -> dict[str, Any]:
        request_payload = read_json(self.run_root / "request.json") or {}
        result_payload = read_json(self.run_root / "result.json") or {}
        summary_payload = read_json(self.run_root / "summary.json") or {}
        events = read_jsonl(self.run_root / "events.jsonl")
        debug_payload = self.trace_rebuilder.debug_payload(result_payload)
        subagent_trace = self.trace_rebuilder.subagent_trace(debug_payload, events)
        dynamic_trace = self.trace_rebuilder.dynamic_trace(debug_payload, events)
        wait_trace = self.trace_rebuilder.wait_trace(events)
        return {
            "run_id": self.run_root.name,
            "request": request_payload,
            "summary": summary_payload,
            "display_summary": build_display_summary(
                result_payload=result_payload,
                summary_payload=summary_payload,
            ),
            "subagent_trace": subagent_trace,
            "dynamic_trace": dynamic_trace,
            "wait_trace": wait_trace,
            "threads": self.thread_states(),
            "thread_tree": self.thread_tree(),
            "event_counts": {
                "events": len(events),
                "subagent_trace": len(subagent_trace),
                "dynamic_trace": len(dynamic_trace),
                "wait_trace": len(wait_trace),
            },
        }

    def events(self) -> dict[str, Any]:
        request_payload = read_json(self.run_root / "request.json") or {}
        result_payload = read_json(self.run_root / "result.json") or {}
        summary_payload = read_json(self.run_root / "summary.json") or {}
        events = read_jsonl(self.run_root / "events.jsonl")
        timeline = self.trace_rebuilder.event_timeline(events)
        return {
            "run_id": self.run_root.name,
            "request": request_payload,
            "summary": summary_payload,
            "display_summary": build_display_summary(
                result_payload=result_payload,
                summary_payload=summary_payload,
            ),
            "events": timeline,
            "threads": self.thread_states(),
            "thread_tree": self.thread_tree(),
            "event_counts": {
                "events": len(timeline),
                "distinct_event_types": len({str(row.get("event_type") or "") for row in timeline}),
            },
        }

    def metrics(self) -> dict[str, Any]:
        events = read_jsonl(self.run_root / "events.jsonl")
        items = self._latest_items()
        threads = self.thread_states()
        trace_payload = self.trace()
        subagent_trace = list(trace_payload.get("subagent_trace") or [])

        subagent_items = [row for row in items if str(row.get("item_type") or "") == "subagent"]
        action_items = [row for row in items if str(row.get("item_type") or "") == "action"]
        degradation_items = [row for row in items if str(row.get("item_type") or "") == "degradation"]

        methodology_search_total = 0
        methodology_search_hits = 0
        for item in subagent_trace:
            if str(item.get("subagent_type") or "") != "methodology_memory_recaller":
                continue
            methodology_search_total += 1
            notes = [str(note) for note in list(item.get("notes") or [])]
            row_count_note = next((note for note in notes if note.startswith("row_count=")), "")
            try:
                row_count = int(row_count_note.split("=", 1)[1]) if row_count_note else 0
            except ValueError:
                row_count = 0
            if row_count > 0:
                methodology_search_hits += 1

        child_threads = [row for row in threads if str(row.get("parent_thread_id") or "").strip()]
        degraded_subagents = [
            row for row in subagent_items
            if str(row.get("status") or "") == "degraded"
        ]
        durations = self._duration_stats(subagent_items)
        action_durations = self._duration_stats(action_items)
        per_subagent = self._duration_stats_by_key(subagent_items, key_field="title")
        per_action = self._duration_stats_by_key(action_items, key_field="title")
        degrade_reason_counts = self._degrade_reason_counts(items)
        event_type_counts = self._count_by_key(events, "event_type")
        fanout = self._fanout_metrics(threads)

        return {
            "run_id": self.run_root.name,
            "counts": {
                "events": len(events),
                "items": len(items),
                "actions": len(action_items),
                "subagents": len(subagent_items),
                "degradations": len(degradation_items),
                "child_threads": len(child_threads),
            },
            "rates": {
                "fallback_rate": round(len(degraded_subagents) / len(subagent_items), 4) if subagent_items else 0.0,
                "memory_hit_rate": (
                    round(methodology_search_hits / methodology_search_total, 4)
                    if methodology_search_total
                    else None
                ),
                "qdrant_hit_rate": None,
            },
            "latency_ms": {
                "subagents": durations,
                "actions": action_durations,
                "per_subagent": per_subagent,
                "per_action": per_action,
            },
            "fanout": fanout,
            "degrade_reason_counts": degrade_reason_counts,
            "event_type_counts": event_type_counts,
            "dag_event_counts": {
                key: value
                for key, value in event_type_counts.items()
                if str(key).startswith("dag_")
            },
        }

    def steps(self) -> dict[str, Any]:
        request_payload = read_json(self.run_root / "request.json") or {}
        result_payload = read_json(self.run_root / "result.json") or {}
        summary_payload = read_json(self.run_root / "summary.json") or {}
        task_plan_payload = read_json(self.run_root / "task_plan.json") or {}
        events = read_jsonl(self.run_root / "events.jsonl")
        return self.step_builder.build_payload(
            run_id=self.run_root.name,
            request_payload=request_payload,
            result_payload=result_payload,
            summary_payload=summary_payload,
            task_plan_payload=task_plan_payload,
            events=events,
            threads=self.thread_states(),
            thread_tree=self.thread_tree(),
        )

    def subagent(self, invocation_id: str) -> dict[str, Any]:
        trace_payload = self.trace()
        events_payload = self.events()
        return self.trace_rebuilder.build_subagent_payload(
            run_id=self.run_root.name,
            trace_payload=trace_payload,
            events_payload=events_payload,
            invocation_id=invocation_id,
        )

    def write_result(self, result: dict[str, Any], summary: dict[str, Any]) -> None:
        write_json(self.run_root / "result.json", result)
        write_json(self.run_root / "summary.json", summary)

    def thread_states(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        root_thread = read_json(self.run_root / "thread_state.json") or {}
        root_turn = read_json(self.run_root / "turn_state.json") or {}
        root_thread_id = str(root_thread.get("thread_id") or "").strip()
        if root_thread_id:
            root_thread["turns"] = [root_turn] if root_turn else []
            rows.append(root_thread)

        threads_root = self.run_root / "threads"
        if not threads_root.exists():
            return rows
        for path in sorted(threads_root.glob("*.thread.json")):
            payload = read_json(path) or {}
            thread_id = str(payload.get("thread_id") or "").strip()
            if not thread_id:
                continue
            turn_rows = []
            for turn_path in sorted(threads_root.glob(f"{thread_id}.turn.*.json")):
                turn_payload = read_json(turn_path) or {}
                if turn_payload:
                    turn_rows.append(turn_payload)
            payload["turns"] = turn_rows
            rows.append(payload)
        return rows

    def thread_tree(self) -> list[dict[str, Any]]:
        rows = self.thread_states()
        if not rows:
            return []
        indexed = {str(row.get("thread_id") or ""): dict(row) for row in rows if str(row.get("thread_id") or "").strip()}
        children_by_parent: dict[str, list[dict[str, Any]]] = {}
        roots: list[dict[str, Any]] = []
        for row in indexed.values():
            row["children"] = []
        for row in indexed.values():
            parent_thread_id = str(row.get("parent_thread_id") or "").strip()
            if not parent_thread_id or parent_thread_id not in indexed:
                roots.append(row)
                continue
            children_by_parent.setdefault(parent_thread_id, []).append(row)
        for thread_id, children in children_by_parent.items():
            indexed[thread_id]["children"] = children
        return roots

    def _duration_stats(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        samples = [sample for sample in (self._duration_ms(row) for row in items) if sample is not None]
        if not samples:
            return {"count": 0, "avg": None, "max": None, "p50": None, "p95": None}
        ordered = sorted(samples)
        return {
            "count": len(ordered),
            "avg": round(sum(ordered) / len(ordered), 2),
            "max": max(ordered),
            "p50": ordered[len(ordered) // 2],
            "p95": ordered[min(len(ordered) - 1, max(int(len(ordered) * 0.95) - 1, 0))],
        }

    def _duration_stats_by_key(self, items: list[dict[str, Any]], *, key_field: str) -> dict[str, dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in items:
            key = str(row.get(key_field) or "").strip() or "unknown"
            grouped.setdefault(key, []).append(row)
        return {
            key: self._duration_stats(rows)
            for key, rows in grouped.items()
        }

    def _duration_ms(self, row: dict[str, Any]) -> int | None:
        created_at = self._parse_datetime(row.get("created_at"))
        updated_at = self._parse_datetime(row.get("updated_at"))
        if created_at is None or updated_at is None:
            return None
        return max(int((updated_at - created_at).total_seconds() * 1000), 0)

    def _degrade_reason_counts(self, items: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in items:
            reason = str(row.get("degrade_reason") or "").strip()
            if not reason:
                continue
            counts[reason] = counts.get(reason, 0) + 1
        return counts

    def _count_by_key(self, rows: list[dict[str, Any]], key: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in rows:
            value = str(row.get(key) or "").strip() or "unknown"
            counts[value] = counts.get(value, 0) + 1
        return counts

    def _fanout_metrics(self, threads: list[dict[str, Any]]) -> dict[str, Any]:
        children_by_parent: dict[str, list[str]] = {}
        indexed: dict[str, dict[str, Any]] = {}
        for row in threads:
            thread_id = str(row.get("thread_id") or "").strip()
            if not thread_id:
                continue
            indexed[thread_id] = row
            parent = str(row.get("parent_thread_id") or "").strip()
            if parent:
                children_by_parent.setdefault(parent, []).append(thread_id)

        def depth(thread_id: str) -> int:
            children = children_by_parent.get(thread_id) or []
            if not children:
                return 0
            return 1 + max(depth(child_id) for child_id in children)

        max_depth = 0
        for thread_id, row in indexed.items():
            parent = str(row.get("parent_thread_id") or "").strip()
            if parent:
                continue
            max_depth = max(max_depth, depth(thread_id))
        return {
            "max_depth": max_depth,
            "per_parent_child_count": {
                parent: len(children)
                for parent, children in children_by_parent.items()
            },
        }

    def _parse_datetime(self, value: object) -> datetime | None:
        token = str(value or "").strip()
        if not token:
            return None
        normalized = token[:-1] + "+00:00" if token.endswith("Z") else token
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None

    def _latest_items(self) -> list[dict[str, Any]]:
        indexed: dict[str, dict[str, Any]] = {}
        for row in read_jsonl(self.run_root / "items.jsonl"):
            if not isinstance(row, dict):
                continue
            item_id = str(row.get("item_id") or "").strip()
            if not item_id:
                continue
            indexed[item_id] = row
        return list(indexed.values())


__all__ = ["SessionHistory"]
