from __future__ import annotations

import argparse
from importlib import metadata, util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Sequence


_RUNTIME_PACKAGES: tuple[tuple[str, str, str], ...] = (
    ("akshare", "akshare", "A-share provider aggregation"),
    ("baostock", "baostock", "BaoStock fallback"),
    ("beautifulsoup4", "bs4", "public disclosure parsing"),
    ("ddgs", "ddgs", "web evidence search"),
    ("efinance", "efinance", "Eastmoney fallback"),
    ("exchange-calendars", "exchange_calendars", "trading calendar"),
    ("fastapi", "fastapi", "HTTP API"),
    ("httpx", "httpx", "async HTTP client"),
    ("jsonschema", "jsonschema", "artifact validation"),
    (
        "py-mini-racer" if sys.platform.startswith("linux") else "mini-racer",
        "py_mini_racer",
        "AkShare JavaScript decoding",
    ),
    ("openai", "openai", "model provider"),
    ("pandas", "pandas", "tabular finance data"),
    ("playwright", "playwright", "browser-backed evidence and report export"),
    ("pypdf", "pypdf", "PDF evidence parsing"),
    ("pydantic", "pydantic", "API and artifact contracts"),
    ("python-dotenv", "dotenv", "environment loading"),
    ("PyYAML", "yaml", "DomainPack configuration"),
    ("qdrant-client", "qdrant_client", "vector memory adapter"),
    ("redis", "redis", "distributed resource leases"),
    ("requests", "requests", "provider HTTP client"),
    ("tushare", "tushare", "TuShare provider"),
    ("uvicorn", "uvicorn", "ASGI server"),
    ("yfinance", "yfinance", "global peer data"),
    ("vnpy", "vnpy", "VeighNa trading models"),
    ("vnpy_paperaccount", "vnpy_paperaccount", "VeighNa paper matching"),
    ("hikyuu", "hikyuu", "isolated strategy backtesting"),
)

_WORKSPACE_DIRECTORIES = (
    "runs",
    "chat_runs",
    "reports",
    "memory",
    "cache/finance_ashare",
    "paper_trading",
    "backtests",
)


def _package_checks() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for distribution, module, capability in _RUNTIME_PACKAGES:
        available = util.find_spec(module) is not None
        version: str | None = None
        if available:
            try:
                version = metadata.version(distribution)
            except metadata.PackageNotFoundError:
                available = False
        checks.append(
            {
                "distribution": distribution,
                "module": module,
                "capability": capability,
                "available": available,
                "version": version,
            }
        )
    return checks


def _run_probe(
    name: str,
    code: str,
    *,
    environment: dict[str, str] | None = None,
    timeout_seconds: int = 45,
) -> dict[str, Any]:
    try:
        process = subprocess.run(
            [sys.executable, "-c", code],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "name": name,
            "passed": False,
            "detail": f"探测在 {timeout_seconds} 秒后超时",
        }
    detail = (process.stdout if process.returncode == 0 else process.stderr).strip()
    return {
        "name": name,
        "passed": process.returncode == 0,
        "detail": detail[-2000:] or None,
    }


def _workspace_checks(workspace_root: Path | None) -> list[dict[str, Any]]:
    if workspace_root is None:
        return []
    checks: list[dict[str, Any]] = []
    for relative in _WORKSPACE_DIRECTORIES:
        destination = workspace_root / relative
        passed = destination.is_dir() and os.access(destination, os.R_OK | os.W_OK | os.X_OK)
        detail: str | None = None
        if passed:
            try:
                with tempfile.NamedTemporaryFile(dir=destination, prefix=".preflight-"):
                    pass
            except OSError as exc:
                passed = False
                detail = f"{exc.__class__.__name__}: {exc}"
        elif not destination.is_dir():
            detail = "directory is missing"
        else:
            detail = "directory is not readable and writable by the runtime user"
        checks.append(
            {
                "path": str(destination),
                "passed": passed,
                "detail": detail,
            }
        )
    return checks


def build_deployment_report(
    *,
    workspace_root: Path | None = None,
    require_browser: bool = True,
) -> dict[str, Any]:
    packages = _package_checks()
    probes = [
        _run_probe(
            "python-dependency-graph",
            "import subprocess, sys; raise SystemExit(subprocess.call([sys.executable, '-m', 'pip', 'check']))",
        ),
        _run_probe(
            "api-import",
            "import research_system.api.app; print('FastAPI application import succeeded')",
        ),
        _run_probe(
            "veighna-paperaccount",
            "from vnpy.trader.object import ContractData, OrderRequest, TickData; "
            "from vnpy_paperaccount.engine import PaperEngine; "
            "print('VeighNa PaperAccount native models and matching engine imported')",
        ),
    ]

    temporary_parent = workspace_root / "backtests" if workspace_root else None
    if temporary_parent is not None and not temporary_parent.is_dir():
        temporary_parent = None
    with tempfile.TemporaryDirectory(prefix="cognitrade-hikyuu-", dir=temporary_parent) as temporary:
        # Hikyuu initializes its Hub during import. Seed the same local-only Hub
        # database used by the production runner so a deployment check never
        # downloads strategies or depends on external network availability.
        from research_system.api.hikyuu_backtest_service import HikyuuBacktestService

        isolated_home = Path(temporary)
        HikyuuBacktestService._prepare_hikyuu_home(isolated_home)
        isolated_environment = os.environ.copy()
        isolated_environment["HOME"] = temporary
        isolated_environment["MPLCONFIGDIR"] = str(isolated_home / ".cache" / "matplotlib")
        probes.append(
            _run_probe(
                "hikyuu-native-runtime",
                "from importlib import metadata; import hikyuu; "
                "print('Hikyuu native runtime imported:', metadata.version('hikyuu'))",
                environment=isolated_environment,
            )
        )

    if require_browser:
        probes.append(
            _run_probe(
                "playwright-chromium",
                "from pathlib import Path; from playwright.sync_api import sync_playwright; "
                "p = sync_playwright().start(); path = Path(p.chromium.executable_path); "
                "p.stop(); assert path.is_file(), f'Chromium executable missing: {path}'; print(path)",
            )
        )

    workspace = _workspace_checks(workspace_root)
    missing_packages = [item["distribution"] for item in packages if not item["available"]]
    failed_probes = [item["name"] for item in probes if not item["passed"]]
    failed_paths = [item["path"] for item in workspace if not item["passed"]]
    passed = not (missing_packages or failed_probes or failed_paths)
    return {
        "status": "passed" if passed else "failed",
        "packages": packages,
        "probes": probes,
        "workspace": workspace,
        "failures": {
            "missing_packages": missing_packages,
            "failed_probes": failed_probes,
            "failed_paths": failed_paths,
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate the complete Cognitrade deployment runtime without network data calls."
    )
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument(
        "--skip-browser",
        action="store_true",
        help="Skip the Chromium binary check (intended only for non-browser test environments).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = build_deployment_report(
        workspace_root=args.workspace,
        require_browser=not args.skip_browser,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_deployment_report"]
