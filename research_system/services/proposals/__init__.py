from __future__ import annotations

from importlib import import_module

__all__ = [
    "ProposalGenerator",
    "ProposalGeneratorDimensionSupport",
    "ProposalGeneratorReviewSupport",
    "ProposalGeneratorValuationSupport",
    "ProposalPayloadBuilders",
    "ProposalPayloadFields",
    "ProposalPayloadRenderers",
    "proposal_detail_fields",
    "proposal_detail_payload",
    "proposal_display_summary",
    "proposal_result_sections",
    "proposal_summary_row",
    "render_proposal_review_history_line",
    "render_proposal_row_text",
]

_EXPORTS = {
    "ProposalGenerator": ("research_system.services.proposals.generator.proposal_generator", "ProposalGenerator"),
    "ProposalGeneratorDimensionSupport": (
        "research_system.services.proposals.generator.proposal_generator_dimension_support",
        "ProposalGeneratorDimensionSupport",
    ),
    "ProposalGeneratorReviewSupport": (
        "research_system.services.proposals.generator.proposal_generator_review_support",
        "ProposalGeneratorReviewSupport",
    ),
    "ProposalGeneratorValuationSupport": (
        "research_system.services.proposals.generator.proposal_generator_valuation_support",
        "ProposalGeneratorValuationSupport",
    ),
    "ProposalPayloadBuilders": (
        "research_system.services.proposals.payload.payload_builders",
        "ProposalPayloadBuilders",
    ),
    "ProposalPayloadFields": (
        "research_system.services.proposals.payload.payload_fields",
        "ProposalPayloadFields",
    ),
    "ProposalPayloadRenderers": (
        "research_system.services.proposals.payload.payload_renderers",
        "ProposalPayloadRenderers",
    ),
    "proposal_detail_fields": ("research_system.services.proposals.payload.payloads", "proposal_detail_fields"),
    "proposal_detail_payload": ("research_system.services.proposals.payload.payloads", "proposal_detail_payload"),
    "proposal_display_summary": ("research_system.services.proposals.payload.payloads", "proposal_display_summary"),
    "proposal_result_sections": ("research_system.services.proposals.payload.payloads", "proposal_result_sections"),
    "proposal_summary_row": ("research_system.services.proposals.payload.payloads", "proposal_summary_row"),
    "render_proposal_review_history_line": (
        "research_system.services.proposals.payload.payloads",
        "render_proposal_review_history_line",
    ),
    "render_proposal_row_text": (
        "research_system.services.proposals.payload.payloads",
        "render_proposal_row_text",
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
