"""Explicit capture and immutable replay of pinned UZI's quant-style inputs.

These are auxiliary business responses, not research citations or native
scores. Only collection calls a source; report materialization reads fixed
evidence IDs, and scoring consumes the resulting recording without a database.
"""

from __future__ import annotations

import base64
import gzip
import importlib
import json
import sys
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from threading import Lock
from time import monotonic
from zoneinfo import ZoneInfo

from sqlalchemy import select

from research_system.market_data.contracts import (
    canonical_hash,
    canonical_json,
    cutoff_at_for_date,
)
from research_system.market_data.models import (
    MarketDataCollectionJob,
    MarketDataCollectionTask,
    MarketDataEvidence,
)
from research_system.market_data.repository import MarketDataRepository

VERSION = "native-quant-aux-v1"
REPLAY_POLICY = "native-optional-auxiliary-v2"
NATIVE_COMMIT = "b3665e85b8666477038e11157d8e8feb0e006b76"
HOLDINGS_YEAR = (
    "2025"  # Pinned lib.quant_signal.HOLDINGS_QUARTER, not a host correction.
)
_RESPONSE_PROVIDER = "uzi.native_quant_auxiliary.response"
_RECEIPT_PROVIDER = "uzi.native_quant_auxiliary.receipt"
_COMPLETE = {"available", "verified_empty"}
_QUANT_INPUT_DIMENSIONS = frozenset({"6_fund_holders"})


class QuantAuxiliaryInputError(ValueError):
    def __init__(self, code: str, *, missing_requests=()):
        super().__init__(code)
        self.code = code
        self.missing_requests = list(missing_requests)


def _utc(value):
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def quant_input_hash(raw):
    return canonical_hash(
        {
            "ticker_code": str(raw.get("ticker") or "").split(".")[0].strip(),
            "fund_managers": raw.get("fund_managers") or [],
        }
    )


def _request(operation, **parameters):
    return {"operation": operation, "parameters": parameters}


def _native_module():
    from domain_packs.finance_ashare.vendor.uzi_3_9_2.runtime import (
        Uzi392FetcherRuntime,
    )

    if "lib.quant_signal" not in sys.modules:
        Uzi392FetcherRuntime(
            workspace_root=Path(__file__).resolve().parents[2] / "workspace"
        )
    module = importlib.import_module("lib.quant_signal")
    if module.HOLDINGS_QUARTER != HOLDINGS_YEAR:
        raise QuantAuxiliaryInputError("native_quant_version_mismatch")
    return module


class NativeQuantSource:
    """Execute the pinned fetch helpers, recording their otherwise swallowed errors."""

    def fetch(self, operation, parameters, *, deadline, cancellation=None):
        from domain_packs.finance_ashare.vendor.uzi_3_9_2.runtime import (
            UZI_ENVIRONMENT_LOCK,
        )
        from research_system.adapters.vendors.uzi_request_timeout import (
            uzi_request_timeout_scope,
        )

        calls = []

        class ObservedAkshare:
            def __init__(self, delegate):
                self.delegate = delegate

            def __getattr__(self, name):
                target = getattr(self.delegate, name)

                def invoke(*args, **kwargs):
                    event = {"method": name, "parameters": kwargs}
                    calls.append(event)
                    try:
                        if monotonic() >= deadline or (
                            cancellation and cancellation.is_set()
                        ):
                            raise TimeoutError(
                                "auxiliary acquisition deadline or cancellation"
                            )
                        result = target(*args, **kwargs)
                        event.update(
                            status="success"
                            if result is not None
                            else "unverified_empty",
                            result_count=len(result) if result is not None else 0,
                        )
                        if result is None:
                            event["error_type"] = "empty_response_unverified"
                        return result
                    except Exception as exc:
                        event.update(status="failed", error_type=type(exc).__name__)
                        raise

                return invoke

        with UZI_ENVIRONMENT_LOCK:
            module = _native_module()
            holders = importlib.import_module("fetch_fund_holders")
            if module.ak is None or holders.ak is None:
                return {
                    "status": "source_error",
                    "rows": [],
                    "errors": ["akshare_not_available"],
                }
            previous = module.ak, holders.ak, module.cached
            try:
                module.ak = ObservedAkshare(module.ak)
                holders.ak = ObservedAkshare(holders.ak)
                # Persist our own dated, hashed responses. An undated native
                # TTL cache entry cannot prove a historical observation.
                module.cached = lambda _ticker, _key, fetch, **_kwargs: fetch()
                with uzi_request_timeout_scope(
                    total_timeout_seconds=max(0.01, deadline - monotonic())
                ):
                    if operation == "all_holding_funds":
                        rows = module._fetch_all_holding_funds(**parameters)
                    elif operation == "top_holdings":
                        args = dict(parameters)
                        if args.pop("holdings_year") != module.HOLDINGS_QUARTER:
                            raise QuantAuxiliaryInputError(
                                "native_quant_version_mismatch"
                            )
                        rows = module._fetch_top_holdings(**args)
                    else:
                        raise QuantAuxiliaryInputError("native_quant_unknown_request")
            finally:
                module.ak, holders.ak, module.cached = previous
        # An empty list after a swallowed transport failure is NOT verified empty.
        confirmed = bool(calls and calls[-1]["status"] == "success")
        return {
            "status": "available"
            if rows and confirmed
            else "verified_empty"
            if confirmed
            else "source_error",
            "rows": rows,
            "source_requests": calls,
            "errors": [row["error_type"] for row in calls if row.get("error_type")],
        }


