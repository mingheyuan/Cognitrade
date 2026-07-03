from __future__ import annotations

import re
import unicodedata


def normalize_dimension_text(text: str) -> str:
    lowered = unicodedata.normalize("NFKC", text).strip().lower()
    lowered = re.sub(r"[\s\-_/:,.;()\[\]{}<>]+", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered)
    return lowered.strip()


def default_dimension_patterns(canonical_name: str, aliases: tuple[str, ...]) -> list[str]:
    tokens = [canonical_name, *aliases]
    patterns: list[str] = []
    for token in tokens:
        stripped = str(token).strip()
        if not stripped:
            continue
        if stripped.isascii() and any(char.isalpha() for char in stripped):
            patterns.append(rf"\b{re.escape(stripped)}\b")
        else:
            patterns.append(re.escape(stripped))
    return patterns


def merge_text_lists(*groups: list[str] | tuple[str, ...]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            value = str(item).strip()
            if not value or value in seen:
                continue
            seen.add(value)
            merged.append(value)
    return merged
