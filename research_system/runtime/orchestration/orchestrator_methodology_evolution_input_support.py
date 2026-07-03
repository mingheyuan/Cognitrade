from __future__ import annotations

from research_system.runtime.storage.jsonl import read_jsonl
from research_system.schemas.protocol import RequestEnvelope


class OrchestratorMethodologyEvolutionInputSupport:
    def __init__(self, *, run_store, methodology_evolver, memory_service) -> None:
        self.run_store = run_store
        self.methodology_evolver = methodology_evolver
        self.memory_service = memory_service

    def collect_inputs(self, *, request: RequestEnvelope, target_run_ids: list[str]) -> dict[str, object]:
        methodology_memory_rows = self.memory_service.read_recent_methodology_memory(limit_per_type=3)
        run_inputs = self.run_inputs(target_run_ids)
        search_terms = self.search_terms(request=request, run_inputs=run_inputs)
        methodology_memory_search_rows = self.memory_service.search_methodology_memory(
            query="\n".join(term for term in search_terms if term),
            limit=12,
        )
        candidates = self.methodology_evolver.build_candidates_from_runs(
            runs=run_inputs,
            methodology_memory_rows=methodology_memory_rows,
            methodology_memory_search_rows=methodology_memory_search_rows,
        )
        return {
            "run_inputs": run_inputs,
            "candidates": candidates,
        }

    def run_inputs(self, target_run_ids: list[str]) -> list[dict]:
        return [
            {
                "run_id": target_run_id,
                "blackboard_entries": read_jsonl(self.run_store.load_run_paths(target_run_id).blackboard),
            }
            for target_run_id in target_run_ids
        ]

    def search_terms(self, *, request: RequestEnvelope, run_inputs: list[dict]) -> list[str]:
        terms: list[str] = [str(request.user_query or "").strip()]
        for run_input in run_inputs:
            for entry in list(run_input.get("blackboard_entries") or []):
                if not isinstance(entry, dict):
                    continue
                payload = entry.get("payload") or {}
                if entry.get("kind") == "distilled_dimension":
                    candidate_title = str(payload.get("canonical_dimension_name") or payload.get("dimension_name") or "").strip()
                    if candidate_title:
                        terms.append(candidate_title)
                if entry.get("kind") == "source_record" and str(payload.get("source_type") or "").startswith("methodology_"):
                    candidate_title = str(payload.get("title") or payload.get("methodology_type") or "").strip()
                    if candidate_title:
                        terms.append(candidate_title)
        return terms
