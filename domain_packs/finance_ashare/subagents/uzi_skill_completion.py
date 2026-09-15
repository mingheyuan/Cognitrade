"""Pinned full-SKILL duties outside the unchanged native Python CHECKS.

This projection never changes a native issue's severity or invents an Agent
acknowledgement. The reference driver implements its own independent check.
"""

import re

from pydantic import BaseModel, ConfigDict, Field, model_validator


def pending_native_warnings(review: dict, agent: dict | None) -> list[dict]:
    acknowledged = (agent or {}).get("review_acknowledged") or {}
    if not isinstance(acknowledged, dict):
        acknowledged = {}
    pending = []
    for issue in review.get("issues") or []:
        if issue.get("severity") != "warning":
            continue
        keys = [issue.get("dim"), issue.get("issue")]
        check = re.match(r"^check ([A-Za-z_]\w*) 自己炸了:", str(issue.get("issue") or ""))
        if check and issue.get("category") == "self-check":
            keys.extend([check[1], f"{issue.get('dim')}.{check[1]}"])
        aliases = [alias for key in keys if isinstance(key, str) and key
                   for alias in (key, f"warning_{key}", f"warning:{key}", f"warning.{key}")]
        if not any(isinstance(acknowledged.get(key), str) and acknowledged[key].strip() for key in aliases):
            pending.append(issue)
    return pending


def full_skill_debate_complete(agent: dict | None) -> bool:
    debate = (agent or {}).get("great_divide_override")
    if not isinstance(debate, dict):
        return False
    return (isinstance(debate.get("punchline"), str) and len(debate["punchline"].strip()) >= 10
            and all(isinstance(debate.get(side), list) and len(debate[side]) >= 3
                    and all(isinstance(row, str) and row.strip() for row in debate[side])
                    for side in ("bull_say_rounds", "bear_say_rounds")))


class NativeWarningReason(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issue_index: int = Field(ge=0, strict=True)
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def reject_blank_reason(self):
        if not self.reason.strip():
            raise ValueError("warning acknowledgement requires an explicit reason")
        return self


class NativeWarningAcknowledgements(BaseModel):
    model_config = ConfigDict(extra="forbid")
    acknowledgements: list[NativeWarningReason] = Field(min_length=1)

    def for_issues(self, issues: list[dict]) -> dict[str, str]:
        indices = [row.issue_index for row in self.acknowledgements]
        if len(indices) != len(issues) or set(indices) != set(range(len(issues))):
            raise ValueError("acknowledge each supplied warning exactly once; do not add or omit issues")
        return {str(issues[row.issue_index]["issue"]): row.reason for row in self.acknowledgements}
