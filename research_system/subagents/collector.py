from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import akshare as ak
from pydantic import BaseModel, ConfigDict, Field

from research_system.adapters.vendors.akshare_sector_adapter import AkShareSectorAdapter
from research_system.adapters.vendors.akshare_utils import call_with_retry, normalize_value
from research_system.agents._subagent_invoker import RuntimeBoundSubagentInvoker
from research_system.schemas.subagents import SubagentInvocation
from research_system.runtime.storage.jsonl import read_json, read_jsonl
from research_system.runtime.platform.skill_runtime import SkillRuntime
from research_system.subagents.base import SubagentRuntimePolicy
from research_system.subagents.registry import SubagentRegistry


def _normalize_query(query: str) -> dict[str, str | None]:
    return {
        "symbol": _extract_symbol(query),
        "jsonl_path": _extract_jsonl_path(query),
        "sector_query": _extract_sector_query(query),
        "investment_cycle": _extract_investment_cycle(query),
    }


def _extract_symbol(query: str) -> str | None:
    match = re.search(r"\b(\d{6})\b", query)
    if match:
        return match.group(1)
    return None


def _extract_jsonl_path(query: str) -> str | None:
    match = re.search(r'([A-Za-z]:[\\/][^\\/:*?"<>|\r\n]+(?:[\\/][^\\/:*?"<>|\r\n]+)*\.jsonl)', query)
    if match:
        return match.group(1)
    return None


def _extract_sector_query(query: str) -> str | None:
    stripped = query.strip()
    if not stripped:
        return None
    markers = ("板块", "行业", "主题", "赛道", "概念", "sector", "theme", "industry")
    lowered = stripped.lower()
    if any(marker in lowered for marker in ("sector", "theme", "industry")):
        return stripped
    if any(marker in stripped for marker in markers[:5]):
        return stripped
    if _extract_symbol(stripped):
        return None
    return None


def _extract_investment_cycle(query: str) -> str | None:
    lowered = query.lower()
    if "短线" in query or "short" in lowered:
        return "short_term"
    if "长期" in query or "long" in lowered:
        return "long_term"
    if "中线" in query or "中期" in query or "mid" in lowered:
        return "mid_term"
    return None


class StockSnapshotCollectorInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    symbol: str | None = None
    snapshot_types: list[str] = Field(default_factory=list)
    allow_partial: bool = False


class StockSnapshotCollectorOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshots: list[dict[str, Any]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class SnapshotSkillRunnerInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    symbol: str | None = None
    snapshot_type: str


class SnapshotSkillRunnerOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_type: str
    snapshot: dict[str, Any] | None = None
    degraded: bool = False
    degrade_reason: str | None = None


class SectorSnapshotCollectorInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    include_policy_snapshot: bool = False


class SectorSnapshotCollectorOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot: dict[str, Any]
    related_snapshots: list[dict[str, Any]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class CandidateUniverseBuilderInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    sector_query: str | None = None
    top_n: int = 12


class CandidateUniverseBuilderOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_universe: list[dict[str, Any]] = Field(default_factory=list)
    sector_context: dict[str, Any] | None = None
    notes: list[str] = Field(default_factory=list)


class ReviewTargetLoaderInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    constraints: dict[str, Any] = Field(default_factory=dict)
    query: str


class ReviewTargetLoaderOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_target_run_id: str
    review_source_entries: list[dict[str, Any]] = Field(default_factory=list)
    review_source_result: dict[str, Any] = Field(default_factory=dict)
    sector: str | None = None
    symbol: str | None = None
    notes: list[str] = Field(default_factory=list)


class SnapshotSkillRunnerSubagent:
    input_model = SnapshotSkillRunnerInputModel
    output_model = SnapshotSkillRunnerOutputModel
    runtime_policy = SubagentRuntimePolicy(allow_parallel=True, max_concurrency=8)

    def __init__(self, *, snapshot_type: str, skill_name: str, skill_runtime: SkillRuntime) -> None:
        self.subagent_type = f"{snapshot_type}_snapshot_skill_runner"
        self.snapshot_type = snapshot_type
        self.skill_name = skill_name
        self.skill_runtime = skill_runtime

    def run(self, *, query: str, symbol: str | None, snapshot_type: str) -> dict:
        try:
            snapshot = self.skill_runtime.run(self.skill_name, query=query, symbol=symbol)
        except Exception as exc:
            return {
                "snapshot_type": snapshot_type,
                "snapshot": None,
                "degraded": True,
                "degrade_reason": type(exc).__name__,
            }
        return {
            "snapshot_type": snapshot_type,
            "snapshot": snapshot,
            "degraded": False,
            "degrade_reason": None,
        }


class StockSnapshotCollectorSubagent(RuntimeBoundSubagentInvoker):
    subagent_type = "stock_snapshot_collector"
    input_model = StockSnapshotCollectorInputModel
    output_model = StockSnapshotCollectorOutputModel
    runtime_policy = SubagentRuntimePolicy(allow_parallel=True, max_concurrency=8)

    def __init__(self, skill_runtime: SkillRuntime) -> None:
        self.optional_snapshot_types = {"lhb", "capital_flow", "sentiment", "events"}
        self.skill_map = {
            "market": "market_snapshot_skill",
            "fundamental": "fundamental_snapshot_skill",
            "notice": "notice_snapshot_skill",
            "lhb": "lhb_snapshot_skill",
            "capital_flow": "capital_flow_snapshot_skill",
            "sentiment": "sentiment_snapshot_skill",
            "events": "events_snapshot_skill",
        }
        self._skill_runtime = skill_runtime
        self.registry = SubagentRegistry(
            [
                SnapshotSkillRunnerSubagent(
                    snapshot_type=snapshot_type,
                    skill_name=skill_name,
                    skill_runtime=skill_runtime,
                )
                for snapshot_type, skill_name in self.skill_map.items()
            ]
        )

    @property
    def skill_runtime(self) -> SkillRuntime:
        return self._skill_runtime

    @skill_runtime.setter
    def skill_runtime(self, value: SkillRuntime) -> None:
        self._skill_runtime = value
        for subagent in self.registry._subagents.values():
            if hasattr(subagent, "skill_runtime"):
                setattr(subagent, "skill_runtime", value)

    def run(
        self,
        *,
        query: str,
        symbol: str | None,
        snapshot_types: list[str] | None = None,
        allow_partial: bool = False,
    ) -> dict:
        snapshots = []
        notes = [f"stock_snapshot_types={','.join(snapshot_types or ['market', 'fundamental', 'notice'])}"]
        invocations: list[SubagentInvocation] = []
        ordered_snapshot_types = list(snapshot_types or ["market", "fundamental", "notice"])
        for snapshot_type in ordered_snapshot_types:
            skill_name = self.skill_map.get(snapshot_type)
            if not skill_name:
                if allow_partial and snapshot_type in self.optional_snapshot_types:
                    notes.append(f"snapshot_skipped={snapshot_type}:unsupported_mapping")
                    continue
                raise ValueError(f"unsupported snapshot_type: {snapshot_type}")
            invocations.append(
                SubagentInvocation(
                    subagent_type=f"{snapshot_type}_snapshot_skill_runner",
                    objective=f"Collect {snapshot_type} stock snapshot.",
                    inputs={
                        "query": query,
                        "symbol": symbol,
                        "snapshot_type": snapshot_type,
                    },
                    context={
                        "task_name": "collect:stock_snapshot_collector",
                        "agent_path": "collector/stock_snapshot_collector",
                    },
                )
            )
        runs = self._invoke_subagents_concurrently(invocations, max_workers=len(invocations))
        for snapshot_type, (outputs, _) in zip(
            [item for item in ordered_snapshot_types if item in self.skill_map],
            runs,
            strict=False,
        ):
            snapshot = outputs.get("snapshot")
            degrade_reason = str(outputs.get("degrade_reason") or "").strip() or "snapshot_failed"
            if snapshot is not None:
                snapshots.append(snapshot)
                continue
            if allow_partial and snapshot_type in self.optional_snapshot_types:
                notes.append(f"snapshot_skipped={snapshot_type}:{degrade_reason}")
                continue
            raise RuntimeError(f"snapshot collection failed for {snapshot_type}: {degrade_reason}")
        return {
            "snapshots": snapshots,
            "notes": notes,
        }


class SectorSnapshotCollectorSubagent:
    subagent_type = "sector_snapshot_collector"
    input_model = SectorSnapshotCollectorInputModel
    output_model = SectorSnapshotCollectorOutputModel
    runtime_policy = SubagentRuntimePolicy()

    def __init__(self, skill_runtime: SkillRuntime) -> None:
        self.skill_runtime = skill_runtime

    def run(self, *, query: str, include_policy_snapshot: bool = False) -> dict:
        sector_snapshot = self.skill_runtime.run("sector_snapshot_skill", query=query)
        related_snapshots = []
        if include_policy_snapshot:
            policy_snapshot = self.skill_runtime.run(
                "policy_snapshot_skill",
                query=query,
                sector=sector_snapshot.get("sector"),
                constituents=(sector_snapshot.get("facts") or {}).get("top_constituents") or [],
            )
            related_snapshots.append(policy_snapshot)
        return {
            "snapshot": sector_snapshot,
            "related_snapshots": related_snapshots,
            "notes": [f"sector={sector_snapshot.get('sector') or query}"],
        }


class CandidateUniverseBuilderSubagent:
    subagent_type = "candidate_universe_builder"
    input_model = CandidateUniverseBuilderInputModel
    output_model = CandidateUniverseBuilderOutputModel
    runtime_policy = SubagentRuntimePolicy()

    def __init__(self, skill_runtime: SkillRuntime) -> None:
        self.skill_runtime = skill_runtime
        self.sector_adapter = AkShareSectorAdapter()

    def run(
        self,
        *,
        query: str,
        sector_query: str | None,
        top_n: int = 12,
    ) -> dict:
        sector_context = None
        candidates: list[dict] = []
        if sector_query:
            sector_match = self.sector_adapter.resolve_sector(query=sector_query)
            sector_snapshot = self.skill_runtime.run("sector_snapshot_skill", query=sector_match["sector_name"])
            facts = sector_snapshot.get("facts") or {}
            top_constituents = facts.get("top_constituents") or []
            candidates = [
                {
                    "symbol": str(item.get("code") or "").strip(),
                    "name": str(item.get("name") or "").strip(),
                    "sector": sector_snapshot.get("sector"),
                    "theme": sector_snapshot.get("theme"),
                    "latest_price": item.get("latest_price"),
                    "latest_pct_change": item.get("pct_change"),
                    "latest_amount": item.get("amount"),
                    "turnover_rate": item.get("turnover_rate"),
                    "universe_source": "sector_constituents",
                }
                for item in top_constituents[:top_n]
                if str(item.get("code") or "").strip()
            ]
            sector_context = {
                "sector": sector_snapshot.get("sector"),
                "theme": sector_snapshot.get("theme"),
                "sector_snapshot": sector_snapshot,
            }
        else:
            spot_df = call_with_retry(ak.stock_zh_a_spot)
            filtered_rows: list[dict] = []
            for row in spot_df.to_dict("records"):
                code = str(row.get("代码") or row.get("浠ｇ爜") or "").strip()
                name = str(row.get("名称") or row.get("鍚嶇О") or "").strip()
                if not code or not name:
                    continue
                if "ST" in name.upper():
                    continue
                amount = self._to_float(row.get("成交额") or row.get("鎴愪氦棰?"))
                price = self._to_float(row.get("最新价") or row.get("鏈€鏂颁环"))
                pct_change = self._to_float(row.get("涨跌幅") or row.get("娑ㄨ穼骞?"))
                if amount is None or price is None:
                    continue
                filtered_rows.append(
                    {
                        "symbol": code[-6:] if len(code) >= 6 else code,
                        "name": name,
                        "latest_price": price,
                        "latest_pct_change": pct_change,
                        "latest_amount": amount,
                        "universe_source": "liquid_a_share",
                    }
                )
            filtered_rows.sort(
                key=lambda item: (
                    item.get("latest_amount") or 0.0,
                    item.get("latest_pct_change") or 0.0,
                ),
                reverse=True,
            )
            candidates = filtered_rows[:top_n]

        return {
            "candidate_universe": candidates,
            "sector_context": sector_context,
            "notes": [
                f"candidate_count={len(candidates)}",
                f"sector_context={'yes' if sector_context else 'no'}",
            ],
        }

    def _to_float(self, value):
        if value in (None, "", False):
            return None
        try:
            return float(normalize_value(value))
        except Exception:
            return None


class ReviewTargetLoaderSubagent:
    subagent_type = "review_target_loader"
    input_model = ReviewTargetLoaderInputModel
    output_model = ReviewTargetLoaderOutputModel
    runtime_policy = SubagentRuntimePolicy()

    def __init__(self, runs_root: Path | None = None) -> None:
        self.runs_root = runs_root

    def run(self, *, constraints: dict, query: str) -> dict:
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
            "notes": [f"review_target_run_id={target_run_id}"],
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
        if _normalize_query(query).get("symbol"):
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
        return _normalize_query(query).get("symbol")
