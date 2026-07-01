from __future__ import annotations

from datetime import date
from typing import Any

from research_system.adapters.vendors.akshare_policy_adapter import AkSharePolicyAdapter
from research_system.adapters.vendors.provider_registry import VendorProviderRegistry
from research_system.schemas.snapshots import SnapshotSource, VerifiedSnapshot


class PolicySnapshotSkill:
    skill_name = "policy_snapshot_skill"

    def __init__(self, provider_registry: VendorProviderRegistry | None = None) -> None:
        self.provider_registry = provider_registry
        self.adapter = AkSharePolicyAdapter()

    def run(self, *, query: str, sector: str | None = None, constituents: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        snapshot_date = date.today().isoformat()
        theme = str(sector or query or "").strip()
        provider_trace = [{"provider_id": "akshare_policy", "status": "configured"}]
        if not theme:
            primary_source = SnapshotSource(
                skill=self.skill_name,
                vendor=self.adapter.vendor_name,
                from_cache=False,
                provider_id="akshare_policy",
                source_id="akshare",
                chain_position=0,
                dim="policy",
            )
            return VerifiedSnapshot(
                snapshot_type="policy",
                query=query,
                snapshot_date=snapshot_date,
                facts={"availability": "degraded", "note": "sector_missing"},
                source=primary_source,
                primary_source=primary_source,
                missing_fields=["sector"],
                source_trace=provider_trace,
                degraded=True,
                degrade_reason="sector_missing",
            ).model_dump(mode="json")
        try:
            facts = self.adapter.get_policy_snapshot(theme=theme, constituents=constituents)
            primary_source = SnapshotSource(
                skill=self.skill_name,
                vendor=self.adapter.vendor_name,
                from_cache=False,
                provider_id="akshare_policy",
                source_id="akshare",
                chain_position=0,
                dim="policy",
            )
            return VerifiedSnapshot(
                snapshot_type="policy",
                query=query,
                snapshot_date=snapshot_date,
                sector=theme,
                theme=theme,
                facts=facts,
                source=primary_source,
                primary_source=primary_source,
                source_confidence="high",
                source_trace=provider_trace,
            ).model_dump(mode="json")
        except Exception as exc:
            primary_source = SnapshotSource(
                skill=self.skill_name,
                vendor=self.adapter.vendor_name,
                from_cache=False,
                provider_id="akshare_policy",
                source_id="akshare",
                chain_position=0,
                dim="policy",
            )
            return VerifiedSnapshot(
                snapshot_type="policy",
                query=query,
                snapshot_date=snapshot_date,
                sector=theme or None,
                theme=theme or None,
                facts={"availability": "degraded", "note": str(exc)},
                source=primary_source,
                primary_source=primary_source,
                source_trace=provider_trace + [{"provider_id": "akshare_policy", "status": f"error:{exc.__class__.__name__}"}],
                degraded=True,
                degrade_reason="vendor_failure",
            ).model_dump(mode="json")
