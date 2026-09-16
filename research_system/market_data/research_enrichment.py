"""Explicit, evidence-bound raw fact revisions; never a report-side fetcher.

This is intentionally not a JSON patch engine. Only named scalar facts in the
native six research dimensions are supported. Original DimResult envelopes,
collection failures and old snapshots remain intact. Compound restructurings,
unverifiable inferences and score/rating changes return refusal reasons.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from threading import Event
from time import monotonic
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)
from sqlalchemy import select

from domain_packs.finance_ashare.subagents.uzi_input_views import UziInputBudgetExceeded
from domain_packs.finance_ashare.subagents.uzi_llm_support import (
    UziQualitativeValidationError,
    model_failure_record,
    parse_uzi_structured,
)
from domain_packs.finance_ashare.subagents.uzi_qualitative_deep_dive import (
    _GROUPS,
    QUALITATIVE_SOURCE_ATTRIBUTION_RULES,
    QualitativeGroupResult,
    _check_qualitative_prompt_budget,
    _group_user_prompt,
    _validate_group,
)
from research_system.market_data.bundles import native_transport_facts
from research_system.market_data.company_profile_dependencies import (
    check_company_profile_dependencies,
)
from research_system.market_data.company_profile_revision import (
    CompanyProfileProposal,
    CompanyProfileRefusal,
    build_company_profile_scope,
    persist_company_profile_revisions,
)
from research_system.market_data.contracts import (
    EvidenceInput,
    SeriesPointInput,
    SnapshotIngestInput,
    canonical_hash,
    canonical_json,
    cutoff_at_for_date,
)
from research_system.market_data.historical import capability_for_dimension
from research_system.market_data.ingestion import SnapshotIngestionService
from research_system.market_data.models import (
    MarketDataCollectionJob,
    MarketDataCollectionTask,
)
from research_system.market_data.native_projection import project_native_result
from research_system.market_data.repository import MarketDataRepository
from research_system.market_data.research_archive import research_publication_projection
from research_system.market_data.research_archive_text import (
    ResearchTextError,
    snapshot_text_bindings,
    verified_text_projection,
)
from research_system.market_data.research_period_context import (
    PERIOD_BINDING_VERSION,
    report_period_citation,
)
from research_system.market_data.research_prompt_views import (
    RESEARCH_PROMPT_TRANSPORT_VERSION,
    background_view,
    group_document_view,
    reference_matching_raw,
)
from research_system.market_data.research_suggestions import (
    CITATION_LOCATOR_VERSION,
    PROFILE_SUGGESTIONS_VERSION,
    SUGGESTIONS_VERSION,
    FactSuggestion,
    ProfileSuggestion,
    literal_text_present,
    resolve_fact_suggestions,
    resolve_profile_suggestions,
)
from research_system.market_data.resolver import (
    SnapshotResolver,
    _database_utc,
    _snapshot_pit_visibility_errors,
)

ENRICHMENT_VERSION = "uzi-research-facts-v2"
DOCUMENT_VIEW_VERSION = "uzi-research-document-view-v1"
RESEARCH_CITATION_VIEW_VERSION = "uzi-research-supplied-document-citations-v1"
PROFILE_PROPOSAL_VIEW_VERSION = "uzi-company-profile-proposal-view-v1"
RESEARCH_TASK_SCOPE_VERSION = "uzi-data-center-research-task-scope-v1"
FACT_FIELDS = {
    "3_macro": {"industry", "rate_cycle", "fx_trend", "commodity", "geo_risk", "industry_macro_impact"},
    "13_policy": {"industry", "policy_dir", "subsidy", "anti_trust", "monitoring"},
    "7_industry": {"industry", "name", "growth", "market_size", "cycle", "concentration"},
    "15_events": {"catalyst", "recent_news", "recent_notices"},
    "8_materials": {"core_material", "cost_share", "import_dep", "industry_resolved", "supply_security"},
    "9_futures": {"linked_contract", "hedging_direction", "hedging_disclosure_status", "hedging_notional"},
}


class OriginalCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_id: str
    document_hash: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=5)


class FactProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_id: str
    snapshot_hash: str
    dimension: str
    field: str
    old_exists: bool
    old_value: StrictStr | StrictInt | StrictFloat | StrictBool | None
    new_value: StrictStr | StrictInt | StrictFloat | StrictBool | None
    unit: str = Field(min_length=1)
    period_start: date
    period_end: date
    period_text: str = Field(min_length=1)
    finding: str = Field(min_length=5, max_length=1200)
    citations: list[OriginalCitation] = Field(min_length=1, max_length=8)


class ResearchProposalGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    research: QualitativeGroupResult
    proposals: list[FactProposal] = Field(default_factory=list, max_length=24)
    unresolved: list[str] = Field(default_factory=list)


class CompanyResearchProposalGroup(ResearchProposalGroup):
    """Only explicit basic-scope jobs receive this additional write protocol."""

    company_profile_proposals: list[CompanyProfileProposal] = Field(default_factory=list, max_length=3)


class ResearchSuggestionGroup(BaseModel):
    """Model wire only; persisted full proposals keep their existing contract."""

    model_config = ConfigDict(extra="forbid")
    research: QualitativeGroupResult
    proposals: list[FactSuggestion] = Field(default_factory=list, max_length=24)
    unresolved: list[str] = Field(default_factory=list)


class ProfileAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: Literal["name", "industry", "main_business"]
    status: Literal["proposed", "unchanged", "unresolved"]
    reason: str = Field(min_length=1, max_length=1200)

    @model_validator(mode="after")
    def require_reason(self):
        if not self.reason.strip():
            raise ValueError("profile assessment requires a reason")
        return self


class CompanyResearchSuggestionGroup(ResearchSuggestionGroup):
    company_profile_proposals: list[ProfileSuggestion] = Field(default_factory=list, max_length=3)
    company_profile_assessments: list[ProfileAssessment] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def account_for_profile_fields(self):
        fields = {item.field for item in self.company_profile_assessments}
        if fields != {"name", "industry", "main_business"}:
            raise ValueError("profile assessment requires each field exactly once")
        proposed = {item.field for item in self.company_profile_proposals}
        assessed = {item.field for item in self.company_profile_assessments if item.status == "proposed"}
        if assessed != proposed:
            raise ValueError("profile assessments must match proposed fields")
        return self


class FactRefusal(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class _LocalCancellation:
    """Add writable lease-loss cancellation to a read-only worker signal."""

    def __init__(self, parent):
        self.parent = parent
        self.local = Event()

    def is_set(self):
        return self.local.is_set() or (self.parent is not None and self.parent.is_set())

    def set(self):
        self.local.set()

    def wait(self, timeout=None):
        deadline = None if timeout is None else monotonic() + timeout
        while not self.is_set():
            remaining = None if deadline is None else deadline - monotonic()
            if remaining is not None and remaining <= 0:
                return False
            self.local.wait(0.05 if remaining is None else min(0.05, remaining))
        return True


def _stop_reason(deadline_at, cancellation) -> str | None:
    if cancellation is not None and cancellation.is_set():
        return "cancelled"
    if deadline_at is not None and monotonic() >= deadline_at:
        return "deadline_exceeded"
    return None


def _fact_period(text: str) -> tuple[date, date] | None:
    """Only normalize explicit periods; do not infer an undocumented period."""
    match = re.fullmatch(r"(\d{4})年(上半年|半年度|下半年|度|全年)?", text)
    if match:
        year, suffix = int(match[1]), match[2]
        return (date(year, 7 if suffix == "下半年" else 1, 1),
                date(year, 6, 30) if suffix in {"上半年", "半年度"} else date(year, 12, 31))
    match = re.fullmatch(r"(\d{4})年([一二三四1-4])季度", text)
    if match:
        year = int(match[1])
        quarter = "一二三四".index(match[2]) + 1 if match[2] in "一二三四" else int(match[2])
        start = date(year, quarter * 3 - 2, 1)
        end = date(year + 1, 1, 1) if quarter == 4 else date(year, quarter * 3 + 1, 1)
        return start, end - timedelta(days=1)
    match = re.fullmatch(r"(\d{4})[-年](\d{1,2})[-月](\d{1,2})日?", text)
    if match:
        value = date(*(int(item) for item in match.groups()))
        return value, value
    return None


def _document_error(evidence, cutoff) -> str | None:
    if (
        evidence.source_kind != "research_document" or not evidence.inline_content
        or not evidence.content_ref or not evidence.content_hash
        or not evidence.source_url or evidence.grade not in {"A", "B"}
    ):
        return "original_document_required"
    if _database_utc(evidence.published_at) > cutoff:
        return "evidence_after_cutoff"
    if _database_utc(evidence.observed_at) > cutoff and not (
        evidence.grade == "B" and evidence.immutable_proof
        and (evidence.metadata_json or {}).get("evidence_mode")
        in {"public_archive_reconstruction", "contemporaneous_archive"}
    ):
        return "historical_version_unproven"
    path = Path(evidence.content_ref)
    if not path.is_file() or sha256(path.read_bytes()).hexdigest() != evidence.content_hash:
        return "document_content_corrupt"
    return None


def _document_view(evidence, bindings):
    """The provider and proposal validator must use identical character offsets.

    Keep the historical `inline_content` protocol key, but explicitly mark the
    new full-text view. Never expand persisted inline text or old snapshots.
    """
    expected = bindings.get(evidence.evidence_key)
    if expected is None:
        return {"inline_content": evidence.inline_content}
    projection = verified_text_projection(evidence, expected)
    return {"inline_content": projection["content"],
            "text_extraction": projection["text_extraction"],
            "document_view_version": DOCUMENT_VIEW_VERSION}


def _research_documents(snapshot, evidence, cutoff):
    bindings = snapshot_text_bindings(snapshot, evidence)
    return [{"evidence_id": row.id, "document_hash": row.content_hash,
             "url": row.source_url, "published_at": _database_utc(row.published_at).isoformat(),
             "observed_at": _database_utc(row.observed_at).isoformat(),
             "retrieved_at": _database_utc(row.observed_at).date().isoformat(),
             **_document_view(row, bindings), **research_publication_projection(row)}
            for row in evidence if _document_error(row, cutoff) is None]


def _validate_fact(repository, snapshot, proposal: FactProposal):
    if snapshot.dimension != proposal.dimension or proposal.field not in FACT_FIELDS.get(proposal.dimension, ()):
        raise FactRefusal("field_not_supported")
    if snapshot.content_hash != proposal.snapshot_hash:
        raise FactRefusal("input_hash_mismatch")
    if snapshot.revoked_at is not None or snapshot.status == "revoked":
        raise FactRefusal("input_revoked")
    cutoff = cutoff_at_for_date(snapshot.as_of_date, snapshot.exchange_timezone)
    if _snapshot_pit_visibility_errors(repository, snapshot, cutoff):
        raise FactRefusal("input_pit_invalid")
    raw = dict(snapshot.payload_json.get("raw") or {})
    if proposal.old_exists != (proposal.field in raw) or canonical_json(raw.get(proposal.field)) != canonical_json(proposal.old_value):
        raise FactRefusal("old_value_mismatch")
    if canonical_json(proposal.old_value) == canonical_json(proposal.new_value) and proposal.old_exists:
        raise FactRefusal("no_change")
    value = proposal.new_value
    if isinstance(value, bool) or not isinstance(value, (str, int, float)) or (
        isinstance(value, str) and not value.strip()
    ) or (isinstance(value, (int, float)) and not math.isfinite(value)):
        raise FactRefusal("value_type_not_supported")
    if proposal.period_end > snapshot.as_of_date:
        raise FactRefusal("period_after_cutoff")
    if proposal.period_start > proposal.period_end:
        raise FactRefusal("period_invalid")
    evidence = {row.id: row for row in repository.list_snapshot_evidence(snapshot.id)}
    try:
        bindings = snapshot_text_bindings(snapshot, evidence.values())
    except ResearchTextError as exc:
        raise FactRefusal(exc.code) from None
    quotes, documents = [], {}
    for citation in proposal.citations:
        row = evidence.get(citation.evidence_id)
        if row is None:
            raise FactRefusal("evidence_not_bound")
        if row.content_hash != citation.document_hash:
            raise FactRefusal("document_hash_mismatch")
        if reason := _document_error(row, cutoff):
            raise FactRefusal(reason)
        try:
            content = _document_view(row, bindings)["inline_content"]
        except ResearchTextError as exc:
            raise FactRefusal(exc.code) from None
        if content[citation.start:citation.end] != citation.quote:
            raise FactRefusal("excerpt_mismatch")
        documents[row.id] = {"evidence_id": row.id, "document_hash": row.content_hash,
                             "inline_content": content}
        quotes.append(citation.quote)
    if not any(proposal.period_text in quote for quote in quotes):
        citations = [ref.model_dump() for ref in proposal.citations]
        binding = report_period_citation(
            dimension=proposal.dimension, field=proposal.field, value=value, unit=proposal.unit,
            period=(proposal.period_start, proposal.period_end), symbol=snapshot.symbol,
            documents=list(documents.values()), citations=citations)
        if binding is None or binding not in citations:
            raise FactRefusal("period_not_grounded")
    if _fact_period(proposal.period_text) != (proposal.period_start, proposal.period_end):
        raise FactRefusal("period_not_supported_or_mismatch")
    if proposal.unit not in {"文本", "text"} and not any(proposal.unit in quote for quote in quotes):
        raise FactRefusal("unit_not_grounded")
    if isinstance(value, str):
        grounded = any(literal_text_present(value, quote) for quote in quotes)
    else:
        grounded = any(
            float(token.replace(",", "")) == value
            for quote in quotes
            for token in re.findall(r"(?<![\d.])[-+]?\d[\d,]*(?:\.\d+)?(?![\d.])", quote)
        )
    if not grounded:
        raise FactRefusal("value_not_grounded")


def _evidence_inputs(repository, snapshot_id):
    return [EvidenceInput(
        provider=row.provider, source_url=row.source_url, source_kind=row.source_kind,
        title=row.title, mime_type=row.mime_type, content_hash=row.content_hash,
        content_ref=row.content_ref, inline_content=row.inline_content,
        published_at=_database_utc(row.published_at), observed_at=_database_utc(row.observed_at),
        grade=row.grade, immutable_proof=row.immutable_proof, is_manual=row.is_manual,
        metadata=deepcopy(row.metadata_json or {}),
    ) for row in repository.list_snapshot_evidence(snapshot_id)]


def persist_fact_revisions(repository, *, proposals, registry, job_id: str, now=None,
                           allowed_snapshot_ids=None, deadline_at=None, cancellation=None,
                           write_fence=None):
    """Validate all proposals against immutable inputs; revise each scope once."""
    now = now or datetime.now(timezone.utc)
    groups, rejected, revisions = defaultdict(list), [], []
    # Fence in the SAME transaction before loading ORM objects. In particular,
    # don't let SQLite's naive loaded timestamps participate in lease SQL's
    # synchronize-evaluate comparison with the owner's aware UTC cutoff.
    if proposals and write_fence is not None and not write_fence(repository.session):
        if cancellation is not None:
            cancellation.set()
        return {"version": ENRICHMENT_VERSION, "revisions": [],
                "rejected": [{"reason": "worker_lease_lost"}]}
    for item in proposals:
        if reason := _stop_reason(deadline_at, cancellation):
            rejected.append({"reason": reason})
            break
        try:
            proposal = FactProposal.model_validate(item)
            if allowed_snapshot_ids is not None and proposal.snapshot_id not in allowed_snapshot_ids:
                raise FactRefusal("snapshot_outside_requested_scope")
            snapshot = repository.require_snapshot(proposal.snapshot_id)
            _validate_fact(repository, snapshot, proposal)
            if any(row.field == proposal.field for row in groups[snapshot.id]):
                raise FactRefusal("duplicate_field_proposal")
            groups[snapshot.id].append(proposal)
        except Exception as exc:  # noqa: BLE001 - reject one proposal without authorizing unrelated edits
            rejected.append({
                "snapshot_id": item.get("snapshot_id") if isinstance(item, dict) else None,
                "field": item.get("field") if isinstance(item, dict) else None,
                "reason": exc.reason if isinstance(exc, FactRefusal) else "proposal_invalid",
                "error_type": type(exc).__name__,
            })
    for snapshot_id, facts in groups.items():
        if reason := _stop_reason(deadline_at, cancellation):
            rejected.append({"snapshot_id": snapshot_id, "reason": reason})
            break
        original = repository.require_snapshot(snapshot_id)
        payload = deepcopy(original.payload_json)
        raw = payload.setdefault("raw", {})
        for fact in facts:
            raw[fact.field] = fact.new_value
        manifest = deepcopy(original.query_manifest_json or {})
        manifest["research_enrichment"] = {
            "version": ENRICHMENT_VERSION, "input_snapshot_id": original.id,
            "period_binding_version": PERIOD_BINDING_VERSION,
            "input_snapshot_hash": original.content_hash,
            "facts": [row.model_dump(mode="json") for row in facts],
            "proposal_hash": canonical_hash([row.model_dump(mode="json") for row in facts]),
            "collector_envelope_preserved": True, "report_delivery_assessed": False,
        }
        if (manifest.get("research_archive") or {}).get("text_extractions"):
            manifest["research_enrichment"]["document_view_version"] = DOCUMENT_VIEW_VERSION
        if now > cutoff_at_for_date(original.as_of_date, original.exchange_timezone):
            manifest["pit_mode"] = "historical_reconstruction"
        fields = deepcopy(original.field_sources_json or {})
        for fact in facts:
            fields[f"raw.{fact.field}"] = {"evidence_ids": [ref.evidence_id for ref in fact.citations], "origin": ENRICHMENT_VERSION}
        evidence = _evidence_inputs(repository, snapshot_id)
        ingest = SnapshotIngestInput(
            symbol=original.symbol, market=original.market, exchange_timezone=original.exchange_timezone,
            dimension=original.dimension, contract_version=original.contract_version,
            as_of_date=original.as_of_date, status=original.status, payload=payload,
            effective_at=_database_utc(original.effective_at),
            published_at=max([_database_utc(original.published_at), *(row.published_at for row in evidence)]),
            observed_at=now, ingested_at=now, source_version=original.source_version,
            source_priority=original.source_priority, origin="derived", query_manifest=manifest,
            validation=deepcopy(original.validation_json), field_sources=fields, evidence=evidence,
            collection_task_id=original.collection_task_id,
            provider_attempt_id=f"{ENRICHMENT_VERSION}:{job_id}:{manifest['research_enrichment']['proposal_hash']}",
            series_points=[SeriesPointInput(
                series_key=row.series_key, point_at=_database_utc(row.point_at),
                effective_at=_database_utc(row.effective_at), published_at=_database_utc(row.published_at),
                observed_at=_database_utc(row.observed_at), value=row.value_json,
                source_version=row.source_version, metadata=row.metadata_json,
            ) for row in repository.list_snapshot_series_points(snapshot_id)],
        )
        # Roll back this revision if the hard PIT boundary rejects it. Normal
        # contract/collector failures are retained, not silently promoted.
        try:
            with repository.session.begin_nested():
                if reason := _stop_reason(deadline_at, cancellation):
                    raise FactRefusal(reason)
                revision = SnapshotIngestionService(repository, registry=registry).ingest(ingest)
                cutoff = cutoff_at_for_date(revision.as_of_date, revision.exchange_timezone)
                if _snapshot_pit_visibility_errors(repository, revision, cutoff):
                    raise FactRefusal("revision_pit_invalid")
                projection = project_native_result(revision)
                if any(projection.get("data", {}).get(row.field) != row.new_value for row in facts):
                    raise FactRefusal("native_projection_mismatch")
                revisions.append({
                    "input_snapshot_id": snapshot_id, "snapshot_id": revision.id,
                    "content_hash": revision.content_hash, "dimension": revision.dimension,
                    "native_projection": projection, "facts_count": len(facts),
                })
        except Exception as exc:  # noqa: BLE001 - savepoint rolls back one rejected revision, retaining its reason
            rejected.append({"snapshot_id": snapshot_id, "reason": getattr(exc, "reason", "revision_validation_failed"), "error_type": type(exc).__name__})
    return {"version": ENRICHMENT_VERSION, "revisions": revisions, "rejected": rejected}


def _company_profile_prompt_scope(scope, context):
    """Reference an already-present exact body instead of duplicating a PDF.

    The full immutable scope remains the validation authority. This is solely
    the model's bounded view of field permissions and citation identities.
    """
    view = deepcopy(scope)
    # The stored scope is immutable, not a ban on proposing a new version.
    # This view is only sent when the explicit task includes 0_basic; neither
    # this flag nor a model answer grants authority to the persistence layer.
    view.pop("read_only", None)
    view["input_immutable"] = True
    view["proposal_permission"] = {
        "version": PROFILE_PROPOSAL_VIEW_VERSION,
        "allowed": True,
        "target_dimension": "0_basic",
        "target_snapshot_id": scope["target"]["snapshot_id"],
        "fields": list(scope["allowed_fields"]),
        "output_field": "company_profile_proposals",
        "persistence": "validated_new_snapshot_only",
    }
    view["target"]["raw"] = {key: value for key, value in view["target"]["raw"].items()
                             if key in scope["allowed_fields"]}
    for document in view["documents"]:
        # The profile scope records the real archive observation. Supply the
        # same date semantics as ordinary research documents, never as-of or
        # publication time. The immutable validation scope remains unchanged.
        document["retrieved_at"] = (datetime.fromisoformat(document["observed_at"]).date().isoformat()
                                    if document.get("observed_at") else None)
        for dimension, row in context.items():
            match = next((item for item in row.get("documents", [])
                          if item["evidence_id"] == document["evidence_id"]
                          and item["document_hash"] == document["document_hash"]
                          and item["inline_content"] == document["inline_content"]), None)
            if match is not None:
                document.pop("inline_content")
                document["text_reference"] = {"dimension": dimension, "evidence_id": match["evidence_id"]}
                break
    return view


def _research_citation_view(context, profile_scope):
    """Share only verified originals actually supplied to this group.

    The caller has already fixed same-stock/PIT documents through
    _research_documents/build_company_profile_scope. This view neither reads
    more snapshots nor discovers URLs in raw facts, snippets or background.
    Keep the original context/scope for suggestion compilation and writes.
    """
    records = [
        {"scope": dimension, "snapshot_id": row.get("snapshot_id"),
         "snapshot_hash": row.get("snapshot_hash"), **document}
        for dimension, row in context.items() for document in row.get("documents", [])
    ]
    records.extend({"scope": "company_profile", **document}
                   for document in (profile_scope or {}).get("documents", []))
    urls = list(dict.fromkeys(row["url"] for row in records))
    publications = {
        url: list(dict.fromkeys(str(row["published_at"]) for row in records
                               if row["url"] == url and row.get("published_at")))
        for url in urls
    }
    view = {dimension: {**row, "allowed_urls": list(urls),
                        "document_publications": publications}
            for dimension, row in context.items()}
    # A small non-content receipt supports later comparison without retaining
    # model answers, raw URLs, body text or arbitrary model field values.
    identities = [{key: row.get(key) for key in (
        "scope", "snapshot_id", "snapshot_hash", "source_snapshot_id", "source_snapshot_hash",
        "evidence_id", "document_hash", "text_content_hash", "url",
        "published_at", "observed_at", "retrieved_at",
    )} for row in records]
    receipt = {"version": RESEARCH_CITATION_VIEW_VERSION,
               "document_binding_count": len(records), "url_count": len(urls),
               "input_hash": canonical_hash(identities)}
    return view, receipt


def _unbound_citation_diagnostics(parsed, context):
    """Retain typed dimension identity and URL digests, never response text."""
    return [{
        "dimension_id": dimension.dimension_id,
        "url_sha256": sha256(url.encode()).hexdigest(),
    } for dimension in parsed.research.dimensions
        for url in sorted({item.url for item in dimension.evidence if item.url is not None}
                          - set(context[dimension.dimension_id]["allowed_urls"]))]


def _proposal_period_diagnostics(suggestions, rejected):
    """Keep rejected date syntax, not arbitrary model output or inferred dates.

    Rejections used to replace the original suggestions, making a compiler
    refusal impossible to distinguish from an unsupported period spelling.
    A tightly bounded date alphabet can be retained verbatim; everything else
    is represented only by length/hash. This does not change period parsing.
    """
    output = []
    for row in rejected:
        if row["reason"] != "period_not_supported_or_mismatch":
            continue
        index = row["suggestion_index"]
        text = suggestions[index].period_text
        literal = text if (
            len(text) <= 64
            and re.fullmatch(r"[0-9年月日上下半全度第季度一二三四至到HhQq ./~—–-]+", text)
        ) else None
        output.append({
            "version": "uzi-fact-period-diagnostics-v1",
            "suggestion_index": index,
            "stage": "period_resolution",
            "period_text_sha256": sha256(text.encode()).hexdigest(),
            "period_text_length": len(text),
            "period_literal": literal,
        })
    return output


def _research_task_scope(context, profile_scope):
    """Separate native research coverage from explicit new-version targets.

    This small header precedes the full native manual and archived bodies. The
    model must assess the actual target, not an issuer description elsewhere
    in the research context. Persistence still independently checks authority.
    """
    scope = {
        "version": RESEARCH_TASK_SCOPE_VERSION,
        "research": {"dimension_keys": list(context)},
        "proposals": [
            {"dimension": dimension, "snapshot_id": row["snapshot_id"],
             "fields": sorted(FACT_FIELDS[dimension])}
            for dimension, row in context.items() if row.get("revision_allowed", True)
            and row.get("snapshot_id")
        ],
    }
    if profile_scope:
        target = profile_scope["target"]
        scope["company_profile_proposals"] = {
            "dimension": "0_basic", "snapshot_id": target["snapshot_id"],
            "snapshot_hash": target["snapshot_hash"],
            "field_states": {
                field: {"exists": field in target["raw"], "value": deepcopy(target["raw"].get(field))}
                for field in profile_scope["allowed_fields"]
            },
        }
    return scope


def request_fact_proposals(provider, *, group_label, dimensions, inputs, deadline_at,
                           cancellation=None, profile="medium"):
    if reason := _stop_reason(deadline_at, cancellation):
        return {"status": reason, "proposals": []}
    if provider is None or not getattr(provider, "available", True):
        return {"status": "provider_unavailable", "proposals": []}
    context = {key: inputs.get(key, {"allowed_urls": [], "documents": []}) for key in dimensions}
    profile_scope = inputs.get("_company_profile_scope") if "7_industry" in dimensions else None
    schema = CompanyResearchSuggestionGroup if profile_scope else ResearchSuggestionGroup
    parsed = None
    citation_receipt = None
    try:
        research_context, citation_receipt = _research_citation_view(context, profile_scope)
        prompt_context = group_document_view(research_context)
        if inputs.get("_read_only_context"):
            prompt_context = reference_matching_raw(prompt_context, inputs["_read_only_context"])
        prompt = (
            "DATA_CENTER_TASK_SCOPE=" + canonical_json(_research_task_scope(context, profile_scope)) + "\n"
            + _group_user_prompt(group_label=group_label, dimensions=dimensions,
                                 evidence_bundle=prompt_context, workflow="data_center")
        )
        if inputs.get("_read_only_context"):
            read_only_context = background_view(inputs["_read_only_context"], enrichment_version=ENRICHMENT_VERSION)
            prompt += "\nREAD_ONLY_RESEARCH_CONTEXT=" + canonical_json(read_only_context)
        system_prompt = (
                "你在用户显式启动的数据中心任务内执行原生UZI三组研究。只读给定已归档原文，不联网。"
                "DATA_CENTER_TASK_SCOPE分别列出research.dimensions、普通proposals和公司身份company_profile_proposals的目标；"
                "原生两维分组约束仅作用于research.dimensions，不限制另行明确授权的新版本提案。"
                "research按原生问题分析；proposals仅修改以下已知标量事实字段，不写评分评级、价格目标或任意JSON路径。"
                "没有可精确支持的新事实就返回空proposals和具体unresolved；不编造期间、单位或来源。"
                f"提案协议{SUGGESTIONS_VERSION}：每项只含snapshot_id、field、new_value、period_text、unit、finding，"
                "citations只含已绑定evidence_id和inline_content中唯一精确出现的quote；短引文重复时提供足够的原文上下文。"
                "不要生成旧值、old_exists、任何hash、start/end或period_start/end；程序从本次固定输入确定它们。"
                "quote须真正支持new_value，报告标题不能代替披露事实的正文；文本事实unit使用文本，数值单位须在引文中出现。"
                f"引文定位{CITATION_LOCATOR_VERSION}只容许汉字之间的PDF换行差异；数字、标点、其他正文不可改写。"
                "research.evidence的非unknown事实必须有原样绑定的url和retrieved_at；"
                "retrieved_at原样使用documents.retrieved_at，它是原文实际归档日，不是发布日期、as_of或本次推理日期。"
                f"研究引用视图{RESEARCH_CITATION_VIEW_VERSION}：本组实际提供的documents及明确提供的COMPANY_PROFILE_SCOPE原文，"
                "允许在本组两维研究中跨维引用不同事实；allowed_urls仅表示这些已提供原文的目录，不证明具体finding有依据。"
                "原文仍在原documents或精确text_reference处，不重复正文，不把READ_ONLY背景、snippet或未提供的其他组URL当证据。"
                f"传输视图{RESEARCH_PROMPT_TRANSPORT_VERSION}：text_reference按dimension/document_index定位同一完整原文，"
                "不合并引用身份；raw_reference定位该快照在READ_ONLY_RESEARCH_CONTEXT的data。"
                "__uzi_indexed_sequence__仅为原数组索引采样，original_length为原长度，items键为原索引，"
                "未展示位置不是空值或缺失事实，不得据此推算或宣称源数据缺失。"
                "__uzi_keyed_table__保留字典行键，__uzi_row_table__保留列表顺序；每行由common_values和columns对应的行值还原；"
                "__uzi_same_value_reference__从READ_ONLY_RESEARCH_CONTEXT根按JSON指针读取同值，非引用权限共享。"
                f"{QUALITATIVE_SOURCE_ATTRIBUTION_RULES}"
                "不支持单位换算、推算值、整段数组重写；文字new_value须是原文引用子串。保留原始采集错误。"
                "READ_ONLY_RESEARCH_CONTEXT是按本次symbol/as_of_date/cutoff_at选出的公司及已有事实背景，"
                "仅供识别公司、行业和条件传导；不是新增原文证据，不扩展allowed_urls、可写维度或snapshot范围。"
                "proposals仍只能使用本组已绑定documents，其他快照只读；背景缺失不允许联网补齐或使用截止时间之后的事实。"
                "本组revision_allowed=false的维度只读复用已有原文；允许完成research，但不得为它提出proposals或重新采集。"
                "period_text仅接受引文中明确的YYYY年/年度/全年/上半年/半年度/下半年、YYYY年N季度或YYYY-MM-DD/年月日；"
                "其中YYYY年半年度指该年1月1日至6月30日；其他期间列unresolved。"
                f"期间引用补全{PERIOD_BINDING_VERSION}：公司行业、主营原料、供应链及衍生品适用状态等文本披露，"
                "如事实句未重复期间，可由同公司同一报告中唯一且日期一致的报告期定义补充精确引用；"
                "数值、比例、上年对比或预测不可套用整份报告期间，须另引明确期间及对应事实。"
                f"FACT_FIELDS={canonical_json({key: sorted(FACT_FIELDS[key]) for key in dimensions})}"
        )
        if any(document.get("document_view_version") for row in context.values()
               for document in row.get("documents", [])):
            system_prompt += (
                f"文档视图版本{DOCUMENT_VIEW_VERSION}：带text_extraction的inline_content是快照明确绑定的完整提取文本，"
                "quote须来自该字符串；程序确定Unicode字符索引及原始文档hash，模型不填偏移或hash。"
                "全文提取不代表OCR或图表已被完整识别。"
            )
        if profile_scope:
            prompt += "\nCOMPANY_PROFILE_SCOPE=" + canonical_json(_company_profile_prompt_scope(profile_scope, context))
            system_prompt += (
                "本任务还显式授权修订COMPANY_PROFILE_SCOPE.target的公司身份；"
                f"提案权限视图{PROFILE_PROPOSAL_VIEW_VERSION}：input_immutable只表示旧快照和原文不可改，"
                "不表示禁止提交新版本提案；以proposal_permission指定的目标和字段为准。"
                "company_profile_proposals仅限name/industry/main_business，不写价格、财务指标或评分。"
                "逐项核对已有非空值是否错误，不仅检查null；company_profile_assessments必须分别说明name/industry/main_business，"
                "每字段恰好一项status和reason：有提案为proposed、有据确认原值为unchanged、无充分依据为unresolved。"
                "只与目标快照的 field_states 比较：exists=false表示目标没有该字段，value=null表示目标值为空；"
                "正文或READ_ONLY背景已描述某事实，不等于该事实已写入目标。缺失/null不可因背景描述正确而判unchanged。"
                "允许无提案和unresolved，不强造修订；发现原文能纠正缺失或错误值时提交该字段提案。"
                "公司身份不强造财务期间。行业自述使用industry_basis=issuer_disclosure，不等于申万或其他正式分类；"
                "只有原文明确陈述分类体系及归属，才允许classification并提交独立classification_citations。"
                f"公司提案协议{PROFILE_SUGGESTIONS_VERSION}：每项只提交field/new_value/industry_basis/classification_system及引用；"
                "company_profile_proposals每个new_value必须是单条citation.quote中的连续原文；"
                "main_business也按事实摘录提交，不概括、改写或把多句拼接为一个新值。"
                "可选一段能独立说明主营的完整原文；更广泛的总结写在research，不回填成公司身份事实。"
                "引用必须来自该scope固定目录，只需evidence_id和唯一原文quote；evidence_id只有一个绑定时不要抄source_snapshot_id，"
                "程序确定它；仅同一evidence_id存在多个来源绑定时用原样source_snapshot_id消歧，不能编造或拼接ID。"
                "目标快照身份、旧值、hash和偏移由程序从固定scope确定。"
                "text_reference指本组documents的同一完整字符串，非缺失正文。"
                "本组7/15的revision_allowed=false不限制独立的company_profile_proposals；"
                "已授权的0_basic身份修订与本组行业指标修改是两条不同范围，不以伙伴只读为由拒绝身份提案。"
                "普通proposals仍限FACT_FIELDS，不能用它修改0_basic；新身份会要求相关数据重建，不改旧快照或行情观测时间。"
            )
        _check_qualitative_prompt_budget(system_prompt, prompt)
        parsed = parse_uzi_structured(
            provider, schema=schema, system_prompt=system_prompt,
            user_prompt=prompt, research_profile=profile,
            uzi_stage=f"research_enrichment_{group_label}", cancellation=cancellation,
            deadline_at=deadline_at, temperature=0.1,
        )
        parsed = schema.model_validate(parsed)
        _validate_group(parsed.research, dimensions, research_context)
        if reason := _stop_reason(deadline_at, cancellation):
            return {"status": reason, "proposals": []}
        rejection_diagnostics = []
        resolved = resolve_fact_suggestions(parsed.proposals, inputs=context, diagnostics=rejection_diagnostics)
        output = {"status": "completed", **parsed.model_dump(mode="json"),
                  "proposal_protocol": SUGGESTIONS_VERSION,
                  "period_binding_version": PERIOD_BINDING_VERSION,
                  "citation_locator_version": CITATION_LOCATOR_VERSION,
                  "research_citation_scope": citation_receipt,
                  "proposals": resolved["proposals"],
                  "proposal_rejections": [{"proposal_kind": "fact", **row} for row in resolved["rejected"]]}
        if diagnostics := _proposal_period_diagnostics(parsed.proposals, resolved["rejected"]):
            output["proposal_diagnostics"] = diagnostics
        if profile_scope:
            output["company_profile_proposal_protocol"] = PROFILE_SUGGESTIONS_VERSION
            profile_resolved = resolve_profile_suggestions(parsed.company_profile_proposals, scope=profile_scope,
                                                           diagnostics=rejection_diagnostics)
            output["company_profile_proposals"] = profile_resolved["proposals"]
            output["proposal_rejections"].extend(
                {"proposal_kind": "company_profile", **row} for row in profile_resolved["rejected"]
            )
        if rejection_diagnostics:
            output["suggestion_rejection_diagnostics"] = rejection_diagnostics
        return output
    except Exception as exc:  # noqa: BLE001 - retain the failed group's typed model diagnostics for manual recovery
        output = {"status": _stop_reason(deadline_at, cancellation) or "model_failed", "proposals": [],
                  "failure": model_failure_record(exc, stage=f"research_enrichment_{group_label}")}
        if isinstance(exc, UziInputBudgetExceeded):
            output.update(status=_stop_reason(deadline_at, cancellation) or "input_invalid",
                          input_errors=[{"reason": exc.code, "stage": "prompt_projection"}],
                          model_request_started=False)
        if citation_receipt is not None:
            output["research_citation_scope"] = citation_receipt
        if (isinstance(exc, UziQualitativeValidationError) and exc.code == "qualitative_ungrounded_url"
                and isinstance(parsed, (ResearchSuggestionGroup, CompanyResearchSuggestionGroup))):
            rejected_urls = _unbound_citation_diagnostics(parsed, research_context)
            output["citation_rejections"] = rejected_urls[:16]
            output["citation_rejection_count"] = len(rejected_urls)
        return output


def recompute_native_facts(raw, *, profile, deadline_at, cancellation=None, native_recompute=None):
    if reason := _stop_reason(deadline_at, cancellation):
        return {"status": reason}
    if native_recompute is None:
        from domain_packs.finance_ashare.subagents.uzi_392_analysis import (
            _prepare_native_computation,
        )
        native_recompute = _prepare_native_computation
    before = canonical_hash(raw)
    result = native_recompute(deepcopy(raw), profile=profile, school=None)
    status = _stop_reason(deadline_at, cancellation) or "completed"
    return {"status": status, "input_hash": before, "output_hash": canonical_hash(result), "result": result}


def _native_input_row(snapshot, *, evidence, cutoff_at):
    # Do not trim the input to the six edited dimensions, or lose the native
    # root/bonus presence transports when the collector stored them.
    facts = {"_uzi_raw_result": project_native_result(snapshot)}
    facts.update(native_transport_facts(snapshot, evidence=evidence, cutoff_at=cutoff_at))
    return {"symbol": snapshot.symbol,
            "snapshot_type": capability_for_dimension(snapshot.dimension).snapshot_type,
            "snapshot_id": snapshot.id, "content_hash": snapshot.content_hash,
            "facts": facts}


def _scope_group_results(provider, *, requested, inputs, deadline_at, cancellation, profile):
    """Parallel research over one already-fixed symbol/day, without DB access.

    These three orchestration workers still enter parse_uzi_structured's shared
    physical-request limiter. They create neither another LLM budget nor a
    second scope in flight. The owner consumes results and persists revisions.
    """
    with ThreadPoolExecutor(max_workers=len(_GROUPS), thread_name_prefix="uzi-research-facts") as pool:
        pending, skipped = {}, []
        for label, dimensions in _GROUPS:
            if not set(dimensions) & requested:
                continue
            if reason := _stop_reason(deadline_at, cancellation):
                skipped.append((label, {"status": reason, "proposals": []}))
            elif errors := [{"dimension": key, **inputs[key]["input_error"]}
                            for key in dimensions if inputs.get(key, {}).get("input_error")]:
                skipped.append((label, {"status": "input_invalid", "proposals": [], "input_errors": errors}))
            elif "7_industry" in dimensions and inputs.get("_company_profile_error"):
                skipped.append((label, {"status": "input_invalid", "proposals": [],
                                       "input_errors": [inputs["_company_profile_error"]]}))
            elif not any(inputs.get(key, {}).get("documents") for key in dimensions):
                skipped.append((label, {"status": "original_documents_missing", "proposals": []}))
            else:
                pending[pool.submit(
                    request_fact_proposals, provider, group_label=label, dimensions=dimensions,
                    inputs=deepcopy(inputs), deadline_at=deadline_at,
                    cancellation=cancellation, profile=profile,
                )] = label
        yield from skipped
        for future in as_completed(pending):
            label = pending[future]
            try:
                output = future.result()
            except Exception as exc:  # noqa: BLE001 - one group must not discard sibling results
                output = {"status": _stop_reason(deadline_at, cancellation) or "model_failed",
                          "proposals": [], "failure": model_failure_record(exc, stage=f"research_enrichment_{label}")}
            yield label, output


def run_research_enrichment(database, *, job_id, registry, provider, deadline_at,
                            cancellation=None, profile="medium", native_recompute=None,
                            progress_callback=None, write_fence=None, task_ids=None):
    """Caller explicitly opts in and supplies the existing job's common deadline.

    Three research groups run in parallel over each fixed symbol/day input;
    shared physical model limits, timer budget and report-side authority stay unchanged.
    Only dimensions in the supplied task IDs can be revised. ``None`` retains
    the existing explicit whole-job direct-call contract; coordinator workers
    always supply their claimed wave IDs. An empty set grants no authority.
    """
    from domain_packs.finance_ashare.subagents.uzi_392_native_methods import (
        build_uzi_raw,
    )
    if write_fence is not None:
        cancellation = _LocalCancellation(cancellation)
    result = {"version": ENRICHMENT_VERSION, "status": "running", "groups": [], "revisions": [], "rejected": [], "recomputations": [], "company_profile_rebuilds": [], "report_delivery_assessed": False}

    def persist_progress():
        if progress_callback is not None:
            # The owning worker applies its lease fence and state merge. Do
            # not independently overwrite its progress or a newer generation.
            progress_callback(deepcopy(result))
            return
        with database.session() as session:
            job = session.get(MarketDataCollectionJob, job_id)
            if job is None:
                raise ValueError("collection job not found")
            job.checkpoint_json = {**dict(job.checkpoint_json or {}), "research_enrichment": deepcopy(result)}
            job.progress_json = {**dict(job.progress_json or {}), "research_enrichment": deepcopy(result)}

    with database.session() as session:
        job = session.get(MarketDataCollectionJob, job_id)
        if job is None:
            raise ValueError("collection job not found")
        if job.origin != "manual":
            return {**result, "status": "explicit_collection_required"}
        if job.cancel_requested:
            return {**result, "status": "cancelled"}
        query = select(MarketDataCollectionTask).where(MarketDataCollectionTask.job_id == job_id)
        if task_ids is not None:
            try:
                if isinstance(task_ids, (str, bytes)):
                    raise TypeError("invalid task IDs")
                task_ids = frozenset(task_ids)
                if any(not isinstance(key, str) or not key for key in task_ids):
                    raise ValueError("invalid task IDs")
            except (TypeError, ValueError):
                return {**result, "status": "input_invalid", "rejected": [{"reason": "research_task_scope_invalid"}]}
            if not task_ids:
                return {**result, "status": "not_requested", "reason_code": "research_task_scope_empty"}
            query = query.where(MarketDataCollectionTask.id.in_(task_ids))
        tasks = list(session.scalars(query))
        if task_ids is not None and {task.id for task in tasks} != task_ids:
            return {**result, "status": "input_invalid", "rejected": [{"reason": "research_task_scope_invalid"}]}
        scopes = defaultdict(set)
        for task in tasks:
            if task.dimension in FACT_FIELDS or task.dimension == "0_basic":
                for offset in range((task.end_date - task.start_date).days + 1):
                    scopes[(task.symbol, task.market, task.exchange_timezone, task.start_date + timedelta(days=offset))].add(task.dimension)
    for (symbol, market, zone, as_of), requested in scopes.items():
        if reason := _stop_reason(deadline_at, cancellation):
            result["status"] = reason
            break
        with database.session() as session:
            repo = MarketDataRepository(session)
            coverage = SnapshotResolver(repo, registry=registry).coverage(symbol, market, as_of, exchange_timezone=zone)
            snapshots = {row.dimension: repo.require_snapshot(row.snapshot_id) for row in coverage.dimensions if row.snapshot_id}
            cutoff = cutoff_at_for_date(as_of, zone)
            evidence_by_snapshot = {row.id: repo.list_snapshot_evidence(row.id) for row in snapshots.values()}
            inputs = {}
            # A one-dimension retry still answers the native paired research
            # questions. Read the partner's existing PIT-safe evidence only;
            # selected_ids below remains the sole revision authority.
            research_requested = requested | ({"7_industry"} if "0_basic" in requested else set())
            research_dimensions = {key for _, keys in _GROUPS if research_requested.intersection(keys) for key in keys}
            for dimension in sorted(research_dimensions):
                snapshot = snapshots.get(dimension)
                if snapshot is None:
                    continue
                try:
                    documents = _research_documents(snapshot, evidence_by_snapshot[snapshot.id], cutoff)
                except ResearchTextError as exc:
                    inputs[dimension] = {"snapshot_id": snapshot.id, "snapshot_hash": snapshot.content_hash,
                                         "documents": [], "allowed_urls": [],
                                         "input_error": {"reason": exc.code}}
                    continue
                inputs[dimension] = {
                    "snapshot_id": snapshot.id, "snapshot_hash": snapshot.content_hash,
                    "symbol": snapshot.symbol,
                    "revision_allowed": dimension in requested,
                    "raw": deepcopy(snapshot.payload_json.get("raw") or {}),
                    "as_of_date": as_of.isoformat(),
                    "allowed_urls": list(dict.fromkeys(row["url"] for row in documents)),
                    "documents": documents,
                }
            selected_ids = {row.id for row in snapshots.values() if row.dimension in requested and row.dimension in FACT_FIELDS}
            if "0_basic" in requested:
                basic = snapshots.get("0_basic")
                try:
                    if basic is None:
                        raise CompanyProfileRefusal("profile_basic_snapshot_missing")
                    inputs["_company_profile_scope"] = build_company_profile_scope(
                        repo, basic_snapshot=basic, cutoff_at=cutoff,
                        source_snapshots=[snapshots[key] for key in ("7_industry", "15_events") if key in snapshots],
                    )
                except (CompanyProfileRefusal, ResearchTextError) as exc:
                    inputs["_company_profile_error"] = {"dimension": "0_basic", "reason": getattr(exc, "reason", None) or exc.code}
            prepared_rows = {dimension: _native_input_row(snapshot, evidence=evidence_by_snapshot[snapshot.id], cutoff_at=cutoff) for dimension, snapshot in snapshots.items()}
            # Coverage has already enforced the same symbol/day and PIT
            # visibility. Reuse those native projections as read-only model
            # context, without admitting their URLs or snapshots for editing.
            inputs["_read_only_context"] = {
                "symbol": symbol, "market": market,
                "as_of_date": as_of.isoformat(), "exchange_timezone": zone,
                "cutoff_at": cutoff.isoformat(),
                "requested_dimensions": sorted(requested),
                "snapshot_inputs": [
                    {"dimension": key, "snapshot_id": row["snapshot_id"],
                     "content_hash": row["content_hash"]}
                    for key, row in sorted(prepared_rows.items())
                ],
                "dimensions": {
                    key: deepcopy(row["facts"]["_uzi_raw_result"])
                    for key, row in sorted(prepared_rows.items())
                },
            }
        accepted_for_scope = []
        profile_rebuild = None
        for label, output in _scope_group_results(
            provider, requested=research_requested, inputs=inputs, deadline_at=deadline_at,
            cancellation=cancellation, profile=profile,
        ):
            result["groups"].append({"symbol": symbol, "as_of_date": as_of.isoformat(), "group": label, **output})
            result["rejected"].extend(
                {"symbol": symbol, "as_of_date": as_of.isoformat(), "group": label, **row}
                for row in output.get("proposal_rejections", [])
            )
            with database.session() as session:
                applied = persist_fact_revisions(MarketDataRepository(session), proposals=output["proposals"], registry=registry, job_id=job_id, allowed_snapshot_ids=selected_ids, deadline_at=deadline_at, cancellation=cancellation, write_fence=write_fence)
            accepted_for_scope.extend(applied["revisions"])
            result["revisions"].extend([{k: v for k, v in row.items() if k != "native_projection"} for row in applied["revisions"]])
            result["rejected"].extend(applied["rejected"])
            if (profile_scope := inputs.get("_company_profile_scope")) and (proposals := output.get("company_profile_proposals")):
                with database.session() as session:
                    applied_profile = persist_company_profile_revisions(
                        MarketDataRepository(session), proposals=proposals, scope=profile_scope,
                        authorized_basic_snapshot_ids={profile_scope["target"]["snapshot_id"]},
                        registry=registry, job_id=job_id, deadline_at=deadline_at,
                        cancellation=cancellation, write_fence=write_fence,
                    )
                accepted_for_scope.extend(applied_profile["revisions"])
                result["revisions"].extend([{key: value for key, value in row.items() if key != "native_projection"}
                                            for row in applied_profile["revisions"]])
                result["rejected"].extend(applied_profile["rejected"])
            persist_progress()
        if reason := _stop_reason(deadline_at, cancellation):
            result["status"] = reason
            break
        with database.session() as session:
            repo = MarketDataRepository(session)
            # Inspect this request's fixed inputs plus its accepted revisions,
            # not whatever another job may now expose as the latest version.
            selected = {key: repo.require_snapshot(row["snapshot_id"])
                        for key, row in prepared_rows.items()}
            for row in accepted_for_scope:
                selected[row["dimension"]] = repo.require_snapshot(row["snapshot_id"])
            dependency_check = check_company_profile_dependencies(selected)
            if dependency_check["status"] != "not_required":
                basic = selected["0_basic"]
                profile_marker = basic.query_manifest_json.get("company_profile_revision")
                profile_rebuild = {
                    "symbol": symbol, "as_of_date": as_of.isoformat(),
                    "status": "completed" if dependency_check["can_freeze"] else "missing_dependency",
                    "reason": "company_profile_dependencies_bound" if dependency_check["can_freeze"]
                              else "company_profile_dependencies_require_rebuild",
                    "dependency_fingerprint": dependency_check["dependency_fingerprint"],
                    "basic_snapshot_id": basic.id,
                    "required": deepcopy(profile_marker.get("rebuild_required", {})) if isinstance(profile_marker, dict) else {},
                    "pending_dimensions": dependency_check["pending_dimensions"],
                    "issues": dependency_check["issues"],
                }
                result["company_profile_rebuilds"].append(profile_rebuild)
            if accepted_for_scope and dependency_check["can_freeze"]:
                for row in accepted_for_scope:
                    revision = selected[row["dimension"]]
                    prepared_rows[row["dimension"]] = _native_input_row(
                        revision, evidence=repo.list_snapshot_evidence(revision.id), cutoff_at=cutoff,
                    )
        if accepted_for_scope:
            if profile_rebuild and profile_rebuild["status"] != "completed":
                # Task 1.5–3 recomputation does not rebuild raw industry/peer
                # inputs. Never score a revised identity with those old facts.
                result["recomputations"].append(deepcopy(profile_rebuild))
                continue
            try:
                recomputed = recompute_native_facts(build_uzi_raw(list(prepared_rows.values()), symbol), profile=profile, deadline_at=deadline_at, cancellation=cancellation, native_recompute=native_recompute)
                # Do not copy raw documents/large native graphs into job logs.
                result["recomputations"].append({"symbol": symbol, "as_of_date": as_of.isoformat(), "snapshot_inputs": [{"dimension": key, "snapshot_id": row["snapshot_id"], "content_hash": row["content_hash"]} for key, row in sorted(prepared_rows.items())], **{key: value for key, value in recomputed.items() if key != "result"}})
            except Exception as exc:  # noqa: BLE001 - native recompute failure is not a model-output repair
                result["recomputations"].append({"symbol": symbol, "status": "failed", "failure": {
                    "stage": "research_native_recompute", "kind": "pipeline_failure",
                    "error_type": type(exc).__name__,
                }})
    if result["status"] == "running":
        failures = result["rejected"] or any(row["status"] != "completed" for row in [*result["groups"], *result["recomputations"], *result["company_profile_rebuilds"]])
        result["status"] = "partial" if failures else "completed"
    persist_progress()
    return result
