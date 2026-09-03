from __future__ import annotations

from threading import Event

from research_system.market_data.recovery import MarketDataRecoveryScheduler


class _Coordinator:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = list(result or [])
        self.error = error
        self.calls: list[bool] = []
        self.called = Event()

    def resume_pending(self, *, resume_subscription_jobs: bool):
        self.calls.append(resume_subscription_jobs)
        self.called.set()
        if self.error is not None:
            raise self.error
        return list(self.result)


def test_recovery_tick_scans_equity_and_fund_jobs() -> None:
    equity = _Coordinator(["equity-job"])
    fund = _Coordinator(["fund-job"])
    scheduler = MarketDataRecoveryScheduler(
        equity,
        fund,
        resume_subscription_jobs=False,
    )

    assert scheduler.tick() == {
        "equity": ["equity-job"],
        "fund": ["fund-job"],
    }
    assert equity.calls == [False]
    assert fund.calls == [False]


def test_recovery_tick_isolates_one_coordinator_failure() -> None:
    equity = _Coordinator(error=RuntimeError("database unavailable"))
    fund = _Coordinator(["fund-job"])
    scheduler = MarketDataRecoveryScheduler(
        equity,
        fund,
        resume_subscription_jobs=True,
    )

    assert scheduler.tick() == {"equity": [], "fund": ["fund-job"]}
    assert equity.calls == [True]
    assert fund.calls == [True]


def test_recovery_scheduler_repeats_until_stopped() -> None:
    equity = _Coordinator()
    fund = _Coordinator()
    scheduler = MarketDataRecoveryScheduler(
        equity,
        fund,
        resume_subscription_jobs=False,
        interval_seconds=0.05,
    )
    scheduler.start()
    try:
        assert equity.called.wait(timeout=1)
        assert fund.called.wait(timeout=1)
    finally:
        scheduler.stop()

    assert len(equity.calls) >= 1
    assert len(fund.calls) >= 1
