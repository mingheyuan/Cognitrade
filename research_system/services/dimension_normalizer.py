from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DimensionTaxonomyEntry:
    canonical_name: str
    family: str
    category: str
    aliases: tuple[str, ...]
    patterns: tuple[str, ...]
    scenario_tags: tuple[str, ...]
    methodology_tags: tuple[str, ...]


def build_default_dimension_taxonomy() -> list[DimensionTaxonomyEntry]:
    return [
        DimensionTaxonomyEntry(
            canonical_name="AI",
            family="technology_ai",
            category="technology",
            aliases=("artificial intelligence", "人工智能"),
            patterns=(r"\bAI\b", r"artificial intelligence", r"人工智能"),
            scenario_tags=("technology", "efficiency"),
            methodology_tags=("technology", "productivity"),
        ),
        DimensionTaxonomyEntry(
            canonical_name="互联网",
            family="internet_platform",
            category="industry",
            aliases=("internet", "互聯網"),
            patterns=(r"internet", r"互联网", r"互聯網"),
            scenario_tags=("industry", "distribution"),
            methodology_tags=("industry",),
        ),
        DimensionTaxonomyEntry(
            canonical_name="创新药",
            family="innovation_drug",
            category="pipeline",
            aliases=("新药", "innovation drug"),
            patterns=(r"创新药", r"新药", r"innovation drug"),
            scenario_tags=("pipeline", "clinical", "regulation"),
            methodology_tags=("pipeline", "event_driven"),
        ),
        DimensionTaxonomyEntry(
            canonical_name="出海",
            family="global_expansion",
            category="strategy",
            aliases=("global expansion", "overseas expansion"),
            patterns=(r"出海", r"global expansion", r"overseas expansion"),
            scenario_tags=("strategy", "overseas"),
            methodology_tags=("strategy", "growth"),
        ),
        DimensionTaxonomyEntry(
            canonical_name="产品体验",
            family="product_experience",
            category="product",
            aliases=(),
            patterns=(r"产品体验",),
            scenario_tags=("product", "user_value"),
            methodology_tags=("quality",),
        ),
        DimensionTaxonomyEntry(
            canonical_name="效率",
            family="efficiency_improvement",
            category="product",
            aliases=(),
            patterns=(r"效率",),
            scenario_tags=("efficiency", "operations"),
            methodology_tags=("quality", "productivity"),
        ),
        DimensionTaxonomyEntry(
            canonical_name="成本",
            family="cost_control",
            category="economics",
            aliases=(),
            patterns=(r"成本",),
            scenario_tags=("economics", "margin"),
            methodology_tags=("economics",),
        ),
        DimensionTaxonomyEntry(
            canonical_name="用户习惯",
            family="user_habit",
            category="distribution",
            aliases=(),
            patterns=(r"用户习惯",),
            scenario_tags=("distribution", "retention"),
            methodology_tags=("quality",),
        ),
        DimensionTaxonomyEntry(
            canonical_name="临床",
            family="clinical_progress",
            category="pipeline",
            aliases=(),
            patterns=(r"临床",),
            scenario_tags=("clinical", "pipeline"),
            methodology_tags=("event_driven",),
        ),
        DimensionTaxonomyEntry(
            canonical_name="资金流入",
            family="capital_flow",
            category="capital",
            aliases=("capital inflow", "net inflow", "capital_inflow"),
            patterns=(r"资金流入", r"净流入", r"capital inflow", r"net inflow"),
            scenario_tags=("capital", "flow"),
            methodology_tags=("momentum", "liquidity"),
        ),
        DimensionTaxonomyEntry(
            canonical_name="板块热度",
            family="sector_sentiment",
            category="sentiment",
            aliases=("sector heat", "board heat", "sector_heat", "board_heat"),
            patterns=(r"板块热度", r"热度", r"sector heat", r"board heat"),
            scenario_tags=("sentiment", "theme"),
            methodology_tags=("momentum",),
        ),
    ]


