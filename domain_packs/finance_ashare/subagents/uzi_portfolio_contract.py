from __future__ import annotations

import re
from typing import Any


_A_SHARE_FULL = re.compile(r"^(\d{6})\.(?:SH|SZ|BJ)$", re.IGNORECASE)
_A_SHARE_BARE = re.compile(r"^\d{6}$")
_HK_SHARE = re.compile(r"^(\d{1,5})(?:\.HK)?$", re.IGNORECASE)
_SEHK_SHARE = re.compile(r"^SEHK\.(\d{1,5})$", re.IGNORECASE)


def ticker_identity(value: object) -> str:
    """Return a market-aware identity without collapsing US class shares.

    UZI canonicalizes A/H tickers but deliberately accepts symbols such as
    ``BRK.A`` and ``BRK.B``.  Only exchange suffixes with unambiguous A/H
    semantics may therefore be removed for matching.
    """

    ticker = str(value or "").strip().upper()
    if not ticker:
        return ""
    a_full = _A_SHARE_FULL.fullmatch(ticker)
    if a_full:
        return f"A:{a_full.group(1)}"
    if _A_SHARE_BARE.fullmatch(ticker):
        return f"A:{ticker}"
    sehk = _SEHK_SHARE.fullmatch(ticker)
    if sehk:
        return f"H:{int(sehk.group(1)):05d}"
    hk = _HK_SHARE.fullmatch(ticker)
    if hk:
        return f"H:{int(hk.group(1)):05d}"
    return f"GLOBAL:{ticker}"


def normalize_portfolio_holdings(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mirror UZI ``portfolio_runner._normalize_weights`` without mutation."""

    holdings = [dict(row) for row in rows]
    count = len(holdings)
    if count == 0:
        return holdings
    weighted = [row for row in holdings if row.get("weight") is not None]
    unweighted = [row for row in holdings if row.get("weight") is None]
    if not weighted:
        for row in holdings:
            row["weight"] = 1.0 / count
        return holdings
    total = sum(float(row["weight"]) for row in weighted)
    if unweighted:
        remaining = max(0.0, 1.0 - total)
        share = remaining / len(unweighted) if remaining > 0 else 0.0
        for row in unweighted:
            row["weight"] = share
        total = sum(float(row.get("weight") or 0.0) for row in holdings)
    if total > 0:
        for row in holdings:
            row["weight"] = float(row.get("weight") or 0.0) / total
    return holdings


def result_ticker(result: dict[str, Any]) -> str:
    raw = result.get("raw") if isinstance(result.get("raw"), dict) else {}
    return str(raw.get("ticker") or result.get("symbol") or "").strip()


def result_ticker_identities(result: dict[str, Any]) -> set[str]:
    """Match both the request entity and UZI's basic-resolved ticker."""

    raw = result.get("raw") if isinstance(result.get("raw"), dict) else {}
    return {
        identity
        for identity in (
            ticker_identity(result.get("symbol")),
            ticker_identity(raw.get("ticker")),
            ticker_identity(raw.get("full")),
        )
        if identity
    }


__all__ = [
    "normalize_portfolio_holdings",
    "result_ticker",
    "result_ticker_identities",
    "ticker_identity",
]
