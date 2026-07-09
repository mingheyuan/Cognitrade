from __future__ import annotations

import unittest

from research_system.runtime import config, execution, methodology, platform, workspace
from research_system.runtime.execution import core as execution_core
from research_system.runtime.config.settings_env import build_settings_from_env, load_settings_env_values
from research_system.runtime.config.settings_yaml_loader import load_settings_yaml_configs
from research_system.runtime.execution.handlers import OrchestratorAgentHandlers
from research_system.runtime.execution.core import (
    OrchestratorActionRunner,
    OrchestratorExecutionControlSupport,
    OrchestratorExecutionLifecycleSupport,
    OrchestratorExecutionResearchFacade,
    OrchestratorExecutionRuntimeSupport,
    OrchestratorExecutionSupport,
    OrchestratorExecutionTraceSupport,
    OrchestratorPlanExecutor,
    OrchestratorRuntimeControlPlaneController,
    OrchestratorRuntimeProtocolController,
    OrchestratorRuntimeSubagentControlService,
)
from research_system.runtime.methodology.banking import (
    OrchestratorMethodologyBankHighlightSupport as PackageBankHighlightSupport,
    OrchestratorMethodologyBankMetricSupport as PackageBankMetricSupport,
    OrchestratorMethodologyBankScorecardSupport as PackageBankScorecardSupport,
)
from research_system.runtime.methodology.banking import (
    OrchestratorMethodologyBankHighlightSupport,
    OrchestratorMethodologyBankMetricSupport,
    OrchestratorMethodologyBankScorecardSupport,
)
from research_system.runtime.methodology.core import (
    OrchestratorMethodologyBuilder as CorePackageMethodologyBuilder,
)
from research_system.runtime.methodology.core import (
    OrchestratorMethodologyCompareSupport as CorePackageMethodologyCompareSupport,
)
from research_system.runtime.methodology.core import (
    OrchestratorMethodologyEntryFactory as CorePackageMethodologyEntryFactory,
)
from research_system.runtime.methodology.core import (
    OrchestratorMethodologySupport as CorePackageMethodologySupport,
)
from research_system.runtime.methodology.core import (
    OrchestratorMethodologySymbolSupport as CorePackageMethodologySymbolSupport,
)
from research_system.runtime.methodology.core import (
    OrchestratorMethodologyBuilder,
    OrchestratorMethodologyCompareSupport,
    OrchestratorMethodologyEntryFactory,
    OrchestratorMethodologySupport,
    OrchestratorMethodologySymbolSupport,
)
from research_system.runtime.methodology.snapshots import (
    OrchestratorMethodologySnapshotCandidateSupport as PackageSnapshotCandidateSupport,
    OrchestratorMethodologySnapshotFields as PackageSnapshotFields,
    OrchestratorMethodologySnapshotGrouping as PackageSnapshotGrouping,
    OrchestratorMethodologySnapshotSupport as PackageSnapshotSupport,
)
from research_system.runtime.methodology.snapshots import (
    OrchestratorMethodologySnapshotCandidateSupport,
    OrchestratorMethodologySnapshotFields,
    OrchestratorMethodologySnapshotGrouping,
    OrchestratorMethodologySnapshotSupport,
)
from research_system.runtime.platform.checks import run_checks
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout
from research_system.runtime.platform.skill_runtime import LocalSkillBackend, SkillRuntime
from research_system.runtime.workspace.layout import ensure_workspace_layout as layout_ensure_workspace_layout
from research_system.runtime.workspace.layout import required_workspace_directories


