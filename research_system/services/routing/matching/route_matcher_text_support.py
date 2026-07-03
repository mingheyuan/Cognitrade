from __future__ import annotations

import math


class RouteMatcherTextSupport:
    def tokenize_for_overlap(self, text: str) -> set[str]:
        lowered = text.lower()
        ascii_tokens = {
            token
            for token in "".join(char if char.isalnum() else " " for char in lowered).split()
            if len(token) >= 2
        }
        cjk_bigrams = {
            lowered[index : index + 2]
            for index in range(len(lowered) - 1)
            if any("\u4e00" <= char <= "\u9fff" for char in lowered[index : index + 2])
        }
        return ascii_tokens | cjk_bigrams

    def token_vector(self, tokens: set[str]) -> dict[str, float]:
        if not tokens:
            return {}
        weight = 1.0 / math.sqrt(len(tokens))
        return {token: weight for token in tokens}

    def cosine_similarity(self, left: dict[str, float], right: dict[str, float]) -> float:
        if not left or not right:
            return 0.0
        overlap = set(left) & set(right)
        if not overlap:
            return 0.0
        numerator = sum(left[token] * right[token] for token in overlap)
        left_norm = math.sqrt(sum(value * value for value in left.values()))
        right_norm = math.sqrt(sum(value * value for value in right.values()))
        if left_norm <= 0.0 or right_norm <= 0.0:
            return 0.0
        return numerator / (left_norm * right_norm)


__all__ = ["RouteMatcherTextSupport"]