class DimensionNormalizer:
    def __init__(self, taxonomy_config: dict[str, Any] | None = None) -> None:
        self.entries = self._build_entries(taxonomy_config)
        self._alias_index: dict[str, DimensionTaxonomyEntry] = {}
        self._patterns: list[tuple[re.Pattern[str], DimensionTaxonomyEntry]] = []
        for entry in self.entries:
            for token in (entry.canonical_name, *entry.aliases):
                normalized = self.normalize_text(token)
                if normalized and normalized not in self._alias_index:
                    self._alias_index[normalized] = entry
            for pattern in entry.patterns:
                try:
                    compiled = re.compile(pattern, flags=re.IGNORECASE)
                except re.error:
                    compiled = re.compile(re.escape(pattern), flags=re.IGNORECASE)
                self._patterns.append((compiled, entry))

    def extract_dimensions(self, text: str) -> list[tuple[str, str]]:
        if not text.strip():
            return []
        matches: list[tuple[str, str]] = []
        seen: set[str] = set()
        for entry in self._matched_entries(text):
            if entry.family in seen:
                continue
            seen.add(entry.family)
            matches.append((entry.canonical_name, entry.category))
        return matches

    def normalize_dimension(
        self,
        *,
        name: str,
        category: str | None = None,
        alias_hits: list[str] | None = None,
        normalization_notes: list[str] | None = None,
    ) -> dict[str, Any]:
        entry = self._find_entry(name, alias_hits=alias_hits or [])
        notes = [str(item).strip() for item in normalization_notes or [] if str(item).strip()]
        if entry is None:
            canonical_name = name.strip()
            dimension_family = self.normalize_text(canonical_name).replace(" ", "_") or "other_dimension"
            if "taxonomy_unmatched" not in notes:
                notes.append("taxonomy_unmatched")
            scenario_tags: list[str] = []
            methodology_tags: list[str] = []
        else:
            canonical_name = entry.canonical_name
            dimension_family = entry.family
            category = entry.category or category
            note = f"taxonomy_family={dimension_family}"
            if note not in notes:
                notes.append(note)
            scenario_tags = list(entry.scenario_tags)
            methodology_tags = list(entry.methodology_tags)
        canonical_category = str(category or "other").strip() or "other"
        dimension_tags = self._merge_text_lists(
            [canonical_name, dimension_family],
            scenario_tags,
            methodology_tags,
            alias_hits or [],
        )
        return {
            "dimension_name": canonical_name,
            "canonical_dimension_name": canonical_name,
            "dimension_family": dimension_family,
            "dimension_category": canonical_category,
            "canonical_aliases": list(entry.aliases) if entry is not None else [],
            "scenario_tags": scenario_tags,
            "methodology_tags": methodology_tags,
            "dimension_tags": dimension_tags,
            "dimension_key": self.dimension_key(canonical_name, family=dimension_family),
            "normalization_notes": notes,
        }

    def canonical_dimension_name(self, name: str) -> str:
        normalized = self.normalize_dimension(name=name)
        return str(normalized["canonical_dimension_name"])

    def dimension_key(self, name: str, *, family: str | None = None) -> str:
        key = family or self.normalize_text(name).replace(" ", "_")
        return key or "other_dimension"

    def normalize_text(self, text: str) -> str:
        lowered = unicodedata.normalize("NFKC", text).strip().lower()
        lowered = re.sub(r"[\s\-_/:,.;()\[\]{}<>]+", " ", lowered)
        lowered = re.sub(r"\s+", " ", lowered)
        return lowered.strip()

    def _find_entry(self, name: str, *, alias_hits: list[str]) -> DimensionTaxonomyEntry | None:
        normalized = self.normalize_text(name)
        if normalized in self._alias_index:
            return self._alias_index[normalized]
        for alias in alias_hits:
            alias_normalized = self.normalize_text(alias)
            if alias_normalized in self._alias_index:
                return self._alias_index[alias_normalized]
        for pattern, entry in self._patterns:
            if pattern.search(name):
                return entry
        return None

    def _matched_entries(self, text: str) -> list[DimensionTaxonomyEntry]:
        matched: list[DimensionTaxonomyEntry] = []
        seen: set[str] = set()
        for pattern, entry in self._patterns:
            if pattern.search(text) and entry.family not in seen:
                seen.add(entry.family)
                matched.append(entry)
        return matched

    def _build_entries(self, taxonomy_config: dict[str, Any] | None) -> list[DimensionTaxonomyEntry]:
        entries: list[DimensionTaxonomyEntry] = []
        raw_dimensions = list((taxonomy_config or {}).get("dimensions") or [])
        alias_index: dict[str, list[str]] = {}
        pattern_index: dict[str, list[str]] = {}

        for item in list((taxonomy_config or {}).get("aliases") or []):
            if not isinstance(item, dict):
                continue
            canonical_name = str(item.get("canonical_name") or item.get("name") or "").strip()
            aliases = [
                str(alias).strip()
                for alias in item.get("aliases") or []
                if str(alias).strip()
            ]
            if canonical_name and aliases:
                alias_index.setdefault(canonical_name, []).extend(aliases)

        for item in list((taxonomy_config or {}).get("patterns") or []):
            if not isinstance(item, dict):
                continue
            canonical_name = str(item.get("canonical_name") or item.get("name") or "").strip()
            patterns = [
                str(pattern).strip()
                for pattern in item.get("patterns") or []
                if str(pattern).strip()
            ]
            if canonical_name and patterns:
                pattern_index.setdefault(canonical_name, []).extend(patterns)

        for item in raw_dimensions:
            if not isinstance(item, dict):
                continue
            canonical_name = str(item.get("canonical_name") or item.get("name") or "").strip()
            category = str(item.get("category") or "other").strip() or "other"
            if not canonical_name:
                continue
            family = str(item.get("family") or self.normalize_text(canonical_name).replace(" ", "_") or canonical_name).strip()
            aliases = tuple(
                str(alias).strip()
                for alias in [*(item.get("aliases") or []), *alias_index.get(canonical_name, [])]
                if str(alias).strip()
            )
            patterns = tuple(
                str(pattern).strip()
                for pattern in (
                    item.get("patterns")
                    or pattern_index.get(canonical_name)
                    or self._default_patterns(canonical_name, aliases)
                )
                if str(pattern).strip()
            )
            scenario_tags = tuple(
                str(tag).strip()
                for tag in item.get("scenario_tags") or []
                if str(tag).strip()
            )
            methodology_tags = tuple(
                str(tag).strip()
                for tag in item.get("methodology_tags") or []
                if str(tag).strip()
            )
            entries.append(
                DimensionTaxonomyEntry(
                    canonical_name=canonical_name,
                    family=family,
                    category=category,
                    aliases=aliases,
                    patterns=patterns,
                    scenario_tags=scenario_tags,
                    methodology_tags=methodology_tags,
                )
            )

        seen_families = {entry.family for entry in entries}
        for entry in build_default_dimension_taxonomy():
            if entry.family not in seen_families:
                entries.append(entry)
        return entries

    def _default_patterns(self, canonical_name: str, aliases: tuple[str, ...]) -> list[str]:
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

    def _merge_text_lists(self, *groups: list[str] | tuple[str, ...]) -> list[str]:
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
