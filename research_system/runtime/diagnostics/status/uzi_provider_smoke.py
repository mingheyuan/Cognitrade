from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any

from domain_packs.finance_ashare.tools.snapshot_collector import collect_snapshots
from research_system.adapters.vendors.provider_registry import VendorProviderRegistry
from research_system.runtime.platform.settings import Settings


UZI_FETCHER_DIMENSIONS: tuple[tuple[str, str], ...] = (
    ("0_basic", "market"),
    ("1_financials", "fundamental"),
    ("2_kline", "kline"),
    ("3_macro", "macro"),
    ("4_peers", "peers"),
    ("5_chain", "chain"),
    ("6_research", "research"),
    ("7_industry", "industry"),
    ("8_materials", "materials"),
    ("9_futures", "futures"),
    ("10_valuation", "valuation"),
    ("11_governance", "governance"),
    ("12_capital_flow", "capital_flow"),
    ("13_policy", "policy"),
    ("14_moat", "moat"),
    ("15_events", "events"),
    ("16_lhb", "lhb"),
    ("17_sentiment", "sentiment"),
    ("18_trap", "trap"),
    ("19_contests", "contests"),
    ("fund_holders", "fund_holders"),
    ("similar_stocks", "similar_stocks"),
)

_UNAVAILABLE_STATUSES = {
    "data_gap",
    "degraded",
    "no_data",
    "parse_failed",
    "provider_unavailable",
    "rate_limited",
}
_SOURCE_FIELDS = (
    "skill",
    "vendor",
    "provider_id",
    "source_id",
    "dim",
    "chain_position",
    "status",
    "error_type",
)
_MODULE_NAME = "research_system.runtime.diagnostics.status.uzi_provider_smoke"


class _DimensionProbeFailure(RuntimeError):
    def __init__(self, return_code: int) -> None:
        super().__init__(f"dimension probe exited with code {return_code}")
        self.return_code = int(return_code)


def run_uzi_provider_smoke(
    settings: Settings,
    *,
    symbol: str = "600519",
    sector: str | None = None,
    dimensions: Iterable[str] | None = None,
    max_workers: int = 1,
    collector: Callable[..., list[dict[str, Any]]] = collect_snapshots,
) -> dict[str, Any]:
    """Probe UZI's 22 real provider dimensions without exposing raw observations.

    A data gap is a valid provider outcome. Contract failures are limited to a
    missing dimension result or any result without source lineage.
    """

    if int(max_workers) != 1:
        raise ValueError(
            "UZI provider smoke must run serially because some AkShare providers use a process-global JavaScript runtime"
        )
    selected = _select_dimensions(dimensions)
    snapshot_types = [snapshot_type for _fetcher_id, snapshot_type in selected]
    started = time.monotonic()
    snapshots = collector(
        query=f"UZI 3.9.2 provider smoke for {symbol}",
        symbols=[symbol],
        snapshot_types=snapshot_types,
        sector=sector,
        max_workers=1,
        provider_registry=VendorProviderRegistry(settings),
    )
    by_type = {
        str(snapshot.get("snapshot_type") or ""): snapshot
        for snapshot in snapshots
        if isinstance(snapshot, dict) and snapshot.get("snapshot_type")
    }
    rows = [
        _summarize_snapshot(
            fetcher_id,
            snapshot_type,
            by_type.get(snapshot_type),
            settings=settings,
        )
        for fetcher_id, snapshot_type in selected
    ]
    return _assemble_report(
        settings,
        symbol=symbol,
        sector=sector,
        selected=selected,
        rows=rows,
        started=started,
    )


