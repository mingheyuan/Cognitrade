"""Agent-driven UZI full-workflow panel role-play owned by the Finance DomainPack.

The latest UZI SKILL.md treats the deterministic rule engine as reference
material only whenever the full Agent workflow is selected.  This module preserves that boundary
without importing or executing the external UZI project: persona YAML is
vendored as Pack data, Agent OS supplies the configured LLM provider, and the
validated overrides are written into the panel Artifact.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from threading import Event, get_ident
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from domain_packs.finance_ashare.subagents.uzi_llm_support import (
    EXHAUSTED_MODEL_FAILURES,
    model_failure_record,
    parse_uzi_structured,
    resolve_uzi_llm_worker_count,
    submit_uzi_stage,
)
from domain_packs.finance_ashare.subagents.uzi_native_agent_protocol import (
    native_agent_protocol,
)
from domain_packs.finance_ashare.subagents.uzi_panel_profiles import get_profile
from domain_packs.finance_ashare.subagents.uzi_roleplay_context import (
    pinned_persona_context,
    roleplay_raw_summary,
)
from domain_packs.finance_ashare.subagents.uzi_stage_progress import (
    failed_stage_next_action,
    notify_stage_progress,
)

LATEST_UZI_SOURCE_COMMIT = "b3665e85b8666477038e11157d8e8feb0e006b76"
_LOGGER = logging.getLogger("uvicorn.error")
_ROLEPLAY_STAGE_CACHE_SCHEMA_VERSION = "uzi-3.9.4-roleplay-stage-v1"
PERSONAS_DIR = (
    Path(__file__).resolve().parents[1]
    / "vendor"
    / "uzi_3_9_2"
    / "skill"
    / "personas"
)
_SIGNALS = {"bullish", "bearish", "neutral", "skip", "insufficient_evidence"}
_GROUP_BATCHES: tuple[tuple[str, frozenset[str]], ...] = (
    ("A+B · 经典价值与成长", frozenset({"A", "B"})),
    ("C · 宏观对冲", frozenset({"C"})),
    ("D+E · 技术趋势与中国价投", frozenset({"D", "E"})),
    ("F · A股游资", frozenset({"F"})),
    ("G+H+I · 量化、科技领袖与AI瓶颈", frozenset({"G", "H", "I"})),
)
_ROLEPLAY_REPAIR_HINTS = {
    "roleplay_override_reason_missing":
        "对应评委声明覆盖规则但 override_reason 为空；说明实际覆盖的依据，不改变评分或评级来规避校验。",
    "roleplay_rationale_grounding_missing":
        ("对应评委的理由未通过事实依据检查；引用已有冻结事实、明确数据缺口或合法弃评依据，"
         "不编造数字，不改变评分或评级来规避校验。"),
}


class AgentRoleplayVote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    investor_id: str = Field(min_length=1)
    signal: Literal["bullish", "bearish", "neutral", "skip", "insufficient_evidence"]
    score: float = Field(ge=0, le=100)
    headline: str = Field(min_length=1)
    reasoning: str = Field(min_length=1)
    comment: str | None = None
    verdict: str | None = None
    override_rule_engine: bool
    override_reason: str | None = None

    @model_validator(mode="after")
    def validate_override(self) -> "AgentRoleplayVote":
        if self.override_rule_engine and not str(self.override_reason or "").strip():
            raise ValueError("override_reason is required when overriding the rule engine")
        # Task3 requires concrete facts, not digits or a host keyword list.
        # Keep that obligation in the unchanged native protocol/content review;
        # structural acceptance alone never proves semantic grounding.
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
    required: bool | None = None,
    previous_failures: list[dict[str, Any]] | None = None,
    research_context: dict[str, Any] | None = None,
    raw: dict[str, Any] | None = None,
    progress_callback=None,
) -> dict[str, Any]:
    """Apply all-or-nothing Agent role-play overrides to a UZI panel.

    ``required=None`` preserves the profile-native compatibility behavior
    (Deep only).  Cognitrade's full Agent OS path passes ``required=True`` for
    Lite, Medium and Deep, matching the interactive SKILL.md hard gate.
    """

    result = deepcopy(panel)
    profile = str(result.get("profile") or "deep").strip().lower()
    roleplay_required = profile == "deep" if required is None else bool(required)
    if not roleplay_required:
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
    system_prompt = _system_prompt(snapshot_json, research_context=research_context, raw=raw)
    batches = _nonempty_batches(votes)
    completed: list[AgentRoleplayBatch] = []

    def notify_group(label, *, cache_hit=False, cache_reason="content_profile_source_prompt_match", failure=None):
        if callable(progress_callback):
            notify_stage_progress(progress_callback, {
                "current_action": f"UZI 评委分组 {len(completed)}/{len(batches)}：{label}",
                "next_action": failed_stage_next_action(failure) if failure else "继续剩余评委，完成后生成综合研判",
                "stage": f"roleplay_{label}",
                "stage_phase": "failed" if failure else "validated",
                "completed_stages": len(completed),
                "stage_total": len(batches),
                "progress_percent": round(len(completed) / max(len(batches), 1) * 100),
                "progress_scope": "roleplay_groups",
                "cache_hit": cache_hit,
                **({"cache_reason": cache_reason} if cache_hit else {}),
            })
    errors: list[str] = []
    failure_records: list[dict[str, Any]] = []
    previous = {row["stage"]: row for row in previous_failures or [] if row.get("stage")}
    pending_batches: list[tuple[str, list[dict[str, Any]], str, Path | None]] = []
    for label, group_votes in batches:
        prior = previous.get(f"roleplay_{label}")
        if prior and prior.get("kind") in EXHAUSTED_MODEL_FAILURES:
            failure_records.append(dict(prior))
            errors.append(f"{label}:{prior['kind']}:stage_budget_already_exhausted")
            notify_group(label, failure=prior)
            continue
        user_prompt = _batch_user_prompt(label, group_votes)
        cache_path = _roleplay_stage_cache_path(
            llm_provider,
            profile=profile,
            group_label=label,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        cached = _load_roleplay_stage_cache(cache_path)
        cache_reason = "content_profile_source_prompt_match"
        if cached is None:
            # Only the explicit assignment was added. Reuse the exact old
            # input/prompt cache iff every currently assigned member validates.
            # Do not rewrite it as a response to the new prompt.
            legacy_path = _roleplay_stage_cache_path(
                llm_provider, profile=profile, group_label=label,
                system_prompt=system_prompt,
                user_prompt=_batch_user_prompt(label, group_votes, include_assignment=False),
            )
            cached = _load_legacy_assignment_cache(legacy_path, label, group_votes)
            if cached is not None:
                cache_reason = "legacy_assignment_membership_validated"
        if cached is not None:
            try:
                _validate_batch_members(
                    cached,
                    [str(row.get("investor_id") or "") for row in group_votes],
                    expected_group_label=label,
                )
            except Exception as exc:
                _discard_roleplay_stage_cache(cache_path, exc)
            else:
                completed.append(cached)
                notify_group(label, cache_hit=True, cache_reason=cache_reason)
                continue
        if prior:
            user_prompt += "\nOUTPUT_REPAIR=" + json.dumps(prior, ensure_ascii=False)
            # The old votes.N/value_error pair cannot identify which custom
            # obligation failed. Explain only codes emitted by our validators;
            # unknown/legacy failures are not retrospectively guessed.
            hints = list(dict.fromkeys(
                _ROLEPLAY_REPAIR_HINTS[reason["code"]]
                for reason in prior.get("validation_reasons", [])
                if isinstance(reason, dict) and reason.get("code") in _ROLEPLAY_REPAIR_HINTS
            ))
            if hints:
                user_prompt += "\n定向修复说明：\n" + "\n".join(hints)
        pending_batches.append((label, group_votes, user_prompt, cache_path))
    with ThreadPoolExecutor(
        max_workers=resolve_uzi_llm_worker_count(
            llm_provider,
            len(pending_batches),
            ceiling=5,
        ),
        thread_name_prefix="uzi-roleplay",
    ) as pool:
        futures = {
            submit_uzi_stage(pool,
                _evaluate_batch,
                llm_provider,
                system_prompt=system_prompt,
                group_label=label,
                user_prompt=user_prompt,
                research_profile=profile,
            ): (label, group_votes, cache_path)
            for label, group_votes, user_prompt, cache_path in pending_batches
        }
        for future in as_completed(futures):
            if cancellation is not None and cancellation.is_set():
                for pending in futures:
                    pending.cancel()
                raise RuntimeError("deep_agent_roleplay_cancelled")
            label, group_votes, cache_path = futures[future]
            try:
                batch = future.result()
                _validate_batch_members(
                    batch,
                    [str(row.get("investor_id") or "") for row in group_votes],
                    expected_group_label=label,
                )
                completed.append(batch)
                _write_roleplay_stage_cache(cache_path, batch)
                notify_group(label)
            except Exception as exc:  # one failed group invalidates the Deep gate
                failure = model_failure_record(exc, stage=f"roleplay_{label}")
                failure_records.append(failure)
                notify_group(label, failure=failure)
                errors.append(f"{label}:{exc.__class__.__name__}:{str(exc)[:300]} fields={failure.get('validation_fields', [])}")

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
            failure_records=failure_records,
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
    result["data_gap"] = [
        gap
        for gap in list(result.get("data_gap") or [])
        if gap not in {"agent_roleplay_required", "deep_agent_roleplay_required"}
    ]
    _recompute_panel_aggregates(result)
    return result


def _evaluate_batch(
    provider: Any,
    *,
    system_prompt: str,
    group_label: str,
    user_prompt: str,
    research_profile: str,
) -> AgentRoleplayBatch:
    return parse_uzi_structured(
        provider,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        schema=AgentRoleplayBatch,
        temperature=0.1,
        research_profile=research_profile,
        uzi_stage=f"roleplay_{group_label}",
    )


def _batch_user_prompt(
    group_label: str,
    votes: list[dict[str, Any]],
    *,
    include_assignment: bool = True,
) -> str:
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
    assignment = ""
    if include_assignment:
        assignment = (
            "本调用只处理当前子组，你不是主控；原生参考文档中的全流程和旧人数仅为背景。"
            "以下 ROLEPLAY_ASSIGNMENT 来自本次原生评委表，是唯一成员清单；"
            "每个 ID 恰好一次，不增加、不省略，votes 数组长度必须等于 required_investor_count。"
            "观点、评分和是否 skip 仍按原生规则与冻结事实判断；合法 skip 席位也保留，不编造判断。\n"
            "ROLEPLAY_ASSIGNMENT=" + json.dumps({
                "group": group_label.split("·", 1)[0].strip(),
                "required_investor_ids": [str(vote["investor_id"]) for vote in votes],
                "required_investor_count": len(votes),
            }, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n\n"
        )
    return (
        f"负责分组：{group_label}\n\n" + assignment +
        "逐一扮演下面列出的每位评委。规则引擎只是可审计参考，不是最终判断。"
        "flagship 必须优先遵守 YAML persona 的 key_metrics、avoids 与 voice；stub 只能依据规则和已给数据，"
        "不得编造公开言论、当前持仓或未提供的事实。非 A 股标的对游资应 skip。"
        "每个 headline 必须引用一个具体数字、已观测事实或明确的数据缺口；reasoning 必须说明其方法论如何作用于这些证据。"
        "若覆盖规则结论，override_rule_engine=true 且给出 override_reason。"
        "必须且只能返回本组每个 investor_id 一次。\n\n"
        "PERSONAS:\n" + "\n\n".join(persona_blocks)
        + "\n原始 YAML 优先；无 YAML 的评委依照原生台词库与规则。以下静态历史资料不能证明当前或目标历史日持仓，"
        "不得把台词模板当成本股事实或强制照抄；真实持仓/言论只有冻结证据支持时才能断言。\n"
        + "PINNED_PERSONA_CONTEXT="
        + json.dumps(pinned_persona_context([str(row.get("investor_id") or "") for row in votes]),
                     ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n\nRULE_ENGINE_REFERENCE:\n"
        + json.dumps(rule_reference, ensure_ascii=False, sort_keys=True)
    )


def _system_prompt(snapshot_json: str, *, research_context: dict[str, Any] | None = None,
                   raw: dict[str, Any] | None = None) -> str:
    research_prompt = ""
    if research_context:
        research_prompt = (
            "以下是Task2.5实际输出；结论、关联和影响估计是研究推断，不是新增原始事实。"
            "结合本人的方法论审查其因果、引用及未知披露，不照抄、不把部分研究说成已完成。"
            "不得将估计数字写回真实特征、原生规则分或交易计划。\n"
            "EXECUTED_QUALITATIVE_RESEARCH_CONTEXT="
            + json.dumps(research_context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        )
    raw_prompt = ("FROZEN_RAW_SUMMARY=" + json.dumps(roleplay_raw_summary(raw),
        ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n") if raw is not None else ""
    return (
        "你是 UZI 完整 Agent 工作流的投资者角色评审执行器。使用下方冻结原始摘要、真实特征、已执行研究和 persona/规则参考。"
        "缺失数据必须写成缺口，禁止补值、禁止把历史 persona 材料当成当前持仓，禁止输出真人投资建议。"
        "同一研究的所有分组共用本 system message，保持字节级稳定。\n"
        f"UZI_SOURCE_COMMIT={LATEST_UZI_SOURCE_COMMIT}\n"
        f"{native_agent_protocol('roleplay')}{research_prompt}{raw_prompt}"
        f"FROZEN_FEATURE_SNAPSHOT={snapshot_json}"
    )


def _roleplay_stage_cache_path(
    provider: Any,
    *,
    profile: str,
    group_label: str,
    system_prompt: str,
    user_prompt: str,
) -> Path | None:
    settings = getattr(provider, "settings", None)
    workspace_root = getattr(settings, "workspace_root", None)
    if workspace_root is None:
        return None
    identity = {
        "schema_version": _ROLEPLAY_STAGE_CACHE_SCHEMA_VERSION,
        "uzi_source_commit": LATEST_UZI_SOURCE_COMMIT,
        "profile": profile,
        "group_label": group_label,
        "model_provider": str(getattr(settings, "provider_name", "")),
        "model": str(getattr(settings, "default_model", "")),
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
    }
    digest = sha256(
        json.dumps(
            identity,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return Path(workspace_root) / "cache" / "uzi_roleplay_stages" / f"{digest}.json"


def _load_roleplay_stage_cache(path: Path | None) -> AgentRoleplayBatch | None:
    if path is None or not path.exists():
        return None
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        if envelope.get("schema_version") != _ROLEPLAY_STAGE_CACHE_SCHEMA_VERSION:
            return None
        return AgentRoleplayBatch.model_validate(envelope.get("payload"))
    except Exception as exc:
        _discard_roleplay_stage_cache(path, exc)
        return None


def _load_legacy_assignment_cache(
    path: Path | None, label: str, votes: list[dict[str, Any]],
) -> AgentRoleplayBatch | None:
    """Read-only compatibility for the pre-assignment prompt, not any old cache."""
    if path is None or not path.is_file():
        return None
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        if envelope.get("schema_version") != _ROLEPLAY_STAGE_CACHE_SCHEMA_VERSION:
            return None
        batch = AgentRoleplayBatch.model_validate(envelope.get("payload"))
        _validate_batch_members(
            batch, [str(vote["investor_id"]) for vote in votes], expected_group_label=label,
        )
        return batch
    except (OSError, ValueError, TypeError):
        return None


def _discard_roleplay_stage_cache(path: Path | None, exc: Exception) -> None:
    _LOGGER.warning(
        "Ignoring invalid UZI roleplay stage cache: path=%s error=%s:%s",
        path,
        exc.__class__.__name__,
        str(exc)[:300],
    )
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _write_roleplay_stage_cache(
    path: Path | None,
    payload: AgentRoleplayBatch,
) -> None:
    if path is None:
        return
    envelope = {
        "schema_version": _ROLEPLAY_STAGE_CACHE_SCHEMA_VERSION,
        "payload": payload.model_dump(mode="json"),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{get_ident()}.tmp")
        temporary.write_text(
            json.dumps(envelope, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(path)
    except OSError as exc:
        _LOGGER.warning(
            "Unable to persist UZI roleplay stage cache: path=%s error=%s:%s",
            path,
            exc.__class__.__name__,
            str(exc)[:300],
        )


def _prompt_features(features: dict[str, Any]) -> dict[str, Any]:
    """Keep every normalized scalar plus compact evidence context."""

    from domain_packs.finance_ashare.subagents.uzi_input_views import (
        PROMPT_EVIDENCE_VIEW_VERSION,
        compact_evidence_view,
    )

    notices: list[dict[str, Any]] = []
    result = compact_evidence_view(
        features, max_sequence_items=10, max_text_chars=500, truncations=notices,
        preserve_sequences_at=frozenset({"available_fields", "data_gap", "feature_derivations", "assumptions"}),
    )
    result["_prompt_view"] = {
        "version": PROMPT_EVIDENCE_VIEW_VERSION, "truncations": notices,
        "omission_semantics": "__uzi_prompt_omitted__ means outside this view, not missing source data; never cite it as a fact",
    }
    return result


def _compact_mapping(value: dict[str, Any], *, depth: int = 0) -> dict[str, Any]:
    from domain_packs.finance_ashare.subagents.uzi_input_views import (
        compact_evidence_view,
    )

    return compact_evidence_view(value, max_sequence_items=10, max_text_chars=500)


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


def _validate_batch_members(
    batch: AgentRoleplayBatch,
    expected_ids: list[str],
    *,
    expected_group_label: str | None = None,
) -> None:
    actual_ids = [vote.investor_id for vote in batch.votes]
    if batch.group_label.strip() == "":
        raise ValueError("group_label is empty")
    if expected_group_label is not None and not _equivalent_group_label(
        batch.group_label,
        expected_group_label,
    ):
        raise ValueError(
            f"group label mismatch; expected={expected_group_label!r}; "
            f"actual={batch.group_label!r}"
        )
    if len(actual_ids) != len(set(actual_ids)):
        raise ValueError("duplicate investor_id in roleplay batch")
    if set(actual_ids) != set(expected_ids):
        raise ValueError(
            f"batch membership mismatch; missing={sorted(set(expected_ids) - set(actual_ids))}; "
            f"extra={sorted(set(actual_ids) - set(expected_ids))}"
        )


def _equivalent_group_label(actual: str, expected: str) -> bool:
    """Accept the canonical label or its exact group-code prefix.

    The prose after ``·`` is display metadata. Membership remains strictly
    checked below, so an LLM returning ``G+H+I`` instead of the full Chinese
    label cannot change which investors were evaluated.
    """

    normalized_actual = re.sub(r"\s+", "", actual.strip())
    normalized_expected = re.sub(r"\s+", "", expected.strip())
    if normalized_actual == normalized_expected:
        return True
    expected_code = normalized_expected.split("·", 1)[0]
    actual_code = normalized_actual.split("·", 1)[0]
    return bool(actual_code) and actual_code == expected_code


def _roleplay_gap(
    result: dict[str, Any],
    *,
    status: str,
    errors: list[str],
    group_count: int,
    completed_group_count: int = 0,
    failure_records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    gaps = list(result.get("data_gap") or [])
    if "agent_roleplay_required" not in gaps:
        gaps.append("agent_roleplay_required")
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
        "failure_records": list(failure_records or []),
    }
    result["risk_flags"] = list(dict.fromkeys([*list(result.get("risk_flags") or []), "agent_roleplay_incomplete"]))
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
        "summary": "完整 Agent 角色评审对同一冻结证据形成不同方法论判断。" if active else "没有足够证据形成 Agent 判断。",
    }
    result["confidence"] = (
        round(sum(float(row.get("confidence") or 0.0) for row in active) / len(active), 4)
        if active
        else 0.0
    )


def _native_roleplay_votes(panel, school):
    selected_school = str(school or "").strip().upper()
    return [row for row in list(panel.get("investors") or []) if isinstance(row, dict) and (
        not selected_school or str(row.get("school") or row.get("group") or "") == selected_school
    )]


def native_roleplay_group_count(panel, *, school=None):
    return len(_nonempty_batches(_native_roleplay_votes(panel, school)))


def build_native_panel_overrides(
    panel: dict[str, Any],
    *,
    features: dict[str, Any],
    llm_provider: Any | None,
    cancellation: Event | None = None,
    school: str | None = None,
    profile: str = "deep",
    required: bool | None = None,
    previous_failures: list[dict[str, Any]] | None = None,
    research_context: dict[str, Any] | None = None,
    raw: dict[str, Any] | None = None,
    progress_callback=None,
) -> dict[str, Any]:
    """Generate Agent OS overrides for the exact upstream ``investors`` list.

    UZI's pure rule engine calls the rows ``investors`` while the earlier
    Cognitrade adapter called them ``votes``.  This bridge changes only that
    transport key: persona files, frozen evidence, five parallel role-play
    groups and the all-or-nothing membership gate remain identical.
    """

    native = deepcopy(panel)
    native["profile"] = profile
    native["votes"] = deepcopy(_native_roleplay_votes(native, school))
    evaluated = apply_deep_agent_roleplay(
        native,
        features=features,
        llm_provider=llm_provider,
        cancellation=cancellation,
        required=required,
        previous_failures=previous_failures,
        research_context=research_context,
        raw=raw,
        progress_callback=progress_callback,
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
