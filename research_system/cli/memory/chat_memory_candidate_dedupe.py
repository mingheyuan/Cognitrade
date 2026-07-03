from __future__ import annotations

from typing import Any

from research_system.cli.memory.chat_memory_match_support import context_signature


def dedupe_relevant_candidates(
    *,
    candidates: list[tuple[float, dict[str, Any]]],
    limit: int,
) -> list[dict[str, Any]]:
    ordered: list[dict[str, Any]] = []
    seen_signatures: set[tuple[Any, ...]] = set()
    for _, context in sorted(candidates, key=lambda item: item[0], reverse=True):
        signature = context_signature(context)
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        ordered.append(context)
        if len(ordered) >= limit:
            break
    return ordered


__all__ = ["dedupe_relevant_candidates"]
