from __future__ import annotations

from pathlib import Path

from research_system.runtime.storage.jsonl import read_json, write_json
from research_system.schemas.subagents import (
    SubagentActivationEntry,
    SubagentCatalogEntry,
    SubagentCapabilityGap,
    SubagentDiscoveryTicket,
    SubagentToolDiscoveryRequest,
)


class SubagentDiscoveryStore:
    STATUSES = ("pending", "approved", "rejected")

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, status: str, ticket_id: str) -> Path:
        return self.root / status / f"{ticket_id}.json"

    def create_request(
        self,
        *,
        gap: SubagentCapabilityGap,
        request: SubagentToolDiscoveryRequest,
    ) -> SubagentDiscoveryTicket:
        ticket = SubagentDiscoveryTicket(
            ticket_id=request.discovery_request_id,
            status="pending",
            capability_gap=gap,
            tool_discovery_request=request,
            notes=[
                "created_from_runtime_capability_gap",
                f"gap_id={gap.gap_id}",
            ],
        )
        write_json(self._path(ticket.status, ticket.ticket_id), ticket.model_dump(mode="json"))
        return ticket

    def list(self, status: str) -> list[SubagentDiscoveryTicket]:
        root = self.root / status
        if not root.exists():
            return []
        tickets: list[SubagentDiscoveryTicket] = []
        for path in sorted(root.glob("*.json")):
            payload = read_json(path)
            if payload is None:
                continue
            tickets.append(SubagentDiscoveryTicket.model_validate(payload))
        return tickets

    def list_all(self) -> list[SubagentDiscoveryTicket]:
        tickets: list[SubagentDiscoveryTicket] = []
        for status in self.STATUSES:
            tickets.extend(self.list(status))
        tickets.sort(key=lambda item: item.updated_at, reverse=True)
        return tickets

    def load(self, status: str, ticket_id: str) -> SubagentDiscoveryTicket | None:
        payload = read_json(self._path(status, ticket_id))
        if payload is None:
            return None
        return SubagentDiscoveryTicket.model_validate(payload)

    def find(self, ticket_id: str) -> tuple[SubagentDiscoveryTicket, str] | None:
        for status in self.STATUSES:
            ticket = self.load(status, ticket_id)
            if ticket is not None:
                return ticket, status
        return None

    def approved_catalog_entries(self) -> list[SubagentCatalogEntry]:
        entries: list[SubagentCatalogEntry] = []
        for ticket in self.list("approved"):
            activated_types = {
                str(item.subagent_type).strip()
                for item in ticket.activated_entries
                if str(item.status or "").strip().lower() == "active"
            }
            for entry in ticket.candidate_catalog_entries:
                if str(entry.subagent_type).strip() in activated_types:
                    continue
                entries.append(self._normalize_candidate_entry(ticket, entry))
        return entries

    def activated_alias_entries(self) -> list[SubagentActivationEntry]:
        rows: list[SubagentActivationEntry] = []
        for ticket in self.list("approved"):
            for entry in ticket.activated_entries:
                if str(entry.status or "").strip().lower() != "active":
                    continue
                rows.append(self._normalize_activation_entry(ticket, entry))
        return rows

    def save(self, ticket: SubagentDiscoveryTicket, *, previous_status: str | None = None) -> SubagentDiscoveryTicket:
        old_status = previous_status or ticket.status
        if old_status != ticket.status:
            old_path = self._path(old_status, ticket.ticket_id)
            if old_path.exists():
                old_path.unlink()
        write_json(self._path(ticket.status, ticket.ticket_id), ticket.model_dump(mode="json"))
        return ticket

    @staticmethod
    def _normalize_candidate_entry(
        ticket: SubagentDiscoveryTicket,
        entry: SubagentCatalogEntry,
    ) -> SubagentCatalogEntry:
        tags = list(entry.tags or [])
        if "approved_discovery_candidate" not in tags:
            tags.append("approved_discovery_candidate")
        ticket_tag = f"discovery_ticket:{ticket.ticket_id}"
        if ticket_tag not in tags:
            tags.append(ticket_tag)
        return entry.model_copy(
            update={
                "tags": tags,
            },
            deep=True,
        )

    @staticmethod
    def _normalize_activation_entry(
        ticket: SubagentDiscoveryTicket,
        entry: SubagentActivationEntry,
    ) -> SubagentActivationEntry:
        tags = list(entry.catalog_entry.tags or [])
        if "activated_runtime_alias" not in tags:
            tags.append("activated_runtime_alias")
        ticket_tag = f"discovery_ticket:{ticket.ticket_id}"
        if ticket_tag not in tags:
            tags.append(ticket_tag)
        return entry.model_copy(
            update={
                "catalog_entry": entry.catalog_entry.model_copy(
                    update={"tags": tags},
                    deep=True,
                )
            },
            deep=True,
        )


__all__ = ["SubagentDiscoveryStore"]
