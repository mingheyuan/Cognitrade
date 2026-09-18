"""Shared LLM request policy for the UZI research stages."""

from __future__ import annotations

import inspect
import logging
import re
import socket
import ssl
from contextvars import ContextVar
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from functools import wraps
from math import isfinite
from random import random
from threading import Condition, Event, Lock
from time import monotonic, sleep
from typing import Any
from uuid import uuid4

import httpx
from pydantic import ValidationError

from research_system.runtime.providers.errors import (
    CodexTurnError,
    LLMCancelledError,
    LLMDeadlineError,
    LLMOutputRepetitionError,
    StructuredOutputError,
)

DEFAULT_UZI_LLM_REQUEST_TIMEOUT_SECONDS = 480.0
DEFAULT_UZI_LLM_PROFILE_REQUEST_TIMEOUT_SECONDS = {
    "lite": 240.0,
    "medium": 600.0,
    "deep": 900.0,
}
DEFAULT_UZI_LLM_BUY_ZONE_REQUEST_TIMEOUT_SECONDS = 300.0
DEFAULT_UZI_LLM_QUEUE_TIMEOUT_SECONDS = 1200.0
DEFAULT_UZI_LLM_MAX_CONCURRENCY = 2
DEFAULT_UZI_LLM_TRANSIENT_RETRIES = 2
DEFAULT_UZI_LLM_RETRY_BASE_SECONDS = 2.0
DEFAULT_UZI_LLM_RETRY_MAX_SECONDS = 60.0
_SEMAPHORE_LOCK = Lock()
_SEMAPHORES: dict[int, _RequestLimiter] = {}
_LOGGER = logging.getLogger("uvicorn.error")
# Worker-local dispatch/entry times; never mutate a provider shared by stocks.
_STAGE_DISPATCH: ContextVar[tuple[float, float] | None] = ContextVar("uzi_stage_dispatch", default=None)


class UziRequestCancelled(RuntimeError):
    """The caller cancelled a queued or active model stage."""


class UziRequestDeadline(TimeoutError):
    """A model stage exhausted its queue/request/recovery budget."""


_QUALITATIVE_GROUP_VALIDATION_FIELDS = {
    "qualitative_dimension_membership_mismatch": frozenset({"dimensions"}),
    "qualitative_repeated_finding": frozenset({"dimensions.0.evidence", "dimensions.1.evidence"}),
    "qualitative_ungrounded_url": frozenset({"dimensions.0.evidence", "dimensions.1.evidence"}),
    "qualitative_source_attribution_conflict": frozenset({"dimensions.0.evidence", "dimensions.1.evidence"}),
}


_FACTUAL_EVIDENCE_FIELDS = ("url", "retrieved_at", "finding")


def _safe_factual_missing_fields(value: Any) -> tuple[str, ...]:
    if (
        type(value) is not tuple or not 1 <= len(value) <= len(_FACTUAL_EVIDENCE_FIELDS)
        or any(type(field) is not str or field not in _FACTUAL_EVIDENCE_FIELDS for field in value)
        or len(set(value)) != len(value)
    ):
        return ()
    return tuple(field for field in _FACTUAL_EVIDENCE_FIELDS if field in value)


class UziFactualEvidenceValidationError(ValueError):
    """The existing factual rejection, with finite field names and no values."""

    def __init__(self, *, missing_fields: tuple[str, ...]) -> None:
        fields = _safe_factual_missing_fields(missing_fields)
        if not fields:
            raise ValueError("invalid factual evidence validation diagnostic")
        self.missing_fields = fields
        # Keep Pydantic's value_error type and the established parent reason.
        super().__init__("factual evidence requires URL and a concrete finding")


class UziQualitativeValidationError(ValueError):
    """A post-parse group rejection carrying only a fixed field/reason pair."""

    def __init__(self, *, field: str, code: str) -> None:
        if (
            type(field) is not str or type(code) is not str
            or field not in _QUALITATIVE_GROUP_VALIDATION_FIELDS.get(code, ())
        ):
            raise ValueError("invalid qualitative validation diagnostic")
        self.field = field
        self.code = code
        super().__init__(code)


EXHAUSTED_MODEL_FAILURES = frozenset({
    "llm_cancelled", "llm_stage_deadline", "llm_queue_timeout",
    "llm_transport_timeout", "llm_provider_unavailable",
    "llm_output_repetition",
})


class UziStageFailures(RuntimeError):
    """A request-local vector; never flatten unrelated concurrent causes."""

    def __init__(self, failures: list[dict[str, Any]]) -> None:
        self.failures = [dict(row) for row in failures]
        super().__init__("; ".join(f"{row.get('stage')}: {row.get('kind')}" for row in self.failures))


def _exception_chain(exc: Exception) -> list[Exception]:
    chain: list[Exception] = []
    seen: set[int] = set()
    while isinstance(exc, Exception) and id(exc) not in seen:
        chain.append(exc)
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return chain


_NON_TRANSIENT_PROVIDER_CODES = frozenset({
    "insufficient_quota", "credit_balance_exhausted", "billing_hard_limit_reached",
    "organization_spend_limit_exceeded", "project_spend_limit_exceeded",
    "organization_usage_limit_exceeded", "invalid_api_key", "permission_denied",
})


def _diagnostic_token(value: Any) -> str | None:
    """Only bounded identifiers, never response messages or arbitrary headers."""
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,512}", value):
        return value
    return None


def _retry_after_seconds(value: Any) -> float | None:
    if not isinstance(value, (str, int, float)):
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            date = parsedate_to_datetime(str(value))
            seconds = (date - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    return max(seconds, 0.0) if isfinite(seconds) else None


def _provider_error_metadata(exc: Exception) -> dict[str, Any]:
    """Read the SDK cause retained by both provider parse paths.

    Old persisted errors only have text. Recognize their status/code without
    inventing response headers or a provider request ID that was never saved.
    """
    metadata: dict[str, Any] = {}
    # Fixed type identities only: str(exc), request URLs, certificate details
    # and response bodies can contain credentials or user input. In particular
    # a connection error has no HTTP status to invent. Old text-only records
    # cannot establish an underlying transport cause retroactively.
    known_types = {
        **{kind: f"httpx.{kind.__name__}" for kind in (
            httpx.ConnectError, httpx.ReadError, httpx.WriteError, httpx.CloseError,
            httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout,
            httpx.RemoteProtocolError, httpx.LocalProtocolError, httpx.ProxyError,
        )},
        ssl.SSLCertVerificationError: "ssl.SSLCertVerificationError",
        ssl.SSLError: "ssl.SSLError",
        socket.gaierror: "socket.gaierror",
        ConnectionResetError: "builtins.ConnectionResetError",
        ConnectionRefusedError: "builtins.ConnectionRefusedError",
        BrokenPipeError: "builtins.BrokenPipeError",
    }
    causes = [known_types[type(error)] for error in _exception_chain(exc)[:8] if type(error) in known_types]
    if causes:
        metadata["provider_error_chain"] = causes
    for error in _exception_chain(exc):
        status = getattr(error, "status_code", None)
        if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599:
            metadata["provider_status_code"] = status
        body = getattr(error, "body", None)
        body = body.get("error", body) if isinstance(body, dict) else {}
        for field in ("code", "type"):
            value = body.get(field) if isinstance(body, dict) else None
            token = _diagnostic_token(value or getattr(error, field, None))
            if token:
                metadata[f"provider_error_{field}"] = token
        headers = getattr(getattr(error, "response", None), "headers", {})
        if not hasattr(headers, "get"):
            headers = {}
        request_id = _diagnostic_token(getattr(error, "request_id", None) or headers.get("x-request-id"))
        if request_id:
            metadata["provider_request_id"] = request_id
        delay = _retry_after_seconds(headers.get("retry-after") or headers.get("Retry-After"))
        if delay is not None:
            metadata["retry_after_seconds"] = delay
    detail = str(exc)
    if "provider_status_code" not in metadata:
        match = re.search(r"Error code:\s*([45]\d\d)\b", detail, flags=re.IGNORECASE)
        if match:
            metadata["provider_status_code"] = int(match[1])
    if "provider_status_code" in metadata:
        for field in ("code", "type"):
            match = re.search(rf"['\"]{field}['\"]\s*:\s*['\"]([a-zA-Z0-9_-]+)['\"]", detail)
            token = _diagnostic_token(match[1]) if match else None
            if token:
                metadata.setdefault(f"provider_error_{field}", token)
    return metadata


def _status_retryable(metadata: dict[str, Any]) -> bool | None:
    code = str(metadata.get("provider_error_code", "")).lower()
    error_type = str(metadata.get("provider_error_type", "")).lower()
    status = metadata.get("provider_status_code")
    if code in _NON_TRANSIENT_PROVIDER_CODES or error_type == "insufficient_quota":
        return False
    if code in {"httpconnectionfailed", "responsestreamconnectionfailed", "responsestreamdisconnected",
                "serveroverloaded", "internalservererror"}:
        return True  # Codex protocol error, without inventing an HTTP status.
    if status == 429:
        return not code or error_type == "rate_limit_error" or code in {
            "gateway_concurrency_limit", "rate_limit_exceeded", "slow_down",
        }
    if isinstance(status, int):
        return status in {500, 502, 503, 504}
    return None


def model_failure_kind(exc: Exception) -> str:
    from domain_packs.finance_ashare.subagents.uzi_input_views import (
        UziInputBudgetExceeded,
    )

    if isinstance(exc, UziStageFailures):
        kinds = {str(row.get("kind")) for row in exc.failures}
        return next((kind for kind in (
            "llm_cancelled", "llm_stage_deadline", "llm_queue_timeout",
            "llm_transport_timeout", "llm_provider_unavailable", "pipeline_failure", "agent_content_invalid", "source_gap",
            "llm_output_repetition",
        ) if kind in kinds), "agent_content_invalid")
    chain = _exception_chain(exc)
    if any(isinstance(error, CodexTurnError) and error.code == "codex_runtime_exit_unconfirmed" for error in chain):
        # An unconfirmed process exit is never repaired by starting another
        # attempt, even if its chained original failure was repetition.
        return "llm_provider_unavailable"
    if any(isinstance(error, (LLMCancelledError, UziRequestCancelled)) for error in chain):
        return "llm_cancelled"
    if any(isinstance(error, (LLMDeadlineError, UziRequestDeadline)) for error in chain):
        return "llm_queue_timeout" if "queue_timeout" in str(exc) else "llm_stage_deadline"
    if any(isinstance(error, LLMOutputRepetitionError) for error in chain):
        return "llm_output_repetition"
    if any(isinstance(error, (ValidationError, StructuredOutputError, UziQualitativeValidationError)) for error in chain):
        return "agent_content_invalid"
    if any(isinstance(error, UziInputBudgetExceeded) for error in chain):
        return "pipeline_failure"
    # Compatibility with older persisted per-group errors. Inspect the error
    # type before its message: invalid model text may itself mention timeout
    # or bad gateway without describing a transport failure.
    detail = f"{exc.__class__.__name__}:{exc}".lower()
    if "validationerror:" in detail or "structuredoutputerror:" in detail:
        return "agent_content_invalid"
    if "input_budget_exceeded" in detail:
        return "pipeline_failure"
    if _provider_error_metadata(exc).get("provider_status_code"):
        # Provider rejection (including auth/quota) must not trigger content
        # repair. Its retryability is a separate, structured decision.
        return "llm_provider_unavailable"
    if any(marker in detail for marker in (
        "no_grounded_citation_after_analysis", "grounded_citations_below_two_after_analysis",
        "frozen_citations_unavailable", "requires_two_cited_sources",
    )):
        return "source_gap"
    if "cancelled" in detail or "canceled" in detail:
        return "llm_cancelled"
    if "queue_timeout" in detail:
        return "llm_queue_timeout"
    if "deadline" in detail:
        return "llm_stage_deadline"
    if "timeout" in detail or "timed out" in detail:
        return "llm_transport_timeout"
    if any(marker in detail for marker in (
        "llm_request_failed", "provider_unavailable", "upstream_error",
        "connectionerror", "connection error", "connection aborted", "connection reset",
        "remote end closed", "service unavailable", "bad gateway",
        "error code: 502", "error code: 503", "error code: 504",
    )):
        return "llm_provider_unavailable"
    return "agent_content_invalid"


_VALIDATION_REASON_ALLOWLIST = {
    **{("value_error", f"Value error, {code}"): code for code in (
        "qualitative_archive_binding_unknown", "qualitative_archive_binding_conflict",
        "qualitative_archive_binding_ambiguous", "qualitative_archive_binding_date_missing",
    )},
    ("value_error", "Value error, override_reason is required when overriding the rule engine"):
        "roleplay_override_reason_missing",
    ("value_error", ("Value error, vote rationale must cite a concrete number, observed fact, explicit "
                     "data gap, or an explicit abstention for skip/insufficient_evidence")):
        "roleplay_rationale_grounding_missing",
    ("value_error", "Value error, evidence URL must be HTTP(S)"):
        "qualitative_evidence_url_invalid",
    ("value_error", "Value error, unknown evidence must explicitly explain the missing fact"):
        "qualitative_unknown_gap_not_disclosed",
    ("value_error", "Value error, factual evidence requires URL, retrieval date and a concrete finding"):
        "qualitative_factual_evidence_incomplete",
    ("value_error", "Value error, factual evidence requires URL and a concrete finding"):
        "qualitative_factual_evidence_incomplete",
}


def _safe_validation_reasons(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate only exact fixed validator messages; never echo arbitrary text.

    Prefix/substring matching would mislabel messages containing model output,
    provider details or secrets. Unknown errors keep only legacy field/type.
    """
    reasons: dict[tuple[str, str], dict[str, Any]] = {}
    for error in errors:
        code = _VALIDATION_REASON_ALLOWLIST.get((error.get("type"), error.get("msg")))
        if not code:
            continue
        field = ".".join(str(value) for value in error["loc"])
        reason = reasons.setdefault((field, code), {"field": field, "code": code})
        context = error.get("ctx")
        cause = context.get("error") if isinstance(context, dict) else None
        # Pydantic retains our ValueError in ctx.error. Do not persist ctx or
        # infer details from arbitrary messages/duck-typed attributes. Recheck
        # the finite tuple before serialization in case attributes mutated.
        if (
            code == "qualitative_factual_evidence_incomplete"
            and type(cause) is UziFactualEvidenceValidationError
            and (fields := _safe_factual_missing_fields(getattr(cause, "missing_fields", None)))
        ):
            reason["missing_fields"] = list(fields)
    return [reasons[key] for key in sorted(reasons)]


def model_failure_record(exc: Exception, *, stage: str) -> dict[str, Any]:
    """Persist type, field paths and allowlisted reasons, never raw input/text."""
    chain = _exception_chain(exc)
    validation = next((error for error in chain if isinstance(error, ValidationError)), None)
    record: dict[str, Any] = {
        "stage": stage, "kind": model_failure_kind(exc), "error_type": type(exc).__name__,
    }
    provider_metadata = _provider_error_metadata(exc) if record["kind"] not in {"agent_content_invalid", "pipeline_failure"} else {}
    if provider_metadata:
        # A deadline/cancellation during Retry-After still has a real provider
        # cause. Keep its trace, without changing the terminal failure kind.
        record.update(provider_metadata)
        retryable = _status_retryable(provider_metadata)
        record["provider_retryable"] = (
            retryable if retryable is not None else _is_transient_upstream_failure(exc)
        )
    elif record["kind"] == "llm_provider_unavailable":
        record["provider_retryable"] = _is_transient_upstream_failure(exc)
    if validation is not None:
        errors = validation.errors(include_input=False, include_context=True, include_url=False)
        record["validation_fields"] = sorted({".".join(str(v) for v in error["loc"]) for error in errors})
        record["validation_codes"] = sorted({str(error["type"]) for error in errors})
        if reasons := _safe_validation_reasons(errors):
            record["validation_reasons"] = reasons
    # Only live typed rejections establish these causes. Do not infer them
    # from old ValueError strings or duck-typed exception attributes. Recheck
    # the finite pair allowlist here so mutated attributes cannot leak text.
    group_reasons = {
        (error.field, error.code)
        for error in chain
        if isinstance(error, UziQualitativeValidationError)
        and type(error.field) is str and type(error.code) is str
        and error.field in _QUALITATIVE_GROUP_VALIDATION_FIELDS.get(error.code, ())
    }
    if group_reasons:
        record["validation_fields"] = sorted({*record.get("validation_fields", []), *(field for field, _ in group_reasons)})
        record["validation_codes"] = sorted({*record.get("validation_codes", []), "value_error"})
        reasons = {(row["field"], row["code"]): row for row in record.get("validation_reasons", [])}
        for field, code in group_reasons:
            reasons.setdefault((field, code), {"field": field, "code": code})
        record["validation_reasons"] = [reasons[key] for key in sorted(reasons)]
    return record


def execution_failure_records(execution: dict[str, Any], *, stage: str) -> list[dict[str, Any]]:
    records = execution.get("failure_records")
    if isinstance(records, list) and records:
        return [dict(row) for row in records if isinstance(row, dict)]
    errors = [str(error) for error in [execution.get("error"), *(execution.get("errors") or [])] if error]
    causes = [error for error in errors if not error.startswith((
        "missing_dimensions:", "missing_investor_ids:", "cross_domain_associations_below_three:",
    ))]
    return [model_failure_record(RuntimeError(error), stage=stage) for error in causes or errors]


def check_uzi_request_control(cancellation: Event | None, deadline_at: float | None) -> None:
    if cancellation is not None and cancellation.is_set():
        raise UziRequestCancelled("uzi_request_cancelled")
    if deadline_at is not None and monotonic() >= deadline_at:
        raise UziRequestDeadline("uzi_stage_deadline_exceeded")


class _RequestLimiter:
    """Work-conserving per-report admission; FIFO for equally served owners.

    A holding may borrow every idle slot, but its fan-out cannot stay ahead of
    other waiting holdings with fewer active requests. Legacy calls all share
    the None owner and retain their existing FIFO behavior.
    """

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.active = 0
        self.active_by_owner: dict[object | None, int] = {}
        self.last_admitted: dict[object | None, int] = {}
        self.admission_sequence = 0
        self.waiters: list[tuple[object, object | None]] = []
        self.condition = Condition()

    def acquire(self, cancellation: Event | None, deadline_at: float, *, owner: object | None = None) -> None:
        ticket = (object(), owner)
        with self.condition:
            self.waiters.append(ticket)
            try:
                while True:
                    check_uzi_request_control(cancellation, deadline_at)
                    next_ticket = min(self.waiters, key=lambda item: (
                        self.active_by_owner.get(item[1], 0), self.last_admitted.get(item[1], -1),
                    ))
                    if next_ticket is ticket and self.active < self.limit:
                        self.active += 1
                        self.active_by_owner[owner] = self.active_by_owner.get(owner, 0) + 1
                        self.admission_sequence += 1
                        self.last_admitted[owner] = self.admission_sequence
                        self.waiters.remove(ticket)
                        self.condition.notify_all()
                        return
                    self.condition.wait(timeout=min(0.25, max(deadline_at - monotonic(), 0)))
            except BaseException:
                self.waiters.remove(ticket)
                self._forget_idle_owner(owner)
                self.condition.notify_all()
                raise

    def release(self, *, owner: object | None = None) -> None:
        with self.condition:
            self.active -= 1
            remaining = self.active_by_owner[owner] - 1
            if remaining:
                self.active_by_owner[owner] = remaining
            else:
                del self.active_by_owner[owner]
            self._forget_idle_owner(owner)
            self.condition.notify_all()

    def _forget_idle_owner(self, owner: object | None) -> None:
        if owner not in self.active_by_owner and not any(item[1] is owner for item in self.waiters):
            self.last_admitted.pop(owner, None)


class _ControlledProvider:
    def __init__(self, provider: Any, cancellation: Event | None, deadline_at: float | None, callback: Any) -> None:
        self._provider = provider
        self._uzi_cancellation = cancellation or getattr(provider, "_uzi_cancellation", None)
        self._uzi_deadline_at = _earliest_deadline(deadline_at, getattr(provider, "_uzi_deadline_at", None))
        self._uzi_progress_callback = callback or getattr(provider, "_uzi_progress_callback", None)
        self._uzi_request_owner = provider._uzi_request_owner if isinstance(provider, _ControlledProvider) else object()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._provider, name)


def _earliest_deadline(*deadlines: float | None) -> float | None:
    return min((deadline for deadline in deadlines if deadline is not None), default=None)


def with_uzi_request_control(provider: Any, *, cancellation: Event | None = None,
                             deadline_at: float | None = None, progress_callback: Any = None) -> Any:
    """Share immutable per-run control with all nested stage threads."""
    if provider is None:
        return None
    return _ControlledProvider(provider, cancellation, deadline_at, progress_callback)


def resolve_uzi_llm_worker_count(provider: Any, task_count: int, *, ceiling: int) -> int:
    """Bound one parallel UZI stage by tasks, stage policy, and provider settings."""
    settings = getattr(provider, "settings", None)
    configured = getattr(settings, "uzi_llm_max_concurrency", ceiling)
    try:
        limit = int(configured)
    except (TypeError, ValueError):
        limit = ceiling
    return max(1, min(task_count or 1, ceiling, max(limit, 1)))


def submit_uzi_stage(executor: Any, function: Any, *args: Any, **kwargs: Any) -> Any:
    """Start the existing stage budget at submission, including executor wait.

    Calls and output repairs inside this worker share one dispatch origin.
    Reset even on failure so reused threads cannot inherit another stock's age.
    """
    submitted_at = monotonic()

    @wraps(function)
    def run(*call_args: Any, **call_kwargs: Any) -> Any:
        token = _STAGE_DISPATCH.set((submitted_at, monotonic()))
        try:
            return function(*call_args, **call_kwargs)
        finally:
            _STAGE_DISPATCH.reset(token)

    return executor.submit(run, *args, **kwargs)


def resolve_uzi_llm_request_timeout(
    provider: Any,
    *,
    research_profile: str | None = None,
    uzi_stage: str | None = None,
) -> float:
    """Resolve the bounded request budget for one UZI model stage."""

    settings = getattr(provider, "settings", None)
    fallback = float(
        getattr(
            settings,
            "uzi_llm_request_timeout_seconds",
            DEFAULT_UZI_LLM_REQUEST_TIMEOUT_SECONDS,
        )
    )
    normalized_profile = str(research_profile or "").strip().lower()
    setting_name = {
        "lite": "uzi_llm_lite_request_timeout_seconds",
        "medium": "uzi_llm_medium_request_timeout_seconds",
        "deep": "uzi_llm_deep_request_timeout_seconds",
    }.get(normalized_profile)
    if setting_name is None:
        timeout_seconds = fallback
    else:
        timeout_seconds = float(
            getattr(
                settings,
                setting_name,
                DEFAULT_UZI_LLM_PROFILE_REQUEST_TIMEOUT_SECONDS[normalized_profile],
            )
        )
    if str(uzi_stage or "").startswith("buy_zone_"):
        buy_zone_ceiling = float(
            getattr(
                settings,
                "uzi_llm_buy_zone_request_timeout_seconds",
                DEFAULT_UZI_LLM_BUY_ZONE_REQUEST_TIMEOUT_SECONDS,
            )
        )
        timeout_seconds = min(timeout_seconds, buy_zone_ceiling)
    if timeout_seconds <= 0:
        raise ValueError("UZI LLM request timeout must be positive")
    return timeout_seconds


def resolve_uzi_llm_queue_timeout(provider: Any) -> float:
    value = float(getattr(getattr(provider, "settings", None), "uzi_llm_queue_timeout_seconds",
                          DEFAULT_UZI_LLM_QUEUE_TIMEOUT_SECONDS))
    if not isfinite(value) or value <= 0:
        raise ValueError("UZI LLM queue timeout must be finite and positive")
    return value


def parse_uzi_structured(
    provider: Any,
    *,
    research_profile: str | None = None,
    uzi_stage: str | None = None,
    cancellation: Event | None = None,
    deadline_at: float | None = None,
    progress_callback: Any = None,
    **kwargs: Any,
) -> Any:
    """Run a UZI structured request with its dedicated long-form budget.

    Test and alternative providers are allowed to expose the older ``parse``
    signature.  The timeout override is only supplied when the provider
    explicitly accepts it (or accepts arbitrary keyword arguments).
    """

    cancellation = cancellation or getattr(provider, "_uzi_cancellation", None)
    deadline_at = _earliest_deadline(deadline_at, getattr(provider, "_uzi_deadline_at", None))
    progress_callback = progress_callback or getattr(provider, "_uzi_progress_callback", None)
    # Every UZI call gets a stage deadline below, including direct data-center
    # calls with no parent control. Always use an owned, cancellable transport
    # when supported so that this deadline actually reaches the live request.
    parse = getattr(provider, "parse_cancellable", None)
    parse = parse if callable(parse) else provider.parse
    settings = getattr(provider, "settings", None)
    timeout_seconds = resolve_uzi_llm_request_timeout(
        provider,
        research_profile=research_profile,
        uzi_stage=uzi_stage,
    )
    queue_timeout_seconds = resolve_uzi_llm_queue_timeout(provider)
    max_concurrency = int(
        getattr(settings, "uzi_llm_max_concurrency", DEFAULT_UZI_LLM_MAX_CONCURRENCY)
    )
    transient_retries = min(2, int(
        getattr(
            settings,
            "uzi_llm_transient_retries",
            DEFAULT_UZI_LLM_TRANSIENT_RETRIES,
        )
    ))
    retry_base_seconds = float(
        getattr(
            settings,
            "uzi_llm_retry_base_seconds",
            DEFAULT_UZI_LLM_RETRY_BASE_SECONDS,
        )
    )
    retry_max_seconds = float(
        getattr(
            settings,
            "uzi_llm_retry_max_seconds",
            DEFAULT_UZI_LLM_RETRY_MAX_SECONDS,
        )
    )
    if max_concurrency <= 0:
        raise ValueError("uzi_llm_max_concurrency must be positive")
    if transient_retries < 0:
        raise ValueError("uzi_llm_transient_retries must not be negative")
    if retry_base_seconds <= 0 or retry_max_seconds < retry_base_seconds:
        raise ValueError(
            "UZI retry delays require 0 < base_seconds <= max_seconds"
        )
    parameters: tuple[inspect.Parameter, ...] = ()
    try:
        parameters = tuple(inspect.signature(parse).parameters.values())
        supports_timeout = any(
            parameter.name == "timeout_seconds"
            or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )
    except (TypeError, ValueError):
        supports_timeout = False

    def supports(parameter_name: str) -> bool:
        return any(
            parameter.name == parameter_name
            or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )

    if supports("cancellation"):
        kwargs["cancellation"] = cancellation
    if "reasoning_effort" in kwargs and not supports("reasoning_effort"):
        kwargs.pop("reasoning_effort")
    if "validation_context" in kwargs and not supports("validation_context"):
        kwargs.pop("validation_context")
    if "json_schema_override" in kwargs and not supports("json_schema_override"):
        kwargs.pop("json_schema_override")
    # Opt in only for generated commentary/synthesis, not arbitrary JSON data,
    # quoted research documents, roleplay, or general provider callers.
    guard_repetition = bool(
        uzi_stage == "narrative_core"
        or re.fullmatch(r"dimensions_\d+_\d+", str(uzi_stage or ""))
    ) and supports("output_repetition_guard")
    if guard_repetition:
        kwargs["output_repetition_guard"] = True
    supports_max_retries = supports("max_retries")
    if supports_max_retries:
        # UZI owns recovery through suggested_fix → recompute → self-review.
        # The Codex adapter performs one SDK turn; runtime stream reconnection
        # remains Codex-managed and is bounded by our shared wall deadline.
        kwargs["max_retries"] = 0
    started = monotonic()
    dispatch = _STAGE_DISPATCH.get()
    stage_started = dispatch[0] if dispatch is not None else started
    executor_wait_seconds = max(dispatch[1] - dispatch[0], 0) if dispatch is not None else 0.0
    # One bounded budget covers queueing, every attempt and backoff. Root
    # cancellation/deadline always takes precedence and retries never reset it.
    stage_deadline = _earliest_deadline(deadline_at, stage_started + queue_timeout_seconds + timeout_seconds)
    # Charge executor wait to admission, not previous HTTP/validation work in
    # an output repair. All of that work still consumes the stage deadline.
    queue_deadline = min(stage_deadline, started + max(queue_timeout_seconds - executor_wait_seconds, 0))
    if supports("deadline_at"):
        kwargs["deadline_at"] = stage_deadline
    semaphore = _uzi_semaphore(max_concurrency)
    owner = provider._uzi_request_owner if isinstance(provider, _ControlledProvider) else None
    local_request_id = uuid4().hex
    queue_seconds = 0.0
    http_seconds = 0.0
    backoff_seconds = 0.0
    failure_metadata: dict[str, Any] = {}
    execution_metadata: dict[str, Any] = {}
    upstream_retries = 0
    repetition_retries = 0

    def emit(phase: str, attempt: int) -> None:
        timing = {"queue_seconds": round(queue_seconds, 3), "http_seconds": round(http_seconds, 3),
                  "queue_budget_seconds": queue_timeout_seconds, "request_budget_seconds": timeout_seconds,
                  "backoff_seconds": round(backoff_seconds, 3), "total_seconds": round(monotonic() - started, 3),
                  "executor_wait_seconds": round(executor_wait_seconds, 3),
                  "stage_elapsed_seconds": round(monotonic() - stage_started, 3),
                  "local_request_id": local_request_id,
                  "remaining_budget_seconds": round(max(stage_deadline - monotonic(), 0), 3),
                  **failure_metadata, **execution_metadata}
        _LOGGER.info("UZI model request: stage=%s phase=%s attempt=%s timing=%s", uzi_stage, phase, attempt, timing)
        if callable(progress_callback):
            try:
                progress_callback({"stage": uzi_stage, "request_phase": phase, "request_attempt": attempt,
                                   "request_timing": timing})
            except Exception:
                _LOGGER.warning("UZI request progress callback failed", exc_info=True)

    for attempt in range(transient_retries + 1 + int(guard_repetition)):
        try:
            check_uzi_request_control(cancellation, stage_deadline)
        except (UziRequestCancelled, UziRequestDeadline) as exc:
            emit("cancelled" if isinstance(exc, UziRequestCancelled) else "stage_deadline", attempt + 1)
            raise
        failure_metadata = {}
        execution_metadata = {}
        queue_started = monotonic()
        emit("queued", attempt + 1)
        try:
            semaphore.acquire(cancellation, queue_deadline, owner=owner)
        except UziRequestDeadline as exc:
            queue_seconds += monotonic() - queue_started
            emit("queue_timeout", attempt + 1)
            raise UziRequestDeadline("llm_queue_timeout") from exc
        except UziRequestCancelled:
            queue_seconds += monotonic() - queue_started
            emit("cancelled", attempt + 1)
            raise
        queue_seconds += monotonic() - queue_started
        request_started: float | None = None
        request_phase = "failed"
        attempt_error: Exception | None = None
        repair_repetition = False
        try:
            check_uzi_request_control(cancellation, stage_deadline)
            emit("requesting", attempt + 1)
            # Progress persistence can itself block. Recheck after the callback
            # and keep its latency out of provider-call wall time.
            check_uzi_request_control(cancellation, stage_deadline)
            if supports_timeout:
                kwargs["timeout_seconds"] = min(timeout_seconds, stage_deadline - monotonic())
            request_started = monotonic()
            result = parse(**kwargs)
            check_uzi_request_control(cancellation, stage_deadline)
            request_phase = "completed"
            return result
        except Exception as exc:
            attempt_error = exc
            kind = model_failure_kind(exc)
            failure_metadata = {"failure_kind": kind}
            if kind in {"llm_provider_unavailable", "llm_transport_timeout"}:
                failure_metadata.update(_provider_error_metadata(exc))
                failure_metadata["provider_retryable"] = _is_transient_upstream_failure(exc)
            if kind == "llm_cancelled":
                request_phase = "cancelled"
            if kind == "llm_output_repetition" and guard_repetition and repetition_retries == 0:
                repetition_retries += 1
                repair_repetition = True
                kwargs["user_prompt"] = str(kwargs.get("user_prompt") or "") + (
                    "\n上一次本阶段生成出现连续大段完全重复文本，已终止且未采纳任何不完整结果。"
                    "请从相同证据重新生成本阶段完整 JSON，每个结论只写一次；"
                    "不要续写或复述上次输出，不复制输入目录，保留所有要求的字段、维度、风险与辩论。"
                )
            elif upstream_retries < transient_retries and _is_transient_upstream_failure(exc):
                upstream_retries += 1
            else:
                raise
        finally:
            if request_started is not None:
                http_seconds += monotonic() - request_started
                metadata = getattr(provider, "last_call_metadata", {})
                if isinstance(metadata, dict):
                    keys = ("provider", "model", "reasoning_effort", "sdk_version", "runtime_version", "transport_version",
                            "adapter_source_hash", "isolation_hash", "thread_id", "turn_id", "status")
                    execution_metadata = {f"sdk_{key}": metadata[key] for key in keys
                        if isinstance(metadata.get(key), str)}
                    if isinstance(metadata.get("frozen_read_audit"), dict):
                        execution_metadata["frozen_read_audit"] = metadata["frozen_read_audit"]
                    if isinstance(metadata.get("request_trace"), dict):
                        execution_metadata["sdk_request_trace"] = metadata["request_trace"]
            semaphore.release(owner=owner)
            emit(request_phase, attempt + 1)
        if repair_repetition:
            # The provider has interrupted/reaped its runtime before releasing
            # the slot. Never renew the shared deadline or rerun another stage.
            emit("repairing_output", attempt + 1)
            queue_deadline = min(stage_deadline, monotonic() + queue_timeout_seconds)
            continue
        delay = min(retry_max_seconds, retry_base_seconds * (2 ** (upstream_retries - 1)))
        jitter = min(delay * 0.25, 5.0) * random()
        backoff_started = monotonic()
        # A server's Retry-After is a lower bound, not subject to our local
        # exponential-backoff cap. If it exceeds the shared budget, wait only
        # to that deadline and exit; never send an early retry or reset time.
        delay = max(delay + jitter, failure_metadata.get("retry_after_seconds", 0))
        delay = min(delay, max(stage_deadline - monotonic(), 0))
        emit("backoff", attempt + 1)
        if cancellation is not None:
            cancellation.wait(delay)
        else:
            sleep(delay)
        backoff_seconds += monotonic() - backoff_started
        try:
            check_uzi_request_control(cancellation, stage_deadline)
        except (UziRequestCancelled, UziRequestDeadline) as exc:
            emit("cancelled" if isinstance(exc, UziRequestCancelled) else "stage_deadline", attempt + 1)
            raise exc from attempt_error
        queue_deadline = min(stage_deadline, monotonic() + queue_timeout_seconds)
    raise AssertionError("unreachable UZI request retry state")


def _is_transient_upstream_failure(exc: Exception) -> bool:
    if model_failure_kind(exc) != "llm_provider_unavailable":
        return False
    status_retryable = _status_retryable(_provider_error_metadata(exc))
    if status_retryable is not None:
        return status_retryable
    detail = f"{exc.__class__.__name__}: {exc}".lower()
    if any(
        code in detail
        for code in ("error code: 502", "error code: 503", "error code: 504")
    ):
        return True
    if "timeout" in detail or "timed out" in detail:
        return False
    return any(
        marker in detail
        for marker in (
            "apiconnectionerror",
            "connection error",
            "connection reset",
            "connection aborted",
            "remote disconnected",
            "server disconnected",
            "bad gateway",
            "service unavailable",
            "upstream_error",
            "upstream request failed",
        )
    )


def _uzi_semaphore(limit: int) -> _RequestLimiter:
    with _SEMAPHORE_LOCK:
        semaphore = _SEMAPHORES.get(limit)
        if semaphore is None:
            semaphore = _RequestLimiter(limit)
            _SEMAPHORES[limit] = semaphore
        return semaphore


__all__ = [
    "DEFAULT_UZI_LLM_BUY_ZONE_REQUEST_TIMEOUT_SECONDS",
    "DEFAULT_UZI_LLM_MAX_CONCURRENCY",
    "DEFAULT_UZI_LLM_PROFILE_REQUEST_TIMEOUT_SECONDS",
    "DEFAULT_UZI_LLM_REQUEST_TIMEOUT_SECONDS",
    "DEFAULT_UZI_LLM_RETRY_BASE_SECONDS",
    "DEFAULT_UZI_LLM_RETRY_MAX_SECONDS",
    "DEFAULT_UZI_LLM_TRANSIENT_RETRIES",
    "UziFactualEvidenceValidationError",
    "UziQualitativeValidationError",
    "UziRequestCancelled",
    "UziRequestDeadline",
    "check_uzi_request_control",
    "parse_uzi_structured",
    "resolve_uzi_llm_request_timeout",
    "resolve_uzi_llm_worker_count",
    "submit_uzi_stage",
    "with_uzi_request_control",
]
