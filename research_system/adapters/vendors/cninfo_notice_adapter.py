from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import akshare as ak


class CninfoNoticeAdapter:
    vendor_name = "cninfo"

    def get_notice_snapshot(self, *, symbol: str) -> dict[str, Any]:
        end_date = date.today()
        begin_date = end_date - timedelta(days=30)
        notice_df = ak.stock_zh_a_disclosure_report_cninfo(
            symbol=symbol,
            start_date=begin_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
        )
        if notice_df.empty:
            raise ValueError(f"no notice data returned for symbol {symbol}")

        latest = notice_df.iloc[0].to_dict()
        return {
            "symbol": symbol,
            "window_start": begin_date.isoformat(),
            "window_end": end_date.isoformat(),
            "latest_notice": {str(k): self._normalize_value(v) for k, v in latest.items()},
            "recent_notices": [
                {str(k): self._normalize_value(v) for k, v in row.items()}
                for row in notice_df.head(5).to_dict(orient="records")
            ],
            "notice_count": int(len(notice_df)),
        }

    def _normalize_value(self, value: Any) -> Any:
        if hasattr(value, "item"):
            try:
                return value.item()
            except Exception:
                return str(value)
        return value
