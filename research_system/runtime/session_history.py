from __future__ import annotations

from pathlib import Path
from typing import Any

from research_system.runtime.display import build_display_summary
from research_system.runtime.jsonl import read_json, read_jsonl, write_json


class SessionHistory:
    def __init__(self, run_root: Path) -> None:
        self.run_root = run_root

    def replay(self) -> dict[str, Any]:
        result_payload = read_json(self.run_root / "result.json")
        summary_payload = read_json(self.run_root / "summary.json")
        return {
            "run_id": self.run_root.name,
            "request": read_json(self.run_root / "request.json"),
            "events": read_jsonl(self.run_root / "events.jsonl"),
            "blackboard": read_jsonl(self.run_root / "blackboard.jsonl"),
            "result": result_payload,
            "summary": summary_payload,
            "display_summary": build_display_summary(
                result_payload=result_payload,
                summary_payload=summary_payload,
            ),
        }

    def write_result(self, result: dict[str, Any], summary: dict[str, Any]) -> None:
        write_json(self.run_root / "result.json", result)
        write_json(self.run_root / "summary.json", summary)
