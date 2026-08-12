from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache


DEFAULT_CALENDAR_ID = "XSHG"


@lru_cache(maxsize=8)
def _calendar(calendar_id: str):
    import exchange_calendars as xcals

    return xcals.get_calendar(calendar_id)


def is_trading_day(value: date, *, calendar_id: str = DEFAULT_CALENDAR_ID) -> bool:
    """Return whether *value* is an exchange session.

    The weekday fallback keeps local development usable when the optional calendar
    package is unavailable, while production dependencies pin exchange-calendars.
    """

    if value.weekday() >= 5:
        return False
    try:
        import pandas as pd

        return bool(_calendar(calendar_id).is_session(pd.Timestamp(value)))
    except (ImportError, KeyError, ValueError):
        return value.weekday() < 5


def trading_dates(
    start: date,
    end: date,
    *,
    calendar_id: str = DEFAULT_CALENDAR_ID,
) -> list[date]:
    if end < start:
        return []
    try:
        import pandas as pd

        sessions = _calendar(calendar_id).sessions_in_range(pd.Timestamp(start), pd.Timestamp(end))
        return [value.date() for value in sessions]
    except (ImportError, KeyError, ValueError):
        values: list[date] = []
        cursor = start
        while cursor <= end:
            if cursor.weekday() < 5:
                values.append(cursor)
            cursor += timedelta(days=1)
        return values


def next_trading_day(value: date, *, calendar_id: str = DEFAULT_CALENDAR_ID) -> date:
    candidate = value + timedelta(days=1)
    while not is_trading_day(candidate, calendar_id=calendar_id):
        candidate += timedelta(days=1)
    return candidate


def add_trading_days(
    value: date,
    days: int,
    *,
    calendar_id: str = DEFAULT_CALENDAR_ID,
) -> date:
    candidate = value
    remaining = max(0, int(days))
    while remaining:
        candidate = next_trading_day(candidate, calendar_id=calendar_id)
        remaining -= 1
    return candidate


def trading_day_distance(
    start: date,
    end: date,
    *,
    calendar_id: str = DEFAULT_CALENDAR_ID,
) -> int:
    if end <= start:
        return 0
    return len(trading_dates(start + timedelta(days=1), end, calendar_id=calendar_id))


__all__ = [
    "DEFAULT_CALENDAR_ID",
    "add_trading_days",
    "is_trading_day",
    "next_trading_day",
    "trading_dates",
    "trading_day_distance",
]
