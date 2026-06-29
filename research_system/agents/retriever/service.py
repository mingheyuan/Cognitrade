from __future__ import annotations

import ast

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from research_system.adapters.storage.qdrant_adapter import QdrantAdapter, QdrantUnavailableError
from research_system.runtime.provider_registry import LLMProviderError, OpenAIProvider
from research_system.runtime.settings import Settings
from research_system.schemas.subagents import SubagentInvocation
from research_system.subagents.registry import SubagentRegistry
from research_system.subagents.retrieval import (
    BlackboardEvidenceIndexerSubagent,
    EvidenceDeduperSubagent,
    QdrantEvidenceSearcherSubagent,
    RetrievalDecisionSubagent,
)


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
        self.registry = SubagentRegistry(
            [
                BlackboardEvidenceIndexerSubagent(
                    qdrant=self.qdrant,
                    to_blackboard_entry=self._to_blackboard_entry,
                    evidence_from_blackboard_entry=self._evidence_from_blackboard_entry,
                ),
                QdrantEvidenceSearcherSubagent(
                    qdrant=self.qdrant,
                    retrieval_hint_from_qdrant_payload=self._retrieval_hint_from_qdrant_payload,
                ),
                EvidenceDeduperSubagent(dedupe_evidences=self._dedupe_evidences),
                RetrievalDecisionSubagent(
                    llm_provider=self.llm_provider,
                    structured_retrieval_summary=self._structured_retrieval_summary,
                    merge_retrieval_summary=self._merge_retrieval_summary,
                    combine_degrade_reason=self._combine_degrade_reason,
                    fallback_summary=self._fallback_summary,
                    select_screening_evidences=self._select_screening_evidences,
                    select_distillation_evidences=self._select_distillation_evidences,
                    select_review_evidences=self._select_review_evidences,
                    retrieval_decision_schema=RetrievalDecision,
                ),
            ]
        )

    def close(self) -> None:
        seen_ids: set[int] = set()
        candidates = [self.qdrant]
        for subagent in self.registry._subagents.values():
            qdrant = getattr(subagent, "qdrant", None)
            candidates.append(qdrant)
        for adapter in candidates:
            if adapter is None:
                continue
            adapter_id = id(adapter)
            if adapter_id in seen_ids:
                continue
            seen_ids.add(adapter_id)
            close = getattr(adapter, "close", None)
            if callable(close):
                close()

    def run(self, *, intent: str, query: str, blackboard_entries: list[dict]) -> dict:
        indexer_qdrant = getattr(self.registry._subagents.get("blackboard_evidence_indexer"), "qdrant", None)
        searcher_qdrant = getattr(self.registry._subagents.get("qdrant_evidence_searcher"), "qdrant", None)
        effective_qdrant_available = bool(self.qdrant_available and self.qdrant is not None and indexer_qdrant is not None and searcher_qdrant is not None)
        index_outputs, _ = self.registry.invoke(
            SubagentInvocation(
                subagent_type="blackboard_evidence_indexer",
                objective="Index blackboard entries and expose evidence candidates.",
                inputs={"blackboard_entries": blackboard_entries},
            )
        )
        evidence_map = dict(index_outputs["evidence_map"])
        symbol = index_outputs.get("symbol")
        sector = index_outputs.get("sector")
        current_run_id = index_outputs.get("current_run_id")
        top_k = int(self.retrieval_policy.get("final_top_k", 5))
        qdrant_degraded_reason = None
        should_skip_qdrant = intent == "stock_screen"
        if effective_qdrant_available and not should_skip_qdrant:
            search_outputs, _ = self.registry.invoke(
                SubagentInvocation(
                    subagent_type="qdrant_evidence_searcher",
                    objective="Search indexed evidence candidates from Qdrant.",
                    inputs={
                        "query": query,
                        "top_k": top_k,
                        "symbol": symbol,
                        "sector": sector,
                        "current_run_id": current_run_id,
                        "evidence_map": evidence_map,
                    },
                )
            )
            evidence_map = dict(search_outputs["evidence_map"])
        elif not effective_qdrant_available:
            qdrant_degraded_reason = self.qdrant_error or "qdrant_unavailable"

        dedupe_outputs, _ = self.registry.invoke(
            SubagentInvocation(
                subagent_type="evidence_deduper",
                objective="Deduplicate evidence candidates with current-run preference.",
                inputs={
                    "evidences": list(evidence_map.values()),
                    "current_run_id": current_run_id,
                },
            )
        )
        evidences = dedupe_outputs["evidences"]
        decision_outputs, _ = self.registry.invoke(
            SubagentInvocation(
                subagent_type="retrieval_decider",
                objective="Select final evidence set and summarize retrieval.",
                inputs={
                    "intent": intent,
                    "query": query,
                    "evidences": evidences,
                    "top_k": top_k,
                    "qdrant_degraded_reason": qdrant_degraded_reason,
                },
            )
        )
        return decision_outputs

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
        if entry["kind"] == "source_record" and payload.get("source_type") == "screen_candidate":
            candidate = payload.get("candidate") or {}
            return f"screen_candidate:{candidate.get('symbol') or 'unknown'}"
        if entry["kind"] == "source_record" and payload.get("source_type") == "watchlist_candidate":
            candidate = payload.get("candidate") or {}
            return f"watchlist_candidate:{candidate.get('symbol') or 'unknown'}"
        if entry["kind"] == "source_record" and payload.get("source_type") == "screening_scorecard":
            return "screening_scorecard"
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
        if entry["kind"] == "source_record" and payload.get("source_type") == "screen_candidate":
            candidate = payload.get("candidate") or {}
            parts = [
                f"screen_candidate symbol={candidate.get('symbol')} name={candidate.get('name')}",
                f"score={candidate.get('score')} latest_pct_change={candidate.get('latest_pct_change')}",
                f"latest_amount={candidate.get('latest_amount')} notice_count={candidate.get('notice_count')}",
                f"deep_checked={candidate.get('deep_checked')}",
                f"why={candidate.get('why_it_made_the_list')}",
                f"risk={candidate.get('main_risk')}",
            ]
            if candidate.get("missing_evidence"):
                parts.append(f"missing_evidence={candidate.get('missing_evidence')}")
            return "\n".join(parts)
        if entry["kind"] == "source_record" and payload.get("source_type") == "watchlist_candidate":
            candidate = payload.get("candidate") or {}
            parts = [
                f"watchlist_candidate symbol={candidate.get('symbol')} name={candidate.get('name')}",
                f"score={candidate.get('score')} latest_pct_change={candidate.get('latest_pct_change')}",
                f"latest_amount={candidate.get('latest_amount')} notice_count={candidate.get('notice_count')}",
                f"deep_checked={candidate.get('deep_checked')}",
                f"why={candidate.get('why_it_made_the_list')}",
                f"risk={candidate.get('main_risk')}",
            ]
            if candidate.get("missing_evidence"):
                parts.append(f"missing_evidence={candidate.get('missing_evidence')}")
            return "\n".join(parts)
        if entry["kind"] == "source_record" and payload.get("source_type") == "screening_scorecard":
            scorecard = payload.get("scorecard") or {}
            return f"screening_scorecard\n{scorecard}"
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
        if intent == "stock_screen":
            candidates = [
                item for item in evidences
                if str((item.get("metadata") or {}).get("source_type") or "") == "screen_candidate"
            ]
            watchlist_candidates = [
                item for item in evidences
                if str((item.get("metadata") or {}).get("source_type") or "") == "watchlist_candidate"
            ]
            scorecards = [
                item for item in evidences
                if str((item.get("metadata") or {}).get("source_type") or "") == "screening_scorecard"
            ]
            review_summaries = [
                item for item in evidences
                if str((item.get("metadata") or {}).get("source_type") or "") == "review_summary"
            ]
            if not candidates and not watchlist_candidates and not scorecards:
                return None
            parts = [f"Stock screening: candidates={len(candidates)}"]
            if watchlist_candidates:
                parts.append(f"watchlist_candidates={len(watchlist_candidates)}")
            top_symbols: list[str] = []
            top_names: list[str] = []
            best_score = None
            for item in candidates[:3]:
                summary = str(item.get("summary") or "")
                if "symbol=" in summary:
                    top_symbols.append(summary.split("symbol=", 1)[1].split()[0].strip())
                if "name=" in summary:
                    top_names.append(summary.split("name=", 1)[1].split()[0].strip())
                if "score=" in summary and best_score is None:
                    best_score = summary.split("score=", 1)[1].split()[0].strip()
            if top_names:
                parts.append(f"top_names={top_names}")
            elif top_symbols:
                parts.append(f"top_symbols={top_symbols}")
            if best_score is not None:
                parts.append(f"best_score={best_score}")
            if scorecards:
                scorecard_payload = self._screening_scorecard_payload_from_evidence(scorecards[0])
                if isinstance(scorecard_payload, dict):
                    if scorecard_payload.get("investment_cycle"):
                        parts.append(f"investment_cycle={scorecard_payload.get('investment_cycle')}")
                    if scorecard_payload.get("candidate_count") is not None:
                        parts.append(f"candidate_count={scorecard_payload.get('candidate_count')}")
                    if scorecard_payload.get("summary"):
                        parts.append(f"summary={scorecard_payload.get('summary')}")
            if review_summaries:
                review_summary_payload = self._review_summary_payload_from_evidence(review_summaries[0])
                if isinstance(review_summary_payload, dict):
                    overall_verdict = review_summary_payload.get("overall_verdict")
                    if overall_verdict:
                        parts.append(f"overall_verdict={overall_verdict}")
                    summary_text = review_summary_payload.get("summary_text")
                    if summary_text:
                        parts.append(f"review_summary={summary_text}")
            return "; ".join(parts)
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

    def _screening_scorecard_payload_from_evidence(self, item: dict) -> dict | None:
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

    def _select_screening_evidences(self, *, evidences: list[dict], top_k: int) -> list[dict]:
        screen_candidates = [
            item for item in evidences
            if str((item.get("metadata") or {}).get("source_type") or "") == "screen_candidate"
        ]
        watchlist_candidates = [
            item for item in evidences
            if str((item.get("metadata") or {}).get("source_type") or "") == "watchlist_candidate"
        ]
        screening_scorecards = [
            item for item in evidences
            if str((item.get("metadata") or {}).get("source_type") or "") == "screening_scorecard"
        ]
        review_summaries = [
            item for item in evidences
            if str((item.get("metadata") or {}).get("source_type") or "") == "review_summary"
        ]
        sector_snapshots = [
            item for item in evidences
            if str((item.get("metadata") or {}).get("source_type") or "") == "sector_snapshot"
        ]
        selected: list[dict] = []
        if screening_scorecards:
            selected.append(screening_scorecards[0])
        if review_summaries and len(selected) < top_k:
            selected.append(review_summaries[0])
        selected.extend(screen_candidates[: max(top_k - len(selected) - (1 if sector_snapshots else 0), 0)])
        remaining = max(top_k - len(selected) - (1 if sector_snapshots else 0), 0)
        if remaining > 0:
            selected.extend(watchlist_candidates[:remaining])
        if sector_snapshots and len(selected) < top_k:
            selected.append(sector_snapshots[0])
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
