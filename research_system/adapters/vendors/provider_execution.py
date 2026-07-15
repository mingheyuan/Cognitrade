from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal

from research_system.adapters.vendors.provider_registry import ProviderSpec


DataAvailability = Literal[
    "available",
    "no_data",
    "provider_unavailable",
    "rate_limited",
    "parse_failed",
]


@dataclass(frozen=True)
class ProviderExecution:
    facts: dict[str, Any]
    provider: ProviderSpec | None
    chain_position: int | None
    trace: list[dict[str, Any]]
    availability: DataAvailability

    @property
    def fallback_applied(self) -> bool:
        return self.chain_position is not None and self.chain_position > 0


def execute_provider_chain(
    *,
    providers: list[ProviderSpec],
    loaders: dict[str, Callable[[], dict[str, Any]]],
) -> ProviderExecution:
    """Execute configured providers in order and stop at the first usable result."""

    trace: list[dict[str, Any]] = []
    last_status: DataAvailability = "provider_unavailable"
    for position, provider in enumerate(providers):
        loader = loaders.get(provider.provider_id)
        if loader is None:
            trace.append(_trace(provider, position, "provider_unavailable", error_type="adapter_not_installed"))
            last_status = "provider_unavailable"
            continue
        try:
            facts = dict(loader() or {})
        except Exception as exc:  # provider boundaries must never leak raw transport errors
            last_status = classify_provider_exception(exc)
            trace.append(_trace(provider, position, last_status, error_type=exc.__class__.__name__))
            continue
        if not _has_usable_data(facts):
            last_status = "no_data"
            trace.append(_trace(provider, position, "no_data"))
            continue
        facts.setdefault("availability", "available")
        trace.append(_trace(provider, position, "available"))
        return ProviderExecution(
            facts=facts,
            provider=provider,
            chain_position=position,
            trace=trace,
            availability="available",
        )

    return ProviderExecution(
        facts=failure_facts(last_status),
        provider=None,
        chain_position=None,
        trace=trace,
        availability=last_status,
    )


def classify_provider_exception(exc: Exception) -> DataAvailability:
    name = exc.__class__.__name__.lower()
    message = str(exc).lower()
    if any(token in message for token in ("no data", "empty result", "not found", "no lhb dates")):
        return "no_data"
    if any(token in message for token in ("429", "rate limit", "too many requests", "frequency limit")):
        return "rate_limited"
    if any(token in name for token in ("jsondecode", "parser", "decode")) or any(
        token in message for token in ("parse", "invalid json", "unexpected field", "schema")
    ):
        return "parse_failed"
    return "provider_unavailable"


def failure_facts(status: DataAvailability) -> dict[str, Any]:
    messages = {
        "no_data": "数据源在当前查询范围内没有记录",
        "provider_unavailable": "数据源暂时不可用，请稍后重试",
        "rate_limited": "数据源请求频率受限，请稍后重试",
        "parse_failed": "数据源返回格式暂时无法解析",
        "available": "数据可用",
    }
    return {
        "availability": status,
        "data_gap": [status] if status != "available" else [],
        "message": messages[status],
    }


def _has_usable_data(facts: dict[str, Any]) -> bool:
    status = str(facts.get("availability") or "").strip().lower()
    if status in {"no_data", "provider_unavailable", "rate_limited", "parse_failed", "data_gap", "degraded"}:
        return False
    return any(
        value not in (None, "", [], {})
        for key, value in facts.items()
        if key not in {"availability", "message", "note", "data_gap"}
    )


def _trace(
    provider: ProviderSpec,
    position: int,
    status: DataAvailability,
    *,
    error_type: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "provider_id": provider.provider_id,
        "source_id": provider.source_id,
        "chain_position": position,
        "status": status,
    }
    if error_type:
        payload["error_type"] = error_type
    return payload
