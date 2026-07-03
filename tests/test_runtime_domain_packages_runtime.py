from __future__ import annotations

import unittest

from research_system.runtime import history as history_package
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
from research_system.runtime.history.run_trace_row_builder import RunTraceRowBuilder
from research_system.runtime.history.session_history import SessionHistory
from research_system.runtime.history.step_builder import SessionHistoryStepBuilder
from research_system.runtime.history.trace_rebuilder import SessionHistoryTraceRebuilder
from research_system.runtime.planning.action_factory import PlannerActionFactory
from research_system.runtime.planning.context_hints import PlannerContextHints
from research_system.runtime.planning.dynamic_plan_support import PlannerDynamicPlanSupport
from research_system.runtime.planning.entry_decision_support import PlannerEntryDecisionSupport
from research_system.runtime.planning.models import EntryPlanDecision, EntryPlanPayload
from research_system.runtime.planning.planner import RuntimePlanner
from research_system.runtime.planning.static_plan_support import PlannerStaticPlanSupport
from research_system.runtime.proposals import management as proposals_management_package
from research_system.runtime.proposals.admin_support import OrchestratorProposalAdminSupport
from research_system.runtime.proposals.approval_policy import OrchestratorProposalApprovalPolicy
from research_system.runtime.proposals.auto_approval_support import OrchestratorProposalAutoApprovalSupport
from research_system.runtime.proposals.collect_support import OrchestratorProposalCollectSupport
from research_system.runtime.proposals.entry_factory import OrchestratorProposalEntryFactory
from research_system.runtime.proposals.memory_gate_support import OrchestratorProposalMemoryGateSupport
from research_system.runtime.proposals.proposal_store import ProposalStore
from research_system.runtime.proposals.review_pipeline import OrchestratorProposalReviewPipeline
from research_system.runtime.proposals.support import OrchestratorProposalSupport
from research_system.runtime.proposals.target_support import OrchestratorProposalTargetSupport
from research_system.runtime.proposals.transition_support import OrchestratorProposalTransitionSupport
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


if __name__ == "__main__":
    unittest.main()
