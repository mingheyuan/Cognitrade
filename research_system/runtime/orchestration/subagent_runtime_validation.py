from __future__ import annotations

from dataclasses import dataclass

from research_system.schemas.subagents import SubagentCatalogEntry


@dataclass(frozen=True)
class _RuntimeValidationHandler:
    registration_type: str
    validator_name: str


_RUNTIME_VALIDATION_HANDLERS = (
    _RuntimeValidationHandler(
        registration_type="snapshot_skill_runner",
        validator_name="_validate_snapshot_skill_registration",
    ),
    _RuntimeValidationHandler(
        registration_type="skill_runner",
        validator_name="_validate_skill_registration",
    ),
    _RuntimeValidationHandler(
        registration_type="http_json_runner",
        validator_name="_validate_contract_registration",
    ),
    _RuntimeValidationHandler(
        registration_type="openapi_http_runner",
        validator_name="_validate_contract_registration",
    ),
)

SUPPORTED_RUNTIME_VALIDATION_TYPES = tuple(handler.registration_type for handler in _RUNTIME_VALIDATION_HANDLERS)


class SubagentRuntimeValidationSupport:
    def __init__(self, host=None) -> None:
        self.host = host
        self._handlers = {handler.registration_type: handler for handler in _RUNTIME_VALIDATION_HANDLERS}

    def validation_types(self) -> tuple[str, ...]:
        return tuple(self._handlers)

    def validate_registration(
        self,
        *,
        registration_type: str,
        candidate_entry: SubagentCatalogEntry,
        skill_name: str | None = None,
        snapshot_type: str | None = None,
    ) -> list[str]:
        handler = self._handler(registration_type)
        return list(
            getattr(self, handler.validator_name)(
                candidate_entry=candidate_entry,
                registration_type=registration_type,
                skill_name=str(skill_name or "").strip(),
                snapshot_type=str(snapshot_type or "").strip(),
            )
        )

    def _handler(self, registration_type: str) -> _RuntimeValidationHandler:
        normalized_type = str(registration_type or "").strip()
        handler = self._handlers.get(normalized_type)
        if handler is not None:
            return handler
        raise self._error(f"unsupported runtime validation type: {registration_type}")

    def _validate_snapshot_skill_registration(
        self,
        *,
        candidate_entry: SubagentCatalogEntry,
        registration_type: str,
        skill_name: str,
        snapshot_type: str,
    ) -> list[str]:
        del snapshot_type
        normalized_stage = str(candidate_entry.stage or "").strip().lower()
        if normalized_stage not in {"", "collect"}:
            raise self._error(
                f"{registration_type} activation is only supported for collect-stage candidates: {candidate_entry.subagent_type}"
            )
        if not skill_name:
            raise self._error(f"skill_name is required for {registration_type} activation")
        self._validate_skill_name(skill_name)
        return ["validated_via_skill_runtime_registry"]

    def _validate_skill_registration(
        self,
        *,
        candidate_entry: SubagentCatalogEntry,
        registration_type: str,
        skill_name: str,
        snapshot_type: str,
    ) -> list[str]:
        del snapshot_type
        if not skill_name:
            raise self._error(f"skill_name is required for {registration_type} activation")
        self._validate_runtime_contract(candidate_entry, registration_type=registration_type)
        self._validate_skill_name(skill_name)
        return [
            "validated_via_skill_runtime_registry",
            "validated_runtime_contract_schema",
        ]

    def _validate_contract_registration(
        self,
        *,
        candidate_entry: SubagentCatalogEntry,
        registration_type: str,
        skill_name: str,
        snapshot_type: str,
    ) -> list[str]:
        del skill_name, snapshot_type
        self._validate_runtime_contract(candidate_entry, registration_type=registration_type)
        return ["validated_runtime_contract_schema"]

    def _validate_skill_name(self, skill_name: str) -> None:
        runtime = getattr(self.host, "require_skill_runtime", lambda: None)()
        registry = getattr(getattr(runtime, "backend", None), "registry", None)
        if not isinstance(registry, dict):
            raise self._error("skill_runtime registry is not available for validation")
        if skill_name not in registry:
            raise self._error(f"unknown skill_name for runtime registration: {skill_name}")

    def _validate_runtime_contract(self, candidate_entry: SubagentCatalogEntry, *, registration_type: str) -> None:
        input_schema = candidate_entry.input_json_schema
        output_schema = candidate_entry.output_json_schema
        if not self.is_object_contract_schema(input_schema):
            raise self._error(
                f"{registration_type} activation requires input_json_schema on candidate: {candidate_entry.subagent_type}"
            )
        if not self.is_object_contract_schema(output_schema):
            raise self._error(
                f"{registration_type} activation requires output_json_schema on candidate: {candidate_entry.subagent_type}"
            )

    @staticmethod
    def is_object_contract_schema(schema: object) -> bool:
        if not isinstance(schema, dict):
            return False
        normalized_type = str(schema.get("type") or "").strip().lower()
        properties = schema.get("properties")
        if normalized_type and normalized_type != "object":
            return False
        return isinstance(properties, dict)

    def _error(self, message: str) -> Exception:
        error_cls = getattr(self.host, "orchestrator_error_cls", ValueError) or ValueError
        return error_cls(message)


__all__ = [
    "SUPPORTED_RUNTIME_VALIDATION_TYPES",
    "SubagentRuntimeValidationSupport",
]
