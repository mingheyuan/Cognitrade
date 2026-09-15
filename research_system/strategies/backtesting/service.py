from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
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
from research_system.strategies.backtesting.instruments import (
    AssetType,
    InstrumentResolver,
)
from research_system.strategies.backtesting.market_data_gateway import (
    MarketDataGateway,
    MarketSeries,
)
from research_system.strategies.backtesting.portfolio_ledger import (
    run_exchange_single_asset,
    run_holdings_portfolio,
    run_otc_single_asset,
)
from research_system.strategies.backtesting.report_condition_ledger import (
    ENGINE_VERSION as REPORT_CONDITION_ENGINE,
)
from research_system.strategies.backtesting.report_condition_ledger import (
    run_report_conditions,
)
from research_system.strategies.backtesting.result_store import (
    ContentAddressedResultStore,
    json_content_hash,
)
from research_system.strategies.backtesting.signal_core import (
    HikyuuSignalCore,
    TechnicalSignalCore,
)
from research_system.strategies.contracts import (
    BacktestMode,
    StrategyDocument,
    is_report_conditions,
    parse_strategy_document,
)
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


@dataclass(frozen=True)
class _BacktestInputs:
    request: dict[str, Any]
    start_date: date
    end_date: date
    mode: BacktestMode
    strategy: Strategy
    version: StrategyVersion
    assignment: StrategyAssignment
    document: StrategyDocument
    signals: list[TradeSignal]
    reports: list[DailyReportSet]
    nodes_by_report: dict[str, list[DailyReportNode]]

    @property
    def initial_cash(self) -> float:
        return float(self.request["initial_cash"])

    @property
    def warmup_start(self) -> date:
        if is_report_conditions(self.document):
            return self.start_date
        return self.start_date - timedelta(days=self.document.execution.slow_period * 3)


