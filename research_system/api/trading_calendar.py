from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo


_SHANGHAI = ZoneInfo("Asia/Shanghai")
_MORNING_OPEN = time(9, 30)
_MORNING_CLOSE = time(11, 30)
_AFTERNOON_OPEN = time(13, 0)
_AFTERNOON_CLOSE = time(15, 0)


class AShareTradingCalendar:
    """Offline XSHG session calendar with a weekday fallback for minimal installs."""

    def __init__(self) -> None:
        try:
            import exchange_calendars as exchange_calendars

            self._calendar: Any | None = exchange_calendars.get_calendar("XSHG")
            self.backend = "exchange_calendars:XSHG"
        except (ImportError, ValueError):
            self._calendar = None
            self.backend = "weekday_fallback"

    def localize(self, moment: datetime) -> datetime:
        if moment.tzinfo is None:
            return moment.replace(tzinfo=_SHANGHAI)
        return moment.astimezone(_SHANGHAI)

    def trading_date(self, moment: datetime) -> date:
        return self.localize(moment).date()

    def is_trading_day(self, value: date) -> bool:
        if self._calendar is not None:
            import pandas as pd

            return bool(self._calendar.is_session(pd.Timestamp(value)))
        return value.weekday() < 5

    def next_trading_day(self, value: date) -> date:
        if self._calendar is not None:
            import pandas as pd

            stamp = pd.Timestamp(value)
            if self._calendar.is_session(stamp):
                return self._calendar.next_session(stamp).date()
            return self._calendar.date_to_session(stamp, direction="next").date()
        candidate = value + timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        return candidate

    def is_market_open(self, moment: datetime) -> bool:
        local = self.localize(moment)
        if not self.is_trading_day(local.date()):
            return False
        current = local.time().replace(tzinfo=None)
        return _MORNING_OPEN <= current <= _MORNING_CLOSE or _AFTERNOON_OPEN <= current <= _AFTERNOON_CLOSE

    def next_session_date(self, moment: datetime) -> date:
        local = self.localize(moment)
        if self.is_trading_day(local.date()) and local.time().replace(tzinfo=None) < _MORNING_OPEN:
            return local.date()
        return self.next_trading_day(local.date())

    def rule_summary(self) -> dict[str, Any]:
        return {
            "calendar": self.backend,
            "timezone": "Asia/Shanghai",
            "sessions": ["09:30-11:30", "13:00-15:00"],
        }


__all__ = ["AShareTradingCalendar"]
