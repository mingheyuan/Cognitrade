from __future__ import annotations

from research_system.schemas.runtime import BlackboardEntry


class OrchestratorMethodologyEntryFactory:
    def methodology_entry(
        self,
        *,
        context,
        request,
        phase: str,
        agent: str,
        methodology_type: str,
        title: str,
        payload: dict,
        target_symbol: str,
        source_refs: list[dict],
    ) -> BlackboardEntry:
        valuation_band = payload.get("valuation_band") or {}
        output_payload = {
            "title": title,
            "content": str(payload),
            "source_type": f"methodology_{methodology_type}",
            "methodology_type": methodology_type,
            "symbol": target_symbol,
            "methodology_tags": [methodology_type, *list(payload.get("assumption_tags") or [])],
            "scenario_tags": list(payload.get("sensitivity_tags") or []),
            "peer_group_tags": list(payload.get("peer_group_tags") or []),
            "assumption_tags": list(payload.get("assumption_tags") or []),
            "sensitivity_tags": list(payload.get("sensitivity_tags") or []),
            "valuation_band": valuation_band,
            "valuation_band_label": self.valuation_band_label(valuation_band),
            "methodology_result": payload,
        }
        return BlackboardEntry(
            run_id=context.run_id,
            thread_id=context.thread.thread_id,
            request_id=request.request_id,
            kind="source_record",
            agent=agent,
            phase=phase,
            payload=output_payload,
            source_refs=source_refs,
            confidence=payload.get("confidence"),
            risk_flags=list(payload.get("warnings") or []),
        )

    def valuation_band_label(self, valuation_band: dict) -> str | None:
        if not isinstance(valuation_band, dict):
            return None
        low = valuation_band.get("low")
        base = valuation_band.get("base")
        high = valuation_band.get("high")
        if low is None and base is None and high is None:
            return None
        return f"{low}|{base}|{high}"
