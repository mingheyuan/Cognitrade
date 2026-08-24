from __future__ import annotations

import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from research_system.strategies.contracts import (
    BacktestMode,
    StrategyDocumentV1,
    canonical_strategy_json,
    strategy_content_hash,
)
from research_system.strategies.database import StrategyDatabase
from research_system.strategies.errors import StrategyConflict, StrategyNotFound
from research_system.strategies.factors import (
    holding_stock_quality_score,
    point_in_time_available,
    weighted_bounds,
)
from research_system.strategies.models import DailyReportNode, DailyReportSet
from research_system.strategies.orchestration import ReportOrchestrationService
from research_system.strategies.service import OwnerScope, StrategyService
from research_system.strategies.signals import SignalEngineService, decide_signal_state


class StrategyContractTests(unittest.TestCase):
    def test_old_otc_contract_defaults_preserve_single_all_in_entry(self) -> None:
        payload = StrategyDocumentV1().model_dump(mode="json")
        for key in (
            "max_position_pct",
            "entry_tranche_pct",
            "max_entry_tranches",
            "minimum_entry_interval_days",
        ):
            payload["asset_execution"]["otc_fund"].pop(key)

        restored = StrategyDocumentV1.model_validate(payload)
        rules = restored.asset_execution.otc_fund
        self.assertEqual(rules.max_position_pct, 100.0)
        self.assertEqual(rules.entry_tranche_pct, 100.0)
        self.assertEqual(rules.max_entry_tranches, 1)
        self.assertEqual(rules.minimum_entry_interval_days, 0)

    def test_report_analysis_profile_is_versioned_and_selectable(self) -> None:
        hashes: set[str] = set()
        for profile in ("lite", "medium", "deep"):
            payload = StrategyDocumentV1().model_dump(mode="json")
            payload["report_inputs"]["research_profile"] = profile
            document = StrategyDocumentV1.model_validate(payload)
            self.assertEqual(document.report_inputs.research_profile, profile)
            hashes.add(strategy_content_hash(document))

        self.assertEqual(len(hashes), 3)

    def test_canonical_hash_is_order_independent_and_unknown_fields_are_rejected(self) -> None:
        document = StrategyDocumentV1()
        payload = document.model_dump(mode="json")
        reordered = {key: payload[key] for key in reversed(list(payload))}

        self.assertEqual(canonical_strategy_json(payload), canonical_strategy_json(reordered))
        self.assertEqual(strategy_content_hash(payload), strategy_content_hash(reordered))
        payload["arbitrary_formula"] = "score * 2"
        with self.assertRaises(ValidationError):
            StrategyDocumentV1.model_validate(payload)

    def test_missing_evidence_keeps_fixed_score_mass(self) -> None:
        bounds = weighted_bounds(
            {"present": 80.0, "missing": None},
            {"present": 0.4, "missing": 0.6},
        )
        self.assertAlmostEqual(bounds.lower, 32.0)
        self.assertAlmostEqual(bounds.upper, 92.0)
        self.assertAlmostEqual(bounds.coverage, 0.4)

        holding_bounds = holding_stock_quality_score(
            [
                {
                    "status": "completed",
                    "effective_weight_pct": 10.0,
                    "uzi_overall_score": 80.0,
                    "confidence": 1.0,
                }
                for _ in range(5)
            ],
            expected_count=10,
        )
        self.assertAlmostEqual(holding_bounds.lower, 40.0)
        self.assertAlmostEqual(holding_bounds.upper, 90.0)
        self.assertAlmostEqual(holding_bounds.coverage, 0.5)

    def test_signal_coverage_boundaries_and_point_in_time_boundary(self) -> None:
        document = StrategyDocumentV1()
        state, _ = decide_signal_state(
            lower=90,
            coverage=0.5999,
            confidence=1,
            disclosure_age_days=1,
            hard_reasons=[],
            document=document,
        )
        self.assertEqual(state.value, "BLOCKED")
        state, _ = decide_signal_state(
            lower=90,
            coverage=0.7999,
            confidence=1,
            disclosure_age_days=1,
            hard_reasons=[],
            document=document,
        )
        self.assertEqual(state.value, "WATCH")
        state, _ = decide_signal_state(
            lower=90,
            coverage=0.8,
            confidence=1,
            disclosure_age_days=1,
            hard_reasons=[],
            document=document,
        )
        self.assertEqual(state.value, "ALLOW_BUY")
        state, _ = decide_signal_state(
            lower=40,
            coverage=1,
            confidence=1,
            disclosure_age_days=1,
            hard_reasons=[],
            document=document,
        )
        self.assertEqual(state.value, "AVOID")

        self.assertFalse(
            point_in_time_available(
                as_of_date=date(2026, 8, 3),
                published_at=None,
                first_seen_at=None,
            )
        )
        self.assertTrue(
            point_in_time_available(
                as_of_date=date(2026, 8, 3),
                published_at="2026-08-03T08:00:00+08:00",
                first_seen_at=None,
            )
        )


class StrategyPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.database = StrategyDatabase("sqlite:///:memory:")
        self.service = StrategyService(
            self.database,
            workspace_root=Path(self.temporary.name),
            auto_bootstrap=True,
        )
        self.owner = OwnerScope("tenant-a", "user-a", "actor-a")

    def tearDown(self) -> None:
        self.service.close()
        self.temporary.cleanup()

    def _published_assignment(self) -> tuple[dict, dict, dict]:
        strategy = self.service.create_strategy(self.owner, name="日报策略")
        version = self.service.publish(self.owner, strategy["id"])
        assignment = self.service.create_assignment(
            self.owner,
            strategy_id=strategy["id"],
            fund_code="110011",
            fund_name="测试基金",
            mode=BacktestMode.FUND_ONLY,
        )
        return strategy, version, assignment

    def test_owner_scope_optimistic_lock_and_published_delete_boundary(self) -> None:
        strategy = self.service.create_strategy(self.owner, name="可编辑策略")
        document = strategy["draft"]["document"]
        updated = self.service.update_draft(
            self.owner,
            strategy["id"],
            expected_revision=1,
            document=document,
            name="新名称",
        )
        self.assertEqual(updated["draft"]["draft_revision"], 2)
        with self.assertRaises(StrategyConflict):
            self.service.update_draft(
                self.owner,
                strategy["id"],
                expected_revision=1,
                document=document,
            )
        with self.assertRaises(StrategyNotFound):
            self.service.get_strategy(OwnerScope("tenant-a", "user-b", "actor-b"), strategy["id"])

        self.service.publish(self.owner, strategy["id"])
        with self.assertRaises(StrategyConflict):
            self.service.hard_delete(self.owner, strategy["id"])
        archived = self.service.archive(self.owner, strategy["id"])
        self.assertEqual(archived["lifecycle"], "archived")
        restored = self.service.restore(self.owner, strategy["id"])
        self.assertEqual(restored["lifecycle"], "published")

    def test_stock_signal_uses_parent_disclosure_and_carry_expires_from_root(self) -> None:
        _, version, assignment = self._published_assignment()
        report_date = date(2026, 8, 3)
        with self.database.session() as session:
            report = DailyReportSet(
                tenant_id=self.owner.tenant_id,
                user_id=self.owner.user_id,
                assignment_id=assignment["id"],
                fund_code="110011",
                fund_name="测试基金",
                as_of_date=report_date,
                effective_date=date(2026, 8, 4),
                pin_set_hash="a" * 64,
                revision=1,
                status="final",
                max_top_n=10,
                count_coverage=1.0,
                weight_coverage=1.0,
                coverage=1.0,
                source_hash="b" * 64,
                source_run_id="source-run",
                source_artifact_id="fund-artifact",
                data_gaps_json=[],
                finalized_at=datetime.now(UTC),
            )
            session.add(report)
            session.flush()
            session.add(
                DailyReportNode(
                    tenant_id=self.owner.tenant_id,
                    user_id=self.owner.user_id,
                    report_set_id=report.id,
                    node_type="fund_report",
                    subject_code="110011",
                    canonical_key="fund:110011:2026-08-03",
                    status="completed",
                    confidence=1.0,
                    data_gaps_json=[],
                    source_json={
                        "disclosure_age_days": 30,
                        "report_allowed": True,
                        "critical_count": 0,
                        "factor_values": {
                            "year_1_return": 45,
                            "since_inception_annualized": 30,
                            "benchmark_excess_year_1": 25,
                            "max_drawdown": 0,
                            "volatility": 0,
                            "fee": 0,
                            "return_consistency": 100,
                        },
                        "confidence": 1.0,
                    },
                )
            )
            for index in range(10):
                session.add(
                    DailyReportNode(
                        tenant_id=self.owner.tenant_id,
                        user_id=self.owner.user_id,
                        report_set_id=report.id,
                        node_type="holding_uzi",
                        subject_code=f"600{index:03d}",
                        subject_name=f"股票{index}",
                        rank=index + 1,
                        disclosed_weight_pct=10.0,
                        effective_weight_pct=10.0,
                        canonical_key=f"uzi:600{index:03d}:2026-08-03",
                        status="completed",
                        confidence=1.0,
                        data_gaps_json=[],
                        source_json={
                            "uzi_overall_score": 85.0,
                            "report_allowed": True,
                            "critical_count": 0,
                            "supported": True,
                        },
                    )
                )
            report_id = report.id

        engine = SignalEngineService(self.database)
        result = engine.recompute(
            self.owner,
            assignment_id=assignment["id"],
            report_set_id=report_id,
        )
        self.assertEqual(len(result["holding_signals"]), 10)
        self.assertNotEqual(result["holding_signals"][0]["state"], "BLOCKED")

        for carry_date in (date(2026, 8, 4), date(2026, 8, 5), date(2026, 8, 6)):
            carried = engine.carry_forward(
                self.owner,
                assignment_id=assignment["id"],
                as_of_date=carry_date,
            )
            self.assertEqual(carried["state"], result["fund_signal"]["state"])
            self.assertEqual(carried["expires_after"], date(2026, 8, 7))
        expired = engine.carry_forward(
            self.owner,
            assignment_id=assignment["id"],
            as_of_date=date(2026, 8, 7),
        )
        self.assertEqual(expired["state"], "BLOCKED")
        self.assertIsNone(expired["expires_after"])

    def test_late_revision_effective_date_moves_after_settlement_cutoff(self) -> None:
        strategy, version, assignment = self._published_assignment()
        version_detail = self.service.get_version(self.owner, strategy["id"], version["id"])
        version_row = SimpleNamespace(document_json=version_detail["document"])
        assignment_row = SimpleNamespace(schedule_json={"settlement_cutoff": "08:30"})
        shanghai = ZoneInfo("Asia/Shanghai")

        before = ReportOrchestrationService._report_effective_date(
            assignment=assignment_row,
            version=version_row,
            as_of=date(2026, 8, 3),
            revision=2,
            now=datetime(2026, 8, 4, 8, 0, tzinfo=shanghai),
        )
        after = ReportOrchestrationService._report_effective_date(
            assignment=assignment_row,
            version=version_row,
            as_of=date(2026, 8, 3),
            revision=2,
            now=datetime(2026, 8, 4, 9, 0, tzinfo=shanghai),
        )
        self.assertEqual(before, date(2026, 8, 4))
        self.assertEqual(after, date(2026, 8, 5))


if __name__ == "__main__":
    unittest.main()
