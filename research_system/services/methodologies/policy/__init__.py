from __future__ import annotations

from importlib import import_module

__all__ = [
    "DEFAULT_RETRIEVAL_POLICY",
    "MethodologyManifestService",
    "RetrievalPolicyService",
    "build_review_focus_notes",
    "normalize_retrieval_policy",
    "resolve_review_focus",
    "utc_now_iso",
]

_EXPORTS = {
    "DEFAULT_RETRIEVAL_POLICY": (
        "research_system.services.methodologies.policy.retrieval_policy_view",
        "DEFAULT_RETRIEVAL_POLICY",
    ),
    "MethodologyManifestService": (
        "research_system.services.methodologies.policy.methodology_manifest_service",
        "MethodologyManifestService",
    ),
    "RetrievalPolicyService": (
        "research_system.services.methodologies.policy.retrieval_policy_service",
        "RetrievalPolicyService",
    ),
    "build_review_focus_notes": (
        "research_system.services.methodologies.policy.review_focus_support",
        "build_review_focus_notes",
    ),
    "normalize_retrieval_policy": (
        "research_system.services.methodologies.policy.retrieval_policy_view",
        "normalize_retrieval_policy",
    ),
    "resolve_review_focus": (
        "research_system.services.methodologies.policy.review_focus_support",
        "resolve_review_focus",
    ),
    "utc_now_iso": (
        "research_system.services.methodologies.policy.methodology_manifest_service",
        "utc_now_iso",
    ),
}


def __getattr__(name: str):
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