@dataclass(frozen=True)
class _ModeResult:
    ledger: dict[str, Any]
    benchmarks: dict[str, Any]
    instruments: list[dict[str, Any]]


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
        if recover:
            self._recover()

    def close(self) -> None:
        self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=False)

    def preflight(
        self,
        scope: OwnerScope,
        *,
        strategy_version_id: str,
        fund_code: str,
        mode: BacktestMode,
        start_date: date,
        end_date: date,
        market_data_manifest_hash: str | None = None,
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
                        TradeSignal.kind == ("holding_stock" if is_report_conditions(document) else "fund"),
                        TradeSignal.effective_from <= end_date,
                    )
                )
            )
            if is_report_conditions(document):
                signals = [row for row in signals if row.expires_after is not None and row.expires_after >= start_date]
            native_symbols = sorted(set(session.scalars(select(DailyReportNode.subject_code).where(
                DailyReportNode.report_set_id.in_({row.source_report_set_id for row in signals}),
                DailyReportNode.node_type == "holding_uzi",
            )))) if is_report_conditions(document) else []
        expected = _trading_dates(start_date, end_date)
        if is_report_conditions(document):
            linked_reports = {row.source_report_set_id for row in signals}
            available_reports = [row for row in available_reports if row.id in linked_reports]
        report_dates = {row.as_of_date for row in available_reports}
        missing = [value for value in expected if value not in report_dates]
        payload = {
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
        if is_report_conditions(document):
            covered_days = [day for day in expected if any(
                row.effective_from <= day and row.expires_after is not None and day <= row.expires_after
                for row in signals
            )]
            plan_readiness = _native_plan_readiness(
                [_signal_payload(row) for row in signals], expected,
            )
            market_errors = self._native_market_preflight(
                scope, symbols=native_symbols, start_date=start_date, end_date=end_date,
                market_data_manifest_hash=market_data_manifest_hash,
                price_basis_date=min([start_date, *(row.as_of_date for row in signals)]),
            )
            payload.update({
                "signal_model": document.report_signal.model,
                **plan_readiness,
                "can_run": bool(plan_readiness["executable_plan_covered_dates"]) and bool(native_symbols) and not market_errors,
                # Native plans remain valid without a newly published report
                # every day. Strict daily coverage is an explicit create policy.
                "ready": bool(plan_readiness["executable_plan_covered_dates"]) and bool(native_symbols) and not market_errors,
                "report_coverage": len(report_dates) / max(1, len(expected)),
                "signal_coverage": len(covered_days) / max(1, len(expected)),
                "signal_uncovered_dates": [day for day in expected if day not in covered_days],
                "market_data_checked": True,
                "market_data_missing": market_errors,
                "market_data_symbol_count": len(native_symbols),
                "limitations": ["日线收盘触发、次一真实开盘成交；无有效入场许可时保留现金"],
            })
        return payload

    def _native_market_preflight(
        self, scope: OwnerScope, *, symbols: list[str], start_date: date, end_date: date,
        market_data_manifest_hash: str | None, price_basis_date: date | None = None,
    ) -> list[dict[str, Any]]:
        manifest = self._read_market_manifest(
            market_data_manifest_hash, tenant_id=scope.tenant_id, user_id=scope.user_id,
            start_date=start_date, end_date=end_date,
        ) if market_data_manifest_hash else None
        missing = []
        for symbol in symbols:
            try:
                instrument = InstrumentResolver.resolve_stock(symbol.split(".")[0])
                if manifest is not None:
                    source = manifest["observations"].get(instrument.instrument_id, {}).get("source", {})
                    if not source:
                        raise StrategyConflict("market_manifest_symbol_missing", "冻结清单缺少证券行情")
                else:
                    loader = getattr(self.market_data_gateway, "get_frozen_series", None)
                    if not callable(loader):
                        raise StrategyConflict("frozen_market_gateway_unavailable", "缺少数据中心只读行情入口")
                    source = loader(instrument, start_date=price_basis_date or start_date, end_date=end_date, as_of_date=end_date).source
                _validate_report_price_basis(source, price_basis_date or start_date, end_date)
            except StrategyDomainError as exc:
                missing.append({"symbol": symbol, "code": exc.code, "message": exc.message, **exc.details})
        return missing

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
        market_data_manifest_hash: str | None = None,
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
            market_data_manifest_hash=market_data_manifest_hash,
        )
        if not preflight["signal_count"]:
            raise StrategyConflict("backtest_signals_missing", "该策略版本在区间内没有可回放信号")
        if preflight.get("signal_model") == "report_conditions_v1" and not preflight["can_run"]:
            if preflight.get("market_data_missing"):
                raise StrategyConflict("backtest_market_data_incomplete", "数据中心行情尚不满足回放，请先显式补采",
                                       details={"market_data_missing": preflight["market_data_missing"]})
            if preflight.get("report_plan_blockers") and not preflight.get("executable_plan_covered_dates"):
                raise StrategyConflict(
                    "backtest_report_plans_blocked", "报告计划均未满足可执行契约，不能用全现金结果代替报告/输入阻断",
                    details={"report_plan_blockers": preflight["report_plan_blockers"],
                             "executable_plan_coverage": preflight["executable_plan_coverage"]},
                )
            raise StrategyConflict("backtest_conditions_outside_period", "回测区间没有处于有效期内的报告条件")
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
        if market_data_manifest_hash is not None:
            if preflight.get("signal_model") != "report_conditions_v1":
                raise StrategyConflict("market_manifest_mode_unsupported", "冻结行情清单入口仅用于原生报告条件回放")
            self._read_market_manifest(
                market_data_manifest_hash, tenant_id=scope.tenant_id, user_id=scope.user_id,
                start_date=start_date, end_date=end_date,
            )
            request["market_data_manifest_hash"] = market_data_manifest_hash
        request_hash = json_content_hash(request)
        # Coalesce rapid duplicate submissions while keeping completed runs
        # replayable for deterministic verification.
        with self._lock, self.database.session() as session:
            active_query = (
                select(BacktestRun)
                .where(
                    BacktestRun.tenant_id == scope.tenant_id,
                    BacktestRun.user_id == scope.user_id,
                    BacktestRun.input_snapshot_hash == request_hash,
                    BacktestRun.status.in_(["queued", "running"]),
                    BacktestRun.cancel_requested.is_(False),
                )
            )
            if parent_backtest_run_id is not None:
                # A retry is a child of one specific attempt. Reusing an
                # unrelated active run would silently erase that lineage.
                active_query = active_query.where(
                    BacktestRun.parent_backtest_run_id == parent_backtest_run_id
                )
            active = session.scalar(
                active_query.order_by(BacktestRun.created_at.asc()).limit(1)
            )
            if active is not None:
                row = active
            else:
                version = session.get(StrategyVersion, strategy_version_id)
                assignment = session.get(
                    StrategyAssignment,
                    preflight["assignment_id"],
                )
                if version is None or assignment is None:
                    raise StrategyConflict(
                        "backtest_context_changed",
                        "回测上下文已变化，请重新预检",
                    )
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
                    input_snapshot_hash=request_hash,
                    engine_version=(REPORT_CONDITION_ENGINE if preflight.get("signal_model") == "report_conditions_v1"
                                    else self.signal_core.engine_version),
                    status="queued",
                )
                session.add(row)
                session.flush()
            payload = self._payload(row)
            backtest_id = row.id
        self._submit(backtest_id)
        return payload

    def list(
        self,
        scope: OwnerScope,
        *,
        strategy_id: str | None = None,
        strategy_version_id: str | None = None,
        fund_code: str | None = None,
        mode: BacktestMode | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        with self.database.session() as session:
            rows = self.repository.list_backtests(
                session,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                strategy_id=strategy_id,
                strategy_version_id=strategy_version_id,
                fund_code=fund_code,
                mode=mode.value if mode else None,
                status=status,
                limit=limit,
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
            if row.result_object_uri:
                result = self.result_store.get_json(row.result_object_uri)
                frozen_hash = (result.get("market_data_manifest") or {}).get("content_hash")
                if frozen_hash:
                    request["market_data_manifest_hash"] = frozen_hash
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
            market_data_manifest_hash=request.get("market_data_manifest_hash"),
        )

    def cancel(self, scope: OwnerScope, backtest_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            row = self._require(session, scope, backtest_id)
            if row.status not in {"queued", "running"}:
                raise StrategyConflict("backtest_not_cancellable", "当前回测状态不能取消")
            row.cancel_requested = True
            return self._payload(row)

    def _submit(self, backtest_id: str) -> None:
        if self._closed:
            return
        with self._lock:
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
                manifest = result.get("market_data_manifest")
                if manifest:
                    self._retain_result_object(
                        session, backtest_id=row.id, content_hash=manifest["content_hash"],
                        uri=manifest["uri"], size_bytes=manifest["size_bytes"],
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
                    if isinstance(exc, StrategyDomainError):
                        row.metrics_json = {"error_code": exc.code, "error_details": exc.details}
                    row.completed_at = datetime.now(UTC)
                    row.lease_owner = None
                    row.lease_until = None
        finally:
            heartbeat_stop.set()
            if heartbeat is not None:
                heartbeat.join(timeout=1.0)

    def _run(
        self,
        scope: OwnerScope,
        request: dict[str, Any],
        *,
        control: Callable[[], None] = lambda: None,
    ) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
        control()
        inputs = self._load_inputs(scope, request)
        start_date = inputs.start_date
        end_date = inputs.end_date
        mode = inputs.mode
        strategy = inputs.strategy
        version = inputs.version
        assignment = inputs.assignment
        signals = inputs.signals
        reports = inputs.reports
        signal_payloads = [_signal_payload(row) for row in signals]
        control()
        fund_signals = [row for row in signal_payloads if row["kind"] == "fund"]
        native_conditions = is_report_conditions(inputs.document)
        if not fund_signals and not native_conditions:
            raise StrategyConflict("fund_signal_missing", "回测区间没有基金总门禁信号")
        market_hashes: dict[str, str] = {}
        benchmark_config = dict(request.get("benchmark") or {})
        if native_conditions:
            mode_result = self._run_report_conditions(
                inputs, signal_payloads=signal_payloads, benchmark_config=benchmark_config,
                market_hashes=market_hashes, control=control,
            )
        elif mode == BacktestMode.FUND_ONLY:
            mode_result = self._run_fund_only(
                inputs,
                fund_signals=fund_signals,
                benchmark_config=benchmark_config,
                market_hashes=market_hashes,
                control=control,
            )
        else:
            mode_result = self._run_holdings_portfolio(
                inputs,
                signal_payloads=signal_payloads,
                fund_signals=fund_signals,
                benchmark_config=benchmark_config,
                market_hashes=market_hashes,
                control=control,
            )
        custom_code = str(benchmark_config.get("custom_code") or "").strip()
        if custom_code and not native_conditions:
            mode_result.benchmarks["custom"] = self._index_benchmark(
                code=custom_code,
                name=str(benchmark_config.get("custom_name") or custom_code),
                start_date=start_date,
                end_date=end_date,
                market_hashes=market_hashes,
            )
        _enrich_trade_lineage(
            mode_result.ledger["trades"],
            strategy=strategy,
            version=version,
            assignment=assignment,
            signals=signals,
            report_ids={report.id for report in reports},
        )
        control()
        result = {
            "schema_version": "2" if native_conditions else "1",
            "engine": {
                "technical_core": REPORT_CONDITION_ENGINE if native_conditions else self.signal_core.engine_version,
                "ledger": REPORT_CONDITION_ENGINE if native_conditions else "cognitrade-deterministic-ledger-v1",
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
            "instruments": mode_result.instruments,
            "market_data_hashes": market_hashes,
            "report_set_ids": [report.id for report in reports],
            "benchmarks": mode_result.benchmarks,
            **mode_result.ledger,
        }
        links = _lineage_links(result["trades"], allowed_signal_ids={row.id for row in signals})
        input_hash = json_content_hash(
            {
                "request": {key: value for key, value in request.items() if key != "market_data_manifest_hash"},
                "strategy_content_hash": version.content_hash,
                "reports": [(row.id, row.source_hash, row.revision) for row in reports],
                "signals": [(row.id, row.content_hash) for row in signals],
                "market_data_hashes": market_hashes,
            }
        )
        return result, input_hash, links

    def _run_report_conditions(
        self, inputs: _BacktestInputs, *, signal_payloads: list[dict[str, Any]],
        benchmark_config: dict[str, Any], market_hashes: dict[str, str], control: Callable[[], None],
    ) -> _ModeResult:
        schedule = _report_schedule(inputs.reports, inputs.nodes_by_report, include_unavailable=True)
        symbols = sorted({node.subject_code for nodes in inputs.nodes_by_report.values()
                          for node in nodes if node.node_type == "holding_uzi"})
        instruments = {symbol: InstrumentResolver.resolve_stock(symbol.split(".")[0], _holding_name(symbol, inputs.nodes_by_report))
                       for symbol in symbols}
        stock_signals = {symbol: [row for row in signal_payloads if row["kind"] == "holding_stock"
                                 and row["subject_code"] == symbol] for symbol in symbols}
        # Recheck queued/recovered jobs as well: old preflight success must not
        # turn ten blocked plans into a seemingly valid all-cash replay.
        plan_readiness = _native_plan_readiness(
            [row for rows in stock_signals.values() for row in rows],
            _trading_dates(inputs.start_date, inputs.end_date),
        )
        if not plan_readiness["executable_plan_covered_dates"]:
            raise StrategyConflict(
                "backtest_report_plans_blocked", "区间内没有可执行报告计划；这不是合法零成交",
                details={"report_plan_blockers": plan_readiness["report_plan_blockers"],
                         "executable_plan_coverage": plan_readiness["executable_plan_coverage"]},
            )
        price_basis_date = min([inputs.start_date, *(row.as_of_date for row in inputs.signals)])
        if any(not row.get("execution_plan") for rows in stock_signals.values() for row in rows):
            raise StrategyConflict("native_execution_plan_missing", "原生回放需要冻结的报告条件，不能使用旧四态信号")
        loader = getattr(self.market_data_gateway, "get_frozen_series", None)
        if not callable(loader) and not inputs.request.get("market_data_manifest_hash"):
            raise StrategyConflict("frozen_market_gateway_unavailable", "原生条件回测需要数据中心只读行情入口")
        observations: dict[str, dict[str, Any]] = {}
        replay_hash = inputs.request.get("market_data_manifest_hash")
        replay_manifest = self._read_market_manifest(
            replay_hash, tenant_id=inputs.assignment.tenant_id, user_id=inputs.assignment.user_id,
            start_date=inputs.start_date, end_date=inputs.end_date,
        ) if replay_hash else None
        benchmark_failures: dict[str, dict[str, Any]] = {}

        def frozen_series(instrument):
            control()
            if replay_manifest is not None:
                observation = replay_manifest["observations"].get(instrument.instrument_id)
                if not observation:
                    raise StrategyConflict("market_manifest_symbol_missing", "冻结行情清单不含所需证券，不能联网补取",
                                           details={"symbol": instrument.code})
                if observation.get("instrument", {}).get("instrument_id") != instrument.instrument_id:
                    raise StrategyConflict("market_manifest_instrument_mismatch", "冻结行情证券身份不一致")
                series = MarketSeries(instrument, tuple(observation["bars"]), observation["content_hash"],
                                      observation["source"], tuple(observation.get("degraded_reasons") or []))
            else:
                series = loader(instrument, start_date=price_basis_date, end_date=inputs.end_date, as_of_date=inputs.end_date)
            _validate_report_price_basis(series.source, price_basis_date, inputs.end_date)
            market_hashes[instrument.instrument_id] = series.content_hash
            observations[instrument.instrument_id] = series.payload()
            return list(series.bars)

        bars = {symbol: frozen_series(instrument) for symbol, instrument in instruments.items()}
        ledger = run_report_conditions(
            instruments=instruments, bars_by_symbol=bars, stock_signals=stock_signals,
            adapter=ExchangeExecutionAdapter.stock(inputs.document), initial_cash=inputs.initial_cash,
            start_date=inputs.start_date, end_date=inputs.end_date, control=control,
        )
        ledger["report_plan_readiness"] = plan_readiness
        benchmarks = {}
        if benchmark_config.get("initial_disclosed_weights_static", True):
            benchmarks["initial_disclosed_weights_static"] = _static_holdings_benchmark(
                schedule, bars, inputs.start_date, inputs.end_date,
            )
        requested_indexes = []
        if benchmark_config.get("hs300", True):
            requested_indexes.append(("hs300", "000300", "沪深300"))
        if benchmark_config.get("custom_code"):
            requested_indexes.append(("custom", str(benchmark_config["custom_code"]), str(benchmark_config.get("custom_name") or "自定义基准")))
        for key, code, name in requested_indexes:
            if replay_manifest is not None and key in replay_manifest.get("benchmark_failures", {}):
                benchmarks[key] = replay_manifest["benchmark_failures"][key]
                benchmark_failures[key] = benchmarks[key]
                continue
            try:
                benchmark_bars = frozen_series(InstrumentResolver.resolve_index(code, name))
                if benchmark_bars and benchmark_bars[0].get("open") is None:
                    raise StrategyConflict("benchmark_open_unavailable", "基准首日缺少真实开盘价")
                benchmarks[key] = _buy_hold_benchmark(benchmark_bars, inputs.start_date, inputs.end_date)
            except StrategyDomainError as exc:
                benchmarks[key] = {"status": "unavailable", "reason": exc.code, "details": exc.details}
                benchmark_failures[key] = benchmarks[key]
        manifest = self.result_store.put_json({
            "schema_version": "1", "purpose": "report_condition_backtest_prices",
            "tenant_id": inputs.assignment.tenant_id, "user_id": inputs.assignment.user_id,
            "period": {"start": inputs.start_date, "end": inputs.end_date}, "observations": observations,
            "benchmark_failures": benchmark_failures,
        })
        ledger["market_data_manifest"] = {"uri": manifest.uri, "content_hash": manifest.content_hash,
                                          "size_bytes": manifest.size_bytes}
        ledger["report_execution_plans"] = {row["id"]: row["execution_plan"] for rows in stock_signals.values() for row in rows}
        return _ModeResult(ledger=ledger, benchmarks=benchmarks, instruments=[row.payload() for row in instruments.values()])

    def _read_market_manifest(
        self, content_hash: str, *, tenant_id: str, user_id: str, start_date: date, end_date: date,
    ) -> dict[str, Any]:
        if len(content_hash) != 64 or any(character not in "0123456789abcdef" for character in content_hash):
            raise StrategyConflict("market_manifest_hash_invalid", "行情清单必须使用有效的 SHA-256 内容哈希")
        root = self.result_store.root.resolve()
        path = (root / content_hash[:2] / f"{content_hash}.json").resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise StrategyConflict("market_manifest_not_found", "未找到可访问的冻结行情清单")
        try:
            payload = self.result_store.get_json(str(path))
        except (OSError, ValueError) as exc:
            raise StrategyConflict("market_manifest_integrity_failed", "冻结行情清单无法解码") from exc
        if json_content_hash(payload) != content_hash or payload.get("purpose") != "report_condition_backtest_prices":
            raise StrategyConflict("market_manifest_integrity_failed", "冻结行情清单内容校验失败")
        if payload.get("tenant_id") != tenant_id or payload.get("user_id") != user_id:
            raise StrategyConflict("market_manifest_not_found", "未找到可访问的冻结行情清单")
        if payload.get("period") != {"start": start_date.isoformat(), "end": end_date.isoformat()}:
            raise StrategyConflict("market_manifest_period_mismatch", "请求区间与冻结行情清单不一致")
        return payload

    def _run_fund_only(
        self,
        inputs: _BacktestInputs,
        *,
        fund_signals: list[dict[str, Any]],
        benchmark_config: dict[str, Any],
        market_hashes: dict[str, str],
        control: Callable[[], None],
    ) -> _ModeResult:
        fund_sources = [
            {
                "report_as_of": report.as_of_date,
                "source": next(
                    (
                        dict(node.source_json or {})
                        for node in inputs.nodes_by_report[report.id]
                        if node.node_type == "fund_report"
                    ),
                    {},
                ),
            }
            for report in inputs.reports
        ]
        latest_fund_payload = next(
            (entry["source"] for entry in reversed(fund_sources) if entry["source"]),
            {},
        )
        instrument = InstrumentResolver.resolve_fund(
            latest_fund_payload,
            code=inputs.assignment.fund_code,
            name=inputs.assignment.fund_name,
        )
        series = self.market_data_gateway.get_series(
            instrument,
            start_date=inputs.warmup_start,
            end_date=inputs.end_date,
            as_of_date=inputs.end_date,
            fund_sources=fund_sources,
        )
        control()
        bars = list(series.bars)
        market_hashes[instrument.instrument_id] = series.content_hash
        events = self.signal_core.events(
            instrument=instrument,
            bars=bars,
            signals=fund_signals,
            fast_period=inputs.document.execution.fast_period,
            slow_period=inputs.document.execution.slow_period,
        )
        control()
        if instrument.asset_type == AssetType.OTC_FUND:
            ledger = run_otc_single_asset(
                instrument=instrument,
                bars=bars,
                events=events,
                fund_signals=fund_signals,
                adapter=OTCExecutionAdapter.from_document(inputs.document),
                initial_cash=inputs.initial_cash,
                start_date=inputs.start_date,
                end_date=inputs.end_date,
            )
        else:
            ledger = run_exchange_single_asset(
                instrument=instrument,
                bars=bars,
                events=events,
                adapter=ExchangeExecutionAdapter.exchange_fund(inputs.document),
                initial_cash=inputs.initial_cash,
                start_date=inputs.start_date,
                end_date=inputs.end_date,
            )
        benchmarks: dict[str, Any] = {}
        if benchmark_config.get("fund_buy_and_hold", True):
            benchmarks["fund_buy_and_hold"] = _buy_hold_benchmark(
                bars,
                inputs.start_date,
                inputs.end_date,
            )
        if benchmark_config.get("category_index"):
            category_code, category_name = _category_benchmark(latest_fund_payload)
            benchmarks["category_index"] = (
                self._index_benchmark(
                    code=category_code,
                    name=category_name,
                    start_date=inputs.start_date,
                    end_date=inputs.end_date,
                    market_hashes=market_hashes,
                )
                if category_code
                else {
                    "status": "unavailable",
                    "reason": "category_index_code_unavailable",
                }
            )
        if benchmark_config.get("hs300"):
            benchmarks["hs300"] = self._index_benchmark(
                code="000300",
                name="沪深300",
                start_date=inputs.start_date,
                end_date=inputs.end_date,
                market_hashes=market_hashes,
            )
        return _ModeResult(
            ledger=ledger,
            benchmarks=benchmarks,
            instruments=[instrument.payload()],
        )

    def _run_holdings_portfolio(
        self,
        inputs: _BacktestInputs,
        *,
        signal_payloads: list[dict[str, Any]],
        fund_signals: list[dict[str, Any]],
        benchmark_config: dict[str, Any],
        market_hashes: dict[str, str],
        control: Callable[[], None],
    ) -> _ModeResult:
        report_schedule = _report_schedule(inputs.reports, inputs.nodes_by_report)
        symbols = sorted(
            {
                symbol
                for report in report_schedule
                for symbol in report["target_weights"]
            }
        )
        instruments = {
            symbol: InstrumentResolver.resolve_stock(
                symbol,
                _holding_name(symbol, inputs.nodes_by_report),
            )
            for symbol in symbols
        }
        stock_signals = {
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
                raise StrategyConflict(
                    "holding_signal_missing",
                    f"持仓 {symbol} 缺少可追溯个股信号",
                )
            series = self.market_data_gateway.get_series(
                instrument,
                start_date=inputs.warmup_start,
                end_date=inputs.end_date,
                as_of_date=inputs.end_date,
            )
            bars_by_symbol[symbol] = list(series.bars)
            market_hashes[instrument.instrument_id] = series.content_hash
            events_by_symbol[symbol] = self.signal_core.events(
                instrument=instrument,
                bars=bars_by_symbol[symbol],
                signals=stock_signals[symbol],
                fast_period=inputs.document.execution.fast_period,
                slow_period=inputs.document.execution.slow_period,
            )
            control()
        ledger = run_holdings_portfolio(
            instruments=instruments,
            bars_by_symbol=bars_by_symbol,
            events_by_symbol=events_by_symbol,
            stock_signals=stock_signals,
            fund_signals=fund_signals,
            report_schedule=report_schedule,
            adapter=ExchangeExecutionAdapter.stock(inputs.document),
            initial_cash=inputs.initial_cash,
            start_date=inputs.start_date,
            end_date=inputs.end_date,
            max_single_stock_pct=inputs.document.execution.max_single_stock_pct,
            drift_pct_points=inputs.document.execution.rebalance_drift_pct_points,
            fallback_trading_days=trading_dates(inputs.start_date, inputs.end_date),
        )
        control()
        benchmarks: dict[str, Any] = {}
        if benchmark_config.get("initial_disclosed_weights_static", True):
            benchmarks["initial_disclosed_weights_static"] = (
                _static_holdings_benchmark(
                    report_schedule,
                    bars_by_symbol,
                    inputs.start_date,
                    inputs.end_date,
                )
            )
        if benchmark_config.get("hs300", True):
            benchmarks["hs300"] = self._index_benchmark(
                code="000300",
                name="沪深300",
                start_date=inputs.start_date,
                end_date=inputs.end_date,
                market_hashes=market_hashes,
            )
        return _ModeResult(
            ledger=ledger,
            benchmarks=benchmarks,
            instruments=[value.payload() for value in instruments.values()],
        )

    def _load_inputs(
        self,
        scope: OwnerScope,
        request: dict[str, Any],
    ) -> _BacktestInputs:
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
                    ).order_by(
                        TradeSignal.effective_from.asc(),
                        TradeSignal.revision.asc(),
                    )
                )
            )
            if is_report_conditions(document):
                signals = [row for row in signals if row.kind == "holding_stock" and row.expires_after is not None and row.expires_after >= start_date]
            if not signals:
                raise StrategyConflict("backtest_signals_missing", "回测信号不存在")
            report_ids = self._report_ids_for_valuation(
                session,
                scope=scope,
                assignment=assignment,
                version=version,
                signals=signals,
                end_date=end_date,
            )
            if is_report_conditions(document):
                # Initial holdings are empty. Old expired permissions cannot
                # contribute positions and must not require years of prices.
                report_ids = list({row.source_report_set_id for row in signals})
            reports = list(
                session.scalars(
                    select(DailyReportSet)
                    .where(DailyReportSet.id.in_(report_ids))
                    .order_by(
                        DailyReportSet.effective_date.asc(),
                        DailyReportSet.revision.asc(),
                    )
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
        return _BacktestInputs(
            request=request,
            start_date=start_date,
            end_date=end_date,
            mode=mode,
            strategy=strategy,
            version=version,
            assignment=assignment,
            document=document,
            signals=signals,
            reports=reports,
            nodes_by_report=nodes_by_report,
        )

    @staticmethod
    def _report_ids_for_valuation(
        session,
        *,
        scope: OwnerScope,
        assignment: StrategyAssignment,
        version: StrategyVersion,
        signals: list[TradeSignal],
        end_date: date,
    ) -> list[str]:
        # A final-session report is valid NAV evidence even when its signal is
        # only actionable on T+1. Keep those reports out of execution signals.
        valuation_report_ids = list(
            session.scalars(
                select(TradeSignal.source_report_set_id)
                .where(
                    TradeSignal.tenant_id == scope.tenant_id,
                    TradeSignal.user_id == scope.user_id,
                    TradeSignal.assignment_id == assignment.id,
                    TradeSignal.strategy_version_id == version.id,
                    TradeSignal.as_of_date <= end_date,
                )
                .distinct()
            )
        )
        return list(
            dict.fromkeys(
                [signal.source_report_set_id for signal in signals]
                + valuation_report_ids
            )
        )

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
    ) -> tuple[Strategy, StrategyVersion, StrategyAssignment, StrategyDocument]:
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
        document = parse_strategy_document(version.document_json)
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
            "report_policy": str(
                (row.request_json or {}).get("report_policy") or "existing_only"
            ),
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
        "execution_plan": row.execution_plan_json,
    }


def _native_plan_readiness(signals: list[dict[str, Any]], expected: list[date]) -> dict[str, Any]:
    """Executable WATCH plans are valid inputs; BLOCKED plans are not cash signals.

    This projection never removes blocked stocks from the market-data universe.
    Coverage follows the latest report per stock/day, like the replay ledger,
    so an older executable plan cannot hide its blocked replacement.
    """
    blockers = []
    executable_ids = set()
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for signal in signals:
        by_symbol.setdefault(signal["subject_code"], []).append(signal)
        plan = signal.get("execution_plan") or {}
        reasons = list(plan.get("errors") or [])
        if not plan:
            reasons.append("native_execution_plan_missing")
        if plan.get("executable") is not True:
            reasons.append("report_plan_not_executable")
        if plan.get("price_basis_verified") is not True:
            reasons.append("report_reference_price_unverified")
        if signal.get("state") == "BLOCKED":
            reasons.append("report_signal_blocked")
        if reasons:
            blockers.append({
                "symbol": signal["subject_code"], "signal_id": signal["id"],
                "source_report_set_id": signal.get("source_report_set_id"),
                "reasons": list(dict.fromkeys(reasons)),
                "suggested_collection": plan.get("suggested_collection"),
            })
        else:
            executable_ids.add(signal["id"])
    covered_dates = []
    for day in expected:
        day_text = day.isoformat()
        for rows in by_symbol.values():
            latest = max(
                (row for row in rows if str(row["effective_from"]) <= day_text),
                key=lambda row: (str(row["effective_from"]), int(row.get("revision") or 0)),
                default=None,
            )
            if latest and latest["id"] in executable_ids and day_text <= str(latest.get("expires_after") or ""):
                covered_dates.append(day)
                break
    return {
        "executable_plan_count": len(executable_ids),
        "blocked_plan_count": len(blockers),
        "executable_plan_coverage": len(covered_dates) / max(1, len(expected)),
        "executable_plan_covered_dates": covered_dates,
        "executable_plan_uncovered_dates": [day for day in expected if day not in covered_dates],
        "report_plan_blockers": blockers,
    }


def _report_schedule(
    reports: list[DailyReportSet],
    nodes_by_report: dict[str, list[DailyReportNode]],
    *, include_unavailable: bool = False,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for report in reports:
        targets = {
            node.subject_code: max(0.0, float((node.disclosed_weight_pct if include_unavailable else
                                             node.effective_weight_pct or node.disclosed_weight_pct) or 0.0)) / 100.0
            for node in nodes_by_report[report.id]
            if node.node_type == "holding_uzi" and (include_unavailable or node.status == "completed")
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
    initial_value = float(rows[0]["open"])
    curve = [
        {
            "date": str(row["date"]),
            "value": round(float(row["close"]) / initial_value * 100, 6),
            "return_pct": round((float(row["close"]) / initial_value - 1) * 100, 6),
        }
        for row in rows
    ]
    return {
        "status": "available",
        "start": rows[0]["date"],
        "end": rows[-1]["date"],
        "return_pct": round((float(rows[-1]["close"]) / float(rows[0]["open"]) - 1) * 100, 6),
        "equity_curve": curve,
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
        if rows[0].get("open") is None:
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


def _validate_report_price_basis(source: dict[str, Any], start: date, end: date) -> None:
    if (source.get("adjustment") != "raw" or source.get("currency") != "CNY"
            or source.get("price_unit") != "per_share"):
        raise StrategyConflict("report_price_basis_mismatch", "原生人民币每股价位必须使用已核实的未复权 CNY 行情")
    # Dependency-injected test data must identify itself, never masquerade as
    # audited production observations in a frozen replay manifest.
    if source.get("evidence_mode") == "fixture":
        return
    coverage = source.get("corporate_action_coverage") or {}
    try:
        first = date.fromisoformat(str(coverage.get("start")))
        last = date.fromisoformat(str(coverage.get("end")))
    except (TypeError, ValueError):
        first = last = None
    sessions = trading_dates(start, end)
    if (coverage.get("verified") is not True or first is None or last is None
            or first > start or not sessions or last < sessions[-1]):
        raise StrategyConflict(
            "report_price_action_coverage_unverified",
            "缺少报告定价日至回测结束日的公司行动/复权查询证明，不能假定未发生除权",
            details={"required_price_basis_range": {"start": start.isoformat(), "end": end.isoformat()},
                     "snapshot_id": source.get("snapshot_id")},
        )


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


__all__ = ["StrategyBacktestService"]