def _store(repository, body, *, provider, title, now):
    text = canonical_json(body)
    if len(text.encode("utf-8")) > 16 * 1024 * 1024:
        raise QuantAuxiliaryInputError("native_quant_recording_too_large")
    encoding = "json"
    if len(text.encode("utf-8")) > 262_144:
        text = base64.b64encode(gzip.compress(text.encode("utf-8"), mtime=0)).decode(
            "ascii"
        )
        encoding = "gzip+base64"
    if len(text.encode("utf-8")) > 262_144:
        raise QuantAuxiliaryInputError("native_quant_recording_too_large")
    digest = canonical_hash(body)
    key = canonical_hash(
        {
            "provider": provider,
            "title": title,
            "content_hash": digest,
        }
    )
    evidence = repository.find_evidence_by_key(key)
    if evidence is None:
        evidence = repository.add_evidence(
            MarketDataEvidence(
                evidence_key=key,
                provider=provider,
                source_kind="native_auxiliary_response",
                title=title,
                mime_type="application/json",
                content_hash=digest,
                inline_content=text,
                published_at=now,
                observed_at=now,
                grade="A",
                immutable_proof=False,
                metadata_json={
                    "version": VERSION,
                    "native_commit": NATIVE_COMMIT,
                    "encoding": encoding,
                },
            )
        )
    return {"evidence_id": evidence.id, "content_hash": evidence.content_hash}


def _read(repository, ref, *, provider, cutoff_at=None):
    evidence = repository.get_evidence(str(ref.get("evidence_id") or ""))
    if (
        evidence is None
        or evidence.provider != provider
        or evidence.content_hash != ref.get("content_hash")
    ):
        raise QuantAuxiliaryInputError("native_quant_evidence_mismatch")
    # A receipt is a newly generated index, not a historical market fact.
    # Its constituent responses must each have been observed by the cutoff.
    if (
        provider != _RECEIPT_PROVIDER
        and cutoff_at is not None
        and (
            _utc(evidence.observed_at) > _utc(cutoff_at)
            or _utc(evidence.published_at) > _utc(cutoff_at)
        )
    ):
        raise QuantAuxiliaryInputError("native_quant_pit_violation")
    text = evidence.inline_content or "null"
    if (evidence.metadata_json or {}).get("encoding") == "gzip+base64":
        with gzip.GzipFile(fileobj=BytesIO(base64.b64decode(text))) as compressed:
            expanded = compressed.read(16 * 1024 * 1024 + 1)
        if len(expanded) > 16 * 1024 * 1024:
            raise QuantAuxiliaryInputError("native_quant_recording_too_large")
        text = expanded.decode("utf-8")
    body = json.loads(text)
    if not isinstance(body, dict) or canonical_hash(body) != evidence.content_hash:
        raise QuantAuxiliaryInputError("native_quant_evidence_mismatch")
    return body


