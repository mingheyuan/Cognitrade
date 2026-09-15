"""Opt-in single-stock acquisition; independent explicit freeze and read-only export.

No report execution, automatic retry, cancellation, subscription or implicit
degradation acknowledgement. Explicit all-dimension collection may invoke the
data-centre research model and incur model charges; --execute authorizes both
source acquisition and that research stage. Export remains read-only.
Every output file is create-only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research_system.market_data.contracts import canonical_hash
from scripts.run_uzi_report_e2e import DATA, api_url, server_job_timing

STAGES = (
    "native_postfetch",
    "research_archive",
    "research_enrichment",
    "company_profile_dependency_rebuild",
    "native_quant_auxiliary",
    "agent_recovery",
)
ACTIVE = {"queued", "running", "cancelling"}
TERMINAL = {"succeeded", "partial", "failed", "cancelled", "timed_out"}


def utc_now():
    return datetime.now(timezone.utc)


def collect_request(symbol="688041.SH", as_of=None, *, now=None):
    day = as_of or (now or utc_now()).astimezone(ZoneInfo("Asia/Shanghai")).date()
    return {
        "symbols": [symbol],
        "asset_family": "equity",
        "start_date": day.isoformat(),
        "end_date": day.isoformat(),
        "dimensions": "all",
        "mode": "force_all",
        "max_concurrency": 5,
        "publish_when_complete": False,
    }


def job_state(job):
    stages = {
        name: dict(
            job.get(name)
            or (job.get("progress") or {}).get(name)
            or (job.get("checkpoint") or {}).get(name)
            or {}
        )
        for name in STAGES
    }
    status = str(job.get("status") or "unknown")
    active = status in ACTIVE or any(
        row.get("status") in ACTIVE for row in stages.values()
    )
    terminal = status in TERMINAL and not active
    gaps = status == "partial" or any(
        row.get("status")
        in {
            "partial",
            "failed",
            "unassessed",
            "not_run",
            "cancelled",
            "timed_out",
        }
        for row in stages.values()
    )
    outcome = (
        "running"
        if not terminal
        else status
        if status
        in {
            "failed",
            "cancelled",
            "timed_out",
        }
        else "completed_with_input_gaps"
        if gaps
        else "collection_completed"
    )
    return {
        "collection_status": status,
        "terminal": terminal,
        "outcome": outcome,
        "stages": stages,
        "stage_timing": {name: server_job_timing(row) for name, row in stages.items()},
        "progress": job.get("progress") or {},
        "server_timing": server_job_timing(job),
        "report_status": "not_run",
    }


def check_job_scope(job, symbol, as_of):
    if job.get("symbols") and list(job["symbols"]) != [symbol]:
        raise ValueError("job symbol differs from requested single stock")
    if any(
        job.get(key) and str(job[key]) != as_of.isoformat()
        for key in ("start_date", "end_date")
    ):
        raise ValueError(
            "job date differs; pass the actual persisted --as-of, never relabel facts"
        )


class AuditWriter:
    def __init__(self, output):
        self.output = Path(output).resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.files = []

    def write(self, name, payload):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            raise ValueError("unsafe artifact name")
        body = (
            json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"
        ).encode()
        path = self.output / f"{name}.json"
        with path.open("xb") as stream:
            stream.write(body)
        item = {
            "path": path.name,
            "file_sha256": hashlib.sha256(body).hexdigest(),
            "content_hash": canonical_hash(json.loads(body)),
            "bytes": len(body),
        }
        self.files.append(item)
        return item

    def finish(self, result):
        result = {
            **result,
            "captured_at": utc_now().isoformat(),
            "files": list(self.files),
        }
        self.write("summary", result)
        return result


def run_collect(
    api,
    writer,
    request,
    *,
    execute=False,
    job_id=None,
    max_wait=3600,
    poll_seconds=5,
    sleep=time.sleep,
    clock=time.monotonic,
):
    if not execute:
        raise ValueError("collect requires --execute; no business request was made")
    writer.write(
        "request",
        {
            "request": request,
            "resume_job_id": job_id,
            "requested_at": utc_now().isoformat(),
        },
    )
    started = clock()
    if not job_id:
        created = api("POST", DATA + "/collection-jobs", request)
        writer.write("created-job", created)
        job_id = created.get("id") or created.get("job_id")
    if not job_id:
        raise ValueError(
            "collection response has no job id; inspect jobs before retrying creation"
        )
    writer.write("job-reference", {"job_id": job_id})
    count, state = 0, {"outcome": "not_observed", "terminal": False}
    try:
        while clock() - started < max_wait:
            job = api("GET", DATA + "/collection-jobs/" + job_id)
            check_job_scope(
                job, request["symbols"][0], date.fromisoformat(request["start_date"])
            )
            state = job_state(job)
            count += 1
            writer.write(
                f"poll-{count:04d}",
                {"job_id": job_id, "at": utc_now().isoformat(), **state},
            )
            print(
                json.dumps(
                    {
                        "job_id": job_id,
                        "status": state["collection_status"],
                        "stages": {
                            key: value.get("status")
                            for key, value in state["stages"].items()
                        },
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            if state["terminal"]:
                writer.write("terminal-job", job)
                break
            sleep(min(60, max(0, poll_seconds), max(0, max_wait - (clock() - started))))
        else:
            state = {
                **state,
                "outcome": "monitor_deadline_reached",
                "remote_job_cancelled": False,
            }
    except KeyboardInterrupt:
        state = {
            **state,
            "outcome": "monitor_interrupted",
            "remote_job_cancelled": False,
        }
    return writer.finish(
        {
            "stage": "collect",
            "job_id": job_id,
            **state,
            "elapsed_seconds": round(clock() - started, 3),
        }
    )


def frozen_research_evidence(manifest, item):
    """Copy six-dimension archived text from this materialization, without I/O.

    A content_ref is provenance, not permission to download or load fresh text.
    The frozen inline content may be an excerpt, not the complete source PDF.
    """
    from copy import deepcopy

    dimensions = {
        key: {"snapshot_ids": [], "evidence_ids": []}
        for key in (
            "3_macro",
            "13_policy",
            "7_industry",
            "15_events",
            "8_materials",
            "9_futures",
        )
    }
    bindings = (item.get("context") or {}).get("evidence_bindings")
    documents, selected_bindings = {}, []
    for snapshot in item["snapshots"]:
        dimension = snapshot.get("dimension_id") or snapshot.get("dimension")
        if dimension not in dimensions:
            continue
        entry = dimensions[dimension]
        snapshot_id = snapshot["snapshot_id"]
        if snapshot_id not in entry["snapshot_ids"]:
            entry["snapshot_ids"].append(snapshot_id)
        for ref in snapshot.get("source_refs") or []:
            if ref.get("source_kind") != "research_document":
                continue
            evidence_id = ref["id"]
            matches = [
                row
                for row in bindings or []
                if row.get("snapshot_id") == snapshot_id
                and row.get("evidence_id") == evidence_id
                and row.get("content_hash") == ref.get("content_hash")
            ]
            if bindings is not None and not matches:
                raise ValueError("frozen research evidence binding mismatch")
            if evidence_id in documents and documents[evidence_id] != ref:
                raise ValueError("conflicting fixed research document versions")
            documents[evidence_id] = deepcopy(ref)
            if evidence_id not in entry["evidence_ids"]:
                entry["evidence_ids"].append(evidence_id)
            for binding in matches:
                if binding not in selected_bindings:
                    selected_bindings.append(deepcopy(binding))
    return {
        "version": "uzi-fixed-research-export-v1",
        "manifest_id": manifest["manifest_id"],
        "manifest_hash": manifest["manifest_hash"],
        "bundle_id": item["bundle_id"],
        "symbol": item["symbol"],
        "as_of_date": manifest["as_of_date"],
        "cutoff_at": item["bundle"]["cutoff_at"],
        "dimensions": dimensions,
        "documents": list(documents.values()),
        "evidence_bindings": selected_bindings,
        "binding_status": "fixed"
        if bindings is not None
        else "legacy_relation_set_not_fixed",
        "missing_dimensions": [
            key for key, row in dimensions.items() if not row["evidence_ids"]
        ],
        "missing_inline_content_ids": [
            key for key, row in documents.items() if not row.get("content")
        ],
        "content_scope": "frozen_materialized_text_only_no_download",
        "full_document_reading_claimed": False,
    }


def export_frozen_manifest(manifest, writer, *, expected_bundle_id=None):
    """Export an existing materialized Manifest, never resolve current evidence."""
    from domain_packs.finance_ashare.subagents.uzi_392_native_methods import (
        build_uzi_raw,
    )
    from research_system.market_data.quant_auxiliary import (
        validate_quant_auxiliary_recording,
    )
    from research_system.market_data.report_inputs import (
        slots_for_manifest,
        validate_bundle_only_input,
    )

    before = canonical_hash(manifest)
    writer.write("manifest", manifest)
    slots = slots_for_manifest(manifest)
    validate_bundle_only_input(
        snapshots=slots["snapshots"],
        manifest=manifest,
        bundle=slots["market_data_bundle"],
    )
    stocks = []
    for item in manifest["items"]:
        if expected_bundle_id and item["bundle_id"] != expected_bundle_id:
            raise ValueError(
                "Manifest selected another Bundle; exported evidence is preserved, do not execute"
            )
        symbol = item["symbol"]
        raw = build_uzi_raw(item["snapshots"], symbol)
        recording = item.get("native_quant_auxiliary_recording")
        binding = (item.get("context") or {}).get("native_quant_auxiliary")
        if (manifest.get("workflow_pins") or {}).get("native_quant_auxiliary_version"):
            if not isinstance(recording, dict) or not isinstance(binding, dict):
                raise ValueError("fixed auxiliary recording is missing")
            validate_quant_auxiliary_recording(recording, raw=raw, binding=binding)
        prefix = symbol.replace(".", "_")
        outputs = {
            key: writer.write(f"{prefix}-{key}", value)
            for key, value in {
                "snapshots": item["snapshots"],
                "raw": raw,
                "auxiliary": {"binding": binding, "recording": recording},
                "evidence": frozen_research_evidence(manifest, item),
            }.items()
        }
        stocks.append(
            {
                "symbol": symbol,
                "bundle_id": item["bundle_id"],
                "bundle_hash": item.get("bundle_manifest_hash")
                or item["bundle"]["manifest_hash"],
                "snapshot_count": len(item["snapshots"]),
                "auxiliary_status": (binding or {}).get("status", "legacy_not_fixed"),
                "cutoff_at": item["bundle"]["cutoff_at"],
                "outputs": outputs,
            }
        )
    assert canonical_hash(manifest) == before, "export mutated fixed input"
    return {
        "manifest_id": manifest["manifest_id"],
        "manifest_hash": manifest["manifest_hash"],
        "as_of_date": manifest["as_of_date"],
        "materialized_content_hash": before,
        "workflow_pins": manifest.get("workflow_pins"),
        "stocks": stocks,
        "business_fetch_calls": 0,
        "model_calls": 0,
        "report_status": "not_run",
        "scope": "fixed_input_export_only",
    }


def load_existing_manifest(
    database, settings, *, manifest_id, manifest_hash, tenant_id, user_id
):
    """Container-side read-only alternative: use configured DB, no new Manifest.

    Pass existing current Settings explicitly. Never print or serialize Settings.
    """
    from research_system.market_data.report_inputs import DatabaseReportInputGateway

    return DatabaseReportInputGateway(
        database, runtime_settings=settings
    ).load_frozen_report_manifest(
        manifest_id=manifest_id,
        manifest_hash=manifest_hash,
        tenant_id=tenant_id,
        user_id=user_id,
    )


def run_freeze(
    api, writer, *, job_id, symbol, as_of, execute=False, acknowledge_degraded=False
):
    if not execute:
        raise ValueError("freeze requires --execute")
    job = api("GET", DATA + "/collection-jobs/" + job_id)
    writer.write("before-freeze-job", job)
    check_job_scope(job, symbol, as_of)
    if not job_state(job)["terminal"]:
        raise ValueError("collection or research stage still running")
    if job.get("freeze_status") != "frozen":
        candidate = job.get("publication_candidate") or {}
        if not job.get("can_confirm_freeze") or not candidate.get("proposal_hash"):
            raise ValueError(
                "no confirmable publication candidate; do not bypass the gate"
            )
        request = {
            "proposal_hash": candidate["proposal_hash"],
            "acknowledge_degraded": acknowledge_degraded,
        }
        writer.write("freeze-request", request)
        job = api("POST", DATA + f"/collection-jobs/{job_id}/confirm-freeze", request)
    writer.write("frozen-job", job)
    published = (
        (job.get("checkpoint") or {}).get("publication") or job.get("publication") or {}
    )
    bundles = [
        row for row in published.get("bundles") or [] if row.get("symbol") == symbol
    ]
    if len(bundles) != 1:
        raise ValueError("expected exactly one confirmed stock Bundle")
    request = {
        "symbols": [symbol],
        "market_by_symbol": {symbol: "CN_EQUITY"},
        "as_of_date": as_of.isoformat(),
        "report_kind": "single_stock",
        "workflow_pins": {"research_profile": "medium", "agent_workflow_mode": "full"},
    }
    writer.write("manifest-request", request)
    manifest = api("POST", DATA + "/report-manifests", request)
    return writer.finish(
        {
            "stage": "freeze",
            "job_id": job_id,
            **export_frozen_manifest(
                manifest, writer, expected_bundle_id=bundles[0]["bundle_id"]
            ),
        }
    )


def run_export(
    api, writer, *, symbol, as_of, job_id=None, bundle_id=None, manifest=None
):
    if manifest is not None:
        return writer.finish(
            {
                "stage": "export",
                **export_frozen_manifest(
                    manifest, writer, expected_bundle_id=bundle_id
                ),
            }
        )
    if job_id:
        job = api("GET", DATA + "/collection-jobs/" + job_id)
        writer.write("job", job)
        check_job_scope(job, symbol, as_of)
    if bundle_id:
        writer.write("bundle", api("GET", DATA + "/bundles/" + bundle_id))
    query = {"symbol": symbol, "as_of_date": as_of.isoformat()}
    coverage = api("GET", DATA + "/coverage?" + urlencode(query))
    writer.write("coverage", coverage)
    rows, offset = [], 0
    while True:
        page = api(
            "GET",
            DATA + "/snapshots?" + urlencode({**query, "limit": 200, "offset": offset}),
        )
        current = page.get("items") or []
        rows.extend(current)
        if len(current) < 200:
            break
        offset += 200
    for row in rows:
        snapshot_id = row["snapshot_id"]
        writer.write(
            "snapshot-" + snapshot_id, api("GET", DATA + "/snapshots/" + snapshot_id)
        )
    writer.write("snapshot-index", rows)
    return writer.finish(
        {
            "stage": "export",
            "scope": "live_inventory_not_frozen_input",
            "symbol": symbol,
            "as_of_date": as_of.isoformat(),
            "snapshot_count": len(rows),
            "limitation": "Use a saved materialized Manifest or load_existing_manifest for exact fixed evidence/aux replay.",
            "report_status": "not_run",
            "model_calls": 0,
            "business_fetch_calls": 0,
        }
    )


def configured_api(base_url):
    import requests
    from dotenv import dotenv_values

    token = os.getenv("COGNITRADE_API_TOKEN") or dotenv_values(ROOT / ".env").get(
        "COGNITRADE_API_TOKEN"
    )
    if not token:
        raise ValueError("COGNITRADE_API_TOKEN is not configured")
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {token}"

    def call(method, path, payload=None):
        response = session.request(
            method, api_url(base_url, path), json=payload, timeout=120
        )
        if not response.ok:
            try:
                payload = response.json()
                code = str(
                    (payload.get("error") or {}).get("code")
                    or payload.get("code")
                    or "api_error"
                )
            except (ValueError, AttributeError):
                code = "api_error"
            code = (
                code if re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", code) else "api_error"
            )
            error = RuntimeError(
                f"API {method} {path.split('?')[0]} returned HTTP {response.status_code}: {code}"
            )
            error.status_code, error.code = response.status_code, code
            raise error
        return response.json()

    return call


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage", choices=("collect", "freeze", "export"), required=True
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Authorize real source acquisition and data-centre research model calls; or the separate freeze stage",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--symbol", default="688041.SH")
    parser.add_argument("--as-of", type=date.fromisoformat)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--job-id")
    parser.add_argument("--bundle-id")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--acknowledge-degraded", action="store_true")
    parser.add_argument("--max-wait", type=float, default=3600)
    args = parser.parse_args(argv)
    request = collect_request(args.symbol, args.as_of)
    if args.stage in {"collect", "freeze"} and not args.execute:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "stage": args.stage,
                    "request": request,
                    "required": "--execute; freeze additionally requires --job-id",
                    "collection_includes_data_center_research_model": args.stage
                    == "collect",
                },
                ensure_ascii=False,
            )
        )
        return 0
    if args.stage == "freeze" and not args.job_id:
        parser.error("freeze requires --job-id")
    manifest = None
    if args.manifest:
        body = args.manifest.read_bytes()
        if (
            not args.manifest_sha256
            or hashlib.sha256(body).hexdigest() != args.manifest_sha256
        ):
            parser.error("--manifest-sha256 must match the unchanged input file")
        manifest = json.loads(body)
    api = (
        None
        if args.stage == "export" and manifest is not None
        else configured_api(args.base_url)
    )
    writer = AuditWriter(args.output)
    as_of = date.fromisoformat(request["start_date"])
    if args.stage == "collect":
        result = run_collect(
            api,
            writer,
            request,
            execute=True,
            job_id=args.job_id,
            max_wait=args.max_wait,
        )
    elif args.stage == "freeze":
        result = run_freeze(
            api,
            writer,
            job_id=args.job_id,
            symbol=args.symbol,
            as_of=as_of,
            execute=True,
            acknowledge_degraded=args.acknowledge_degraded,
        )
    else:
        result = run_export(
            api,
            writer,
            symbol=args.symbol,
            as_of=as_of,
            job_id=args.job_id,
            bundle_id=args.bundle_id,
            manifest=manifest,
        )
    print(
        json.dumps(
            {
                key: result.get(key)
                for key in ("stage", "job_id", "outcome", "scope", "manifest_id")
            },
            ensure_ascii=False,
        )
    )
    return (
        2
        if result.get("outcome")
        in {
            "failed",
            "cancelled",
            "timed_out",
            "monitor_interrupted",
            "monitor_deadline_reached",
        }
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
