from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StockScreenView:
    review_summary_present: bool
    top_candidate_count: int
    watchlist_candidate_count: int
    enrichment_target_count: int
    enrichment_pending: bool


@dataclass(frozen=True)
class StockCheckView:
    symbol: str
    request_source_type: str
    request_query: str
    missing_snapshot_types: list[str]


@dataclass(frozen=True)
class SectorResearchView:
    request_query: str
    missing_sector_evidence: list[str]


@dataclass(frozen=True)
class ReviewThesisView:
    review_summary_present: bool
    needs_review: bool


__all__ = [
    "StockScreenView",
    "StockCheckView",
    "SectorResearchView",
    "ReviewThesisView",
]