def _binding(ref, receipt):
    return {
        "version": VERSION,
        **ref,
        "input_hash": receipt["input_hash"],
        "status": receipt["status"],
        "reason_codes": receipt["reason_codes"],
    }


def _receipt_title(raw, as_of_date):
    return canonical_hash(
        {
            "input_hash": quant_input_hash(raw),
            "as_of_date": as_of_date.isoformat(),
            "version": VERSION,
        }
    )


def find_quant_auxiliary_binding(repository, *, raw, as_of_date, cutoff_at):
    rows = repository.session.scalars(
        select(MarketDataEvidence)
        .where(
            MarketDataEvidence.provider == _RECEIPT_PROVIDER,
            MarketDataEvidence.title == _receipt_title(raw, as_of_date),
        )
        .order_by(MarketDataEvidence.observed_at.desc(), MarketDataEvidence.id.desc())
    )
    incomplete = None
    for evidence in rows:
        ref = {"evidence_id": evidence.id, "content_hash": evidence.content_hash}
        receipt = _read(
            repository, ref, provider=_RECEIPT_PROVIDER, cutoff_at=cutoff_at
        )
        binding = _binding(ref, receipt)
        try:
            load_quant_auxiliary_recording(
                repository, binding=binding, raw=raw, cutoff_at=cutoff_at
            )
        except QuantAuxiliaryInputError as exc:
            if exc.code != "native_quant_pit_violation":
                raise
            continue
        if receipt["status"] == "available":
            return binding
        if incomplete is None:
            incomplete = binding
    if incomplete is not None:
        return incomplete
    return {
        "version": VERSION,
        "input_hash": quant_input_hash(raw),
        "status": "unassessed",
        "reason_codes": ["native_quant_inputs_missing"],
    }


