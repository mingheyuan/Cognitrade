from __future__ import annotations

from pathlib import Path

from research_system.runtime.storage.jsonl import append_jsonl, read_jsonl
from research_system.schemas.runtime import MailboxMessage


class Mailbox:
    def __init__(self, path: Path) -> None:
        self.path = path

    def send(self, message: MailboxMessage) -> MailboxMessage:
        append_jsonl(self.path, message.model_dump(mode="json"))
        return message

    def list_messages(self, *, to_agent: str | None = None) -> list[MailboxMessage]:
        rows = [MailboxMessage.model_validate(row) for row in read_jsonl(self.path)]
        if to_agent is None:
            return rows
        return [row for row in rows if row.to_agent == to_agent]
