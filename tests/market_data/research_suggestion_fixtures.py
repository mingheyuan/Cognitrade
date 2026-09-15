"""Project trusted complete test proposals onto the smaller model wire only.

Never use these helpers at the persistence boundary. Original fixture hashes,
old values, dates and offsets remain intact for direct validator regressions.
"""
from copy import deepcopy


def fact_suggestion(proposal):
    return {
        **{key: deepcopy(proposal[key]) for key in (
            "snapshot_id", "field", "new_value", "unit", "period_text", "finding",
        )},
        "citations": [{"evidence_id": row["evidence_id"], "quote": row["quote"]}
                      for row in proposal["citations"]],
    }


def profile_suggestion(proposal):
    def citations(rows):
        return [{key: row[key] for key in ("source_snapshot_id", "evidence_id", "quote")}
                for row in rows]

    return {
        "field": proposal["field"], "new_value": deepcopy(proposal["new_value"]),
        "industry_basis": proposal.get("industry_basis"),
        "classification_system": proposal.get("classification_system"),
        "citations": citations(proposal["citations"]),
        "classification_citations": citations(proposal.get("classification_citations") or []),
    }


def profile_assessments(suggestions):
    proposed = {row["field"] for row in suggestions}
    return [{"field": field, "status": "proposed" if field in proposed else "unresolved",
             "reason": "离线模拟响应提出该字段的原文修订。" if field in proposed
                       else "离线模拟响应未形成该字段修订结论，保持未决。"}
            for field in ("name", "industry", "main_business")]
