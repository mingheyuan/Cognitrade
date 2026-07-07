from __future__ import annotations

import unittest

from research_system.runtime.platform import run_checks
from research_system.runtime.platform.checks import run_checks as package_run_checks
from research_system.runtime.platform.settings import (
    Settings as PackageSettings,
    ensure_workspace_layout as package_ensure_workspace_layout,
)
from research_system.runtime.platform.skill_runtime import (
    LocalSkillBackend as PackageLocalSkillBackend,
    SkillRuntime as PackageSkillRuntime,
)
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout
from research_system.runtime.platform.skill_runtime import LocalSkillBackend, SkillRuntime


class RuntimePlatformRuntimeTests(unittest.TestCase):
    def test_settings_compat_exports_match_platform_exports(self) -> None:
        self.assertIs(Settings, PackageSettings)
        self.assertIs(ensure_workspace_layout, package_ensure_workspace_layout)

    def test_checks_compat_exports_match_platform_exports(self) -> None:
        self.assertIs(run_checks, package_run_checks)

    def test_skill_runtime_compat_exports_match_platform_exports(self) -> None:
        self.assertIs(LocalSkillBackend, PackageLocalSkillBackend)
        self.assertIs(SkillRuntime, PackageSkillRuntime)


if __name__ == "__main__":
    unittest.main()
