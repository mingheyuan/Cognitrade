from __future__ import annotations

from typing import Any


class SessionHistorySupervisorState:
    def event_payloads(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            payload = event.get("payload")
            if isinstance(payload, dict):
                payloads.append(dict(payload))
        return payloads

    def supervisor_state(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        next_actions_by_node_id: dict[str, dict[str, Any]] = {}
        completed_action_ids: set[str] = set()
        skipped_action_ids: set[str] = set()
        latest_supervisor_note: str | None = None
        for payload in self.event_payloads(events):
            completed_ids = payload.get("completed_action_ids")
            if isinstance(completed_ids, list):
                for action_id in completed_ids:
                    text = str(action_id or "").strip()
                    if text:
                        completed_action_ids.add(text)
            skipped_ids = payload.get("skipped_action_ids")
            if isinstance(skipped_ids, list):
                for action_id in skipped_ids:
                    text = str(action_id or "").strip()
                    if text:
                        skipped_action_ids.add(text)
            next_actions = payload.get("next_actions")
            if isinstance(next_actions, list):
                for item in next_actions:
                    if not isinstance(item, dict):
                        continue
                    node_id = str(item.get("node_id") or "").strip()
                    if node_id:
                        next_actions_by_node_id[node_id] = dict(item)
            notes = payload.get("notes")
            if isinstance(notes, list) and notes:
                first_note = str(notes[0] or "").strip()
                if first_note:
                    latest_supervisor_note = first_note
        return {
            "next_actions_by_node_id": next_actions_by_node_id,
            "completed_action_ids": completed_action_ids,
            "skipped_action_ids": skipped_action_ids,
            "latest_supervisor_note": latest_supervisor_note,
        }


__all__ = ["SessionHistorySupervisorState"]
