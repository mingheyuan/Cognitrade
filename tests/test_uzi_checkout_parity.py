from __future__ import annotations

import os
from pathlib import Path
import subprocess
import unittest

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MAPPING_PATH = (
    REPOSITORY_ROOT
    / "domain_packs"
    / "finance_ashare"
    / "migration"
    / "uzi_source_mapping.yaml"
)


def _file_tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


def _production_python_tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*.py"))
        if "tests" not in path.parts
        and "__pycache__" not in path.parts
        and path.relative_to(root).as_posix() != "__init__.py"
    }


class UziPinnedCheckoutParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        upstream_value = str(os.environ.get("UZI_UPSTREAM_ROOT") or "").strip()
        if not upstream_value:
            raise unittest.SkipTest(
                "UZI_UPSTREAM_ROOT is only provided by the pinned-checkout CI job"
            )
        cls.upstream_root = Path(upstream_value).resolve()
        if not cls.upstream_root.is_dir():
            raise AssertionError(f"pinned UZI checkout missing: {cls.upstream_root}")
        cls.mapping = yaml.safe_load(MAPPING_PATH.read_text(encoding="utf-8"))
        cls.vendor_root = (
            REPOSITORY_ROOT / str(cls.mapping["source"]["vendored_root"])
        ).resolve()

    def test_checkout_is_the_exact_declared_commit(self) -> None:
        completed = subprocess.run(
            ["git", "-C", str(self.upstream_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.stdout.strip(), self.mapping["source"]["commit"])

    def test_declared_vendored_scope_matches_pinned_checkout_byte_for_byte(self) -> None:
        relocations = self.mapping["vendored_methodology_resources"][
            "deep_analysis_relocations"
        ]
        for upstream_relative, vendor_relative in relocations.items():
            upstream_path = self.upstream_root / upstream_relative
            vendor_path = self.vendor_root / vendor_relative
            with self.subTest(upstream=upstream_relative, vendor=vendor_relative):
                self.assertTrue(upstream_path.exists(), upstream_path)
                self.assertTrue(vendor_path.exists(), vendor_path)
                self.assertEqual(upstream_path.is_file(), vendor_path.is_file())
                self.assertEqual(upstream_path.is_dir(), vendor_path.is_dir())
                if upstream_path.is_file():
                    self.assertEqual(vendor_path.read_bytes(), upstream_path.read_bytes())
                elif upstream_relative == "skills/deep-analysis/scripts":
                    # Upstream tests remain in the pristine checkout and exercise
                    # the overlaid implementation later in this CI job.  The
                    # vendored runtime scope is the complete non-test Python tree.
                    self.assertEqual(
                        _production_python_tree(vendor_path),
                        _production_python_tree(upstream_path),
                    )
                else:
                    self.assertEqual(_file_tree(vendor_path), _file_tree(upstream_path))

        for skill_root in self.mapping["vendored_methodology_resources"][
            "standalone_skill_roots"
        ]:
            with self.subTest(skill_root=skill_root):
                self.assertTrue((self.upstream_root / skill_root).is_dir())
                self.assertTrue((self.vendor_root / skill_root).is_dir())
                self.assertEqual(
                    _file_tree(self.vendor_root / skill_root),
                    _file_tree(self.upstream_root / skill_root),
                )

        self.assertTrue((self.upstream_root / "commands").is_dir())
        self.assertTrue((self.vendor_root / "commands").is_dir())
        self.assertEqual(
            _file_tree(self.vendor_root / "commands"),
            _file_tree(self.upstream_root / "commands"),
        )
        self.assertEqual(
            (self.vendor_root / "agents" / "investor-panel.md").read_bytes(),
            (self.upstream_root / "agents" / "investor-panel.md").read_bytes(),
        )
        self.assertEqual(
            (self.vendor_root / "LICENSE").read_bytes(),
            (self.upstream_root / "LICENSE").read_bytes(),
        )


if __name__ == "__main__":
    unittest.main()