def collect_quant_auxiliary_inputs(
    database,
    *,
    raw,
    as_of_date: date,
    source=None,
    now=None,
    clock=None,
    deadline=None,
    cancellation=None,
    force=False,
):
    """Explicit collection only. Current sources cannot backfill a past response."""
    supplied_now = now
    now = _utc(now or (clock() if clock else datetime.now(timezone.utc)))

    def observed_now():
        return (
            _utc(clock())
            if clock
            else now
            if supplied_now is not None
            else datetime.now(timezone.utc)
        )

    deadline = deadline if deadline is not None else monotonic() + 600
    cutoff = min(now, cutoff_at_for_date(as_of_date, "Asia/Shanghai"))
    with database.session() as session:
        existing = find_quant_auxiliary_binding(
            MarketDataRepository(session),
            raw=raw,
            as_of_date=as_of_date,
            cutoff_at=cutoff,
        )
    historical_request = as_of_date != now.astimezone(ZoneInfo("Asia/Shanghai")).date()
    if (not force or historical_request) and existing["status"] == "available":
        return existing
    source = source or NativeQuantSource()
    references, responses, missing = {}, {}, []

    def obtain(request):
        key = canonical_hash(request)
        if key in responses:
            return responses[key]
        title = canonical_hash(
            {
                "version": VERSION,
                "as_of_date": as_of_date.isoformat(),
                "request": request,
            }
        )
        if not force or historical_request:
            with database.session() as session:
                repository = MarketDataRepository(session)
                candidates = session.scalars(
                    select(MarketDataEvidence)
                    .where(
                        MarketDataEvidence.provider == _RESPONSE_PROVIDER,
                        MarketDataEvidence.title == title,
                        MarketDataEvidence.observed_at <= cutoff,
                        MarketDataEvidence.published_at <= cutoff,
                    )
                    .order_by(
                        MarketDataEvidence.observed_at.desc(),
                        MarketDataEvidence.id.desc(),
                    )
                )
                for evidence in candidates:
                    ref = {
                        "evidence_id": evidence.id,
                        "content_hash": evidence.content_hash,
                    }
                    body = _read(
                        repository, ref, provider=_RESPONSE_PROVIDER, cutoff_at=cutoff
                    )
                    if body["status"] in _COMPLETE:
                        references[key], responses[key] = ref, body
                        return body
        historical = (
            as_of_date != observed_now().astimezone(ZoneInfo("Asia/Shanghai")).date()
        )
        reason = (
            "historical_auxiliary_unavailable"
            if historical
            else "collection_cancelled"
            if cancellation and cancellation.is_set()
            else "deadline_exhausted"
            if monotonic() >= deadline
            else None
        )
        if reason:
            body = {
                "request": request,
                "status": "unassessed",
                "rows": [],
                "errors": [reason],
            }
        else:
            try:
                body = {
                    **source.fetch(
                        request["operation"],
                        deepcopy(request["parameters"]),
                        deadline=deadline,
                        cancellation=cancellation,
                    ),
                    "request": request,
                }
                if body.get("status") not in _COMPLETE | {
                    "source_error"
                } or not isinstance(body.get("rows"), list):
                    raise QuantAuxiliaryInputError(
                        "native_quant_source_response_invalid"
                    )
                canonical_json(body)
            except Exception as exc:  # noqa: BLE001 - retain a typed failure of one explicit source request
                body = {
                    "request": request,
                    "status": "source_error",
                    "rows": [],
                    "errors": [getattr(exc, "code", type(exc).__name__)],
                }
        observed_at = observed_now()
        responses[key] = body
        if body["status"] not in _COMPLETE:
            missing.append(
                {
                    "request_hash": key,
                    "reason_codes": body.get("errors") or ["source_error"],
                }
            )
        elif observed_at > cutoff_at_for_date(as_of_date, "Asia/Shanghai"):
            missing.append(
                {"request_hash": key, "reason_codes": ["native_quant_pit_violation"]}
            )
        with database.session() as session:
            references[key] = _store(
                MarketDataRepository(session),
                body,
                provider=_RESPONSE_PROVIDER,
                title=title,
                now=observed_at,
            )
        return body

    managers = raw.get("fund_managers") or []
    if len(managers) < 20:
        body = obtain(
            _request(
                "all_holding_funds",
                ticker_code=str(raw.get("ticker") or "").split(".")[0].strip(),
                max_funds=80,
            )
        )
        if body["status"] in _COMPLETE and body["rows"]:
            managers = body["rows"]
    for manager in managers:
        if manager.get("fund_code"):
            obtain(
                _request(
                    "top_holdings",
                    fund_code=str(manager["fund_code"]),
                    top_n=10,
                    holdings_year=HOLDINGS_YEAR,
                )
            )
    receipt = {
        "version": VERSION,
        "native_commit": NATIVE_COMMIT,
        "input_hash": quant_input_hash(raw),
        "as_of_date": as_of_date.isoformat(),
        "responses": references,
        "status": "unassessed" if missing else "available",
        "missing_requests": missing,
        "reason_codes": sorted(
            {reason for row in missing for reason in row["reason_codes"]}
        ),
    }
    with database.session() as session:
        ref = _store(
            MarketDataRepository(session),
            receipt,
            provider=_RECEIPT_PROVIDER,
            title=_receipt_title(raw, as_of_date),
            now=observed_now(),
        )
    return _binding(ref, receipt)


def load_quant_auxiliary_recording(repository, *, binding, raw, cutoff_at):
    if not binding.get("evidence_id"):
        return {"binding": deepcopy(binding), "receipt": None, "responses": {}}
    receipt = _read(
        repository, binding, provider=_RECEIPT_PROVIDER, cutoff_at=cutoff_at
    )
    if (
        date.fromisoformat(receipt["as_of_date"])
        != _utc(cutoff_at).astimezone(ZoneInfo("Asia/Shanghai")).date()
    ):
        raise QuantAuxiliaryInputError("native_quant_pit_violation")
    responses = {
        key: _read(repository, ref, provider=_RESPONSE_PROVIDER, cutoff_at=cutoff_at)
        for key, ref in receipt["responses"].items()
    }
    recording = {
        "binding": deepcopy(binding),
        "receipt": receipt,
        "responses": responses,
    }
    validate_quant_auxiliary_recording(recording, raw=raw)
    return recording


