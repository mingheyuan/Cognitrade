from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

RuntimeRegistrationType = Literal[
    "snapshot_skill_runner",
    "skill_runner",
    "http_json_runner",
    "openapi_http_runner",
]

SUPPORTED_RUNTIME_REGISTRATION_TYPES: tuple[RuntimeRegistrationType, ...] = (
    "snapshot_skill_runner",
    "skill_runner",
    "http_json_runner",
    "openapi_http_runner",
)


@dataclass(frozen=True)
class RuntimeRegistrationForbiddenFieldGroup:
    fields: tuple[str, ...]
    error_message: str


@dataclass(frozen=True)
class RuntimeRegistrationSpec:
    registration_type: RuntimeRegistrationType
    template_allowed_keys: tuple[str, ...]
    required_fields: tuple[str, ...] = ()
    forbidden_field_groups: tuple[RuntimeRegistrationForbiddenFieldGroup, ...] = ()
    supports_timeout_seconds: bool = False


_RUNTIME_REGISTRATION_SPECS: dict[str, RuntimeRegistrationSpec] = {
    "snapshot_skill_runner": RuntimeRegistrationSpec(
        registration_type="snapshot_skill_runner",
        template_allowed_keys=("skill_name", "snapshot_type"),
        required_fields=("skill_name", "snapshot_type"),
        forbidden_field_groups=(
            RuntimeRegistrationForbiddenFieldGroup(
                fields=("source_id", "endpoint_path", "http_method", "openapi_spec_uri", "openapi_operation_id"),
                error_message="snapshot_skill_runner does not support http source settings",
            ),
        ),
        supports_timeout_seconds=False,
    ),
    "skill_runner": RuntimeRegistrationSpec(
        registration_type="skill_runner",
        template_allowed_keys=("skill_name",),
        required_fields=("skill_name",),
        forbidden_field_groups=(
            RuntimeRegistrationForbiddenFieldGroup(
                fields=("snapshot_type",),
                error_message="skill_runner does not support snapshot_type",
            ),
            RuntimeRegistrationForbiddenFieldGroup(
                fields=("source_id", "endpoint_path", "http_method", "openapi_spec_uri", "openapi_operation_id"),
                error_message="skill_runner does not support http source settings",
            ),
        ),
        supports_timeout_seconds=False,
    ),
    "http_json_runner": RuntimeRegistrationSpec(
        registration_type="http_json_runner",
        template_allowed_keys=("source_id", "endpoint_path", "http_method", "timeout_seconds"),
        required_fields=("source_id", "endpoint_path", "http_method"),
        forbidden_field_groups=(
            RuntimeRegistrationForbiddenFieldGroup(
                fields=("skill_name", "snapshot_type"),
                error_message="http_json_runner does not support skill_name or snapshot_type",
            ),
            RuntimeRegistrationForbiddenFieldGroup(
                fields=("openapi_spec_uri", "openapi_operation_id"),
                error_message="http_json_runner does not support openapi provenance fields",
            ),
        ),
        supports_timeout_seconds=True,
    ),
    "openapi_http_runner": RuntimeRegistrationSpec(
        registration_type="openapi_http_runner",
        template_allowed_keys=(
            "source_id",
            "endpoint_path",
            "http_method",
            "openapi_spec_uri",
            "openapi_operation_id",
            "timeout_seconds",
        ),
        required_fields=("source_id", "endpoint_path", "http_method", "openapi_operation_id"),
        forbidden_field_groups=(
            RuntimeRegistrationForbiddenFieldGroup(
                fields=("skill_name", "snapshot_type"),
                error_message="openapi_http_runner does not support skill_name or snapshot_type",
            ),
        ),
        supports_timeout_seconds=True,
    ),
}


def runtime_registration_spec(registration_type: str) -> RuntimeRegistrationSpec:
    normalized_type = str(registration_type or "").strip()
    spec = _RUNTIME_REGISTRATION_SPECS.get(normalized_type)
    if spec is None:
        raise ValueError(f"unsupported runtime registration type: {registration_type}")
    return spec


def validate_runtime_registration_template_defaults(
    *,
    registration_type: str,
    defaults: dict[str, Any],
) -> None:
    spec = runtime_registration_spec(registration_type)
    allowed_keys = {str(key) for key in spec.template_allowed_keys}
    unknown_keys = sorted(str(key) for key in defaults if str(key) not in allowed_keys)
    if unknown_keys:
        raise ValueError(
            f"unsupported runtime registration template defaults for {spec.registration_type}: {', '.join(unknown_keys)}"
        )
    default_http_method = str(defaults.get("http_method") or "").strip().upper()
    if default_http_method and default_http_method not in {"GET", "POST"}:
        raise ValueError("runtime registration template http_method must be GET or POST")
    default_timeout_seconds = defaults.get("timeout_seconds")
    if default_timeout_seconds is not None:
        if not spec.supports_timeout_seconds:
            raise ValueError(f"{spec.registration_type} does not support timeout_seconds defaults")
        if float(default_timeout_seconds) <= 0.0:
            raise ValueError("runtime registration template timeout_seconds must be > 0")


def validate_runtime_registration_shape(
    *,
    registration_type: str,
    skill_name: str,
    snapshot_type: str,
    source_id: str,
    endpoint_path: str,
    http_method: str,
    openapi_spec_uri: str,
    openapi_operation_id: str,
    has_timeout_seconds: bool,
) -> None:
    spec = runtime_registration_spec(registration_type)
    normalized_values = {
        "skill_name": skill_name,
        "snapshot_type": snapshot_type,
        "source_id": source_id,
        "endpoint_path": endpoint_path,
        "http_method": http_method,
        "openapi_spec_uri": openapi_spec_uri,
        "openapi_operation_id": openapi_operation_id,
    }
    for field_name in spec.required_fields:
        if str(normalized_values.get(field_name) or "").strip():
            continue
        raise ValueError(f"{spec.registration_type} requires {field_name}")
    for group in spec.forbidden_field_groups:
        if any(str(normalized_values.get(field_name) or "").strip() for field_name in group.fields):
            raise ValueError(group.error_message)
    if has_timeout_seconds and not spec.supports_timeout_seconds:
        raise ValueError(f"{spec.registration_type} does not support timeout_seconds")


__all__ = [
    "RuntimeRegistrationForbiddenFieldGroup",
    "RuntimeRegistrationSpec",
    "RuntimeRegistrationType",
    "SUPPORTED_RUNTIME_REGISTRATION_TYPES",
    "runtime_registration_spec",
    "validate_runtime_registration_shape",
    "validate_runtime_registration_template_defaults",
]
