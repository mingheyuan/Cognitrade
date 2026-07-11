from __future__ import annotations

import sys
import argparse
import os
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Cognitrade test suite with isolated file processes.")
    parser.add_argument("--pattern", default="test_*.py")
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()
    tests_root = Path(__file__).resolve().parent
    repository_root = tests_root.parent
    env = os.environ.copy()
    path_entries = [str(tests_root), str(repository_root)]
    if env.get("PYTHONPATH"):
        path_entries.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(path_entries)
    failures = 0
    selected = sorted(tests_root.glob(args.pattern))
    for path in selected:
        module = f"tests.{path.stem}"
        print(f"[START] {module}", flush=True)
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "unittest", module, "-v"],
                cwd=repository_root,
                env=env,
                capture_output=True,
                text=True,
                timeout=args.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            failures += 1
            print(f"[TIMEOUT] {module} after {args.timeout:.1f}s", flush=True)
            if exc.stdout:
                print(exc.stdout, end="" if exc.stdout.endswith("\n") else "\n")
            continue
        if completed.returncode == 0:
            print(f"[PASS] {module}", flush=True)
        else:
            failures += 1
            print(f"[FAIL] {module} (exit {completed.returncode})", flush=True)
            if completed.stdout:
                print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
            if completed.stderr:
                print(completed.stderr, end="" if completed.stderr.endswith("\n") else "\n")
    print(f"[SUMMARY] files={len(selected)} failures={failures}", flush=True)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
