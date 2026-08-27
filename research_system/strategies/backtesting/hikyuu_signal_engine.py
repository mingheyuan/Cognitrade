from __future__ import annotations

from importlib import metadata
from pathlib import Path
import sqlite3
from typing import Any


def prepare_hikyuu_home(runtime_home: Path) -> None:
    """Prepare a local-only Hikyuu home for the governed signal subprocess."""

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


def report_gate_signal_events(
    *,
    symbol: str,
    name: str,
    asset_type: str,
    bars: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    fast_period: int,
    slow_period: int,
) -> dict[str, Any]:
    """Generate frozen MA events gated by point-in-time report signals."""

    import hikyuu as hku

    if not bars:
        raise RuntimeError("report gate signal calculation requires bars")
    market = "SH" if symbol.startswith(("5", "6", "9")) else (
        "BJ" if symbol.startswith(("4", "8", "92")) else "SZ"
    )
    stock = hku.Stock(market, symbol, name)
    stock.valid = True
    stock.type = {
        "stock": hku.constant.STOCKTYPE_A,
        "exchange_fund": hku.constant.STOCKTYPE_ETF,
        "otc_fund": hku.constant.STOCKTYPE_FUND,
    }.get(asset_type, hku.constant.STOCKTYPE_TMP)
    stock.tick = 0.0001 if asset_type == "otc_fund" else 0.01
    stock.tick_value = stock.tick
    stock.atom = 1
    stock.min_trade_number = 1 if asset_type == "otc_fund" else 100
    stock.max_trade_number = 100_000_000
    records = [
        hku.KRecord(
            hku.Datetime(bar["date"]),
            float(bar["open"]),
            float(bar["high"]),
            float(bar["low"]),
            float(bar["close"]),
            float(bar.get("amount") or 0),
            float(bar.get("volume") or 0),
        )
        for bar in bars
    ]
    stock.set_krecord_list(records, hku.Query.DAY)
    kdata = stock.get_kdata(hku.Query(0))
    signal_rows = sorted(
        signals,
        key=lambda row: (
            str(row.get("effective_from") or ""),
            int(row.get("revision") or 0),
        ),
    )
    gate_by_date: dict[str, dict[str, Any]] = {}
    for bar in bars:
        day = str(bar["date"])
        latest = None
        for row in signal_rows:
            if str(row.get("effective_from") or "9999-12-31") <= day:
                latest = row
            else:
                break
        if latest is None:
            gate_by_date[day] = {"state": "BLOCKED", "signal_id": None, "expired": True}
            continue
        expired = bool(latest.get("expires_after") and day > str(latest["expires_after"]))
        gate_by_date[day] = {
            "state": "BLOCKED" if expired else str(latest.get("state") or "BLOCKED"),
            "signal_id": latest.get("id"),
            "source_report_set_id": latest.get("source_report_set_id"),
            "expired": expired,
        }

    metadata_by_event: dict[tuple[str, str], dict[str, Any]] = {}

    def calculate(self, _input_kdata):
        current = self.to
        close = hku.CLOSE(current)
        fast = hku.MA(close, fast_period)
        slow = hku.MA(close, slow_period)
        start = max(
            int(getattr(fast, "discard", 0)),
            int(getattr(slow, "discard", 0)),
            1,
        )
        previous_gate = "BLOCKED"
        for index in range(start, len(current)):
            day = (
                f"{current[index].datetime.year:04d}-{current[index].datetime.month:02d}-"
                f"{current[index].datetime.day:02d}"
            )
            gate = gate_by_date.get(day, {"state": "BLOCKED", "signal_id": None})
            state = str(gate.get("state") or "BLOCKED")
            crossed_up = fast[index] > slow[index] and fast[index - 1] <= slow[index - 1]
            crossed_down = fast[index] < slow[index] and fast[index - 1] >= slow[index - 1]
            if crossed_up and state == "ALLOW_BUY":
                self._add_buy_signal(current[index].datetime)
                metadata_by_event[(day, "buy")] = {
                    **gate,
                    "technical_reason": f"ma{fast_period}_cross_above_ma{slow_period}",
                }
            if crossed_down or (state == "AVOID" and previous_gate != "AVOID"):
                self._add_sell_signal(current[index].datetime)
                metadata_by_event[(day, "sell")] = {
                    **gate,
                    "technical_reason": (
                        "report_gate_avoid"
                        if state == "AVOID" and previous_gate != "AVOID"
                        else f"ma{fast_period}_cross_below_ma{slow_period}"
                    ),
                }
            previous_gate = state

    signal = hku.crtSG(calculate, name="CognitradeReportGateMA")
    signal.set_param("alternate", False)
    signal.to = kdata
    events: list[dict[str, Any]] = []
    for index, bar in enumerate(bars):
        current = kdata[index].datetime
        day = str(bar["date"])
        if signal.should_buy(current):
            events.append({"date": day, "side": "buy", **metadata_by_event[(day, "buy")]})
        if signal.should_sell(current):
            events.append({"date": day, "side": "sell", **metadata_by_event[(day, "sell")]})
    return {
        "engine": {"name": "hikyuu", "version": metadata.version("hikyuu")},
        "signal_model": "crtSG-report-gate-ma-v1",
        "symbol": symbol,
        "asset_type": asset_type,
        "events": events,
    }


__all__ = ["prepare_hikyuu_home", "report_gate_signal_events"]
