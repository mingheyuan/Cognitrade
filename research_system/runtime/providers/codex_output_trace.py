"""Bounded output-shape diagnostics; never persist model text or reasoning."""

from collections import OrderedDict
from dataclasses import dataclass, field
from hashlib import sha256


def _repeated_period(text: str) -> int | None:
    """Find an exact period in a bounded suffix, independent of chunk alignment.

    Require at least four copies across 4096 characters. This is a transport
    degeneration heuristic, not a maximum report length or native quality rule.
    Prefix-function matching is linear, including adversarial near-repetition.
    """
    if len(text) < 4096:
        return None
    prefix = [0] * len(text)
    for index in range(1, len(text)):
        matched = prefix[index - 1]
        while matched and text[index] != text[matched]:
            matched = prefix[matched - 1]
        if text[index] == text[matched]:
            matched += 1
        prefix[index] = matched
    period = len(text) - prefix[-1]
    return period if period <= len(text) // 4 else None


@dataclass
class _Message:
    ordinal: int
    phase: str = "unknown"
    completed: bool = False
    characters: int = 0
    whitespace_characters: int = 0
    repeated_blocks: int = 0
    max_repeated_blocks: int = 0
    previous_block_hash: str | None = None
    pending: str = ""
    repetition_tail: str = ""
    repeated_period: int | None = None
    digest: object = field(default_factory=sha256)

    def append(self, delta: str) -> None:
        self.characters += len(delta)
        self.whitespace_characters += sum(char.isspace() for char in delta)
        self.digest.update(delta.encode())
        # Retain at most one partial block; compare hashes, never log samples.
        for start in range(0, len(delta), 512):
            self.pending += delta[start:start + 512]
            if len(self.pending) >= 512:
                block, self.pending = self.pending[:512], self.pending[512:]
                digest = sha256(block.encode()).hexdigest()
                self.repeated_blocks = self.repeated_blocks + 1 if digest == self.previous_block_hash else 1
                self.max_repeated_blocks = max(self.max_repeated_blocks, self.repeated_blocks)
                self.previous_block_hash = digest
                self.repetition_tail = (self.repetition_tail + block)[-4096:]
                if self.repeated_period is None:
                    self.repeated_period = _repeated_period(self.repetition_tail)

    def snapshot(self) -> dict:
        return {"ordinal": self.ordinal, "phase": self.phase, "completed": self.completed,
                "streamed_characters": self.characters, "whitespace_characters": self.whitespace_characters,
                "stream_sha256": self.digest.hexdigest(),
                "max_consecutive_identical_512_character_blocks": self.max_repeated_blocks,
                "exact_repetition_period_characters": self.repeated_period}


class OutputTrace:
    """Keep only the latest eight assistant messages; phases are allowlisted."""

    def __init__(self) -> None:
        self.messages: OrderedDict[str, _Message] = OrderedDict()
        self.count = 0

    def record(self, method: str, payload: dict) -> None:
        if method == "item/agentMessage/delta":
            item_id = payload.get("itemId") or payload.get("item_id") or "unknown"
            phase = None
        elif method in {"item/started", "item/completed"}:
            item = payload.get("item") or {}
            if item.get("type") != "agentMessage":
                return
            item_id = item.get("id") or "unknown"
            phase = item.get("phase")
        else:
            return
        key = str(item_id)
        if key not in self.messages:
            self.count += 1
            self.messages[key] = _Message(self.count)
            if len(self.messages) > 8:
                self.messages.popitem(last=False)
        message = self.messages[key]
        if phase in {"commentary", "final_answer"}:
            message.phase = phase
        if method == "item/agentMessage/delta" and isinstance(payload.get("delta"), str):
            message.append(payload["delta"])
        if method == "item/completed":
            message.completed = True

    def snapshot(self) -> dict:
        return {"message_count": self.count, "retained_message_limit": 8,
                "messages": [message.snapshot() for message in self.messages.values()]}

    def repetition(self) -> dict | None:
        for message in self.messages.values():
            if message.phase == "final_answer" and not message.completed and message.repeated_period is not None:
                # Text is never included in traces or error messages.
                return {"message_ordinal": message.ordinal,
                        "period_characters": message.repeated_period,
                        "window_characters": 4096}
        return None
