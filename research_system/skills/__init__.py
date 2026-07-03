from __future__ import annotations

from importlib import import_module

__all__ = [
    "capital_flow_snapshot",
    "events_snapshot",
    "fundamental_snapshot",
    "lhb_snapshot",
    "market_snapshot",
    "notice_snapshot",
    "policy_snapshot",
    "sector_snapshot",
    "sentiment_snapshot",
]

_EXPORTS = {
    "capital_flow_snapshot": "research_system.skills.capital_flow_snapshot",
    "events_snapshot": "research_system.skills.events_snapshot",
    "fundamental_snapshot": "research_system.skills.fundamental_snapshot",
    "lhb_snapshot": "research_system.skills.lhb_snapshot",
    "market_snapshot": "research_system.skills.market_snapshot",
    "notice_snapshot": "research_system.skills.notice_snapshot",
    "policy_snapshot": "research_system.skills.policy_snapshot",
    "sector_snapshot": "research_system.skills.sector_snapshot",
    "sentiment_snapshot": "research_system.skills.sentiment_snapshot",
}


def __getattr__(name: str) -> object:
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return import_module(_EXPORTS[name])


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
