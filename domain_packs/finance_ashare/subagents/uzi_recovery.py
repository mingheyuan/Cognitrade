"""Recovery decisions shared by native UZI analysis and its batch supervisor.

Recovery changes execution, never the original self-review or trading verdict.
In particular another model request cannot repair unchanged source evidence.
"""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from domain_packs.finance_ashare.subagents.uzi_llm_support import (
    EXHAUSTED_MODEL_FAILURES,
)


def content_hash(value: Any) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


_RECOVERY_RUNTIME_FIELDS = frozenset({
    "artifact_id", "run_id", "request_id", "thread_id", "reviewed_at", "created_at", "updated_at",
    "generated_at", "cycle", "analysis_cycle", "elapsed_seconds", "duration_seconds", "retry_count",
    "request_timing", "recovery_history", "recovery_progress", "computation_diagnostics", "self_review",
})
_RECOVERY_FACT_CONTAINERS = frozenset({
    "raw", "facts", "data", "source_refs", "evidence", "evidence_records", "query_manifest",
    "snapshots", "snapshot_rows", "auxiliary_input",
})


def stable_recovery_value(value: Any) -> Any:
    """Ignore runtime churn, never source facts/provenance with the same names.

    This is a recovery-only view. It never edits an Artifact/Manifest or changes
    its canonical content hash. Original review issues remain stored separately.
    """
    if isinstance(value, dict):
        return {key: item if key in _RECOVERY_FACT_CONTAINERS else stable_recovery_value(item)
                for key, item in value.items() if key not in _RECOVERY_RUNTIME_FIELDS}
    if isinstance(value, list):
        return [stable_recovery_value(item) for item in value]
    return value


def recovery_content_hash(value: Any) -> str:
    """Hash the recovery view without allocating another full Bundle JSON string."""
    digest = sha256()
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    for chunk in encoder.iterencode(stable_recovery_value(value)):
        digest.update(chunk.encode())
    return digest.hexdigest()


def recovery_issue_signature(issues: list[dict[str, Any]]) -> tuple[Any, ...]:
    """Problem types and affected fields, not volatile evidence/error messages."""
    return tuple(sorted({(
        str(issue.get("severity") or ""), str(issue.get("category") or ""),
        str(issue.get("dim") or ""), str(issue.get("issue") or ""),
        tuple(sorted((str(failure.get("stage") or ""), str(failure.get("kind") or ""),
                      tuple(sorted(str(field) for field in failure.get("validation_fields") or [])))
                     for failure in issue.get("failure_records") or [] if isinstance(failure, dict))),
    ) for issue in issues}))


def recovery_progress_state(*, input_hash: str, outputs: Any, issues: list[dict[str, Any]]) -> dict[str, str]:
    state = {"schema_version": "uzi-recovery-progress-v1", "input_hash": input_hash,
             "output_hash": recovery_content_hash(outputs),
             "issues_hash": content_hash(recovery_issue_signature(issues))}
    return {**state, "state_hash": content_hash(state)}


def repairable_output_failures(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A valid output repair cannot replace missing input or renew a spent stage."""
    blocked = {row.get("stage") for row in records
               if row.get("kind") in {*EXHAUSTED_MODEL_FAILURES, "source_gap", "pipeline_failure"}}
    return [row for row in records if row.get("kind") == "agent_content_invalid"
            and row.get("stage") not in blocked]


def native_recovery(content: dict[str, Any], *, run_status: str = "") -> dict[str, Any]:
    """Project current and legacy native artifacts into the same decision."""
    results = [row for row in content.get("results") or [] if isinstance(row, dict)]
    issues = [issue for row in results for issue in (row.get("self_review") or {}).get("issues") or []
              if isinstance(issue, dict) and issue.get("severity") == "critical"]
    history = [row for row in content.get("recovery_history") or [] if isinstance(row, dict)]
    exhausted = any(row.get("exhausted_reason") for row in history)
    current_histories = [row["recovery_history"] for row in results
                         if isinstance(row.get("recovery_history"), list)] or [history]
    no_progress = any(rows and isinstance(rows[-1], dict)
                      and rows[-1].get("exhausted_reason") == "unchanged_recovery_state_after_recompute"
                      for rows in current_histories)
    # A dimension named in an output error is not proof of absent source
    # data. Only native data issues and actual input diagnostics authorize
    # acquisition. In particular zero model citations may be repairable.
    dimensions = sorted({str(row.get("dim") or "") for row in issues
                         if row.get("category") == "data"
                         and str(row.get("dim") or "").split("_", 1)[0].isdigit()})
    dimensions = sorted(set(dimensions) | {
        str(gap).split(":", 1)[0] for row in results
        for gap in (row.get("qualitative_execution") or {}).get("input_gaps") or []
        if str(gap).endswith(":frozen_citations_unavailable")
    })
    evidence = " ".join(str(row.get("evidence") or "") for row in issues)
    failure_records = list({
        (str(failure.get("stage")), str(failure.get("kind"))): dict(failure)
        for owner in [*issues, *[
            (row.get("self_review") or {}).get("execution_status") or {}
            for row in results
        ]]
        for failure in owner.get("failure_records") or []
        if isinstance(failure, dict)
    }.values())
    failure_kinds = {row.get("kind") for row in failure_records}
    source_gap = "source_gap" in failure_kinds or bool(dimensions) or any(
        row.get("category") == "data" or (
            row.get("dim") == "qualitative_deep_dive"
            and any(code in str(row.get("evidence") or "") for code in ("evidence_preflight_failed", "requires_two_cited_sources", "required_but_unavailable"))
        ) for row in issues
    )
    pending_evidence = any((row.get("qualitative_execution") or {}).get("pending_evidence") for row in results)
    if run_status == "cancelled" or "llm_cancelled" in failure_kinds:
        kind, action = "cancelled", "none"
    elif failure_kinds & {"llm_queue_timeout", "llm_stage_deadline", "llm_transport_timeout"} or (
        not failure_records and ("queue_timeout" in evidence or "deadline" in evidence or "llm_transport_timeout" in evidence)
    ):
        kind, action = "deadline_exceeded", "resume_stage"
    elif source_gap:
        kind, action = "source_gap", "collect_data"
    elif pending_evidence:
        kind, action = "evidence_pending", "verify_sources"
    elif "llm_provider_unavailable" in failure_kinds or (not failure_records and "llm_provider_unavailable" in evidence):
        kind, action = "provider_unavailable", "inspect_provider"
    elif "pipeline_failure" in failure_kinds:
        kind, action = "pipeline_failure", "inspect"
    elif "llm_output_repetition" in failure_kinds:
        kind, action = "output_invalid", "repair_stage"
    elif content.get("report_allowed") is True and not issues:
        kind, action = "complete", "none"
    elif no_progress:
        kind, action = "no_progress", "inspect"
    elif issues:
        kind, action = "output_invalid", "repair_stage"
    elif not content and run_status in {"running", "paused"}:
        kind, action = "worker_interrupted", "resume_stage"
    else:
        kind, action = "unknown", "inspect"
    # Stage retries already ran under the request budget. The supervisor only
    # resumes interrupted execution, not exhausted analyses or data repair.
    retryable = kind == "worker_interrupted" and not exhausted
    return {
        "schema_version": "uzi-recovery-v1", "kind": kind, "action": action,
        "retryable": retryable, "manual_retryable": kind not in {"complete", "cancelled"},
        "dimensions": dimensions,
        "output_dimensions": sorted({
            str(error).split(":", 1)[0] for row in results
            for error in (row.get("qualitative_execution") or {}).get("errors") or []
            if str(error).split("_", 1)[0].isdigit()
        } - set(dimensions)),
        "stages": sorted({str(row.get("dim") or "") for row in issues if row.get("dim")}),
        "input_hash": content_hash({"source_commit": content.get("source_commit"), "profile": content.get("profile"),
                                     "raw": [row.get("raw") for row in results]}),
        "reason_codes": sorted({kind, *(["new_manifest_required"] if source_gap or pending_evidence else []),
                                 *[str(row["exhausted_reason"]) for row in history if row.get("exhausted_reason")]}),
        "failure_records": failure_records,
        "repairable_stages": sorted({str(row["stage"]) for row in repairable_output_failures(failure_records)}),
        "exhausted_stages": sorted({str(row["stage"]) for row in failure_records if row.get("kind") in EXHAUSTED_MODEL_FAILURES}),
    }
