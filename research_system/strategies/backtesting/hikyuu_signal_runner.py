from __future__ import annotations

import json
from pathlib import Path
import sys

from research_system.strategies.backtesting.hikyuu_signal_engine import report_gate_signal_events


def main() -> int:
    if len(sys.argv) != 3:
        return 2
    request_path = Path(sys.argv[1])
    response_path = Path(sys.argv[2])
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if request.get("operation") != "report_gate_signals":
        return 2
    result = report_gate_signal_events(
        symbol=str(request["symbol"]),
        name=str(request["name"]),
        asset_type=str(request["asset_type"]),
        bars=list(request["bars"]),
        signals=list(request["signals"]),
        fast_period=int(request["fast_period"]),
        slow_period=int(request["slow_period"]),
    )
    response_path.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
