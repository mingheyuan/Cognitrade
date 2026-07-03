from __future__ import annotations

from research_system.schemas.protocol import RequestEnvelope


class PlannerContextHints:
    def extract(self, request: RequestEnvelope) -> dict[str, str]:
        hints: dict[str, str] = {}
        for item in request.conversation_context:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "").strip()
            if item_type == "chat_thread_profile":
                self._merge_thread_profile(hints, item, overwrite=True)
                continue
            if item_type == "relevant_thread_profile":
                self._merge_thread_profile(hints, item, overwrite=False)
                continue
            if item_type == "relevant_methodology_memory":
                benchmark_scope = str(item.get("benchmark_scope") or "").strip()
                memory_type = str(item.get("memory_type") or "").strip()
                match_score = str(item.get("match_score") or "").strip()
                if benchmark_scope and "methodology_benchmark_scope" not in hints:
                    hints["methodology_benchmark_scope"] = benchmark_scope
                if memory_type and "methodology_memory_type" not in hints:
                    hints["methodology_memory_type"] = memory_type
                if match_score and "methodology_memory_match_score" not in hints:
                    hints["methodology_memory_match_score"] = match_score
        return hints

    def merge_entry_reason(self, base_reason: str | None, context_hints: dict[str, str]) -> str | None:
        base = str(base_reason or "").strip()
        if not context_hints:
            return base or None
        fragments: list[str] = [base] if base else []
        if context_hints.get("recommended_next_step"):
            fragments.append(f"thread_next_step={context_hints['recommended_next_step']}")
        if context_hints.get("open_question"):
            fragments.append(f"thread_open_question={context_hints['open_question']}")
        if context_hints.get("thread_topic"):
            fragments.append(f"thread_topic={context_hints['thread_topic']}")
        return " | ".join(fragments) if fragments else None

    def planning_inputs(self, context_hints: dict[str, str] | None) -> dict[str, str]:
        return {
            str(key): str(value)
            for key, value in (context_hints or {}).items()
            if str(key).strip() and str(value).strip()
        }

    def _merge_thread_profile(self, hints: dict[str, str], item: dict, *, overwrite: bool) -> None:
        mappings = {
            "topic_title": "thread_topic",
            "topic_intent": "thread_intent",
            "last_result_summary": "previous_summary",
            "recommended_next_step": "recommended_next_step",
            "open_question": "open_question",
            "thread_status": "thread_status",
        }
        for raw_key, hint_key in mappings.items():
            value = str(item.get(raw_key) or "").strip()
            if not value:
                continue
            if overwrite or hint_key not in hints:
                hints[hint_key] = value
