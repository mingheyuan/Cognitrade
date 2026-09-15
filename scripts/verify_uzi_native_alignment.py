#!/usr/bin/env python3
"""Offline, two-process UZI b366 alignment replay (never a live Agent E2E).

Example (a differing baseline exits 1, but still writes its complete audit):
  .venv/bin/python scripts/verify_uzi_native_alignment.py \
    --reference /tmp/pristine-uzi --experiment workspace/reports/native-example \
    --output-directory /tmp/new-empty-audit --profiles lite medium deep

The reference imports only the pristine checkout. The host imports its real
adapter, or an explicitly identified pre-fix source snapshot for a baseline.
Auxiliary provider *responses*, not scoring functions, are recorded/replayed.
No model, HTTP, database, report renderer or production cache is invoked.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from copy import deepcopy
import dataclasses
import datetime as datetime_module
import hashlib
import importlib
import importlib.metadata
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import time
from typing import Any

PIN = "b3665e85b8666477038e11157d8e8feb0e006b76"
ROOT = Path(__file__).resolve().parents[1]
HOST_MODULE = "domain_packs.finance_ashare.subagents.uzi_392_analysis"


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def json_value(value: Any) -> Any:
    """Lossless tags for non-JSON native numbers; never turn NaN into zero/null."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else {"$native_float": str(value)}
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("Non-string business mapping key")
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_value(item) for item in value]
    if isinstance(value, tuple):
        return {"$native_tuple": [json_value(item) for item in value]}
    if dataclasses.is_dataclass(value):
        return json_value(dataclasses.asdict(value))
    if isinstance(value, (datetime_module.datetime, datetime_module.date)):
        return {"$native_datetime": value.isoformat()}
    if hasattr(value, "item") and type(value).__module__.startswith("numpy"):
        return json_value(value.item())
    raise TypeError(f"Unsupported native output type: {type(value).__name__}")