def run_uzi_provider_smoke_isolated(
    settings: Settings,
    *,
    symbol: str = "600519",
    sector: str | None = None,
    dimensions: Iterable[str] | None = None,
    timeout_seconds: float = 35.0,
    output: Path | None = None,
    runner: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Probe each dimension in a fresh process and persist evidence incrementally.

    Third-party provider SDKs can block below Python's request timeout. Process
    isolation lets the CI smoke record that real timeout as a data gap without
    losing the evidence already collected for other dimensions.
    """

    timeout = float(timeout_seconds)
    if timeout <= 0:
        raise ValueError("dimension timeout must be greater than zero")
    selected = _select_dimensions(dimensions)
    started = time.monotonic()
    probe = runner or _probe_dimension_subprocess
    rows: list[dict[str, Any]] = []
    for index, (fetcher_id, snapshot_type) in enumerate(selected, start=1):
        try:
            child_report = probe(
                fetcher_id=fetcher_id,
                symbol=symbol,
                sector=sector,
                timeout_seconds=timeout,
            )
            child_rows = child_report.get("dimensions")
            row = (
                child_rows[0]
                if isinstance(child_rows, list)
                and len(child_rows) == 1
                and isinstance(child_rows[0], dict)
                and child_rows[0].get("fetcher_id") == fetcher_id
                and child_rows[0].get("snapshot_type") == snapshot_type
                else _failed_probe_row(fetcher_id, snapshot_type, "invalid_child_report")
            )
        except subprocess.TimeoutExpired:
            row = _timeout_row(fetcher_id, snapshot_type, timeout)
        except _DimensionProbeFailure as exc:
            row = _failed_probe_row(
                fetcher_id,
                snapshot_type,
                f"child_exit_{exc.return_code}",
            )
        rows.append(row)
        report = _assemble_report(
            settings,
            symbol=symbol,
            sector=sector,
            selected=selected,
            rows=rows,
            started=started,
        )
        if output is not None:
            _write_report(output, report)
        print(
            f"[uzi-provider-smoke] {index}/{len(selected)} "
            f"{fetcher_id}={row['status']}",
            file=sys.stderr,
            flush=True,
        )
    return report


def _assemble_report(
    settings: Settings,
    *,
    symbol: str,
    sector: str | None,
    selected: Sequence[tuple[str, str]],
    rows: Sequence[dict[str, Any]],
    started: float,
) -> dict[str, Any]:
    status_counts = Counter(str(row["status"]) for row in rows)
    contract_failures = [
        str(row["fetcher_id"])
        for row in rows
        if row["status"] == "missing_result"
        or not row["lineage_ok"]
        or row.get("policy_ok") is False
    ]
    incomplete = [fetcher_id for fetcher_id, _snapshot_type in selected[len(rows) :]]
    return {
        "schema_version": "1",
        "uzi_version": "3.9.2",
        "symbol": symbol,
        "sector": sector,
        "requested_count": len(selected),
        "completed_count": len(rows),
        "contract_status": (
            "in_progress" if incomplete else "failed" if contract_failures else "passed"
        ),
        "contract_failures": contract_failures,
        "pending_dimensions": incomplete,
        "status_counts": dict(sorted(status_counts.items())),
        "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
        "sensitive_sources": {
            "enabled": settings.sensitive_sources_enabled,
            "allowlist": list(settings.sensitive_source_allowlist),
            "xueqiu_cookie_configured": bool(settings.xueqiu_cookie),
        },
        "dimensions": rows,
    }


def _probe_dimension_subprocess(
    *,
    fetcher_id: str,
    symbol: str,
    sector: str | None,
    timeout_seconds: float,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="uzi-provider-smoke-") as temp_dir:
        output = Path(temp_dir) / "dimension.json"
        command = [
            sys.executable,
            "-m",
            _MODULE_NAME,
            "--symbol",
            symbol,
            "--dimensions",
            fetcher_id,
            "--output",
            str(output),
        ]
        if sector:
            command.extend(("--sector", sector))
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        if output.is_file():
            try:
                report = json.loads(output.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise _DimensionProbeFailure(completed.returncode or 2) from exc
            if isinstance(report, dict):
                return report
        raise _DimensionProbeFailure(completed.returncode or 2)


def _timeout_row(
    fetcher_id: str,
    snapshot_type: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    return {
        "fetcher_id": fetcher_id,
        "snapshot_type": snapshot_type,
        "status": "data_gap",
        "degraded": True,
        "degrade_reason": "provider_chain_timeout",
        "data_gap": [f"provider_chain_timeout:{timeout_seconds:g}s"],
        "source_refs": [
            {
                "skill": "uzi_provider_smoke",
                "source_id": f"uzi_fetcher:{fetcher_id}",
                "dim": snapshot_type,
                "status": "provider_unavailable",
                "error_type": "TimeoutExpired",
            }
        ],
        "lineage_ok": True,
    }


def _failed_probe_row(
    fetcher_id: str,
    snapshot_type: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "fetcher_id": fetcher_id,
        "snapshot_type": snapshot_type,
        "status": "missing_result",
        "degraded": True,
        "degrade_reason": reason,
        "data_gap": [reason],
        "source_refs": [],
        "lineage_ok": False,
    }


def _select_dimensions(dimensions: Iterable[str] | None) -> list[tuple[str, str]]:
    requested = {str(item).strip() for item in dimensions or [] if str(item).strip()}
    if not requested:
        return list(UZI_FETCHER_DIMENSIONS)
    known = {item for pair in UZI_FETCHER_DIMENSIONS for item in pair}
    unknown = sorted(requested - known)
    if unknown:
        raise ValueError(f"unknown UZI dimensions: {', '.join(unknown)}")
    return [pair for pair in UZI_FETCHER_DIMENSIONS if pair[0] in requested or pair[1] in requested]


def _summarize_snapshot(
    fetcher_id: str,
    snapshot_type: str,
    snapshot: dict[str, Any] | None,
    *,
    settings: Settings,
) -> dict[str, Any]:
    if snapshot is None:
        return {
            "fetcher_id": fetcher_id,
            "snapshot_type": snapshot_type,
            "status": "missing_result",
            "degraded": True,
            "degrade_reason": "collector_result_missing",
            "data_gap": ["collector_result_missing"],
            "source_refs": [],
            "lineage_ok": False,
        }
    facts = snapshot.get("facts") if isinstance(snapshot.get("facts"), dict) else {}
    declared = str(
        snapshot.get("quality_status")
        or snapshot.get("status")
        or facts.get("availability")
        or ""
    ).strip().lower()
    reason = str(snapshot.get("degrade_reason") or facts.get("degrade_reason") or "").strip()
    if reason.startswith("sensitive_source_"):
        status = "disabled"
    elif snapshot.get("degraded") or declared in _UNAVAILABLE_STATUSES:
        status = "data_gap"
    else:
        status = "available"
    source_refs = _source_refs(snapshot)
    data_gap = facts.get("data_gap")
    gaps = [str(item) for item in data_gap] if isinstance(data_gap, list) else []
    row = {
        "fetcher_id": fetcher_id,
        "snapshot_type": snapshot_type,
        "status": status,
        "degraded": status != "available",
        "degrade_reason": reason or None,
        "data_gap": gaps,
        "source_refs": source_refs,
        "lineage_ok": bool(source_refs),
    }
    if snapshot_type == "contests":
        policy_audit = _xueqiu_policy_audit(facts, settings)
        row["sensitive_source_audit"] = policy_audit
        row["policy_ok"] = policy_audit["policy_ok"]
    return row


def _xueqiu_policy_audit(facts: dict[str, Any], settings: Settings) -> dict[str, Any]:
    raw_result = facts.get("_uzi_raw_result")
    raw_data = raw_result.get("data") if isinstance(raw_result, dict) else None
    data = raw_data if isinstance(raw_data, dict) else {}
    cubes = data.get("xueqiu_cubes")
    observed = bool(cubes) if isinstance(cubes, list) else False
    meta = data.get("xueqiu_meta") if isinstance(data.get("xueqiu_meta"), dict) else {}
    allowlist = {str(item).strip().lower() for item in settings.sensitive_source_allowlist}
    configured = (
        settings.sensitive_sources_enabled
        and "xueqiu" in allowlist
        and bool(settings.xueqiu_cookie)
    )
    return {
        "source_id": "xueqiu",
        "configured_enabled": configured,
        "observation_present": observed,
        "source_mode": str(meta.get("source") or "") or None,
        "policy_status": str(meta.get("policy_status") or "") or None,
        "policy_ok": configured or not observed,
    }


def _source_refs(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for field in ("source", "primary_source"):
        value = snapshot.get(field)
        if isinstance(value, dict):
            candidates.append(value)
    trace = snapshot.get("source_trace")
    if isinstance(trace, list):
        candidates.extend(item for item in trace if isinstance(item, dict))
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        ref = {
            field: candidate[field]
            for field in _SOURCE_FIELDS
            if candidate.get(field) not in {None, ""}
        }
        if not ref:
            continue
        fingerprint = json.dumps(ref, sort_keys=True, ensure_ascii=True, default=str)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        refs.append(ref)
    return refs


def _parse_dimensions(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run explicit real-provider smoke checks for UZI 3.9.2 dimensions."
    )
    parser.add_argument("--symbol", default="600519", help="A-share security code")
    parser.add_argument("--sector", default=None, help="Optional sector needed by policy data")
    parser.add_argument(
        "--dimensions",
        type=_parse_dimensions,
        default=None,
        help="Comma-separated fetcher ids or snapshot types; defaults to all 22",
    )
    parser.add_argument(
        "--fail-on-data-gap",
        action="store_true",
        help="Also return a non-zero exit code when a real provider has no data",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path for the redacted JSON smoke report",
    )
    parser.add_argument(
        "--dimension-timeout-seconds",
        type=float,
        default=None,
        help="Run each selected dimension in an isolated process with this timeout",
    )
    return parser


def _write_report(output: Path, report: dict[str, Any]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parents[4]
    try:
        settings = Settings.from_env(repo_root)
        if args.dimension_timeout_seconds is not None:
            report = run_uzi_provider_smoke_isolated(
                settings,
                symbol=args.symbol,
                sector=args.sector,
                dimensions=args.dimensions,
                timeout_seconds=args.dimension_timeout_seconds,
                output=args.output,
            )
        else:
            report = run_uzi_provider_smoke(
                settings,
                symbol=args.symbol,
                sector=args.sector,
                dimensions=args.dimensions,
            )
    except (TypeError, ValueError) as exc:
        print(json.dumps({"status": "invalid_request", "error_type": exc.__class__.__name__}))
        return 2
    rendered = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if args.output is not None:
        _write_report(args.output, report)
    print(rendered)
    if report["contract_status"] != "passed":
        return 2
    if args.fail_on_data_gap and any(
        row["status"] != "available" for row in report["dimensions"]
    ):
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "UZI_FETCHER_DIMENSIONS",
    "run_uzi_provider_smoke",
    "run_uzi_provider_smoke_isolated",
]
