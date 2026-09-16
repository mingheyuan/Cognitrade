"""Unverified report-source leads, never evidence or a grant to fetch them."""
from hashlib import sha256
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from research_system.market_data.research_archive import RESEARCH_SOURCES, _check_document_url, _check_url


class ResearchDocumentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dimension: str
    url: str = Field(min_length=1, max_length=2048)

    @field_validator("dimension")
    @classmethod
    def supported_dimension(cls, value):
        if value not in RESEARCH_SOURCES:
            raise ValueError("unsupported research dimension")
        return value

    @model_validator(mode="after")
    def public_source(self):
        # Reuse the existing transport allowlist. Do not retain signed URLs,
        # embedded credentials or control characters in a report repair plan.
        if any(ord(char) < 32 for char in self.url):
            raise ValueError("unsafe research URL")
        parsed = urlsplit(self.url)
        sensitive = ("token", "secret", "password", "signature", "credential", "api_key", "apikey", "authorization")
        if any(any(part in key.lower() for part in sensitive) for key, _ in parse_qsl(parsed.query)):
            raise ValueError("credential-bearing research URL")
        _check_url(self.url, self.dimension)
        # Reject before removing fragments: #/apiDoc and similar navigation
        # routes must not turn into a seemingly new, repeatedly retried root.
        _check_document_url(self.url)
        # Fragments are client-side anchors, not different fetched documents.
        self.url = urlunsplit(parsed._replace(fragment=""))
        return self


def pending_source_requests(group, evidence_bundle):
    """Keep safe public leads separately from native findings and diagnostics."""
    rows = {}
    for dimension in group.dimensions:
        if dimension.dimension_id not in evidence_bundle:
            continue
        allowed = set(evidence_bundle[dimension.dimension_id]["allowed_urls"])
        for evidence in dimension.evidence:
            if not evidence.url or evidence.url in allowed:
                continue
            try:
                request = ResearchDocumentRequest(dimension=dimension.dimension_id, url=evidence.url)
            except ValueError:
                # Keep the ordinary finite failure diagnostic; never persist
                # arbitrary model text or secret-bearing unsupported URLs here.
                continue
            row = {**request.model_dump(), "status": "needs_verification",
                   "finding_hash": sha256(evidence.finding.encode()).hexdigest()}
            rows[(row["dimension"], row["url"], row["finding_hash"])] = row
    return list(rows.values())
