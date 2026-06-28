from __future__ import annotations

import ast

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from research_system.adapters.storage.qdrant_adapter import QdrantAdapter, QdrantUnavailableError
from research_system.runtime.provider_registry import LLMProviderError, OpenAIProvider
from research_system.runtime.settings import Settings


class RetrievalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_evidence_ids: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("selected_evidence_ids", "evidence_ids"),
    )
    retrieval_summary: str
    degraded: bool = False
    degrade_reason: str | None = None


class RetrieverService:
    agent_name = "retriever"

    def __init__(self, llm_provider: OpenAIProvider, settings: Settings, retrieval_policy: dict | None = None) -> None:
        self.llm_provider = llm_provider
        try:
            self.qdrant = QdrantAdapter(settings)
            self.qdrant_available = self.qdrant.available
            self.qdrant_error: str | None = None
        except QdrantUnavailableError as exc:
            self.qdrant = None
            self.qdrant_available = False
            self.qdrant_error = str(exc)
        self.retrieval_policy = retrieval_policy or {}

    def run(self, *, intent: str, query: str, blackboard_entries: list[dict]) -> dict:
        evidence_map: dict[str, dict] = {}
        for entry in blackboard_entries:
            if entry["kind"] in {"source_record", "verified_snapshot", "logic_block", "distilled_dimension", "review_finding"}:
                if self.qdrant is not None:
                    self.qdrant.upsert_entry(self._to_blackboard_entry(entry))
                evidence = self._evidence_from_blackboard_entry(entry)
                evidence_map[evidence["evidence_id"]] = evidence

        symbol = None
        sector = None
        current_run_id = blackboard_entries[0]["run_id"] if blackboard_entries else None
        for entry in blackboard_entries:
            symbol = entry.get("payload", {}).get("symbol") or symbol
            sector = entry.get("payload", {}).get("sector") or sector

        top_k = int(self.retrieval_policy.get("final_top_k", 5))
        metadata_filters: dict[str, str] = {"record_type": "verified_snapshot"}
        if symbol:
            metadata_filters["symbol"] = symbol
        elif sector:
            metadata_filters["sector"] = sector
        elif current_run_id:
            metadata_filters["run_id"] = current_run_id
        qdrant_degraded_reason = None
        if self.qdrant is not None:
            qdrant_hits = self.qdrant.search(
                query_text=query,
                top_k=top_k,
                metadata_filters=metadata_filters,
            )
            for hit in qdrant_hits:
                payload = hit["payload"]
                evidence_id = payload["entry_id"]
                candidate = {
                    "evidence_id": evidence_id,
                    "title": payload["document_title"],
                    "summary": payload["content"],
                    "retrieval_hint": self._retrieval_hint_from_qdrant_payload(payload),
                    "source_refs": payload.get("source_refs", []),
                    "metadata": {
                        "phase": payload.get("phase"),
                        "agent": payload.get("agent"),
                        "record_type": payload.get("record_type"),
                        "source_type": payload.get("source_type"),
                    },
                    "score": hit["score"],
                }
                existing = evidence_map.get(evidence_id)
                if existing is None:
                    evidence_map[evidence_id] = candidate
                elif (existing.get("score") or 0.0) < (candidate.get("score") or 0.0):
                    merged = dict(existing)
                    merged.update(candidate)
                    evidence_map[evidence_id] = merged
        else:
            qdrant_degraded_reason = self.qdrant_error or "qdrant_unavailable"

        evidences = list(evidence_map.values())
        evidences = self._dedupe_evidences(
            evidences=evidences,
            current_run_id=current_run_id,
        )
        evidences.sort(key=lambda item: item.get("score") or 0.0, reverse=True)

        if intent == "distill_kol_logic":
            selected = self._select_distillation_evidences(evidences=evidences, top_k=top_k)
            return {
                "intent": intent,
                "query": query,
                "evidences": selected,
                "degraded": bool(qdrant_degraded_reason),
                "degrade_reason": qdrant_degraded_reason,
                "retrieval_summary": self._merge_retrieval_summary(
                    self._structured_retrieval_summary(intent=intent, evidences=selected),
                    "Deterministic distillation retrieval completed.",
                ),
            }
        if intent == "review_thesis":
            selected = self._select_review_evidences(evidences=evidences, top_k=top_k)
            return {
                "intent": intent,
                "query": query,
                "evidences": selected,
                "degraded": bool(qdrant_degraded_reason),
                "degrade_reason": qdrant_degraded_reason,
                "retrieval_summary": self._merge_retrieval_summary(
                    self._structured_retrieval_summary(intent=intent, evidences=selected),
                    "Deterministic review retrieval completed.",
                ),
            }

        if not evidences:
            return {
                "intent": intent,
                "query": query,
                "evidences": [],
                "degraded": True,
                "retrieval_summary": "No evidence candidates available.",
                "degrade_reason": "missing_candidates",
            }

        evidence_block = [
            {
                "evidence_id": item["evidence_id"],
                "title": item["title"],
                "summary": item["summary"],
                "metadata": item["metadata"],
                "source_refs": item["source_refs"],
            }
            for item in evidences
        ]

        if not self.llm_provider.available:
            fallback_evidences = evidences[:top_k]
            return {
                "intent": intent,
                "query": query,
                "evidences": fallback_evidences,
                "degraded": True,
                "degrade_reason": self._combine_degrade_reason("llm_unavailable", qdrant_degraded_reason),
                "retrieval_summary": self._fallback_summary(
                    intent=intent,
                    evidences=fallback_evidences,
                    qdrant_degraded_reason=qdrant_degraded_reason,
                ),
            }

        try:
            decision = self.llm_provider.parse(
                system_prompt=(
                    "You are a retrieval agent for an A-share research system.\n"
                    "Select the most relevant evidence candidates for the user's research intent.\n"
                    "Do not invent evidence. Prefer verified snapshots when exact claims may matter."
                ),
                user_prompt=(
                    f"Intent: {intent}\n"
                    f"User query: {query}\n"
                    f"Top K limit: {top_k}\n"
                    f"Evidence candidates: {evidence_block}\n\n"
                    "Return a JSON object with keys "
                    "`selected_evidence_ids`, `retrieval_summary`, `degraded`, and `degrade_reason`.\n"
                    "Use `selected_evidence_ids` for the chosen evidence ids."
                ),
                schema=RetrievalDecision,
                temperature=0.0,
                reasoning_effort="low",
            )
        except LLMProviderError as exc:
            fallback_evidences = evidences[:top_k]
            return {
                "intent": intent,
                "query": query,
                "evidences": fallback_evidences,
                "degraded": True,
                "degrade_reason": self._combine_degrade_reason(f"llm_error:{exc.__class__.__name__}", qdrant_degraded_reason),
                "retrieval_summary": self._fallback_summary(
                    intent=intent,
                    evidences=fallback_evidences,
                    qdrant_degraded_reason=qdrant_degraded_reason,
                ),
            }
        selected_ids = decision.selected_evidence_ids[:top_k]
        if not selected_ids:
            selected_ids = [item["evidence_id"] for item in evidences[:top_k]]
            decision.degraded = True
            decision.degrade_reason = "empty_selection"

        selected_lookup = {item["evidence_id"]: item for item in evidences}
        selected = [selected_lookup[evidence_id] for evidence_id in selected_ids if evidence_id in selected_lookup]
        if not selected:
            selected = evidences[:top_k]
            decision.degraded = True
            decision.degrade_reason = "selection_mismatch"

        structured_summary = self._structured_retrieval_summary(intent=intent, evidences=selected)
        return {
            "intent": intent,
            "query": query,
            "evidences": selected,
            "degraded": decision.degraded or bool(qdrant_degraded_reason),
            "degrade_reason": self._combine_degrade_reason(decision.degrade_reason, qdrant_degraded_reason),
            "retrieval_summary": self._merge_retrieval_summary(structured_summary, decision.retrieval_summary),
        }

    def _to_blackboard_entry(self, payload: dict):
        from research_system.schemas.runtime import BlackboardEntry

        return BlackboardEntry.model_validate(payload)

    def _evidence_from_blackboard_entry(self, entry: dict) -> dict:
        title = self._entry_title(entry)
        return {
            "evidence_id": entry["entry_id"],
            "title": title,
            "summary": self._entry_summary(entry),
            "retrieval_hint": self._entry_retrieval_hint(entry),
            "source_refs": entry["source_refs"],
            "metadata": {
                "phase": entry["phase"],
                "agent": entry["agent"],
                "record_type": entry["kind"],
                "source_type": self._entry_source_type(entry),
            },
            "score": 1.0,
        }

    def _entry_title(self, entry: dict) -> str:
        payload = entry["payload"]
        if entry["kind"] == "logic_block":
            block_type = str(payload.get("block_type") or "logic_block")
            return f"logic_block:{block_type}"
        if entry["kind"] == "distilled_dimension":
            dimension_name = str(payload.get("dimension_name") or "unknown_dimension")
            return f"distilled_dimension:{dimension_name}"
        if entry["kind"] == "review_finding":
            finding_type = str(payload.get("finding_type") or "review_finding")
            return f"review_finding:{finding_type}"
        return payload.get("title") or payload.get("snapshot_type") or entry["kind"]

    def _entry_summary(self, entry: dict) -> str:
        payload = entry["payload"]
        if entry["kind"] == "source_record":
            title = payload.get("title", "source_record")
            content = payload.get("content", "")
            return f"{title}\n{content}".strip()
        if entry["kind"] == "verified_snapshot":
            snapshot_type = payload.get("snapshot_type", "snapshot")
            facts = payload.get("facts", {})
            if snapshot_type == "sector":
                return self._sector_snapshot_summary(payload=payload, facts=facts)
            parts = [
                f"{snapshot_type} snapshot for {payload.get('symbol') or payload.get('sector') or 'unknown symbol'}",
                f"snapshot_date: {payload.get('snapshot_date', '')}",
            ]
            if facts:
                parts.append(str(facts))
            if payload.get("degraded"):
                parts.append(f"degraded: {payload.get('degrade_reason')}")
            return "\n".join(parts)
        if entry["kind"] == "logic_block":
            parts = [
                f"logic_block type={payload.get('block_type')} source_record_id={payload.get('source_record_id')} speaker_type={payload.get('speaker_type')}",
                f"text: {payload.get('text', '')}",
            ]
            if payload.get("attribution_label"):
                parts.append(f"attribution_label: {payload.get('attribution_label')}")
            if payload.get("normalized_claim"):
                parts.append(f"normalized_claim: {payload.get('normalized_claim')}")
            if payload.get("risk_flags"):
                parts.append(f"risk_flags: {payload.get('risk_flags')}")
            return "\n".join(parts)
        if entry["kind"] == "distilled_dimension":
            parts = [
                f"distilled_dimension {payload.get('dimension_name')} category={payload.get('dimension_category')}",
                f"supported_claim_block_ids: {payload.get('supported_claim_block_ids', [])}",
                f"evidence_block_ids: {payload.get('evidence_block_ids', [])}",
            ]
            return "\n".join(parts)
        if entry["kind"] == "review_finding":
            if payload.get("finding_type") is not None:
                parts = [
                    f"review_finding type={payload.get('finding_type')} severity={payload.get('severity')} verdict={payload.get('verdict')}",
                    f"reason: {payload.get('reason', '')}",
                ]
                if payload.get("suggested_action"):
                    parts.append(f"suggested_action: {payload.get('suggested_action')}")
                return "\n".join(parts)
            parts = [
                f"review_finding claim_type={payload.get('claim_type')} verdict={payload.get('verdict')}",
                f"benchmark={payload.get('benchmark_name')} scope={payload.get('benchmark_scope')}",
            ]
            if payload.get("expected_direction"):
                parts.append(f"expected_direction={payload.get('expected_direction')}")
            if payload.get("excess_metric") is not None:
                parts.append(f"excess_metric={payload.get('excess_metric')}")
            if payload.get("notes"):
                parts.append(f"notes: {payload.get('notes')}")
            return "\n".join(parts)
        return str(payload)

    def _entry_retrieval_hint(self, entry: dict) -> dict | None:
        if entry["kind"] != "verified_snapshot":
            return None
        payload = entry["payload"]
        if payload.get("snapshot_type") != "sector":
            return None

        facts = payload.get("facts", {})
        signal_scorecard = facts.get("signal_scorecard") or {}
        latest_hist = facts.get("latest_hist") or {}
        relative_strength = facts.get("relative_strength") or {}
        catalyst_digest = facts.get("catalyst_digest") or {}
        breadth = facts.get("breadth") or {}
        return {
            "hint_type": "sector_signal_scorecard",
            "sector": payload.get("sector"),
            "setup_label": signal_scorecard.get("setup_label"),
            "market_tone": signal_scorecard.get("market_tone"),
            "benchmark_view": signal_scorecard.get("benchmark_view"),
            "turnover_state": signal_scorecard.get("turnover_state"),
            "breadth_state": signal_scorecard.get("breadth_state"),
            "policy_signal_strength": signal_scorecard.get("policy_signal_strength"),
            "regulatory_signal_strength": signal_scorecard.get("regulatory_signal_strength"),
            "signal_summary": signal_scorecard.get("summary"),
            "latest_pct_change": latest_hist.get("pct_change"),
            "latest_excess_pct_change": relative_strength.get("latest_excess_pct_change"),
            "up_ratio": breadth.get("up_ratio"),
            "median_pct_change": breadth.get("median_pct_change"),
            "average_turnover_rate": breadth.get("average_turnover_rate"),
            "top_policy_title": (catalyst_digest.get("top_policy_titles") or [None])[0],
            "top_regulatory_title": (catalyst_digest.get("top_regulatory_titles") or [None])[0],
        }

    def _combine_degrade_reason(self, primary: str | None, secondary: str | None) -> str | None:
        reasons = [reason for reason in (primary, secondary) if reason]
        if not reasons:
            return None
        return "; ".join(reasons)

    def _fallback_summary(self, *, intent: str, evidences: list[dict], qdrant_degraded_reason: str | None) -> str:
        structured_summary = self._structured_retrieval_summary(intent=intent, evidences=evidences)
        if qdrant_degraded_reason:
            base = "LLM unavailable and Qdrant degraded; using blackboard evidence only."
        else:
            base = "LLM unavailable; using the best available evidence candidates."
        return self._merge_retrieval_summary(structured_summary, base)

    def _entry_source_type(self, entry: dict) -> str | None:
        if entry["kind"] == "verified_snapshot":
            snapshot_type = entry["payload"].get("snapshot_type")
            if snapshot_type:
                return f"{snapshot_type}_snapshot"
        if entry["kind"] in {"logic_block", "distilled_dimension", "review_finding"}:
            return entry["kind"]
        source_type = entry["payload"].get("source_type")
        return str(source_type) if source_type is not None else None

    def _sector_snapshot_summary(self, *, payload: dict, facts: dict) -> str:
        scorecard = facts.get("signal_scorecard") or {}
        latest_hist = facts.get("latest_hist") or {}
        relative_strength = facts.get("relative_strength") or {}
        catalyst_digest = facts.get("catalyst_digest") or {}

        parts = [
            f"sector snapshot for {payload.get('sector') or 'unknown sector'}",
            f"snapshot_date: {payload.get('snapshot_date', '')}",
        ]
        if scorecard:
            parts.append(f"signal_scorecard: {scorecard}")
        if latest_hist:
            parts.append(f"latest_hist: {latest_hist}")
        if relative_strength:
            parts.append(f"relative_strength: {relative_strength}")
        if catalyst_digest:
            parts.append(f"catalyst_digest: {catalyst_digest}")
        if payload.get("degraded"):
            parts.append(f"degraded: {payload.get('degrade_reason')}")
        return "\n".join(parts)

    def _structured_retrieval_summary(self, *, intent: str, evidences: list[dict]) -> str | None:
        if intent == "distill_kol_logic":
            logic_blocks = [
                item for item in evidences
                if str((item.get("metadata") or {}).get("source_type") or "") == "logic_block"
            ]
            dimensions = [
                item for item in evidences
                if str((item.get("metadata") or {}).get("source_type") or "") == "distilled_dimension"
            ]
            review_findings = [
                item for item in evidences
                if str((item.get("metadata") or {}).get("source_type") or "") == "review_finding"
            ]
            scorecards = [
                item for item in evidences
                if str((item.get("metadata") or {}).get("source_type") or "") == "distillation_scorecard"
            ]
            if not logic_blocks and not dimensions:
                return None
            block_type_counts: dict[str, int] = {}
            for item in logic_blocks:
                block_type = str(item.get("title") or "")
                if "logic_block:" in block_type:
                    block_type = block_type.split(":", 1)[1]
                block_type_counts[block_type or "logic_block"] = block_type_counts.get(block_type or "logic_block", 0) + 1
            dimension_names = []
            for item in dimensions[:5]:
                title = str(item.get("title") or "")
                dimension_names.append(title.split(":", 1)[1] if ":" in title else title)
            parts = [
                f"Distillation scorecard: logic_blocks={len(logic_blocks)}",
                f"distilled_dimensions={len(dimensions)}",
            ]
            if block_type_counts:
                parts.append(f"block_type_counts={block_type_counts}")
            attribution_counts: dict[str, int] = {}
            for item in logic_blocks:
                summary = str(item.get("summary") or "")
                marker = "speaker_type="
                if marker in summary:
                    speaker_type = summary.split(marker, 1)[1].split()[0]
                    attribution_counts[speaker_type] = attribution_counts.get(speaker_type, 0) + 1
            if attribution_counts:
                parts.append(f"speaker_type_counts={attribution_counts}")
            if dimension_names:
                parts.append(f"top_dimensions={dimension_names}")
            if review_findings:
                severity_counts: dict[str, int] = {}
                finding_types: list[str] = []
                for item in review_findings:
                    title = str(item.get("title") or "")
                    finding_type = title.split(":", 1)[1] if ":" in title else title
                    if finding_type:
                        finding_types.append(finding_type)
                    summary = str(item.get("summary") or "")
                    if "severity=" in summary:
                        severity = summary.split("severity=", 1)[1].split()[0]
                        severity_counts[severity] = severity_counts.get(severity, 0) + 1
                parts.append(f"review_findings={len(review_findings)}")
                if severity_counts:
                    parts.append(f"review_severity_counts={severity_counts}")
                if finding_types:
                    parts.append(f"review_types={sorted(set(finding_types))[:5]}")
            if scorecards:
                scorecard_summary = str(scorecards[0].get("summary") or "")
                if "distillation_scorecard" in scorecard_summary:
                    scorecard_summary = scorecard_summary.split("\n", 1)[-1]
                parts.append(f"scorecard={scorecard_summary}")
            return "; ".join(parts)
        if intent == "review_thesis":
            review_findings = [
                item for item in evidences
                if str((item.get("metadata") or {}).get("source_type") or "") == "review_finding"
            ]
            sector_snapshots = [
                item for item in evidences
                if str((item.get("metadata") or {}).get("source_type") or "") == "sector_snapshot"
            ]
            review_summaries = [
                item for item in evidences
                if str((item.get("metadata") or {}).get("source_type") or "") == "review_summary"
            ]
            if not review_findings and not sector_snapshots:
                return None
            verdict_counts: dict[str, int] = {}
            claim_type_counts: dict[str, int] = {}
            for item in review_findings:
                summary = str(item.get("summary") or "")
                if "verdict=" in summary:
                    verdict = summary.split("verdict=", 1)[1].split()[0]
                    verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
                if "claim_type=" in summary:
                    claim_type = summary.split("claim_type=", 1)[1].split()[0]
                    claim_type_counts[claim_type] = claim_type_counts.get(claim_type, 0) + 1
            parts = [
                f"Posterior review: review_findings={len(review_findings)}",
                f"sector_snapshots={len(sector_snapshots)}",
            ]
            if verdict_counts:
                parts.append(f"verdict_counts={verdict_counts}")
            if claim_type_counts:
                parts.append(f"claim_type_counts={claim_type_counts}")
            if review_summaries:
                review_summary_payload = self._review_summary_payload_from_evidence(review_summaries[0])
                if isinstance(review_summary_payload, dict):
                    verdict_by_claim_type = review_summary_payload.get("verdict_by_claim_type")
                    if verdict_by_claim_type:
                        parts.append(f"verdict_by_claim_type={verdict_by_claim_type}")
                    benchmark_scope_counts = review_summary_payload.get("benchmark_scope_counts")
                    if benchmark_scope_counts:
                        parts.append(f"benchmark_scope_counts={benchmark_scope_counts}")
                    benchmark_scope_explanations = review_summary_payload.get("benchmark_scope_explanations")
                    if benchmark_scope_explanations:
                        parts.append(f"benchmark_scope_explanations={benchmark_scope_explanations}")
                    overall_verdict = review_summary_payload.get("overall_verdict")
                    if overall_verdict:
                        parts.append(f"overall_verdict={overall_verdict}")
                    summary_text = review_summary_payload.get("summary_text")
                    if summary_text:
                        parts.append(f"summary_text={summary_text}")
                parts.append(f"review_summary={str(review_summaries[0].get('summary') or '').splitlines()[-1]}")
            return "; ".join(parts)
        if intent != "sector_research":
            return None
        for item in evidences:
            hint = item.get("retrieval_hint") or {}
            if hint.get("hint_type") != "sector_signal_scorecard":
                continue
            sector = hint.get("sector") or "unknown_sector"
            parts = [
                f"Sector scorecard: sector={sector}",
                f"setup={hint.get('setup_label') or 'unknown'}",
                f"market_tone={hint.get('market_tone') or 'unknown'}",
                f"benchmark_view={hint.get('benchmark_view') or 'unknown'}",
                f"turnover_state={hint.get('turnover_state') or 'unknown'}",
                f"breadth_state={hint.get('breadth_state') or 'unknown'}",
            ]
            if hint.get("latest_pct_change") is not None:
                parts.append(f"latest_pct_change={hint.get('latest_pct_change')}")
            if hint.get("latest_excess_pct_change") is not None:
                parts.append(f"latest_excess_pct_change={hint.get('latest_excess_pct_change')}")
            if hint.get("up_ratio") is not None:
                parts.append(f"up_ratio={hint.get('up_ratio')}")
            if hint.get("median_pct_change") is not None:
                parts.append(f"median_pct_change={hint.get('median_pct_change')}")
            if hint.get("average_turnover_rate") is not None:
                parts.append(f"average_turnover_rate={hint.get('average_turnover_rate')}")
            parts.append(f"policy_signal_strength={hint.get('policy_signal_strength') or 'unknown'}")
            parts.append(f"regulatory_signal_strength={hint.get('regulatory_signal_strength') or 'unknown'}")
            if hint.get("top_policy_title"):
                parts.append(f"top_policy_signal={hint.get('top_policy_title')}")
            if hint.get("top_regulatory_title"):
                parts.append(f"top_regulatory_signal={hint.get('top_regulatory_title')}")
            return "; ".join(parts)
        return None

    def _merge_retrieval_summary(self, structured_summary: str | None, base_summary: str | None) -> str:
        if structured_summary and base_summary:
            if structured_summary in base_summary:
                return base_summary
            return f"{structured_summary}\n{base_summary}"
        if structured_summary:
            return structured_summary
        return base_summary or "Retrieval completed."

    def _review_summary_payload_from_evidence(self, item: dict) -> dict | None:
        summary = item.get("summary")
        if isinstance(summary, dict):
            return summary
        if not isinstance(summary, str):
            return None
        lines = summary.splitlines()
        tail = lines[-1].strip() if lines else summary.strip()
        if not tail.startswith("{") or not tail.endswith("}"):
            return None
        try:
            parsed = ast.literal_eval(tail)
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _dedupe_evidences(self, *, evidences: list[dict], current_run_id: str | None) -> list[dict]:
        best_by_key: dict[str, dict] = {}
        passthrough: list[dict] = []

        for item in evidences:
            dedupe_key = self._dedupe_key(item)
            if dedupe_key is None:
                passthrough.append(item)
                continue
            existing = best_by_key.get(dedupe_key)
            if existing is None or self._prefer_candidate(candidate=item, existing=existing, current_run_id=current_run_id):
                best_by_key[dedupe_key] = item

        return passthrough + list(best_by_key.values())

    def _dedupe_key(self, item: dict) -> str | None:
        metadata = item.get("metadata") or {}
        source_type = str(metadata.get("source_type") or "")
        if not source_type.endswith("_snapshot"):
            return None

        target = self._snapshot_target(item=item, source_type=source_type)
        if not target:
            return None
        return f"{source_type}:{target}"

    def _prefer_candidate(self, *, candidate: dict, existing: dict, current_run_id: str | None) -> bool:
        candidate_current = self._is_current_run(candidate, current_run_id)
        existing_current = self._is_current_run(existing, current_run_id)
        if candidate_current != existing_current:
            return candidate_current

        candidate_date = self._snapshot_date(candidate)
        existing_date = self._snapshot_date(existing)
        if candidate_date != existing_date:
            return candidate_date > existing_date

        candidate_score = candidate.get("score") or 0.0
        existing_score = existing.get("score") or 0.0
        return candidate_score > existing_score

    def _is_current_run(self, item: dict, current_run_id: str | None) -> bool:
        if not current_run_id:
            return False
        for ref in item.get("source_refs") or []:
            if str(ref.get("run_id") or "") == current_run_id:
                return True
        return False

    def _snapshot_date(self, item: dict) -> str:
        summary = str(item.get("summary") or "")
        marker = "snapshot_date: "
        if marker not in summary:
            return ""
        tail = summary.split(marker, 1)[1]
        return tail.splitlines()[0].strip()

    def _retrieval_hint_from_qdrant_payload(self, payload: dict) -> dict | None:
        source_type = str(payload.get("source_type") or "")
        if source_type != "sector_snapshot":
            return None
        sector = payload.get("sector")
        if not sector:
            sector = self._extract_snapshot_target_from_text(
                text=str(payload.get("content") or ""),
                prefix="sector snapshot for ",
            )
        if not sector:
            return None
        return {
            "hint_type": "sector_signal_scorecard",
            "sector": sector,
        }

    def _snapshot_target(self, *, item: dict, source_type: str) -> str | None:
        hint = item.get("retrieval_hint") or {}
        if source_type == "sector_snapshot":
            sector = hint.get("sector") or self._extract_snapshot_target(item=item, prefix="sector snapshot for ")
            if sector:
                return f"sector:{sector}"
            return None

        target_kind = source_type.removesuffix("_snapshot")
        target = self._extract_snapshot_target(item=item, prefix=f"{target_kind} snapshot for ")
        if target:
            return f"target:{target}"
        return None

    def _extract_snapshot_target(self, *, item: dict, prefix: str) -> str | None:
        title_target = self._extract_target_from_title(str(item.get("title") or ""))
        if title_target:
            return title_target
        return self._extract_snapshot_target_from_text(
            text=str(item.get("summary") or ""),
            prefix=prefix,
        )

    def _extract_target_from_title(self, title: str) -> str | None:
        if ":" not in title:
            return None
        _, tail = title.split(":", 1)
        target = tail.strip()
        return target or None

    def _extract_snapshot_target_from_text(self, *, text: str, prefix: str) -> str | None:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(prefix):
                target = stripped.removeprefix(prefix).strip()
                if target and target not in {"unknown sector", "unknown symbol"}:
                    return target
        return None

    def _select_distillation_evidences(self, *, evidences: list[dict], top_k: int) -> list[dict]:
        ranked = sorted(
            evidences,
            key=lambda item: (
                self._distillation_priority(item),
                item.get("score") or 0.0,
            ),
            reverse=True,
        )

        summary_items = [
            item for item in ranked
            if str((item.get("metadata") or {}).get("source_type") or "") == "distillation_summary"
        ]
        scorecard_items = [
            item for item in ranked
            if str((item.get("metadata") or {}).get("source_type") or "") == "distillation_scorecard"
        ]
        logic_items = [
            item for item in ranked
            if str((item.get("metadata") or {}).get("source_type") or "") == "logic_block"
        ]
        dimension_items = [
            item for item in ranked
            if str((item.get("metadata") or {}).get("source_type") or "") == "distilled_dimension"
        ]
        review_items = [
            item for item in ranked
            if str((item.get("metadata") or {}).get("source_type") or "") == "review_finding"
        ]
        other_items = [
            item for item in ranked
            if str((item.get("metadata") or {}).get("source_type") or "") not in {
                "distillation_summary",
                "distillation_scorecard",
                "logic_block",
                "distilled_dimension",
                "review_finding",
            }
        ]

        selected: list[dict] = []
        selected.extend(summary_items[:1])
        if scorecard_items and len(selected) < top_k:
            selected.extend(scorecard_items[:1])

        reserved_slots = 0
        if review_items:
            reserved_slots += 1
        if dimension_items:
            reserved_slots += 1
        logic_budget = max(top_k - len(selected) - reserved_slots, 0)
        selected.extend(logic_items[: min(3, logic_budget)])

        if review_items and len(selected) < top_k:
            selected.extend(review_items[:1])
        if dimension_items and len(selected) < top_k:
            selected.extend(dimension_items[:1])

        for item in other_items + review_items[1:] + dimension_items[1:] + logic_items[3:] + scorecard_items[1:] + summary_items[1:]:
            if len(selected) >= top_k:
                break
            if item not in selected:
                selected.append(item)
        return selected[:top_k]

    def _select_review_evidences(self, *, evidences: list[dict], top_k: int) -> list[dict]:
        ranked = sorted(
            evidences,
            key=lambda item: (
                self._review_priority(item),
                item.get("score") or 0.0,
            ),
            reverse=True,
        )
        selected: list[dict] = []
        for item in ranked:
            if len(selected) >= top_k:
                break
            if item not in selected:
                selected.append(item)
        return selected[:top_k]

    def _distillation_priority(self, item: dict) -> int:
        source_type = str((item.get("metadata") or {}).get("source_type") or "")
        title = str(item.get("title") or "")
        if source_type == "logic_block":
            if title.endswith(":claim"):
                return 40
            if title.endswith(":catalyst"):
                return 35
            if title.endswith(":evidence"):
                return 30
            if title.endswith(":risk"):
                return 25
            return 20
        if source_type == "distilled_dimension":
            return 15
        if source_type == "review_finding":
            title = str(item.get("title") or "")
            if title.endswith(":fallback_distillation"):
                return 18
            if "severity=high" in str(item.get("summary") or ""):
                return 17
            return 16
        if source_type == "distillation_scorecard":
            return 19
        if source_type == "distillation_summary":
            return 10
        if source_type == "xueqiu_status":
            return 5
        return 1

    def _review_priority(self, item: dict) -> int:
        source_type = str((item.get("metadata") or {}).get("source_type") or "")
        title = str(item.get("title") or "")
        if source_type == "review_finding":
            summary = str(item.get("summary") or "")
            if "verdict=confirmed" in summary:
                return 40
            if "verdict=rejected" in summary:
                return 38
            return 35
        if source_type == "review_summary":
            return 30
        if source_type == "sector_snapshot":
            return 20
        if title.startswith("report_fragment"):
            return 10
        return 1
