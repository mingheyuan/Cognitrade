from __future__ import annotations

from research_system.services.planning_context import normalize_methodology_preferences


DEFAULT_RETRIEVAL_POLICY = {
    "policy_id": "default_retrieval_policy",
    "version": 1,
    "final_top_k": 5,
    "recency_window_days": 30,
    "rerank_enabled": False,
    "score_weights": {
        "metadata_match": 1.0,
        "semantic_similarity": 1.0,
    },
    "preferred_methodologies": [],
    "approved_proposal_ids": [],
}


def _safe_positive_int(value, *, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def _safe_bool(value) -> bool:
    return bool(value)


def _safe_float(value, *, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def normalize_retrieval_policy(policy: dict | None) -> dict:
    payload = dict(DEFAULT_RETRIEVAL_POLICY)
    source = dict(policy or {})
    payload.update(source)
    score_weights = dict(DEFAULT_RETRIEVAL_POLICY["score_weights"])
    score_weights.update(source.get("score_weights") or {})
    payload["score_weights"] = {
        "metadata_match": _safe_float(score_weights.get("metadata_match"), fallback=1.0),
        "semantic_similarity": _safe_float(score_weights.get("semantic_similarity"), fallback=1.0),
    }
    payload["preferred_methodologies"] = normalize_methodology_preferences(
        source.get("preferred_methodologies") or []
    )
    payload["approved_proposal_ids"] = [
        str(item or "").strip()
        for item in (source.get("approved_proposal_ids") or [])
        if str(item or "").strip()
    ]
    payload["final_top_k"] = _safe_positive_int(source.get("final_top_k"), fallback=5)
    payload["recency_window_days"] = _safe_positive_int(source.get("recency_window_days"), fallback=30)
    payload["rerank_enabled"] = _safe_bool(source.get("rerank_enabled"))
    payload["version"] = _safe_positive_int(source.get("version"), fallback=1)
    payload["policy_id"] = str(source.get("policy_id") or DEFAULT_RETRIEVAL_POLICY["policy_id"]).strip() or DEFAULT_RETRIEVAL_POLICY["policy_id"]
    return payload
