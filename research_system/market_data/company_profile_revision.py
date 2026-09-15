"""Explicit, append-only company identity repair from fixed PIT documents.

This module neither discovers sources nor executes models/native algorithms.
Building a read-only scope grants no write authority. A profile revision does
not refresh market observations or establish downstream report readiness.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from time import monotonic
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    field_validator,
)

from research_system.market_data.company_profile_dependencies import (
    DEPENDENCY_LINEAGE_RULES_VERSION,
    DEPENDENCY_LINEAGE_VERSION,
    _fingerprint_error,
    profile_consumer_input_hash,
)
from research_system.market_data.contracts import (
    EvidenceInput,
    SeriesPointInput,
    SnapshotIngestInput,
    canonical_hash,
    canonical_json,
    cutoff_at_for_date,
    require_aware_utc,
)
from research_system.market_data.dimension_contracts import DIMENSION_IDS
from research_system.market_data.ingestion import (
    SnapshotIngestionService,
    snapshot_content_hash,
)
from research_system.market_data.native_projection import project_native_result
from research_system.market_data.research_archive import research_publication_projection
from research_system.market_data.research_archive_text import (
    ResearchTextError,
    snapshot_text_bindings,
    verified_text_projection,
)
from research_system.market_data.resolver import (
    _database_utc,
    _snapshot_pit_visibility_errors,
)

COMPANY_PROFILE_VERSION = "company-profile-revision-v1"
PROFILE_FIELDS = ("name", "industry", "main_business")


class CompanyProfileRefusal(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class CompanyProfileCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_snapshot_id: str = Field(min_length=1)
    source_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_id: str = Field(min_length=1)
    document_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    text_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1, max_length=10_000)


class CompanyProfileProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str = Field(min_length=1)
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    field: Literal["name", "industry", "main_business"]
    old_exists: StrictBool
    old_value: StrictStr | None
    new_value: StrictStr = Field(min_length=1, max_length=2_000)
    industry_basis: Literal["issuer_disclosure", "classification"] | None = None
    classification_system: StrictStr | None = Field(default=None, min_length=1, max_length=200)
    citations: list[CompanyProfileCitation] = Field(min_length=1, max_length=8)
    classification_citations: list[CompanyProfileCitation] = Field(default_factory=list, max_length=8)

    @field_validator("new_value", "classification_system")
    @classmethod
    def nonempty_text(cls, value):
        if value is not None and not value.strip():
            raise ValueError("profile fact text must be nonempty")
        return value


def _stop(deadline_at, cancellation):
    if cancellation is not None and cancellation.is_set():
        raise CompanyProfileRefusal("cancelled")
    if deadline_at is not None and monotonic() >= deadline_at:
        raise CompanyProfileRefusal("deadline_exceeded")


def _snapshot_identity(snapshot):
    return {"snapshot_id": snapshot.id, "snapshot_hash": snapshot.content_hash}


def _check_snapshot(repository, snapshot, expected_hash, cutoff):
    if snapshot.content_hash != expected_hash:
        raise CompanyProfileRefusal("input_hash_mismatch")
    if snapshot.revoked_at is not None or snapshot.status == "revoked":
        raise CompanyProfileRefusal("input_revoked")
    digest = snapshot_content_hash(
        symbol=snapshot.symbol, market=snapshot.market, exchange_timezone=snapshot.exchange_timezone,
        dimension=snapshot.dimension, contract_version=snapshot.contract_version,
        as_of_date=snapshot.as_of_date, status=snapshot.status, evidence_grade=snapshot.evidence_grade,
        payload=snapshot.payload_json, effective_at=_database_utc(snapshot.effective_at),
        published_at=_database_utc(snapshot.published_at), source_version=snapshot.source_version,
        source_priority=snapshot.source_priority, query_manifest=snapshot.query_manifest_json,
        field_sources=snapshot.field_sources_json,
        series_hashes=[row.content_hash for row in repository.list_snapshot_series_points(snapshot.id)],
    )
    if digest != expected_hash:
        raise CompanyProfileRefusal("input_content_hash_mismatch")
    if _snapshot_pit_visibility_errors(repository, snapshot, cutoff):
        raise CompanyProfileRefusal("input_pit_invalid")


def _evidence_input(row):
    return EvidenceInput(
        provider=row.provider, source_url=row.source_url, source_kind=row.source_kind,
        title=row.title, mime_type=row.mime_type, content_hash=row.content_hash,
        content_ref=row.content_ref, inline_content=row.inline_content,
        published_at=_database_utc(row.published_at), observed_at=_database_utc(row.observed_at),
        grade=row.grade, immutable_proof=row.immutable_proof, is_manual=row.is_manual,
        metadata=deepcopy(row.metadata_json or {}),
    )


def _document(snapshot, proof, bindings, cutoff):
    if proof.source_kind != "research_document":
        return None
    if (not proof.inline_content or not proof.content_ref or not proof.content_hash
            or not proof.source_url or not proof.source_url.startswith(("http://", "https://"))
            or proof.grade not in {"A", "B"}):
        raise CompanyProfileRefusal("original_document_required")
    if _database_utc(proof.published_at) > cutoff:
        raise CompanyProfileRefusal("evidence_after_cutoff")
    if _database_utc(proof.observed_at) > cutoff and not (
        proof.grade == "B" and proof.immutable_proof
        and (proof.metadata_json or {}).get("evidence_mode")
        in {"public_archive_reconstruction", "contemporaneous_archive"}
    ):
        raise CompanyProfileRefusal("historical_version_unproven")
    try:
        if sha256(Path(proof.content_ref).read_bytes()).hexdigest() != proof.content_hash:
            raise CompanyProfileRefusal("document_content_corrupt")
    except (OSError, ValueError) as exc:
        if isinstance(exc, CompanyProfileRefusal):
            raise
        raise CompanyProfileRefusal("document_content_unavailable") from None
    expected = bindings.get(proof.evidence_key)
    view = verified_text_projection(proof, expected) if expected is not None else {"content": proof.inline_content}
    text = view["content"]
    return {
        "source_snapshot_id": snapshot.id, "source_snapshot_hash": snapshot.content_hash,
        "evidence_id": proof.id, "evidence_key": proof.evidence_key,
        "document_hash": proof.content_hash, "url": proof.source_url, "title": proof.title,
        "published_at": _database_utc(proof.published_at).isoformat(),
        "observed_at": _database_utc(proof.observed_at).isoformat(),
        "grade": proof.grade, "immutable_proof": proof.immutable_proof,
        "inline_content": text, "text_content_hash": sha256(text.encode("utf-8")).hexdigest(),
        "text_mode": "explicit_bound_text" if expected is not None else "legacy_inline",
        **({"text_extraction": deepcopy(view["text_extraction"])} if expected is not None else {}),
        **research_publication_projection(proof),
    }


def build_company_profile_scope(repository, *, basic_snapshot, source_snapshots, cutoff_at=None,
                                deadline_at=None, cancellation=None):
    """Freeze an explicit same-stock/day read scope; this never authorizes a write.

    Caller supplies persisted snapshots, not model-selected IDs. Exact cutoff
    may narrow the day's PIT boundary but may not extend it. Identity facts are
    descriptions known by that boundary, not invented effective-date claims.
    """
    _stop(deadline_at, cancellation)
    basic = repository.require_snapshot(basic_snapshot.id)
    if basic.dimension != "0_basic":
        raise CompanyProfileRefusal("profile_target_must_be_basic")
    day_cutoff = cutoff_at_for_date(basic.as_of_date, basic.exchange_timezone)
    cutoff = require_aware_utc(cutoff_at, field_name="cutoff_at") if cutoff_at is not None else day_cutoff
    if cutoff > day_cutoff:
        raise CompanyProfileRefusal("cutoff_outside_target_day")
    _check_snapshot(repository, basic, basic_snapshot.content_hash, cutoff)
    source_by_id = {row.id: row for row in source_snapshots}
    if len(source_by_id) != len(source_snapshots):
        raise CompanyProfileRefusal("duplicate_source_snapshot")
    sources, documents = [], []
    for supplied in sorted(source_by_id.values(), key=lambda row: row.id):
        _stop(deadline_at, cancellation)
        row = repository.require_snapshot(supplied.id)
        if (row.symbol, row.market, row.as_of_date, row.exchange_timezone) != (
            basic.symbol, basic.market, basic.as_of_date, basic.exchange_timezone,
        ):
            raise CompanyProfileRefusal("source_scope_mismatch")
        _check_snapshot(repository, row, supplied.content_hash, cutoff)
        sources.append(row)
    for row in {entry.id: entry for entry in [basic, *sources]}.values():
        proofs = repository.list_snapshot_evidence(row.id)
        bindings = snapshot_text_bindings(row, proofs)
        for proof in proofs:
            _stop(deadline_at, cancellation)
            document = _document(row, proof, bindings, cutoff)
            if document is not None:
                documents.append(document)
    # The existing profile receipt/scope contract fixes v3, independently of
    # the projection selected by a new report Manifest. Do not reinterpret
    # historical payload lineage when a newer display projection is shipped.
    raw = deepcopy(project_native_result(basic, version="uzi-dim-result-v3").get("data") or {})
    scope = {
        "version": COMPANY_PROFILE_VERSION,
        "target": {**_snapshot_identity(basic), "symbol": basic.symbol, "market": basic.market,
                   "as_of_date": basic.as_of_date.isoformat(), "exchange_timezone": basic.exchange_timezone,
                   "quote_observed_at": _database_utc(basic.observed_at).isoformat(),
                   "raw": raw},
        "cutoff_at": cutoff.isoformat(),
        "allowed_fields": list(PROFILE_FIELDS), "read_only": True,
        "source_snapshots": [_snapshot_identity(row) for row in sources],
        "documents": sorted(documents, key=lambda row: (row["source_snapshot_id"], row["evidence_id"])),
        "temporal_semantics": "document-supported profile known by cutoff; effective dates and financial periods are not inferred",
    }
    if "company_profile_revision" in (basic.query_manifest_json or {}):
        scope["dependency_lineage"] = _profile_lineage(
            repository, basic, cutoff, deadline_at=deadline_at, cancellation=cancellation,
        )
    return {**scope, "scope_hash": canonical_hash(scope)}


def _citation_documents(citations, scope):
    catalog = {(row["source_snapshot_id"], row["evidence_id"]): row for row in scope["documents"]}
    selected = []
    for citation in citations:
        document = catalog.get((citation.source_snapshot_id, citation.evidence_id))
        if document is None:
            raise CompanyProfileRefusal("evidence_not_bound")
        if citation.source_snapshot_hash != document["source_snapshot_hash"]:
            raise CompanyProfileRefusal("input_hash_mismatch")
        if citation.document_hash != document["document_hash"]:
            raise CompanyProfileRefusal("document_hash_mismatch")
        if citation.text_content_hash != document["text_content_hash"]:
            raise CompanyProfileRefusal("text_hash_mismatch")
        text = document["inline_content"]
        if not 0 <= citation.start < citation.end <= len(text):
            raise CompanyProfileRefusal("excerpt_bounds_invalid")
        if text[citation.start:citation.end] != citation.quote:
            raise CompanyProfileRefusal("excerpt_mismatch")
        selected.append(document)
    return selected


def _validate_proposal(item, scope):
    from research_system.market_data.research_suggestions import literal_text_present

    proposal = CompanyProfileProposal.model_validate(item)
    target = scope["target"]
    if proposal.snapshot_id != target["snapshot_id"] or proposal.snapshot_hash != target["snapshot_hash"]:
        raise CompanyProfileRefusal("input_hash_mismatch")
    raw = target["raw"]
    if proposal.old_exists != (proposal.field in raw) or canonical_json(proposal.old_value) != canonical_json(raw.get(proposal.field)):
        raise CompanyProfileRefusal("old_value_mismatch")
    if proposal.old_exists and proposal.old_value == proposal.new_value:
        raise CompanyProfileRefusal("no_change")
    documents = _citation_documents(proposal.citations, scope)
    if not any(literal_text_present(proposal.new_value, ref.quote) for ref in proposal.citations):
        raise CompanyProfileRefusal("value_not_grounded")
    if proposal.field == "industry":
        if proposal.industry_basis is None:
            raise CompanyProfileRefusal("industry_basis_required")
        if proposal.industry_basis == "classification":
            if not proposal.classification_system or not proposal.classification_citations:
                raise CompanyProfileRefusal("classification_evidence_required")
            documents.extend(_citation_documents(proposal.classification_citations, scope))
            if not any(literal_text_present(proposal.classification_system, ref.quote)
                       and literal_text_present(proposal.new_value, ref.quote)
                       for ref in proposal.classification_citations):
                raise CompanyProfileRefusal("classification_not_grounded")
        elif proposal.classification_system is not None or proposal.classification_citations:
            raise CompanyProfileRefusal("issuer_disclosure_is_not_classification")
    elif proposal.industry_basis is not None or proposal.classification_system is not None or proposal.classification_citations:
        raise CompanyProfileRefusal("classification_only_applies_to_industry")
    return proposal, documents


def _rebuild_summary(fields, *, before, after):
    # Pinned A-share fetcher input dependencies, not broad topic relevance or
    # native quality gates. In particular fetch_chain reads its own THS/EM
    # business tables; no native fetcher consumes basic.main_business.
    direct = set()
    if "industry" in fields:
        direct.update((
            "3_macro", "4_peers", "7_industry", "8_materials", "9_futures",
            "10_valuation", "13_policy", "similar_stocks",
        ))
        # Native fetch_sector_lhb only gates on truthiness; it does not filter
        # its all-market response by the industry value. Do not invent that
        # filtering or invalidate it for one nonempty label becoming another.
        if bool(before.get("industry")) != bool(after.get("industry")):
            direct.add("16_lhb")
    if "name" in fields:
        direct.update(("4_peers", "14_moat", "15_events", "17_sentiment", "18_trap"))
    required = [dimension for dimension in DIMENSION_IDS if dimension in direct]
    return {
        "version": "native-profile-fetch-dependencies-v1",
        "status": "pending", "changed_fields": sorted(fields),
        "required_dimensions": required,
        "direct_dimensions": list(required),  # compatibility alias, not an extra gate
        "review_dimensions": [],
        "derived_stages": ["native_modeling", "qualitative_research", "native_panel", "native_review"],
        "reason": "company_profile_changed_no_downstream_rebuild_performed",
    }


def _with_lineage(summary, lineage):
    """Keep obligations, not a demand to recollect already-bound consumers."""
    if lineage is None:
        return summary
    required = set(summary["required_dimensions"]) | set(lineage["consumer_anchors"])
    return {
        **summary, "version": DEPENDENCY_LINEAGE_RULES_VERSION,
        "current_required_dimensions": list(summary["required_dimensions"]),
        "required_dimensions": [key for key in DIMENSION_IDS if key in required],
        "direct_dimensions": [key for key in DIMENSION_IDS if key in required],
        "dependency_lineage": deepcopy(lineage),
    }


def _profile_lineage(repository, basic, cutoff, *, deadline_at=None, cancellation=None):
    """Validate the explicit 0-basic parent chain, never resolve 'latest'.

    Only this producer reads ancestors/documents. Its frozen attestation lets
    the pure Bundle guard check old receipts without reading a mutable DB head.
    No current snapshot hash is included in its own fingerprint.
    """
    records, seen, cursor = [], set(), basic
    while "company_profile_revision" in (cursor.query_manifest_json or {}):
        _stop(deadline_at, cancellation)
        if cursor.id in seen:
            raise CompanyProfileRefusal("profile_lineage_cycle")
        if len(seen) >= 256:
            raise CompanyProfileRefusal("profile_lineage_depth_exceeded")
        seen.add(cursor.id)
        marker = cursor.query_manifest_json["company_profile_revision"]
        if not isinstance(marker, dict):
            raise CompanyProfileRefusal("profile_lineage_marker_invalid")
        error = _fingerprint_error(cursor, marker)
        if error:
            raise CompanyProfileRefusal(error)
        if marker["input_snapshot_id"] in seen:
            raise CompanyProfileRefusal("profile_lineage_cycle")
        parent = repository.require_snapshot(marker["input_snapshot_id"])
        for row, digest in ((cursor, cursor.content_hash), (parent, marker["input_snapshot_hash"])):
            if (row.dimension != "0_basic" or any(getattr(row, field) != getattr(basic, field)
                    for field in ("symbol", "market", "as_of_date", "exchange_timezone", "contract_version"))):
                raise CompanyProfileRefusal("profile_lineage_scope_mismatch")
            _check_snapshot(repository, row, digest, cutoff)
        before = deepcopy(project_native_result(parent, version="uzi-dim-result-v3").get("data") or {})
        after = deepcopy(project_native_result(cursor, version="uzi-dim-result-v3").get("data") or {})
        sources = []
        for identity in marker["source_snapshot_inputs"]:
            _stop(deadline_at, cancellation)
            source = repository.require_snapshot(identity["snapshot_id"])
            if any(getattr(source, field) != getattr(basic, field)
                   for field in ("symbol", "market", "as_of_date", "exchange_timezone")):
                raise CompanyProfileRefusal("profile_lineage_source_scope_mismatch")
            _check_snapshot(repository, source, identity["snapshot_hash"], cutoff)
            sources.append(source)
        documents = []
        for row in {entry.id: entry for entry in [parent, *sources]}.values():
            proofs = repository.list_snapshot_evidence(row.id)
            bindings = snapshot_text_bindings(row, proofs)
            for proof in proofs:
                _stop(deadline_at, cancellation)
                document = _document(row, proof, bindings, cutoff)
                if document is not None:
                    documents.append(document)
        fact_scope = {"target": {**_snapshot_identity(parent), "raw": before}, "documents": documents}
        fields, used = set(), set()
        expected_payload = deepcopy(parent.payload_json)
        expected_payload["raw"] = deepcopy(before)
        for fact in marker["facts"]:
            try:
                proposal, cited = _validate_proposal(fact, fact_scope)
            except CompanyProfileRefusal:
                raise
            except Exception:  # noqa: BLE001 - corrupt historical lineage must fail closed without echoing stored content
                raise CompanyProfileRefusal("profile_lineage_fact_invalid") from None
            if proposal.field in fields:
                raise CompanyProfileRefusal("profile_lineage_fact_invalid")
            fields.add(proposal.field)
            used.update((row["source_snapshot_id"], row["source_snapshot_hash"]) for row in cited)
            expected_payload["raw"][proposal.field] = proposal.new_value
            if proposal.field in expected_payload:
                expected_payload[proposal.field] = proposal.new_value
        if used != {(row.id, row.content_hash) for row in sources}:
            raise CompanyProfileRefusal("profile_lineage_sources_mismatch")
        if expected_payload != cursor.payload_json or expected_payload["raw"] != after:
            raise CompanyProfileRefusal("profile_lineage_payload_changed")
        if (any(getattr(cursor, field) != getattr(parent, field) for field in (
                "source_version", "source_priority", "status"))
                or any(_database_utc(getattr(cursor, field)) != _database_utc(getattr(parent, field))
                       for field in ("observed_at", "effective_at"))
                or _database_utc(cursor.published_at) > _database_utc(parent.observed_at)
                or sorted(row.content_hash for row in repository.list_snapshot_series_points(cursor.id))
                != sorted(row.content_hash for row in repository.list_snapshot_series_points(parent.id))):
            raise CompanyProfileRefusal("profile_lineage_quote_changed")
        records.append((cursor, parent, sources, _rebuild_summary(fields, before=before, after=after)))
        cursor = parent
    validated = {cursor.id: _snapshot_identity(cursor)}
    anchors = {}
    for row, parent, sources, current_summary in reversed(records):
        _stop(deadline_at, cancellation)
        marker = row.query_manifest_json["company_profile_revision"]
        parent_lineage = {
            "version": DEPENDENCY_LINEAGE_VERSION, "parent_basic": _snapshot_identity(parent),
            "validated_inputs": [validated[key] for key in sorted(validated)],
            "consumer_anchors": deepcopy(anchors),
        }
        expected = (_with_lineage(current_summary, parent_lineage)
                    if marker["rebuild_required"]["version"] == DEPENDENCY_LINEAGE_RULES_VERSION else current_summary)
        if marker["rebuild_required"] != expected:
            raise CompanyProfileRefusal("profile_lineage_summary_mismatch")
        current_required = set(current_summary["required_dimensions"])
        for dimension in set(anchors) | current_required:
            anchor = {
                "input_basic_snapshot_id": row.id, "input_basic_snapshot_hash": row.content_hash,
                "dependency_fingerprint": marker["dependency_fingerprint"],
                "profile_input_hash": profile_consumer_input_hash(dimension, row.payload_json["raw"]),
            }
            anchors[dimension] = [anchor] if dimension in current_required else [*anchors[dimension], anchor]
        validated.update({entry.id: _snapshot_identity(entry) for entry in [row, *sources]})
    return {
        "version": DEPENDENCY_LINEAGE_VERSION, "parent_basic": _snapshot_identity(basic),
        "validated_inputs": [validated[key] for key in sorted(validated)],
        "consumer_anchors": {key: anchors[key] for key in DIMENSION_IDS if key in anchors},
    }


def _revision_result(revision, basic_id, facts_count):
    return {"snapshot_id": revision.id, "content_hash": revision.content_hash,
            "input_snapshot_id": basic_id, "dimension": "0_basic", "facts_count": facts_count,
            "published_at": _database_utc(revision.published_at).isoformat(),
            "observed_at": _database_utc(revision.observed_at).isoformat(),
            "native_projection": project_native_result(revision)}


def persist_company_profile_revisions(repository, *, proposals, scope, authorized_basic_snapshot_ids,
                                      registry, job_id, now=None, deadline_at=None,
                                      cancellation=None, write_fence=None):
    """Make at most one new basic snapshot in the caller's fenced transaction.

    All valid fields share one atomic revision; individual refused proposals
    are disclosed. Neither existing snapshots nor source evidence are mutated.
    The caller must rebuild dependents before claiming a new report is ready.
    """
    result = {"version": COMPANY_PROFILE_VERSION, "revisions": [], "rejected": [],
              "dependency_fingerprint": None, "rebuild_required": {}, "ready_to_freeze": False}
    try:
        _stop(deadline_at, cancellation)
        if write_fence is not None and not write_fence(repository.session):
            raise CompanyProfileRefusal("worker_lease_lost")
        if not isinstance(scope, dict) or scope.get("scope_hash") != canonical_hash({key: value for key, value in scope.items() if key != "scope_hash"}):
            raise CompanyProfileRefusal("scope_hash_mismatch")
        if scope.get("version") != COMPANY_PROFILE_VERSION:
            raise CompanyProfileRefusal("scope_version_unsupported")
        target = scope["target"]
        if target["snapshot_id"] not in set(authorized_basic_snapshot_ids):
            raise CompanyProfileRefusal("profile_target_not_authorized")
        identities = [target, *scope["source_snapshots"],
                      *scope.get("dependency_lineage", {}).get("validated_inputs", [])]
        locked = {identity["snapshot_id"]: repository.lock_snapshot_for_update(identity["snapshot_id"])
                  for identity in sorted(identities, key=lambda row: row["snapshot_id"])}
        for identity in identities:
            if locked[identity["snapshot_id"]].content_hash != identity["snapshot_hash"]:
                raise CompanyProfileRefusal("input_hash_mismatch")
        basic = locked[target["snapshot_id"]]
        current = build_company_profile_scope(
            repository, basic_snapshot=basic,
            source_snapshots=[locked[row["snapshot_id"]] for row in scope["source_snapshots"]],
            cutoff_at=datetime.fromisoformat(scope["cutoff_at"]),
            deadline_at=deadline_at, cancellation=cancellation,
        )
        if current != scope:
            raise CompanyProfileRefusal("scope_binding_changed")
        accepted, used_documents, fields = [], {}, set()
        for item in proposals:
            _stop(deadline_at, cancellation)
            try:
                proposal, documents = _validate_proposal(item, current)
                if proposal.field in fields:
                    raise CompanyProfileRefusal("duplicate_field_proposal")
                accepted.append(proposal)
                fields.add(proposal.field)
                used_documents.update({(row["source_snapshot_id"], row["evidence_id"]): row for row in documents})
            except Exception as exc:  # noqa: BLE001 - fixed diagnostics never expose model content
                result["rejected"].append({"reason": getattr(exc, "reason", "proposal_invalid"), "error_type": type(exc).__name__})
        if not accepted:
            return result
        now = require_aware_utc(now or datetime.now(timezone.utc), field_name="now")
        payload = deepcopy(basic.payload_json)
        payload["raw"] = deepcopy(current["target"]["raw"])
        for proposal in accepted:
            payload["raw"][proposal.field] = proposal.new_value
            if proposal.field in payload:
                payload[proposal.field] = proposal.new_value
        facts = [row.model_dump(mode="json") for row in accepted]
        inputs = sorted({(row["source_snapshot_id"], row["source_snapshot_hash"]) for row in used_documents.values()})
        rebuild = _rebuild_summary(fields, before=current["target"]["raw"], after=payload["raw"])
        rebuild = _with_lineage(rebuild, current.get("dependency_lineage"))
        dependency_fingerprint = canonical_hash({
            "version": COMPANY_PROFILE_VERSION, "basic_input": _snapshot_identity(basic),
            "profile": {field: payload["raw"].get(field) for field in PROFILE_FIELDS},
            "source_inputs": inputs, "facts": facts, "rebuild_required": rebuild,
        })
        # A resumed owner may submit the same frozen proposal after its earlier
        # transaction committed. Keep that revision and its actual derived_at;
        # a later wall clock alone must not manufacture another identity fact.
        for candidate in repository.list_snapshots(
            symbol=basic.symbol, market=basic.market, dimension="0_basic", as_of_date=basic.as_of_date,
        ):
            marker = (candidate.query_manifest_json or {}).get("company_profile_revision") or {}
            if (marker.get("version") == COMPANY_PROFILE_VERSION and marker.get("job_id") == job_id
                    and marker.get("scope_hash") == current["scope_hash"]
                    and marker.get("dependency_fingerprint") == dependency_fingerprint):
                _check_snapshot(repository, candidate, candidate.content_hash, datetime.fromisoformat(scope["cutoff_at"]))
                result.update(dependency_fingerprint=dependency_fingerprint, rebuild_required=rebuild)
                result["revisions"].append(_revision_result(candidate, basic.id, len(accepted)))
                return result
        manifest = deepcopy(basic.query_manifest_json or {})
        manifest["company_profile_revision"] = {
            "version": COMPANY_PROFILE_VERSION, "job_id": job_id,
            "input_snapshot_id": basic.id, "input_snapshot_hash": basic.content_hash,
            "scope_hash": current["scope_hash"], "derived_at": now.isoformat(),
            "quote_observed_at_preserved": _database_utc(basic.observed_at).isoformat(),
            "facts": facts, "source_snapshot_inputs": [{"snapshot_id": key, "snapshot_hash": digest} for key, digest in inputs],
            "dependency_fingerprint": dependency_fingerprint, "rebuild_required": rebuild,
            "ready_to_freeze": False, "collector_envelope_preserved": True,
            "temporal_semantics": current["temporal_semantics"],
        }
        originals = repository.list_snapshot_evidence(basic.id)
        proof_by_id = {row.id: row for row in originals}
        for source_id, _proof_id in used_documents:
            proof_by_id.update({row.id: row for row in repository.list_snapshot_evidence(source_id)
                                if (source_id, row.id) in used_documents})
        evidence = [_evidence_input(row) for row in proof_by_id.values()]
        bindings = snapshot_text_bindings(basic, originals)
        bindings.update({row["evidence_key"]: row["text_extraction"] for row in used_documents.values()
                         if "text_extraction" in row})
        if bindings:
            manifest.setdefault("research_archive", {})["text_extractions"] = [
                {"evidence_key": key, "binding": deepcopy(binding)} for key, binding in sorted(bindings.items())
            ]
        field_sources = deepcopy(basic.field_sources_json or {})
        for proposal in accepted:
            citations = [*proposal.citations, *proposal.classification_citations]
            field_sources[f"raw.{proposal.field}"] = {
                "origin": COMPANY_PROFILE_VERSION,
                "evidence_ids": sorted({row.evidence_id for row in citations}),
                "source_snapshot_ids": sorted({row.source_snapshot_id for row in citations}),
                "industry_basis": proposal.industry_basis,
                "classification_system": proposal.classification_system,
                "derived_at": now.isoformat(),
            }
            if proposal.field in basic.payload_json:
                field_sources[proposal.field] = deepcopy(field_sources[f"raw.{proposal.field}"])
        # The shared snapshot schema cannot publish after its unchanged quote
        # observation. Refuse that case instead of refreshing old price data.
        published = max(_database_utc(basic.published_at), *(row.published_at for row in evidence))
        if published > _database_utc(basic.observed_at):
            raise CompanyProfileRefusal("profile_publication_after_quote_observation")
        ingest = SnapshotIngestInput(
            symbol=basic.symbol, market=basic.market, exchange_timezone=basic.exchange_timezone,
            dimension="0_basic", contract_version=basic.contract_version, as_of_date=basic.as_of_date,
            status=basic.status, payload=payload, effective_at=_database_utc(basic.effective_at),
            published_at=published, observed_at=_database_utc(basic.observed_at), ingested_at=now,
            source_version=basic.source_version, source_priority=basic.source_priority, origin="derived",
            query_manifest=manifest, validation=deepcopy(basic.validation_json), field_sources=field_sources,
            evidence=evidence, collection_task_id=basic.collection_task_id,
            provider_attempt_id=f"{COMPANY_PROFILE_VERSION}:{job_id}:{dependency_fingerprint}",
            series_points=[SeriesPointInput(
                series_key=row.series_key, point_at=_database_utc(row.point_at),
                effective_at=_database_utc(row.effective_at), published_at=_database_utc(row.published_at),
                observed_at=_database_utc(row.observed_at), value=row.value_json,
                source_version=row.source_version, metadata=deepcopy(row.metadata_json or {}),
            ) for row in repository.list_snapshot_series_points(basic.id)],
        )
        with repository.session.begin_nested():
            _stop(deadline_at, cancellation)
            if write_fence is not None and not write_fence(repository.session):
                raise CompanyProfileRefusal("worker_lease_lost")
            revision = SnapshotIngestionService(repository, registry=registry).ingest(ingest)
            if _snapshot_pit_visibility_errors(repository, revision, datetime.fromisoformat(scope["cutoff_at"])):
                raise CompanyProfileRefusal("revision_pit_invalid")
            if any(project_native_result(revision).get("data", {}).get(row.field) != row.new_value for row in accepted):
                raise CompanyProfileRefusal("native_projection_mismatch")
            for proposal in accepted:
                for citation in [*proposal.citations, *proposal.classification_citations]:
                    repository.add_snapshot_evidence(snapshot_id=revision.id, evidence_id=citation.evidence_id,
                                                     field_path=f"raw.{proposal.field}", purpose="supports")
            _stop(deadline_at, cancellation)
        result.update(dependency_fingerprint=dependency_fingerprint, rebuild_required=rebuild)
        result["revisions"].append(_revision_result(revision, basic.id, len(accepted)))
    except Exception as exc:  # noqa: BLE001 - no raw model values, text, paths or arbitrary exception messages
        reason = exc.code if isinstance(exc, ResearchTextError) else getattr(exc, "reason", "profile_revision_failed")
        result["rejected"].append({"reason": reason, "error_type": type(exc).__name__})
    return result


__all__ = ["CompanyProfileCitation", "CompanyProfileProposal", "CompanyProfileRefusal",
           "build_company_profile_scope", "persist_company_profile_revisions"]
