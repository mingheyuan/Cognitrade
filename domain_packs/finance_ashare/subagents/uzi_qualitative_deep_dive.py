"""Three-way Agent qualitative deep dive for UZI Deep research.

Collection remains in governed DomainPack skills.  This module receives the
frozen ArtifactBoard snapshots, dispatches the latest UZI qualitative groups
in parallel, and only accepts citations that were present in those artifacts.
It never invents evidence or performs an external UZI runtime call.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from hashlib import sha256
import json
import logging
from pathlib import Path
from threading import Event, get_ident
from time import monotonic
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from domain_packs.finance_ashare.subagents.uzi_agent_roleplay import LATEST_UZI_SOURCE_COMMIT
from domain_packs.finance_ashare.subagents.uzi_llm_support import parse_uzi_structured


_LOGGER = logging.getLogger("uvicorn.error")
_QUALITATIVE_STAGE_CACHE_SCHEMA_VERSION = "uzi-3.9.4-qualitative-stage-v1"

_DIMENSION_TYPES: dict[str, tuple[str, ...]] = {
    "3_macro": ("macro",),
    "13_policy": ("policy",),
    "7_industry": ("industry",),
    "15_events": ("events",),
    "8_materials": ("materials",),
    "9_futures": ("futures",),
}
_GROUPS: tuple[tuple[str, tuple[str, str]], ...] = (
    ("A · Macro-Policy", ("3_macro", "13_policy")),
    ("B · Industry-Events", ("7_industry", "15_events")),
    ("C · Cost-Transmission", ("8_materials", "9_futures")),
)
class QualitativeEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=8, max_length=2_000)
    finding: str = Field(min_length=10, max_length=1_200)
    retrieved_at: str = Field(min_length=1, max_length=100)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("evidence URL must be HTTP(S)")
        return value


class QualitativeAssociation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    link_to: str = Field(min_length=1, max_length=100)
    chain_id: str = Field(min_length=1, max_length=100)
    causal_chain: str = Field(min_length=20, max_length=1_200)
    estimated_impact: str = Field(min_length=4, max_length=500)


class QualitativeDimension(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension_id: Literal["3_macro", "13_policy", "7_industry", "15_events", "8_materials", "9_futures"]
    evidence: list[QualitativeEvidence] = Field(min_length=2)
    associations: list[QualitativeAssociation] = Field(default_factory=list)
    conclusion: str = Field(min_length=20, max_length=1_500)


class QualitativeGroupResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_reviewed: Literal[True]
    group_label: str = Field(min_length=1)
    dimensions: list[QualitativeDimension] = Field(min_length=2, max_length=2)


def apply_qualitative_deep_dive(
    deep_analysis: dict[str, Any],
    *,
    snapshot_rows: list[dict[str, Any]],
    llm_provider: Any | None,
    cancellation: Event | None = None,
    progress_callback=None,
    analysis_cycle: int | None = None,
) -> dict[str, Any]:
    result = deepcopy(deep_analysis)
    provider_available = llm_provider is not None and bool(getattr(llm_provider, "available", True))
    evidence_bundle = {
        dimension: _dimension_context(snapshot_rows, dimension)
        for dimension in _DIMENSION_TYPES
    }
    preflight_gaps = [
        f"{dimension}:requires_two_cited_sources"
        for dimension, context in evidence_bundle.items()
        if len(context["allowed_urls"]) < 2
    ]
    if not provider_available:
        return _qualitative_gap(
            result,
            status="required_but_unavailable",
            errors=["agent_os_llm_provider_unavailable"],
            evidence_bundle=evidence_bundle,
        )
    if preflight_gaps:
        return _qualitative_gap(
            result,
            status="evidence_preflight_failed",
            errors=preflight_gaps,
            evidence_bundle=evidence_bundle,
        )

    system_prompt = (
        "你是 UZI Deep 六维定性研究执行器。只允许引用冻结证据包里的 URL 和事实；"
        "禁止补造来源、补造数字或把爬虫片段直接改写成无引用结论。每维至少引用两个不同 URL，"
        "并明确跨域因果链。三个分析组共用同一冻结证据前缀。\n"
        f"UZI_SOURCE_COMMIT={LATEST_UZI_SOURCE_COMMIT}\n"
        "FROZEN_EVIDENCE_BUNDLE="
        + json.dumps(evidence_bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    completed: list[QualitativeGroupResult] = []
    errors: list[str] = []
    pending_groups: list[tuple[str, tuple[str, str], str, Path | None]] = []
    for label, dimensions in _GROUPS:
        user_prompt = _group_user_prompt(
            group_label=label,
            dimensions=dimensions,
            evidence_bundle=evidence_bundle,
        )
        cache_path = _qualitative_stage_cache_path(
            llm_provider,
            group_label=label,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        cached = _load_qualitative_stage_cache(cache_path)
        if cached is None:
            pending_groups.append((label, dimensions, user_prompt, cache_path))
            continue
        try:
            _validate_group(cached, dimensions, evidence_bundle)
        except Exception as exc:
            _discard_qualitative_stage_cache(cache_path, exc)
            pending_groups.append((label, dimensions, user_prompt, cache_path))
            continue
        completed.append(cached)
        _LOGGER.info("UZI qualitative stage cache hit: group=%s", label)
        _notify_qualitative_progress(
            progress_callback,
            analysis_cycle=analysis_cycle,
            completed=len(completed),
            group_label=label,
            cache_hit=True,
        )
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="uzi-qualitative") as pool:
        futures = {
            pool.submit(
                _evaluate_group,
                llm_provider,
                system_prompt=system_prompt,
                group_label=label,
                dimensions=dimensions,
                evidence_bundle=evidence_bundle,
                user_prompt=user_prompt,
            ): (label, dimensions, cache_path)
            for label, dimensions, user_prompt, cache_path in pending_groups
        }
        for future in as_completed(futures):
            if cancellation is not None and cancellation.is_set():
                for pending in futures:
                    pending.cancel()
                raise RuntimeError("qualitative_deep_dive_cancelled")
            label, expected_dimensions, cache_path = futures[future]
            try:
                group = future.result()
                _validate_group(group, expected_dimensions, evidence_bundle)
                completed.append(group)
                _write_qualitative_stage_cache(cache_path, group)
                _notify_qualitative_progress(
                    progress_callback,
                    analysis_cycle=analysis_cycle,
                    completed=len(completed),
                    group_label=label,
                    cache_hit=False,
                )
            except Exception as exc:
                errors.append(f"{label}:{exc.__class__.__name__}:{str(exc)[:300]}")

    dimensions = {
        dimension.dimension_id: dimension.model_dump(mode="json")
        for group in completed
        for dimension in group.dimensions
    }
    expected = set(_DIMENSION_TYPES)
    association_count = sum(len(row["associations"]) for row in dimensions.values())
    if errors or set(dimensions) != expected or association_count < 3:
        missing = sorted(expected - set(dimensions))
        if missing:
            errors.append("missing_dimensions:" + ",".join(missing))
        if association_count < 3:
            errors.append(f"cross_domain_associations_below_three:{association_count}")
        return _qualitative_gap(
            result,
            status="incomplete",
            errors=errors,
            evidence_bundle=evidence_bundle,
            completed_group_count=len(completed),
            partial_dimensions=dimensions,
        )

    result["agent_reviewed"] = True
    result["qualitative_deep_dive"] = dimensions
    result["qualitative_agent_execution"] = {
        "required": True,
        "status": "completed",
        "agent_reviewed": True,
        "source_commit": LATEST_UZI_SOURCE_COMMIT,
        "group_count": 3,
        "completed_group_count": 3,
        "evidence_count": sum(len(row["evidence"]) for row in dimensions.values()),
        "association_count": association_count,
        "errors": [],
    }
    remaining_gaps = [
        gap
        for gap in list(result.get("data_gap") or [])
        if gap != "qualitative_deep_dive_required"
    ]
    base_status = str(deep_analysis.get("quality_status") or deep_analysis.get("status") or "").strip()
    if base_status == "data_gap":
        result["quality_status"] = "data_gap"
    elif base_status == "assumption_only" and not remaining_gaps:
        result["quality_status"] = "assumption_only"
    elif base_status == "degraded" or remaining_gaps:
        result["quality_status"] = "degraded"
    else:
        result["quality_status"] = "available"
    result["data_gap"] = remaining_gaps
    result["risk_flags"] = [flag for flag in list(result.get("risk_flags") or []) if flag != "qualitative_deep_dive_incomplete"]
    return result


def _evaluate_group(
    provider: Any,
    *,
    system_prompt: str,
    group_label: str,
    dimensions: tuple[str, str],
    evidence_bundle: dict[str, dict[str, Any]],
    user_prompt: str | None = None,
) -> QualitativeGroupResult:
    user_prompt = user_prompt or _group_user_prompt(
        group_label=group_label,
        dimensions=dimensions,
        evidence_bundle=evidence_bundle,
    )
    started_at = monotonic()
    _LOGGER.info(
        "UZI qualitative stage started: group=%s dimensions=%s prompt_chars=%d",
        group_label,
        ",".join(dimensions),
        len(system_prompt) + len(user_prompt),
    )
    try:
        result = parse_uzi_structured(
            provider,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=QualitativeGroupResult,
            temperature=0.1,
        )
    except Exception as exc:
        _LOGGER.warning(
            "UZI qualitative stage failed: group=%s dimensions=%s elapsed=%.1fs error=%s:%s",
            group_label,
            ",".join(dimensions),
            monotonic() - started_at,
            exc.__class__.__name__,
            str(exc)[:300],
        )
        raise
    _LOGGER.info(
        "UZI qualitative stage completed: group=%s dimensions=%s elapsed=%.1fs",
        group_label,
        ",".join(dimensions),
        monotonic() - started_at,
    )
    return result


def _group_user_prompt(
    *,
    group_label: str,
    dimensions: tuple[str, str],
    evidence_bundle: dict[str, dict[str, Any]],
) -> str:
    prompts = {
        "A · Macro-Policy": "分析增长/通胀/利率/流动性向公司与行业的传导，以及政策目标、实施工具、受益与约束。",
        "B · Industry-Events": "分析行业生命周期、供需和竞争格局，并区分公告/事件的时间、确定性与财务传导。",
        "C · Cost-Transmission": "从已识别原材料映射到相关期货，分析成本变动、价格传导、毛利率时滞与对冲条件。",
    }
    return (
        f"负责分组：{group_label}\n"
        f"必须且只能返回维度 {list(dimensions)}。{prompts[group_label]}"
        "每维 evidence 至少两条且 URL 必须原样取自冻结包；六维总体需要至少三条跨域 associations，"
        "本组至少提供一条。缺失事实只能写缺口，不能猜测。\n"
        "本组证据="
        + json.dumps({key: evidence_bundle[key] for key in dimensions}, ensure_ascii=False, sort_keys=True)
    )


def _qualitative_stage_cache_path(
    provider: Any,
    *,
    group_label: str,
    system_prompt: str,
    user_prompt: str,
) -> Path | None:
    settings = getattr(provider, "settings", None)
    workspace_root = getattr(settings, "workspace_root", None)
    if workspace_root is None:
        return None
    identity = {
        "schema_version": _QUALITATIVE_STAGE_CACHE_SCHEMA_VERSION,
        "uzi_source_commit": LATEST_UZI_SOURCE_COMMIT,
        "group_label": group_label,
        "model_provider": str(getattr(settings, "provider_name", "")),
        "model": str(getattr(settings, "default_model", "")),
        "reasoning_effort": str(getattr(settings, "openai_reasoning_effort", "")),
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
    }
    encoded = json.dumps(
        identity,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    digest = sha256(encoded).hexdigest()
    return Path(workspace_root) / "cache" / "uzi_qualitative_stages" / f"{digest}.json"


def _load_qualitative_stage_cache(path: Path | None) -> QualitativeGroupResult | None:
    if path is None or not path.exists():
        return None
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        if envelope.get("schema_version") != _QUALITATIVE_STAGE_CACHE_SCHEMA_VERSION:
            return None
        return QualitativeGroupResult.model_validate(envelope.get("payload"))
    except Exception as exc:
        _discard_qualitative_stage_cache(path, exc)
        return None


def _discard_qualitative_stage_cache(path: Path | None, exc: Exception) -> None:
    _LOGGER.warning(
        "Ignoring invalid UZI qualitative stage cache: path=%s error=%s:%s",
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


def _write_qualitative_stage_cache(
    path: Path | None,
    payload: QualitativeGroupResult,
) -> None:
    if path is None:
        return
    envelope = {
        "schema_version": _QUALITATIVE_STAGE_CACHE_SCHEMA_VERSION,
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
            "Unable to persist UZI qualitative stage cache: path=%s error=%s:%s",
            path,
            exc.__class__.__name__,
            str(exc)[:300],
        )


def _notify_qualitative_progress(
    progress_callback,
    *,
    analysis_cycle: int | None,
    completed: int,
    group_label: str,
    cache_hit: bool,
) -> None:
    if not callable(progress_callback):
        return
    cycle = int(analysis_cycle or 1)
    verb = "已恢复" if cache_hit else "已完成"
    progress_callback({
        "current_action": (
            f"UZI 第 {cycle} 轮：六维定性研究 {completed}/3，"
            f"{verb} {group_label}"
        ),
        "next_action": "继续完成剩余定性研究组并执行机械自审",
        "progress_percent": min(90, 82 + completed * 2),
        "stage": "qualitative_deep_dive",
        "stage_index": completed,
        "stage_total": 3,
        "cycle": cycle,
        "cache_hit": cache_hit,
    })


def _dimension_context(rows: list[dict[str, Any]], dimension: str) -> dict[str, Any]:
    snapshot_types = set(_DIMENSION_TYPES[dimension])
    selected = [
        row for row in rows
        if isinstance(row, dict) and str(row.get("snapshot_type") or "") in snapshot_types
    ]
    evidence_records: list[dict[str, str]] = []
    for row in selected:
        evidence_records.extend(_extract_evidence_records(row))
    deduped_records = list({row["url"]: row for row in evidence_records}.values())
    allowed_urls = [row["url"] for row in deduped_records]
    compact_rows = [
        {
            "snapshot_type": row.get("snapshot_type"),
            "symbol": row.get("symbol"),
            "snapshot_date": row.get("snapshot_date"),
            "data_period": row.get("data_period"),
            "quality_status": row.get("quality_status"),
            "facts": _compact(row.get("facts")),
            "source_refs": _compact(row.get("source_refs")),
        }
        for row in selected
    ]
    return {
        "snapshot_types": sorted(snapshot_types),
        "allowed_urls": allowed_urls,
        "evidence_records": deduped_records,
        "snapshots": compact_rows,
    }


def _extract_evidence_records(value: Any) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    if isinstance(value, dict):
        url = str(value.get("url") or "").strip().rstrip(".,;，。；)")
        if url.startswith(("http://", "https://")):
            title = str(value.get("title") or value.get("text") or "").strip()
            finding = str(
                value.get("finding")
                or value.get("body")
                or value.get("text_excerpt")
                or value.get("snippet")
                or ""
            ).strip()
            records.append({
                "url": url,
                "source": str(value.get("source_id") or value.get("source") or "artifactboard").strip(),
                "title": title[:300],
                "finding": finding[:2_000],
            })
        for item in value.values():
            records.extend(_extract_evidence_records(item))
    elif isinstance(value, list):
        for item in value:
            records.extend(_extract_evidence_records(item))
    return records


def _compact(value: Any, *, depth: int = 0) -> Any:
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        return value[:800]
    if depth >= 3:
        return None
    if isinstance(value, dict):
        return {str(key): _compact(item, depth=depth + 1) for key, item in list(value.items())[:40]}
    if isinstance(value, list):
        return [_compact(item, depth=depth + 1) for item in value[:20]]
    return str(value)[:300]


def _validate_group(
    group: QualitativeGroupResult,
    expected_dimensions: tuple[str, str],
    evidence_bundle: dict[str, dict[str, Any]],
) -> None:
    actual = [row.dimension_id for row in group.dimensions]
    if len(actual) != len(set(actual)) or set(actual) != set(expected_dimensions):
        raise ValueError(f"dimension membership mismatch: expected={expected_dimensions}, actual={actual}")
    for dimension in group.dimensions:
        allowed = set(evidence_bundle[dimension.dimension_id]["allowed_urls"])
        cited = [evidence.url for evidence in dimension.evidence]
        if len(set(cited)) < 2:
            raise ValueError(f"{dimension.dimension_id} requires two distinct cited URLs")
        ungrounded = sorted(set(cited) - allowed)
        if ungrounded:
            raise ValueError(f"{dimension.dimension_id} cited ungrounded URLs: {ungrounded}")


def _qualitative_gap(
    result: dict[str, Any],
    *,
    status: str,
    errors: list[str],
    evidence_bundle: dict[str, dict[str, Any]],
    completed_group_count: int = 0,
    partial_dimensions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    gaps = list(result.get("data_gap") or [])
    if "qualitative_deep_dive_required" not in gaps:
        gaps.append("qualitative_deep_dive_required")
    result["agent_reviewed"] = False
    result["quality_status"] = "data_gap"
    result["data_gap"] = gaps
    result["qualitative_deep_dive"] = dict(partial_dimensions or {})
    result["qualitative_agent_execution"] = {
        "required": True,
        "status": status,
        "agent_reviewed": False,
        "source_commit": LATEST_UZI_SOURCE_COMMIT,
        "group_count": 3,
        "completed_group_count": completed_group_count,
        "evidence_count": sum(len(context["allowed_urls"]) for context in evidence_bundle.values()),
        "association_count": sum(len(row.get("associations") or []) for row in (partial_dimensions or {}).values()),
        "errors": errors,
    }
    result["risk_flags"] = list(dict.fromkeys([*list(result.get("risk_flags") or []), "qualitative_deep_dive_incomplete"]))
    return result


__all__ = [
    "QualitativeAssociation",
    "QualitativeDimension",
    "QualitativeEvidence",
    "QualitativeGroupResult",
    "apply_qualitative_deep_dive",
]
