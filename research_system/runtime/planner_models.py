from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict


class EntryPlanPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    use_subagents: bool
    initial_actions: list[Literal["collect", "screen", "retrieve", "report"]]
    reason: str | None = None


@dataclass(frozen=True)
class EntryPlanDecision:
    source: Literal["explicit_intent", "llm", "rule"]
    use_subagents: bool
    initial_actions: list[str]
    reason: str | None = None
