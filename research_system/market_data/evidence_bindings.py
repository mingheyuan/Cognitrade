"""Freeze the exact evidence uses, not the snapshot's append-only live links."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from research_system.market_data.models import (
    MarketDataEvidence,
    MarketDataSnapshotEvidence,
)


def freeze_evidence_bindings(
    repository, snapshot_ids: Iterable[str], *, known_at: datetime | None = None
) -> list[dict[str, Any]]:
    ids = list(dict.fromkeys(snapshot_ids))
    if not ids:
        return []
    rows = repository.session.execute(
        select(MarketDataSnapshotEvidence, MarketDataEvidence)
        .join(
            MarketDataEvidence,
            MarketDataEvidence.id == MarketDataSnapshotEvidence.evidence_id,
        )
        .where(MarketDataSnapshotEvidence.snapshot_id.in_(ids))
        .order_by(
            MarketDataSnapshotEvidence.snapshot_id,
            MarketDataEvidence.provider,
            MarketDataEvidence.published_at,
            MarketDataEvidence.id,
            MarketDataSnapshotEvidence.id,
        )
    ).all()
    return [
        {
            "snapshot_id": use.snapshot_id,
            "relation_id": use.id,
            "evidence_id": proof.id,
            "content_hash": proof.content_hash,
            "field_path": use.field_path,
            "purpose": use.purpose,
        }
        for use, proof in rows
        if known_at is None
        or (
            _utc(use.created_at) <= _utc(known_at)
            and _utc(proof.created_at) <= _utc(known_at)
        )
    ]


def load_bound_evidence(
    repository, snapshot_id: str, bindings: Iterable[Mapping[str, Any]]
):
    evidence = {}
    for binding in bindings:
        if binding.get("snapshot_id") != snapshot_id:
            continue
        use = repository.session.get(
            MarketDataSnapshotEvidence, str(binding.get("relation_id") or "")
        )
        proof = repository.session.get(
            MarketDataEvidence, str(binding.get("evidence_id") or "")
        )
        if (
            use is None
            or proof is None
            or use.snapshot_id != snapshot_id
            or use.evidence_id != proof.id
            or proof.content_hash != binding.get("content_hash")
            or use.field_path != binding.get("field_path")
            or use.purpose != binding.get("purpose")
        ):
            raise ValueError(
                f"frozen evidence binding changed for snapshot {snapshot_id}"
            )
        evidence[proof.id] = proof
    return list(evidence.values())


def _utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )
