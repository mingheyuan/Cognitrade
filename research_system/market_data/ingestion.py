from __future__ import annotations

import json
import math
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from research_system.market_data.contracts import (
    EvidenceGrade,
    EvidenceInput,
    SnapshotIngestInput,
    SnapshotStatus,
    canonical_hash,
    canonical_json,
)
from research_system.market_data.dimension_contracts import (
    DEFAULT_DIMENSION_REGISTRY,
    DimensionContract,
    DimensionRegistry,
)
from research_system.market_data.models import (
    MarketDataConflict,
    MarketDataEvidence,
    MarketDataObservation,
    MarketDataSeriesPoint,
    MarketDataSnapshot,
)
from research_system.market_data.repository import MarketDataRepository


class SnapshotIngestionError(ValueError):
    pass


def _evidence_insert_if_absent(dialect_name: str, values: dict[str, Any]):
    """Let the unique key arbitrate concurrent writers without an UPDATE.

    In particular, do not recover SQLite's insert race with a first-statement
    SAVEPOINT: releasing it under legacy transaction control can commit the
    evidence before the caller's enclosing snapshot transaction completes.
    """
    factory = {"sqlite": sqlite_insert, "postgresql": postgresql_insert}.get(
        dialect_name
    )
    if factory is None:
        return None
    return (
        factory(MarketDataEvidence)
        .values(**values)
        .on_conflict_do_nothing(index_elements=[MarketDataEvidence.evidence_key])
        .returning(MarketDataEvidence.id)
    )


def snapshot_content_hash(
    *,
    symbol: str,
    market: str,
    exchange_timezone: str,
    dimension: str,
    contract_version: str,
    as_of_date,
    status: SnapshotStatus | str,
    evidence_grade: EvidenceGrade | str,
    payload: dict[str, Any],
    effective_at: datetime,
    published_at: datetime,
    source_version: str | None,
    source_priority: int,
    query_manifest: dict[str, Any],
    field_sources: dict[str, Any],
    series_hashes: list[str],
) -> str:
    return canonical_hash(
        {
            "symbol": symbol.strip().upper(),
            "market": market.strip().upper(),
            "exchange_timezone": exchange_timezone,
            "dimension": dimension,
            "contract_version": contract_version,
            "as_of_date": as_of_date,
            "status": status,
            "evidence_grade": evidence_grade,
            "payload": payload,
            "effective_at": effective_at,
            "published_at": published_at,
            "source_version": source_version,
            "source_priority": source_priority,
            "query_manifest": query_manifest,
            "field_sources": field_sources,
            "series_hashes": sorted(series_hashes),
        }
    )


def _path_value(payload: Any, path: str) -> tuple[bool, Any]:
    value = payload
    for part in path.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
            continue
        if isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
            continue
        return False, None
    return True, value


def _is_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (dict, list, tuple, set)):
        return bool(value)
    return True


def _minimum_grade(evidence: list[EvidenceInput]) -> EvidenceGrade:
    if not evidence:
        return EvidenceGrade.C
    rank = {EvidenceGrade.C: 0, EvidenceGrade.B: 1, EvidenceGrade.A: 2}
    return min((item.grade for item in evidence), key=rank.__getitem__)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _json_value(value: Any) -> Any:
    return json.loads(canonical_json(value))


def _date_value(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    candidates = [text, text[:10]]
    for candidate in candidates:
        try:
            return datetime.fromisoformat(candidate.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return date.fromisoformat(candidate)
            except ValueError:
                continue
    if len(text) >= 4 and text[:4].isdigit():
        year = int(text[:4])
        if 1900 <= year <= 2200:
            return date(year, 1, 1)
    return None


def _strict_date_value(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    for candidate in (text, text[:10]):
        try:
            return datetime.fromisoformat(candidate.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return date.fromisoformat(candidate)
            except ValueError:
                continue
    return None


def _strict_datetime_value(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        resolved = value
    elif isinstance(value, str) and value.strip():
        try:
            resolved = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if resolved.tzinfo is None or resolved.utcoffset() is None:
        return None
    return resolved.astimezone(timezone.utc)


def _collection_length(value: Any) -> int | None:
    if isinstance(value, (list, tuple, dict)):
        return len(value)
    return None


def _query_window_days(query_window: Any) -> int | None:
    if not isinstance(query_window, dict):
        return None
    start = _strict_date_value(query_window.get("start"))
    end = _strict_date_value(query_window.get("end"))
    if start is None or end is None or end < start:
        return None
    return (end - start).days


def _query_proof_errors(
    query: Any,
    *,
    label: str,
    expected_count: int | None = None,
    require_zero: bool = False,
    minimum_window_days: int = 0,
    allow_not_applicable: bool = False,
) -> list[str]:
    if not isinstance(query, dict):
        return [f"{label} requires a query proof object"]
    if query.get("not_applicable") is True:
        errors: list[str] = []
        if not allow_not_applicable:
            errors.append(f"{label} does not permit not_applicable")
        if not str(query.get("reason") or "").strip():
            errors.append(f"{label} not_applicable requires a reason")
        if not str(query.get("market") or "").strip():
            errors.append(f"{label} not_applicable requires a market")
        if not query.get("contract_rule"):
            errors.append(f"{label} not_applicable requires an explicit contract_rule")
        return errors

    errors = []
    query_window = query.get("query_window")
    window_days = _query_window_days(query_window)
    if window_days is None:
        errors.append(f"{label} requires a valid bounded query_window with start/end")
    elif window_days < minimum_window_days:
        errors.append(
            f"{label} query_window covers {window_days} days; requires at least {minimum_window_days}"
        )
    required_sources = query.get("required_sources")
    source_results = query.get("sources")
    if not isinstance(required_sources, list) or not required_sources:
        errors.append(f"{label} requires a non-empty required_sources list")
        required_sources = []
    normalized_sources = [str(source).strip() for source in required_sources]
    if any(not source for source in normalized_sources):
        errors.append(f"{label} required_sources cannot contain empty source IDs")
    if len(normalized_sources) != len(set(normalized_sources)):
        errors.append(f"{label} required_sources must be unique")
    if not isinstance(source_results, list):
        errors.append(f"{label} requires per-source results")
        source_results = []
    result_names = [
        str(item.get("source") or item.get("provider") or "").strip()
        for item in source_results
        if isinstance(item, dict)
    ]
    if len(result_names) != len(set(result_names)):
        errors.append(f"{label} per-source results must have unique source IDs")
    indexed = {
        str(item.get("source") or item.get("provider") or "").strip(): item
        for item in source_results
        if isinstance(item, dict)
    }
    for source in normalized_sources:
        result = indexed.get(source)
        if result is None:
            errors.append(f"required source {source!r} has no query result for {label}")
            continue
        if str(result.get("status") or "").lower() != "success":
            errors.append(f"required source {source!r} did not succeed for {label}")
        try:
            result_count = int(result.get("result_count"))
        except (TypeError, ValueError):
            errors.append(
                f"required source {source!r} has no numeric result_count for {label}"
            )
        else:
            if result_count < 0:
                errors.append(
                    f"required source {source!r} has a negative result_count for {label}"
                )
            if require_zero and result_count != 0:
                errors.append(
                    f"required source {source!r} returned {result_count} results for {label}"
                )
    try:
        aggregate_count = int(query.get("result_count"))
    except (TypeError, ValueError):
        errors.append(f"{label} requires a numeric aggregate result_count")
    else:
        if aggregate_count < 0:
            errors.append(f"{label} aggregate result_count cannot be negative")
        if require_zero and aggregate_count != 0:
            errors.append(f"{label} aggregate result_count must be zero")
        if expected_count is not None and aggregate_count != expected_count:
            errors.append(
                f"{label} aggregate result_count {aggregate_count} does not match payload count {expected_count}"
            )
    return errors


def _field_query(query_manifest: dict[str, Any], path: str) -> dict[str, Any] | None:
    field_queries = query_manifest.get("field_queries")
    if not isinstance(field_queries, dict):
        return None
    query = field_queries.get(path)
    return query if isinstance(query, dict) else None


def _coverage_claim_valid(
    query_manifest: dict[str, Any], claim_key: str, *, actual_count: int
) -> bool:
    coverage = query_manifest.get("coverage")
    if not isinstance(coverage, dict):
        return False
    claim = coverage.get(claim_key)
    if not isinstance(claim, dict) or claim.get("complete") is not True:
        return False
    if not str(claim.get("basis") or "").strip():
        return False
    try:
        return int(claim.get("result_count")) == actual_count
    except (TypeError, ValueError):
        return False


def _history_dates(value: Any, date_fields: tuple[str, ...]) -> list[date]:
    if not isinstance(value, (list, tuple)):
        return []
    dates: list[date] = []
    for item in value:
        if isinstance(item, dict):
            resolved = next(
                (
                    parsed
                    for field in date_fields
                    if (parsed := _date_value(item.get(field))) is not None
                ),
                None,
            )
        else:
            resolved = _date_value(item)
        if resolved is not None:
            dates.append(resolved)
    return dates


def _is_number(value: Any) -> bool:
    return bool(
        not isinstance(value, bool)
        and isinstance(value, (int, float, Decimal))
        and math.isfinite(float(value))
    )


def _matches_value_type(value: Any, value_type: str) -> bool:
    if value_type == "number":
        return _is_number(value)
    if value_type == "string":
        return isinstance(value, str) and bool(value.strip())
    if value_type == "object":
        return isinstance(value, dict)
    if value_type == "collection":
        return isinstance(value, (list, tuple))
    if value_type == "date":
        return _strict_date_value(value) is not None
    if value_type == "datetime":
        return _strict_datetime_value(value) is not None
    return False


def _matches_any_type(value: Any, allowed_types: tuple[str, ...]) -> bool:
    return any(_matches_value_type(value, item) for item in allowed_types)


def _number_constraint_error(value: Any, constraint: str, *, label: str) -> str | None:
    if constraint == "any":
        return None
    if not _is_number(value):
        return f"{label} must be numeric before applying {constraint} constraint"
    number = float(value)
    if constraint == "positive" and number <= 0:
        return f"{label} must be positive"
    if constraint == "nonnegative" and number < 0:
        return f"{label} must be nonnegative"
    return None


def _field_value_errors(
    payload: dict[str, Any], contract: DimensionContract
) -> list[str]:
    errors: list[str] = []
    for requirement in contract.field_value_requirements:
        found, value = _path_value(payload, requirement.path)
        if not found:
            continue
        if not _matches_any_type(value, requirement.allowed_types):
            errors.append(
                f"field {requirement.path} must be one of types "
                f"{', '.join(requirement.allowed_types)}"
            )
            continue
        number_error = _number_constraint_error(
            value,
            requirement.number_constraint,
            label=f"field {requirement.path}",
        )
        if number_error:
            errors.append(number_error)
        if (
            not requirement.item_allowed_types
            and not requirement.item_required_keys
            and not requirement.item_required_any
        ):
            continue
        if not isinstance(value, (list, tuple)):
            errors.append(f"field {requirement.path} must be a row collection")
            continue
        for index, row in enumerate(value):
            label = f"field {requirement.path}[{index}]"
            if requirement.item_allowed_types and not _matches_any_type(
                row, requirement.item_allowed_types
            ):
                errors.append(
                    f"{label} must be one of types {', '.join(requirement.item_allowed_types)}"
                )
                continue
            if not isinstance(row, dict):
                continue
            for key in requirement.item_required_keys:
                if key not in row or not _is_present(row.get(key)):
                    errors.append(f"{label} requires non-empty key {key}")
            for key, allowed_types in requirement.item_field_types.items():
                if key not in row:
                    continue
                item_value = row.get(key)
                if not _matches_any_type(item_value, allowed_types):
                    errors.append(
                        f"{label}.{key} must be one of types {', '.join(allowed_types)}"
                    )
                    continue
                item_number_error = _number_constraint_error(
                    item_value,
                    requirement.item_number_constraints.get(key, "any"),
                    label=f"{label}.{key}",
                )
                if item_number_error:
                    errors.append(item_number_error)
            for alternative in requirement.item_required_any:
                selected = next(
                    (
                        (key, row.get(key))
                        for key in alternative.keys
                        if key in row and _is_present(row.get(key))
                    ),
                    None,
                )
                if selected is None:
                    errors.append(
                        f"{label} requires one of keys {', '.join(alternative.keys)}"
                    )
                    continue
                key, item_value = selected
                if alternative.allowed_types and not _matches_any_type(
                    item_value, alternative.allowed_types
                ):
                    errors.append(
                        f"{label}.{key} must be one of types "
                        f"{', '.join(alternative.allowed_types)}"
                    )
                    continue
                alternative_number_error = _number_constraint_error(
                    item_value,
                    alternative.number_constraint,
                    label=f"{label}.{key}",
                )
                if alternative_number_error:
                    errors.append(alternative_number_error)
    return errors


def _validate_field_sources(
    required_fields: tuple[str, ...], field_sources: dict[str, Any]
) -> list[str]:
    if "$" in field_sources and _is_present(field_sources["$"]):
        return []
    missing: list[str] = []
    for required in required_fields:
        source = field_sources.get(required)
        if _is_present(source):
            continue
        if any(
            required.startswith(f"{path}.") and _is_present(value)
            for path, value in field_sources.items()
        ):
            continue
        missing.append(required)
    return missing


def _validate_verified_empty(
    contract: DimensionContract, query_manifest: dict[str, Any]
) -> list[str]:
    if not contract.permits_verified_empty:
        return [f"{contract.dimension} does not permit verified_empty"]
    policy = contract.verified_empty_policy
    is_not_applicable = query_manifest.get("not_applicable") is True
    allows_na = policy in {
        "market_not_applicable",
        "bounded_zero_or_market_not_applicable",
    }
    if is_not_applicable:
        return _query_proof_errors(
            query_manifest,
            label="verified_empty",
            require_zero=True,
            allow_not_applicable=allows_na,
        )

    allows_zero = policy in {"bounded_zero", "bounded_zero_or_market_not_applicable"}
    if not allows_zero:
        return ["this verified_empty policy only permits market not-applicable"]
    return _query_proof_errors(
        query_manifest,
        label="verified_empty",
        require_zero=True,
        minimum_window_days=contract.verified_empty_minimum_window_days,
    )


def _field_query_requirement(contract: DimensionContract, path: str):
    return next(
        (item for item in contract.field_query_requirements if item.path == path),
        None,
    )


def _field_query_errors(
    contract: DimensionContract,
    query_manifest: dict[str, Any],
    path: str,
    value: Any,
) -> list[str]:
    requirement = _field_query_requirement(contract, path)
    if requirement is None:
        return [f"field {path} has no query-proof contract"]
    count = _collection_length(value)
    expected_count = count if requirement.result_count_matches_payload else None
    return _query_proof_errors(
        _field_query(query_manifest, path),
        label=f"field {path}",
        expected_count=expected_count,
        require_zero=count == 0,
        minimum_window_days=requirement.minimum_window_days,
        allow_not_applicable=requirement.allow_not_applicable,
    )


def _empty_field_is_proven(
    contract: DimensionContract,
    query_manifest: dict[str, Any],
    path: str,
    value: Any,
) -> bool:
    requirement = _field_query_requirement(contract, path)
    return bool(
        requirement is not None
        and requirement.allow_empty
        and not _is_present(value)
        and not _field_query_errors(contract, query_manifest, path, value)
    )


def validate_available_snapshot_payload(
    *,
    payload: dict[str, Any],
    query_manifest: dict[str, Any],
    contract: DimensionContract,
) -> list[str]:
    errors: list[str] = []
    for requirement in contract.item_count_requirements:
        found, value = _path_value(payload, requirement.path)
        count = _collection_length(value) if found else None
        if count is None:
            errors.append(f"field {requirement.path} must be a collection")
            continue
        if count == 0 and requirement.zero_requires_verified_empty:
            errors.append(
                f"field {requirement.path} returned zero items; use verified_empty with bounded proof"
            )
            continue
        if (
            count == 0
            and requirement.empty_query_alternative
            and not _field_query_errors(
                contract, query_manifest, requirement.path, value
            )
        ):
            continue
        if requirement.minimum is not None and count < requirement.minimum:
            if requirement.alternative_coverage_claim and _coverage_claim_valid(
                query_manifest,
                requirement.alternative_coverage_claim,
                actual_count=count,
            ):
                continue
            errors.append(
                f"field {requirement.path} has {count} items; requires at least {requirement.minimum}"
            )
        if requirement.exact is not None and count != requirement.exact:
            errors.append(
                f"field {requirement.path} has {count} items; requires exactly {requirement.exact}"
            )

    for requirement in contract.item_status_requirements:
        found, value = _path_value(payload, requirement.path)
        if not found or not isinstance(value, (list, tuple)):
            errors.append(f"field {requirement.path} must be a row collection")
            continue
        allowed = {item.strip().lower() for item in requirement.allowed_values}
        ids: list[str] = []
        for index, row in enumerate(value):
            if not isinstance(row, dict):
                errors.append(f"field {requirement.path}[{index}] must be an object")
                continue
            status = str(row.get(requirement.status_field) or "").strip().lower()
            if status not in allowed:
                errors.append(
                    f"field {requirement.path}[{index}].{requirement.status_field} "
                    f"must be one of {', '.join(sorted(allowed))}"
                )
            if requirement.require_unique_ids:
                identifier = str(row.get(requirement.id_field or "id") or "").strip()
                if not identifier:
                    errors.append(
                        f"field {requirement.path}[{index}] requires {requirement.id_field or 'id'}"
                    )
                else:
                    ids.append(identifier)
        if requirement.require_unique_ids and len(ids) != len(set(ids)):
            errors.append(f"field {requirement.path} contains duplicate signal IDs")

    for requirement in contract.field_query_requirements:
        found, value = _path_value(payload, requirement.path)
        if not found:
            continue
        if requirement.mode == "when_empty" and _is_present(value):
            continue
        if not _is_present(value) and not requirement.allow_empty:
            errors.append(f"field {requirement.path} cannot be empty")
            continue
        errors.extend(
            _field_query_errors(contract, query_manifest, requirement.path, value)
        )

    for requirement in contract.history_span_requirements:
        found, value = _path_value(payload, requirement.path)
        dates = (
            sorted(set(_history_dates(value, requirement.date_fields))) if found else []
        )
        span_days = (max(dates) - min(dates)).days if len(dates) >= 2 else None
        if (
            len(dates) >= requirement.minimum_points
            and span_days is not None
            and span_days >= requirement.minimum_days
        ):
            continue
        query_errors: list[str] = ["query-window alternative is disabled"]
        if requirement.allow_query_window_alternative:
            query_errors = _query_proof_errors(
                _field_query(query_manifest, requirement.path),
                label=f"field {requirement.path} history",
                expected_count=_collection_length(value),
                minimum_window_days=requirement.minimum_days,
            )
        if query_errors:
            actual = 0 if span_days is None else span_days
            errors.append(
                f"field {requirement.path} history has {len(dates)} dated points over {actual} days; "
                f"requires at least {requirement.minimum_points} points and {requirement.minimum_days} days"
            )

    coverage = query_manifest.get("coverage")
    coverage = coverage if isinstance(coverage, dict) else {}
    for requirement in contract.coverage_value_requirements:
        claim = coverage.get(requirement.key)
        value = claim.get("value") if isinstance(claim, dict) else claim
        normalized = str(value or "").strip().lower()
        allowed = {item.strip().lower() for item in requirement.allowed_values}
        if normalized not in allowed:
            errors.append(
                f"coverage claim {requirement.key} must be one of {', '.join(sorted(allowed))}"
            )
    errors.extend(_field_value_errors(payload, contract))
    return list(dict.fromkeys(errors))


def validate_snapshot_contract_payload(
    *,
    status: SnapshotStatus | str,
    payload: dict[str, Any],
    query_manifest: dict[str, Any],
    field_sources: dict[str, Any],
    contract: DimensionContract,
) -> list[str]:
    try:
        resolved_status = (
            status if isinstance(status, SnapshotStatus) else SnapshotStatus(status)
        )
    except ValueError:
        return [f"unsupported snapshot status: {status}"]
    if resolved_status == SnapshotStatus.AVAILABLE:
        errors: list[str] = []
        missing_fields: list[str] = []
        for path in contract.required_fields:
            found, value = _path_value(payload, path)
            if found and (
                _is_present(value)
                or _empty_field_is_proven(contract, query_manifest, path, value)
            ):
                continue
            missing_fields.append(path)
        if missing_fields:
            errors.append(
                f"required payload fields missing: {', '.join(missing_fields)}"
            )
        missing_sources = _validate_field_sources(
            contract.required_fields, field_sources
        )
        if missing_sources:
            errors.append(
                f"required field provenance missing: {', '.join(missing_sources)}"
            )
        errors.extend(
            validate_available_snapshot_payload(
                payload=payload,
                query_manifest=query_manifest,
                contract=contract,
            )
        )
        return list(dict.fromkeys(errors))
    if resolved_status == SnapshotStatus.VERIFIED_EMPTY:
        errors = []
        if payload:
            errors.append("verified_empty payload must be empty")
        errors.extend(_validate_verified_empty(contract, query_manifest))
        return list(dict.fromkeys(errors))
    return [f"snapshot status {resolved_status.value} is not contract complete"]


def validate_persisted_snapshot_contract(
    snapshot: MarketDataSnapshot,
    *,
    contract: DimensionContract,
    expected_contract_version: str,
) -> list[str]:
    errors: list[str] = []
    if snapshot.contract_version != expected_contract_version:
        errors.append(
            f"snapshot contract version {snapshot.contract_version} does not match {expected_contract_version}"
        )
    if snapshot.dimension != contract.dimension:
        errors.append(
            f"snapshot dimension {snapshot.dimension} does not match {contract.dimension}"
        )
    validation = (
        snapshot.validation_json if isinstance(snapshot.validation_json, dict) else {}
    )
    if validation.get("passed") is not True:
        errors.append("persisted validation did not pass")
    if validation.get("final_status") != snapshot.status:
        errors.append(
            "persisted validation final_status does not match snapshot status"
        )
    if validation.get("contract_version") != expected_contract_version:
        errors.append("persisted validation contract_version does not match registry")
    if validation.get("contract_dimension") != contract.dimension:
        errors.append(
            "persisted validation contract_dimension does not match bundle slot"
        )
    errors.extend(
        validate_snapshot_contract_payload(
            status=snapshot.status,
            payload=dict(snapshot.payload_json or {}),
            query_manifest=dict(snapshot.query_manifest_json or {}),
            field_sources=dict(snapshot.field_sources_json or {}),
            contract=contract,
        )
    )
    return list(dict.fromkeys(errors))


class SnapshotIngestionService:
    def __init__(
        self,
        repository: MarketDataRepository,
        *,
        registry: DimensionRegistry = DEFAULT_DIMENSION_REGISTRY,
    ) -> None:
        self.repository = repository
        self.registry = registry

    def ingest(self, item: SnapshotIngestInput | dict[str, Any]) -> MarketDataSnapshot:
        ingest = (
            item
            if isinstance(item, SnapshotIngestInput)
            else SnapshotIngestInput.model_validate(item)
        )
        if ingest.contract_version != self.registry.contract_version:
            raise SnapshotIngestionError(
                f"contract version mismatch: {ingest.contract_version} != {self.registry.contract_version}"
            )
        try:
            contract = self.registry.get(ingest.dimension)
        except KeyError as exc:
            raise SnapshotIngestionError(str(exc)) from exc
        if ingest.status == SnapshotStatus.REVOKED:
            raise SnapshotIngestionError(
                "revocation must target an existing immutable snapshot"
            )

        validation_errors = self._validation_errors(ingest, contract)
        native_uzi_gate = str(ingest.validation.get("adapter") or "").startswith(
            "cognitrade_uzi_native_gate_"
        )
        # UZI snapshots are admitted according to their upstream native
        # quality.  The richer PIT/evidence/shape checks remain visible for
        # audit and backtest diagnostics, but are not report publication gates.
        hard_validation_errors = [] if native_uzi_gate else validation_errors
        final_status = (
            ingest.status if not hard_validation_errors else SnapshotStatus.DATA_GAP
        )
        evidence_grade = _minimum_grade(ingest.evidence)
        validation = {
            **ingest.validation,
            "contract_version": self.registry.contract_version,
            "contract_dimension": contract.dimension,
            "requested_status": ingest.status.value,
            "final_status": final_status.value,
            "passed": not hard_validation_errors,
            "errors": hard_validation_errors,
            "audit_passed": not validation_errors,
            "audit_errors": validation_errors,
        }

        series_hashes = [
            self._series_point_hash(ingest, point) for point in ingest.series_points
        ]
        content_hash = snapshot_content_hash(
            symbol=ingest.symbol,
            market=ingest.market,
            exchange_timezone=ingest.exchange_timezone,
            dimension=ingest.dimension,
            contract_version=ingest.contract_version,
            as_of_date=ingest.as_of_date,
            status=final_status,
            evidence_grade=evidence_grade,
            payload=ingest.payload,
            effective_at=ingest.effective_at,
            published_at=ingest.published_at,
            source_version=ingest.source_version,
            source_priority=ingest.source_priority,
            query_manifest=ingest.query_manifest,
            field_sources=ingest.field_sources,
            series_hashes=series_hashes,
        )

        evidence_rows = [
            self._get_or_create_evidence(evidence) for evidence in ingest.evidence
        ]
        snapshot = self.repository.find_snapshot_by_hash(content_hash)
        if snapshot is None:
            snapshot = self.repository.add_snapshot(
                MarketDataSnapshot(
                    symbol=ingest.symbol,
                    market=ingest.market,
                    exchange_timezone=ingest.exchange_timezone,
                    dimension=ingest.dimension,
                    contract_version=ingest.contract_version,
                    as_of_date=ingest.as_of_date,
                    status=final_status.value,
                    evidence_grade=evidence_grade.value,
                    payload_json=ingest.payload,
                    effective_at=ingest.effective_at,
                    published_at=ingest.published_at,
                    observed_at=ingest.observed_at,
                    ingested_at=ingest.ingested_at,
                    source_version=ingest.source_version,
                    source_priority=ingest.source_priority,
                    origin=ingest.origin,
                    query_manifest_json=ingest.query_manifest,
                    validation_json=validation,
                    field_sources_json=ingest.field_sources,
                    content_hash=content_hash,
                    collection_task_id=ingest.collection_task_id,
                )
            )

        for evidence_input, evidence in zip(
            ingest.evidence, evidence_rows, strict=True
        ):
            declared_paths = evidence_input.metadata.get("field_paths")
            field_paths = (
                [str(path) for path in declared_paths if str(path).strip()]
                if isinstance(declared_paths, list)
                else ["$"]
            )
            purpose = str(evidence_input.metadata.get("purpose") or "supports")
            for field_path in field_paths or ["$"]:
                self.repository.add_snapshot_evidence(
                    snapshot_id=snapshot.id,
                    evidence_id=evidence.id,
                    field_path=field_path,
                    purpose=purpose,
                )
        for point, point_hash in zip(ingest.series_points, series_hashes, strict=True):
            row = self.repository.find_series_point_by_hash(point_hash)
            if row is None:
                row = self.repository.add_series_point(
                    MarketDataSeriesPoint(
                        symbol=ingest.symbol,
                        market=ingest.market,
                        dimension=ingest.dimension,
                        series_key=point.series_key,
                        point_at=point.point_at,
                        effective_at=point.effective_at,
                        published_at=point.published_at,
                        observed_at=point.observed_at,
                        value_json=point.value,
                        source_version=point.source_version,
                        metadata_json=point.metadata,
                        content_hash=point_hash,
                    )
                )
            self.repository.link_snapshot_series_point(
                snapshot_id=snapshot.id, series_point_id=row.id
            )

        if final_status == SnapshotStatus.AVAILABLE:
            self._record_equal_priority_conflicts(
                snapshot, contract, cutoff_at=ingest.cutoff_at
            )

        primary_evidence = evidence_rows[0] if evidence_rows else None
        self.repository.add_observation(
            MarketDataObservation(
                snapshot_id=snapshot.id,
                evidence_id=primary_evidence.id if primary_evidence else None,
                provider=primary_evidence.provider
                if primary_evidence
                else ingest.origin,
                provider_attempt_id=ingest.provider_attempt_id,
                observed_at=ingest.observed_at,
                ingested_at=ingest.ingested_at,
                metadata_json={
                    **ingest.observation_metadata,
                    "requested_as_of_date": ingest.as_of_date.isoformat(),
                    "collection_task_id": ingest.collection_task_id,
                },
            )
        )
        return snapshot

    def _record_equal_priority_conflicts(
        self,
        snapshot: MarketDataSnapshot,
        contract: DimensionContract,
        *,
        cutoff_at: datetime,
    ) -> None:
        """Block unresolved, same-priority claims for the same effective fact.

        Different source priorities are resolved deterministically by the
        resolver. Different effective times are revisions, not conflicts.
        """

        candidates = self.repository.list_snapshots(
            symbol=snapshot.symbol,
            market=snapshot.market,
            dimension=snapshot.dimension,
            include_revoked=False,
            limit=200,
        )
        existing_conflicts = self.repository.list_open_conflicts(
            symbol=snapshot.symbol,
            market=snapshot.market,
            dimension=snapshot.dimension,
            as_of_date=snapshot.as_of_date,
            snapshot_id=snapshot.id,
        )
        conflicted_paths = {item.field_path for item in existing_conflicts}
        for other in candidates:
            if (
                other.id == snapshot.id
                or other.as_of_date != snapshot.as_of_date
                or other.contract_version != snapshot.contract_version
                or other.status != SnapshotStatus.AVAILABLE.value
                or other.source_priority != snapshot.source_priority
                or _utc(other.effective_at) != _utc(snapshot.effective_at)
            ):
                continue
            for field_path in contract.required_fields:
                if field_path in conflicted_paths:
                    continue
                left_found, left = _path_value(snapshot.payload_json, field_path)
                right_found, right = _path_value(other.payload_json, field_path)
                if (
                    not left_found
                    or not right_found
                    or canonical_hash(left) == canonical_hash(right)
                ):
                    continue
                self.repository.add_conflict(
                    MarketDataConflict(
                        symbol=snapshot.symbol,
                        market=snapshot.market,
                        dimension=snapshot.dimension,
                        as_of_date=snapshot.as_of_date,
                        cutoff_at=cutoff_at,
                        snapshot_id=snapshot.id,
                        field_path=field_path,
                        candidate_values_json=[
                            {"candidate_id": other.id, "value": _json_value(right)},
                            {"candidate_id": snapshot.id, "value": _json_value(left)},
                        ],
                        candidate_evidence_json=[
                            {
                                "snapshot_id": other.id,
                                "evidence_ids": [
                                    item.id
                                    for item in self.repository.list_snapshot_evidence(
                                        other.id
                                    )
                                ],
                            },
                            {
                                "snapshot_id": snapshot.id,
                                "evidence_ids": [
                                    item.id
                                    for item in self.repository.list_snapshot_evidence(
                                        snapshot.id
                                    )
                                ],
                            },
                        ],
                    )
                )
                conflicted_paths.add(field_path)

    def _validation_errors(
        self, ingest: SnapshotIngestInput, contract: DimensionContract
    ) -> list[str]:
        if ingest.status == SnapshotStatus.DATA_GAP:
            # Keep the adapter's concrete diagnosis.  The old implementation
            # replaced every source/normalization/evidence failure with the
            # same generic sentence, which made a non-empty upstream payload
            # indistinguishable from a real provider outage in the data
            # centre and made scoped retries impossible to reason about.
            errors = [
                str(item).strip()
                for item in list(ingest.validation.get("semantic_errors") or [])
                if str(item).strip()
            ]
            native_quality = str(
                ingest.query_manifest.get("native_quality_status") or ""
            ).strip()
            if native_quality:
                errors.append(f"native quality status: {native_quality}")
            errors.extend(
                f"collector degradation: {str(item).strip()}"
                for item in list(
                    ingest.query_manifest.get("degrade_reasons") or []
                )
                if str(item).strip()
            )
            missing_fields = [
                str(item).strip()
                for item in list(
                    ingest.query_manifest.get("missing_fields") or []
                )
                if str(item).strip()
            ]
            if missing_fields:
                errors.append(
                    "collector missing fields: " + ", ".join(missing_fields)
                )
            return list(dict.fromkeys(errors)) or ["collector reported data_gap"]
        errors: list[str] = []
        cutoff_at = ingest.cutoff_at
        if ingest.published_at > cutoff_at:
            errors.append("snapshot published_at is later than the requested cutoff")
        if (
            _minimum_grade(ingest.evidence) == EvidenceGrade.A
            and ingest.observed_at > cutoff_at
        ):
            errors.append("grade A snapshot was not observed by cutoff")
        if len(ingest.evidence) < contract.min_evidence_count:
            errors.append(
                f"dimension requires at least {contract.min_evidence_count} evidence records"
            )
        for evidence in ingest.evidence:
            if evidence.published_at > cutoff_at:
                errors.append(
                    f"evidence from {evidence.provider} was published after cutoff"
                )
            if evidence.grade == EvidenceGrade.A and evidence.observed_at > cutoff_at:
                errors.append(
                    f"grade A evidence from {evidence.provider} was not observed by cutoff"
                )
            if evidence.grade == EvidenceGrade.C:
                errors.append(
                    f"grade C evidence from {evidence.provider} is not report eligible"
                )
        if _minimum_grade(ingest.evidence) == EvidenceGrade.C:
            errors.append("snapshot has no A/B evidence")
        for point in ingest.series_points:
            if point.published_at > cutoff_at:
                errors.append(
                    f"series point {point.series_key} was published after cutoff"
                )
            if point.observed_at > ingest.ingested_at:
                errors.append(
                    f"series point {point.series_key} was observed after ingestion"
                )
        if ingest.status in {SnapshotStatus.AVAILABLE, SnapshotStatus.VERIFIED_EMPTY}:
            errors.extend(
                validate_snapshot_contract_payload(
                    status=ingest.status,
                    payload=ingest.payload,
                    query_manifest=ingest.query_manifest,
                    field_sources=ingest.field_sources,
                    contract=contract,
                )
            )
        if ingest.status == SnapshotStatus.AVAILABLE:
            try:
                from research_system.market_data.fund_holdings_contracts import (
                    FUND_HOLDINGS_CONTRACT_VERSION,
                    FundHoldingsSemanticError,
                    validate_fund_top_holdings_ingest,
                )

                if self.registry.contract_version == FUND_HOLDINGS_CONTRACT_VERSION:
                    validate_fund_top_holdings_ingest(ingest)
            except FundHoldingsSemanticError as exc:
                errors.extend(f"fund holdings semantics: {item}" for item in exc.errors)
        return list(dict.fromkeys(errors))

    def _get_or_create_evidence(self, evidence: EvidenceInput) -> MarketDataEvidence:
        evidence_key = evidence.evidence_key()
        existing = self.repository.find_evidence_by_key(evidence_key)
        if existing is not None:
            self._validate_evidence_reuse(existing, evidence)
            return existing
        values = {
            "evidence_key": evidence_key,
            "provider": evidence.provider,
            "source_url": evidence.source_url,
            "source_kind": evidence.source_kind,
            "title": evidence.title,
            "mime_type": evidence.mime_type,
            "content_hash": evidence.resolved_content_hash(),
            "content_ref": evidence.content_ref,
            "inline_content": evidence.inline_content,
            "published_at": evidence.published_at,
            "observed_at": evidence.observed_at,
            "grade": evidence.grade.value,
            "immutable_proof": evidence.immutable_proof,
            "is_manual": evidence.is_manual,
            "metadata_json": evidence.metadata,
        }
        session = self.repository.session
        statement = _evidence_insert_if_absent(session.get_bind().dialect.name, values)
        if statement is None:
            # Preserve the existing path for other dialects; the application
            # supports SQLite and PostgreSQL for atomic concurrent ingestion.
            return self.repository.add_evidence(MarketDataEvidence(**values))
        inserted_id = session.execute(statement).scalar_one_or_none()
        row = (
            self.repository.get_evidence(inserted_id)
            if inserted_id is not None
            else self.repository.find_evidence_by_key(evidence_key)
        )
        if row is None:
            raise SnapshotIngestionError("concurrent evidence winner is not visible")
        self._validate_evidence_reuse(row, evidence)
        return row

    @staticmethod
    def _validate_evidence_reuse(
        existing: MarketDataEvidence, evidence: EvidenceInput
    ) -> None:
        """Only identical content may reuse a winning immutable evidence row.

        Collection time, title, local path and dimension-specific use metadata
        are not content identity. Keep the first stored values unchanged; the
        caller records its own snapshot association and observation separately.
        A different known body under the same claimed hash is not a harmless
        uniqueness race and must fail the enclosing transaction.
        """
        from research_system.market_data.research_archive_text import (
            text_binding_identity,
        )

        published = existing.published_at
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        identity_matches = (
            existing.content_hash == evidence.resolved_content_hash()
            and existing.provider == evidence.provider
            and existing.source_url == evidence.source_url
            and existing.source_kind == evidence.source_kind
            and published == evidence.published_at
            and existing.grade == evidence.grade.value
            and existing.immutable_proof == evidence.immutable_proof
            and existing.is_manual == evidence.is_manual
            and (
                existing.source_kind != "research_document"
                or text_binding_identity(existing.metadata_json or {})
                == text_binding_identity(evidence.metadata)
            )
        )
        known_body_conflicts = (
            existing.inline_content is not None
            and evidence.inline_content is not None
            and existing.inline_content != evidence.inline_content
        )
        if not identity_matches or known_body_conflicts:
            raise SnapshotIngestionError("evidence content identity conflict")

    @staticmethod
    def _series_point_hash(ingest: SnapshotIngestInput, point) -> str:
        return canonical_hash(
            {
                "symbol": ingest.symbol,
                "market": ingest.market,
                "dimension": ingest.dimension,
                "series_key": point.series_key,
                "point_at": point.point_at,
                "effective_at": point.effective_at,
                "published_at": point.published_at,
                "value": point.value,
                "source_version": point.source_version,
                "metadata": point.metadata,
            }
        )


__all__ = [
    "SnapshotIngestionError",
    "SnapshotIngestionService",
    "snapshot_content_hash",
    "validate_available_snapshot_payload",
    "validate_persisted_snapshot_contract",
    "validate_snapshot_contract_payload",
]
