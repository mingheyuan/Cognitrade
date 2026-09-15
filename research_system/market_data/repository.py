from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import Select, and_, func, or_, select, update
from sqlalchemy.orm import Session

from research_system.market_data.contracts import ConflictStatus, require_aware_utc
from research_system.market_data.models import (
    MarketDataBundle,
    MarketDataBundleItem,
    MarketDataCollectionJob,
    MarketDataConflict,
    MarketDataEvidence,
    MarketDataObservation,
    MarketDataSeriesPoint,
    MarketDataSnapshot,
    MarketDataSnapshotEvidence,
    MarketDataSnapshotSeriesPoint,
    ReportDataManifest,
    ReportDataManifestDependency,
    ReportDataManifestItem,
)


class MarketDataRepository:
    """Transactional persistence/query boundary for PIT market data.

    The repository deliberately owns no provider or network code.  The caller
    controls the Session transaction so one acquisition result can atomically
    persist evidence, snapshot, observations and an optional Bundle publication.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def flush(self) -> None:
        self.session.flush()

    # Evidence and immutable snapshots -------------------------------------------------
    def get_evidence(self, evidence_id: str) -> MarketDataEvidence | None:
        return self.session.get(MarketDataEvidence, evidence_id)

    def find_evidence_by_key(self, evidence_key: str) -> MarketDataEvidence | None:
        return self.session.scalar(
            select(MarketDataEvidence).where(MarketDataEvidence.evidence_key == evidence_key)
        )

    def add_evidence(self, evidence: MarketDataEvidence) -> MarketDataEvidence:
        self.session.add(evidence)
        self.session.flush()
        return evidence

    def get_snapshot(self, snapshot_id: str) -> MarketDataSnapshot | None:
        return self.session.get(MarketDataSnapshot, snapshot_id)

    def require_snapshot(self, snapshot_id: str) -> MarketDataSnapshot:
        snapshot = self.get_snapshot(snapshot_id)
        if snapshot is None:
            raise KeyError(f"market-data snapshot not found: {snapshot_id}")
        return snapshot

    def lock_snapshot_for_update(self, snapshot_id: str) -> MarketDataSnapshot:
        """Lock and refresh one Snapshot before revocation or publication.

        PostgreSQL provides row-level ``FOR UPDATE`` locks. SQLite ignores that
        clause, so a no-op UPDATE is used there to acquire its transaction-wide
        writer lock before reading the row. Both paths keep the lock until the
        caller's transaction ends.
        """

        if self.session.get_bind().dialect.name == "sqlite":
            self.session.execute(
                update(MarketDataSnapshot)
                .where(MarketDataSnapshot.id == snapshot_id)
                .values(revoked_at=MarketDataSnapshot.revoked_at)
                .execution_options(synchronize_session=False)
            )
        snapshot = self.session.scalar(
            select(MarketDataSnapshot)
            .where(MarketDataSnapshot.id == snapshot_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if snapshot is None:
            raise KeyError(f"market-data snapshot not found: {snapshot_id}")
        return snapshot

    def lock_snapshot_scope_for_publication(
        self,
        *,
        symbol: str,
        market: str,
        as_of_date: date,
        contract_version: str,
    ) -> None:
        """Serialize Bundle publication for one immutable revision scope."""

        filters = (
            MarketDataSnapshot.symbol == symbol.strip().upper(),
            MarketDataSnapshot.market == market.strip().upper(),
            MarketDataSnapshot.as_of_date == as_of_date,
            MarketDataSnapshot.contract_version == contract_version,
        )
        if self.session.get_bind().dialect.name == "sqlite":
            first_snapshot_id = (
                select(MarketDataSnapshot.id)
                .where(*filters)
                .order_by(MarketDataSnapshot.id)
                .limit(1)
                .scalar_subquery()
            )
            self.session.execute(
                update(MarketDataSnapshot)
                .where(MarketDataSnapshot.id == first_snapshot_id)
                .values(revoked_at=MarketDataSnapshot.revoked_at)
                .execution_options(synchronize_session=False)
            )
            return
        # Lock in a deterministic order so concurrent publishers and revokers
        # cannot take the same Snapshot rows in opposite order.
        list(
            self.session.scalars(
                select(MarketDataSnapshot)
                .where(*filters)
                .order_by(MarketDataSnapshot.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )

    def lock_snapshots_for_update(
        self, snapshot_ids: Iterable[str]
    ) -> list[MarketDataSnapshot]:
        """Lock selected candidate rows and return freshly loaded state."""

        ids = sorted({str(snapshot_id) for snapshot_id in snapshot_ids if snapshot_id})
        if not ids:
            return []
        if self.session.get_bind().dialect.name == "sqlite":
            # Publication already owns SQLite's writer lock after locking the
            # scope. Keep this fallback for direct callers of this helper.
            self.session.execute(
                update(MarketDataSnapshot)
                .where(MarketDataSnapshot.id == ids[0])
                .values(revoked_at=MarketDataSnapshot.revoked_at)
                .execution_options(synchronize_session=False)
            )
        return list(
            self.session.scalars(
                select(MarketDataSnapshot)
                .where(MarketDataSnapshot.id.in_(ids))
                .order_by(MarketDataSnapshot.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )

    def find_snapshot_by_hash(self, content_hash: str) -> MarketDataSnapshot | None:
        return self.session.scalar(
            select(MarketDataSnapshot).where(MarketDataSnapshot.content_hash == content_hash)
        )

    def add_snapshot(self, snapshot: MarketDataSnapshot) -> MarketDataSnapshot:
        self.session.add(snapshot)
        self.session.flush()
        return snapshot

    def list_snapshots(
        self,
        *,
        symbol: str | None = None,
        market: str | None = None,
        dimension: str | None = None,
        as_of_date: date | None = None,
        cutoff_at: datetime | None = None,
        status: str | None = None,
        include_revoked: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[MarketDataSnapshot]:
        statement: Select[tuple[MarketDataSnapshot]] = select(MarketDataSnapshot)
        if symbol:
            statement = statement.where(MarketDataSnapshot.symbol == symbol.strip().upper())
        if market:
            statement = statement.where(MarketDataSnapshot.market == market.strip().upper())
        if dimension:
            statement = statement.where(MarketDataSnapshot.dimension == dimension)
        if as_of_date:
            statement = statement.where(MarketDataSnapshot.as_of_date == as_of_date)
        if cutoff_at is not None:
            cutoff = require_aware_utc(cutoff_at, field_name="cutoff_at")
            statement = statement.where(MarketDataSnapshot.published_at <= cutoff)
        if status:
            statement = statement.where(MarketDataSnapshot.status == status)
        if not include_revoked:
            statement = statement.where(MarketDataSnapshot.revoked_at.is_(None))
        statement = statement.order_by(
            MarketDataSnapshot.published_at.desc(), MarketDataSnapshot.created_at.desc()
        ).offset(max(0, offset)).limit(max(1, min(limit, 1000)))
        return list(self.session.scalars(statement))

    def add_snapshot_evidence(
        self,
        *,
        snapshot_id: str,
        evidence_id: str,
        field_path: str = "$",
        purpose: str = "supports",
    ) -> MarketDataSnapshotEvidence:
        existing = self.session.scalar(
            select(MarketDataSnapshotEvidence).where(
                MarketDataSnapshotEvidence.snapshot_id == snapshot_id,
                MarketDataSnapshotEvidence.evidence_id == evidence_id,
                MarketDataSnapshotEvidence.field_path == field_path,
                MarketDataSnapshotEvidence.purpose == purpose,
            )
        )
        if existing is not None:
            return existing
        association = MarketDataSnapshotEvidence(
            snapshot_id=snapshot_id,
            evidence_id=evidence_id,
            field_path=field_path,
            purpose=purpose,
        )
        self.session.add(association)
        self.session.flush()
        return association

    def list_snapshot_evidence(self, snapshot_id: str) -> list[MarketDataEvidence]:
        statement = (
            select(MarketDataEvidence).distinct()
            .join(
                MarketDataSnapshotEvidence,
                MarketDataSnapshotEvidence.evidence_id == MarketDataEvidence.id,
            )
            .where(MarketDataSnapshotEvidence.snapshot_id == snapshot_id)
            .order_by(MarketDataEvidence.provider, MarketDataEvidence.published_at)
        )
        return list(self.session.scalars(statement))

    def add_observation(self, observation: MarketDataObservation) -> MarketDataObservation:
        self.session.add(observation)
        self.session.flush()
        return observation

    def list_observations(self, snapshot_id: str) -> list[MarketDataObservation]:
        return list(
            self.session.scalars(
                select(MarketDataObservation)
                .where(MarketDataObservation.snapshot_id == snapshot_id)
                .order_by(MarketDataObservation.observed_at)
            )
        )

    def revoke_snapshot(
        self,
        snapshot_id: str,
        *,
        reason: str,
        revoked_by: str,
        superseded_by_id: str | None = None,
        revoked_at: datetime | None = None,
    ) -> MarketDataSnapshot:
        snapshot = self.lock_snapshot_for_update(snapshot_id)
        if snapshot.revoked_at is not None:
            return snapshot
        if not reason.strip():
            raise ValueError("snapshot revocation requires a reason")
        if superseded_by_id:
            replacement = self.require_snapshot(superseded_by_id)
            if replacement.id == snapshot.id:
                raise ValueError("a snapshot cannot supersede itself")
            if (replacement.symbol, replacement.market, replacement.dimension) != (
                snapshot.symbol, snapshot.market, snapshot.dimension
            ):
                raise ValueError("superseding snapshot must have the same symbol/market/dimension")
        snapshot.revoked_at = require_aware_utc(
            revoked_at or datetime.now(timezone.utc), field_name="revoked_at"
        )
        snapshot.revoke_reason = reason.strip()
        snapshot.revoked_by = revoked_by.strip()
        snapshot.superseded_by_id = superseded_by_id
        affected_bundles = list(
            self.session.scalars(
                select(MarketDataBundle)
                .join(
                    MarketDataBundleItem,
                    MarketDataBundleItem.bundle_id == MarketDataBundle.id,
                )
                .where(MarketDataBundleItem.snapshot_id == snapshot.id)
            )
        )
        invalidation_reason = f"snapshot {snapshot.id} revoked: {reason.strip()}"
        for bundle in affected_bundles:
            self.invalidate_bundle(
                bundle.id,
                reason=invalidation_reason,
                invalidated_at=snapshot.revoked_at,
            )
        self.session.flush()
        return snapshot

    # Incremental series ---------------------------------------------------------------
    def find_series_point_by_hash(self, content_hash: str) -> MarketDataSeriesPoint | None:
        return self.session.scalar(
            select(MarketDataSeriesPoint).where(MarketDataSeriesPoint.content_hash == content_hash)
        )

    def add_series_point(self, point: MarketDataSeriesPoint) -> MarketDataSeriesPoint:
        self.session.add(point)
        self.session.flush()
        return point

    def link_snapshot_series_point(
        self, *, snapshot_id: str, series_point_id: str
    ) -> MarketDataSnapshotSeriesPoint:
        existing = self.session.scalar(
            select(MarketDataSnapshotSeriesPoint).where(
                MarketDataSnapshotSeriesPoint.snapshot_id == snapshot_id,
                MarketDataSnapshotSeriesPoint.series_point_id == series_point_id,
            )
        )
        if existing is not None:
            return existing
        link = MarketDataSnapshotSeriesPoint(
            snapshot_id=snapshot_id, series_point_id=series_point_id
        )
        self.session.add(link)
        self.session.flush()
        return link

    def list_snapshot_series_points(self, snapshot_id: str) -> list[MarketDataSeriesPoint]:
        return list(
            self.session.scalars(
                select(MarketDataSeriesPoint)
                .join(
                    MarketDataSnapshotSeriesPoint,
                    MarketDataSnapshotSeriesPoint.series_point_id == MarketDataSeriesPoint.id,
                )
                .where(
                    MarketDataSnapshotSeriesPoint.snapshot_id == snapshot_id,
                    MarketDataSeriesPoint.revoked_at.is_(None),
                )
                .order_by(MarketDataSeriesPoint.series_key, MarketDataSeriesPoint.point_at)
            )
        )

    # Conflicts ------------------------------------------------------------------------
    def add_conflict(self, conflict: MarketDataConflict) -> MarketDataConflict:
        self.session.add(conflict)
        self.session.flush()
        return conflict

    def list_open_conflicts(
        self,
        *,
        symbol: str,
        market: str,
        dimension: str,
        as_of_date: date,
        snapshot_id: str | None = None,
    ) -> list[MarketDataConflict]:
        scope = and_(
            MarketDataConflict.symbol == symbol.strip().upper(),
            MarketDataConflict.market == market.strip().upper(),
            MarketDataConflict.dimension == dimension,
            MarketDataConflict.as_of_date == as_of_date,
        )
        if snapshot_id:
            scope = or_(MarketDataConflict.snapshot_id == snapshot_id, scope)
        return list(
            self.session.scalars(
                select(MarketDataConflict)
                .where(MarketDataConflict.status == ConflictStatus.OPEN.value, scope)
                .order_by(MarketDataConflict.created_at)
            )
        )

    def resolve_conflict(
        self,
        conflict_id: str,
        *,
        resolution: dict[str, Any],
        resolved_by: str,
        automatic: bool = False,
        resolved_at: datetime | None = None,
    ) -> MarketDataConflict:
        conflict = self.session.get(MarketDataConflict, conflict_id)
        if conflict is None:
            raise KeyError(f"market-data conflict not found: {conflict_id}")
        if conflict.status != ConflictStatus.OPEN.value:
            return conflict
        conflict.status = (
            ConflictStatus.AUTO_RESOLVED.value
            if automatic
            else ConflictStatus.MANUALLY_RESOLVED.value
        )
        conflict.resolution_json = dict(resolution)
        conflict.resolved_by = resolved_by
        conflict.resolved_at = require_aware_utc(
            resolved_at or datetime.now(timezone.utc), field_name="resolved_at"
        )
        self.session.flush()
        return conflict

    def resolve_conflicts_atomically(
        self,
        conflict_ids: list[str],
        *,
        resolution: dict[str, Any],
        resolved_by: str,
        resolved_at: datetime | None = None,
    ) -> list[MarketDataConflict]:
        """Claim one whole-fact decision with a status compare-and-set.

        ``FOR UPDATE`` serializes PostgreSQL callers.  This additional CAS is
        required for SQLite (which ignores ``FOR UPDATE``) and protects against
        stale sessions or another process that committed between the read and
        write.  Callers must perform this before revoking any candidate.
        """

        normalized_ids = sorted({str(item or "").strip() for item in conflict_ids})
        if not normalized_ids:
            raise ValueError("atomic conflict resolution requires conflict IDs")
        decided_at = require_aware_utc(
            resolved_at or datetime.now(timezone.utc), field_name="resolved_at"
        )
        result = self.session.execute(
            update(MarketDataConflict)
            .where(
                MarketDataConflict.id.in_(normalized_ids),
                MarketDataConflict.status == ConflictStatus.OPEN.value,
            )
            .values(
                status=ConflictStatus.MANUALLY_RESOLVED.value,
                resolution_json=dict(resolution),
                resolved_by=resolved_by,
                resolved_at=decided_at,
            )
            .execution_options(synchronize_session=False)
        )
        if int(result.rowcount or 0) != len(normalized_ids):
            raise ValueError("conflict decision lost a concurrent compare-and-set")
        return list(
            self.session.scalars(
                select(MarketDataConflict)
                .where(MarketDataConflict.id.in_(normalized_ids))
                .order_by(MarketDataConflict.id)
                .execution_options(populate_existing=True)
            )
        )

    # Collection jobs (read primitives; orchestration lives elsewhere) -----------------
    def get_collection_job(self, job_id: str) -> MarketDataCollectionJob | None:
        return self.session.get(MarketDataCollectionJob, job_id)

    def list_collection_jobs(
        self,
        *,
        tenant_id: str | None = None,
        user_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[MarketDataCollectionJob]:
        statement = select(MarketDataCollectionJob)
        if tenant_id:
            statement = statement.where(MarketDataCollectionJob.tenant_id == tenant_id)
        if user_id:
            statement = statement.where(MarketDataCollectionJob.user_id == user_id)
        if status:
            statement = statement.where(MarketDataCollectionJob.status == status)
        statement = statement.order_by(MarketDataCollectionJob.created_at.desc()).limit(
            max(1, min(limit, 1000))
        )
        return list(self.session.scalars(statement))

    # Bundles --------------------------------------------------------------------------
    def get_bundle(self, bundle_id: str) -> MarketDataBundle | None:
        return self.session.get(MarketDataBundle, bundle_id)

    def require_bundle(self, bundle_id: str) -> MarketDataBundle:
        bundle = self.get_bundle(bundle_id)
        if bundle is None:
            raise KeyError(f"market-data bundle not found: {bundle_id}")
        return bundle

    def find_bundle_by_hash(self, manifest_hash: str) -> MarketDataBundle | None:
        return self.session.scalar(
            select(MarketDataBundle).where(MarketDataBundle.manifest_hash == manifest_hash)
        )

    def list_bundles(
        self,
        *,
        symbol: str | None = None,
        market: str | None = None,
        as_of_date: date | None = None,
        include_invalidated: bool = False,
        limit: int = 100,
    ) -> list[MarketDataBundle]:
        statement = select(MarketDataBundle)
        if symbol:
            statement = statement.where(MarketDataBundle.symbol == symbol.strip().upper())
        if market:
            statement = statement.where(MarketDataBundle.market == market.strip().upper())
        if as_of_date:
            statement = statement.where(MarketDataBundle.as_of_date == as_of_date)
        if not include_invalidated:
            statement = statement.where(MarketDataBundle.invalidated_at.is_(None))
        statement = statement.order_by(
            MarketDataBundle.as_of_date.desc(), MarketDataBundle.revision.desc()
        ).limit(max(1, min(limit, 1000)))
        return list(self.session.scalars(statement))

    def latest_bundle(
        self, *, symbol: str, market: str, as_of_date: date, contract_version: str
    ) -> MarketDataBundle | None:
        return self.session.scalar(
            select(MarketDataBundle)
            .where(
                MarketDataBundle.symbol == symbol.strip().upper(),
                MarketDataBundle.market == market.strip().upper(),
                MarketDataBundle.as_of_date == as_of_date,
                MarketDataBundle.contract_version == contract_version,
            )
            .order_by(MarketDataBundle.revision.desc())
            .limit(1)
        )

    def next_bundle_revision(
        self, *, symbol: str, market: str, as_of_date: date, contract_version: str
    ) -> int:
        current = self.session.scalar(
            select(func.max(MarketDataBundle.revision)).where(
                MarketDataBundle.symbol == symbol.strip().upper(),
                MarketDataBundle.market == market.strip().upper(),
                MarketDataBundle.as_of_date == as_of_date,
                MarketDataBundle.contract_version == contract_version,
            )
        )
        return int(current or 0) + 1

    def add_bundle(self, bundle: MarketDataBundle) -> MarketDataBundle:
        self.session.add(bundle)
        self.session.flush()
        return bundle

    def invalidate_bundle(
        self,
        bundle_id: str,
        *,
        reason: str,
        invalidated_at: datetime | None = None,
    ) -> MarketDataBundle:
        bundle = self.require_bundle(bundle_id)
        if bundle.invalidated_at is not None:
            return bundle
        if not reason.strip():
            raise ValueError("bundle invalidation requires a reason")
        timestamp = require_aware_utc(
            invalidated_at or datetime.now(timezone.utc), field_name="invalidated_at"
        )
        bundle.status = "invalidated"
        bundle.invalidated_at = timestamp
        bundle.invalidation_reason = reason.strip()
        report_manifests = list(
            self.session.scalars(
                select(ReportDataManifest)
                .join(
                    ReportDataManifestItem,
                    ReportDataManifestItem.manifest_id == ReportDataManifest.id,
                )
                .where(ReportDataManifestItem.bundle_id == bundle.id)
            )
        )
        dependency_manifests = list(
            self.session.scalars(
                select(ReportDataManifest)
                .join(
                    ReportDataManifestDependency,
                    ReportDataManifestDependency.manifest_id == ReportDataManifest.id,
                )
                .where(ReportDataManifestDependency.bundle_id == bundle.id)
            )
        )
        by_id = {manifest.id: manifest for manifest in [*report_manifests, *dependency_manifests]}
        for manifest in by_id.values():
            if manifest.invalidated_at is None:
                manifest.status = "invalidated"
                manifest.invalidated_at = timestamp
                manifest.invalidation_reason = reason.strip()
        self.session.flush()
        return bundle

    def add_bundle_item(self, item: MarketDataBundleItem) -> MarketDataBundleItem:
        self.session.add(item)
        self.session.flush()
        return item

    def list_bundle_items(self, bundle_id: str) -> list[MarketDataBundleItem]:
        return list(
            self.session.scalars(
                select(MarketDataBundleItem)
                .where(MarketDataBundleItem.bundle_id == bundle_id)
                .order_by(MarketDataBundleItem.ordinal)
            )
        )

    # Report manifests -----------------------------------------------------------------
    def get_report_manifest(self, manifest_id: str) -> ReportDataManifest | None:
        return self.session.get(ReportDataManifest, manifest_id)

    def find_report_manifest_by_hash(
        self, *, tenant_id: str, user_id: str, manifest_hash: str
    ) -> ReportDataManifest | None:
        return self.session.scalar(
            select(ReportDataManifest).where(
                ReportDataManifest.tenant_id == tenant_id,
                ReportDataManifest.user_id == user_id,
                ReportDataManifest.manifest_hash == manifest_hash,
            )
        )

    def add_report_manifest(self, manifest: ReportDataManifest) -> ReportDataManifest:
        self.session.add(manifest)
        self.session.flush()
        return manifest

    def add_report_manifest_item(self, item: ReportDataManifestItem) -> ReportDataManifestItem:
        self.session.add(item)
        self.session.flush()
        return item

    def list_report_manifest_items(self, manifest_id: str) -> list[ReportDataManifestItem]:
        return list(
            self.session.scalars(
                select(ReportDataManifestItem)
                .where(ReportDataManifestItem.manifest_id == manifest_id)
                .order_by(ReportDataManifestItem.ordinal)
            )
        )

    def add_report_manifest_dependency(
        self, dependency: ReportDataManifestDependency
    ) -> ReportDataManifestDependency:
        self.session.add(dependency)
        self.session.flush()
        return dependency

    def list_report_manifest_dependencies(
        self, manifest_id: str
    ) -> list[ReportDataManifestDependency]:
        return list(
            self.session.scalars(
                select(ReportDataManifestDependency)
                .where(ReportDataManifestDependency.manifest_id == manifest_id)
                .order_by(ReportDataManifestDependency.ordinal)
            )
        )


__all__ = ["MarketDataRepository"]
