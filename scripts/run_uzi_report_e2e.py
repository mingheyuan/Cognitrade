"""Opt-in, resumable live acceptance through Cognitrade's business APIs.

Never run in CI by default. No fake reports, direct database mutations, hidden
subscription changes or signal edits are performed. Output may contain research
data; credentials are never written to the result files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo

import requests
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research_system.strategies.contracts import report_conditions_template_document

DATA = "/finance/market-data"


def api_url(base_url, path):
    """Finance uses V2; run artifacts and legacy holding batches use V1."""
    base = base_url.rstrip("/")
    if base.endswith(("/api/v1", "/api/v2")):
        base = base[:-7]
    route = urlparse(path).path
    legacy_route = any(
        route == prefix or route.startswith(prefix + "/")
        for prefix in ("/runs", "/holding-report-batches")
    )
    version = "v1" if legacy_route else "v2"
    return base + "/api/" + version + path


def execution_provenance(base_url, *, root=ROOT, command=None):
    """Hash local code and inspect only non-secret deployment identity fields."""
    command = command or (
        lambda args: subprocess.check_output(
            args, cwd=root, timeout=15, stderr=subprocess.PIPE
        )
    )
    captured = {"captured_at": datetime.now(timezone.utc).isoformat()}
    try:
        captured["code_commit"] = command(["git", "rev-parse", "HEAD"]).decode().strip()
        captured["tracked_diff_hash"] = hashlib.sha256(
            command(["git", "diff", "--binary", "HEAD"])
        ).hexdigest()
        paths = (
            command(["git", "ls-files", "--others", "--exclude-standard", "-z"])
            .decode()
            .split("\0")
        )
        suffixes = {
            ".py",
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".mjs",
            ".cjs",
            ".css",
            ".html",
            ".yaml",
            ".yml",
            ".toml",
            ".sh",
        }
        untracked = []
        for relative in sorted(filter(None, paths)):
            path = root / relative
            if path.suffix not in suffixes and path.name not in {
                "Dockerfile",
                "package.json",
                "package-lock.json",
            }:
                continue
            if (
                path.is_symlink()
                or not path.is_file()
                or not path.resolve().is_relative_to(root.resolve())
            ):
                continue
            untracked.append(
                {
                    "path": relative,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
        captured["untracked_code"] = untracked
        captured["untracked_code_hash"] = hashlib.sha256(
            json.dumps(untracked, sort_keys=True).encode()
        ).hexdigest()
        captured["code_fingerprint"] = hashlib.sha256(
            json.dumps(
                {
                    key: captured[key]
                    for key in (
                        "code_commit",
                        "tracked_diff_hash",
                        "untracked_code_hash",
                    )
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
    except (OSError, subprocess.SubprocessError) as exc:
        captured["code_identity_error"] = type(exc).__name__
    deployment = {
        "scope": "local_compose_identity_not_remote_attestation",
        "containers": [],
    }
    captured["deployment"] = deployment
    if urlparse(base_url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        deployment["status"] = "not_inspected_remote_endpoint"
        return captured
    try:
        for service in ("api", "web"):
            ids = (
                command(
                    [
                        "docker",
                        "ps",
                        "-q",
                        "--filter",
                        "label=com.docker.compose.project=cognitrade",
                        "--filter",
                        "label=com.docker.compose.service=" + service,
                    ]
                )
                .decode()
                .split()
            )
            for container_id in ids:
                # Do not dump docker inspect: Config.Env may contain credentials.
                row = json.loads(
                    command(
                        [
                            "docker",
                            "inspect",
                            "--format",
                            '{"container_id":{{json .Id}},"image_id":{{json .Image}},"image_name":{{json .Config.Image}},"started_at":{{json .State.StartedAt}}}',
                            container_id,
                        ]
                    )
                )
                image = json.loads(
                    command(
                        [
                            "docker",
                            "image",
                            "inspect",
                            "--format",
                            '{"image_id":{{json .Id}},"repo_digests":{{json .RepoDigests}},"created_at":{{json .Created}}}',
                            row["image_id"],
                        ]
                    )
                )
                deployment["containers"].append(
                    {"service": service, **row, "image": image}
                )
        deployment["status"] = (
            "captured" if deployment["containers"] else "no_local_compose_container"
        )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        deployment.update(status="unavailable", reason=type(exc).__name__)
    return captured


def server_job_timing(item):
    def timestamp(value):
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return (
                parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
            )
        except (TypeError, ValueError):
            return None

    timing = {
        key: item.get(key)
        for key in ("created_at", "started_at", "completed_at")
        if item.get(key)
    }
    end = timestamp(item.get("completed_at"))
    for key, output in (
        ("created_at", "server_elapsed_seconds"),
        ("started_at", "server_execution_seconds"),
    ):
        start = timestamp(item.get(key))
        if start and end:
            timing[output] = max(0, round((end - start).total_seconds(), 3))
    return timing


class ApiFailure(RuntimeError):
    def __init__(self, method, path, status_code, payload):
        self.status_code = status_code
        self.payload = payload
        error = payload.get("error") or payload if isinstance(payload, dict) else {}
        self.code = str(error.get("code") or "http_error")
        super().__init__(
            f"{method} {path}: HTTP {status_code}: {json.dumps(payload, ensure_ascii=False)[:1800]}"
        )


def bar_date_evidence(snapshot):
    """The data-centre partition day is not necessarily the last trading day."""
    payload = snapshot.get("payload") or {}
    raw = (
        payload.get("raw") or ((payload.get("_uzi_raw_result") or {}).get("data")) or {}
    )
    rows = (
        payload.get("ohlcv")
        or raw.get("ohlcv")
        or raw.get("klines")
        or raw.get("history")
        or []
    )
    dates = set()
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not any(
            row.get(key) is not None for key in ("close", "收盘", "收盘价")
        ):
            continue
        value = next(
            (
                row[key]
                for key in ("date", "日期", "trade_date", "datetime", "point_at")
                if row.get(key)
            ),
            None,
        )
        try:
            dates.add(date.fromisoformat(str(value).replace("/", "-")[:10]).isoformat())
        except ValueError:
            continue
    return {
        "snapshot_id": snapshot.get("snapshot_id") or snapshot.get("id"),
        "snapshot_as_of_date": snapshot.get("as_of_date"),
        "bar_date_count": len(dates),
        "first_bar_date": min(dates) if dates else None,
        "last_bar_date": max(dates) if dates else None,
        "content_hash": snapshot.get("content_hash"),
    }


def _report_review_layers(source):
    """Whitelist compact diagnostics; never copy raw inputs or exception bodies."""
    keys = (
        "input_dependencies", "execution_status", "native_python_review",
        "native_skill_completion", "frozen_input_safety",
    )
    reviews = [
        row["self_review"] for row in source.get("results") or []
        if isinstance(row, dict) and isinstance(row.get("self_review"), dict)
    ]
    if not reviews and isinstance(source.get("self_review"), dict):
        reviews = [source["self_review"]]
    if not reviews and any(key in source for key in keys):
        reviews = [source]
    layers = {}
    for key in keys:
        rows = [review[key] for review in reviews if isinstance(review.get(key), dict)]
        statuses, counts, reasons, kinds = set(), [], set(), set()
        for row in rows:
            status = row.get("status")
            if not status and isinstance(row.get("passed"), bool):
                status = "passed" if row["passed"] else "failed"
            if status:
                statuses.add(str(status))
            count = row.get("critical_count")
            if isinstance(count, int) and not isinstance(count, bool):
                counts.append(count)
            reasons.update(str(value) for value in row.get("reason_codes") or [])
            kinds.update(
                str(record["kind"]) for record in row.get("failure_records") or []
                if isinstance(record, dict) and record.get("kind")
            )
        layers[key] = {
            "statuses": sorted(statuses) or ["not_recorded"],
            "critical_count": sum(counts) if counts else None,
            "reason_codes": sorted(reasons),
            "failure_kinds": sorted(kinds),
        }
    return layers, any(any(key in review for key in keys) for review in reviews)


def summarize_report_acceptance(reports):
    """Keep delivery classification separate from overlapping failure layers.

    Older artifacts have only a combined gate. Preserve their legacy outcome,
    but explicitly mark its attribution unknown instead of inventing a native
    Python review. Layer counts count holdings, not issues, and can overlap.
    """
    counts = {
        "delivered": 0,
        "input_blocked": 0,
        "native_blocked": 0,
        "skill_incomplete": 0,
        "system_failed": 0,
        "cancelled": 0,
        "unfinished": 0,
    }
    layer_counts = dict.fromkeys((
        "input_unassessed", "input_blocked", "execution_failed",
        "native_python_critical", "native_python_not_run", "skill_incomplete",
        "skill_not_started", "legacy_unattributed",
    ), 0)
    holdings = []
    for report in reports:
        for node in report.get("nodes") or []:
            if node.get("node_type") != "holding_uzi":
                continue
            source = node.get("source") or {}
            recovery = source.get("recovery") or {}
            layers, layered = _report_review_layers(source)
            inputs = set(layers["input_dependencies"]["statuses"])
            execution = set(layers["execution_status"]["statuses"])
            native = layers["native_python_review"]
            skill = layers["native_skill_completion"]
            input_unassessed = bool(inputs & {"unassessed", "not_run", "not_started"})
            input_blocked = input_unassessed or bool(inputs & {"blocked", "failed", "incomplete"}) or bool(
                set(layers["frozen_input_safety"]["statuses"]) & {"blocked", "failed", "invalid"}
            ) or source.get("report_stage") == "input_preflight" or "input_preflight_blocked" in (
                node.get("data_gaps") or []
            )
            computation_failed = source.get("computation_error") or any(
                row.get("computation_error")
                for row in source.get("results") or []
                if isinstance(row, dict)
            )
            execution_failed = bool(computation_failed) or bool(execution & {"failed", "timed_out"}) or recovery.get("kind") in {
                "deadline_exceeded",
                "provider_unavailable",
                "worker_interrupted",
                "pipeline_failure",
            }
            native_critical = (native["critical_count"] or 0) > 0
            native_not_run = bool(set(native["statuses"]) & {"not_run", "not_started"})
            skill_incomplete = bool(set(skill["statuses"]) & {"incomplete", "failed", "blocked"}) or (
                skill["critical_count"] or 0
            ) > 0
            skill_not_started = "not_started" in skill["statuses"]
            for key, active in {
                "input_unassessed": input_unassessed, "input_blocked": input_blocked,
                "execution_failed": execution_failed, "native_python_critical": native_critical,
                "native_python_not_run": native_not_run, "skill_incomplete": skill_incomplete,
                "skill_not_started": skill_not_started, "legacy_unattributed": not layered,
            }.items():
                layer_counts[key] += int(active)
            if node.get("status") == "cancelled":
                classification = "cancelled"
            elif execution_failed:
                classification = "system_failed"
            elif input_blocked:
                classification = "input_blocked"
            elif native_critical:
                classification = "native_blocked"
            elif skill_incomplete:
                classification = "skill_incomplete"
            elif native_not_run or skill_not_started:
                classification = "unfinished"
            elif node.get("status") == "completed" and source.get("report_allowed") is True:
                classification = "delivered"
            elif not layered and (
                source.get("report_allowed") is False
                or int(source.get("critical_count") or 0) > 0
            ):
                classification = "native_blocked"
            elif node.get("status") in {"failed", "blocked", "partial", "completed"}:
                classification = "system_failed"
            else:
                classification = "unfinished"
            counts[classification] += 1
            holdings.append({
                "report_set_id": report.get("id"), "node_id": node.get("id"),
                "subject_code": node.get("subject_code"), "run_id": node.get("run_id"),
                "node_status": node.get("status"), "classification": classification,
                "review_scope": "layered" if layered else "legacy_unattributed",
                "layers": layers,
            })
    if counts["delivered"] == 10 and sum(counts.values()) == 10:
        status = "allowed"
    elif counts["delivered"]:
        status = "partial_delivery"
    elif counts["system_failed"]:
        status = "generation_failed"
    elif counts["native_blocked"]:
        status = "blocked"
    elif counts["input_blocked"]:
        status = "input_blocked"
    elif counts["skill_incomplete"]:
        status = "skill_incomplete"
    else:
        status = "cancelled" if counts["cancelled"] else "generation_failed"
    return {
        "schema_version": "uzi-e2e-report-acceptance-v2", "status": status,
        "counts": counts, "layer_counts": layer_counts, "holdings": holdings,
        "limitations": ["legacy_unattributed_does_not_prove_native_python_review"]
        if layer_counts["legacy_unattributed"] else [],
    }


def classify_reports(reports):
    """Backward-compatible pair; detailed attribution lives in the summary."""
    summary = summarize_report_acceptance(reports)
    return summary["status"], summary["counts"]


class LiveAcceptance:
    def __init__(self, args):
        self.args = args
        self.output = args.output.resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.state_path = self.output / "e2e-result.json"
        self.state = (
            json.loads(self.state_path.read_text())
            if self.state_path.exists()
            else {
                "schema_version": "uzi-report-replay-e2e-v1",
                "evidence_mode": "live",
                "fund_code": "020464",
                "report_date": "2026-09-02",
                "end_date": "2026-09-04",
                "profile": "medium",
                "created_at": datetime.now().astimezone().isoformat(),
                "pipeline": "running",
                "native_report": "not_started",
                "replay": "not_started",
                "stages": {},
            }
        )
        token = os.getenv("COGNITRADE_API_TOKEN") or dotenv_values(ROOT / ".env").get(
            "COGNITRADE_API_TOKEN"
        )
        if not token:
            raise RuntimeError("COGNITRADE_API_TOKEN is not configured")
        self.http = requests.Session()
        self.http.headers["Authorization"] = f"Bearer {token}"
        self.save()

    def save(self):
        self.state["updated_at"] = datetime.now().astimezone().isoformat()
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2, default=str)
        )
        temporary.replace(self.state_path)

    def api(self, method, path, payload=None):
        response = self.http.request(
            method, api_url(self.args.base_url, path), json=payload, timeout=120
        )
        if not response.ok:
            try:
                error = response.json()
            except ValueError:
                error = {"message": response.text[:1800]}
            raise ApiFailure(method, path, response.status_code, error)
        return response.json()

    def export(self, name, payload):
        (self.output / f"{name}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        )

    def execute_stage(self, stage):
        """A resumed invocation gets a fresh outcome without erasing its audit."""
        history = self.state.setdefault("error_history", [])
        old_error = self.state.pop("last_error", None)
        if old_error and old_error not in history:
            history.append(old_error)
        self.state["pipeline"] = "running"
        self.state.pop("blocking_stage", None)
        attempts = self.state.setdefault("stage_attempts", {}).setdefault(stage, [])
        attempt = {
            "attempt": len(attempts) + 1,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "status": "running",
        }
        attempts.append(attempt)
        self.save()
        started = time.monotonic()
        try:
            getattr(self, stage)()
            attempt["status"] = (
                "completed"
                if self.state["pipeline"] == "running"
                else self.state["pipeline"]
            )
        except Exception as exc:
            error = {
                "stage": stage,
                "attempt": attempt["attempt"],
                "type": type(exc).__name__,
                "message": str(exc),
                "occurred_at": datetime.now(timezone.utc).isoformat(),
            }
            self.state["last_error"] = error
            history.append(error)
            if self.state["pipeline"] != "blocked":
                self.state["pipeline"] = "failed"
            attempt["status"] = self.state["pipeline"]
            raise
        finally:
            attempt.update(
                completed_at=datetime.now(timezone.utc).isoformat(),
                elapsed_seconds=round(time.monotonic() - started, 3),
            )
            self.save()

    def wait(self, path, *, stage, collection=False):
        started = time.monotonic()
        last = None
        finalizing_since = None
        while True:
            item = self.api("GET", path)
            status = item.get("status", "unknown")
            recovery = (
                item.get("agent_recovery")
                or (item.get("checkpoint") or {}).get("agent_recovery")
                or {}
            )
            archive = (
                item.get("research_archive")
                or (item.get("checkpoint") or {}).get("research_archive")
                or {}
            )
            progress = item.get("portfolio_summary") or item.get("progress") or {}
            publication = item.get("publication") or (item.get("checkpoint") or {}).get("publication") or {}
            finalizing = bool(collection and status in {"succeeded", "partial", "failed"}
                              and publication.get("error_code") in {
                                  "collection_retry_pending", "publication_candidate_recomputing",
                              } and not item.get("publication_candidate"))
            if finalizing:
                if finalizing_since is None:
                    finalizing_since = time.monotonic()
                if time.monotonic() - finalizing_since > 120:
                    raise RuntimeError("collection finalization did not settle; no freeze conclusion can be drawn")
            else:
                finalizing_since = None
            compact = json.dumps(
                {
                    "status": status,
                    "recovery": recovery.get("status"),
                    "archive": archive.get("status"),
                    "progress": progress,
                },
                sort_keys=True,
                default=str,
            )
            if compact != last:
                print(f"[{stage}] {compact[:1400]}", flush=True)
                last = compact
            self.state["stages"].setdefault(stage, {}).update(
                {
                    "status": status,
                    "last_poll_at": datetime.now().astimezone().isoformat(),
                }
            )
            self.state["stages"][stage]["server_timing"] = server_job_timing(item)
            self.save()
            self.export(stage, item)
            if not finalizing and status not in {"queued", "running", "cancelling"} and (
                not collection
                or all(
                    stage_state.get("status") not in {"queued", "running", "cancelling"}
                    for stage_state in (recovery, archive)
                )
            ):
                elapsed = round(time.monotonic() - started, 3)
                timing = self.state["stages"][stage]
                sessions = timing.setdefault("wait_sessions", [])
                if not sessions and timing.get("wait_seconds") is not None:
                    sessions.append(
                        {
                            "seconds": timing["wait_seconds"],
                            "migrated_from_previous_invocation": True,
                        }
                    )
                sessions.append(
                    {
                        "seconds": elapsed,
                        "completed_at": datetime.now(timezone.utc).isoformat(),
                        "status": status,
                    }
                )
                timing["wait_seconds"] = elapsed
                timing["wait_seconds_total"] = round(
                    sum(row["seconds"] for row in sessions), 3
                )
                self.save()
                return item
            time.sleep(5)

    def collection(self, stage, payload):
        stored = self.state["stages"].setdefault(stage, {})
        if not stored.get("id"):
            started = time.monotonic()
            result = self.api("POST", DATA + "/collection-jobs", payload)
            stored.update(
                {
                    "id": result.get("id") or result.get("job_id"),
                    "enqueue_seconds": round(time.monotonic() - started, 3),
                    "request": payload,
                }
            )
            self.save()
        return self.wait(
            DATA + "/collection-jobs/" + stored["id"], stage=stage, collection=True
        )

    def probe(self):
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        result = self.collection(
            "today_probe",
            {
                "symbols": ["688012.SH"],
                "start_date": today,
                "end_date": today,
                "asset_family": "equity",
                "dimensions": ["2_kline", "15_events"],
                "mode": "force_all",
                "max_concurrency": 5,
                "publish_when_complete": False,
            },
        )
        self.state["today_probe_status"] = result["status"]
        today = (
            result.get("start_date")
            or self.state["stages"]["today_probe"]["request"]["start_date"]
        )
        snapshots = self.api(
            "GET", DATA + f"/snapshots?symbol=688012.SH&as_of_date={today}&limit=200"
        )
        self.export("today_probe_snapshots", snapshots)
        details = [
            self.api("GET", DATA + "/snapshots/" + row["snapshot_id"])
            for row in snapshots.get("items") or []
            if row.get("dimension_id") in {"2_kline", "15_events"}
        ]
        self.export("today_probe_snapshot_details", details)
        self.state["today_probe_bar_evidence"] = [
            bar_date_evidence(row)
            for row in details
            if row.get("dimension_id") == "2_kline"
        ]
        if any(
            row.get("last_bar_date") and row["last_bar_date"] > today
            for row in self.state["today_probe_bar_evidence"]
        ):
            raise RuntimeError("probe contains bars after its requested day")
        self.save()

    def collect(self):
        result = self.collection(
            "historical_collection",
            {
                "symbols": ["020464"],
                "asset_family": "fund",
                "start_date": "2026-09-02",
                "end_date": "2026-09-02",
                "dimensions": "all",
                "mode": "missing_or_stale",
                "max_concurrency": 5,
                "publish_when_complete": False,
            },
        )
        stored = self.state["stages"]["historical_collection"]
        path = DATA + "/collection-jobs/" + stored["id"]
        summary = result.get("portfolio_summary") or {}
        if summary.get("retryable_slots", 0) and not any(
            stored.get(key)
            for key in ("targeted_retry_requested", "research_evidence_requested")
        ):
            stored["targeted_retry_requested"] = True
            self.save()
            try:
                self.api("POST", path + "/retry-failed", {})
            except ApiFailure as exc:
                if exc.status_code != 409 or exc.code not in {
                    "collection_retry_unavailable",
                    "collection_retry_bundle_frozen",
                    "collection_retry_recovery_running",
                }:
                    raise
                stored["targeted_retry_conflict"] = {
                    "code": exc.code,
                    "detail": exc.payload,
                }
            result = self.wait(path, stage="historical_retry", collection=True)
        # Original acquisition and its sole targeted retry own research
        # archiving. Never append a third full Agent-recovery wave. Preserve
        # legacy attempt flags as audit and count them against this limit.
        stored["acquisition_policy"] = {
            "additional_waves_limit": 1,
            "research_archive": "collection_stage",
            "extra_agent_recovery": False,
        }
        # Parent summary omits the detailed archive progress of its stock jobs.
        # Capture every durable child rather than mistaking terminal structured
        # slots for successful research acquisition.
        children = [
            self.api("GET", DATA + "/collection-jobs/" + child_id)
            for child_id in result.get("child_collection_job_ids") or []
        ]
        self.export("historical_collection_children", children)
        self.export("historical_collection_final", result)
        self.state["collection_summary"] = result.get("portfolio_summary")
        self.save()

    def freeze(self):
        job_id = self.state["stages"]["historical_collection"]["id"]
        result = self.api("GET", DATA + "/collection-jobs/" + job_id)
        if result.get("freeze_status") != "frozen":
            candidate = result.get("publication_candidate") or {}
            if not result.get("can_confirm_freeze") or not candidate.get(
                "proposal_hash"
            ):
                self.state["pipeline"] = "blocked"
                self.state["blocking_stage"] = "data_freeze"
                self.save()
                raise RuntimeError(
                    "data center has no confirmable frozen candidate; see historical_collection_final.json"
                )
            result = self.api(
                "POST",
                DATA + f"/collection-jobs/{job_id}/confirm-freeze",
                {
                    "proposal_hash": candidate["proposal_hash"],
                    "acknowledge_degraded": True,
                },
            )
        self.export("frozen_collection", result)
        # Bind the candidate actually confirmed, never a concurrent publisher's
        # later "latest" Bundle.
        checkpoint = result.get("checkpoint") or {}
        publication = checkpoint.get("publication") or result.get("publication") or {}
        bundles = publication.get("bundles") or []
        parent = next(
            (item for item in bundles if item.get("asset_role") == "fund"), {}
        )
        self.state["fund_bundle_id"] = parent.get("bundle_id") or checkpoint.get(
            "parent_bundle_id"
        )
        self.state["frozen_bundles"] = bundles
        if not self.state["fund_bundle_id"]:
            raise RuntimeError("frozen fund Bundle not found")
        for bundle in bundles:
            detail = self.api("GET", DATA + "/bundles/" + bundle["bundle_id"])
            self.export("bundle_" + bundle["bundle_id"], detail)
            if detail.get("integrity_verified") is not True or detail.get(
                "manifest_hash"
            ) != bundle.get("manifest_hash"):
                self.state.update(
                    pipeline="blocked", blocking_stage="frozen_bundle_integrity"
                )
                self.save()
                raise RuntimeError(
                    "confirmed Bundle failed exact hash/integrity verification"
                )
        if len([row for row in bundles if row.get("asset_role") == "holding"]) != 10:
            raise RuntimeError(
                "confirmed fund publication does not contain exactly ten holding Bundles"
            )
        self.save()

    def report(self):
        if not self.state.get("strategy_id"):
            document = report_conditions_template_document().model_dump(mode="json")
            document["report_inputs"]["research_profile"] = "medium"
            document["schedule_defaults"]["enabled"] = False
            strategy = self.api(
                "POST",
                "/finance/strategies",
                {
                    "name": "E2E 020464 原生报告回放 " + self.output.name,
                    "document": document,
                },
            )
            self.state["strategy_id"] = strategy["id"]
            self.save()
        if not self.state.get("strategy_version_id"):
            version = self.api(
                "POST", f"/finance/strategies/{self.state['strategy_id']}/publish", {}
            )
            self.state["strategy_version_id"] = version["id"]
            self.export("strategy_version", version)
            self.save()
        if not self.state.get("assignment_id"):
            assignment = self.api(
                "POST",
                "/finance/strategy-assignments",
                {
                    "strategy_id": self.state["strategy_id"],
                    "fund_code": "020464",
                    "mode": "holdings_portfolio",
                    "enabled": False,
                    "schedule": {"enabled": False},
                },
            )
            self.state["assignment_id"] = assignment["id"]
            self.save()
        stage = self.state["stages"].setdefault("report", {})
        if not stage.get("id"):
            result = self.api(
                "POST",
                "/finance/report-jobs",
                {
                    "assignment_id": self.state["assignment_id"],
                    "strategy_version_id": self.state["strategy_version_id"],
                    "as_of_date": "2026-09-02",
                    "fund_bundle_id": self.state["fund_bundle_id"],
                },
            )
            stage["id"] = result["id"]
            self.save()
        result = self.wait("/finance/report-jobs/" + stage["id"], stage="report")
        for day, batch_id in (
            (result.get("progress") or {}).get("batches") or {}
        ).items():
            if batch_id:
                self.export(
                    "holding_batch_" + day,
                    self.api("GET", "/holding-report-batches/" + batch_id),
                )
        report_ids = (result.get("progress") or {}).get("report_set_ids") or []
        self.state["report_set_ids"] = report_ids
        sets = [
            self.api("GET", "/finance/daily-report-sets/" + item) for item in report_ids
        ]
        self.export("daily_reports", sets)
        acceptance = summarize_report_acceptance(sets)
        self.state["report_acceptance"] = acceptance
        self.state["native_report"] = acceptance["status"]
        self.state["native_report_counts"] = acceptance["counts"]
        self.export("report_acceptance", acceptance)
        self.save()
        self.render_reports(sets)

    def render_reports(self, reports):
        """Render only genuinely delivered artifacts; the HTML GET runs no LLM."""
        records = []
        seen = set()
        for report in reports:
            for node in report.get("nodes") or []:
                source = node.get("source") or {}
                if (
                    node.get("node_type") != "holding_uzi"
                    or node.get("status") != "completed"
                    or source.get("report_allowed") is not True
                ):
                    continue
                run_id = str(node.get("run_id") or source.get("canonical_run_id") or "")
                if run_id in seen:
                    continue
                seen.add(run_id)
                record = {
                    "run_id": run_id,
                    "status": "failed",
                    "http_status": None,
                    "server_timing": None,
                }
                records.append(record)
                if not run_id:
                    record["reason"] = "delivered_report_missing_run_id"
                    continue
                path = "/runs/" + quote(run_id, safe="") + "/report?format=html"
                started = time.monotonic()
                try:
                    response = self.http.request(
                        "GET", api_url(self.args.base_url, path), timeout=120
                    )
                    content = response.content
                    record.update(
                        http_status=response.status_code,
                        content_type=response.headers.get("Content-Type"),
                        server_timing=response.headers.get("Server-Timing"),
                        content_hash=hashlib.sha256(content).hexdigest(),
                        bytes=len(content),
                        elapsed_seconds=round(time.monotonic() - started, 3),
                    )
                    if (
                        response.ok
                        and "text/html"
                        in (response.headers.get("Content-Type") or "").lower()
                        and content.strip()
                    ):
                        filename = (
                            "report_"
                            + hashlib.sha256(run_id.encode()).hexdigest()[:16]
                            + ".html"
                        )
                        (self.output / filename).write_bytes(content)
                        record.update(status="completed", file=filename)
                    else:
                        record["reason"] = "report_html_response_unavailable"
                except requests.RequestException as exc:
                    record.update(
                        reason=type(exc).__name__,
                        elapsed_seconds=round(time.monotonic() - started, 3),
                    )
        self.state["render"] = (
            "not_run"
            if not records
            else "completed"
            if all(row["status"] == "completed" for row in records)
            else "failed"
        )
        self.state["render_reports"] = records
        if not records:
            self.state["render_not_run_reason"] = "no_deliverable_native_report"
        else:
            self.state.pop("render_not_run_reason", None)
        self.export(
            "report_html_evidence", {"status": self.state["render"], "reports": records}
        )
        self.save()

    def market(self):
        symbols = [
            row["symbol"]
            for row in self.state.get("frozen_bundles") or []
            if row.get("asset_role") == "holding"
        ]
        if len(symbols) != 10:
            raise RuntimeError(f"expected ten frozen holdings, found {len(symbols)}")
        result = self.collection(
            "outcome_market_collection",
            {
                "symbols": symbols,
                "asset_family": "equity",
                "start_date": "2026-09-04",
                "end_date": "2026-09-04",
                "dimensions": ["2_kline"],
                "mode": "missing_or_stale",
                "max_concurrency": 5,
                "publish_when_complete": False,
            },
        )
        # Export the task's exact outputs, not whichever snapshot is latest
        # when a later preflight runs. A blocked report still needs an honest
        # audit of independently acquired outcome prices.
        snapshot_ids = list(
            dict.fromkeys(
                row["snapshot_id"]
                for row in result.get("tasks") or []
                if row.get("dimension_id") == "2_kline" and row.get("snapshot_id")
            )
        )
        details = [
            self.api("GET", DATA + "/snapshots/" + item) for item in snapshot_ids
        ]
        self.export("outcome_market_snapshot_details", details)
        self.state["outcome_market_evidence"] = [
            bar_date_evidence(row) for row in details
        ]
        self.save()

    def backtest(self):
        request = {
            "strategy_version_id": self.state["strategy_version_id"],
            "fund_code": "020464",
            "mode": "holdings_portfolio",
            "start_date": "2026-09-02",
            "end_date": "2026-09-04",
        }
        preflight = self.api("POST", "/finance/strategy-backtests/preflight", request)
        self.export("backtest_preflight", preflight)
        # This driver does not bind the market collection's exact snapshot IDs.
        # The service reads persisted prices and freezes its actual selection.
        replay_acceptance = {
            "schema_version": "uzi-e2e-replay-acceptance-v2",
            "backtest_run_created": bool((self.state["stages"].get("backtest") or {}).get("id")),
            "backtest_engine_executed": None,
            "completed_ledger": False,
            "market_input_selection": "existing_data_center_at_execution",
            "exact_collection_snapshot_ids_enforced": False,
        }
        self.state["replay_acceptance"] = replay_acceptance
        self.state.update(replay="running", trade_count=None, trading_execution_demonstrated=False)
        if preflight.get("can_run") is not True:
            self.state["replay"] = "not_executable"
            self.state["trade_count"] = None  # Not a completed zero-trade ledger.
            self.state["trading_execution_demonstrated"] = False
            if not replay_acceptance["backtest_run_created"]:
                replay_acceptance["backtest_engine_executed"] = False
            replay_acceptance["reason"] = "backtest_preflight_not_executable"
            self.state["blocking_stage"] = (
                "report_input_preflight"
                if self.state["native_report"] == "input_blocked"
                else "backtest_preflight"
            )
            self.state["pipeline"] = (
                "failed"
                if self.state["native_report"] == "generation_failed"
                else "blocked"
            )
            self.save()
            return
        stage = self.state["stages"].setdefault("backtest", {})
        if not stage.get("id"):
            result = self.api(
                "POST",
                "/finance/strategy-backtests",
                {
                    **request,
                    "initial_cash": 1_000_000,
                    "report_policy": "existing_only",
                    "benchmark": {
                        "hs300": False,
                        "initial_disclosed_weights_static": True,
                    },
                },
            )
            stage["id"] = result["id"]
            replay_acceptance.update(backtest_run_created=True, backtest_run_id=stage["id"])
            self.save()
        replay_acceptance.update(backtest_run_created=True, backtest_run_id=stage["id"])
        result = self.wait(
            "/finance/strategy-backtests/" + stage["id"], stage="backtest"
        )
        payload = result.get("result") or {}
        trades = payload.get("trades")
        ledger_completed = result["status"] == "completed" and isinstance(trades, list)
        replay_acceptance.update(
            backtest_run_created=True,
            backtest_run_id=stage["id"],
            backtest_run_status=result["status"],
            completed_ledger=ledger_completed,
            backtest_engine_executed=True if ledger_completed or (isinstance(trades, list) and trades) else None,
            observed_partial_trade_count=(len(trades) if isinstance(trades, list) else None),
            market_data_manifest_hash=(payload.get("market_data_manifest") or {}).get("content_hash"),
        )
        self.state["trade_count"] = len(trades) if ledger_completed else None
        self.state["trading_execution_demonstrated"] = ledger_completed and bool(trades)
        self.state["replay"] = (
            ("completed_with_trades" if trades else "completed_without_trades")
            if ledger_completed
            else "cancelled" if result["status"] == "cancelled"
            else "failed"
        )
        self.state["pipeline"] = (
            "completed"
            if self.state["native_report"] == "allowed"
            and ledger_completed
            else "cancelled" if result["status"] == "cancelled"
            else "blocked"
            if self.state["native_report"]
            in {"blocked", "input_blocked", "partial_delivery", "skill_incomplete"}
            else "failed"
        )
        if result["status"] == "completed" and not ledger_completed:
            replay_acceptance["reason"] = "completed_backtest_missing_trade_ledger"
            self.state.update(pipeline="failed", blocking_stage="backtest_result_contract")
        if self.state.get("render") == "failed":
            self.state.update(pipeline="failed", blocking_stage="report_html_render")
        self.save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Explicitly allow real acquisition and model calls",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--stage",
        choices=["all", "probe", "collect", "freeze", "report", "market", "backtest"],
        default="all",
    )
    args = parser.parse_args()
    if not args.execute:
        parser.error(
            "live execution requires --execute; this is not an offline fixture"
        )
    run = LiveAcceptance(args)
    provenance = execution_provenance(args.base_url)
    run.state.setdefault("execution_provenance", []).append(provenance)
    for key in (
        "code_commit",
        "tracked_diff_hash",
        "untracked_code_hash",
        "code_fingerprint",
    ):
        if key in provenance:
            run.state[key] = provenance[key]
    run.state.setdefault("native_commit", "b3665e85b8666477038e11157d8e8feb0e006b76")
    run.save()
    for stage in (
        ["probe", "collect", "freeze", "report", "market", "backtest"]
        if args.stage == "all"
        else [args.stage]
    ):
        print(f"== {stage} ==", flush=True)
        run.execute_stage(stage)


if __name__ == "__main__":
    main()
