from __future__ import annotations

import sys
import unittest
from pathlib import Path


def main() -> int:
    tests_root = Path(__file__).resolve().parent
    repository_root = tests_root.parent
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))

    # Load the canonical top-level test suite in a deterministic order.
    suite = unittest.TestSuite()
    for path in sorted(tests_root.glob("test_*.py")):
        suite.addTests(
            unittest.defaultTestLoader.loadTestsFromName(
                f"tests.{path.stem}"
            )
        )
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
