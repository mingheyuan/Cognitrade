"""Three-way qualitative research for UZI's full Agent workflow.

Collection remains in governed DomainPack skills.  This module receives the
frozen ArtifactBoard snapshots, dispatches the latest UZI qualitative groups
in parallel, and only accepts citations that were present in those artifacts.
It never invents evidence or performs an external UZI runtime call.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from threading import Event, get_ident
from time import monotonic
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationInfo,
    field_validator,
    model_validator,
)

from domain_packs.finance_ashare.subagents.uzi_agent_roleplay import (
    LATEST_UZI_SOURCE_COMMIT,
)
from .uzi_evidence_bindings import bind_citation, citation_validation_context, document_attribution_conflicts
from domain_packs.finance_ashare.subagents.uzi_input_views import (
    DEFAULT_PROMPT_VIEW_MAX_CHARACTERS,
    UziInputBudgetExceeded,
)
from domain_packs.finance_ashare.subagents.uzi_llm_support import (
    EXHAUSTED_MODEL_FAILURES,
    UziFactualEvidenceValidationError,
    UziQualitativeValidationError,
    model_failure_record,
    parse_uzi_structured,
    resolve_uzi_llm_worker_count,
    submit_uzi_stage,
)
from domain_packs.finance_ashare.subagents.uzi_native_agent_protocol import (
    native_factcheck_protocol,
)
from domain_packs.finance_ashare.subagents.uzi_stage_progress import (
    failed_stage_next_action,
    notify_stage_progress,
)

_LOGGER = logging.getLogger("uvicorn.error")
_QUALITATIVE_STAGE_CACHE_SCHEMA_VERSION = "uzi-3.9.4-qualitative-stage-v10-source-roles"

# Shared by report research and data-center research; this explains provenance
# without introducing a new validator or a verbatim-quote research contract.
QUALITATIVE_SOURCE_ATTRIBUTION_RULES = (
    "\nRESEARCH_SOURCE_ATTRIBUTION_V1\n"
    "raw/已有快照是固定采集结果，可以参与分析；保留其 source/error/_timeout 等来源与质量含义，"
    "不能借用相邻文档的 URL 充当其来源。"
    "allowed_urls 仅表示可引用目录，不代表其中任一文档支持任一 finding。"
    "有原文支持的事实可以转述，不要求逐字引用；推断应写明依据、条件与无法量化的限制，不能冒充原文事实。"
    '事实本身缺少可核实依据时，source="unknown"，把缺口解释放在 finding，'
    "不在 source 中追加缺口说明，不附无关 URL，不编造抓取日期；没有来源时 url/retrieved_at 用 null。"
    "提供者未知但文档确有支持的事实，仍可使用 source=unknown 并引用该文档的真实绑定 URL 和归档日期。"
    "无 URL 的 unknown 只披露缺口，不能充当有效事实引用或无风险结论。"
    "同一 URL 可以支持不同事实，不得为满足证据数量要求补造关联或重复同一 finding。\n"
    "SOURCE_ROLES_V1：snapshot_origins 给出结构化事实的原始快照、字段路径和提供者；"
    "这些行情/接口数值可以用于 conclusion 和 associations，明确写出原始来源，不能挂到公司 PDF 上。"
    "采集未查询、映射缺失、接口失败属于数据限制，只能作 unknown 披露，不能引用公司公告当作其出处。"
    "evidence 中带文档 URL 的 finding 必须由该文档支持；晚于原文发布时点的实际收盘行情不能归因于该原文。"
    "原始 historical_evidence 字段也可能含旧版接口导航，source_discovery 标出的入口不是研究文章；"
    "不要把它当事实引用或再次提交正文归档。结构化数据本身仍保留，不代表该维度完全无数据。\n"
)

_DIMENSION_TYPES: dict[str, tuple[str, ...]] = {
    "3_macro": ("macro",),
    "13_policy": ("policy",),
    "7_industry": ("industry",),
    "15_events": ("events",),
    "8_materials": ("materials",),
    "9_futures": ("futures",),
}
_COMPANY_CONTEXT_TYPES = {
    "market", "fundamental", "financial", "peers", "chain", "research",
    "valuation", "governance", "moat",
    *(kind for kinds in _DIMENSION_TYPES.values() for kind in kinds),
}
_GROUPS: tuple[tuple[str, tuple[str, str]], ...] = (
    ("A · Macro-Policy", ("3_macro", "13_policy")),
    ("B · Industry-Events", ("7_industry", "15_events")),
    ("C · Cost-Transmission", ("8_materials", "9_futures")),
)
class QualitativeEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1)
    url: str | None = Field(default=None, min_length=8)
    finding: str = Field(min_length=1)
    retrieved_at: str | None = None
    evidence_id: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="before")
    @classmethod
    def bind_archive(cls, value: Any, info: ValidationInfo) -> Any:
        # Persisted, already bound output is validated without a context by the
        # cache/schema readers; new SDK output receives the exact frozen set.
        return bind_citation(value, info.context) if info.context is not None else value

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith(("http://", "https://")):
            raise ValueError("evidence URL must be HTTP(S)")
        return value

    @model_validator(mode="after")
    def validate_disclosed_unknown(self) -> QualitativeEvidence:
        if self.source.strip().lower() == "unknown":
            # Native task2.5 explicitly permits absent public facts to be
            # disclosed. Do not invent a URL or retrieval date for an absence.
            # Native task2.5 requires an explanation, not a host vocabulary.
            # Structural acceptance does not verify its semantic quality or
            # make a URL-less disclosure count as factual evidence.
            if not self.finding.strip():
                raise ValueError("unknown evidence must explicitly explain the missing fact")
        # Native Task 2.5 requires URL and finding, but retrieved_at appears
        # only in its examples, not its field constraints. Archive/PIT checks
        # validate the underlying fixed source independently of this display
        # metadata; never invent an observation date to satisfy a local gate.
        elif self.url is None or not self.finding.strip():
            raise UziFactualEvidenceValidationError(missing_fields=tuple(
                field for field, missing in (
                    ("url", self.url is None),
                    ("finding", not self.finding.strip()),
                ) if missing
            ))
        return self


class QualitativeAssociation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    link_to: str = Field(min_length=1)
    chain_id: str = Field(min_length=1)
    causal_chain: str = Field(min_length=1)
    estimated_impact: str = Field(min_length=1)

    @field_validator("causal_chain", "estimated_impact")
    @classmethod
    def validate_nonempty_text(cls, value: str) -> str:
        # Task 2.5 requires substantive content, not host character quotas.
        # Nonempty structure alone does not establish its semantic quality.
        if not value.strip():
            raise ValueError("qualitative association text must be nonempty")
        return value


class QualitativeDimension(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension_id: Literal["3_macro", "13_policy", "7_industry", "15_events", "8_materials", "9_futures"]
    evidence: list[QualitativeEvidence] = Field(min_length=2)
    associations: list[QualitativeAssociation] = Field(default_factory=list)
    conclusion: str = Field(min_length=1)

    @field_validator("conclusion")
    @classmethod
    def validate_nonempty_conclusion(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("qualitative conclusion must be nonempty")
        return value


class QualitativeGroupResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    _frozen_read_audit: dict = PrivateAttr(default_factory=dict)
    _citation_binding_audit: list = PrivateAttr(default_factory=list)

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
    repair_feedback: list[str] | None = None,
    research_profile: Literal["lite", "medium", "deep"] = "deep",
    previous_failures: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    result = deepcopy(deep_analysis)
    provider_available = llm_provider is not None and bool(getattr(llm_provider, "available", True))
    use_reader = getattr(llm_provider, "supports_frozen_reading", False) is True
    scoped_rows, company_context, stock_documents = _fixed_report_inputs(
        snapshot_rows, deep_analysis, bounded_view=not use_reader,
    )
    evidence_bundle = {
        dimension: _dimension_context(scoped_rows, dimension, stock_documents=stock_documents,
                                      include_prompt_snapshots=not use_reader)
        for dimension in _DIMENSION_TYPES
    }
    validation_context = citation_validation_context(evidence_bundle, stock_documents)
    corpus = None
    if use_reader:
        from .uzi_frozen_research import READER_INSTRUCTIONS, research_documents
        corpus = research_documents(scoped_rows, company_context, stock_documents, evidence_bundle)
    if not provider_available:
        return _qualitative_gap(
            result,
            status="required_but_unavailable",
            errors=["agent_os_llm_provider_unavailable"],
            evidence_bundle=evidence_bundle,
        )
    system_prompt = (
        "你是 UZI 完整 Agent 工作流的六维定性研究执行器。已核实结论只能引用本次输入包里的 URL 和事实；"
        "禁止补造来源、补造数字或把爬虫片段直接改写成无引用结论。每维至少两条证据，"
        "同一真实 URL 中不同 finding 可以分别引用，重复同一 finding 不能凑数。"
        f"{QUALITATIVE_SOURCE_ATTRIBUTION_RULES}"
        "证据缺口不是启动分析的门槛：完成可完成的研判并披露未知项。"
        "报告阶段不联网补采，仅正常调用模型分析固定输入；最终是否可交付由输出质量门判断。"
        "当前资料是本次研究工作版，不是永久不可扩展的白名单。"
        "尚未归档的新来源不能冒充本轮已核实证据；宿主会将此类公开 URL 列为待核实补证请求，"
        "由用户显式启动数据中心归档后，用新输入版本继续研究，不覆盖旧版。"
        "并明确跨域因果链。三个分析组共用同一冻结证据前缀。\n"
        "FROZEN_COMPANY_CONTEXT 是本次固定公司的身份、日期及已有事实背景，所有组均可读取；"
        "shared_native_transport 按内容 hash 保存重复的原生顶层信息，"
        "materialized_transport_view.native_transport_refs 关联其原快照，引用不是缺失值；"
        "native_data_field_refs 把与原生 data 完全相同的平铺字段关联到本快照 facts 内的原路径，"
        "按路径数组读取完整值；类型不同或内容冲突的两份值仍各自保留，引用不是未知值。"
        "normalized_payload 的 hash 仅标识留存的宿主审计载荷，不是新增研究事实。"
        "cutoff_at 为 null 时精确截止未提供，不得把当前时刻或日末捏造为截止。"
        "FROZEN_STOCK_DOCUMENTS 中的同股固定原文允许跨维引用不同事实；维度归属不是文档引用门禁。"
        "引用的 retrieved_at 使用该原文 archive_bindings 中的 observed_at，"
        "优先返回 archive_bindings 中明确的 evidence_id；宿主按该唯一冻结绑定填入 url 和 retrieved_at，"
        "这两个元数据字段可返回 null，不必重抄。不能选择未知 ID；同 URL 存在多个归档绑定时必须指定 ID。"
        "published_at 是原文发布日期，不能把相邻快照时间或当前时间冒充原文获取时间；"
        "未提供的时间保持未知，不编造。多个归档绑定分别列明，不混合不同来源的时间与正文。"
        "其中保留的是全部已冻结材料化文本，不保证归档包含原始文档全文；"
        "保留原文页码与截断边界，不得把可见片段中的未披露外推为整份文件或历年均无相关事项。"
        "\n"
        f"UZI_SOURCE_COMMIT={LATEST_UZI_SOURCE_COMMIT}\n"
        + (READER_INSTRUCTIONS + "\nFROZEN_CORPUS=" + json.dumps(corpus.identity, sort_keys=True)
           if corpus is not None else "FROZEN_COMPANY_CONTEXT="
        + json.dumps(company_context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\nFROZEN_STOCK_DOCUMENTS="
        + json.dumps(stock_documents, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\nFROZEN_EVIDENCE_BUNDLE="
        + json.dumps(evidence_bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    )
    completed: list[QualitativeGroupResult] = []
    read_audits = {}
    binding_audits = {}
    pending_evidence = []
    errors: list[str] = []
    failure_records: list[dict[str, Any]] = []
    pending_groups: list[tuple[str, tuple[str, str], str, Path | None]] = []
    for label, dimensions in _GROUPS:
        prior = [row for row in previous_failures or [] if row.get("stage") == f"qualitative_{label}"]
        blocked = next((row for row in prior if row.get("kind") in {*EXHAUSTED_MODEL_FAILURES, "source_gap"}), None)
        if blocked:
            # Preserve the original model failure alongside the input cause.
            # The first attempt is never skipped: this requires prior failure.
            failure_records.extend(dict(row) for row in prior)
            reason = "collect_data:new_manifest_required" if blocked["kind"] == "source_gap" else "stage_budget_already_exhausted"
            errors.append(f"{label}:{blocked['kind']}:{reason}")
            continue
        canonical_user_prompt = _group_user_prompt(
            group_label=label,
            dimensions=dimensions,
            evidence_bundle=evidence_bundle,
            shared_evidence=True,
        )
        user_prompt = _group_user_prompt(
            group_label=label,
            dimensions=dimensions,
            evidence_bundle=evidence_bundle,
            repair_feedback=_group_repair_feedback(label, repair_feedback),
            shared_evidence=True,
        )
        # Validate every planned request before dispatching the parallel wave.
        # Do not silently truncate an archive to fit, including during repair.
        _check_qualitative_prompt_budget(system_prompt, canonical_user_prompt)
        _check_qualitative_prompt_budget(system_prompt, user_prompt)
        cache_path = _qualitative_stage_cache_path(
            llm_provider,
            group_label=label,
            system_prompt=system_prompt,
            user_prompt=canonical_user_prompt,
        )
        cached = _load_qualitative_stage_cache(cache_path)
        if cached is None:
            pending_groups.append((
                label,
                dimensions,
                user_prompt,
                cache_path,
            ))
            continue
        try:
            _validate_group(cached, dimensions, evidence_bundle)
            if not _group_citations_complete(cached):
                raise ValueError("cached qualitative output has incomplete citations")
        except Exception as exc:
            _discard_qualitative_stage_cache(cache_path, exc)
            pending_groups.append((
                label,
                dimensions,
                user_prompt,
                cache_path,
            ))
            continue
        completed.append(cached)
        binding_audits[label] = cached._citation_binding_audit
        if cached._frozen_read_audit:
            read_audits[label] = {**cached._frozen_read_audit, "cache_hit": True}
        _LOGGER.info("UZI qualitative stage cache hit: group=%s", label)
        _notify_qualitative_progress(
            progress_callback,
            analysis_cycle=analysis_cycle,
            completed=len(completed),
            group_label=label,
            cache_hit=True,
        )
    with ThreadPoolExecutor(
        max_workers=resolve_uzi_llm_worker_count(
            llm_provider,
            len(pending_groups),
            ceiling=3,
        ),
        thread_name_prefix="uzi-qualitative",
    ) as pool:
        futures = {
            submit_uzi_stage(pool,
                _evaluate_group,
                llm_provider,
                system_prompt=system_prompt,
                group_label=label,
                dimensions=dimensions,
                evidence_bundle=evidence_bundle,
                user_prompt=user_prompt,
                research_profile=research_profile,
                frozen_corpus=corpus,
                validation_context=validation_context,
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
                if group._frozen_read_audit:
                    read_audits[label] = group._frozen_read_audit
                from research_system.market_data.research_requests import pending_source_requests
                pending_evidence.extend(pending_source_requests(group, evidence_bundle))
                _validate_group(group, expected_dimensions, evidence_bundle)
                completed.append(group)
                binding_audits[label] = group._citation_binding_audit
                # Honest unknowns are valid partial output, not a successful
                # research cache entry. Otherwise a repair only rereads the
                # same incomplete response without ever calling the Agent.
                if _group_citations_complete(group):
                    _write_qualitative_stage_cache(cache_path, group)
                _notify_qualitative_progress(
                    progress_callback,
                    analysis_cycle=analysis_cycle,
                    completed=len(completed),
                    group_label=label,
                    cache_hit=False,
                )
            except Exception as exc:
                if audit := getattr(exc, "frozen_read_audit", None):
                    read_audits[label] = audit
                failure = model_failure_record(exc, stage=f"qualitative_{label}")
                failure_records.append(failure)
                error = (
                    f"{label}:{failure['error_type']}:{failure['kind']} "
                    f"fields={failure.get('validation_fields', [])} "
                    f"reasons={failure.get('validation_reasons', [])}"
                )
                errors.append(error)
                _LOGGER.warning(
                    "UZI qualitative group rejected: group=%s completed_groups=%d error=%s",
                    label,
                    len(completed),
                    error,
                )
                _notify_qualitative_rejection(
                    progress_callback,
                    analysis_cycle=analysis_cycle,
                    completed=len(completed),
                    group_label=label,
                    failure=failure,
                )

    dimensions = {
        # evidence_id is an optional host transport hint, not a native report
        # field or delivery requirement. Keep its provenance in execution audit.
        dimension.dimension_id: dimension.model_dump(
            mode="json", exclude={"evidence": {"__all__": {"evidence_id"}}},
        )
        for group in completed
        for dimension in group.dimensions
    }
    expected = set(_DIMENSION_TYPES)
    association_count = sum(len(row["associations"]) for row in dimensions.values())
    for dimension, row in dimensions.items():
        # This is an output check, after the Agent has attempted the task.
        # Native evidence entries each require a URL. Keep honest URL-less
        # unknowns separately; they cannot pad the two-citation requirement.
        # The same frozen URL can support different findings, including one
        # that discloses an unknown source/field in that actual document.
        row["disclosures"] = [evidence for evidence in row["evidence"] if not evidence.get("url")]
        row["evidence"] = [evidence for evidence in row["evidence"] if evidence.get("url")]
        grounded_count = len(row["evidence"])
        if grounded_count == 0:
            errors.append(f"{dimension}:no_grounded_citation_after_analysis")
        elif grounded_count < 2:
            errors.append(f"{dimension}:grounded_citations_below_two_after_analysis:{grounded_count}")
        if grounded_count < 2 and evidence_bundle[dimension]["allowed_urls"]:
            label = next(label for label, members in _GROUPS if dimension in members)
            failure_records.append({
                "stage": f"qualitative_{label}",
                "kind": "agent_content_invalid",
                "error_type": "QualitativeCitationIncomplete",
                "validation_fields": [f"{dimension}.evidence"],
                "validation_reasons": [{"field": f"{dimension}.evidence", "code": "qualitative_citations_incomplete"}],
            })
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
            failure_records=_with_frozen_input_failures(failure_records, evidence_bundle),
            read_audits=read_audits,
            binding_audits=binding_audits,
            pending_evidence=pending_evidence,
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
        "input_gaps": _input_evidence_gaps(evidence_bundle),
        "unknown_evidence_count": sum(
            str(evidence.get("source") or "").strip().lower() == "unknown"
            for row in dimensions.values() for evidence in [*row["evidence"], *row["disclosures"]]
        ),
        "errors": [],
        "frozen_read_audits": read_audits,
        "citation_binding_audits": binding_audits,
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
    elif base_status == "degraded" or remaining_gaps or result["qualitative_agent_execution"]["unknown_evidence_count"]:
        result["quality_status"] = "degraded"
    else:
        result["quality_status"] = "available"
    result["data_gap"] = remaining_gaps
    result["risk_flags"] = [flag for flag in list(result.get("risk_flags") or []) if flag != "qualitative_deep_dive_incomplete"]
    return result


def _check_qualitative_prompt_budget(system_prompt: str, user_prompt: str) -> None:
    if len(system_prompt) + len(user_prompt) > DEFAULT_PROMPT_VIEW_MAX_CHARACTERS:
        raise UziInputBudgetExceeded(
            "input_budget_exceeded: qualitative request exceeds "
            f"{DEFAULT_PROMPT_VIEW_MAX_CHARACTERS} characters; frozen archive was not truncated"
        )


def _evaluate_group(
    provider: Any,
    *,
    system_prompt: str,
    group_label: str,
    dimensions: tuple[str, str],
    evidence_bundle: dict[str, dict[str, Any]],
    user_prompt: str | None = None,
    research_profile: Literal["medium", "deep"] = "deep",
    frozen_corpus=None,
    validation_context: dict | None = None,
) -> QualitativeGroupResult:
    user_prompt = user_prompt or _group_user_prompt(
        group_label=group_label,
        dimensions=dimensions,
        evidence_bundle=evidence_bundle,
    )
    _check_qualitative_prompt_budget(system_prompt, user_prompt)
    started_at = monotonic()
    _LOGGER.info(
        "UZI qualitative stage started: group=%s dimensions=%s prompt_chars=%d",
        group_label,
        ",".join(dimensions),
        len(system_prompt) + len(user_prompt),
    )
    try:
        local_context = {
            **(validation_context if validation_context is not None else citation_validation_context(evidence_bundle)),
            "citation_binding_audit": [],
        }
        result = parse_uzi_structured(
            provider,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=QualitativeGroupResult,
            validation_context=local_context,
            temperature=0.1,
            research_profile=research_profile,
            uzi_stage=f"qualitative_{group_label}",
            **({"frozen_corpus": frozen_corpus} if frozen_corpus is not None else {}),
        )
        metadata = getattr(provider, "last_call_metadata", {})
        result._frozen_read_audit = metadata.get("frozen_read_audit", {}) if isinstance(metadata, dict) else {}
        result._citation_binding_audit = local_context["citation_binding_audit"]
    except Exception as exc:
        metadata = getattr(provider, "last_call_metadata", {})
        if isinstance(metadata, dict) and metadata.get("frozen_read_audit"):
            exc.frozen_read_audit = metadata["frozen_read_audit"]
        _LOGGER.warning(
            "UZI qualitative stage failed: group=%s dimensions=%s elapsed=%.1fs failure=%s",
            group_label,
            ",".join(dimensions),
            monotonic() - started_at,
            model_failure_record(exc, stage=f"qualitative_{group_label}"),
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
    repair_feedback: list[str] | None = None,
    workflow: Literal["report", "data_center"] = "report",
    shared_evidence: bool = False,
) -> str:
    # The pinned manual owns the questions, source-discovery instructions and
    # causal-chain requirements. Do not replace it with a host-written précis.
    protocol = (
        Path(__file__).resolve().parents[1]
        / "vendor/uzi_3_9_2/skill/references/task2.5-qualitative-deep-dive.md"
    ).read_text(encoding="utf-8")
    prompts = {
        "A · Macro-Policy": "分析增长/通胀/利率/流动性向公司与行业的传导，以及政策目标、实施工具、受益与约束。",
        "B · Industry-Events": "分析行业生命周期、供需和竞争格局，并区分公告/事件的时间、确定性与财务传导。",
        "C · Cost-Transmission": "从已识别原材料映射到相关期货，分析成本变动、价格传导、毛利率时滞与对冲条件。",
    }
    repair_instruction = ""
    if repair_feedback:
        repair_instruction = (
            "\nUZI_REPAIR_FEEDBACK="
            + json.dumps(repair_feedback, ensure_ascii=False, separators=(",", ":"))
            + "\n这是上一轮 suggested_fix 对应的机械校验错误。逐条修正；"
            "已核实引用的 evidence.url 必须逐字等于本次证据 allowed_urls 中的一个值（包括本股固定原文的跨维复用），"
            "禁止改造 URL 或把不支持该结论的旧来源替换上去。确需新资料的 URL 仅作为待核实线索，"
            "标明 source=unknown 并解释补证需求；宿主不会将其计入有效证据，而会交给显式数据中心补证。"
            "qualitative_source_attribution_conflict 表示把结构化快照/本地查询结果、采集状态或发布日之后的实际行情误归给原文；"
            "应保留真实数据来源，将采集限制单独披露，并用该原文实际支持的不同事实完成引用，不能更换链接掩盖。"
        )
    return (
        f"负责分组：{group_label}\n"
        # DC wraps this native research result alongside separately authorized
        # fact proposals. Restrict that nested result, not the whole response.
        f"{'research.dimensions ' if workflow == 'data_center' else ''}必须且只能返回维度 {list(dimensions)}。{prompts[group_label]}"
        "每维 evidence 至少两条，已核实引用的 URL 原样取自本次输入包，不要求两个不同 URL；"
        "同 URL 可以支持不同 finding，但相同 finding 不得重复。六维总体需要至少三条跨域 associations，"
        "本组尽量提供至少一条有依据的因果链；缺失事实只能标 source=unknown 并明确说明，"
        "无 URL 的 unknown 会单列缺口披露，不能算作每维要求的两条有效引用；"
        "不虚构 URL、时间或数字；未验证的传导只能写条件分析及无法量化的限制。\n"
        + repair_instruction
        + "\n原生方法论全文如下。回答本组每个必答问题；其他组用于跨域关联。"
        "其中资料发现、浏览和归档由用户显式启动的数据中心任务负责，"
        "报告阶段禁止执行其中的联网采集动作，只使用本次固定证据；缺源明确请求补采和新输入版本。"
        "历史研究中的‘当前/近期’均以本次证据截止时间为准，不引用之后事实。\n"
        "NATIVE_TASK_2_5_PROTOCOL_BEGIN\n"
        + protocol
        + "\nNATIVE_TASK_2_5_PROTOCOL_END\n"
        + "\n" + native_factcheck_protocol() + "\n"
        # Report requests already carry the complete six-dimensional bundle
        # in the shared system prefix. Repeating two dimensions here consumed
        # the input budget without supplying another fact. Standalone/DC
        # callers still carry their scoped evidence inline.
        + ("\n本组证据引用=" + json.dumps({
            "container": "FROZEN_EVIDENCE_BUNDLE", "dimensions": list(dimensions),
        }, ensure_ascii=False, sort_keys=True) if shared_evidence else
           "\n本组证据=" + json.dumps({key: evidence_bundle[key] for key in dimensions},
                                    ensure_ascii=False, sort_keys=True))
    )


def _group_repair_feedback(
    group_label: str,
    repair_feedback: list[str] | None,
) -> list[str]:
    """Keep UZI recovery feedback scoped to the group that actually failed."""

    if not repair_feedback:
        return []
    scoped = [
        str(error)[:500]
        for error in repair_feedback
        if str(error).startswith(f"{group_label}:")
    ]
    return list(dict.fromkeys(scoped))[-8:]


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
        "reasoning_effort": str(getattr(settings, "codex_reasoning_effort", "")),
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
        result = QualitativeGroupResult.model_validate(envelope.get("payload"))
        result._frozen_read_audit = envelope.get("frozen_read_audit") or {}
        result._citation_binding_audit = envelope.get("citation_binding_audit") or []
        return result
    except Exception as exc:
        _discard_qualitative_stage_cache(path, exc)
        return None


def _discard_qualitative_stage_cache(path: Path | None, exc: Exception) -> None:
    _LOGGER.warning(
        "Ignoring invalid UZI qualitative stage cache: failure=%s",
        model_failure_record(exc, stage="qualitative_cache"),
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
        "frozen_read_audit": payload._frozen_read_audit,
        "citation_binding_audit": payload._citation_binding_audit,
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
            "Unable to persist UZI qualitative stage cache: error_type=%s",
            exc.__class__.__name__,
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
    notify_stage_progress(progress_callback, {
        "current_action": (
            f"UZI 第 {cycle} 轮：六维定性研究 {completed}/3，"
            f"{verb} {group_label}"
        ),
        "next_action": "继续剩余研究组；研究完成后交给后续评委与综合研判",
        "progress_percent": round(completed / 3 * 100),
        "progress_scope": "qualitative_groups",
        "completed_stages": completed,
        "stage_phase": "validated",
        "stage": "qualitative_deep_dive",
        "stage_index": completed,
        "stage_total": 3,
        "cycle": cycle,
        "cache_hit": cache_hit,
        **({"cache_reason": "content_profile_source_prompt_match"} if cache_hit else {}),
    })


def _notify_qualitative_rejection(
    progress_callback,
    *,
    analysis_cycle: int | None,
    completed: int,
    group_label: str,
    failure: dict[str, Any],
) -> None:
    if not callable(progress_callback):
        return
    cycle = int(analysis_cycle or 1)
    notify_stage_progress(progress_callback, {
        "current_action": (
            f"UZI 第 {cycle} 轮：{group_label} 未通过执行或证据校验，"
            f"已保留 {completed}/3 个通过组"
        ),
        "next_action": failed_stage_next_action(failure),
        "progress_percent": round(completed / 3 * 100),
        "progress_scope": "qualitative_groups",
        "completed_stages": completed,
        "stage_phase": "failed",
        "stage": "qualitative_deep_dive_repair",
        "stage_index": completed,
        "stage_total": 3,
        "cycle": cycle,
        "cache_hit": False,
    })


def _symbol_identity(value: Any) -> tuple[str, str, str] | None:
    from research_system.adapters.vendors.market_router import MarketRouter

    symbol = str(value or "").strip().upper()
    if not symbol:
        return None
    route = MarketRouter().classify(symbol=symbol)
    return route.market, route.exchange, str(route.normalized_symbol or symbol)


def _fixed_report_inputs(
    rows: list[dict[str, Any]], deep_analysis: dict[str, Any], *, bounded_view: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    """Read-only view of this stock's already materialized inputs, never fetch.

    Legacy unscoped rows retain their dimension-local behavior; they cannot
    establish a stock identity or widen the global stock-document pool.
    """
    from domain_packs.finance_ashare.subagents.uzi_input_views import (
        PROMPT_EVIDENCE_VIEW_VERSION,
        compact_evidence_view,
    )

    candidates = [row for row in rows if isinstance(row, dict)]
    symbols = {_symbol_identity(row.get("symbol")): row.get("symbol") for row in candidates if row.get("symbol")}
    symbol = deep_analysis.get("symbol") or deep_analysis.get("ticker")
    if not symbol and len(symbols) == 1:
        symbol = next(iter(symbols.values()))
    target = _symbol_identity(symbol)
    scoped = [row for row in candidates if (
        not row.get("symbol") or (target is not None and _symbol_identity(row.get("symbol")) == target)
    )]
    dates = {str((row.get("query_manifest") or {}).get("requested_as_of_date") or row.get("snapshot_date"))
             for row in scoped if (row.get("query_manifest") or {}).get("requested_as_of_date") or row.get("snapshot_date")}
    as_of_date = deep_analysis.get("as_of_date") or (next(iter(dates)) if len(dates) == 1 else None)
    scoped = [row for row in scoped if (
        (row.get("query_manifest") or {}).get("pit_safe") is not False
        and (not as_of_date or not row.get("snapshot_date") or str(row["snapshot_date"])[:10] <= str(as_of_date)[:10])
    )]
    cutoffs = {str((row.get("query_manifest") or {}).get("cutoff_at")) for row in scoped
               if (row.get("query_manifest") or {}).get("cutoff_at")}
    cutoff_at = next(iter(cutoffs)) if len(cutoffs) == 1 else None
    truncations: list[dict[str, Any]] = []
    prompt_rows, shared_transport = _materialized_transport_view(scoped)
    # Native task2.5 needs company/business/financial context, not every
    # holdings map or price tick. Keep every input's identity and PIT metadata
    # but project facts only for relevant fixed dimensions. Archived documents
    # remain shareable regardless of which dimension originally stored them.
    context_rows = []
    for row in prompt_rows:
        item = {key: row[key] for key in (
            "snapshot_type", "dimension", "dimension_id", "symbol", "snapshot_id", "content_hash",
            "snapshot_date", "data_period", "published_at", "observed_at", "effective_at",
            "quality_status", "availability_status", "query_manifest", "materialized_transport_view",
        ) if key in row}
        if row.get("snapshot_type") in _COMPANY_CONTEXT_TYPES:
            item["facts"] = row.get("facts")
        else:
            item["facts_view_status"] = "outside_company_background_view_not_missing"
        context_rows.append(item)
    # The pre-projection hash binds even facts beyond a displayed text prefix
    # or sampled array, plus IDs, content hashes and PIT manifest metadata.
    context = {
        "symbol": symbol, "as_of_date": as_of_date,
        "as_of_date_source": "deep_analysis" if deep_analysis.get("as_of_date") else "fixed_snapshot_metadata",
        "cutoff_at": cutoff_at,
        "cutoff_status": "provided_by_fixed_manifest" if cutoff_at else "exact_cutoff_unavailable_or_conflicting",
        "fixed_input_hash": sha256(json.dumps(scoped, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "shared_native_transport": compact_evidence_view(
            shared_transport, max_sequence_items=20, max_text_chars=800,
            path="company_context.shared_native_transport", truncations=truncations,
        ) if bounded_view else shared_transport,
        "snapshots": compact_evidence_view(
            context_rows, max_sequence_items=20, max_text_chars=800,
            path="company_context.snapshots", truncations=truncations,
            # Keep every fixed dimension, including 0_basic regardless of
            # input ordering. Nested series remain bounded and disclosed.
            preserve_sequences_at=frozenset({"company_context.snapshots"}),
        ) if bounded_view else context_rows,
        "prompt_view": {"version": PROMPT_EVIDENCE_VIEW_VERSION, "truncations": truncations,
                        "fact_snapshot_types": sorted(_COMPANY_CONTEXT_TYPES),
                        "omission_semantics": "__uzi_prompt_omitted__ is a view omission, not missing source data or a factual claim"},
    }
    # Keep the original rows in fixed_input_hash. A new manifest may retain
    # both an old inline prefix and a verified full representation of the
    # same original PDF; that is provenance, not two independent fact bodies.
    prompt_rows, representations = _document_representation_view(prompt_rows, target)
    stock_documents = []
    for row in prompt_rows:
        if target is not None and _symbol_identity(row.get("symbol")) == target:
            stock_documents.extend(_archived_document_records(row, preserve_full_text=True))
    for record in stock_documents:
        representation = representations.get((record["content_hash"], record["url"]))
        if representation is not None:
            record.update(representation)
    documents = {}
    for record in stock_documents:
        key = (record["url"], record["finding"], record["title"])
        previous = documents.get(key)
        if previous is not None:
            # Equal bodies are one document, not one provenance relationship.
            # Preserve every fixed owner/observation instead of silently
            # selecting one acquisition time when deduplicating the body.
            bindings = [*previous["archive_bindings"], *record["archive_bindings"]]
            record["archive_bindings"] = list({
                json.dumps(binding, ensure_ascii=False, sort_keys=True): binding
                for binding in bindings
            }.values())
        documents[key] = record
    return prompt_rows, context, list(documents.values())


def _materialized_transport_view(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Share root transport and separate materializer audit from model facts.

    BundleMaterializer has already applied the pinned native projection. Its
    ``facts`` contains that result, plus a full normalized audit copy and root
    transport repeated for every dimension. Do not run a newer projection on
    old inputs or recursively remove similarly named provider fields. Keep
    native errors/recoveries and audit validation, bind the omitted audit by
    hash, and share only exactly equal roots. Conflicting roots stay distinct.
    The caller hashes the *original* scoped rows for cache/input identity.
    """
    projected = []
    shared: dict[str, Any] = {}
    for row in rows:
        original = row.get("facts")
        if not isinstance(original, dict):
            projected.append(row)
            continue
        facts = dict(original)
        metadata: dict[str, Any] = {"version": "materialized-research-transport-v2"}
        native = facts.get("_uzi_raw_result")
        native_data = native.get("data") if isinstance(native, dict) else None
        data_refs = {}
        if isinstance(native_data, dict):
            for key, value in native_data.items():
                if not isinstance(key, str) or key not in facts or key.startswith("_"):
                    continue
                # JSON equality, not Python equality (True == 1), and only
                # the materializer's top-level duplicate. Never recursively
                # remove provider fields or collapse conflicting versions.
                if json.dumps(facts[key], ensure_ascii=False, sort_keys=True) == json.dumps(
                    value, ensure_ascii=False, sort_keys=True,
                ):
                    data_refs[key] = ["_uzi_raw_result", "data", key]
                    del facts[key]
        if data_refs:
            metadata["native_data_field_refs"] = data_refs
        refs = {}
        for key in ("_uzi_root_fields", "_uzi_top_level_fields"):
            value = facts.get(key)
            if not isinstance(value, dict):
                continue
            serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            digest = sha256(serialized.encode()).hexdigest()
            shared.setdefault(digest, value)
            refs[key] = digest
            del facts[key]
        if refs:
            metadata["native_transport_refs"] = refs
        audit = facts.get("_market_data_audit")
        if isinstance(audit, dict) and "normalized_payload" in audit:
            serialized = json.dumps(audit["normalized_payload"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            metadata["normalized_payload"] = {
                "content_hash": sha256(serialized.encode()).hexdigest(),
                "json_characters": len(serialized),
                "status": "retained_in_frozen_input_not_additional_research_facts",
            }
            facts["_market_data_audit"] = {key: value for key, value in audit.items() if key != "normalized_payload"}
        if len(metadata) > 1:
            projected.append({**row, "facts": facts, "materialized_transport_view": metadata})
        else:
            projected.append(row)
    return projected, shared


def _verified_materialized_text_binding(row: dict[str, Any], ref: dict[str, Any]) -> dict[str, Any] | None:
    """Verify the frozen in-memory body, never discover/reopen a sidecar.

    BundleMaterializer already checks the immutable evidence-key association
    and source file. Its materialized ref carries the exact explicit binding;
    recheck that binding and body here before changing only its prompt view.
    """
    from research_system.market_data.research_archive_text import (
        MAX_EXTRACTED_TEXT_BYTES,
        TEXT_EXTRACTION_VERSION,
    )

    binding = ref.get("text_extraction")
    if not isinstance(binding, dict):
        return None
    archive = (row.get("query_manifest") or {}).get("research_archive")
    expected = archive.get("text_extractions", []) if isinstance(archive, dict) else []
    if not isinstance(expected, list) or not any(
        isinstance(item, dict) and isinstance(item.get("evidence_key"), str)
        and item.get("evidence_key") and item.get("binding") == binding for item in expected
    ):
        return None
    content = ref.get("content")
    if not isinstance(content, str) or not content:
        return None
    body = content.encode("utf-8")
    digest = sha256(body).hexdigest()
    source_hash = ref.get("content_hash")
    path = Path(str(binding.get("text_content_ref") or ""))
    if (
        binding.get("version") != TEXT_EXTRACTION_VERSION
        or not isinstance(source_hash, str) or len(source_hash) != 64
        or any(character not in "0123456789abcdef" for character in source_hash)
        or binding.get("source_content_hash") != source_hash
        or binding.get("complete") is not True
        or binding.get("full_document_reading_claimed") is not False
        or binding.get("text_content_hash") != digest
        or type(binding.get("text_utf8_bytes")) is not int
        or not 0 < len(body) <= MAX_EXTRACTED_TEXT_BYTES
        or binding["text_utf8_bytes"] != len(body)
        or type(binding.get("text_characters")) is not int
        or binding["text_characters"] != len(content)
        or not path.is_absolute() or path.name != f"{digest}.txt" or path.parent.name != digest[:2]
    ):
        return None
    return binding


def _document_representation_view(
    rows: list[dict[str, Any]], target: tuple[str, str, str] | None,
) -> tuple[list[dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    candidates: dict[tuple[str, str], list[tuple[int, int, dict, dict | None]]] = {}
    for row_index, row in enumerate(rows):
        if target is None or _symbol_identity(row.get("symbol")) != target:
            continue
        for ref_index, ref in enumerate(row.get("source_refs") or []):
            # Reuse exactly the existing stock/archive scope requirements.
            records = _archived_document_records({**row, "source_refs": [ref]})
            if not records or records[0]["url"] != ref.get("url"):
                continue
            key = (ref["content_hash"], ref["url"])
            candidates.setdefault(key, []).append((
                row_index, ref_index, ref, _verified_materialized_text_binding(row, ref),
            ))
    projected = list(rows)
    representations = {}
    for key, entries in candidates.items():
        bound = [entry for entry in entries if entry[3] is not None]
        legacy = [entry for entry in entries if entry[2].get("text_extraction") is None]
        identities = {json.dumps(
            {name: value for name, value in entry[3].items() if name != "text_content_ref"},
            sort_keys=True, ensure_ascii=False,
        ) for entry in bound}
        # Do not rank conflicting extractions or hide an unverified version.
        # URL equality is intentionally stricter than source-hash equality;
        # distinct frozen URL relationships remain independently readable.
        if not legacy or len(identities) != 1 or len(bound) + len(legacy) != len(entries):
            continue
        selected = bound[0]
        selected_id = selected[2].get("id")
        lineage = []
        for row_index, ref_index, ref, binding in entries:
            owner = rows[row_index]
            lineage.append({
                "evidence_id": ref.get("id"), "snapshot_id": owner.get("snapshot_id"),
                "dimension": owner.get("dimension"), "symbol": owner.get("symbol"),
                **{name: ref.get(name) for name in (
                    "url", "title", "provider", "content_hash", "content_ref", "grade",
                    "published_at", "observed_at", "evidence_mode", "proof_basis",
                )},
                "text_sha256": sha256(ref["content"].encode("utf-8")).hexdigest(),
                "text_characters": len(ref["content"]),
                "representation": "explicit_bound_full_text" if binding else "legacy_inline",
            })
            if (row_index, ref_index) == selected[:2]:
                continue
            if projected[row_index] is rows[row_index]:
                projected[row_index] = {**owner, "source_refs": list(owner["source_refs"])}
            # Retain the fixed ref and its source relationship, not a second
            # copy of the original's body. No fact or original is overwritten.
            projected_ref = {name: value for name, value in ref.items() if name != "content"}
            projected_ref["content_representation"] = {
                "status": "body_in_shared_bound_full_text_not_missing",
                "selected_evidence_id": selected_id,
                "source_content_hash": key[0],
                "selected_text_sha256": selected[3]["text_content_hash"],
                "original_text_sha256": sha256(ref["content"].encode("utf-8")).hexdigest(),
                "original_text_characters": len(ref["content"]),
            }
            projected[row_index]["source_refs"][ref_index] = projected_ref
        representations[key] = {
            "text_extraction": deepcopy(selected[3]),
            "representation_view": {
                "status": "bound_full_text_replaces_legacy_body_in_prompt_only",
                "fixed_inputs_unchanged": True, "facts_are_not_additional_evidence": True,
                "selected_evidence_id": selected_id, "source_bindings": lineage,
            },
        }
    return projected, representations


def _archived_document_records(
    row: dict[str, Any], *, preserve_full_text: bool = False,
) -> list[dict[str, Any]]:
    records = []
    for ref in row.get("source_refs") or []:
        if (
            isinstance(ref, dict)
            and ref.get("type") == "market_data_evidence"
            and ref.get("source_kind") == "research_document"
            and ref.get("content") and ref.get("content_hash") and ref.get("content_ref")
            and ref.get("evidence_mode") in {"contemporaneous_archive", "public_archive_reconstruction"}
            and (not ref.get("symbol") or _symbol_identity(ref["symbol"]) == _symbol_identity(row.get("symbol")))
        ):
            # Archive proof binds this document, not arbitrary related links
            # nested in its metadata. Each shared document needs its own ref.
            extracted = _extract_evidence_records(ref, recursive=False)
            metadata = {
                "evidence_id": ref.get("id"),
                **{key: ref.get(key) for key in ("provider", "published_at", "observed_at", "grade")},
            }
            for record in extracted:
                record.update(metadata)
                record["archive_bindings"] = [{
                    **metadata, "snapshot_id": row.get("snapshot_id"),
                    "dimension": row.get("dimension") or row.get("dimension_id"),
                    "symbol": row.get("symbol"), "url": ref.get("url"),
                    "content_hash": ref.get("content_hash"), "content_ref": ref.get("content_ref"),
                    "evidence_mode": ref.get("evidence_mode"),
                    "text_sha256": sha256(str(ref["content"]).encode()).hexdigest(),
                }]
            if preserve_full_text:
                for record in extracted:
                    # Share one complete frozen text copy across all groups.
                    # Dimension-local ordinary snippets retain their old bound.
                    record["finding"] = str(ref["content"])
                    record["content_scope"] = "complete_frozen_materialized_text_not_original_document"
                    record["full_document_reading_claimed"] = False
            records.extend(extracted)
    return records


def _dimension_context(
    rows: list[dict[str, Any]], dimension: str, *, stock_documents: list[dict[str, Any]] | None = None,
    include_prompt_snapshots: bool = True,
) -> dict[str, Any]:
    snapshot_types = set(_DIMENSION_TYPES[dimension])
    selected = [
        row for row in rows
        if isinstance(row, dict) and str(row.get("snapshot_type") or "") in snapshot_types
    ]
    evidence_records: list[dict[str, str]] = []
    snapshot_origins = []
    source_discovery = []
    for row in selected:
        native = (row.get("facts") or {}).get("_uzi_raw_result") or {}
        snapshot_origins.append({
            "source_kind": "structured_snapshot",
            "snapshot_id": row.get("snapshot_id"), "content_hash": row.get("content_hash"),
            "dimension": row.get("dimension") or row.get("dimension_id"),
            "data_path": "facts._uzi_raw_result.data" if "data" in native else "facts",
            "snapshot_date": row.get("snapshot_date"),
            "source": native.get("source") or row.get("source"),
            "error": native.get("error"), "_timeout": native.get("_timeout"),
        })
        # A read-only classification view also covers already frozen legacy
        # snapshots. Their raw fields, original hashes and manifests stay put.
        source_discovery.extend(_discovery_records(row))
        # A URL recovered after the cutoff does not prove what its page said
        # then. Historical materialization supplies verified, frozen excerpts.
        # Do not infer historical mode from today's date: the same manifest
        # must keep the same evidence semantics when replayed tomorrow.
        historical = (row.get("query_manifest") or {}).get("pit_mode") == "historical_reconstruction"
        if historical:
            evidence_records.extend(_archived_document_records(row))
        else:
            evidence_records.extend(_extract_evidence_records(row))
    deduped_records = list({(row["url"], row["finding"], row["title"]): row for row in evidence_records}.values())
    # Global reuse applies only to this stock's fixed archived documents, not
    # to every URL/snippet in a neighboring dimension or the same hostname.
    allowed_urls = list(dict.fromkeys(row["url"] for row in [*deduped_records, *(stock_documents or [])]))
    from domain_packs.finance_ashare.subagents.uzi_input_views import (
        PROMPT_EVIDENCE_VIEW_VERSION,
    )
    view_truncations: list[dict[str, Any]] = []
    compact_rows = [
        {
            "snapshot_type": row.get("snapshot_type"),
            "symbol": row.get("symbol"),
            "snapshot_date": row.get("snapshot_date"),
            "data_period": row.get("data_period"),
            "quality_status": row.get("quality_status"),
            "facts": _compact(row.get("facts"), path=f"snapshots[{index}].facts", truncations=view_truncations),
            "source_refs": _compact(row.get("source_refs"), path=f"snapshots[{index}].source_refs", truncations=view_truncations),
            **({"materialized_transport_view": row["materialized_transport_view"]}
               if "materialized_transport_view" in row else {}),
        }
        for index, row in enumerate(selected if include_prompt_snapshots else [])
    ]
    return {
        "snapshot_types": sorted(snapshot_types),
        "allowed_urls": allowed_urls,
        "evidence_records": deduped_records,
        "snapshot_origins": snapshot_origins,
        "source_discovery": source_discovery,
        "document_publications": {
            url: list(dict.fromkeys(str(binding["published_at"]) for record in (stock_documents or [])
                     if record["url"] == url for binding in record.get("archive_bindings", [])
                     if binding.get("published_at")))
            for url in {record["url"] for record in (stock_documents or [])}
        },
        "snapshots": compact_rows,
        "prompt_view": {
            "version": PROMPT_EVIDENCE_VIEW_VERSION, "truncations": view_truncations,
            "omission_semantics": "__uzi_prompt_omitted__ means outside this view, not missing source data; never cite it as a fact",
            "evidence_records_are_excerpts": True, "maximum_finding_characters": 2000,
            "full_document_reading_claimed": False,
        },
    }


def _extract_evidence_records(value: Any, *, recursive: bool = True) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    if isinstance(value, dict):
        url = str(value.get("url") or value.get("pdf") or value.get("link") or "").strip().rstrip(".,;，。；)")
        if url.startswith(("http://", "https://")) and not _is_discovery_url(url):
            title = str(value.get("title") or value.get("text") or "").strip()
            finding = str(
                value.get("finding")
                or value.get("content")
                or value.get("body")
                or value.get("text_excerpt")
                or value.get("snippet")
                or ""
            ).strip()
            if finding or title:
                records.append({
                    "url": url,
                    "source": str(value.get("source_id") or value.get("source") or "artifactboard").strip(),
                    "title": title[:300],
                    "finding": finding[:2_000],
                    "content_hash": str(value.get("content_hash") or ""),
                    "content_ref": str(value.get("content_ref") or ""),
                    "evidence_mode": str(value.get("evidence_mode") or ""),
                })
        if recursive:
            for item in value.values():
                records.extend(_extract_evidence_records(item))
    elif isinstance(value, list):
        for item in value:
            records.extend(_extract_evidence_records(item))
    return records


def _is_discovery_url(url: str) -> bool:
    from research_system.market_data.research_archive import ArchiveEvidenceError, _check_document_url
    try:
        _check_document_url(url)
    except ArchiveEvidenceError:
        return True
    return False


def _discovery_records(value: Any) -> list[dict[str, str]]:
    records = []
    if isinstance(value, dict):
        url = str(value.get("url") or value.get("link") or "")
        if url.startswith(("http://", "https://")) and _is_discovery_url(url):
            records.append({"url": url, "source_kind": "discovery_entry",
                            "reason": "navigation_not_research_document"})
        for child in value.values():
            records.extend(_discovery_records(child))
    elif isinstance(value, list):
        for child in value:
            records.extend(_discovery_records(child))
    return list({record["url"]: record for record in records}.values())


def _compact(value: Any, *, depth: int = 0, path: str = "",
             truncations: list[dict[str, Any]] | None = None) -> Any:
    from domain_packs.finance_ashare.subagents.uzi_input_views import (
        compact_evidence_view,
    )

    return compact_evidence_view(value, max_sequence_items=20, max_text_chars=800,
                                 path=path, truncations=truncations)


def _group_citations_complete(group: QualitativeGroupResult) -> bool:
    return all(sum(e.url is not None for e in dim.evidence) >= 2 for dim in group.dimensions)


def _validate_group(
    group: QualitativeGroupResult,
    expected_dimensions: tuple[str, str],
    evidence_bundle: dict[str, dict[str, Any]],
) -> None:
    actual = [row.dimension_id for row in group.dimensions]
    if len(actual) != len(set(actual)) or set(actual) != set(expected_dimensions):
        raise UziQualitativeValidationError(
            field="dimensions", code="qualitative_dimension_membership_mismatch",
        )
    for dimension_index, dimension in enumerate(group.dimensions):
        allowed = set(evidence_bundle[dimension.dimension_id]["allowed_urls"])
        findings = [" ".join(evidence.finding.split()).casefold() for evidence in dimension.evidence]
        if len(findings) != len(set(findings)):
            raise UziQualitativeValidationError(
                field=f"dimensions.{dimension_index}.evidence", code="qualitative_repeated_finding",
            )
        cited = [evidence.url for evidence in dimension.evidence if evidence.url is not None]
        ungrounded = sorted(set(cited) - allowed)
        if ungrounded:
            raise UziQualitativeValidationError(
                field=f"dimensions.{dimension_index}.evidence", code="qualitative_ungrounded_url",
            )
        publications = evidence_bundle[dimension.dimension_id].get("document_publications", {})
        for evidence in dimension.evidence:
            if evidence.url in publications and document_attribution_conflicts(
                evidence.finding, publications[evidence.url],
            ):
                raise UziQualitativeValidationError(
                    field=f"dimensions.{dimension_index}.evidence", code="qualitative_source_attribution_conflict",
                )


def _input_evidence_gaps(evidence_bundle: dict[str, dict[str, Any]]) -> list[str]:
    return [f"{dimension}:frozen_citations_unavailable"
            for dimension, context in evidence_bundle.items() if not context["allowed_urls"]]


def _with_frozen_input_failures(
    failures: list[dict[str, Any]], evidence_bundle: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Diagnose failed execution after the Agent call; never relax URL checks."""
    records = [dict(row) for row in failures]
    for label, dimensions in _GROUPS:
        stage = f"qualitative_{label}"
        if any(not evidence_bundle[dimension]["allowed_urls"] for dimension in dimensions) and not any(
            row.get("stage") == stage and row.get("kind") == "source_gap" for row in records
        ):
            records.append({"stage": stage, "kind": "source_gap", "error_type": "FrozenEvidenceUnavailable"})
    return records


def _qualitative_gap(
    result: dict[str, Any],
    *,
    status: str,
    errors: list[str],
    evidence_bundle: dict[str, dict[str, Any]],
    completed_group_count: int = 0,
    partial_dimensions: dict[str, Any] | None = None,
    failure_records: list[dict[str, Any]] | None = None,
    read_audits: dict | None = None,
    binding_audits: dict | None = None,
    pending_evidence: list | None = None,
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
        "evidence_count": sum(len(row.get("evidence") or []) for row in (partial_dimensions or {}).values()),
        "input_source_count": sum(len(context["allowed_urls"]) for context in evidence_bundle.values()),
        "association_count": sum(len(row.get("associations") or []) for row in (partial_dimensions or {}).values()),
        "input_gaps": _input_evidence_gaps(evidence_bundle),
        "errors": errors,
        "failure_records": list(failure_records or []),
        "frozen_read_audits": read_audits or {},
        "citation_binding_audits": binding_audits or {},
        "pending_evidence": pending_evidence or [],
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
