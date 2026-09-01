from __future__ import annotations

from datetime import datetime, timezone


def parse_utc_datetime(value: object) -> datetime | None:
    """Parse an ISO datetime and normalize naive values to UTC."""

    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


__all__ = ["parse_utc_datetime"]
