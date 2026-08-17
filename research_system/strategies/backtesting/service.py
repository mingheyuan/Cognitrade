from __future__ import annotations

import hashlib
import json
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from threading import Event, RLock, Thread
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from research_system.runtime.core.id_utils import generate_ulid
from research_system.strategies.backtesting.adapters import (
    ExchangeExecutionAdapter,
    OTCExecutionAdapter,
)
from research_system.strategies.backtesting.instruments import AssetType, InstrumentResolver
from research_system.strategies.backtesting.market_data_gateway import MarketDataGateway
from research_system.strategies.backtesting.portfolio_ledger import (
    run_exchange_single_asset,
    run_holdings_portfolio,
    run_otc_single_asset,
)
from research_system.strategies.backtesting.result_store import ContentAddressedResultStore
from research_system.strategies.backtesting.signal_core import HikyuuSignalCore, TechnicalSignalCore
from research_system.strategies.contracts import BacktestMode, StrategyDocumentV1
from research_system.strategies.database import StrategyDatabase
from research_system.strategies.errors import StrategyConflict, StrategyDomainError
from research_system.strategies.models import (
    ArtifactObjectRef,
    BacktestRun,
    BacktestSignalLink,
    DailyReportNode,
    DailyReportSet,
    Strategy,
    StrategyAssignment,
    StrategyVersion,
    TradeSignal,
)
from research_system.strategies.repository import StrategyRepository
from research_system.strategies.service import OwnerScope
from research_system.strategies.trading_calendar import trading_dates


class _BacktestControlRequested(RuntimeError):
    pass


