from __future__ import annotations

from datetime import UTC, datetime
from importlib import metadata
from importlib.util import find_spec
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import RLock
from typing import Any

from research_system.api.finance_market_data_service import FinanceMarketDataService
from research_system.runtime.core.id_utils import generate_ulid


class BacktestError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 422,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


class HikyuuBacktestService:
    """Independent, file-persisted Hikyuu strategy backtest facade."""

    def __init__(self, *, result_root: Path, market_data: FinanceMarketDataService) -> None:
        self.result_root = Path(result_root)
        self.market_data = market_data
        self.result_root.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def engine_status(self) -> dict[str, Any]:
        available = find_spec("hikyuu") is not None
        try:
            version = metadata.version("hikyuu")
        except metadata.PackageNotFoundError:
            version = None
        return {
            "engine": "hikyuu",
            "available": available,
            "version": version,
            "mode": "isolated_external_kdata",
            "strategies": [
                {
                    "id": "ma_cross",
                    "name": "双均线交叉",
                    "description": "短周期均线上穿长周期均线买入，反向交叉卖出。",
                }
            ],
            "data_source": "Finance Pack K-line snapshot",
        }

    def run(
        self,
        *,
        symbol: str,
        initial_cash: float,
        fast_period: int,
        slow_period: int,
        trade_size: int,
        refresh: bool = False,
    ) -> dict[str, Any]:
        if find_spec("hikyuu") is None:
            raise BacktestError(
                "backtest_engine_unavailable",
                "Hikyuu 未安装，请安装项目量化依赖后重试",
                status_code=503,
            )
        if fast_period < 2 or slow_period <= fast_period:
            raise BacktestError("invalid_strategy_parameters", "长周期必须大于短周期，且短周期至少为 2")
        if trade_size <= 0 or trade_size % 100 != 0:
            raise BacktestError("invalid_trade_size", "每次交易数量必须是 100 股的正整数倍")
        if initial_cash < 10_000:
            raise BacktestError("invalid_initial_cash", "回测初始资金不能低于 1 万元")

        market_payload = self.market_data.get_market_data(symbol, refresh=refresh)
        bars = self._normalise_bars(
            market_payload.get("sections", {}).get("kline", {}).get("bars")
        )
        minimum_bars = slow_period + 5
        if len(bars) < minimum_bars:
            raise BacktestError(
                "insufficient_kline_history",
                f"至少需要 {minimum_bars} 根有效 K 线，当前只有 {len(bars)} 根",
                details={"required": minimum_bars, "available": len(bars)},
            )

        with self._lock:
            payload = self._run_hikyuu_subprocess(
                symbol=str(market_payload["symbol"]),
                name=str(market_payload["name"]),
                bars=bars,
                initial_cash=float(initial_cash),
                fast_period=int(fast_period),
                slow_period=int(slow_period),
                trade_size=int(trade_size),
            )
            payload.update(
                {
                    "backtest_id": generate_ulid(),
                    "created_at": datetime.now(UTC).isoformat(),
                    "data_quality_status": market_payload.get("quality_status"),
                    "data_source": market_payload.get("sources", {}).get("kline", {}),
                }
            )
            self._write_result(payload)
            return payload

    def _run_hikyuu_subprocess(
        self,
        *,
        symbol: str,
        name: str,
        bars: list[dict[str, Any]],
        initial_cash: float,
        fast_period: int,
        slow_period: int,
        trade_size: int,
    ) -> dict[str, Any]:
        runtime_home = self.result_root / ".hikyuu_runtime"
        self._prepare_hikyuu_home(runtime_home)
        runtime_root = self.result_root / ".runtime"
        runtime_root.mkdir(parents=True, exist_ok=True)
        request_payload = {
            "symbol": symbol,
            "name": name,
            "bars": bars,
            "initial_cash": initial_cash,
            "fast_period": fast_period,
            "slow_period": slow_period,
            "trade_size": trade_size,
        }
        with TemporaryDirectory(dir=runtime_root) as temporary:
            temporary_path = Path(temporary)
            request_path = temporary_path / "request.json"
            response_path = temporary_path / "response.json"
            request_path.write_text(
                json.dumps(request_payload, ensure_ascii=False),
                encoding="utf-8",
            )
            environment = dict(os.environ)
            environment.update(
                {
                    "HOME": str(runtime_home),
                    "MPLCONFIGDIR": str(runtime_home / ".cache" / "matplotlib"),
                    "GIT_PYTHON_REFRESH": "quiet",
                    "QT_QPA_PLATFORM": "offscreen",
                }
            )
            try:
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "research_system.api.hikyuu_backtest_runner",
                        str(request_path),
                        str(response_path),
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    env=environment,
                )
            except subprocess.TimeoutExpired as exc:
                raise BacktestError(
                    "backtest_timeout",
                    "Hikyuu 回测超过 120 秒，已终止隔离进程",
                    status_code=504,
                ) from exc
            if completed.returncode != 0 or not response_path.exists():
                diagnostics = "\n".join(
                    part.strip()
                    for part in (completed.stderr, completed.stdout)
                    if part.strip()
                )
                raise BacktestError(
                    "backtest_engine_failed",
                    "Hikyuu 隔离进程执行失败",
                    status_code=500,
                    details={"diagnostics": diagnostics[-2000:]},
                )
            try:
                return json.loads(response_path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise BacktestError(
                    "backtest_engine_invalid_output",
                    "Hikyuu 返回了无效结果",
                    status_code=500,
                ) from exc

    @staticmethod
    def _prepare_hikyuu_home(runtime_home: Path) -> None:
        hikyuu_root = runtime_home / ".hikyuu"
        hub_cache = hikyuu_root / "hub_cache"
        matplotlib_cache = runtime_home / ".cache" / "matplotlib"
        hub_cache.mkdir(parents=True, exist_ok=True)
        matplotlib_cache.mkdir(parents=True, exist_ok=True)
        database_path = hikyuu_root / "hub.db"
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS hub_repo (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR UNIQUE,
                    hub_type VARCHAR,
                    local_base VARCHAR,
                    local VARCHAR,
                    url VARCHAR,
                    branch VARCHAR
                )
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO hub_repo
                    (id, name, hub_type, local_base, local, url, branch)
                VALUES (1, 'default', 'local', 'default', ?, '', 'main')
                """,
                (str(hub_cache / "default"),),
            )
            connection.commit()

    def list_results(self, *, limit: int = 20) -> list[dict[str, Any]]:
        normalized_limit = min(max(int(limit), 1), 100)
        rows: list[dict[str, Any]] = []
        with self._lock:
            paths = sorted(self.result_root.glob("*.json"), reverse=True)
            for path in paths[:normalized_limit]:
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                rows.append(
                    {
                        key: payload.get(key)
                        for key in (
                            "backtest_id",
                            "created_at",
                            "symbol",
                            "name",
                            "strategy",
                            "parameters",
                            "period",
                            "metrics",
                        )
                    }
                )
        return rows

    def get_result(self, backtest_id: str) -> dict[str, Any]:
        normalized = str(backtest_id or "").strip()
        if not normalized or any(char not in "0123456789ABCDEFGHJKMNPQRSTVWXYZ" for char in normalized):
            raise BacktestError("invalid_backtest_id", "回测 ID 格式无效")
        path = self.result_root / f"{normalized}.json"
        if not path.exists():
            raise BacktestError("backtest_not_found", "未找到指定回测结果", status_code=404)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise BacktestError(
                "backtest_result_corrupt",
                "回测结果文件损坏",
                status_code=500,
            ) from exc

    @staticmethod
    def _normalise_bars(raw_bars: Any) -> list[dict[str, Any]]:
        if not isinstance(raw_bars, list):
            return []
        by_date: dict[str, dict[str, Any]] = {}
        for raw in raw_bars:
            if not isinstance(raw, dict):
                continue
            date = str(raw.get("date") or raw.get("datetime") or "").strip()
            try:
                bar = {
                    "date": date[:10],
                    "open": float(raw["open"]),
                    "high": float(raw["high"]),
                    "low": float(raw["low"]),
                    "close": float(raw["close"]),
                    "volume": float(raw.get("volume") or 0),
                    "amount": float(raw.get("amount") or 0),
                }
            except (KeyError, TypeError, ValueError):
                continue
            if (
                not bar["date"]
                or min(bar["open"], bar["high"], bar["low"], bar["close"]) <= 0
                or bar["low"] > bar["high"]
            ):
                continue
            by_date[bar["date"]] = bar
        return [by_date[key] for key in sorted(by_date)]

    @staticmethod
    def _run_hikyuu(
        *,
        symbol: str,
        name: str,
        bars: list[dict[str, Any]],
        initial_cash: float,
        fast_period: int,
        slow_period: int,
        trade_size: int,
    ) -> dict[str, Any]:
        import hikyuu as hku

        market = "SH" if symbol.startswith(("5", "6", "9")) else (
            "BJ" if symbol.startswith(("4", "8", "92")) else "SZ"
        )
        stock = hku.Stock(market, symbol, name)
        stock.valid = True
        stock.type = hku.constant.STOCKTYPE_A
        stock.tick = 0.01
        stock.tick_value = 0.01
        stock.atom = 1
        stock.min_trade_number = 100
        stock.max_trade_number = 100_000_000
        records = [
            hku.KRecord(
                hku.Datetime(bar["date"]),
                bar["open"],
                bar["high"],
                bar["low"],
                bar["close"],
                bar["amount"],
                bar["volume"],
            )
            for bar in bars
        ]
        stock.set_krecord_list(records, hku.Query.DAY)
        kdata = stock.get_kdata(hku.Query(0))

        costs = hku.TC_FixedA2017(
            commission=0.00025,
            lowest_commission=5.0,
            stamptax=0.0005,
            transferfee=0.00001,
        )
        manager = hku.crtTM(
            date=records[0].datetime,
            init_cash=initial_cash,
            cost_func=costs,
            name=f"Cognitrade-{symbol}",
        )
        signal = hku.SG_Cross(
            hku.MA(hku.CLOSE(), fast_period),
            hku.MA(hku.CLOSE(), slow_period),
        )
        system = hku.SYS_Simple(
            tm=manager,
            sg=signal,
            mm=hku.MM_FixedCount(trade_size),
        )
        system.run(stock, hku.Query(0))

        dates = kdata.get_datetime_list()
        equity_values = [float(value) for value in manager.get_funds_curve(dates, hku.Query.DAY)]
        curve = [
            {"date": bars[index]["date"], "equity": round(value, 2)}
            for index, value in enumerate(equity_values)
        ]
        peak = 0.0
        maximum_drawdown = 0.0
        for value in equity_values:
            peak = max(peak, value)
            if peak > 0:
                maximum_drawdown = min(maximum_drawdown, (value - peak) / peak * 100)

        performance = hku.Performance()
        performance.statistics(manager, dates[-1])
        performance_values = performance.to_dict()
        trades = []
        for trade in manager.get_trade_list():
            business = getattr(trade.business, "name", str(trade.business))
            if business == "INIT":
                continue
            trade_date = trade.datetime
            trades.append(
                {
                    "date": f"{trade_date.year:04d}-{trade_date.month:02d}-{trade_date.day:02d}",
                    "side": str(business).lower(),
                    "price": round(float(trade.real_price), 4),
                    "quantity": int(trade.number),
                    "fees": round(float(trade.cost.total), 2),
                    "cash_after": round(float(trade.cash), 2),
                }
            )

        final_equity = equity_values[-1] if equity_values else initial_cash
        total_return = (final_equity / initial_cash - 1) * 100
        return {
            "engine": {
                "name": "hikyuu",
                "version": metadata.version("hikyuu"),
            },
            "symbol": symbol,
            "name": name,
            "strategy": {
                "id": "ma_cross",
                "name": "双均线交叉",
            },
            "parameters": {
                "initial_cash": initial_cash,
                "fast_period": fast_period,
                "slow_period": slow_period,
                "trade_size": trade_size,
                "commission_rate": 0.00025,
                "minimum_commission": 5.0,
                "stamp_duty_rate": 0.0005,
                "transfer_fee_rate": 0.00001,
            },
            "period": {
                "start": bars[0]["date"],
                "end": bars[-1]["date"],
                "bar_count": len(bars),
                "frequency": "DAY",
            },
            "metrics": {
                "initial_cash": round(initial_cash, 2),
                "final_equity": round(final_equity, 2),
                "total_return_pct": round(total_return, 4),
                "annualized_return_pct": round(
                    float(performance_values.get("帐户年复合收益率%", 0.0)),
                    4,
                ),
                "max_drawdown_pct": round(maximum_drawdown, 4),
                "closed_trade_count": int(performance_values.get("已平仓交易总数", 0)),
                "winning_trade_rate_pct": round(
                    float(performance_values.get("赢利交易比例%", 0.0)),
                    4,
                ),
                "profit_loss_ratio": round(
                    float(performance_values.get("平均赢利/平均亏损比例", 0.0)),
                    4,
                ),
            },
            "equity_curve": curve,
            "trades": trades,
        }

    def _write_result(self, payload: dict[str, Any]) -> None:
        backtest_id = str(payload["backtest_id"])
        destination = self.result_root / f"{backtest_id}.json"
        temporary = self.result_root / f".{backtest_id}.tmp"
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(destination)


__all__ = ["BacktestError", "HikyuuBacktestService"]
