from __future__ import annotations

from datetime import date, timedelta
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
        # stock_news_main_cx exposes only today's feed and has no historical
        # query parameter. A bounded request must not fetch that feed and then
        # masquerade its filtered remainder as a target-date policy archive.
        policy_signals = (
            self.catalyst.get_policy_signals(
                theme=theme, constituents=constituents, limit=6
            )
            if as_of_date is None
            else []
        )
        diagnostics: list[dict[str, Any]] = []
        regulatory_signals = self.catalyst.get_regulatory_signals(
            theme=theme, constituents=constituents, limit=6,
            **({"as_of_date": as_of_date, "diagnostics": diagnostics} if as_of_date is not None else {}),
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
        if as_of_date is None and not policy_signals and not regulatory_signals:
            raise ValueError(f"no policy signals returned for theme {theme}")
        has_records = bool(policy_signals or regulatory_signals)
        failed = any(row["status"] == "provider_unavailable" for row in diagnostics)
        return {
            "theme": theme,
            "policy_signals": policy_signals,
            "regulatory_signals": regulatory_signals,
            "policy_digest": digest,
            **({
                "policy_query_status": "partial" if has_records and failed else "available" if has_records else "failed" if failed else "empty",
                "provider_attempts": diagnostics,
                "query_window": {"start": (as_of_date - timedelta(days=30)).isoformat(), "end": as_of_date.isoformat()},
                "availability": "available" if has_records else "provider_unavailable" if failed else "no_data",
            } if as_of_date is not None else {}),
        }

    @staticmethod
    def _bounded_rows(rows: list[dict[str, Any]], as_of_date: date):
        start = as_of_date - timedelta(days=30)
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
                        "公告时间",
                        "发布日期",
                    )
                    if row.get(key) not in (None, "")
                ),
                None,
            )
            text = str(value or "").strip().replace("/", "-")
            parsed = None
            try:
                parsed = date.fromisoformat(text[:10])
            except ValueError:
                pass
            if parsed is not None and start <= parsed <= as_of_date:
                bounded.append(row)
        return bounded
