from __future__ import annotations

from datetime import date
from typing import Any

from research_system.adapters.vendors.akshare_policy_adapter import AkSharePolicyAdapter
from research_system.adapters.vendors.provider_registry import VendorProviderRegistry
from research_system.adapters.vendors.provider_execution import classify_provider_exception, failure_facts
from research_system.schemas.snapshots import SnapshotSource, VerifiedSnapshot


class PolicySnapshotSkill:
    skill_name = "policy_snapshot_skill"

    def __init__(self, provider_registry: VendorProviderRegistry | None = None) -> None:
        self.provider_registry = provider_registry
        self.adapter = AkSharePolicyAdapter()

    def run(self, *, query: str, sector: str | None = None, constituents: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        snapshot_date = date.today().isoformat()
        theme = str(sector or query or "").strip()
        provider_chain = self.provider_registry.get_provider_chain("policy", "cn_sector") if self.provider_registry else []
        provider_trace = [
            {"provider_id": item.provider_id, "source_id": item.source_id, "status": "configured"}
            for item in provider_chain
        ] or [{"provider_id": "akshare_policy", "source_id": "akshare", "status": "configured"}]
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
                facts={**failure_facts("no_data"), "missing_scope": "sector"},
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
                source_confidence=(
                    self.provider_registry.source_confidence(provider_chain[0])
                    if self.provider_registry and provider_chain
                    else "high"
                ),
                source_trace=provider_trace,
            ).model_dump(mode="json")
        except Exception as exc:
            failure = classify_provider_exception(exc)
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
                facts=failure_facts(failure),
                source=primary_source,
                primary_source=primary_source,
                source_trace=provider_trace + [{"provider_id": "akshare_policy", "status": f"error:{exc.__class__.__name__}"}],
                degraded=True,
                degrade_reason=failure,
            ).model_dump(mode="json")
