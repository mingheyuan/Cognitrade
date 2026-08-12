from __future__ import annotations

import json
import os
from importlib.util import find_spec
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from typing import Any, Protocol

from research_system.api.hikyuu_backtest_service import HikyuuBacktestService
from research_system.strategies.backtesting.instruments import TradableInstrument
from research_system.strategies.errors import StrategyDomainError


class TechnicalSignalCore(Protocol):
    engine_version: str

    def events(
        self,
        *,
        instrument: TradableInstrument,
        bars: list[dict[str, Any]],
        signals: list[dict[str, Any]],
        fast_period: int,
        slow_period: int,
    ) -> list[dict[str, Any]]: ...


class HikyuuSignalCore:
    engine_version = "hikyuu-crtSG-report-gate-v1"

    def __init__(self, runtime_root: Path, *, timeout_seconds: int = 120) -> None:
        self.runtime_root = Path(runtime_root)
        self.timeout_seconds = max(10, int(timeout_seconds))
        self.runtime_root.mkdir(parents=True, exist_ok=True)

    def events(
        self,
        *,
        instrument: TradableInstrument,
        bars: list[dict[str, Any]],
        signals: list[dict[str, Any]],
        fast_period: int,
        slow_period: int,
    ) -> list[dict[str, Any]]:
        if find_spec("hikyuu") is None:
            raise StrategyDomainError(
                "backtest_engine_unavailable",
                "Hikyuu 未安装，不能运行策略回测",
                status_code=503,
            )
        home = self.runtime_root / "home"
        HikyuuBacktestService._prepare_hikyuu_home(home)
        request = {
            "operation": "report_gate_signals",
            "symbol": instrument.code,
            "name": instrument.name,
            "asset_type": instrument.asset_type.value,
            "bars": bars,
            "signals": signals,
            "fast_period": fast_period,
            "slow_period": slow_period,
        }
        temporary_root = self.runtime_root / "requests"
        temporary_root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=temporary_root) as temporary:
            request_path = Path(temporary) / "request.json"
            response_path = Path(temporary) / "response.json"
            request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
            environment = dict(os.environ)
            environment.update(
                {
                    "HOME": str(home),
                    "MPLCONFIGDIR": str(home / ".cache" / "matplotlib"),
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
                    timeout=self.timeout_seconds,
                    env=environment,
                )
            except subprocess.TimeoutExpired as exc:
                raise StrategyDomainError(
                    "backtest_engine_timeout",
                    "Hikyuu 技术信号计算超时",
                    status_code=504,
                ) from exc
            if completed.returncode != 0 or not response_path.exists():
                diagnostics = "\n".join(
                    value.strip()
                    for value in (completed.stderr, completed.stdout)
                    if value.strip()
                )
                raise StrategyDomainError(
                    "backtest_engine_failed",
                    "Hikyuu 技术信号计算失败",
                    status_code=500,
                    details={"diagnostics": diagnostics[-2000:]},
                )
            try:
                result = json.loads(response_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise StrategyDomainError(
                    "backtest_engine_invalid_output",
                    "Hikyuu 返回了无效技术信号",
                    status_code=500,
                ) from exc
            return [dict(row) for row in result.get("events") or [] if isinstance(row, dict)]


__all__ = ["HikyuuSignalCore", "TechnicalSignalCore"]
