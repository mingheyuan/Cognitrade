from __future__ import annotations

"""Periodic recovery for durable market-data jobs whose worker disappeared."""

import logging
from threading import Event, Thread
from typing import Any

LOGGER = logging.getLogger(__name__)


class MarketDataRecoveryScheduler:
    """Reclaim expired durable jobs without creating new collection work.

    Collection coordinators fence workers with renewable database leases. A
    process can disappear immediately after renewing one of those leases, so a
    startup-only recovery pass is insufficient: the lease may still be valid at
    startup and expire later. This lifecycle-owned poller closes that gap while
    leaving unexpired work entirely untouched.
    """

    def __init__(
        self,
        equity_collection: Any,
        fund_collection: Any,
        *,
        resume_subscription_jobs: bool,
        interval_seconds: float = 10.0,
    ) -> None:
        self.equity_collection = equity_collection
        self.fund_collection = fund_collection
        self.resume_subscription_jobs = bool(resume_subscription_jobs)
        self.interval_seconds = max(0.05, float(interval_seconds))
        self._stop = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(
            target=self._run,
            name="market-data-job-recovery",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=min(self.interval_seconds + 1.0, 5.0))
        self._thread = None

    def tick(self) -> dict[str, list[str]]:
        resumed: dict[str, list[str]] = {"equity": [], "fund": []}
        for label, coordinator in (
            ("equity", self.equity_collection),
            ("fund", self.fund_collection),
        ):
            try:
                job_ids = coordinator.resume_pending(
                    resume_subscription_jobs=self.resume_subscription_jobs
                )
            except Exception:
                LOGGER.exception("Market-data %s recovery scan failed", label)
                continue
            resumed[label] = [str(job_id) for job_id in (job_ids or [])]
        return resumed

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self.tick()


__all__ = ["MarketDataRecoveryScheduler"]
