from __future__ import annotations

from typing import Iterable

from research_system.schemas.runtime import BlackboardEntry, GateVerdict


class GateEvaluator:
    def evaluate(
        self,
        *,
        run_id: str,
        phase: str,
        entries: Iterable[BlackboardEntry],
        requires_verified_snapshot: bool = False,
        requires_review_findings: bool = False,
    ) -> list[GateVerdict]:
        entries = list(entries)
        verdicts = [
            self._evidence_gate(run_id=run_id, entries=entries),
            self._quality_gate(run_id=run_id, entries=entries),
        ]
        if requires_verified_snapshot:
            verdicts.append(self._verified_fact_gate(run_id=run_id, entries=entries))
        if requires_review_findings:
            verdicts.append(self._review_gate(run_id=run_id, entries=entries))
        return verdicts

    def _evidence_gate(self, *, run_id: str, entries: list[BlackboardEntry]) -> GateVerdict:
        has_evidence = any(entry.kind in {"source_record", "verified_snapshot", "retrieved_evidence"} for entry in entries)
        if has_evidence:
            return GateVerdict(run_id=run_id, gate_type="EvidenceGate", verdict="pass", reason="at least one evidence source is present")
        return GateVerdict(
            run_id=run_id,
            gate_type="EvidenceGate",
            verdict="fail",
            reason="no evidence found for current run",
            blocking_items=["missing_evidence"],
        )

    def _quality_gate(self, *, run_id: str, entries: list[BlackboardEntry]) -> GateVerdict:
        has_report = any(entry.kind == "report_fragment" for entry in entries)
        has_risk = any("risk" in entry.payload for entry in entries if entry.kind == "report_fragment")
        if has_report and has_risk:
            return GateVerdict(run_id=run_id, gate_type="QualityGate", verdict="pass", reason="report includes conclusion and risk content")
        missing: list[str] = []
        if not has_report:
            missing.append("missing_report_fragment")
        if not has_risk:
            missing.append("missing_risk")
        return GateVerdict(
            run_id=run_id,
            gate_type="QualityGate",
            verdict="fail",
            reason="formal output must include conclusion, evidence, and risk",
            blocking_items=missing,
        )

    def _verified_fact_gate(self, *, run_id: str, entries: list[BlackboardEntry]) -> GateVerdict:
        has_snapshot = any(entry.kind == "verified_snapshot" for entry in entries)
        if has_snapshot:
            return GateVerdict(run_id=run_id, gate_type="VerifiedFactGate", verdict="pass", reason="verified snapshot present")
        return GateVerdict(
            run_id=run_id,
            gate_type="VerifiedFactGate",
            verdict="needs_review",
            reason="exact claims require verified snapshot support",
            blocking_items=["missing_verified_snapshot"],
        )

    def _review_gate(self, *, run_id: str, entries: list[BlackboardEntry]) -> GateVerdict:
        review_findings = [entry for entry in entries if entry.kind == "review_finding"]
        review_summaries = [
            entry
            for entry in entries
            if entry.kind == "source_record" and entry.payload.get("source_type") == "review_summary"
        ]
        if review_findings:
            return GateVerdict(
                run_id=run_id,
                gate_type="ReviewGate",
                verdict="pass",
                reason=f"posterior review findings recorded: {len(review_findings)}",
            )

        summary_payload = review_summaries[-1].payload.get("summary") if review_summaries else None
        if isinstance(summary_payload, dict):
            review_required = bool(summary_payload.get("review_required"))
            finding_count = int(summary_payload.get("finding_count") or 0)
            notes = [str(note) for note in summary_payload.get("notes") or []]
            if review_required or finding_count == 0:
                blocking_items = notes or ["missing_review_findings"]
                return GateVerdict(
                    run_id=run_id,
                    gate_type="ReviewGate",
                    verdict="needs_review",
                    reason="posterior review summary recorded, but no review finding was confirmed into the run",
                    blocking_items=blocking_items,
                )

        return GateVerdict(
            run_id=run_id,
            gate_type="ReviewGate",
            verdict="fail",
            reason="review_thesis requires review findings or a review summary artifact",
            blocking_items=["missing_review_output"],
        )
