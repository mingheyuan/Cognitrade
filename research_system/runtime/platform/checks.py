from __future__ import annotations

import compileall
import io
import unittest
from pathlib import Path


def run_checks(repo_root: Path) -> dict:
    checks: list[dict] = []

    research_system_root = repo_root / "research_system"
    checks.append(
        {
            "name": "compile_research_system",
            "status": "passed" if compileall.compile_dir(str(research_system_root), quiet=1) else "failed",
            "path": str(research_system_root),
        }
    )

    tests_root = repo_root / "tests"
    if tests_root.exists():
        checks.append(
            {
                "name": "compile_tests",
                "status": "passed" if compileall.compile_dir(str(tests_root), quiet=1) else "failed",
                "path": str(tests_root),
            }
        )

        stream = io.StringIO()
        suite = unittest.defaultTestLoader.discover(str(tests_root), pattern="test_*.py")
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
        checks.append(
            {
                "name": "unit_tests",
                "status": "passed" if result.wasSuccessful() else "failed",
                "tests_run": result.testsRun,
                "failures": len(result.failures),
                "errors": len(result.errors),
                "skipped": len(getattr(result, "skipped", [])),
                "output": stream.getvalue().strip(),
            }
        )
    else:
        checks.append(
            {
                "name": "unit_tests",
                "status": "skipped",
                "reason": "tests directory not found",
            }
        )

    overall_status = "completed" if all(item["status"] in {"passed", "skipped"} for item in checks) else "failed"
    return {
        "status": overall_status,
        "checks": checks,
    }
