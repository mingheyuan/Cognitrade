from __future__ import annotations

from datetime import datetime, timezone

from research_system.schemas.subagents import SubagentCatalogEntry


class OrchestratorSubagentDiscoveryAdminSupport:
    def __init__(self, host) -> None:
        self.host = host

    def list_requests(self, *, status: str | None = None, limit: int = 50) -> list[dict]:
        store = self.host.require_subagent_discovery_store()
        if status:
            tickets = store.list(status)
            tickets.sort(key=lambda item: item.updated_at, reverse=True)
        else:
            tickets = store.list_all()
        return [self._summary_row(ticket) for ticket in tickets[:limit]]

    def show_request(self, ticket_id: str) -> dict:
        located = self.host.require_subagent_discovery_store().find(ticket_id)
        if located is None:
            raise self.host.orchestrator_error_cls(f"subagent discovery ticket not found: {ticket_id}")
        ticket, stored_status = located
        payload = ticket.model_dump(mode="json")
        payload["stored_status"] = stored_status
        return payload

    def review_request(
        self,
        ticket_id: str,
        *,
        decision: str,
        reviewer: str = "manual_cli_review",
        reason: str | None = None,
        candidate_catalog_entries: list[dict] | None = None,
    ) -> dict:
        normalized_decision = str(decision or "").strip().lower()
        if normalized_decision not in {"approve", "reject"}:
            raise self.host.orchestrator_error_cls(f"unsupported discovery review decision: {decision}")
        located = self.host.require_subagent_discovery_store().find(ticket_id)
        if located is None:
            raise self.host.orchestrator_error_cls(f"subagent discovery ticket not found: {ticket_id}")
        ticket, previous_status = located
        if ticket.status in {"approved", "rejected"}:
            current = self.show_request(ticket.ticket_id)
            return {
                "ticket_id": ticket.ticket_id,
                "status": ticket.status,
                "message": f"discovery ticket already {ticket.status}",
                "result": current,
            }
        reviewed_at = datetime.now(timezone.utc)
        ticket.status = "approved" if normalized_decision == "approve" else "rejected"
        ticket.reviewer = reviewer
        ticket.review_reason = str(reason or f"manual_{ticket.status}_via_cli")
        ticket.reviewed_at = reviewed_at
        ticket.updated_at = reviewed_at
        if ticket.status == "approved":
            ticket.candidate_catalog_entries = self._resolve_candidate_catalog_entries(
                ticket,
                candidate_catalog_entries=candidate_catalog_entries,
            )
        self.host.require_subagent_discovery_store().save(ticket, previous_status=previous_status)
        return {
            "ticket_id": ticket.ticket_id,
            "status": ticket.status,
            "message": f"discovery ticket {ticket.status}",
            "result": self.show_request(ticket.ticket_id),
        }

    @staticmethod
    def _summary_row(ticket) -> dict:
        return {
            "ticket_id": ticket.ticket_id,
            "status": ticket.status,
            "intent": ticket.capability_gap.intent,
            "stage": ticket.capability_gap.stage,
            "capability": ticket.capability_gap.capability,
            "search_query": ticket.tool_discovery_request.search_query,
            "candidate_entry_count": len(ticket.candidate_catalog_entries),
            "updated_at": ticket.updated_at.isoformat(),
        }

    @staticmethod
    def _resolve_candidate_catalog_entries(
        ticket,
        *,
        candidate_catalog_entries: list[dict] | None,
    ) -> list[SubagentCatalogEntry]:
        if candidate_catalog_entries:
            return [
                SubagentCatalogEntry.model_validate(item)
                for item in candidate_catalog_entries
                if isinstance(item, dict)
            ]
        gap = ticket.capability_gap
        candidate_type = str(gap.preferred_subagent_type or "").strip()
        if not candidate_type:
            stage = str(gap.stage or "phase").strip() or "phase"
            capability = str(gap.capability or "capability").strip() or "capability"
            candidate_type = f"candidate_{stage}_{capability}".replace("-", "_").replace(" ", "_")
        summary = f"Approved discovery candidate for capability {gap.capability}"
        suitable_intents = [str(gap.intent).strip()] if str(gap.intent or "").strip() else []
        return [
            SubagentCatalogEntry(
                subagent_type=candidate_type,
                summary=summary,
                capabilities=[str(gap.capability).strip()],
                stage=str(gap.stage or "").strip() or None,
                cost_class="medium",
                latency_class="medium",
                side_effect_level="none",
                suitable_intents=suitable_intents,
                tags=["approved_discovery_candidate", "pending_runtime_registration"],
                requires_llm=bool(gap.requires_llm),
                allow_parallel=False,
                max_concurrency=None,
                fallback_subagent_type=None,
                error_fallback_subagent_type=None,
                input_schema_name=None,
                output_schema_name=None,
                input_json_schema=None,
                output_json_schema=None,
            )
        ]


__all__ = ["OrchestratorSubagentDiscoveryAdminSupport"]
