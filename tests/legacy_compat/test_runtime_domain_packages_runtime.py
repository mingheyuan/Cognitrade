from __future__ import annotations

import unittest

from research_system.runtime import history as history_package
from research_system.runtime import orchestration as orchestration_package
from research_system.runtime import planning as planning_package
from research_system.runtime import proposals as proposals_package
from research_system.runtime import supervision as supervision_package
from research_system.runtime.history.session import RunTraceRowBuilder as SessionPackageRunTraceRowBuilder
from research_system.runtime.history.session import SessionHistory as SessionPackageSessionHistory
from research_system.runtime.history.session import SessionHistoryStepBuilder as SessionPackageSessionHistoryStepBuilder
from research_system.runtime.history.session import (
    SessionHistoryTraceRebuilder as SessionPackageSessionHistoryTraceRebuilder,
)
from research_system.runtime.history.session.steps import (
    SessionHistoryStepBuilder as SessionStepsPackageSessionHistoryStepBuilder,
)
from research_system.runtime.history.session.trace import (
    RunTraceRowBuilder as SessionTracePackageRunTraceRowBuilder,
)
from research_system.runtime.history.session.trace import (
    SessionHistoryTraceRebuilder as SessionTracePackageSessionHistoryTraceRebuilder,
)
from research_system.runtime.history import (
    RunTraceRowBuilder,
    SessionHistory,
    SessionHistoryStepBuilder,
    SessionHistoryTraceRebuilder,
)
from research_system.runtime.orchestration import (
    OrchestratorSubagentDiscoveryAdminSupport,
    SubagentDiscoveryApprovalPolicyService,
    SubagentDiscoveryActivationPolicyService,
    SUPPORTED_DISCOVERY_CANDIDATE_SOURCE_TYPES,
    SUPPORTED_RUNTIME_MATERIALIZATION_TYPES,
    SUPPORTED_RUNTIME_REGISTRATION_TYPES,
    SUPPORTED_RUNTIME_REGISTRATION_BUILDER_TYPES,
    SUPPORTED_RUNTIME_TEMPLATE_TYPES,
    SUPPORTED_RUNTIME_SOURCE_RESOLUTION_TYPES,
    SUPPORTED_RUNTIME_VALIDATION_TYPES,
    SubagentDiscoveryStore,
    SubagentDiscoveryCandidateSupport,
    SubagentRuntimeMaterializationSupport,
    SubagentRuntimePackagingSupport,
    SubagentRuntimeRegistrationBuilderSupport,
    SubagentRuntimeTemplateSupport,
    SubagentRuntimeSourceResolutionSupport,
    SubagentRuntimeValidationSupport,
)
from research_system.runtime.orchestration.subagent_runtime_materialization import (
    SUPPORTED_RUNTIME_MATERIALIZATION_TYPES as MaterializationTypes,
)
from research_system.runtime.orchestration.subagent_runtime_materialization import (
    SubagentRuntimeMaterializationSupport as MaterializationSupportImpl,
)
from research_system.runtime.orchestration.subagent_runtime_packaging import (
    SUPPORTED_RUNTIME_REGISTRATION_TYPES as PackagingRuntimeRegistrationTypes,
)
from research_system.runtime.orchestration.subagent_runtime_packaging import (
    SubagentRuntimePackagingSupport as PackagingSupportImpl,
)
from research_system.runtime.orchestration.subagent_runtime_registration_builder import (
    SUPPORTED_RUNTIME_REGISTRATION_BUILDER_TYPES as RegistrationBuilderTypes,
)
from research_system.runtime.orchestration.subagent_runtime_registration_builder import (
    SubagentRuntimeRegistrationBuilderSupport as RegistrationBuilderSupportImpl,
)
from research_system.runtime.orchestration.subagent_runtime_templates import (
    SUPPORTED_RUNTIME_TEMPLATE_TYPES as TemplateTypes,
)
from research_system.runtime.orchestration.subagent_runtime_templates import (
    SubagentRuntimeTemplateSupport as TemplateSupportImpl,
)
from research_system.runtime.orchestration.subagent_runtime_source_resolution import (
    SUPPORTED_RUNTIME_SOURCE_RESOLUTION_TYPES as SourceResolutionTypes,
)
from research_system.runtime.orchestration.subagent_runtime_source_resolution import (
    SubagentRuntimeSourceResolutionSupport as SourceResolutionSupportImpl,
)
from research_system.runtime.orchestration.subagent_runtime_validation import (
    SUPPORTED_RUNTIME_VALIDATION_TYPES as ValidationTypes,
)
from research_system.runtime.orchestration.subagent_runtime_validation import (
    SubagentRuntimeValidationSupport as ValidationSupportImpl,
)
from research_system.runtime.orchestration.subagent_discovery_candidate_sources import (
    SUPPORTED_DISCOVERY_CANDIDATE_SOURCE_TYPES as DiscoveryCandidateSourceTypes,
)
from research_system.runtime.orchestration.subagent_discovery_candidate_sources import (
    SubagentDiscoveryCandidateSupport as DiscoveryCandidateSupportImpl,
)
from research_system.runtime.orchestration._subagent_discovery_approval_policy import (
    SubagentDiscoveryApprovalPolicyService as DiscoveryApprovalPolicySupportImpl,
)
from research_system.runtime.orchestration._subagent_discovery_activation_policy import (
    SubagentDiscoveryActivationPolicyService as DiscoveryActivationPolicySupportImpl,
)
from research_system.runtime.planning.action_factory import PlannerActionFactory
from research_system.runtime.planning.context_hints import PlannerContextHints
from research_system.runtime.planning.dynamic_plan_support import PlannerDynamicPlanSupport
from research_system.runtime.planning.entry_decision_support import PlannerEntryDecisionSupport
from research_system.runtime.planning.models import EntryPlanDecision, EntryPlanPayload
from research_system.runtime.planning.planner import RuntimePlanner
from research_system.runtime.planning.static_plan_support import PlannerStaticPlanSupport
from research_system.runtime.proposals import management as proposals_management_package
from research_system.runtime.proposals.management import OrchestratorProposalAdminSupport, ProposalStore
from research_system.runtime.proposals.policy import (
    OrchestratorProposalApprovalPolicy,
    OrchestratorProposalAutoApprovalSupport,
    OrchestratorProposalMemoryGateSupport,
    OrchestratorProposalTargetSupport,
)
from research_system.runtime.proposals.workflow import (
    OrchestratorProposalCollectSupport,
    OrchestratorProposalEntryFactory,
    OrchestratorProposalReviewPipeline,
    OrchestratorProposalSupport,
    OrchestratorProposalTransitionSupport,
)
from research_system.runtime.supervision.blackboard_models import (
    ReviewThesisView,
    SectorResearchView,
    StockCheckView,
    StockScreenView,
)
from research_system.runtime.supervision.blackboard_review_support import SupervisorBlackboardReviewSupport
from research_system.runtime.supervision.blackboard_screen_support import SupervisorBlackboardScreenSupport
from research_system.runtime.supervision.blackboard_sector_support import SupervisorBlackboardSectorSupport
from research_system.runtime.supervision.blackboard_snapshot_support import SupervisorBlackboardSnapshotSupport
from research_system.runtime.supervision.blackboard_source_support import SupervisorBlackboardSourceSupport
from research_system.runtime.supervision.blackboard_stock_check_support import SupervisorBlackboardStockCheckSupport
from research_system.runtime.supervision.blackboard_view import SupervisorBlackboardView
from research_system.runtime.supervision.decision_factory import SupervisorDecisionFactory
from research_system.runtime.supervision.review_thesis_support import SupervisorReviewThesisSupport
from research_system.runtime.supervision.sector_research_support import SupervisorSectorResearchSupport
from research_system.runtime.supervision.stock_check_support import SupervisorStockCheckSupport
from research_system.runtime.supervision.stock_screen_support import SupervisorStockScreenSupport
from research_system.runtime.supervision.supervisor import RuntimeSupervisor


