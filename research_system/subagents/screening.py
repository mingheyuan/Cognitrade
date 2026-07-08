from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from research_system.subagents.base import SubagentCatalogMetadata, SubagentRuntimePolicy


class ScreeningStrategyResolverInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    source_payload: dict[str, Any] = Field(default_factory=dict)
    planning_context_hints: dict[str, str] = Field(default_factory=dict)
    explicit_cycle: str | None = None
    explicit_methodology: str | None = None


class ScreeningStrategyResolverOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    screening_strategy: dict[str, object] = Field(default_factory=dict)


class ScreeningExecutionInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    source_payload: dict[str, Any] = Field(default_factory=dict)
    snapshot_payloads: list[dict[str, Any]] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)
    screening_strategy: dict[str, object] = Field(default_factory=dict)
    methodology_enabled: bool = True


class ScreeningExecutionOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    screened_candidates: list[dict[str, Any]] = Field(default_factory=list)
    watchlist_candidates: list[dict[str, Any]] = Field(default_factory=list)
    enrichment_targets: list[dict[str, Any]] = Field(default_factory=list)
    scorecard: dict[str, Any] = Field(default_factory=dict)
    screening_notes: list[str] = Field(default_factory=list)


class ScreeningStrategyResolverSubagent:
    subagent_type = "screening_strategy_resolver"
    input_model = ScreeningStrategyResolverInputModel
    output_model = ScreeningStrategyResolverOutputModel
    runtime_policy = SubagentRuntimePolicy()
    catalog_metadata = SubagentCatalogMetadata(
        summary="Resolve screening cycle and methodology strategy from query, source payload, and planning context.",
        capabilities=("screening_strategy", "planning_strategy"),
        stage="screen",
        cost_class="low",
        latency_class="low",
        side_effect_level="none",
        suitable_intents=("stock_screen",),
        tags=("deterministic", "preprocess"),
    )

    def __init__(self, *, derive_screening_strategy: Callable[..., dict[str, Any]]) -> None:
        self.derive_screening_strategy = derive_screening_strategy

    def run(
        self,
        *,
        query: str,
        source_payload: dict[str, Any] | None = None,
        planning_context_hints: dict[str, str] | None = None,
        explicit_cycle: str | None = None,
        explicit_methodology: str | None = None,
    ) -> dict[str, Any]:
        strategy = dict(
            self.derive_screening_strategy(
                query=query,
                source_payload=dict(source_payload or {}),
                planning_context_hints=dict(planning_context_hints or {}),
                explicit_cycle=explicit_cycle,
                explicit_methodology=explicit_methodology,
            )
            or {}
        )
        return {"screening_strategy": strategy}


class ScreeningExecutionSubagent:
    subagent_type = "screening_execution"
    input_model = ScreeningExecutionInputModel
    output_model = ScreeningExecutionOutputModel
    runtime_policy = SubagentRuntimePolicy()
    catalog_metadata = SubagentCatalogMetadata(
        summary="Score screening candidates, attach optional methodology outputs, and decide enrichment targets.",
        capabilities=("screening_execution", "screening_candidates", "screening_methodology"),
        stage="screen",
        cost_class="medium",
        latency_class="medium",
        side_effect_level="read_only",
        suitable_intents=("stock_screen",),
        tags=("deterministic", "screening"),
    )

    def __init__(self, *, execute_screening_pipeline: Callable[..., dict[str, Any]]) -> None:
        self.execute_screening_pipeline = execute_screening_pipeline

    def run(
        self,
        *,
        query: str,
        source_payload: dict[str, Any] | None = None,
        snapshot_payloads: list[dict[str, Any]] | None = None,
        constraints: dict[str, Any] | None = None,
        screening_strategy: dict[str, Any] | None = None,
        methodology_enabled: bool = True,
    ) -> dict[str, Any]:
        payload = dict(
            self.execute_screening_pipeline(
                query=query,
                source_payload=dict(source_payload or {}),
                snapshot_payloads=list(snapshot_payloads or []),
                constraints=dict(constraints or {}),
                screening_strategy=dict(screening_strategy or {}),
                methodology_enabled=methodology_enabled,
            )
            or {}
        )
        return {
            "screened_candidates": list(payload.get("screened_candidates") or []),
            "watchlist_candidates": list(payload.get("watchlist_candidates") or []),
            "enrichment_targets": list(payload.get("enrichment_targets") or []),
            "scorecard": dict(payload.get("scorecard") or {}),
            "screening_notes": [str(item) for item in list(payload.get("screening_notes") or []) if str(item).strip()],
        }


__all__ = [
    "ScreeningExecutionSubagent",
    "ScreeningStrategyResolverSubagent",
]
