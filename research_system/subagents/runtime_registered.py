from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from research_system.runtime.platform.skill_runtime import SkillRuntime
from research_system.subagents.base import SubagentCatalogMetadata, SubagentRuntimePolicy


class RuntimeRegisteredSnapshotSkillInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    symbol: str | None = None


class RuntimeRegisteredSnapshotSkillOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_type: str
    snapshot: dict[str, Any] | None = None
    degraded: bool = False
    degrade_reason: str | None = None


class RuntimeRegisteredSnapshotSkillSubagent:
    input_model = RuntimeRegisteredSnapshotSkillInputModel
    output_model = RuntimeRegisteredSnapshotSkillOutputModel
    runtime_policy = SubagentRuntimePolicy(allow_parallel=True, max_concurrency=4)
    catalog_metadata = SubagentCatalogMetadata(
        summary="Execute one validated snapshot skill as a runtime-registered subagent alias.",
        capabilities=("snapshot_collection", "skill_bridge", "runtime_registration"),
        stage="collect",
        cost_class="medium",
        latency_class="medium",
        side_effect_level="read_only",
        suitable_intents=("stock_check", "stock_compare", "stock_screen", "sector_research", "review_thesis"),
        tags=("runtime_registered", "read_only"),
    )

    def __init__(
        self,
        *,
        subagent_type: str,
        snapshot_type: str,
        skill_name: str,
        skill_runtime: SkillRuntime,
        runtime_policy: SubagentRuntimePolicy | None = None,
        catalog_metadata: SubagentCatalogMetadata | None = None,
    ) -> None:
        self.subagent_type = subagent_type
        self.snapshot_type = snapshot_type
        self.skill_name = skill_name
        self.skill_runtime = skill_runtime
        self.runtime_policy = runtime_policy or type(self).runtime_policy
        self.catalog_metadata = catalog_metadata or type(self).catalog_metadata

    def run(self, *, query: str, symbol: str | None = None) -> dict[str, Any]:
        try:
            snapshot = self.skill_runtime.run(self.skill_name, query=query, symbol=symbol)
        except Exception as exc:
            return {
                "snapshot_type": self.snapshot_type,
                "snapshot": None,
                "degraded": True,
                "degrade_reason": type(exc).__name__,
            }
        return {
            "snapshot_type": self.snapshot_type,
            "snapshot": snapshot,
            "degraded": False,
            "degrade_reason": None,
        }


__all__ = [
    "RuntimeRegisteredSnapshotSkillInputModel",
    "RuntimeRegisteredSnapshotSkillOutputModel",
    "RuntimeRegisteredSnapshotSkillSubagent",
]
