"""Explicit non-authoritative views of pinned UZI model/demo output."""

import json
from typing import Any


PROMPT_EVIDENCE_VIEW_VERSION = "uzi-prompt-evidence-v2-index-preserving"
_OMISSION_KEY = "__uzi_prompt_omitted__"
DEFAULT_PROMPT_VIEW_MAX_CHARACTERS = 500_000


class UziInputBudgetExceeded(RuntimeError):
    """Host input projection exceeded its bound; no model was called."""

    code = "input_budget_exceeded"


def is_prompt_omission(value: Any) -> bool:
    return isinstance(value, dict) and value == {_OMISSION_KEY: True}


def compact_evidence_view(
    value: Any, *, max_sequence_items: int | None = 30, max_text_chars: int = 1200,
    path: str = "", truncations: list[dict[str, Any]] | None = None,
    preserve_sequences_at: frozenset[str] = frozenset(),
    excluded_keys: frozenset[str] = frozenset(),
    indexed_sequences: bool = False,
    max_characters: int = DEFAULT_PROMPT_VIEW_MAX_CHARACTERS,
) -> Any:
    """Bound a prompt view without converting existing nested facts to null.

    Mapping keys and primitive types survive every nesting level. Large arrays
    retain both ends at their original indexes; omitted positions have an
    explicit non-factual marker, excluded by the grounding path enumerator.
    Text prefixes and sampled index ranges are disclosed outside the facts.
    The immutable source is never mutated, and oversized views fail closed.
    """
    if max_sequence_items is not None and max_sequence_items < 2:
        raise ValueError("max_sequence_items must be at least 2")
    if max_text_chars <= 0 or max_characters <= 0:
        raise ValueError("prompt view limits must be positive")
    notices = truncations if truncations is not None else []
    used = 0

    def account(count: int) -> None:
        nonlocal used
        used += count
        if used > max_characters:
            raise UziInputBudgetExceeded(f"input_budget_exceeded: prompt view exceeds {max_characters} JSON characters")

    def project(item: Any, current: str, depth: int) -> Any:
        if depth > 64:
            raise UziInputBudgetExceeded("input_budget_exceeded: prompt evidence nesting exceeds 64")
        if isinstance(item, dict):
            account(2)
            result = {}
            for key, child in item.items():
                child_path = f"{current}.{key}" if current else str(key)
                if key in excluded_keys:
                    notices.append({"path": child_path, "reason": "duplicate_transport_excluded"})
                    continue
                account(len(json.dumps(str(key), ensure_ascii=False)) + 2)
                result[str(key)] = project(child, child_path, depth + 1)
            return result
        if isinstance(item, list):
            account(2)
            limit = None if current in preserve_sequences_at else max_sequence_items
            sampled = limit is not None and len(item) > limit
            first_count = limit // 2 if sampled else len(item)
            tail_start = len(item) - (limit - first_count) if sampled else len(item)
            if sampled:
                notices.append({
                    "path": current, "reason": "sequence_sample", "original_length": len(item),
                    "retained_index_ranges": [[0, first_count - 1], [tail_start, len(item) - 1]],
                    "omitted_count": len(item) - limit,
                })
                if indexed_sequences:
                    # A background-only transport alternative to thousands of
                    # identical omission markers. Original indexes and length
                    # remain explicit; this is not a shortened factual array.
                    indexes = [*range(first_count), *range(tail_start, len(item))]
                    header = {"original_length": len(item),
                              "retained_index_ranges": [[0, first_count - 1], [tail_start, len(item) - 1]]}
                    account(len(json.dumps(header)) + 64)
                    entries = {}
                    for index in indexes:
                        account(len(str(index)) + 4)
                        entries[str(index)] = project(item[index], f"{current}[{index}]", depth + 1)
                    return {"__uzi_indexed_sequence__": {**header, "items": entries}}
            result = []
            for index, child in enumerate(item):
                account(1)
                if sampled and first_count <= index < tail_start:
                    account(len(_OMISSION_KEY) + 9)
                    result.append({_OMISSION_KEY: True})
                else:
                    result.append(project(child, f"{current}[{index}]", depth + 1))
            return result
        if isinstance(item, str):
            # A URL must remain usable for exact frozen-reference matching.
            if len(item) > max_text_chars and not item.startswith(("https://", "http://")):
                notices.append({"path": current, "reason": "text_prefix",
                                "original_length": len(item), "retained_characters": max_text_chars})
                item = item[:max_text_chars]
        elif item is not None and not isinstance(item, (int, float, bool)):
            raise UziInputBudgetExceeded(f"input_budget_exceeded: unsupported evidence type {type(item).__name__}")
        account(len(json.dumps(item, ensure_ascii=False)))
        return item

    return project(value, path, 0)


def without_native_demo_holdings(raw: dict[str, Any]) -> dict[str, Any]:
    """Keep v3.9.4's sample position out of factual prompts and presentation.

    compute_dim_22 always creates a synthetic CNY 10,000 holding. Preserve the
    canonical result for native replay; this shallow copy-on-write view keeps
    the existing safety boundary without changing a score or a frozen input.
    """
    dims = raw.get("dimensions")
    if not isinstance(dims, dict):
        return raw
    dim = dims.get("22_deep_methods")
    if not isinstance(dim, dict) or not isinstance(dim.get("data"), dict):
        return raw
    if "portfolio_rebalance" not in dim["data"]:
        return raw
    return {
        **raw,
        "dimensions": {
            **dims,
            "22_deep_methods": {
                **dim,
                "data": {
                    **dim["data"],
                    "portfolio_rebalance": {
                        "quality_status": "data_gap",
                        "data_gap": ["explicit_portfolio_positions_required"],
                        "result": None,
                    },
                },
            },
        },
    }