class StrategyBacktestService:
    def __init__(
        self,
        database: StrategyDatabase,
        *,
        market_data_gateway: MarketDataGateway,
        object_store_root: Path,
        signal_core: TechnicalSignalCore | None = None,
        max_workers: int = 1,
        recover: bool = True,
        lease_seconds: float = 300.0,
    ) -> None:
        self.database = database
        self.repository = StrategyRepository()
        self.market_data_gateway = market_data_gateway
        self.result_store = ContentAddressedResultStore(object_store_root)
        self.signal_core = signal_core or HikyuuSignalCore(Path(object_store_root) / ".hikyuu_runtime")
        self._lease_seconds = max(1.0, float(lease_seconds))
        self._heartbeat_interval = min(30.0, max(0.25, self._lease_seconds / 3.0))
        self._worker_id = f"backtest-worker-{generate_ulid()}"
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, min(int(max_workers), 4)),
            thread_name_prefix="cognitrade-strategy-backtest",
        )
        self._lock = RLock()
        self._futures: dict[str, Future] = {}
        self._closed = False
        self._shutdown_complete = Event()
        if recover:
            self._recover()

    def close(self) -> None:
        with self._lock:
            should_shutdown = not self._closed
            self._closed = True
        if not should_shutdown:
            self._shutdown_complete.wait()
            return
        try:
            # The database owner is normally closed immediately after this service.
            # Wait for queued/running jobs so no worker can outlive its connection pool.
            self._executor.shutdown(wait=True, cancel_futures=False)
        finally:
            self._shutdown_complete.set()

    def preflight(
        self,
        scope: OwnerScope,
        *,
        strategy_version_id: str,
        fund_code: str,
        mode: BacktestMode,
        start_date: date,
        end_date: date,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            strategy, version, assignment, document = self._context(
                session,
                scope,
                strategy_version_id=strategy_version_id,
                fund_code=fund_code,
                mode=mode,
            )
            available_reports = list(
                session.scalars(
                    select(DailyReportSet).where(
                        DailyReportSet.tenant_id == scope.tenant_id,
                        DailyReportSet.user_id == scope.user_id,
                        DailyReportSet.fund_code == fund_code,
                        DailyReportSet.as_of_date >= start_date,
                        DailyReportSet.as_of_date <= end_date,
                        DailyReportSet.status.in_(["final", "final_partial", "blocked"]),
                    )
                )
            )
            signals = list(
                session.scalars(
                    select(TradeSignal).where(
                        TradeSignal.tenant_id == scope.tenant_id,
                        TradeSignal.user_id == scope.user_id,
                        TradeSignal.assignment_id == assignment.id,
                        TradeSignal.strategy_version_id == version.id,
                        TradeSignal.kind == "fund",
                        TradeSignal.effective_from <= end_date,
                    )
                )
            )
        expected = _trading_dates(start_date, end_date)
        report_dates = {row.as_of_date for row in available_reports}
        missing = [value for value in expected if value not in report_dates]
        return {
            "strategy_id": strategy.id,
            "strategy_version_id": version.id,
            "strategy_content_hash": version.content_hash,
            "assignment_id": assignment.id,
            "fund_code": fund_code,
            "mode": mode.value,
            "start_date": start_date,
            "end_date": end_date,
            "trading_day_count": len(expected),
            "available_report_count": len(report_dates),
            "missing_report_dates": missing,
            "signal_count": len(signals),
            "ready": bool(signals) and not missing,
            "carry_days": document.signal_thresholds.missing_report_carry_days,
        }

    def create(
        self,
        scope: OwnerScope,
        *,
        strategy_version_id: str,
        fund_code: str,
        mode: BacktestMode,
        start_date: date,
        end_date: date,
        initial_cash: float,
        benchmark: dict[str, Any] | None = None,
        report_policy: str = "existing_only",
        parent_backtest_run_id: str | None = None,
    ) -> dict[str, Any]:
        if end_date < start_date:
            raise StrategyDomainError("invalid_date_range", "回测结束日期不能早于开始日期")
        if initial_cash < 10_000:
            raise StrategyDomainError("invalid_initial_cash", "回测初始资金不能低于 1 万元")
        if report_policy not in {"existing_only", "require_complete"}:
            raise StrategyDomainError("invalid_report_policy", "不支持的报告使用策略")
        benchmark_config = _benchmark_config(mode, benchmark)
        preflight = self.preflight(
            scope,
            strategy_version_id=strategy_version_id,
            fund_code=fund_code,
            mode=mode,
            start_date=start_date,
            end_date=end_date,
        )
        if not preflight["signal_count"]:
            raise StrategyConflict("backtest_signals_missing", "该策略版本在区间内没有可回放信号")
        if report_policy == "require_complete" and preflight["missing_report_dates"]:
            raise StrategyConflict(
                "backtest_reports_incomplete",
                "回测区间日报不完整，请先补跑",
                details={"missing_report_dates": preflight["missing_report_dates"]},
            )
        request = {
            "strategy_version_id": strategy_version_id,
            "fund_code": fund_code,
            "mode": mode.value,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "initial_cash": float(initial_cash),
            "benchmark": benchmark_config,
            "report_policy": report_policy,
            "assignment_id": preflight["assignment_id"],
        }
        with self.database.session() as session:
            version = session.get(StrategyVersion, strategy_version_id)
            assignment = session.get(StrategyAssignment, preflight["assignment_id"])
            if version is None or assignment is None:
                raise StrategyConflict("backtest_context_changed", "回测上下文已变化，请重新预检")
            row = BacktestRun(
                parent_backtest_run_id=parent_backtest_run_id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                strategy_id=version.strategy_id,
                strategy_version_id=version.id,
                strategy_content_hash=version.content_hash,
                fund_code=fund_code,
                fund_name=assignment.fund_name,
                mode=mode.value,
                start_date=start_date,
                end_date=end_date,
                initial_cash=float(initial_cash),
                benchmark_json=benchmark_config,
                request_json=request,
                input_snapshot_hash=_hash(request),
                engine_version=self.signal_core.engine_version,
                status="queued",
            )
            session.add(row)
            session.flush()
            payload = self._payload(row)
        self._submit(row.id)
        return payload

    def list(self, scope: OwnerScope) -> dict[str, Any]:
        with self.database.session() as session:
            rows = self.repository.list_backtests(
                session,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
            )
            return {"backtests": [self._payload(row) for row in rows]}

    def get(self, scope: OwnerScope, backtest_id: str, *, include_result: bool = True) -> dict[str, Any]:
        with self.database.session() as session:
            row = self._require(session, scope, backtest_id)
            payload = self._payload(row)
            uri = row.result_object_uri
        if include_result and uri:
            payload["result"] = self.result_store.get_json(uri)
        return payload

    def retry(self, scope: OwnerScope, backtest_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            row = self._require(session, scope, backtest_id)
            if row.status in {"queued", "running"}:
                raise StrategyConflict("backtest_still_running", "回测仍在运行")
            request = dict(row.request_json or {})
        return self.create(
            scope,
            strategy_version_id=str(request["strategy_version_id"]),
            fund_code=str(request["fund_code"]),
            mode=BacktestMode(str(request["mode"])),
            start_date=date.fromisoformat(str(request["start_date"])),
            end_date=date.fromisoformat(str(request["end_date"])),
            initial_cash=float(request["initial_cash"]),
            benchmark=dict(request.get("benchmark") or {}),
            report_policy=str(request.get("report_policy") or "existing_only"),
            parent_backtest_run_id=backtest_id,
        )

    def cancel(self, scope: OwnerScope, backtest_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            row = self._require(session, scope, backtest_id)
            if row.status not in {"queued", "running"}:
                raise StrategyConflict("backtest_not_cancellable", "当前回测状态不能取消")
            row.cancel_requested = True
            return self._payload(row)

    def _submit(self, backtest_id: str) -> None:
        with self._lock:
            if self._closed:
                return
            current = self._futures.get(backtest_id)
            if current is not None and not current.done():
                return
            future = self._executor.submit(self._execute, backtest_id)
            self._futures[backtest_id] = future
            future.add_done_callback(
                lambda completed, key=backtest_id: self._forget(key, completed)
            )

    def _forget(self, backtest_id: str, future: Future) -> None:
        with self._lock:
            if self._futures.get(backtest_id) is future:
                self._futures.pop(backtest_id, None)

    def _execute(self, backtest_id: str) -> None:
        heartbeat_stop = Event()
        heartbeat: Thread | None = None
        try:
            scope, request = self._claim(backtest_id)
            if scope is None or request is None:
                return
            heartbeat = Thread(
                target=self._heartbeat_loop,
                args=(backtest_id, heartbeat_stop),
                name=f"cognitrade-backtest-heartbeat-{backtest_id}",
                daemon=True,
            )
            heartbeat.start()
            control = lambda: self._check_control(backtest_id)
            control()
            result, input_hash, signal_links = self._run(scope, request, control=control)
            control()
            stored = self.result_store.put_json(result)
            control()
            with self.database.session() as session:
                row = session.get(BacktestRun, backtest_id)
                if row is None:
                    return
                if row.cancel_requested:
                    row.status = "cancelled"
                    row.completed_at = datetime.now(UTC)
                    row.lease_owner = None
                    row.lease_until = None
                    return
                row.status = "completed"
                row.input_snapshot_hash = input_hash
                row.metrics_json = dict(result["metrics"])
                row.result_object_uri = stored.uri
                row.result_content_hash = stored.content_hash
                row.completed_at = datetime.now(UTC)
                row.lease_owner = None
                row.lease_until = None
                for link in signal_links:
                    session.add(
                        BacktestSignalLink(
                            backtest_run_id=row.id,
                            signal_id=link["signal_id"],
                            trading_date=link["trading_date"],
                            usage_reason=link["usage_reason"],
                        )
                    )
                self._retain_result_object(
                    session,
                    backtest_id=row.id,
                    content_hash=stored.content_hash,
                    uri=stored.uri,
                    size_bytes=stored.size_bytes,
                )
        except _BacktestControlRequested:
            with self.database.session() as session:
                row = session.get(BacktestRun, backtest_id)
                if row is not None and row.status in {"queued", "running"}:
                    row.status = "cancelled"
                    row.completed_at = datetime.now(UTC)
                    row.lease_owner = None
                    row.lease_until = None
        except Exception as exc:
            with self.database.session() as session:
                row = session.get(BacktestRun, backtest_id)
                if row is not None:
                    row.status = "failed"
                    row.error_summary = str(exc)
                    row.completed_at = datetime.now(UTC)
                    row.lease_owner = None
                    row.lease_until = None
        finally:
            heartbeat_stop.set()
            if heartbeat is not None:
                heartbeat.join()

    def _run(
        self,
        scope: OwnerScope,
        request: dict[str, Any],
        *,
        control: Callable[[], None] = lambda: None,
    ) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
        control()
        start_date = date.fromisoformat(str(request["start_date"]))
        end_date = date.fromisoformat(str(request["end_date"]))
        mode = BacktestMode(str(request["mode"]))
        with self.database.session() as session:
            strategy, version, assignment, document = self._context(
                session,
                scope,
                strategy_version_id=str(request["strategy_version_id"]),
                fund_code=str(request["fund_code"]),
                mode=mode,
            )
            signals = list(
                session.scalars(
                    select(TradeSignal).where(
                        TradeSignal.tenant_id == scope.tenant_id,
                        TradeSignal.user_id == scope.user_id,
                        TradeSignal.assignment_id == assignment.id,
                        TradeSignal.strategy_version_id == version.id,
                        TradeSignal.effective_from <= end_date,
                    ).order_by(TradeSignal.effective_from.asc(), TradeSignal.revision.asc())
                )
            )
            if not signals:
                raise StrategyConflict("backtest_signals_missing", "回测信号不存在")
            report_ids = list(dict.fromkeys(signal.source_report_set_id for signal in signals))
            reports = list(
                session.scalars(
                    select(DailyReportSet)
                    .where(DailyReportSet.id.in_(report_ids))
                    .order_by(DailyReportSet.effective_date.asc(), DailyReportSet.revision.asc())
                )
            )
            nodes_by_report = {
                report.id: list(
                    session.scalars(
                        select(DailyReportNode)
                        .where(DailyReportNode.report_set_id == report.id)
                        .order_by(DailyReportNode.rank.asc())
                    )
                )
                for report in reports
            }
        signal_payloads = [_signal_payload(row) for row in signals]
        control()
        fund_signals = [row for row in signal_payloads if row["kind"] == "fund"]
        if not fund_signals:
            raise StrategyConflict("fund_signal_missing", "回测区间没有基金总门禁信号")
        fund_sources = [
            {
                "report_as_of": report.as_of_date,
                "source": next(
                    (
                        dict(node.source_json or {})
                        for node in nodes_by_report[report.id]
                        if node.node_type == "fund_report"
                    ),
                    {},
                ),
            }
            for report in reports
        ]
        latest_fund_payload = next(
            (entry["source"] for entry in reversed(fund_sources) if entry["source"]),
            {},
        )
        warmup_start = start_date - timedelta(days=document.execution.slow_period * 3)
        market_hashes: dict[str, str] = {}
        benchmark_config = dict(request.get("benchmark") or {})
        if mode == BacktestMode.FUND_ONLY:
            instrument = InstrumentResolver.resolve_fund(
                latest_fund_payload,
                code=assignment.fund_code,
                name=assignment.fund_name,
            )
            series = self.market_data_gateway.get_series(
                instrument,
                start_date=warmup_start,
                end_date=end_date,
                as_of_date=end_date,
                fund_sources=fund_sources,
            )
            control()
            bars = list(series.bars)
            market_hashes[instrument.instrument_id] = series.content_hash
            events = self.signal_core.events(
                instrument=instrument,
                bars=bars,
                signals=fund_signals,
                fast_period=document.execution.fast_period,
                slow_period=document.execution.slow_period,
            )
            control()
            if instrument.asset_type == AssetType.OTC_FUND:
                ledger = run_otc_single_asset(
                    instrument=instrument,
                    bars=bars,
                    events=events,
                    adapter=OTCExecutionAdapter.from_document(document),
                    initial_cash=float(request["initial_cash"]),
                    start_date=start_date,
                    end_date=end_date,
                )
            else:
                ledger = run_exchange_single_asset(
                    instrument=instrument,
                    bars=bars,
                    events=events,
                    adapter=ExchangeExecutionAdapter.exchange_fund(document),
                    initial_cash=float(request["initial_cash"]),
                    start_date=start_date,
                    end_date=end_date,
                )
            benchmarks: dict[str, Any] = {}
            if benchmark_config.get("fund_buy_and_hold", True):
                benchmarks["fund_buy_and_hold"] = _buy_hold_benchmark(bars, start_date, end_date)
            if benchmark_config.get("category_index"):
                category_code, category_name = _category_benchmark(latest_fund_payload)
                benchmarks["category_index"] = (
                    self._index_benchmark(
                        code=category_code,
                        name=category_name,
                        start_date=start_date,
                        end_date=end_date,
                        market_hashes=market_hashes,
                    )
                    if category_code
                    else {"status": "unavailable", "reason": "category_index_code_unavailable"}
                )
            instruments_payload = [instrument.payload()]
        else:
            report_schedule = _report_schedule(reports, nodes_by_report)
            symbols = sorted(
                {
                    symbol
                    for report in report_schedule
                    for symbol in report["target_weights"]
                }
            )
            instruments = {
                symbol: InstrumentResolver.resolve_stock(symbol, _holding_name(symbol, nodes_by_report))
                for symbol in symbols
            }
            stock_signals: dict[str, list[dict[str, Any]]] = {
                symbol: [
                    row
                    for row in signal_payloads
                    if row["kind"] == "holding_stock" and row["subject_code"] == symbol
                ]
                for symbol in symbols
            }
            bars_by_symbol: dict[str, list[dict[str, Any]]] = {}
            events_by_symbol: dict[str, list[dict[str, Any]]] = {}
            for symbol, instrument in instruments.items():
                control()
                if not stock_signals[symbol]:
                    raise StrategyConflict("holding_signal_missing", f"持仓 {symbol} 缺少可追溯个股信号")
                series = self.market_data_gateway.get_series(
                    instrument,
                    start_date=warmup_start,
                    end_date=end_date,
                    as_of_date=end_date,
                )
                bars_by_symbol[symbol] = list(series.bars)
                market_hashes[instrument.instrument_id] = series.content_hash
                events_by_symbol[symbol] = self.signal_core.events(
                    instrument=instrument,
                    bars=bars_by_symbol[symbol],
                    signals=stock_signals[symbol],
                    fast_period=document.execution.fast_period,
                    slow_period=document.execution.slow_period,
                )
                control()
            ledger = run_holdings_portfolio(
                instruments=instruments,
                bars_by_symbol=bars_by_symbol,
                events_by_symbol=events_by_symbol,
                stock_signals=stock_signals,
                fund_signals=fund_signals,
                report_schedule=report_schedule,
                adapter=ExchangeExecutionAdapter.stock(document),
                initial_cash=float(request["initial_cash"]),
                start_date=start_date,
                end_date=end_date,
                max_single_stock_pct=document.execution.max_single_stock_pct,
                drift_pct_points=document.execution.rebalance_drift_pct_points,
            )
            control()
            benchmarks = {}
            if benchmark_config.get("initial_disclosed_weights_static", True):
                benchmarks["initial_disclosed_weights_static"] = _static_holdings_benchmark(
                    report_schedule,
                    bars_by_symbol,
                    start_date,
                    end_date,
                )
            if benchmark_config.get("hs300", True):
                benchmarks["hs300"] = self._index_benchmark(
                    code="000300",
                    name="沪深300",
                    start_date=start_date,
                    end_date=end_date,
                    market_hashes=market_hashes,
                )
            instruments_payload = [value.payload() for value in instruments.values()]
        custom_code = str(benchmark_config.get("custom_code") or "").strip()
        if custom_code:
            benchmarks["custom"] = self._index_benchmark(
                code=custom_code,
                name=str(benchmark_config.get("custom_name") or custom_code),
                start_date=start_date,
                end_date=end_date,
                market_hashes=market_hashes,
            )
        _enrich_trade_lineage(
            ledger["trades"],
            strategy=strategy,
            version=version,
            assignment=assignment,
            signals=signals,
            report_ids={report.id for report in reports},
        )
        control()
        result = {
            "schema_version": "1",
            "engine": {
                "technical_core": self.signal_core.engine_version,
                "ledger": "cognitrade-deterministic-ledger-v1",
            },
            "strategy": {
                "id": strategy.id,
                "version_id": version.id,
                "content_hash": version.content_hash,
            },
            "assignment_id": assignment.id,
            "fund_code": assignment.fund_code,
            "fund_name": assignment.fund_name,
            "mode": mode.value,
            "period": {"start": start_date, "end": end_date},
            "instruments": instruments_payload,
            "market_data_hashes": market_hashes,
            "report_set_ids": [report.id for report in reports],
            "benchmarks": benchmarks,
            **ledger,
        }
        links = _lineage_links(result["trades"], allowed_signal_ids={row.id for row in signals})
        input_hash = _hash(
            {
                "request": request,
                "strategy_content_hash": version.content_hash,
                "reports": [(row.id, row.source_hash, row.revision) for row in reports],
                "signals": [(row.id, row.content_hash) for row in signals],
                "market_data_hashes": market_hashes,
            }
        )
        return result, input_hash, links

    def _claim(self, backtest_id: str) -> tuple[OwnerScope | None, dict[str, Any] | None]:
        now = datetime.now(UTC)
        with self.database.session() as session:
            query = select(BacktestRun).where(BacktestRun.id == backtest_id)
            if not self.database.is_sqlite:
                query = query.with_for_update(skip_locked=True)
            row = session.scalar(query)
            if row is None or row.status not in {"queued", "running"}:
                return None, None
            lease_until = _aware(row.lease_until)
            if row.lease_owner and row.lease_owner != self._worker_id and lease_until and lease_until > now:
                return None, None
            if row.cancel_requested:
                row.status = "cancelled"
                row.completed_at = now
                return None, None
            row.status = "running"
            row.lease_owner = self._worker_id
            row.lease_until = now + timedelta(seconds=self._lease_seconds)
            return OwnerScope(row.tenant_id, row.user_id, self._worker_id), dict(row.request_json or {})

    def _heartbeat_loop(self, backtest_id: str, stop: Event) -> None:
        while not stop.wait(self._heartbeat_interval):
            try:
                with self.database.session() as session:
                    row = session.get(BacktestRun, backtest_id)
                    if (
                        row is None
                        or row.status != "running"
                        or row.lease_owner != self._worker_id
                        or row.cancel_requested
                    ):
                        return
                    row.lease_until = datetime.now(UTC) + timedelta(seconds=self._lease_seconds)
            except Exception:
                continue

    def _check_control(self, backtest_id: str) -> None:
        with self.database.session() as session:
            row = session.get(BacktestRun, backtest_id)
            if (
                row is None
                or row.cancel_requested
                or row.status != "running"
                or row.lease_owner != self._worker_id
            ):
                raise _BacktestControlRequested(backtest_id)

    @staticmethod
    def _retain_result_object(
        session,
        *,
        backtest_id: str,
        content_hash: str,
        uri: str,
        size_bytes: int,
    ) -> None:
        object_ref = session.scalar(
            select(ArtifactObjectRef).where(
                ArtifactObjectRef.content_hash == content_hash,
                ArtifactObjectRef.uri == uri,
            )
        )
        if object_ref is not None:
            object_ref.reference_count += 1
            return
        try:
            with session.begin_nested():
                session.add(
                    ArtifactObjectRef(
                        ref_type="strategy_backtest_result",
                        ref_id=backtest_id,
                        content_hash=content_hash,
                        uri=uri,
                        size_bytes=size_bytes,
                        retention_level="permanent",
                        reference_count=1,
                    )
                )
                session.flush()
        except IntegrityError:
            object_ref = session.scalar(
                select(ArtifactObjectRef).where(
                    ArtifactObjectRef.content_hash == content_hash,
                    ArtifactObjectRef.uri == uri,
                )
            )
            if object_ref is None:
                raise
            object_ref.reference_count += 1

    def _index_benchmark(
        self,
        *,
        code: str,
        name: str,
        start_date: date,
        end_date: date,
        market_hashes: dict[str, str],
    ) -> dict[str, Any]:
        instrument = InstrumentResolver.resolve_index(code, name)
        try:
            series = self.market_data_gateway.get_series(
                instrument,
                start_date=start_date,
                end_date=end_date,
                as_of_date=end_date,
            )
        except StrategyDomainError as exc:
            return {
                "status": "unavailable",
                "symbol": instrument.code,
                "name": instrument.name,
                "reason": exc.code,
            }
        market_hashes[instrument.instrument_id] = series.content_hash
        return {
            **_buy_hold_benchmark(list(series.bars), start_date, end_date),
            "symbol": instrument.code,
            "name": instrument.name,
            "content_hash": series.content_hash,
        }

    def _context(
        self,
        session,
        scope: OwnerScope,
        *,
        strategy_version_id: str,
        fund_code: str,
        mode: BacktestMode,
    ) -> tuple[Strategy, StrategyVersion, StrategyAssignment, StrategyDocumentV1]:
        version = session.get(StrategyVersion, strategy_version_id)
        if version is None:
            raise StrategyDomainError("strategy_version_not_found", "策略版本不存在", status_code=404)
        strategy = session.scalar(
            select(Strategy).where(
                Strategy.id == version.strategy_id,
                Strategy.tenant_id == scope.tenant_id,
                Strategy.user_id == scope.user_id,
                Strategy.is_system_template.is_(False),
            )
        )
        if strategy is None:
            raise StrategyDomainError("strategy_version_not_found", "策略版本不存在", status_code=404)
        assignment = session.scalar(
            select(StrategyAssignment).where(
                StrategyAssignment.tenant_id == scope.tenant_id,
                StrategyAssignment.user_id == scope.user_id,
                StrategyAssignment.strategy_id == strategy.id,
                StrategyAssignment.fund_code == fund_code,
                StrategyAssignment.mode == mode.value,
            )
        )
        if assignment is None:
            raise StrategyConflict("strategy_assignment_missing", "该基金和模式没有策略 Assignment")
        document = StrategyDocumentV1.model_validate(version.document_json)
        if mode not in document.report_inputs.enabled_backtest_modes:
            raise StrategyConflict("backtest_mode_disabled", "策略版本未启用该回测模式")
        return strategy, version, assignment, document

    def _require(self, session, scope: OwnerScope, backtest_id: str) -> BacktestRun:
        row = session.scalar(
            select(BacktestRun).where(
                BacktestRun.id == backtest_id,
                BacktestRun.tenant_id == scope.tenant_id,
                BacktestRun.user_id == scope.user_id,
            )
        )
        if row is None:
            raise StrategyDomainError("strategy_backtest_not_found", "未找到策略回测", status_code=404)
        return row

    def _recover(self) -> None:
        now = datetime.now(UTC)
        try:
            with self.database.session() as session:
                rows = list(
                    session.scalars(
                        select(BacktestRun).where(BacktestRun.status.in_(["queued", "running"]))
                    )
                )
                recover: list[str] = []
                for row in rows:
                    lease_until = _aware(row.lease_until)
                    if row.status == "running" and lease_until and lease_until > now:
                        continue
                    row.status = "queued"
                    row.lease_owner = None
                    row.lease_until = None
                    recover.append(row.id)
            for backtest_id in recover:
                self._submit(backtest_id)
        except Exception:
            return

    @staticmethod
    def _payload(row: BacktestRun) -> dict[str, Any]:
        return {
            "id": row.id,
            "parent_backtest_run_id": row.parent_backtest_run_id,
            "strategy_id": row.strategy_id,
            "strategy_version_id": row.strategy_version_id,
            "strategy_content_hash": row.strategy_content_hash,
            "fund_code": row.fund_code,
            "fund_name": row.fund_name,
            "mode": row.mode,
            "start_date": row.start_date,
            "end_date": row.end_date,
            "initial_cash": row.initial_cash,
            "benchmark": row.benchmark_json,
            "input_snapshot_hash": row.input_snapshot_hash,
            "engine_version": row.engine_version,
            "status": row.status,
            "metrics": row.metrics_json,
            "result_content_hash": row.result_content_hash,
            "error_summary": row.error_summary,
            "created_at": row.created_at,
            "completed_at": row.completed_at,
        }


def _benchmark_config(mode: BacktestMode, payload: dict[str, Any] | None) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "fund_buy_and_hold": mode == BacktestMode.FUND_ONLY,
        "initial_disclosed_weights_static": mode == BacktestMode.HOLDINGS_PORTFOLIO,
        "category_index": mode == BacktestMode.FUND_ONLY,
        "hs300": mode == BacktestMode.HOLDINGS_PORTFOLIO,
    }
    supplied = dict(payload or {})
    allowed = {*defaults, "custom_code", "custom_name"}
    unknown = set(supplied) - allowed
    if unknown:
        raise StrategyDomainError(
            "invalid_benchmark_config",
            f"包含不支持的基准字段：{', '.join(sorted(unknown))}",
        )
    for key in defaults:
        if key in supplied and not isinstance(supplied[key], bool):
            raise StrategyDomainError("invalid_benchmark_config", f"基准字段 {key} 必须是布尔值")
    custom_code = str(supplied.get("custom_code") or "").strip()
    if custom_code:
        InstrumentResolver.resolve_index(custom_code, str(supplied.get("custom_name") or custom_code))
    return {
        **defaults,
        **{key: supplied[key] for key in defaults if key in supplied},
        **({"custom_code": custom_code} if custom_code else {}),
        **(
            {"custom_name": str(supplied.get("custom_name") or custom_code).strip()}
            if custom_code
            else {}
        ),
    }


def _category_benchmark(payload: dict[str, Any]) -> tuple[str | None, str]:
    name = str(
        payload.get("benchmark_name")
        or payload.get("tracking_index_name")
        or payload.get("underlying_index_name")
        or payload.get("benchmark")
        or "类别指数"
    ).strip()
    for key in ("benchmark_code", "tracking_index_code", "underlying_index_code", "index_code"):
        raw = str(payload.get(key) or "").strip().lower()
        normalized = raw.removeprefix("sh").removeprefix("sz").split(".", 1)[0]
        if len(normalized) == 6 and normalized.isdigit():
            return normalized, name or normalized
    joined = " ".join(str(value or "") for value in payload.values() if isinstance(value, (str, int, float)))
    for marker, code in (
        ("沪深300", "000300"),
        ("中证500", "000905"),
        ("中证1000", "000852"),
        ("上证50", "000016"),
        ("创业板", "399006"),
    ):
        if marker in joined:
            return code, marker
    return None, name or "类别指数"


def _signal_payload(row: TradeSignal) -> dict[str, Any]:
    return {
        "id": row.id,
        "kind": row.kind,
        "subject_code": row.subject_code,
        "state": row.state,
        "as_of_date": row.as_of_date.isoformat(),
        "revision": row.revision,
        "effective_from": row.effective_from.isoformat(),
        "expires_after": row.expires_after.isoformat() if row.expires_after else None,
        "source_report_set_id": row.source_report_set_id,
    }


def _report_schedule(
    reports: list[DailyReportSet],
    nodes_by_report: dict[str, list[DailyReportNode]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for report in reports:
        targets = {
            node.subject_code: max(0.0, float(node.effective_weight_pct or node.disclosed_weight_pct or 0.0)) / 100.0
            for node in nodes_by_report[report.id]
            if node.node_type == "holding_uzi" and node.status == "completed"
        }
        rows.append(
            {
                "id": report.id,
                "effective_date": report.effective_date.isoformat(),
                "revision": report.revision,
                "target_weights": targets,
            }
        )
    return rows


def _holding_name(symbol: str, nodes_by_report: dict[str, list[DailyReportNode]]) -> str:
    for nodes in reversed(list(nodes_by_report.values())):
        for node in nodes:
            if node.subject_code == symbol and node.subject_name:
                return node.subject_name
    return symbol


def _buy_hold_benchmark(bars: list[dict[str, Any]], start: date, end: date) -> dict[str, Any]:
    rows = [row for row in bars if start.isoformat() <= str(row["date"]) <= end.isoformat()]
    if len(rows) < 2:
        return {"status": "unavailable"}
    return {
        "status": "available",
        "start": rows[0]["date"],
        "end": rows[-1]["date"],
        "return_pct": round((float(rows[-1]["close"]) / float(rows[0]["open"]) - 1) * 100, 6),
    }


def _static_holdings_benchmark(
    report_schedule: list[dict[str, Any]],
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    start: date,
    end: date,
) -> dict[str, Any]:
    if not report_schedule:
        return {"status": "unavailable"}
    weights = report_schedule[0]["target_weights"]
    weighted_return = 0.0
    invested_weight = 0.0
    for symbol, weight in weights.items():
        rows = [row for row in bars_by_symbol.get(symbol, []) if start.isoformat() <= str(row["date"]) <= end.isoformat()]
        if len(rows) < 2:
            continue
        weighted_return += float(weight) * (float(rows[-1]["close"]) / float(rows[0]["open"]) - 1)
        invested_weight += float(weight)
    return {
        "status": "available" if invested_weight else "unavailable",
        "return_pct": round(weighted_return * 100, 6),
        "invested_weight": round(invested_weight, 6),
        "cash_weight": round(max(0.0, 1.0 - invested_weight), 6),
    }


def _enrich_trade_lineage(
    trades: list[dict[str, Any]],
    *,
    strategy: Strategy,
    version: StrategyVersion,
    assignment: StrategyAssignment,
    signals: list[TradeSignal],
    report_ids: set[str],
) -> None:
    signal_index = {row.id: row for row in signals}
    for trade in trades:
        signal_ids = list(
            dict.fromkeys(
                str(value)
                for value in trade.get("signal_ids") or ([trade.get("signal_id")] if trade.get("signal_id") else [])
                if value
            )
        )
        if not signal_ids:
            raise StrategyDomainError(
                "backtest_trade_lineage_missing",
                "存在无法追溯到交易信号的成交",
                status_code=500,
            )
        unknown = [signal_id for signal_id in signal_ids if signal_id not in signal_index]
        if unknown:
            raise StrategyDomainError(
                "backtest_trade_signal_unknown",
                "成交引用了本次回测输入之外的交易信号",
                status_code=500,
                details={"signal_ids": unknown},
            )
        lineage = [
            {
                "signal_id": signal_id,
                "strategy_version_id": signal_index[signal_id].strategy_version_id,
                "source_report_set_id": signal_index[signal_id].source_report_set_id,
                "kind": signal_index[signal_id].kind,
                "subject_code": signal_index[signal_id].subject_code,
            }
            for signal_id in signal_ids
        ]
        signal_report_ids = [str(row["source_report_set_id"]) for row in lineage]
        execution_report_id = str(trade.get("source_report_set_id") or "") or None
        if execution_report_id and execution_report_id not in report_ids:
            raise StrategyDomainError(
                "backtest_trade_report_unknown",
                "成交引用了本次回测输入之外的日报",
                status_code=500,
                details={"report_set_id": execution_report_id},
            )
        source_report_ids = list(
            dict.fromkeys(
                value for value in [execution_report_id, *signal_report_ids] if value
            )
        )
        trade.update(
            {
                "strategy_id": strategy.id,
                "strategy_version_id": version.id,
                "strategy_content_hash": version.content_hash,
                "assignment_id": assignment.id,
                "signal_id": signal_ids[0],
                "signal_ids": signal_ids,
                "signal_lineage": lineage,
                "source_report_set_id": source_report_ids[0],
                "source_report_set_ids": source_report_ids,
            }
        )


def _lineage_links(
    trades: list[dict[str, Any]],
    *,
    allowed_signal_ids: set[str],
) -> list[dict[str, Any]]:
    links: dict[tuple[str, str], dict[str, Any]] = {}
    for trade in trades:
        signal_ids = list(trade.get("signal_ids") or [])
        if not signal_ids:
            raise StrategyDomainError(
                "backtest_trade_lineage_missing",
                "存在无法追溯到交易信号的成交",
                status_code=500,
            )
        for signal_id in signal_ids:
            if str(signal_id) not in allowed_signal_ids:
                raise StrategyDomainError(
                    "backtest_trade_signal_unknown",
                    "成交引用了本次回测输入之外的交易信号",
                    status_code=500,
                )
            key = (str(signal_id), str(trade["date"]))
            links[key] = {
                "signal_id": str(signal_id),
                "trading_date": date.fromisoformat(str(trade["date"])),
                "usage_reason": str(trade.get("technical_reason") or "strategy_execution")[:128],
            }
    return list(links.values())


def _trading_dates(start: date, end: date) -> list[date]:
    return trading_dates(start, end)


def _hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


__all__ = ["StrategyBacktestService"]