class RuntimeRemainingPackagesRuntimeTests(unittest.TestCase):
    def test_platform_package_exports(self) -> None:
        self.assertIs(platform.Settings, Settings)
        self.assertIs(platform.ensure_workspace_layout, ensure_workspace_layout)
        self.assertIs(platform.run_checks, run_checks)
        self.assertIs(platform.SkillRuntime, SkillRuntime)
        self.assertIs(platform.LocalSkillBackend, LocalSkillBackend)

    def test_config_package_exports(self) -> None:
        self.assertIs(config.build_settings_from_env, build_settings_from_env)
        self.assertIs(config.load_settings_env_values, load_settings_env_values)
        self.assertIs(config.load_settings_yaml_configs, load_settings_yaml_configs)

    def test_workspace_package_exports(self) -> None:
        self.assertIs(workspace.ensure_workspace_layout, layout_ensure_workspace_layout)
        self.assertIs(workspace.required_workspace_directories, required_workspace_directories)

    def test_execution_package_exports(self) -> None:
        self.assertIs(execution.OrchestratorActionRunner, OrchestratorActionRunner)
        self.assertIs(execution.OrchestratorExecutionControlSupport, OrchestratorExecutionControlSupport)
        self.assertIs(execution.OrchestratorExecutionLifecycleSupport, OrchestratorExecutionLifecycleSupport)
        self.assertIs(execution.OrchestratorExecutionResearchFacade, OrchestratorExecutionResearchFacade)
        self.assertIs(execution.OrchestratorExecutionRuntimeSupport, OrchestratorExecutionRuntimeSupport)
        self.assertIs(execution.OrchestratorExecutionSupport, OrchestratorExecutionSupport)
        self.assertIs(execution.OrchestratorExecutionTraceSupport, OrchestratorExecutionTraceSupport)
        self.assertIs(execution.OrchestratorPlanExecutor, OrchestratorPlanExecutor)
        self.assertIs(execution.OrchestratorRuntimeControlPlaneController, OrchestratorRuntimeControlPlaneController)
        self.assertIs(execution.OrchestratorRuntimeProtocolController, OrchestratorRuntimeProtocolController)
        self.assertIs(
            execution.OrchestratorRuntimeSubagentControlService,
            OrchestratorRuntimeSubagentControlService,
        )
        self.assertIs(execution.core, execution_core)
        self.assertIs(execution_core.OrchestratorActionRunner, OrchestratorActionRunner)
        self.assertIs(execution_core.OrchestratorExecutionControlSupport, OrchestratorExecutionControlSupport)
        self.assertIs(execution_core.OrchestratorExecutionLifecycleSupport, OrchestratorExecutionLifecycleSupport)
        self.assertIs(execution_core.OrchestratorExecutionResearchFacade, OrchestratorExecutionResearchFacade)
        self.assertIs(execution_core.OrchestratorExecutionRuntimeSupport, OrchestratorExecutionRuntimeSupport)
        self.assertIs(execution_core.OrchestratorExecutionSupport, OrchestratorExecutionSupport)
        self.assertIs(execution_core.OrchestratorExecutionTraceSupport, OrchestratorExecutionTraceSupport)
        self.assertIs(execution_core.OrchestratorPlanExecutor, OrchestratorPlanExecutor)
        self.assertIs(
            execution_core.OrchestratorRuntimeControlPlaneController,
            OrchestratorRuntimeControlPlaneController,
        )
        self.assertIs(
            execution_core.OrchestratorRuntimeProtocolController,
            OrchestratorRuntimeProtocolController,
        )
        self.assertIs(
            execution_core.OrchestratorRuntimeSubagentControlService,
            OrchestratorRuntimeSubagentControlService,
        )
        self.assertIs(execution.handlers.OrchestratorAgentHandlers, OrchestratorAgentHandlers)

    def test_methodology_package_exports(self) -> None:
        self.assertIs(methodology.OrchestratorMethodologyBankHighlightSupport, OrchestratorMethodologyBankHighlightSupport)
        self.assertIs(methodology.OrchestratorMethodologyBankMetricSupport, OrchestratorMethodologyBankMetricSupport)
        self.assertIs(methodology.OrchestratorMethodologyBankScorecardSupport, OrchestratorMethodologyBankScorecardSupport)
        self.assertIs(OrchestratorMethodologyBankHighlightSupport, PackageBankHighlightSupport)
        self.assertIs(OrchestratorMethodologyBankMetricSupport, PackageBankMetricSupport)
        self.assertIs(OrchestratorMethodologyBankScorecardSupport, PackageBankScorecardSupport)
        self.assertIs(methodology.OrchestratorMethodologyBuilder, OrchestratorMethodologyBuilder)
        self.assertIs(methodology.OrchestratorMethodologyCompareSupport, OrchestratorMethodologyCompareSupport)
        self.assertIs(methodology.OrchestratorMethodologyEntryFactory, OrchestratorMethodologyEntryFactory)
        self.assertIs(OrchestratorMethodologyBuilder, CorePackageMethodologyBuilder)
        self.assertIs(OrchestratorMethodologyCompareSupport, CorePackageMethodologyCompareSupport)
        self.assertIs(OrchestratorMethodologyEntryFactory, CorePackageMethodologyEntryFactory)
        self.assertIs(
            methodology.OrchestratorMethodologySnapshotCandidateSupport,
            OrchestratorMethodologySnapshotCandidateSupport,
        )
        self.assertIs(methodology.OrchestratorMethodologySnapshotFields, OrchestratorMethodologySnapshotFields)
        self.assertIs(methodology.OrchestratorMethodologySnapshotGrouping, OrchestratorMethodologySnapshotGrouping)
        self.assertIs(methodology.OrchestratorMethodologySnapshotSupport, OrchestratorMethodologySnapshotSupport)
        self.assertIs(OrchestratorMethodologySnapshotCandidateSupport, PackageSnapshotCandidateSupport)
        self.assertIs(OrchestratorMethodologySnapshotFields, PackageSnapshotFields)
        self.assertIs(OrchestratorMethodologySnapshotGrouping, PackageSnapshotGrouping)
        self.assertIs(OrchestratorMethodologySnapshotSupport, PackageSnapshotSupport)
        self.assertIs(methodology.OrchestratorMethodologySupport, OrchestratorMethodologySupport)
        self.assertIs(methodology.OrchestratorMethodologySymbolSupport, OrchestratorMethodologySymbolSupport)
        self.assertIs(OrchestratorMethodologySupport, CorePackageMethodologySupport)
        self.assertIs(OrchestratorMethodologySymbolSupport, CorePackageMethodologySymbolSupport)


if __name__ == "__main__":
    unittest.main()
