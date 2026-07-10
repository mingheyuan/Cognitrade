from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from research_system.schemas.subagent_runtime_registration import RuntimeRegistrationType


class ApiRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RerunRequest(ApiRequestModel):
    from_node: str = Field(min_length=1, max_length=128)


class SubagentRerunRequest(ApiRequestModel):
    action_node: str = Field(min_length=1, max_length=128)
    dag_id: str = Field(min_length=1, max_length=128)
    from_node: str = Field(min_length=1, max_length=128)


class _ReviewRequest(ApiRequestModel):
    decision: Literal["approve", "reject"]
    reason: str | None = Field(default=None, min_length=1, max_length=2_000)


class ProposalReviewRequest(_ReviewRequest):
    pass


class DiscoveryReviewRequest(_ReviewRequest):
    pass


class DiscoveryActivationRequest(ApiRequestModel):
    candidate_subagent_type: str | None = Field(default=None, min_length=1, max_length=128)
    delegate_subagent_type: str | None = Field(default=None, min_length=1, max_length=128)
    registration_template_id: str | None = Field(default=None, min_length=1, max_length=128)
    registration_type: RuntimeRegistrationType | None = None
    skill_name: str | None = Field(default=None, min_length=1, max_length=256)
    snapshot_type: str | None = Field(default=None, min_length=1, max_length=64)
    source_id: str | None = Field(default=None, min_length=1, max_length=128)
    tool_name: str | None = Field(default=None, min_length=1, max_length=256)
    endpoint_path: str | None = Field(default=None, min_length=1, max_length=2_048)
    http_method: Literal["GET", "POST"] | None = None
    openapi_operation_id: str | None = Field(default=None, min_length=1, max_length=256)
    timeout_seconds: float | None = Field(default=None, gt=0.0, le=300.0)
    reason: str | None = Field(default=None, min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def validate_activation_mode(self) -> "DiscoveryActivationRequest":
        explicit_fields = {
            "skill_name": self.skill_name,
            "snapshot_type": self.snapshot_type,
            "source_id": self.source_id,
            "tool_name": self.tool_name,
            "endpoint_path": self.endpoint_path,
            "http_method": self.http_method,
            "openapi_operation_id": self.openapi_operation_id,
        }
        if self.delegate_subagent_type is not None:
            if self.registration_template_id is not None or self.registration_type is not None:
                raise ValueError("delegate activation cannot include a runtime registration mode")
            supplied = [name for name, value in explicit_fields.items() if value is not None]
            if self.timeout_seconds is not None:
                supplied.append("timeout_seconds")
            if supplied:
                raise ValueError(
                    "delegate activation does not accept runtime registration fields: "
                    + ", ".join(sorted(supplied))
                )
            return self

        if self.registration_template_id is not None:
            self._validate_endpoint_path()
            return self

        if self.registration_type is not None:
            self._validate_explicit_registration()
            return self

        raise ValueError(
            "activation requires delegate_subagent_type, registration_template_id, or registration_type"
        )

    def _validate_explicit_registration(self) -> None:
        registration_type = self.registration_type
        if registration_type == "snapshot_skill_runner":
            self._require("skill_name", "snapshot_type")
            self._forbid(
                "source_id",
                "tool_name",
                "endpoint_path",
                "http_method",
                "openapi_operation_id",
            )
        elif registration_type == "skill_runner":
            self._require("skill_name")
            self._forbid(
                "snapshot_type",
                "source_id",
                "tool_name",
                "endpoint_path",
                "http_method",
                "openapi_operation_id",
            )
        elif registration_type == "http_json_runner":
            self._require("source_id", "endpoint_path", "http_method")
            self._forbid("skill_name", "snapshot_type", "tool_name", "openapi_operation_id")
            self._validate_endpoint_path()
        elif registration_type == "openapi_http_runner":
            self._require("source_id", "openapi_operation_id")
            self._forbid("skill_name", "snapshot_type", "tool_name", "endpoint_path", "http_method")
        elif registration_type == "mcp_tool_runner":
            self._require("source_id", "tool_name")
            self._forbid(
                "skill_name",
                "snapshot_type",
                "endpoint_path",
                "http_method",
                "openapi_operation_id",
            )

    def _validate_endpoint_path(self) -> None:
        if self.endpoint_path is None:
            return
        if (
            not self.endpoint_path.startswith("/")
            or self.endpoint_path.startswith("//")
            or "://" in self.endpoint_path
            or "\\" in self.endpoint_path
            or ".." in self.endpoint_path.split("/")
        ):
            raise ValueError("endpoint_path must be a safe relative path beginning with a single '/'")

    def _require(self, *field_names: str) -> None:
        missing = [name for name in field_names if getattr(self, name) is None]
        if missing:
            raise ValueError(
                f"{self.registration_type} requires " + ", ".join(sorted(missing))
            )

    def _forbid(self, *field_names: str) -> None:
        supplied = [name for name in field_names if getattr(self, name) is not None]
        if supplied:
            raise ValueError(
                f"{self.registration_type} does not accept " + ", ".join(sorted(supplied))
            )


class ChatThreadRenameRequest(ApiRequestModel):
    thread_label: str = Field(min_length=1, max_length=120)

    @field_validator("thread_label", mode="before")
    @classmethod
    def normalize_thread_label(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return " ".join(value.split())


__all__ = [
    "ApiRequestModel",
    "ChatThreadRenameRequest",
    "DiscoveryActivationRequest",
    "DiscoveryReviewRequest",
    "ProposalReviewRequest",
    "RerunRequest",
    "SubagentRerunRequest",
]
