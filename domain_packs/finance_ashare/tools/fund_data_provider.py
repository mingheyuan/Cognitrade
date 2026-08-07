from __future__ import annotations

from typing import Any

from domain_packs.finance_ashare.skills.fund_snapshot.skill import FundSnapshotSkill


def invoke(arguments: dict[str, Any], context) -> dict[str, Any]:
    """Collect fund evidence through the Finance Pack's governed tool boundary."""

    investment_cycle = str(arguments.get("investment_cycle") or "").strip()
    if investment_cycle not in {"short_term", "mid_term", "long_term"}:
        raise ValueError("investment_cycle is required; choose short_term, mid_term or long_term")

    return FundSnapshotSkill().run(
        query=str(arguments.get("query") or ""),
        fund_codes=[str(value) for value in arguments.get("fund_codes") or []],
        fund_category=str(arguments.get("fund_category") or "").strip() or None,
        candidate_pool_size=int(arguments.get("candidate_pool_size") or 8),
        investment_cycle=investment_cycle,
    )


__all__ = ["invoke"]
