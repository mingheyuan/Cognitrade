from __future__ import annotations

from collections.abc import Callable
from typing import Any, Iterable

from pydantic import BaseModel

from research_system.schemas.subagents import (
    SubagentCatalogEntry,
    SubagentExecutionRecord,
    SubagentInvocation,
)
from research_system.subagents.base import SubagentDefinition, SupportsSubagent, build_subagent_definition


class SubagentRegistry:
    def __init__(
        self,
        subagents: Iterable[SupportsSubagent] | None = None,
        *,
        catalog_overlay_provider: Callable[[], Iterable[SubagentCatalogEntry]] | None = None,
    ) -> None:
        self._subagents: dict[str, SupportsSubagent] = {}
        self._definitions: dict[str, SubagentDefinition] = {}
        self._observers: list[Callable[..., None]] = []
        self._catalog_overlay_provider = catalog_overlay_provider
        for subagent in subagents or []:
            self.register(subagent)

    def register(self, subagent: SupportsSubagent) -> None:
        self._subagents[subagent.subagent_type] = subagent
        self._definitions[subagent.subagent_type] = build_subagent_definition(subagent)

    def add_observer(self, observer: Callable[..., None]) -> None:
        self._observers.append(observer)

    def get(self, subagent_type: str) -> SupportsSubagent:
        try:
            return self._subagents[subagent_type]
        except KeyError as exc:
            raise KeyError(f"unknown subagent: {subagent_type}") from exc

    def definition(self, subagent_type: str) -> SubagentDefinition:
        try:
            return self._definitions[subagent_type]
        except KeyError as exc:
            raise KeyError(f"unknown subagent definition: {subagent_type}") from exc

    def definitions(self) -> list[SubagentDefinition]:
        return [self._definitions[subagent_type] for subagent_type in sorted(self._definitions)]

    def catalog_entries(
        self,
        *,
        stage: str | None = None,
        intent: str | None = None,
        capability: str | None = None,
        requires_llm: bool | None = None,
        include_candidates: bool = False,
    ) -> list[SubagentCatalogEntry]:
        entries = [
            self._definition_to_catalog_entry(definition)
            for definition in self.definitions()
        ]
        if include_candidates:
            entries.extend(self._catalog_overlay_entries())
        normalized_stage = str(stage or "").strip().lower()
        normalized_intent = str(intent or "").strip().lower()
        normalized_capability = str(capability or "").strip().lower()
        filtered: list[SubagentCatalogEntry] = []
        for entry in entries:
            if normalized_stage and str(entry.stage or "").strip().lower() != normalized_stage:
                continue
            if normalized_intent and normalized_intent not in {item.strip().lower() for item in entry.suitable_intents}:
                continue
            if normalized_capability and normalized_capability not in {item.strip().lower() for item in entry.capabilities}:
                continue
            if requires_llm is not None and bool(entry.requires_llm) != bool(requires_llm):
                continue
            filtered.append(entry)
        return filtered

    def _catalog_overlay_entries(self) -> list[SubagentCatalogEntry]:
        provider = self._catalog_overlay_provider
        if not callable(provider):
            return []
        try:
            entries = provider()
        except Exception:
            return []
        normalized: list[SubagentCatalogEntry] = []
        for entry in entries or []:
            if isinstance(entry, SubagentCatalogEntry):
                normalized.append(entry)
        return normalized

    def notify_started(self, *, invocation: SubagentInvocation) -> None:
        self._notify(
            stage="started",
            invocation=invocation,
            record=None,
            outputs=None,
            error=None,
        )

    def notify_completed(
        self,
        *,
        invocation: SubagentInvocation,
        record: SubagentExecutionRecord,
        outputs: dict[str, Any],
    ) -> None:
        self._notify(
            stage="completed",
            invocation=invocation,
            record=record,
            outputs=outputs,
            error=None,
        )

    def notify_failed(
        self,
        *,
        invocation: SubagentInvocation,
        record: SubagentExecutionRecord,
        error: Exception,
    ) -> None:
        self._notify(
            stage="failed",
            invocation=invocation,
            record=record,
            outputs=None,
            error=error,
        )

    def _notify(
        self,
        *,
        stage: str,
        invocation: SubagentInvocation,
        record: SubagentExecutionRecord | None,
        outputs: dict[str, Any] | None,
        error: Exception | None,
    ) -> None:
        for observer in self._observers:
            try:
                observer(
                    stage=stage,
                    invocation=invocation,
                    record=record,
                    outputs=outputs,
                    error=error,
                )
            except Exception:
                continue

    @staticmethod
    def _definition_to_catalog_entry(definition: SubagentDefinition) -> SubagentCatalogEntry:
        metadata = definition.catalog_metadata
        input_model = definition.input_model
        output_model = definition.output_model
        return SubagentCatalogEntry(
            subagent_type=definition.subagent_type,
            summary=metadata.summary,
            capabilities=[item for item in metadata.capabilities if str(item).strip()],
            stage=metadata.stage,
            cost_class=metadata.cost_class,
            latency_class=metadata.latency_class,
            side_effect_level=metadata.side_effect_level,
            suitable_intents=[item for item in metadata.suitable_intents if str(item).strip()],
            tags=[item for item in metadata.tags if str(item).strip()],
            requires_llm=definition.runtime_policy.requires_llm,
            allow_parallel=definition.runtime_policy.allow_parallel,
            max_concurrency=definition.runtime_policy.max_concurrency,
            fallback_subagent_type=definition.runtime_policy.fallback_subagent_type,
            error_fallback_subagent_type=definition.runtime_policy.error_fallback_subagent_type,
            input_schema_name=input_model.__name__ if input_model is not None else None,
            output_schema_name=output_model.__name__ if output_model is not None else None,
            input_json_schema=SubagentRegistry._schema_for_model(input_model),
            output_json_schema=SubagentRegistry._schema_for_model(output_model),
        )

    @staticmethod
    def _schema_for_model(model: type[BaseModel] | None) -> dict[str, Any] | None:
        if model is None:
            return None
        return dict(model.model_json_schema())
