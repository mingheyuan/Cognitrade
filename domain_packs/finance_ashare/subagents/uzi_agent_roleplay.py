"""Agent-driven UZI Deep panel role-play owned by the Finance DomainPack.

The latest UZI workflow treats the deterministic rule engine as reference
material only for ``deep`` research.  This module preserves that boundary
without importing or executing the external UZI project: persona YAML is
vendored as Pack data, Agent OS supplies the configured LLM provider, and the
validated overrides are written into the panel Artifact.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
import re
from threading import Event
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
import yaml

from domain_packs.finance_ashare.subagents.uzi_panel_profiles import get_profile
from domain_packs.finance_ashare.subagents.uzi_llm_support import parse_uzi_structured


LATEST_UZI_SOURCE_COMMIT = "0acf25122cabf493e7bc442faff62ceceb7f2f02"
PERSONAS_DIR = (
    Path(__file__).resolve().parents[1]
    / "vendor"
    / "uzi_3_9_2"
    / "skill"
    / "personas"
)
_SIGNALS = {"bullish", "bearish", "neutral", "skip", "insufficient_evidence"}
_CONCRETE_FACT = re.compile(r"\d|数据缺口|能力圈|不适合|非A股|非 A 股|未取得|缺少|行业|市场")
_GROUP_BATCHES: tuple[tuple[str, frozenset[str]], ...] = (
    ("A+B · 经典价值与成长", frozenset({"A", "B"})),
    ("C · 宏观对冲", frozenset({"C"})),
    ("D+E · 技术趋势与中国价投", frozenset({"D", "E"})),
    ("F · A股游资", frozenset({"F"})),
    ("G+H+I · 量化、科技领袖与AI瓶颈", frozenset({"G", "H", "I"})),
)


class AgentRoleplayVote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    investor_id: str = Field(min_length=1)
    signal: Literal["bullish", "bearish", "neutral", "skip", "insufficient_evidence"]
    score: float = Field(ge=0, le=100)
    headline: str = Field(min_length=8, max_length=240)
    reasoning: str = Field(min_length=20, max_length=1_500)
    comment: str | None = Field(default=None, max_length=1_500)
    verdict: str | None = Field(default=None, max_length=100)
    override_rule_engine: bool
    override_reason: str | None = Field(default=None, max_length=600)

    @model_validator(mode="after")
    def validate_override(self) -> "AgentRoleplayVote":
        if self.override_rule_engine and not str(self.override_reason or "").strip():
            raise ValueError("override_reason is required when overriding the rule engine")
        if not _CONCRETE_FACT.search(self.headline):
            raise ValueError("headline must cite a concrete number, observed fact, or explicit data gap")
        return self


class AgentRoleplayBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_reviewed: Literal[True]
    group_label: str = Field(min_length=1)
    votes: list[AgentRoleplayVote] = Field(min_length=1)


@dataclass(frozen=True)
class Persona:
    investor_id: str
    name: str
    school: str
    group: str
    philosophy: str
    key_metrics: tuple[str, ...]
    avoids: tuple[str, ...]
    a_share_view: str
    voice: str
    famous_positions: tuple[str, ...]
    is_flagship: bool

    def prompt_block(self) -> str:
        status = "flagship（YAML 优先于规则 headline）" if self.is_flagship else "stub（规则结果优先，禁止扩写历史言论）"
        sections = [
            f"## {self.name} ({self.investor_id}) · Group {self.group} · {status}",
            f"流派: {self.school or '未标注'}",
        ]
        if self.philosophy:
            sections.append(f"理念: {self.philosophy.strip()[:800]}")
        if self.key_metrics:
            sections.append("关键指标:\n" + "\n".join(f"- {item}" for item in self.key_metrics[:8]))
        if self.avoids:
            sections.append("回避条件:\n" + "\n".join(f"- {item}" for item in self.avoids[:6]))
        if self.a_share_view:
            sections.append(f"A股视角: {self.a_share_view.strip()[:600]}")
        if self.voice:
            sections.append(f"表达风格: {self.voice.strip()[:500]}")
        if self.famous_positions:
            sections.append(
                "历史材料（只可表述为 persona 文件记载，不能推断当前持仓）:\n"
                + "\n".join(f"- {item}" for item in self.famous_positions[:5])
            )
        return "\n".join(sections)


def load_persona(investor: dict[str, Any]) -> Persona:
    investor_id = str(investor.get("investor_id") or investor.get("id") or "").strip()
    path = PERSONAS_DIR / f"{investor_id}.yaml"
    payload: dict[str, Any] = {}
    if path.is_file():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            payload = loaded
    meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
    fallback = get_profile(investor_id, str(investor.get("school") or investor.get("group") or ""))
    return Persona(
        investor_id=investor_id,
        name=str(payload.get("name") or investor.get("perspective_label") or investor_id),
        school=str(payload.get("school") or investor.get("school") or ""),
        group=str(payload.get("group") or investor.get("school") or ""),
        philosophy=str(payload.get("philosophy") or ""),
        key_metrics=tuple(str(item) for item in list(payload.get("key_metrics") or [])),
        avoids=tuple(str(item) for item in list(payload.get("avoids") or [])),
        a_share_view=str(payload.get("a_share_view") or ""),
        voice=str(payload.get("voice") or fallback.get("time_horizon") or ""),
        famous_positions=tuple(str(item) for item in list(payload.get("famous_positions") or [])),
        is_flagship=bool(payload) and meta.get("status") != "auto_generated_stub",
    )


def apply_deep_agent_roleplay(
    panel: dict[str, Any],
    *,
    features: dict[str, Any],
    llm_provider: Any | None,
    cancellation: Event | None = None,
) -> dict[str, Any]:
    """Apply all-or-nothing Agent role-play overrides to a Deep panel."""

    result = deepcopy(panel)
    if str(result.get("profile") or "") != "deep":
        result["roleplay"] = {
            "required": False,
            "status": "not_requested",
            "agent_reviewed": False,
            "source_commit": LATEST_UZI_SOURCE_COMMIT,
            "group_count": 0,
            "completed_group_count": 0,
            "errors": [],
        }
        result["per_investor_override"] = {}
        return result

    votes = [row for row in list(result.get("votes") or []) if isinstance(row, dict)]
    expected_ids = [str(row.get("investor_id") or "") for row in votes if str(row.get("investor_id") or "")]
    provider_available = llm_provider is not None and bool(getattr(llm_provider, "available", True))
    if not provider_available:
        return _roleplay_gap(
            result,
            status="required_but_unavailable",
            errors=["agent_os_llm_provider_unavailable"],
            group_count=len(_nonempty_batches(votes)),
        )

    snapshot_json = json.dumps(_prompt_features(features), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    system_prompt = _system_prompt(snapshot_json)
    batches = _nonempty_batches(votes)
    completed: list[AgentRoleplayBatch] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=min(5, len(batches) or 1), thread_name_prefix="uzi-roleplay") as pool:
        futures = {
            pool.submit(
                _evaluate_batch,
                llm_provider,
                system_prompt=system_prompt,
                group_label=label,
                votes=group_votes,
            ): label
            for label, group_votes in batches
        }
        for future in as_completed(futures):
            if cancellation is not None and cancellation.is_set():
                for pending in futures:
                    pending.cancel()
                raise RuntimeError("deep_agent_roleplay_cancelled")
            label = futures[future]
            try:
                batch = future.result()
                _validate_batch_members(batch, [str(row.get("investor_id") or "") for row in dict(batches)[label]])
                completed.append(batch)
            except Exception as exc:  # one failed group invalidates the Deep gate
                errors.append(f"{label}:{exc.__class__.__name__}:{str(exc)[:300]}")

    overrides = {
        vote.investor_id: vote.model_dump(mode="json")
        for batch in completed
        for vote in batch.votes
    }
    if errors or set(overrides) != set(expected_ids):
        missing = sorted(set(expected_ids) - set(overrides))
        extra = sorted(set(overrides) - set(expected_ids))
        if missing:
            errors.append("missing_investor_ids:" + ",".join(missing))
        if extra:
            errors.append("unexpected_investor_ids:" + ",".join(extra))
        return _roleplay_gap(
            result,
            status="incomplete",
            errors=errors,
            group_count=len(batches),
            completed_group_count=len(completed),
        )

    for vote in votes:
        override = overrides[str(vote.get("investor_id") or "")]
        vote["original_rule_signal"] = vote.get("signal")
        vote["original_rule_score"] = vote.get("score")
        for key in ("signal", "score", "headline", "reasoning", "comment", "verdict"):
            value = override.get(key)
            if value is not None:
                vote[key] = value
        if override["signal"] == "skip":
            vote["score"] = 0.0
        vote["agent_roleplay_applied"] = True
        vote["override_rule_engine"] = bool(override.get("override_rule_engine"))
        vote["override_reason"] = override.get("override_reason")
        vote["roleplay_source"] = "agent_os_llm"
    result["votes"] = votes
    result["per_investor_override"] = overrides
    result["roleplay"] = {
        "required": True,
        "status": "completed",
        "agent_reviewed": True,
        "source_commit": LATEST_UZI_SOURCE_COMMIT,
        "group_count": len(batches),
        "completed_group_count": len(completed),
        "errors": [],
    }
    result["rule_mode"] = "uzi_rule_reference_plus_agent_roleplay"
    result["quality_status"] = "available" if result.get("evaluated_rule_count") else "degraded"
    result["data_gap"] = [gap for gap in list(result.get("data_gap") or []) if gap != "deep_agent_roleplay_required"]
    _recompute_panel_aggregates(result)
    return result


def _evaluate_batch(
    provider: Any,
    *,
    system_prompt: str,
    group_label: str,
    votes: list[dict[str, Any]],
) -> AgentRoleplayBatch:
    persona_blocks = []
    rule_reference = []
    for vote in votes:
        persona_blocks.append(load_persona(vote).prompt_block())
        rule_reference.append(
            {
                "investor_id": vote.get("investor_id"),
                "rule_signal": vote.get("signal"),
                "rule_score": vote.get("score"),
                "pass_rules": vote.get("pass_rules") or vote.get("pass"),
                "fail_rules": vote.get("fail_rules") or vote.get("fail"),
                "skipped_rule_count": vote.get("skipped_rule_count"),
                "rule_reasoning": vote.get("reasoning"),
            }
        )
    user_prompt = (
        f"负责分组：{group_label}\n\n"
        "逐一扮演下面列出的每位评委。规则引擎只是可审计参考，不是最终判断。"
        "flagship 必须优先遵守 YAML persona 的 key_metrics、avoids 与 voice；stub 只能依据规则和已给数据，"
        "不得编造公开言论、当前持仓或未提供的事实。非 A 股标的对游资应 skip。"
        "每个 headline 必须引用一个具体数字、已观测事实或明确的数据缺口；reasoning 必须说明其方法论如何作用于这些证据。"
        "若覆盖规则结论，override_rule_engine=true 且给出 override_reason。"
        "必须且只能返回本组每个 investor_id 一次。\n\n"
        "PERSONAS:\n" + "\n\n".join(persona_blocks)
        + "\n\nRULE_ENGINE_REFERENCE:\n"
        + json.dumps(rule_reference, ensure_ascii=False, sort_keys=True)
    )
    return parse_uzi_structured(
        provider,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        schema=AgentRoleplayBatch,
        temperature=0.1,
    )


def _system_prompt(snapshot_json: str) -> str:
    return (
        "你是 UZI Deep 的投资者角色评审执行器。只使用下方冻结的真实特征快照和后续 persona/规则参考。"
        "缺失数据必须写成缺口，禁止补值、禁止把历史 persona 材料当成当前持仓，禁止输出真人投资建议。"
        "同一研究的所有分组共用本 system message，保持字节级稳定。\n"
        f"UZI_SOURCE_COMMIT={LATEST_UZI_SOURCE_COMMIT}\n"
        f"FROZEN_FEATURE_SNAPSHOT={snapshot_json}"
    )


def _prompt_features(features: dict[str, Any]) -> dict[str, Any]:
    """Keep every normalized scalar plus compact evidence context."""

    result: dict[str, Any] = {}
    for key, value in features.items():
        if value is None or isinstance(value, (str, int, float, bool)):
            result[key] = value
        elif key in {"available_fields", "data_gap", "feature_derivations", "assumptions"}:
            result[key] = [str(item)[:300] for item in list(value or [])]
        elif key == "field_sources" and isinstance(value, dict):
            result[key] = {str(field): str(source) for field, source in value.items()}
        elif key in {"capital_flow", "sentiment"} and isinstance(value, dict):
            result[key] = _compact_mapping(value)
    return result


def _compact_mapping(value: dict[str, Any], *, depth: int = 0) -> dict[str, Any]:
    if depth >= 2:
        return {}
    compact: dict[str, Any] = {}
    for key, item in list(value.items())[:30]:
        if item is None or isinstance(item, (int, float, bool)):
            compact[str(key)] = item
        elif isinstance(item, str):
            compact[str(key)] = item[:500]
        elif isinstance(item, dict):
            compact[str(key)] = _compact_mapping(item, depth=depth + 1)
        elif isinstance(item, list):
            compact[str(key)] = [
                _compact_mapping(row, depth=depth + 1) if isinstance(row, dict) else str(row)[:300]
                for row in item[:10]
            ]
    return compact


def _nonempty_batches(votes: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    result: list[tuple[str, list[dict[str, Any]]]] = []
    for label, groups in _GROUP_BATCHES:
        rows = [
            row
            for row in votes
            if str(row.get("school") or row.get("group") or "") in groups
        ]
        if rows:
            result.append((label, rows))
    return result


def _validate_batch_members(batch: AgentRoleplayBatch, expected_ids: list[str]) -> None:
    actual_ids = [vote.investor_id for vote in batch.votes]
    if batch.group_label.strip() == "":
        raise ValueError("group_label is empty")
    if len(actual_ids) != len(set(actual_ids)):
        raise ValueError("duplicate investor_id in roleplay batch")
    if set(actual_ids) != set(expected_ids):
        raise ValueError(
            f"batch membership mismatch; missing={sorted(set(expected_ids) - set(actual_ids))}; "
            f"extra={sorted(set(actual_ids) - set(expected_ids))}"
        )


def _roleplay_gap(
    result: dict[str, Any],
    *,
    status: str,
    errors: list[str],
    group_count: int,
    completed_group_count: int = 0,
) -> dict[str, Any]:
    gaps = list(result.get("data_gap") or [])
    if "deep_agent_roleplay_required" not in gaps:
        gaps.append("deep_agent_roleplay_required")
    result["quality_status"] = "data_gap"
    result["data_gap"] = gaps
    result["per_investor_override"] = {}
    result["roleplay"] = {
        "required": True,
        "status": status,
        "agent_reviewed": False,
        "source_commit": LATEST_UZI_SOURCE_COMMIT,
        "group_count": group_count,
        "completed_group_count": completed_group_count,
        "errors": errors,
    }
    result["risk_flags"] = list(dict.fromkeys([*list(result.get("risk_flags") or []), "deep_roleplay_incomplete"]))
    return result


def _recompute_panel_aggregates(result: dict[str, Any]) -> None:
    votes = [row for row in list(result.get("votes") or []) if isinstance(row, dict)]
    result["distribution"] = dict(Counter(str(row.get("signal") or "neutral") for row in votes))
    active = [row for row in votes if row.get("signal") not in {"skip", "insufficient_evidence"}]
    scores = [float(row["score"]) for row in active if row.get("score") is not None]
    result["average_score"] = round(sum(scores) / len(scores), 2) if scores else None
    result["skipped_seat_count"] = sum(row.get("signal") == "skip" for row in votes)
    result["disagreement"] = {
        "exists": len({str(row.get("signal") or "") for row in active}) > 1,
        "summary": "Deep Agent 角色评审对同一冻结证据形成不同方法论判断。" if active else "没有足够证据形成 Deep Agent 判断。",
    }
    result["confidence"] = (
        round(sum(float(row.get("confidence") or 0.0) for row in active) / len(active), 4)
        if active
        else 0.0
    )


def build_native_panel_overrides(
    panel: dict[str, Any],
    *,
    features: dict[str, Any],
    llm_provider: Any | None,
    cancellation: Event | None = None,
    school: str | None = None,
) -> dict[str, Any]:
    """Generate Agent OS overrides for the exact upstream ``investors`` list.

    UZI's pure rule engine calls the rows ``investors`` while the earlier
    Cognitrade adapter called them ``votes``.  This bridge changes only that
    transport key: persona files, frozen evidence, five parallel role-play
    groups and the all-or-nothing membership gate remain identical.
    """

    native = deepcopy(panel)
    native["profile"] = "deep"
    selected_school = str(school or "").strip().upper()
    native["votes"] = [
        deepcopy(row)
        for row in list(native.get("investors") or [])
        if isinstance(row, dict)
        and (
            not selected_school
            or str(row.get("school") or row.get("group") or "") == selected_school
        )
    ]
    evaluated = apply_deep_agent_roleplay(
        native,
        features=features,
        llm_provider=llm_provider,
        cancellation=cancellation,
    )
    return {
        "per_investor_override": dict(evaluated.get("per_investor_override") or {}),
        "roleplay": dict(evaluated.get("roleplay") or {}),
        "data_gap": list(evaluated.get("data_gap") or []),
        "risk_flags": list(evaluated.get("risk_flags") or []),
    }


__all__ = [
    "AgentRoleplayBatch",
    "AgentRoleplayVote",
    "LATEST_UZI_SOURCE_COMMIT",
    "apply_deep_agent_roleplay",
    "build_native_panel_overrides",
    "load_persona",
]
