from __future__ import annotations

from typing import Any

from domain_packs.finance_ashare.skills.fund_snapshot.skill import FundSnapshotSkill


def invoke(arguments: dict[str, Any], context) -> dict[str, Any]:
    """Collect fund evidence through the Finance Pack's governed tool boundary."""

    return FundSnapshotSkill().run(
        query=str(arguments.get("query") or ""),
        fund_codes=[str(value) for value in arguments.get("fund_codes") or []],
        fund_category=str(arguments.get("fund_category") or "").strip() or None,
        candidate_pool_size=int(arguments.get("candidate_pool_size") or 8),
        investment_cycle=str(arguments.get("investment_cycle") or "mid_term"),
    )


__all__ = ["invoke"]