def canonical(value: Any) -> bytes:
    return json.dumps(json_value(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode()


def write_new(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(json_value(value), handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def field_diff(reference: Any, host: Any, pointer: str = "") -> list[dict]:
    """Exact types/order/values, with no business-field ignore list or tolerance."""
    def row(kind, left, right):
        return [{"path": pointer or "/", "kind": kind,
                 "reference": left, "host": right}]
    if type(reference) is not type(host):
        return row("type_changed", reference, host)
    if isinstance(reference, dict):
        changes = []
        for key in sorted(reference.keys() | host.keys()):
            path = pointer + "/" + key.replace("~", "~0").replace("/", "~1")
            if key not in reference or key not in host:
                changes.append({"path": path, "kind": "added" if key not in reference else "removed",
                                "reference_present": key in reference, "host_present": key in host,
                                "reference": reference.get(key), "host": host.get(key)})
            else:
                changes.extend(field_diff(reference[key], host[key], path))
        return changes
    if isinstance(reference, list):
        changes = []
        for index in range(max(len(reference), len(host))):
            path = f"{pointer}/{index}"
            if index >= len(reference) or index >= len(host):
                changes.append({"path": path, "kind": "added" if index >= len(reference) else "removed",
                                "reference_present": index < len(reference), "host_present": index < len(host),
                                "reference": reference[index] if index < len(reference) else None,
                                "host": host[index] if index < len(host) else None})
            else:
                changes.extend(field_diff(reference[index], host[index], path))
        return changes
    return [] if reference == host else row("value_changed", reference, host)


def _git_revision(path: Path) -> str:
    return subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()


def verify_pristine(reference: Path) -> dict:
    if _git_revision(reference) != PIN:
        raise ValueError(f"Reference must be pinned {PIN}")
    scripts = "skills/deep-analysis/scripts"
    tracked = subprocess.check_output(["git", "-C", str(reference), "ls-files", "-z", scripts]).split(b"\0")
    hashes = {}
    for item in tracked:
        if not item:
            continue
        rel = item.decode()
        expected = subprocess.check_output(["git", "-C", str(reference), "show", f"{PIN}:{rel}"])
        actual = (reference / rel).read_bytes()
        if actual != expected:
            raise ValueError(f"Reference source is modified: {rel}")
        hashes[rel] = digest(actual)
    untracked = subprocess.check_output(["git", "-C", str(reference), "ls-files", "--others", "--exclude-standard", "-z", scripts])
    if any(item.endswith(b".py") for item in untracked.split(b"\0")):
        raise ValueError("Untracked Python in reference import tree")
    return {"revision": PIN, "tracked_sources_sha256": digest(canonical(hashes)), "files": hashes}


def _capture(path: Path, inputs: dict[str, str]) -> Any:
    content = path.read_bytes()
    inputs[str(path.resolve())] = digest(content)
    return json.loads(content)


def capture_auxiliary(reference: Path, raw: dict, inputs: dict[str, str]) -> dict:
    """Record actual cached bodies, including honest empty/unrecorded outcomes."""
    base = reference / "skills/deep-analysis/scripts/.cache/_quant"
    rows = {}
    codes = sorted({str(row.get("fund_code")) for row in raw.get("fund_managers", [])
                    if isinstance(row, dict) and row.get("fund_code")})
    for code in codes:
        matches = sorted((base / code / "api_cache").glob("top10_holdings*.json"))
        if len(matches) > 1:
            raise ValueError(f"Ambiguous recorded quant response for {code}")
        if not matches:
            rows[code] = {"status": "unrecorded", "data": None}
            continue
        saved = _capture(matches[0], inputs)
        data = saved.get("data")
        if not isinstance(data, list):
            raise ValueError(f"Recorded top holdings is not a list: {matches[0]}")
        rows[code] = {"status": "recorded_empty" if not data else "recorded", "data": data,
                      "recorded_at": saved.get("_cached_at"), "source_file": str(matches[0].resolve())}
    return {"top_holdings": rows, "all_holding_funds": {},
            "missing_response_policy": "return empty with explicit unrecorded diagnostic; not evidence of no holdings",
            "scope": "captured response bodies at a recorded observation time; no TTL renewal, HTTP or production cache read during replay"}


@contextmanager
def recorded_auxiliary(module, recording: dict, calls: list):
    previous = module._fetch_top_holdings, module._fetch_all_holding_funds
    def top(code, top_n=10):
        record = recording.get("top_holdings", {}).get(str(code))
        status = record.get("status") if record else "unrecorded"
        calls.append({"operation": "top_holdings", "key": str(code), "status": status})
        return deepcopy((record or {}).get("data") or [])[:top_n]
    def all_funds(code, max_funds=80):
        record = recording.get("all_holding_funds", {}).get(str(code))
        status = record.get("status") if record else "unrecorded"
        calls.append({"operation": "all_holding_funds", "key": str(code), "status": status})
        return deepcopy((record or {}).get("data") or [])[:max_funds]
    module._fetch_top_holdings, module._fetch_all_holding_funds = top, all_funds
    try:
        yield
    finally:
        module._fetch_top_holdings, module._fetch_all_holding_funds = previous


def _native_review(review, raw, panel, synthesis, agent_analysis):
    """Call the *actual* review_all, substituting only its input-cache reader."""
    cache = importlib.import_module("lib.cache")
    original = cache.read_task_output
    values = {"raw_data": raw, "panel": panel, "synthesis": synthesis, "agent_analysis": agent_analysis}
    cache.read_task_output = lambda ticker, name: values.get(name)
    try:
        return review.review_all(str(raw.get("full") or raw.get("ticker") or ""))
    finally:
        cache.read_task_output = original


def _stage2_panel(panel_input, computed):
    # An explicit empty panel is invalid data, not permission to choose another.
    return deepcopy(computed if panel_input is None else panel_input)


def _legacy_replay(host, raw, agent_analysis, panel_input, profile, school):
    """Baseline sequence from _execute_symbol; never presented as a live run."""
    score = importlib.import_module("lib.pipeline.score_fns")
    methods = importlib.import_module("compute_deep_methods")
    sf = importlib.import_module("lib.stock_features")
    features = host._align_feature_identity(sf.extract_features(raw, raw.get("dimensions", {})), raw)
    dim20 = methods.compute_dim_20(features, raw)
    raw["dimensions"]["20_valuation_models"] = dim20
    dim21 = methods.compute_dim_21(features, raw, dim20.get("data"))
    raw["dimensions"]["21_research_workflow"] = dim21
    dim22 = methods.compute_dim_22(features, host._deep_methods_compatible_raw(raw), dim20.get("data"), dim21.get("data"))
    dim22["data"]["portfolio_rebalance"] = {"quality_status": "data_gap", "data_gap": ["explicit_portfolio_positions_required"], "result": None}
    raw["dimensions"]["22_deep_methods"] = dim22
    dimensions = host._apply_scoring_exclusions(score.score_dimensions(raw), raw)
    with host._uzi_native_environment(profile=profile, school=school):
        computed = score.generate_panel(dimensions, raw)
        panel = host._profile_native_panel(_stage2_panel(panel_input, computed), profile)
        before_panel = deepcopy(panel)
        synthesis = score.generate_synthesis(raw, dimensions, panel, agent_analysis=agent_analysis)
    overrides = (agent_analysis or {}).get("per_investor_override") or {}
    panel = host._panel_with_roleplay_overrides(panel, overrides, profile=profile) if overrides else host._profile_native_panel(panel, profile)
    review = host.run_native_self_review(symbol=str(raw.get("full") or raw.get("ticker")), profile=profile,
        school=school, raw=raw, dimensions=dimensions, panel=panel, synthesis=synthesis,
        agent_analysis=agent_analysis, agent_validation_issues=host._validate_agent_analysis(agent_analysis) if agent_analysis else [],
        roleplay={"required": True, "status": "completed", "agent_reviewed": True},
        qualitative_execution={"required": True, "status": "completed", "agent_reviewed": True},
        computation_error=None, full_agent_workflow=True)
    return {"features": features, "raw_after_derivation": raw, "dimensions": dimensions,
            "panel_before_overrides": before_panel, "panel": panel, "synthesis": synthesis,
            "native_python_review": review}


def _reference_replay(raw, agent_analysis, panel_input):
    sf = importlib.import_module("lib.stock_features")
    methods = importlib.import_module("compute_deep_methods")
    score = importlib.import_module("lib.pipeline.score_fns")
    features = sf.extract_features(raw, raw.get("dimensions", {}))
    modeling = sf.sanitize_features(features)
    dim20 = methods.compute_dim_20(modeling, raw)
    raw["dimensions"]["20_valuation_models"] = dim20
    dim21 = methods.compute_dim_21(modeling, raw, dim20["data"])
    raw["dimensions"]["21_research_workflow"] = dim21
    raw["dimensions"]["22_deep_methods"] = methods.compute_dim_22(modeling, raw, dim20["data"], dim21["data"])
    dimensions = score.score_dimensions(raw)
    computed = score.generate_panel(dimensions, raw)
    panel = _stage2_panel(panel_input, computed)
    before_panel = deepcopy(panel)
    synthesis = score.generate_synthesis(raw, dimensions, panel, agent_analysis=agent_analysis)
    review = _native_review(importlib.import_module("lib.self_review"), raw, panel, synthesis, agent_analysis)
    return {"features": features, "raw_after_derivation": raw, "dimensions": dimensions,
            "panel_before_overrides": before_panel, "panel": panel, "synthesis": synthesis,
            "native_python_review": review}


def _review_business(value):
    # The host adds provenance not present in upstream review_all. Validate it
    # against the verified reference, rather than silently discarding a mismatch.
    if "source_commit" in value and value["source_commit"] != PIN:
        raise ValueError("Native review provenance does not match the pinned reference")
    return {key: item for key, item in value.items() if key not in {"reviewed_at", "source_commit"}}


def _split_host_diagnostics(value):
    """Host completion/architecture diagnostics are not native output fields.

    Their effects on raw/features/panel/synthesis/review remain fully compared.
    No nested native business field is filtered by this separation.
    """
    result = dict(value)
    return result, result.pop("host_checks", {})


def worker_error(result):
    if result.get("error"):
        return result["error"]
    if result.get("network_attempts") or result.get("forbidden_writes"):
        return {"type": "ReplayIsolationFailure", "message": "An upstream-caught forbidden I/O attempt is still a failed replay"}
    return None


def worker(args) -> int:
    spec = json.loads(Path(args.worker_spec).read_text())
    reference = Path(spec["reference"]).resolve()
    root = Path(spec["root"]).resolve()
    work = Path(spec["work_directory"]).resolve()
    sys.dont_write_bytecode = True
    random.seed(spec["random_seed"])
    network_attempts, writes = [], []
    def guard(event, values):
        if event in {"socket.connect", "socket.getaddrinfo", "socket.gethostbyname", "socket.sendto", "subprocess.Popen"}:
            network_attempts.append(event)
            raise RuntimeError(f"Offline replay prohibits {event}")
        if event == "open":
            target, mode, flags = values
            is_write = (isinstance(mode, str) and any(c in mode for c in "wax+")) or bool((flags or 0) & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
            if is_write and isinstance(target, (str, bytes, os.PathLike)):
                path = Path(os.fsdecode(target)).resolve()
                if not path.is_relative_to(work) and str(path) != "/dev/null":
                    writes.append(str(path))
                    raise RuntimeError(f"Replay forbids non-experiment file write: {path}")
    sys.addaudithook(guard)
    expected_root = reference / "skills/deep-analysis/scripts" if args.side == "reference" else root / "domain_packs/finance_ashare/vendor/uzi_3_9_2/scripts"
    host = None
    log = io.StringIO()
    business = None
    error = None
    host_diagnostics = {}
    native_review_source_commit = None
    calls = []
    started = time.perf_counter()
    with redirect_stdout(log), redirect_stderr(log):
        try:
            if args.side == "reference":
                sys.path = [str(expected_root)] + [p for p in sys.path if Path(p or os.getcwd()).resolve() not in {root, root / "scripts"}]
            else:
                sys.path.insert(0, str(root))
                if spec.get("legacy_host_source"):
                    source = Path(spec["legacy_host_source"]).resolve()
                    module_spec = importlib.util.spec_from_file_location("_alignment_legacy_host", source)
                    host = importlib.util.module_from_spec(module_spec)
                    sys.modules[module_spec.name] = host
                    module_spec.loader.exec_module(host)
                else:
                    host = importlib.import_module(HOST_MODULE)
                host._initialize_native_imports()
            profile = importlib.import_module("lib.analysis_profile")
            profile.apply_profile_to_env(profile.get_profile(spec["profile"]))
            os.environ.pop("UZI_SKIP_REVIEW", None)
            if spec.get("school"):
                os.environ["UZI_SCHOOL"] = spec["school"]
            else:
                os.environ.pop("UZI_SCHOOL", None)
            for name in ("lib.stock_features", "compute_deep_methods", "lib.pipeline.score_fns", "lib.self_review", "lib.quant_signal"):
                module = importlib.import_module(name)
                if not Path(module.__file__).resolve().is_relative_to(expected_root):
                    raise RuntimeError(f"Wrong native module for {args.side}: {name}: {module.__file__}")
            # Modeling modules use this datetime symbol; no business dates are discarded.
            fixed = datetime_module.datetime.fromisoformat(spec["clock"])
            class FixedDateTime(datetime_module.datetime):
                @classmethod
                def now(cls, tz=None):
                    return fixed.replace(tzinfo=tz) if tz else fixed
            importlib.import_module("lib.research_workflow").datetime = FixedDateTime
            case = spec["case"]
            raw = deepcopy(case["raw"])
            aa = deepcopy(case.get("agent_analysis"))
            panel = deepcopy(case.get("panel"))
            with recorded_auxiliary(importlib.import_module("lib.quant_signal"), spec["auxiliary"], calls):
                if case["mode"] == "saved_context_review":
                    if args.side == "reference":
                        review = _native_review(importlib.import_module("lib.self_review"), raw, panel, deepcopy(case["synthesis"]), aa)
                    else:
                        review = host.run_native_self_review(symbol=case["symbol"], profile=spec["profile"], school=spec.get("school"),
                            raw=raw, dimensions=deepcopy(case["dimensions"]), panel=panel, synthesis=deepcopy(case["synthesis"]), agent_analysis=aa,
                            agent_validation_issues=host._validate_agent_analysis(aa) if aa else [], roleplay={}, qualitative_execution={},
                            computation_error=None, full_agent_workflow=True, historical_pit=True)
                        review = review.get("native_python_review", review)
                    native_review_source_commit = review.get("source_commit")
                    business = {"native_python_review": _review_business(review)}
                elif args.side == "reference":
                    business = _reference_replay(raw, aa, panel)
                elif not spec.get("legacy_host_source") and hasattr(host, "replay_native_alignment"):
                    business = host.replay_native_alignment(raw, profile=spec["profile"], school=spec.get("school"), agent_analysis=aa, panel=panel)
                else:
                    business = _legacy_replay(host, raw, aa, panel, spec["profile"], spec.get("school"))
            if business and isinstance(business.get("native_python_review"), dict):
                native_review_source_commit = business["native_python_review"].get("source_commit", native_review_source_commit)
                business["native_python_review"] = _review_business(business["native_python_review"])
            if business:
                business, host_diagnostics = _split_host_diagnostics(business)
        except Exception as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
    sources = {}
    for name, module in list(sys.modules.items()):
        filename = getattr(module, "__file__", None)
        if not filename or not str(filename).endswith(".py"):
            continue
        path = Path(filename).resolve()
        if path.is_relative_to(expected_root) or name == HOST_MODULE or name == "_alignment_legacy_host":
            sources[name] = {"path": str(path), "sha256": digest(path.read_bytes())}
    host_imports = sorted(name for name in sys.modules if name.startswith(("domain_packs", "research_system")))
    if args.side == "reference" and host_imports:
        error = {"type": "IsolationFailure", "message": "Reference imported host modules"}
    result = {"side": args.side, "pid": os.getpid(), "worker_mode": "legacy_host_sequence" if spec.get("legacy_host_source") and args.side == "host" else "host_hook" if host and hasattr(host, "replay_native_alignment") else "native_reference" if args.side == "reference" else "legacy_host_sequence",
              "business": json_value(business), "error": error, "sources": sources, "host_imports": host_imports,
              "host_diagnostics": json_value(host_diagnostics),
              "native_review_source_commit": native_review_source_commit,
              "harness_source_sha256": digest(Path(__file__).read_bytes()),
              "auxiliary_calls": calls, "network_attempts": network_attempts, "forbidden_writes": writes,
              "model_calls": 0, "elapsed_seconds": time.perf_counter() - started, "log": log.getvalue()}
    write_new(Path(args.worker_output), result)
    return 0 if error is None and not network_attempts and not writes else 2


def known_upstream_issues(reference_result: dict) -> list[dict]:
    data = reference_result.get("business") or {}
    features = data.get("features") or {}
    found = []
    if features.get("ma_align") == "非多头" and features.get("ma_bull_aligned") is True:
        found.append({"id": "native_non_bullish_substring", "classification": "known_upstream_issue"})
    if features.get("pe_quantile_5y") == 50:
        raw = data.get("raw_after_derivation") or {}
        value = (((raw.get("dimensions") or {}).get("10_valuation") or {}).get("data") or {}).get("pe_quantile")
        if value in (None, "", "—"):
            found.append({"id": "native_missing_pe_quantile_default_50", "classification": "known_upstream_issue"})
    panel = data.get("panel") or {}
    active = [row for row in panel.get("investors", [])
              if row.get("mandate") != "short" and row.get("signal") != "skip"]
    if isinstance(panel.get("long_active"), int) and panel["long_active"] != len(active):
        found.append({"id": "native_post_override_long_count_stale", "classification": "known_upstream_issue",
                      "reported_active": panel["long_active"], "actual_active": len(active)})
    return found


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--experiment", type=Path)
    parser.add_argument("--output-directory", type=Path)
    parser.add_argument("--historical-directory", type=Path)
    parser.add_argument("--legacy-host-source", type=Path)
    parser.add_argument("--profiles", nargs="+", choices=("lite", "medium", "deep"), default=["medium"])
    parser.add_argument("--school", choices=list("ABCDEFGHI"))
    parser.add_argument("--panel-input", choices=("recorded", "recomputed"), default="recorded",
                        help="Recorded manual Stage2 panel, or recompute the rule skeleton from the same raw")
    parser.add_argument("--clock", default="2026-09-07T09:00:00")
    parser.add_argument("--worker-spec")
    parser.add_argument("--worker-output")
    parser.add_argument("--side", choices=("reference", "host"))
    args = parser.parse_args(argv)
    if args.worker_spec:
        return worker(args)
    if not args.reference or not args.experiment:
        parser.error("--reference and --experiment are required")
    reference = args.reference.resolve()
    pristine = verify_pristine(reference)
    output = args.output_directory.resolve() if args.output_directory else Path(tempfile.mkdtemp(prefix="uzi-alignment-"))
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Output must be new or empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    # Children execute this immutable copy, not a script an editor may change
    # halfway through a multi-profile suite.
    worker_script = output / "harness-source.py"
    with worker_script.open("xb") as handle:
        handle.write(Path(__file__).read_bytes())
    inputs = {}
    experiment = args.experiment.resolve()
    raw = _capture(experiment / "native-final-raw_data.json", inputs)
    aa = _capture(experiment / "native-final-agent_analysis.json", inputs)
    panel = _capture(experiment / "native-final-panel.json", inputs)
    cases = [{"name": "current-native-agent-recording" if args.panel_input == "recorded" else "current-native-recomputed-panel",
              "mode": "compute_and_synthesis", "raw": raw, "agent_analysis": aa,
              "panel": panel if args.panel_input == "recorded" else None}]
    if args.historical_directory:
        for path in sorted(args.historical_directory.resolve().glob("*-native-1.json")):
            artifact = _capture(path, inputs)
            for row in artifact["content"]["results"]:
                cases.append({"name": "historical-" + row["symbol"], "mode": "saved_context_review", "symbol": row["symbol"],
                              "raw": row["raw"], "agent_analysis": row.get("agent_analysis"), "panel": row["panel"],
                              "dimensions": row["dimensions"], "synthesis": row["synthesis"]})
    auxiliary = capture_auxiliary(reference, raw, inputs)
    if args.legacy_host_source:
        inputs[str(args.legacy_host_source.resolve())] = digest(args.legacy_host_source.read_bytes())
    write_new(output / "input-manifest.json", {"source_files": inputs, "case_hashes": {case["name"]: digest(canonical(case)) for case in cases},
              "auxiliary_hash": digest(canonical(auxiliary)), "pristine_reference": pristine})
    comparisons = []
    for case in cases:
        for profile in args.profiles:
            case_dir = output / f"{case['name']}-{profile}"
            case_dir.mkdir()
            spec = {"reference": str(reference), "root": str(ROOT), "case": case, "profile": profile, "school": args.school,
                    "clock": args.clock, "random_seed": 392, "auxiliary": auxiliary, "work_directory": str(output),
                    "legacy_host_source": str(args.legacy_host_source.resolve()) if args.legacy_host_source else None}
            spec_path = case_dir / "input.json"
            # Recorded native JSON may contain NaN. Keep it unchanged for Python
            # replay; hashes and audit outputs use explicit lossless numeric tags.
            with spec_path.open("x", encoding="utf-8") as handle:
                json.dump(spec, handle, ensure_ascii=False, allow_nan=True)
            results = {}
            for side in ("reference", "host"):
                path = case_dir / f"{side}.json"
                env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "392", "TZ": "UTC"}
                env.pop("PYTHONPATH", None)
                proc = subprocess.run([sys.executable, "-B", str(worker_script), "--worker-spec", str(spec_path),
                                       "--worker-output", str(path), "--side", side], cwd=case_dir, env=env, capture_output=True, text=True, timeout=120)
                if not path.exists():
                    write_new(path, {"side": side, "business": None, "error": {"type": "WorkerFailure", "message": proc.stderr[-2000:]}, "exit_code": proc.returncode})
                results[side] = json.loads(path.read_text())
            deltas = field_diff(results["reference"].get("business"), results["host"].get("business"))
            for delta in deltas:
                delta["classification"] = "unexpected_host_delta"
            errors = {side: worker_error(result) for side, result in results.items() if worker_error(result)}
            comparison = {"case": case["name"], "mode": case["mode"], "profile": profile, "deltas": deltas,
                          "error_by_side": errors, "known_upstream_issues": known_upstream_issues(results["reference"]),
                          "status": "incomplete" if errors else "different" if deltas else "aligned",
                          "reference_output_sha256": digest((case_dir / "reference.json").read_bytes()),
                          "host_output_sha256": digest((case_dir / "host.json").read_bytes())}
            write_new(case_dir / "diff.json", comparison)
            comparisons.append(comparison)
            print(json.dumps({"case": case["name"], "profile": profile, "status": comparison["status"], "deltas": len(deltas), "errors": errors}, ensure_ascii=False), flush=True)
    changed = [path for path, expected in inputs.items() if digest(Path(path).read_bytes()) != expected]
    result = {"schema_version": "1", "experiment": "uzi-native-alignment-recorded-agent-offline", "upstream_commit": PIN,
              "host_commit": _git_revision(ROOT), "script_sha256": digest(worker_script.read_bytes()),
              "python_version": sys.version,
              "dependency_versions": {name: importlib.metadata.version(name) for name in ("pandas", "numpy", "pydantic", "requests")},
              "created_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat(), "profiles": args.profiles,
              "source_files_unchanged": not changed, "changed_inputs": changed, "comparisons": comparisons,
              "comparison_policy": {"business_fields_ignored": [], "float_tolerances": {}, "metadata_excluded": ["native_python_review.reviewed_at", "native_python_review.source_commit (must match verified PIN; retained in worker provenance)"],
                                    "host_metadata_separately_preserved": ["host_checks (not an upstream business field; effects still diffed)"],
                                    "clock_control": "lib.research_workflow.datetime.now fixed identically; business dates remain compared", "random_seed": 392},
              "required_architecture_delta": [
                  {"id": "frozen_auxiliary_input", "status": "test-injection-only", "reason": "Only recorded provider leaf responses are injected identically; does not prove production auxiliary evidence is integrated."},
                  {"id": "no_hidden_network", "status": "enforced_by_worker_audit_hook"},
                  {"id": "pit_and_rendering", "status": "not_tested_in_this_offline_replay"}],
              "scope": "Same recorded Agent outputs, not independently executed models. Current sample computes 20/21/22 and Stage2; historical samples are unchanged-context self-review only. Neither HTML delivery nor trading is tested.",
              "status": "aligned" if not changed and all(row["status"] == "aligned" for row in comparisons) else "differences_or_incomplete"}
    write_new(output / "alignment-result.json", result)
    print(json.dumps({"output_directory": str(output), "status": result["status"]}, ensure_ascii=False))
    return 0 if result["status"] == "aligned" else 1


if __name__ == "__main__":
    raise SystemExit(main())