def validate_quant_auxiliary_recording(recording, *, raw, binding=None):
    """Pure recovery check: no DB, current evidence, network, or filesystem."""
    pinned = recording.get("binding") or {}
    if binding is not None and pinned != binding:
        raise QuantAuxiliaryInputError("native_quant_binding_mismatch")
    if pinned.get("version") != VERSION or pinned.get("input_hash") != quant_input_hash(
        raw
    ):
        raise QuantAuxiliaryInputError("native_quant_input_mismatch")
    receipt = recording.get("receipt")
    if receipt is None:
        if (
            pinned.get("evidence_id")
            or pinned.get("content_hash")
            or pinned.get("status") != "unassessed"
            or recording.get("responses")
        ):
            raise QuantAuxiliaryInputError("native_quant_recording_mismatch")
        return
    if (
        canonical_hash(receipt) != pinned.get("content_hash")
        or receipt.get("native_commit") != NATIVE_COMMIT
        or receipt.get("version") != VERSION
    ):
        raise QuantAuxiliaryInputError("native_quant_recording_mismatch")
    if receipt.get("input_hash") != pinned["input_hash"] or receipt.get(
        "status"
    ) != pinned.get("status"):
        raise QuantAuxiliaryInputError("native_quant_input_mismatch")
    responses = recording.get("responses") or {}
    if set(responses) != set(receipt.get("responses") or {}):
        raise QuantAuxiliaryInputError("native_quant_recording_mismatch")
    for key, response in responses.items():
        if (
            canonical_hash(response) != receipt["responses"][key]["content_hash"]
            or canonical_hash(response.get("request")) != key
        ):
            raise QuantAuxiliaryInputError("native_quant_recording_mismatch")


@contextmanager
def replay_quant_auxiliary_recording(recording, *, raw, binding=None):
    """Replay fixed responses with native optional-helper failure semantics.

    Native fetch helpers return [] on source failure; this is not evidence of
    an empty fund universe. Preserve that control flow, disclose unavailable
    requests separately, and never weaken validation of the frozen recording.
    """
    from domain_packs.finance_ashare.vendor.uzi_3_9_2.runtime import (
        UZI_ENVIRONMENT_LOCK,
    )

    validate_quant_auxiliary_recording(recording, raw=raw, binding=binding)
    unavailable, lock = {}, Lock()
    diagnostics = {
        "status": "unassessed",
        "replay_policy": REPLAY_POLICY,
        "input_hash": quant_input_hash(raw),
        "replayed_requests": 0,
    }

    def response(request):
        value = (recording.get("responses") or {}).get(canonical_hash(request))
        with lock:
            if not value or value.get("status") not in _COMPLETE:
                unavailable[canonical_hash(request)] = {
                    "request": deepcopy(request),
                    "reason_codes": list((value or {}).get("errors") or ["native_quant_inputs_missing"]),
                }
                # Both original helpers catch source exceptions and return an
                # empty list. Raising here would skip still-usable managers or
                # stop parallel top-holdings checks, unlike the native code.
                return []
            diagnostics["replayed_requests"] += 1
        return deepcopy(value["rows"])

    with UZI_ENVIRONMENT_LOCK:
        module = _native_module()
        previous = module._fetch_all_holding_funds, module._fetch_top_holdings
        try:
            module._fetch_all_holding_funds = lambda ticker_code, max_funds=80: (
                response(
                    _request(
                        "all_holding_funds",
                        ticker_code=ticker_code,
                        max_funds=max_funds,
                    )
                )
            )
            module._fetch_top_holdings = lambda fund_code, top_n=10: response(
                _request(
                    "top_holdings",
                    fund_code=fund_code,
                    top_n=top_n,
                    holdings_year=module.HOLDINGS_QUARTER,
                )
            )
            yield diagnostics
            diagnostics.update(
                unavailable_responses=list(unavailable.values()),
                missing_requests=[row["request"] for row in unavailable.values()],
                missing_request_count=len(unavailable),
                reason_codes=sorted({code for row in unavailable.values() for code in row["reason_codes"]}),
                report_blocking=False,
            )
            diagnostics["status"] = (
                "degraded" if unavailable
                else "available" if diagnostics["replayed_requests"] else "not_required"
            )
        finally:
            module._fetch_all_holding_funds, module._fetch_top_holdings = previous


