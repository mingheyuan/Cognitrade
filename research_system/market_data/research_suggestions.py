"""Bind model suggestions to trusted frozen context, without adopting facts.

These DTOs deliberately omit old values, hashes and character offsets. The
resolver fills those known identities but does not establish truth or grant
write authority: callers must pass its proposals through the existing fact or
company-profile persistence validators (including their hash/PIT/value checks).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from hashlib import sha256
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
)

from research_system.market_data.research_suggestion_diagnostics import (
    append_rejection_diagnostic,
)

SUGGESTIONS_VERSION = "uzi-research-suggestions-v1"
PROFILE_SUGGESTIONS_VERSION = "uzi-profile-suggestions-v2"
CITATION_LOCATOR_VERSION = "uzi-literal-pdf-line-wrap-v1"


class SuggestionCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_id: StrictStr = Field(min_length=1)
    quote: StrictStr = Field(min_length=1)

    @field_validator("quote")
    @classmethod
    def nonblank_quote(cls, value):
        if not value.strip():
            raise ValueError("quote must be nonempty")
        return value


class FactSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_id: StrictStr = Field(min_length=1)
    field: StrictStr = Field(min_length=1)
    new_value: StrictStr | StrictInt | StrictFloat | StrictBool | None
    unit: StrictStr = Field(min_length=1)
    period_text: StrictStr = Field(min_length=1)
    finding: StrictStr = Field(min_length=5, max_length=1200)
    citations: list[SuggestionCitation] = Field(min_length=1, max_length=8)


class ProfileSuggestionCitation(SuggestionCitation):
    source_snapshot_id: StrictStr | None = Field(default=None, min_length=1,
        description="Omit when evidence_id has a unique binding in the supplied profile scope; only use an exact source_snapshot_id to disambiguate multiple bindings.")
    quote: StrictStr = Field(min_length=1, max_length=10_000)


class ProfileSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: Literal["name", "industry", "main_business"]
    new_value: StrictStr = Field(min_length=1, max_length=2000)
    industry_basis: Literal["issuer_disclosure", "classification"] | None = None
    classification_system: StrictStr | None = Field(default=None, min_length=1, max_length=200)
    citations: list[ProfileSuggestionCitation] = Field(min_length=1, max_length=8)
    classification_citations: list[ProfileSuggestionCitation] = Field(default_factory=list, max_length=8)


class _Refusal(ValueError):
    def __init__(self, reason, *, validation=None):
        super().__init__(reason)
        self.reason = reason
        self.validation = validation


def _hash(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise _Refusal("input_hash_mismatch")
    return value


def _pdf_line_view(text):
    """Join only a PDF line wrap between Han characters, with an offset map.

    Do not collapse arbitrary whitespace: `1\n0` is not the number `10`, and
    Latin words, punctuation and Unicode forms must keep their exact spelling.
    """
    wrapped = set()
    for match in re.finditer(r"(?<=[\u3400-\u9fff])[ \t]*\r?\n[ \t]*(?=[\u3400-\u9fff])", text):
        wrapped.update(range(match.start(), match.end()))
    positions = [index for index in range(len(text)) if index not in wrapped]
    return "".join(text[index] for index in positions), positions


def literal_text_present(value, text):
    """The existing lexical support check, tolerant only of Han PDF wrapping."""
    return value in text or _pdf_line_view(value)[0] in _pdf_line_view(text)[0]


def _locate_quote(text, quote):
    start = text.find(quote)
    if start >= 0:
        if text.find(quote, start + 1) >= 0:
            raise _Refusal("quote_ambiguous")
        return start, start + len(quote)
    # Boundary whitespace is not content; map the unique layout-equivalent
    # string back to the unmodified archive rather than persisting model text.
    needle, _ = _pdf_line_view(quote.strip(" \t\r\n"))
    haystack, positions = _pdf_line_view(text)
    start = haystack.find(needle) if needle else -1
    if start < 0:
        raise _Refusal("quote_not_found")
    if haystack.find(needle, start + 1) >= 0:
        raise _Refusal("quote_ambiguous")
    return positions[start], positions[start + len(needle) - 1] + 1


def _citation(citation, documents, *, profile=False):
    matches = [row for row in documents if isinstance(row, Mapping)
               and row.get("evidence_id") == citation.evidence_id
               and (not profile or citation.source_snapshot_id is None
                    or row.get("source_snapshot_id") == citation.source_snapshot_id)]
    if not matches:
        raise _Refusal("evidence_not_bound")
    if len(matches) != 1:
        raise _Refusal("evidence_ambiguous")
    document = matches[0]
    text = document.get("inline_content")
    if not isinstance(text, str):
        raise _Refusal("original_document_required")
    start, end = _locate_quote(text, citation.quote)
    result = {
        "evidence_id": citation.evidence_id,
        "document_hash": _hash(document.get("document_hash")),
        "start": start, "end": end, "quote": text[start:end],
    }
    if profile:
        text_hash = _hash(document.get("text_content_hash"))
        if sha256(text.encode("utf-8")).hexdigest() != text_hash:
            raise _Refusal("text_hash_mismatch")
        result.update(
            source_snapshot_id=document["source_snapshot_id"],
            source_snapshot_hash=_hash(document.get("source_snapshot_hash")),
            text_content_hash=text_hash,
        )
    return result


def _citations(refs, documents, context, *, collection="citations", profile=False):
    previous_stage = context["stage"]
    context.update(stage="citation_resolution", documents=documents, citation_collection=collection)
    result = []
    for index, citation in enumerate(refs):
        context.update(citation_index=index, citation=citation)
        result.append(_citation(citation, documents, profile=profile))
    context.update(stage=previous_stage, citation=None)
    return result


def resolve_fact_suggestions(suggestions, *, inputs, diagnostics=None):
    """Resolve authorized per-dimension inputs; never consult another snapshot."""
    # Deferred to avoid a cycle when the request layer imports these new DTOs.
    from research_system.market_data.research_enrichment import (
        FACT_FIELDS,
        FactProposal,
        _fact_period,
    )

    proposals, rejected = [], []
    for index, item in enumerate(suggestions):
        context = {"stage": "suggestion_schema"}
        suggestion = None
        try:
            try:
                suggestion = FactSuggestion.model_validate(
                    item.model_dump() if isinstance(item, FactSuggestion) else item,
                )
            except ValidationError as exc:
                raise _Refusal("suggestion_invalid", validation=exc) from None
            context["stage"] = "snapshot_scope"
            matches = [(dimension, row) for dimension, row in inputs.items()
                       if dimension in FACT_FIELDS and isinstance(row, Mapping)
                       and row.get("snapshot_id") == suggestion.snapshot_id]
            if len(matches) != 1:
                raise _Refusal("snapshot_outside_requested_scope")
            dimension, row = matches[0]
            context.update(dimension=dimension, input=row, raw=row.get("raw"),
                           allowed_fields=FACT_FIELDS[dimension], known_fields=set().union(*FACT_FIELDS.values()))
            if row.get("revision_allowed") is not True:
                raise _Refusal("snapshot_outside_requested_scope")
            context["stage"] = "field_scope"
            if suggestion.field not in FACT_FIELDS[dimension]:
                raise _Refusal("field_not_supported")
            raw = row.get("raw")
            context["stage"] = "input_binding"
            if not isinstance(raw, Mapping):
                raise _Refusal("frozen_input_invalid")
            context["stage"] = "period_resolution"
            try:
                period = _fact_period(suggestion.period_text)
            except ValueError:
                period = None
            if period is None:
                raise _Refusal("period_not_supported_or_mismatch")
            context["stage"] = "proposal_binding"
            citations = _citations(suggestion.citations, row.get("documents") or [], context)
            if not any(suggestion.period_text in ref["quote"] for ref in citations):
                from research_system.market_data.research_period_context import (
                    report_period_citation,
                )

                binding = report_period_citation(
                    dimension=dimension, field=suggestion.field, value=suggestion.new_value,
                    unit=suggestion.unit, period=period, symbol=row.get("symbol"),
                    documents=row.get("documents") or [], citations=citations)
                if binding is not None and binding not in citations and len(citations) < 8:
                    citations.append(binding)
            proposal = {
                **suggestion.model_dump(exclude={"citations"}),
                "snapshot_hash": _hash(row.get("snapshot_hash")), "dimension": dimension,
                "old_exists": suggestion.field in raw,
                "old_value": deepcopy(raw.get(suggestion.field)),
                "period_start": period[0], "period_end": period[1], "citations": citations,
            }
            context["stage"] = "proposal_schema"
            proposals.append(FactProposal.model_validate(proposal).model_dump(mode="json"))
        except _Refusal as exc:
            rejected.append({"suggestion_index": index, "reason": exc.reason})
            append_rejection_diagnostic(diagnostics, index=index, kind="fact", reason=exc.reason,
                                        context=context, suggestion=suggestion, validation=exc.validation)
        except (ValidationError, TypeError, KeyError, AttributeError) as exc:
            rejected.append({"suggestion_index": index, "reason": "resolved_proposal_invalid"})
            append_rejection_diagnostic(diagnostics, index=index, kind="fact", reason="resolved_proposal_invalid",
                                        context=context, suggestion=suggestion, validation=exc)
    return {"proposals": proposals, "rejected": rejected}


def resolve_profile_suggestions(suggestions, *, scope, diagnostics=None):
    """Use a fixed profile scope for identity only; it remains a read-only scope."""
    from research_system.market_data.company_profile_revision import (
        CompanyProfileProposal,
    )

    proposals, rejected = [], []
    for index, item in enumerate(suggestions):
        scope_context = scope if isinstance(scope, Mapping) else {}
        target_context = scope_context.get("target")
        target_context = target_context if isinstance(target_context, Mapping) else {}
        context = {"stage": "suggestion_schema", "dimension": "0_basic",
                   "input": target_context, "raw": target_context.get("raw"),
                   "known_fields": {"name", "industry", "main_business"},
                   "allowed_fields": scope_context.get("allowed_fields", [])}
        suggestion = None
        try:
            try:
                suggestion = ProfileSuggestion.model_validate(
                    item.model_dump() if isinstance(item, ProfileSuggestion) else item,
                )
            except ValidationError as exc:
                raise _Refusal("suggestion_invalid", validation=exc) from None
            context["stage"] = "field_scope"
            if suggestion.field not in scope.get("allowed_fields", []):
                raise _Refusal("field_not_supported")
            target = scope["target"]
            raw = target.get("raw")
            context["stage"] = "input_binding"
            if not isinstance(raw, Mapping):
                raise _Refusal("frozen_input_invalid")
            context["stage"] = "proposal_binding"
            proposal = {
                **suggestion.model_dump(exclude={"citations", "classification_citations"}),
                "snapshot_id": target["snapshot_id"], "snapshot_hash": _hash(target.get("snapshot_hash")),
                "old_exists": suggestion.field in raw, "old_value": deepcopy(raw.get(suggestion.field)),
                "citations": _citations(suggestion.citations, scope["documents"], context, profile=True),
                "classification_citations": _citations(suggestion.classification_citations, scope["documents"], context,
                                                       collection="classification_citations", profile=True),
            }
            context["stage"] = "proposal_schema"
            proposals.append(CompanyProfileProposal.model_validate(proposal).model_dump(mode="json"))
        except _Refusal as exc:
            rejected.append({"suggestion_index": index, "reason": exc.reason})
            append_rejection_diagnostic(diagnostics, index=index, kind="company_profile", reason=exc.reason,
                                        context=context, suggestion=suggestion, validation=exc.validation)
        except (ValidationError, TypeError, KeyError, AttributeError) as exc:
            rejected.append({"suggestion_index": index, "reason": "resolved_proposal_invalid"})
            append_rejection_diagnostic(diagnostics, index=index, kind="company_profile", reason="resolved_proposal_invalid",
                                        context=context, suggestion=suggestion, validation=exc)
    return {"proposals": proposals, "rejected": rejected}
