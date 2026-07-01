from __future__ import annotations

from typing import Any

from research_system.adapters.vendors.akshare_catalyst_adapter import AkShareCatalystAdapter


class AkSharePolicyAdapter:
    vendor_name = "akshare"

    def __init__(self) -> None:
        self.catalyst = AkShareCatalystAdapter()

    def get_policy_snapshot(
        self,
        *,
        theme: str,
        constituents: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        constituents = constituents or []
        policy_signals = self.catalyst.get_policy_signals(theme=theme, constituents=constituents, limit=6)
        regulatory_signals = self.catalyst.get_regulatory_signals(theme=theme, constituents=constituents, limit=6)
        digest = self.catalyst.build_catalyst_digest(
            theme=theme,
            constituents=constituents,
            catalyst_samples=[],
            theme_news=[],
            policy_signals=policy_signals,
            regulatory_signals=regulatory_signals,
        )
        if not policy_signals and not regulatory_signals:
            raise ValueError(f"no policy signals returned for theme {theme}")
        return {
            "theme": theme,
            "policy_signals": policy_signals,
            "regulatory_signals": regulatory_signals,
            "policy_digest": digest,
        }