@contextmanager
def replay_quant_auxiliary_inputs(database, *, binding, raw):
    """Convenience for offline tests; production analysis uses the pure scope."""
    with database.session() as session:
        receipt = _read(
            MarketDataRepository(session), binding, provider=_RECEIPT_PROVIDER
        )
        recording = load_quant_auxiliary_recording(
            MarketDataRepository(session),
            binding=binding,
            raw=raw,
            cutoff_at=cutoff_at_for_date(
                date.fromisoformat(receipt["as_of_date"]), "Asia/Shanghai"
            ),
        )
    with replay_quant_auxiliary_recording(recording, raw=raw) as diagnostics:
        yield diagnostics


def raw_quant_inputs(
    repository,
    *,
    symbol,
    snapshot=None,
    snapshots=None,
    projection_version=None,
    cutoff_at=None,
    evidence_bindings=None,
):
    """Use the same native projection as raw_data's top-level fund_managers."""
    from research_system.market_data.bundles import native_transport_facts
    from research_system.market_data.evidence_bindings import load_bound_evidence
    from research_system.market_data.native_projection import (
        PROJECTION_VERSION,
        native_payload,
        project_native_result,
    )

    rows = (
        list(snapshots)
        if snapshots is not None
        else [snapshot]
        if snapshot is not None
        else []
    )
    if (projection_version or PROJECTION_VERSION) in {"uzi-dim-result-v5", "uzi-dim-result-v6", "uzi-dim-result-v7", "uzi-dim-result-v8"}:
        from domain_packs.finance_ashare.subagents.uzi_bonus_inputs import (
            select_bonus_inputs,
        )

        materialized = []
        for row in rows:
            evidence = (load_bound_evidence(repository, row.id, evidence_bindings)
                        if evidence_bindings is not None else repository.list_snapshot_evidence(row.id))
            transport = native_transport_facts(
                row, evidence=evidence,
                cutoff_at=_utc(cutoff_at) if cutoff_at else cutoff_at_for_date(row.as_of_date, row.exchange_timezone),
                projection_version=projection_version or PROJECTION_VERSION,
            )
            is_owner = row.dimension == "6_fund_holders"
            materialized.append({"snapshot_id": row.id,
                "snapshot_type": "fund_holders" if is_owner else "",
                "facts": {**transport, "_uzi_raw_result": project_native_result(row, version=projection_version or PROJECTION_VERSION)
                          if is_owner else {}}})
        selected = select_bonus_inputs(materialized)["fund_managers"]
        return {"ticker": symbol, **({"fund_managers": selected["value"]} if selected["present"] else {})}
    # Match build_uzi_raw's ordered first-write semantics, including mixed
    # legacy fallback rows and newer explicitly transported root observations.
    for row in rows:
        evidence = (
            load_bound_evidence(repository, row.id, evidence_bindings)
            if evidence_bindings is not None
            else repository.list_snapshot_evidence(row.id)
        )
        transported = native_transport_facts(
            row,
            evidence=evidence,
            cutoff_at=_utc(cutoff_at)
            if cutoff_at
            else cutoff_at_for_date(row.as_of_date, row.exchange_timezone),
            **(
                {"projection_version": projection_version} if projection_version else {}
            ),
        )
        spill = dict(transported.get("_uzi_top_level_fields") or {}).get(
            "fund_managers"
        )
        if isinstance(spill, dict) and spill.get("present") is True:
            return {"ticker": symbol, "fund_managers": deepcopy(spill.get("value"))}
        limits = transported.get("_uzi_transport_limitations") or {}
        if (
            row.dimension == "6_fund_holders"
            and limits.get("bonus_status") != "not_fixed_historical"
        ):
            data = native_payload(
                row, **({"version": projection_version} if projection_version else {})
            )
            if "fund_managers" in data:
                return {
                    "ticker": symbol,
                    "fund_managers": deepcopy(data["fund_managers"]),
                }
    return {"ticker": symbol, "fund_managers": []}


