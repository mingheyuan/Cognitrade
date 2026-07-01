from __future__ import annotations

from datetime import datetime
from typing import Any


class MethodologyEvolver:
    def build_candidates_from_runs(
        self,
        *,
        runs: list[dict[str, Any]],
        methodology_memory_rows: list[dict[str, Any]] | None = None,
        methodology_memory_search_rows: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        memory_rows = methodology_memory_rows or []
        memory_search_rows = methodology_memory_search_rows or []
        aggregated_candidates: list[dict[str, Any]] = []
        dimension_groups: dict[tuple[str, str], dict[str, Any]] = {}
        valuation_groups: dict[tuple[str, str], dict[str, Any]] = {}
        review_patterns: list[dict[str, Any]] = []

        for run in runs:
            run_id = str(run.get("run_id") or "").strip()
            blackboard_entries = list(run.get("blackboard_entries") or [])
            for candidate in self.build_candidates(
                run_id=run_id,
                blackboard_entries=blackboard_entries,
                methodology_memory_rows=memory_rows,
                methodology_memory_search_rows=memory_search_rows,
            ):
                candidate_type = str(candidate.get("candidate_type") or "")
                if candidate_type == "dimension":
                    title = str(candidate.get("title") or "").strip()
                    family = str(candidate.get("dimension_family") or "").strip()
                    key = (title, family)
                    group = dimension_groups.setdefault(
                        key,
                        {
                            "candidate_type": "dimension",
                            "title": title,
                            "dimension_family": family,
                            "scenario_tags": set(),
                            "methodology_tags": set(),
                            "run_ids": [],
                            "related_entry_ids": [],
                            "supported_claim_count": 0,
                            "evidence_block_count": 0,
                            "confidence_values": [],
                            "memory_context": [],
                            "memory_match_scores": [],
                        },
                    )
                    group["scenario_tags"].update(candidate.get("scenario_tags") or [])
                    group["methodology_tags"].update(candidate.get("methodology_tags") or [])
                    if run_id and run_id not in group["run_ids"]:
                        group["run_ids"].append(run_id)
                    entry_id = str(candidate.get("entry_id") or "").strip()
                    if entry_id:
                        group["related_entry_ids"].append(entry_id)
                    evidence_summary = candidate.get("evidence_summary") or {}
                    group["supported_claim_count"] += len(evidence_summary.get("supported_claim_block_ids") or [])
                    group["evidence_block_count"] += len(evidence_summary.get("evidence_block_ids") or [])
                    confidence = candidate.get("confidence")
                    if confidence is not None:
                        group["confidence_values"].append(confidence)
                    for item in candidate.get("memory_context") or []:
                        group["memory_context"].append(item)
                        if item.get("match_score") is not None:
                            group["memory_match_scores"].append(item.get("match_score"))
                elif candidate_type == "valuation_heuristic":
                    methodology_type = str(candidate.get("methodology_type") or "").strip()
                    title = str(candidate.get("title") or methodology_type or "").strip()
                    key = (methodology_type, title)
                    group = valuation_groups.setdefault(
                        key,
                        {
                            "candidate_type": "valuation_heuristic",
                            "title": title,
                            "methodology_type": methodology_type,
                            "assumption_tags": set(),
                            "sensitivity_tags": set(),
                            "run_ids": [],
                            "symbols": set(),
                            "related_entry_ids": [],
                            "warnings_count": 0,
                            "confidence_values": [],
                            "memory_context": [],
                            "memory_match_scores": [],
                        },
                    )
                    group["assumption_tags"].update(candidate.get("assumption_tags") or [])
                    group["sensitivity_tags"].update(candidate.get("sensitivity_tags") or [])
                    if run_id and run_id not in group["run_ids"]:
                        group["run_ids"].append(run_id)
                    symbol = str(candidate.get("symbol") or "").strip()
                    if symbol:
                        group["symbols"].add(symbol)
                    entry_id = str(candidate.get("entry_id") or "").strip()
                    if entry_id:
                        group["related_entry_ids"].append(entry_id)
                    evidence_summary = candidate.get("evidence_summary") or {}
                    group["warnings_count"] += len(evidence_summary.get("warnings") or [])
                    confidence = candidate.get("confidence")
                    if confidence is not None:
                        group["confidence_values"].append(confidence)
                    for item in candidate.get("memory_context") or []:
                        group["memory_context"].append(item)
                        if item.get("match_score") is not None:
                            group["memory_match_scores"].append(item.get("match_score"))
                elif candidate_type == "review_pattern":
                    enriched = dict(candidate)
                    enriched["run_ids"] = [run_id] if run_id else []
                    review_patterns.append(enriched)

        for (title, family), group in dimension_groups.items():
            confidence_values = list(group.pop("confidence_values"))
            memory_context = self._dedupe_memory_context(group.pop("memory_context"))
            memory_match_scores = list(group.pop("memory_match_scores"))
            run_ids = list(group["run_ids"])
            aggregated_candidates.append(
                {
                    "candidate_type": "dimension",
                    "run_id": run_ids[0] if len(run_ids) == 1 else None,
                    "title": title,
                    "dimension_family": family,
                    "scenario_tags": sorted(group["scenario_tags"]),
                    "methodology_tags": sorted(group["methodology_tags"]),
                    "sample_size": len(run_ids),
                    "related_run_ids": run_ids,
                    "related_entry_ids": list(group["related_entry_ids"]),
                    "aggregated_evidence_summary": {
                        "supported_claim_count": group["supported_claim_count"],
                        "evidence_block_count": group["evidence_block_count"],
                    },
                    "confidence": self._average(confidence_values),
                    "memory_match_score": self._average(memory_match_scores),
                    "memory_support": self._memory_support_summary(memory_context),
                    "memory_context": memory_context,
                }
            )

        for (methodology_type, title), group in valuation_groups.items():
            confidence_values = list(group.pop("confidence_values"))
            memory_context = self._dedupe_memory_context(group.pop("memory_context"))
            memory_match_scores = list(group.pop("memory_match_scores"))
            run_ids = list(group["run_ids"])
            aggregated_candidates.append(
                {
                    "candidate_type": "valuation_heuristic",
                    "run_id": run_ids[0] if len(run_ids) == 1 else None,
                    "title": title,
                    "methodology_type": methodology_type,
                    "assumption_tags": sorted(group["assumption_tags"]),
                    "sensitivity_tags": sorted(group["sensitivity_tags"]),
                    "sample_size": len(run_ids),
                    "related_run_ids": run_ids,
                    "symbols": sorted(group["symbols"]),
                    "related_entry_ids": list(group["related_entry_ids"]),
                    "aggregated_evidence_summary": {
                        "warnings_count": group["warnings_count"],
                        "symbol_count": len(group["symbols"]),
                    },
                    "confidence": self._average(confidence_values),
                    "memory_match_score": self._average(memory_match_scores),
                    "memory_support": self._memory_support_summary(memory_context),
                    "memory_context": memory_context,
                }
            )

        if review_patterns:
            severity_counts: dict[str, int] = {}
            verdict_counts: dict[str, int] = {}
            sample_size = 0
            run_ids: list[str] = []
            for candidate in review_patterns:
                sample_size += int(candidate.get("sample_size") or 0)
                for severity, count in (candidate.get("severity_counts") or {}).items():
                    severity_text = str(severity).strip()
                    if severity_text:
                        severity_counts[severity_text] = severity_counts.get(severity_text, 0) + int(count)
                for verdict, count in (candidate.get("verdict_counts") or {}).items():
                    verdict_text = str(verdict).strip()
                    if verdict_text:
                        verdict_counts[verdict_text] = verdict_counts.get(verdict_text, 0) + int(count)
                for run_id in candidate.get("run_ids") or []:
                    run_id_text = str(run_id).strip()
                    if run_id_text and run_id_text not in run_ids:
                        run_ids.append(run_id_text)
            aggregated_candidates.append(
                {
                    "candidate_type": "review_pattern",
                    "title": "review_pattern_summary",
                    "sample_size": sample_size,
                    "related_run_ids": run_ids,
                    "severity_counts": severity_counts,
                    "verdict_counts": verdict_counts,
                }
            )

        return aggregated_candidates

    def build_candidates(
        self,
        *,
        run_id: str,
        blackboard_entries: list[dict],
        methodology_memory_rows: list[dict[str, Any]] | None = None,
        methodology_memory_search_rows: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        memory_rows = methodology_memory_rows or []
        memory_search_rows = methodology_memory_search_rows or []
        dimension_entries = [
            entry for entry in blackboard_entries
            if entry.get("kind") == "distilled_dimension"
        ]
        methodology_entries = [
            entry for entry in blackboard_entries
            if entry.get("kind") == "source_record"
            and str((entry.get("payload") or {}).get("source_type") or "").startswith("methodology_")
        ]
        review_findings = [
            entry for entry in blackboard_entries
            if entry.get("kind") == "review_finding"
        ]

        for entry in dimension_entries:
            payload = entry.get("payload") or {}
            candidates.append(
                {
                    "candidate_type": "dimension",
                    "run_id": run_id,
                    "entry_id": entry.get("entry_id"),
                    "title": str(payload.get("canonical_dimension_name") or payload.get("dimension_name") or "unknown_dimension"),
                    "dimension_family": payload.get("dimension_family"),
                    "scenario_tags": list(payload.get("scenario_tags") or []),
                    "methodology_tags": list(payload.get("methodology_tags") or []),
                    "confidence": payload.get("confidence"),
                    "evidence_summary": {
                        "supported_claim_block_ids": list(payload.get("supported_claim_block_ids") or []),
                        "evidence_block_ids": list(payload.get("evidence_block_ids") or []),
                    },
                    "memory_context": self._matching_memory_context(
                        memory_rows=memory_rows,
                        memory_search_rows=memory_search_rows,
                        title=str(payload.get("canonical_dimension_name") or payload.get("dimension_name") or "unknown_dimension"),
                        dimension_family=str(payload.get("dimension_family") or "").strip() or None,
                    ),
                }
            )

        for entry in methodology_entries:
            payload = entry.get("payload") or {}
            methodology_result = payload.get("methodology_result") or {}
            candidates.append(
                {
                    "candidate_type": "valuation_heuristic",
                    "run_id": run_id,
                    "entry_id": entry.get("entry_id"),
                    "title": str(payload.get("title") or payload.get("methodology_type") or "valuation"),
                    "methodology_type": payload.get("methodology_type"),
                    "symbol": payload.get("symbol"),
                    "assumption_tags": list(payload.get("assumption_tags") or []),
                    "sensitivity_tags": list(payload.get("sensitivity_tags") or []),
                    "valuation_band_label": payload.get("valuation_band_label"),
                    "confidence": payload.get("confidence"),
                    "evidence_summary": {
                        "warnings": list(methodology_result.get("warnings") or []),
                        "outputs": dict(methodology_result.get("outputs") or {}),
                    },
                    "memory_context": self._matching_memory_context(
                        memory_rows=memory_rows,
                        memory_search_rows=memory_search_rows,
                        title=str(payload.get("title") or payload.get("methodology_type") or "valuation"),
                        methodology_type=str(payload.get("methodology_type") or "").strip() or None,
                    ),
                }
            )

        if review_findings:
            severity_counts: dict[str, int] = {}
            verdict_counts: dict[str, int] = {}
            for entry in review_findings:
                payload = entry.get("payload") or {}
                severity = str(payload.get("severity") or "")
                verdict = str(payload.get("verdict") or "")
                if severity:
                    severity_counts[severity] = severity_counts.get(severity, 0) + 1
                if verdict:
                    verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
            candidates.append(
                {
                    "candidate_type": "review_pattern",
                    "run_id": run_id,
                    "title": "review_pattern_summary",
                    "severity_counts": severity_counts,
                    "verdict_counts": verdict_counts,
                    "sample_size": len(review_findings),
                }
        )
        return candidates

    def _matching_memory_context(
        self,
        *,
        memory_rows: list[dict[str, Any]],
        memory_search_rows: list[dict[str, Any]],
        title: str,
        dimension_family: str | None = None,
        methodology_type: str | None = None,
    ) -> list[dict[str, Any]]:
        lowered_title = title.strip().lower()
        matched: list[dict[str, Any]] = []
        for row in memory_search_rows:
            content = row.get("content") if isinstance(row.get("content"), dict) else {}
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            if not isinstance(content, dict):
                continue
            content_scope = str(content.get("benchmark_scope") or metadata.get("benchmark_scope") or "").strip().lower()
            if dimension_family and content_scope and content_scope != str(dimension_family).strip().lower():
                continue
            if methodology_type and content_scope and methodology_type not in content_scope:
                continue
            matched.append(
                {
                    "memory_type": row.get("memory_type"),
                    "title": content.get("title"),
                    "summary": content.get("summary"),
                    "benchmark_scope": content.get("benchmark_scope") or metadata.get("benchmark_scope"),
                    "match_score": row.get("match_score"),
                    "matched_terms": row.get("matched_terms") or [],
                    "memory_scope": self._protocol_value(row, "memory_scope"),
                    "entity_scope": self._protocol_value(row, "entity_scope"),
                    "snapshot_id": self._protocol_value(row, "snapshot_id"),
                    "schema_version": self._protocol_value(row, "schema_version"),
                    "snapshot_version": self._protocol_value(row, "snapshot_version"),
                    "recorded_at": self._protocol_value(row, "recorded_at"),
                }
            )
        for row in memory_rows:
            content = row.get("content") if isinstance(row.get("content"), dict) else {}
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            if not isinstance(content, dict):
                continue
            blob = " ".join(
                str(content.get(key) or "")
                for key in ("title", "summary", "benchmark_scope", "proposal_id")
            ).lower()
            if lowered_title and lowered_title in blob:
                matched.append(
                    {
                        "memory_type": row.get("memory_type"),
                        "title": content.get("title"),
                        "summary": content.get("summary"),
                        "benchmark_scope": content.get("benchmark_scope") or metadata.get("benchmark_scope"),
                        "memory_scope": self._protocol_value(row, "memory_scope"),
                        "entity_scope": self._protocol_value(row, "entity_scope"),
                        "snapshot_id": self._protocol_value(row, "snapshot_id"),
                        "schema_version": self._protocol_value(row, "schema_version"),
                        "snapshot_version": self._protocol_value(row, "snapshot_version"),
                        "recorded_at": self._protocol_value(row, "recorded_at"),
                    }
                )
        return matched[:3]

    def _dedupe_memory_context(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[tuple[str, str, str]] = set()
        deduped: list[dict[str, Any]] = []
        for item in items:
            key = (
                str(item.get("memory_type") or ""),
                str(item.get("entity_scope") or item.get("snapshot_id") or item.get("title") or ""),
                str(item.get("snapshot_id") or item.get("summary") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped[:5]

    def _memory_support_summary(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        entity_scopes = {
            str(item.get("entity_scope") or "").strip()
            for item in items
            if str(item.get("entity_scope") or "").strip()
        }
        snapshot_ids = {
            str(item.get("snapshot_id") or "").strip()
            for item in items
            if str(item.get("snapshot_id") or "").strip()
        }
        recorded_ats = sorted(
            {
                value
                for value in (
                    str(item.get("recorded_at") or "").strip()
                    for item in items
                )
                if value
            }
        )
        latest_recorded_at = recorded_ats[-1] if recorded_ats else None
        oldest_recorded_at = recorded_ats[0] if recorded_ats else None
        return {
            "context_count": len(items),
            "entity_scope_count": len(entity_scopes),
            "snapshot_count": len(snapshot_ids),
            "latest_recorded_at": latest_recorded_at,
            "oldest_recorded_at": oldest_recorded_at,
        }

    def _protocol_value(self, row: dict[str, Any], key: str) -> str | None:
        content = row.get("content") if isinstance(row.get("content"), dict) else {}
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        content_value = str(content.get(key) or "").strip() if isinstance(content, dict) else ""
        if content_value:
            return content_value
        metadata_value = str(metadata.get(key) or "").strip() if isinstance(metadata, dict) else ""
        return metadata_value or None

    def _average(self, values: list[Any]) -> float | None:
        numeric_values: list[float] = []
        for value in values:
            try:
                numeric_values.append(float(value))
            except Exception:
                continue
        if not numeric_values:
            return None
        return sum(numeric_values) / len(numeric_values)
