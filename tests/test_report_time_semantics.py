"""Historical report replay must not inherit live late-publication timing."""

from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from research_system.strategies.contracts import BacktestMode, StrategyDocumentV1, StrategyDocumentV2
from research_system.strategies.database import StrategyDatabase
from research_system.strategies.models import DailyReportNode, DailyReportSet, StrategyAssignment, StrategyVersion
from research_system.strategies.orchestration import ReportOrchestrationService as Reports
from research_system.strategies.service import OwnerScope, StrategyService


@pytest.mark.parametrize("revision", [1, 2, 10])
def test_retrospective_revision_keeps_next_trading_day(revision):
    actual = Reports._report_effective_date(
        assignment=SimpleNamespace(schedule_json={}),
        version=SimpleNamespace(document_json=StrategyDocumentV2().model_dump(mode="json")),
        as_of=date(2026, 9, 2), revision=revision, now=datetime(2026, 9, 17, tzinfo=UTC),
        report_time_mode="historical_replay",
    )
    assert actual == date(2026, 9, 3)


@pytest.mark.parametrize("document", [StrategyDocumentV1(), StrategyDocumentV2()])
def test_live_revisions_keep_settlement_cutoff_and_separate_cache(document):
    actual = Reports._report_effective_date(
        assignment=SimpleNamespace(schedule_json={}),
        version=SimpleNamespace(document_json=document.model_dump(mode="json")),
        as_of=date(2026, 9, 2), revision=2, now=datetime(2026, 9, 3, 2, tzinfo=UTC),
    )
    assert actual == date(2026, 9, 4)
    assert Reports._pin_hash(document) == Reports._pin_hash(document, report_time_mode="live")
    assert Reports._pin_hash(document) != Reports._pin_hash(document, report_time_mode="historical_replay")


@pytest.fixture
def services(tmp_path):
    db = StrategyDatabase(f"sqlite:///{tmp_path / 'test.sqlite3'}")
    strategies = StrategyService(db, workspace_root=tmp_path, auto_bootstrap=True)
    reports = Reports(db, run_service=SimpleNamespace(history=lambda **_: []),
                      holding_report_service=SimpleNamespace(), workspace_root=tmp_path, start_scheduler=False)
    reports._submit = Mock()  # No workers, LLM or network in these regressions.
    owner = OwnerScope("tenant", "user", "actor")
    yield db, strategies, reports, owner
    reports.close()
    strategies.close()
    db.engine.dispose()


def assignment(strategies, owner, document):
    strategy = strategies.create_strategy(owner, name="time-test", document=document.model_dump(mode="json"))
    version = strategies.publish(owner, strategy["id"])
    assigned = strategies.create_assignment(owner, strategy_id=strategy["id"], fund_code="020464", fund_name="测试基金",
                                            mode=BacktestMode.HOLDINGS_PORTFOLIO)
    return assigned, version


def test_job_pins_per_day_intent_and_persists_revision_dates(services):
    db, strategies, reports, owner = services
    assigned, published = assignment(strategies, owner, StrategyDocumentV2())
    with patch("research_system.strategies.orchestration.datetime", wraps=datetime) as clock:
        clock.now.return_value = datetime(2026, 9, 3, 8, tzinfo=UTC)
        job = reports.create_job(owner, assignment_id=assigned["id"], start_date=date(2026, 9, 2),
                                 end_date=date(2026, 9, 3), manual=True)
    assert job["request"]["report_time_modes"] == {"2026-09-02": "historical_replay", "2026-09-03": "live"}
    with db.session() as session:
        assigned_row = session.get(StrategyAssignment, assigned["id"])
        version = session.get(StrategyVersion, published["id"])
    kwargs = dict(assignment=assigned_row, version=version, as_of=date(2026, 9, 2),
                  pin_hash=Reports._pin_hash(StrategyDocumentV2(), report_time_mode="historical_replay"),
                  source_run_id=None, source_artifact_id=None, fund_payload={"fund_code": "020464"},
                  top_n=10, initial_gaps=[], report_time_mode="historical_replay")
    first = reports._begin_report_set(owner, **kwargs)
    second = reports._begin_report_set(owner, **kwargs)
    with db.session() as session:
        assert session.get(DailyReportSet, first).revision == 1
        revised = session.get(DailyReportSet, second)
        assert revised.revision == 2 and revised.effective_date == date(2026, 9, 3)
        nodes = session.query(DailyReportNode).filter_by(report_set_id=second).all()
        assert nodes[0].source_json["report_time_mode"] == "historical_replay"

    # Use the pinned request at a later date; don't reclassify today's live
    # intent as history simply because execution was delayed or resumed.
    with patch.object(reports, "_create_blocked_report", return_value=second) as begin, \
         patch.object(reports, "_recompute_or_carry"):
        reports._process_date(job_id=job["id"], request=job["request"], as_of=date(2026, 9, 3), previous_batch_id=None)
    assert begin.call_args.kwargs["report_time_mode"] == "live"


def test_scheduled_backfill_is_not_silently_reclassified_as_replay(services):
    _, strategies, reports, owner = services
    assigned, _ = assignment(strategies, owner, StrategyDocumentV2())
    job = reports.create_job(owner, assignment_id=assigned["id"], start_date=date(2026, 9, 2),
                             end_date=date(2026, 9, 2), manual=False)
    assert job["request"]["report_time_modes"] == {"2026-09-02": "live"}


def test_historical_request_cannot_reuse_an_idempotent_live_job(services):
    _, strategies, reports, owner = services
    assigned, _ = assignment(strategies, owner, StrategyDocumentV2())
    jobs = []
    for day in (2, 3):
        with patch("research_system.strategies.orchestration.datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 9, day, 8, tzinfo=UTC)
            jobs.append(reports.create_job(owner, assignment_id=assigned["id"], start_date=date(2026, 9, 2),
                                           end_date=date(2026, 9, 2), manual=True))
    assert jobs[0]["id"] != jobs[1]["id"]
    assert jobs[0]["request"]["report_pin_set_hashes"] != jobs[1]["request"]["report_pin_set_hashes"]
