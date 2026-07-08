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
        action_plan_patches = self.trace_rebuilder.action_plan_patches(debug_payload, events)
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
            "action_plan_patches": action_plan_patches,
            "wait_trace": wait_trace,
            "threads": self.thread_states(),
            "thread_tree": self.thread_tree(),
            "event_counts": {
                "events": len(events),
                "subagent_trace": len(subagent_trace),
                "dynamic_trace": len(dynamic_trace),
                "action_plan_patches": len(action_plan_patches),
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
        request_payload = read_json(self.run_root / "request.json") or {}
        result_payload = read_json(self.run_root / "result.json") or {}
        summary_payload = read_json(self.run_root / "summary.json") or {}
        events = read_jsonl(self.run_root / "events.jsonl")
        items = self._latest_items()
        threads = self.thread_states()
        return self._build_metrics_payload(
            request_payload=request_payload,
            result_payload=result_payload,
            summary_payload=summary_payload,
            events=events,
            items=items,
            threads=threads,
            thread_tree=self.thread_tree(),
        )

    def thread_metrics(self, thread_id: str, *, include_descendants: bool = True) -> dict[str, Any]:
        request_payload = read_json(self.run_root / "request.json") or {}
        result_payload = read_json(self.run_root / "result.json") or {}
        summary_payload = read_json(self.run_root / "summary.json") or {}
        threads = self.thread_states()
        normalized_thread_id, scoped_threads, thread_tree, scope_thread_ids = self._thread_scope_payload(
            thread_id=thread_id,
            include_descendants=include_descendants,
            threads=threads,
        )
        events = self._filter_events_for_threads(
            read_jsonl(self.run_root / "events.jsonl"),
            scope_thread_ids=scope_thread_ids,
        )
        items = self._filter_items_for_threads(scope_thread_ids)
        payload = self._build_metrics_payload(
            request_payload=request_payload,
            result_payload=result_payload,
            summary_payload=summary_payload,
            events=events,
            items=items,
            threads=scoped_threads,
            thread_tree=thread_tree,
        )
        payload["thread_id"] = normalized_thread_id
        payload["scope_thread_ids"] = sorted(scope_thread_ids)
        payload["scope"] = "thread_subtree" if include_descendants else "thread"
        return payload

    def _build_metrics_payload(
        self,
        *,
        request_payload: dict[str, Any],
        result_payload: dict[str, Any],
        summary_payload: dict[str, Any],
        events: list[dict[str, Any]],
        items: list[dict[str, Any]],
        threads: list[dict[str, Any]],
        thread_tree: list[dict[str, Any]],
    ) -> dict[str, Any]:
        subagent_trace = self.trace_rebuilder.subagent_trace({}, events)

        subagent_items = [row for row in items if str(row.get("item_type") or "") == "subagent"]
        action_items = [row for row in items if str(row.get("item_type") or "") == "action"]
        degradation_items = [row for row in items if str(row.get("item_type") or "") == "degradation"]
        llm_items = [
            row for row in subagent_items
            if bool((row.get("payload") if isinstance(row.get("payload"), dict) else {}).get("llm_used"))
        ]

        methodology_search_total = 0
        methodology_search_hits = 0
        qdrant_search_total = 0
        qdrant_search_hits = 0
        for item in subagent_trace:
            notes = [str(note) for note in list(item.get("notes") or [])]
            subagent_type = str(item.get("subagent_type") or "")
            if subagent_type == "methodology_memory_recaller":
                methodology_search_total += 1
                if self._int_note_value(notes, prefix="row_count=") > 0:
                    methodology_search_hits += 1
            if subagent_type == "qdrant_evidence_searcher":
                qdrant_search_total += 1
                if self._int_note_value(notes, prefix="hit_count=") > 0:
                    qdrant_search_hits += 1

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
            "request": request_payload,
            "summary": summary_payload,
            "display_summary": build_display_summary(
                result_payload=result_payload,
                summary_payload=summary_payload,
            ),
            "counts": {
                "events": len(events),
                "items": len(items),
                "threads": len(threads),
                "turns": sum(len(list(row.get("turns") or [])) for row in threads),
                "actions": len(action_items),
                "subagents": len(subagent_items),
                "llm_calls": len(llm_items),
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
                "qdrant_hit_rate": (
                    round(qdrant_search_hits / qdrant_search_total, 4)
                    if qdrant_search_total
                    else None
                ),
            },
            "latency_ms": {
                "subagents": durations,
                "actions": action_durations,
                "per_subagent": per_subagent,
                "per_action": per_action,
            },
            "turn_usage": self._turn_usage(threads=threads, items=items),
            "fanout": fanout,
            "threads": threads,
            "thread_tree": thread_tree,
            "degrade_reason_counts": degrade_reason_counts,
            "event_type_counts": event_type_counts,
            "dag_event_counts": {
                key: value
                for key, value in event_type_counts.items()
                if str(key).startswith("dag_") or str(key).startswith("subagent_dag_")
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
        payload = self.trace_rebuilder.build_subagent_payload(
            run_id=self.run_root.name,
            trace_payload=trace_payload,
            events_payload=events_payload,
            invocation_id=invocation_id,
        )
        item = self._subagent_item(invocation_id)
        if item is None:
            return payload
        item_payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        payload["item"] = item
        payload["item_id"] = str(item.get("item_id") or "").strip() or None
        payload["status"] = item.get("status") or payload.get("status")
        payload["thread_id"] = payload.get("thread_id") or item.get("thread_id")
        payload["turn_id"] = payload.get("turn_id") or item.get("turn_id")
        payload["phase"] = payload.get("phase") or item.get("phase")
        payload["agent"] = payload.get("agent") or item.get("agent")
        payload["child_thread_id"] = payload.get("child_thread_id") or item_payload.get("child_thread_id")
        payload["child_turn_id"] = payload.get("child_turn_id") or item_payload.get("child_turn_id")
        payload["degrade_reason"] = payload.get("degrade_reason") or item.get("degrade_reason")
        payload["created_at"] = item.get("created_at")
        payload["updated_at"] = item.get("updated_at")
        payload["duration_ms"] = self._duration_ms(item)
        if "llm_used" in item_payload:
            payload["llm_used"] = bool(item_payload.get("llm_used"))
        if isinstance(item_payload.get("output_keys"), list):
            payload["output_keys"] = list(item_payload.get("output_keys") or [])
        if (not payload.get("notes")) and isinstance(item_payload.get("notes"), list):
            payload["notes"] = list(item_payload.get("notes") or [])
        return payload

    def thread(self, thread_id: str) -> dict[str, Any]:
        return self._build_thread_payload(thread_id=thread_id, include_descendants=False)

    def thread_replay(self, thread_id: str, *, include_descendants: bool = True) -> dict[str, Any]:
        return self._build_thread_payload(thread_id=thread_id, include_descendants=include_descendants)

    def _build_thread_payload(
        self,
        *,
        thread_id: str,
        include_descendants: bool,
    ) -> dict[str, Any]:
        normalized_thread_id = str(thread_id or "").strip()
        if not normalized_thread_id:
            raise ValueError("thread_id is required")
        threads = self.thread_states()
        (
            normalized_thread_id,
            scoped_threads,
            thread_tree,
            scope_thread_ids,
        ) = self._thread_scope_payload(
            thread_id=normalized_thread_id,
            include_descendants=include_descendants,
            threads=threads,
        )
        thread_payload = next(
            row
            for row in scoped_threads
            if str(row.get("thread_id") or "").strip() == normalized_thread_id
        )
        request_payload = read_json(self.run_root / "request.json") or {}
        result_payload = read_json(self.run_root / "result.json") or {}
        summary_payload = read_json(self.run_root / "summary.json") or {}
        filtered_events = self._filter_events_for_threads(
            read_jsonl(self.run_root / "events.jsonl"),
            scope_thread_ids=scope_thread_ids,
        )
        event_timeline = self.trace_rebuilder.event_timeline(filtered_events)
        subagent_trace = self.trace_rebuilder.subagent_trace({}, filtered_events)
        dynamic_trace = self.trace_rebuilder.dynamic_trace({}, filtered_events)
        action_plan_patches = self.trace_rebuilder.action_plan_patches({}, filtered_events)
        wait_trace = self.trace_rebuilder.wait_trace(filtered_events)
        items = self._filter_items_for_threads(scope_thread_ids)
        blackboard = self._filter_blackboard_for_threads(scope_thread_ids)
        metrics = self.thread_metrics(
            normalized_thread_id,
            include_descendants=include_descendants,
        )
        return {
            "run_id": self.run_root.name,
            "thread_id": normalized_thread_id,
            "scope": "thread_subtree" if include_descendants else "thread",
            "scope_thread_ids": sorted(scope_thread_ids),
            "request": request_payload,
            "summary": summary_payload,
            "display_summary": build_display_summary(
                result_payload=result_payload,
                summary_payload=summary_payload,
            ),
            "thread": thread_payload,
            "threads": scoped_threads,
            "thread_tree": thread_tree,
            "events": event_timeline,
            "blackboard": blackboard,
            "subagent_trace": subagent_trace,
            "dynamic_trace": dynamic_trace,
            "action_plan_patches": action_plan_patches,
            "wait_trace": wait_trace,
            "items": items,
            "metrics": metrics,
            "event_counts": {
                "events": len(event_timeline),
                "subagent_trace": len(subagent_trace),
                "dynamic_trace": len(dynamic_trace),
                "action_plan_patches": len(action_plan_patches),
                "wait_trace": len(wait_trace),
                "items": len(items),
                "blackboard": len(blackboard),
                "threads": len(scoped_threads),
            },
        }

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
        return self._build_thread_tree(self.thread_states())

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
        indexed: dict[str, dict[str, Any]] = {}
        for row in threads:
            thread_id = str(row.get("thread_id") or "").strip()
            if not thread_id:
                continue
            indexed[thread_id] = row
        children_by_parent: dict[str, list[str]] = {}
        for row in threads:
            thread_id = str(row.get("thread_id") or "").strip()
            parent = str(row.get("parent_thread_id") or "").strip()
            if thread_id and parent and parent in indexed:
                children_by_parent.setdefault(parent, []).append(thread_id)

        def depth(thread_id: str) -> int:
            children = children_by_parent.get(thread_id) or []
            if not children:
                return 0
            return 1 + max(depth(child_id) for child_id in children)

        max_depth = 0
        for thread_id, row in indexed.items():
            parent = str(row.get("parent_thread_id") or "").strip()
            if parent and parent in indexed:
                continue
            max_depth = max(max_depth, depth(thread_id))
        return {
            "max_depth": max_depth,
            "per_parent_child_count": {
                parent: len(children)
                for parent, children in children_by_parent.items()
            },
        }

    def _turn_usage(self, *, threads: list[dict[str, Any]], items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        turn_index: dict[str, dict[str, Any]] = {}
        for thread in threads:
            thread_id = str(thread.get("thread_id") or "").strip()
            for turn in list(thread.get("turns") or []):
                if not isinstance(turn, dict):
                    continue
                turn_id = str(turn.get("turn_id") or "").strip()
                if not turn_id:
                    continue
                turn_index[turn_id] = {
                    "thread_id": thread_id,
                    "intent": turn.get("intent"),
                    "status": turn.get("status"),
                    "llm_calls": 0,
                    "subagents": 0,
                    "actions": 0,
                    "child_threads_spawned": 0,
                    "degradations": 0,
                    "degraded_subagents": 0,
                    "failed_subagents": 0,
                    "subagent_latency_ms": 0,
                    "action_latency_ms": 0,
                    "degrade_reason_counts": {},
                }
        for row in items:
            turn_id = str(row.get("turn_id") or "").strip()
            if not turn_id:
                continue
            usage = turn_index.setdefault(
                turn_id,
                {
                    "thread_id": str(row.get("thread_id") or "").strip() or None,
                    "intent": None,
                    "status": None,
                    "llm_calls": 0,
                    "subagents": 0,
                    "actions": 0,
                    "child_threads_spawned": 0,
                    "degradations": 0,
                    "degraded_subagents": 0,
                    "failed_subagents": 0,
                    "subagent_latency_ms": 0,
                    "action_latency_ms": 0,
                    "degrade_reason_counts": {},
                },
            )
            item_type = str(row.get("item_type") or "")
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            duration_ms = self._duration_ms(row) or 0
            if item_type == "subagent":
                usage["subagents"] += 1
                usage["subagent_latency_ms"] += duration_ms
                if bool(payload.get("llm_used")):
                    usage["llm_calls"] += 1
                status = str(row.get("status") or "").strip()
                if status == "degraded":
                    usage["degraded_subagents"] += 1
                elif status == "failed":
                    usage["failed_subagents"] += 1
            elif item_type == "action":
                usage["actions"] += 1
                usage["action_latency_ms"] += duration_ms
            elif item_type == "thread":
                usage["child_threads_spawned"] += 1
            elif item_type == "degradation":
                usage["degradations"] += 1
            degrade_reason = str(row.get("degrade_reason") or "").strip()
            if degrade_reason:
                counts = usage.get("degrade_reason_counts")
                if not isinstance(counts, dict):
                    counts = {}
                    usage["degrade_reason_counts"] = counts
                counts[degrade_reason] = int(counts.get(degrade_reason) or 0) + 1
        return turn_index

    def _event_matches_thread(self, event: dict[str, Any], thread_id: str) -> bool:
        return self._event_matches_threads(event, {thread_id})

    def _event_matches_threads(self, event: dict[str, Any], thread_ids: set[str]) -> bool:
        if not isinstance(event, dict):
            return False
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if not isinstance(payload, dict):
            payload = {}
        nested_payload = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        candidate_ids = {
            str(payload.get("thread_id") or "").strip(),
            str(payload.get("child_thread_id") or "").strip(),
            str(nested_payload.get("thread_id") or "").strip(),
            str(nested_payload.get("child_thread_id") or "").strip(),
        }
        candidate_ids.discard("")
        return any(candidate_id in thread_ids for candidate_id in candidate_ids)

    def _int_note_value(self, notes: list[str], *, prefix: str) -> int:
        raw_value = next((note for note in notes if note.startswith(prefix)), "")
        if not raw_value:
            return 0
        try:
            return int(raw_value.split("=", 1)[1])
        except ValueError:
            return 0

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

    def _subagent_item(self, invocation_id: str) -> dict[str, Any] | None:
        normalized_invocation_id = str(invocation_id or "").strip()
        if not normalized_invocation_id:
            return None
        for row in reversed(self._latest_items()):
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            if str(payload.get("invocation_id") or "").strip() == normalized_invocation_id:
                return row
        return None

    def _thread_scope_payload(
        self,
        *,
        thread_id: str,
        include_descendants: bool,
        threads: list[dict[str, Any]],
    ) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], set[str]]:
        normalized_thread_id = str(thread_id or "").strip()
        if not normalized_thread_id:
            raise ValueError("thread_id is required")
        indexed = {
            str(row.get("thread_id") or "").strip(): row
            for row in threads
            if str(row.get("thread_id") or "").strip()
        }
        if normalized_thread_id not in indexed:
            raise KeyError(f"thread not found: {normalized_thread_id}")
        scope_thread_ids = self._thread_scope_ids(
            threads=threads,
            root_thread_id=normalized_thread_id,
            include_descendants=include_descendants,
        )
        scoped_threads = [
            row
            for row in threads
            if str(row.get("thread_id") or "").strip() in scope_thread_ids
        ]
        thread_tree = self._build_thread_tree(scoped_threads)
        return normalized_thread_id, scoped_threads, thread_tree, scope_thread_ids

    def _thread_scope_ids(
        self,
        *,
        threads: list[dict[str, Any]],
        root_thread_id: str,
        include_descendants: bool,
    ) -> set[str]:
        normalized_root_thread_id = str(root_thread_id or "").strip()
        if not normalized_root_thread_id:
            return set()
        if not include_descendants:
            return {normalized_root_thread_id}
        children_by_parent: dict[str, list[str]] = {}
        for row in threads:
            thread_id = str(row.get("thread_id") or "").strip()
            parent_thread_id = str(row.get("parent_thread_id") or "").strip()
            if thread_id and parent_thread_id:
                children_by_parent.setdefault(parent_thread_id, []).append(thread_id)
        resolved = {normalized_root_thread_id}
        stack = [normalized_root_thread_id]
        while stack:
            current = stack.pop()
            for child_id in children_by_parent.get(current, []):
                if child_id in resolved:
                    continue
                resolved.add(child_id)
                stack.append(child_id)
        return resolved

    def _build_thread_tree(self, threads: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not threads:
            return []
        indexed = {
            str(row.get("thread_id") or "").strip(): dict(row)
            for row in threads
            if str(row.get("thread_id") or "").strip()
        }
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

    def _filter_events_for_threads(
        self,
        events: list[dict[str, Any]],
        *,
        scope_thread_ids: set[str],
    ) -> list[dict[str, Any]]:
        return [
            row
            for row in events
            if self._event_matches_threads(row, scope_thread_ids)
        ]

    def _filter_items_for_threads(self, scope_thread_ids: set[str]) -> list[dict[str, Any]]:
        return [
            row
            for row in self._latest_items()
            if str(row.get("thread_id") or "").strip() in scope_thread_ids
        ]

    def _filter_blackboard_for_threads(self, scope_thread_ids: set[str]) -> list[dict[str, Any]]:
        return [
            row
            for row in read_jsonl(self.run_root / "blackboard.jsonl")
            if isinstance(row, dict) and str(row.get("thread_id") or "").strip() in scope_thread_ids
        ]


__all__ = ["SessionHistory"]
