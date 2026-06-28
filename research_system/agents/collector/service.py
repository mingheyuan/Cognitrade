from __future__ import annotations

from pathlib import Path

from research_system.runtime.jsonl import read_json, read_jsonl
from research_system.runtime.skill_runtime import SkillRuntime


class CollectorService:
    agent_name = "collector"

    def __init__(self, skill_runtime: SkillRuntime, runs_root: Path | None = None) -> None:
        self.skill_runtime = skill_runtime
        self.runs_root = runs_root

    def run(self, *, intent: str, query: str, constraints: dict | None = None) -> dict:
        constraints = constraints or {}
        if "[wait_external]" in query:
            return {
                "requires_wait": True,
                "sources": [],
                "snapshots": [],
            }
        symbol = self._extract_symbol(query)
        snapshot_payloads = []
        source_payload = {
            "title": f"{intent} input query",
            "content": query,
            "source_type": "user_query",
            "symbol": symbol,
        }

        if intent == "stock_check":
            snapshot_payloads.append(self.skill_runtime.run("market_snapshot_skill", query=query, symbol=symbol))
            snapshot_payloads.append(self.skill_runtime.run("fundamental_snapshot_skill", query=query, symbol=symbol))
            snapshot_payloads.append(self.skill_runtime.run("notice_snapshot_skill", query=query, symbol=symbol))
        elif intent == "sector_research":
            sector_snapshot = self.skill_runtime.run("sector_snapshot_skill", query=query)
            source_payload["sector"] = sector_snapshot.get("sector")
            source_payload["theme"] = sector_snapshot.get("theme")
            snapshot_payloads.append(sector_snapshot)
        elif intent == "distill_kol_logic":
            source_payload["source_type"] = "distill_request"
            source_payload["path"] = self._extract_jsonl_path(query)
            source_payload["content"] = query
            if source_payload["path"]:
                source_payload["title"] = "distill_kol_logic input request"
        elif intent == "review_thesis":
            review_payload = self._load_review_target(constraints=constraints, query=query)
            source_payload.update(
                {
                    "title": "review_thesis input request",
                    "source_type": "review_request",
                    "review_target_run_id": review_payload["review_target_run_id"],
                    "review_source_entries": review_payload["review_source_entries"],
                    "review_source_result": review_payload["review_source_result"],
                }
            )
            sector = review_payload.get("sector")
            resolved_symbol = review_payload.get("symbol")
            if sector:
                source_payload["sector"] = sector
                snapshot_payloads.append(self.skill_runtime.run("sector_snapshot_skill", query=sector))
            if resolved_symbol:
                source_payload["symbol"] = resolved_symbol
                snapshot_payloads.append(self.skill_runtime.run("market_snapshot_skill", query=resolved_symbol, symbol=resolved_symbol))
                snapshot_payloads.append(self.skill_runtime.run("fundamental_snapshot_skill", query=resolved_symbol, symbol=resolved_symbol))
                snapshot_payloads.append(self.skill_runtime.run("notice_snapshot_skill", query=resolved_symbol, symbol=resolved_symbol))
        else:
            snapshot_payloads.append(self.skill_runtime.run("market_snapshot_skill", query=query, symbol=symbol))

        return {
            "sources": [source_payload],
            "snapshots": snapshot_payloads,
        }

    def _extract_symbol(self, query: str) -> str | None:
        import re

        match = re.search(r"\b(\d{6})\b", query)
        if match:
            return match.group(1)
        return None

    def _extract_jsonl_path(self, query: str) -> str | None:
        import re

        match = re.search(r"([A-Za-z]:[\\/][^\\/:*?\"<>|\r\n]+(?:[\\/][^\\/:*?\"<>|\r\n]+)*\.jsonl)", query)
        if match:
            return match.group(1)
        return None

    def _load_review_target(self, *, constraints: dict, query: str) -> dict:
        if self.runs_root is None:
            raise ValueError("runs_root is required for review_thesis collection")

        target_run_id = str(constraints.get("review_target_run_id") or "").strip()
        if not target_run_id:
            raise ValueError("review_thesis requires review_target_run_id")

        run_root = self.runs_root / target_run_id
        blackboard_path = run_root / "blackboard.jsonl"
        result_path = run_root / "result.json"
        if not blackboard_path.exists():
            raise FileNotFoundError(f"review target blackboard not found: {target_run_id}")

        source_entries = read_jsonl(blackboard_path)
        source_result = read_json(result_path) or {}
        sector = self._extract_sector_from_entries(source_entries=source_entries, source_result=source_result, query=query)
        symbol = self._extract_symbol_from_entries(source_entries=source_entries, source_result=source_result, query=query)
        return {
            "review_target_run_id": target_run_id,
            "review_source_entries": source_entries,
            "review_source_result": source_result,
            "sector": sector,
            "symbol": symbol,
        }

    def _extract_sector_from_entries(self, *, source_entries: list[dict], source_result: dict, query: str) -> str | None:
        for entry in source_entries:
            payload = entry.get("payload") or {}
            sector = payload.get("sector")
            if sector:
                return str(sector)
        report = source_result.get("report") or {}
        for item in report.get("evidence") or []:
            summary = str(item.get("summary") or "")
            marker = "sector snapshot for "
            if marker in summary:
                return summary.split(marker, 1)[1].splitlines()[0].strip()
        if self._extract_symbol(query):
            return None
        return query.strip() or None

    def _extract_symbol_from_entries(self, *, source_entries: list[dict], source_result: dict, query: str) -> str | None:
        for entry in source_entries:
            payload = entry.get("payload") or {}
            symbol = payload.get("symbol")
            if symbol:
                return str(symbol)
        report = source_result.get("report") or {}
        for item in report.get("evidence") or []:
            summary = str(item.get("summary") or "")
            marker = "market snapshot for "
            if marker in summary:
                return summary.split(marker, 1)[1].splitlines()[0].strip()
        return self._extract_symbol(query)