def collect_job_quant_auxiliary_inputs(
    database,
    *,
    job_id,
    registry,
    deadline,
    cancellation,
    source=None,
    progress_callback=None,
    recovery=False,
    task_ids=None,
):
    """Run inside the caller's explicit job budget; never launch a scheduler.

    ``None`` retains an explicit whole-job direct call. Coordinator workers
    always supply the fixed task IDs authorized by their current action.
    """
    from research_system.market_data.resolver import SnapshotResolver

    with database.session() as session:
        job = session.get(MarketDataCollectionJob, job_id)
        if job is None:
            raise ValueError("collection job not found")
        if not (recovery or (job.request_json or {}).get("include_research_archive")):
            return {
                "status": "not_requested",
                "total": 0,
                "completed": 0,
                "attempts": [],
            }
        query = select(MarketDataCollectionTask).where(MarketDataCollectionTask.job_id == job_id)
        if task_ids is not None:
            try:
                if isinstance(task_ids, (str, bytes)):
                    raise TypeError("invalid task IDs")
                task_ids = frozenset(task_ids)
                if any(not isinstance(key, str) or not key for key in task_ids):
                    raise ValueError("invalid task IDs")
            except (TypeError, ValueError):
                raise QuantAuxiliaryInputError("quant_task_scope_invalid") from None
            if not task_ids:
                return {"status": "not_requested", "reason_code": "quant_task_scope_empty",
                        "total": 0, "completed": 0, "attempts": []}
            query = query.where(MarketDataCollectionTask.id.in_(task_ids))
        tasks = list(session.scalars(query))
        if task_ids is not None and {task.id for task in tasks} != task_ids:
            raise QuantAuxiliaryInputError("quant_task_scope_invalid")
        # The receipt supplies native fund-manager/quant-style evaluation,
        # not research on an arbitrary dimension. A scoped archive retry must
        # not silently acquire every holding fund and its portfolio. Filter
        # each task (not only the job) so mixed stock/day scopes stay bounded.
        quant_tasks = [task for task in tasks if task.dimension in _QUANT_INPUT_DIMENSIONS]
        if tasks and not quant_tasks:
            return {
                "status": "not_requested",
                "reason_code": "quant_dependency_not_requested",
                "total": 0,
                "completed": 0,
                "attempts": [],
            }
        scopes = sorted(
            {
                (
                    task.symbol,
                    task.market,
                    task.exchange_timezone,
                    task.start_date + timedelta(days=offset),
                )
                for task in quant_tasks
                if task.market in {"CN_EQUITY", "HK_EQUITY", "US_EQUITY"}
                for offset in range((task.end_date - task.start_date).days + 1)
            }
        )
    if not scopes:
        return {
            "status": "not_applicable",
            "reason_code": "no_requested_equity_scope",
            "total": 0,
            "completed": 0,
            "unassessed": 0,
            "attempts": [],
        }
    attempts = []
    for symbol, market, zone, as_of in scopes:
        with database.session() as session:
            repository = MarketDataRepository(session)
            coverage = SnapshotResolver(repository, registry=registry).coverage(
                symbol, market, as_of, exchange_timezone=zone
            )
            snapshots = [
                repository.require_snapshot(row.snapshot_id)
                for row in coverage.dimensions
                if row.snapshot_id
            ]
            raw = raw_quant_inputs(repository, symbol=symbol, snapshots=snapshots)
        binding = collect_quant_auxiliary_inputs(
            database,
            raw=raw,
            as_of_date=as_of,
            source=source,
            deadline=deadline,
            cancellation=cancellation,
        )
        attempts.append({"symbol": symbol, "as_of_date": as_of.isoformat(), **binding})
        if progress_callback:
            progress_callback(
                {
                    "total": len(scopes),
                    "completed": len(attempts),
                    "attempts": list(attempts),
                }
            )
    unassessed = sum(row["status"] != "available" for row in attempts)
    return {
        "status": "unassessed" if unassessed else "available",
        "total": len(scopes),
        "completed": len(attempts),
        "unassessed": unassessed,
        "attempts": attempts,
    }