class RuntimeDomainPackagesRuntimeTests(unittest.TestCase):
    def test_history_package_exports(self) -> None:
        self.assertIs(history_package.RunTraceRowBuilder, RunTraceRowBuilder)
        self.assertIs(history_package.SessionHistory, SessionHistory)
        self.assertIs(history_package.SessionHistoryStepBuilder, SessionHistoryStepBuilder)
        self.assertIs(history_package.SessionHistoryTraceRebuilder, SessionHistoryTraceRebuilder)
        self.assertIs(RunTraceRowBuilder, SessionPackageRunTraceRowBuilder)
        self.assertIs(SessionHistory, SessionPackageSessionHistory)
        self.assertIs(SessionHistoryStepBuilder, SessionPackageSessionHistoryStepBuilder)
        self.assertIs(SessionHistoryTraceRebuilder, SessionPackageSessionHistoryTraceRebuilder)
        self.assertIs(SessionHistoryStepBuilder, SessionStepsPackageSessionHistoryStepBuilder)
        self.assertIs(RunTraceRowBuilder, SessionTracePackageRunTraceRowBuilder)
        self.assertIs(SessionHistoryTraceRebuilder, SessionTracePackageSessionHistoryTraceRebuilder)

    def test_planning_package_exports(self) -> None:
        self.assertIs(planning_package.EntryPlanDecision, EntryPlanDecision)
        self.assertIs(planning_package.EntryPlanPayload, EntryPlanPayload)
        self.assertIs(planning_package.PlannerActionFactory, PlannerActionFactory)
        self.assertIs(planning_package.PlannerContextHints, PlannerContextHints)
        self.assertIs(planning_package.PlannerDynamicPlanSupport, PlannerDynamicPlanSupport)
        self.assertIs(planning_package.PlannerEntryDecisionSupport, PlannerEntryDecisionSupport)
        self.assertIs(planning_package.PlannerStaticPlanSupport, PlannerStaticPlanSupport)
        self.assertIs(planning_package.RuntimePlanner, RuntimePlanner)

    def test_proposals_package_exports(self) -> None:
        self.assertIs(proposals_package.OrchestratorProposalAdminSupport, OrchestratorProposalAdminSupport)
        self.assertIs(proposals_package.OrchestratorProposalApprovalPolicy, OrchestratorProposalApprovalPolicy)
        self.assertIs(proposals_package.OrchestratorProposalAutoApprovalSupport, OrchestratorProposalAutoApprovalSupport)
        self.assertIs(proposals_package.OrchestratorProposalCollectSupport, OrchestratorProposalCollectSupport)
        self.assertIs(proposals_package.OrchestratorProposalEntryFactory, OrchestratorProposalEntryFactory)
        self.assertIs(proposals_package.OrchestratorProposalMemoryGateSupport, OrchestratorProposalMemoryGateSupport)
        self.assertIs(proposals_package.OrchestratorProposalReviewPipeline, OrchestratorProposalReviewPipeline)
        self.assertIs(proposals_package.OrchestratorProposalSupport, OrchestratorProposalSupport)
        self.assertIs(proposals_package.OrchestratorProposalTargetSupport, OrchestratorProposalTargetSupport)
        self.assertIs(proposals_package.OrchestratorProposalTransitionSupport, OrchestratorProposalTransitionSupport)
        self.assertIs(proposals_package.ProposalStore, ProposalStore)
        self.assertIs(proposals_management_package.OrchestratorProposalAdminSupport, OrchestratorProposalAdminSupport)
        self.assertIs(proposals_management_package.ProposalStore, ProposalStore)

    def test_supervision_package_exports(self) -> None:
        self.assertIs(supervision_package.ReviewThesisView, ReviewThesisView)
        self.assertIs(supervision_package.RuntimeSupervisor, RuntimeSupervisor)
        self.assertIs(supervision_package.SectorResearchView, SectorResearchView)
        self.assertIs(supervision_package.StockCheckView, StockCheckView)
        self.assertIs(supervision_package.StockScreenView, StockScreenView)
        self.assertIs(supervision_package.SupervisorBlackboardReviewSupport, SupervisorBlackboardReviewSupport)
        self.assertIs(supervision_package.SupervisorBlackboardScreenSupport, SupervisorBlackboardScreenSupport)
        self.assertIs(supervision_package.SupervisorBlackboardSectorSupport, SupervisorBlackboardSectorSupport)
        self.assertIs(supervision_package.SupervisorBlackboardSnapshotSupport, SupervisorBlackboardSnapshotSupport)
        self.assertIs(supervision_package.SupervisorBlackboardSourceSupport, SupervisorBlackboardSourceSupport)
        self.assertIs(supervision_package.SupervisorBlackboardStockCheckSupport, SupervisorBlackboardStockCheckSupport)
        self.assertIs(supervision_package.SupervisorBlackboardView, SupervisorBlackboardView)
        self.assertIs(supervision_package.SupervisorDecisionFactory, SupervisorDecisionFactory)
        self.assertIs(supervision_package.SupervisorReviewThesisSupport, SupervisorReviewThesisSupport)
        self.assertIs(supervision_package.SupervisorSectorResearchSupport, SupervisorSectorResearchSupport)
        self.assertIs(supervision_package.SupervisorStockCheckSupport, SupervisorStockCheckSupport)
        self.assertIs(supervision_package.SupervisorStockScreenSupport, SupervisorStockScreenSupport)

    def test_orchestration_package_exports_discovery_components(self) -> None:
        self.assertIs(
            orchestration_package.OrchestratorSubagentDiscoveryAdminSupport,
            OrchestratorSubagentDiscoveryAdminSupport,
        )
        self.assertIs(
            orchestration_package.SubagentDiscoveryApprovalPolicyService,
            SubagentDiscoveryApprovalPolicyService,
        )
        self.assertIs(
            orchestration_package.SubagentDiscoveryActivationPolicyService,
            SubagentDiscoveryActivationPolicyService,
        )
        self.assertIs(
            orchestration_package.SubagentRuntimeMaterializationSupport,
            SubagentRuntimeMaterializationSupport,
        )
        self.assertIs(
            orchestration_package.SubagentRuntimePackagingSupport,
            SubagentRuntimePackagingSupport,
        )
        self.assertIs(
            orchestration_package.SubagentRuntimeRegistrationBuilderSupport,
            SubagentRuntimeRegistrationBuilderSupport,
        )
        self.assertIs(
            orchestration_package.SubagentRuntimeTemplateSupport,
            SubagentRuntimeTemplateSupport,
        )
        self.assertIs(
            orchestration_package.SubagentRuntimeSourceResolutionSupport,
            SubagentRuntimeSourceResolutionSupport,
        )
        self.assertIs(
            orchestration_package.SubagentRuntimeValidationSupport,
            SubagentRuntimeValidationSupport,
        )
        self.assertIs(
            orchestration_package.SubagentDiscoveryStore,
            SubagentDiscoveryStore,
        )
        self.assertIs(
            SubagentDiscoveryApprovalPolicyService,
            DiscoveryApprovalPolicySupportImpl,
        )
        self.assertIs(
            SubagentDiscoveryActivationPolicyService,
            DiscoveryActivationPolicySupportImpl,
        )
        self.assertIs(
            orchestration_package.SubagentDiscoveryCandidateSupport,
            SubagentDiscoveryCandidateSupport,
        )
        self.assertIs(
            SubagentRuntimeMaterializationSupport,
            MaterializationSupportImpl,
        )
        self.assertIs(
            SubagentRuntimePackagingSupport,
            PackagingSupportImpl,
        )
        self.assertIs(
            SubagentRuntimeRegistrationBuilderSupport,
            RegistrationBuilderSupportImpl,
        )
        self.assertIs(
            SubagentRuntimeTemplateSupport,
            TemplateSupportImpl,
        )
        self.assertIs(
            orchestration_package.SUPPORTED_RUNTIME_REGISTRATION_TYPES,
            SUPPORTED_RUNTIME_REGISTRATION_TYPES,
        )
        self.assertIs(
            SUPPORTED_RUNTIME_REGISTRATION_TYPES,
            PackagingRuntimeRegistrationTypes,
        )
        self.assertIs(
            orchestration_package.SUPPORTED_RUNTIME_MATERIALIZATION_TYPES,
            SUPPORTED_RUNTIME_MATERIALIZATION_TYPES,
        )
        self.assertIs(
            SUPPORTED_RUNTIME_MATERIALIZATION_TYPES,
            MaterializationTypes,
        )
        self.assertIs(
            orchestration_package.SUPPORTED_RUNTIME_REGISTRATION_BUILDER_TYPES,
            SUPPORTED_RUNTIME_REGISTRATION_BUILDER_TYPES,
        )
        self.assertIs(
            SUPPORTED_RUNTIME_REGISTRATION_BUILDER_TYPES,
            RegistrationBuilderTypes,
        )
        self.assertIs(
            orchestration_package.SUPPORTED_RUNTIME_TEMPLATE_TYPES,
            SUPPORTED_RUNTIME_TEMPLATE_TYPES,
        )
        self.assertIs(
            SUPPORTED_RUNTIME_TEMPLATE_TYPES,
            TemplateTypes,
        )
        self.assertIs(
            SubagentRuntimeSourceResolutionSupport,
            SourceResolutionSupportImpl,
        )
        self.assertIs(
            orchestration_package.SUPPORTED_RUNTIME_SOURCE_RESOLUTION_TYPES,
            SUPPORTED_RUNTIME_SOURCE_RESOLUTION_TYPES,
        )
        self.assertIs(
            SUPPORTED_RUNTIME_SOURCE_RESOLUTION_TYPES,
            SourceResolutionTypes,
        )
        self.assertIs(
            SubagentRuntimeValidationSupport,
            ValidationSupportImpl,
        )
        self.assertIs(
            SubagentDiscoveryCandidateSupport,
            DiscoveryCandidateSupportImpl,
        )
        self.assertIs(
            orchestration_package.SUPPORTED_DISCOVERY_CANDIDATE_SOURCE_TYPES,
            SUPPORTED_DISCOVERY_CANDIDATE_SOURCE_TYPES,
        )
        self.assertIs(
            SUPPORTED_DISCOVERY_CANDIDATE_SOURCE_TYPES,
            DiscoveryCandidateSourceTypes,
        )
        self.assertIs(
            orchestration_package.SUPPORTED_RUNTIME_VALIDATION_TYPES,
            SUPPORTED_RUNTIME_VALIDATION_TYPES,
        )
        self.assertIs(
            SUPPORTED_RUNTIME_VALIDATION_TYPES,
            ValidationTypes,
        )

    def test_runtime_packaging_support_reports_complete_handler_registry(self) -> None:
        support = SubagentRuntimePackagingSupport()
        self.assertEqual(
            support.registration_types(),
            SUPPORTED_RUNTIME_REGISTRATION_TYPES,
        )

    def test_runtime_materialization_support_reports_complete_handler_registry(self) -> None:
        support = SubagentRuntimeMaterializationSupport()
        self.assertEqual(
            support.materialization_types(),
            SUPPORTED_RUNTIME_MATERIALIZATION_TYPES,
        )

    def test_runtime_registration_builder_support_reports_complete_handler_registry(self) -> None:
        support = SubagentRuntimeRegistrationBuilderSupport()
        self.assertEqual(
            support.builder_types(),
            SUPPORTED_RUNTIME_REGISTRATION_BUILDER_TYPES,
        )

    def test_runtime_template_support_reports_complete_handler_registry(self) -> None:
        support = SubagentRuntimeTemplateSupport()
        self.assertEqual(
            support.template_types(),
            SUPPORTED_RUNTIME_TEMPLATE_TYPES,
        )

    def test_runtime_source_resolution_support_reports_complete_handler_registry(self) -> None:
        support = SubagentRuntimeSourceResolutionSupport()
        self.assertEqual(
            support.resolution_types(),
            SUPPORTED_RUNTIME_SOURCE_RESOLUTION_TYPES,
        )

    def test_runtime_validation_support_reports_complete_handler_registry(self) -> None:
        support = SubagentRuntimeValidationSupport()
        self.assertEqual(
            support.validation_types(),
            SUPPORTED_RUNTIME_VALIDATION_TYPES,
        )

    def test_discovery_candidate_support_reports_complete_handler_registry(self) -> None:
        support = SubagentDiscoveryCandidateSupport()
        self.assertEqual(
            support.source_types(),
            SUPPORTED_DISCOVERY_CANDIDATE_SOURCE_TYPES,
        )


if __name__ == "__main__":
    unittest.main()
