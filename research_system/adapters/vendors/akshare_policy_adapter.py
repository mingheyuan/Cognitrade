from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from research_system.adapters.vendors.akshare_catalyst_adapter import (
    AkShareCatalystAdapter,
)


class AkSharePolicyAdapter:
    vendor_name = "akshare"

    def __init__(self) -> None:
        self.catalyst = AkShareCatalystAdapter()

    def get_policy_snapshot(
        self,
        *,
        theme: str,
        constituents: list[dict[str, Any]] | None = None,
        as_of_date: date | None = None,
    ) -> dict[str, Any]:
        constituents = constituents or []
        policy_signals = self.catalyst.get_policy_signals(
            theme=theme, constituents=constituents, limit=6
        )
        regulatory_signals = self.catalyst.get_regulatory_signals(
            theme=theme, constituents=constituents, limit=6
        )
        if as_of_date is not None:
            policy_signals = self._bounded_rows(policy_signals, as_of_date)
            regulatory_signals = self._bounded_rows(regulatory_signals, as_of_date)
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

    @staticmethod
    def _bounded_rows(rows: list[dict[str, Any]], as_of_date: date):
        start = as_of_date - timedelta(days=365)
        bounded: list[dict[str, Any]] = []
        for row in rows:
            value = next(
                (
                    row.get(key)
                    for key in (
                        "published_at",
                        "publish_time",
                        "date",
                        "公告日期",
                        "发布日期",
                    )
                    if row.get(key) not in (None, "")
                ),
                None,
            )
            text = str(value or "").strip().replace("/", "-")
            parsed = None
            for fmt in ("%Y-%m-%d", "%Y%m%d"):
                try:
                    parsed = datetime.strptime(text[:10], fmt).date()
                    break
                except ValueError:
                    continue
            if parsed is not None and start <= parsed <= as_of_date:
                bounded.append(row)
        return bounded
