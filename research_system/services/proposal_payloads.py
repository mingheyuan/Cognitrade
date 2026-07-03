from __future__ import annotations

from research_system.services.proposal_payload_builders import ProposalPayloadBuilders
from research_system.services.proposal_payload_fields import ProposalPayloadFields
from research_system.services.proposal_payload_renderers import ProposalPayloadRenderers

_BUILDERS = ProposalPayloadBuilders()
_FIELDS = ProposalPayloadFields()
_RENDERERS = ProposalPayloadRenderers()


def proposal_display_summary(payload: dict) -> str:
    return _BUILDERS.proposal_display_summary(payload)


def proposal_detail_payload(proposal, *, stored_status: str | None = None) -> dict:
    return _BUILDERS.proposal_detail_payload(proposal, stored_status=stored_status)


def proposal_summary_row(proposal) -> dict:
    return _BUILDERS.proposal_summary_row(proposal)


def proposal_detail_fields(payload: dict) -> list[tuple[str, str]]:
    return _FIELDS.proposal_detail_fields(payload)


def proposal_result_sections(payload: dict) -> list[dict]:
    return _FIELDS.proposal_result_sections(payload)


def render_proposal_row_text(payload: dict) -> str:
    return _RENDERERS.render_proposal_row_text(payload)


def render_proposal_review_history_line(row: dict) -> str:
    return _RENDERERS.render_proposal_review_history_line(row)
