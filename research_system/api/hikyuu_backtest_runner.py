from __future__ import annotations

import json
from pathlib import Path
import sys

from research_system.api.hikyuu_backtest_service import HikyuuBacktestService


def main() -> int:
    if len(sys.argv) != 3:
        return 2
    request_path = Path(sys.argv[1])
    response_path = Path(sys.argv[2])
    request = json.loads(request_path.read_text(encoding="utf-8"))
    result = HikyuuBacktestService._run_hikyuu(
        symbol=str(request["symbol"]),
        name=str(request["name"]),
        bars=list(request["bars"]),
        initial_cash=float(request["initial_cash"]),
        fast_period=int(request["fast_period"]),
        slow_period=int(request["slow_period"]),
        trade_size=int(request["trade_size"]),
    )
    response_path.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
