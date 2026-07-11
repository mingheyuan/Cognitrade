from __future__ import annotations

import re

from research_system.schemas.artifacts import ArtifactDraft


def extract_clauses(context):
    document = context.require_artifact("contract_document")
    text = str(document.content.get("text") or "")
    clauses = [part.strip() for part in re.split(r"(?:\r?\n)+|(?<=\.)\s+", text) if part.strip()]
    return ArtifactDraft(
        artifact_type="clause_set",
        content={
            "clauses": [
                {"clause_id": f"clause-{index}", "text": clause}
                for index, clause in enumerate(clauses, start=1)
            ]
        },
        source_refs=list(document.source_refs) or [{"type": "contract", "id": document.artifact_id}],
        parent_artifact_ids=[document.artifact_id],
        confidence=0.95,
    )


def review_risks(context):
    clause_set = context.require_artifact("clause_set")
    findings = []
    for clause in clause_set.content.get("clauses") or []:
        text = str(clause.get("text") or "")
        lowered = text.lower()
        if any(term in lowered for term in ("terminate", "indemn", "liability", "penalty")):
            findings.append(
                {
                    "clause_id": clause.get("clause_id"),
                    "risk": "Material allocation of termination, indemnity, liability, or penalty risk.",
                }
            )
    return ArtifactDraft(
        artifact_type="risk_finding_set",
        content={"findings": findings},
        source_refs=list(clause_set.source_refs),
        parent_artifact_ids=[clause_set.artifact_id],
        confidence=0.85,
    )


def analyze_obligations(context):
    clause_set = context.require_artifact("clause_set")
    obligations = []
    for clause in clause_set.content.get("clauses") or []:
        text = str(clause.get("text") or "")
        if re.search(r"\b(shall|must|required to)\b", text, flags=re.IGNORECASE):
            obligations.append({"clause_id": clause.get("clause_id"), "text": text})
    return ArtifactDraft(
        artifact_type="obligation_set",
        content={"obligations": obligations},
        source_refs=list(clause_set.source_refs),
        parent_artifact_ids=[clause_set.artifact_id],
        confidence=0.9,
    )


def compose_report(context):
    risks = context.require_artifact("risk_finding_set")
    obligations = context.require_artifact("obligation_set")
    findings = list(risks.content.get("findings") or [])
    duties = list(obligations.content.get("obligations") or [])
    source_refs = list({str(ref): ref for ref in [*risks.source_refs, *obligations.source_refs]}.values())
    return ArtifactDraft(
        artifact_type="legal_review_report",
        content={
            "scope": "Contract clause risk and obligation review.",
            "findings": findings,
            "obligations": duties,
            "recommendations": [
                "Obtain qualified legal review before relying on this analysis.",
                "Resolve material risk clauses and confirm each party's obligations.",
            ],
        },
        source_refs=source_refs,
        parent_artifact_ids=[risks.artifact_id, obligations.artifact_id],
        confidence=min(risks.confidence or 0.0, obligations.confidence or 0.0),
        risk_flags=["not_legal_advice"],
    )


def verify_citations(context):
    """Gate evaluator: every material finding must retain its clause identity."""

    report = context.require_artifact("legal_review_report")
    findings = list(report.content.get("findings") or [])
    obligations = list(report.content.get("obligations") or [])
    uncited_findings = [
        finding
        for finding in findings
        if not str(finding.get("clause_id") or "").strip()
    ]
    uncited_obligations = [
        obligation
        for obligation in obligations
        if not str(obligation.get("clause_id") or "").strip()
    ]
    missing = len(uncited_findings) + len(uncited_obligations)
    if missing:
        return {
            "verdict": "fail",
            "reason": f"{missing} material legal finding or obligation has no clause citation.",
            "evidence_refs": list(report.source_refs),
        }
    return {
        "verdict": "pass",
        "reason": "Every material legal finding and obligation retains a clause citation.",
        "evidence_refs": list(report.source_refs),
        "score": 1.0,
    }
