from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date, datetime, timezone
from typing import Any

_EQUITY_CODE = re.compile(r"^(?:SH|SZ|BJ)?(\d{6})(?:\.(?:SH|SZ|BJ))?$", re.IGNORECASE)
_A_SHARE_PREFIXES = (
    "000",
    "001",
    "002",
    "003",
    "300",
    "301",
    "600",
    "601",
    "603",
    "605",
    "606",
    "688",
    "689",
    "4",
    "8",
    "92",
)


class FundHoldingReportError(ValueError):
    """Base error for one immutable fund-holding report batch."""


class FundSnapshotUnavailableError(FundHoldingReportError):
    pass


class FundCandidateUnavailableError(FundHoldingReportError):
    pass


class FundTypeUnsupportedError(FundHoldingReportError):
    pass


class StaleHoldingsConfirmationRequired(FundHoldingReportError):
    def __init__(self, frozen: dict[str, Any]) -> None:
        super().__init__("holdings older than 180 days require explicit confirmation")
        self.frozen = frozen


def freeze_fund_holdings(
    artifacts: Iterable[dict[str, Any]],
    *,
    fund_code: str,
    source_artifact_id: str | None = None,
    now: datetime | date | None = None,
    top_n: int = 10,
) -> dict[str, Any]:
    """Freeze the displayed fund disclosure without doing another network read."""

    snapshot = _select_fund_snapshot(artifacts, source_artifact_id=source_artifact_id)
    content = _record(snapshot.get("content"))
    normalized_code = _normalize_code(fund_code)
    candidate = _select_fund_candidate(content, fund_code=normalized_code or fund_code)

    fund_name = str(candidate.get("fund_name") or normalized_code or fund_code).strip()
    fund_type = str(candidate.get("fund_type") or "").strip() or None
    coverage = _record(candidate.get("holdings_coverage"))
    is_fund_of_funds = coverage.get("is_fund_of_funds") is True
    if not _is_equity_fund(
        fund_name=fund_name, fund_type=fund_type, candidate=candidate
    ):
        raise FundTypeUnsupportedError("持仓报告仅支持权益类基金")

    raw_holdings, disclosure_date, holding_source = _holding_disclosure(
        candidate,
        is_fund_of_funds=is_fund_of_funds,
    )
    normalized_holdings = _normalize_holdings(
        raw_holdings,
        is_fund_of_funds=is_fund_of_funds,
        holding_source=holding_source,
        disclosure_date=disclosure_date,
    )
    normalized_top_n = int(top_n)
    if not 5 <= normalized_top_n <= 20:
        raise FundHoldingReportError("top_n must be between 5 and 20")
    normalized_holdings.sort(key=lambda row: (int(row["rank"]), str(row["symbol"])))
    normalized_holdings = normalized_holdings[:normalized_top_n]
    # Upstream disclosure tables can retain the source document row number
    # (for example 16-25 after non-equity rows).  This batch is explicitly the
    # selected top-ten set, so expose a stable 1-N position without changing
    # any security identity or weight evidence.
    for position, holding in enumerate(normalized_holdings, start=1):
        holding["rank"] = position

    age_days = _age_days(disclosure_date, now=now)
    return {
        "source_artifact_id": str(snapshot.get("artifact_id") or ""),
        "fund_code": normalized_code or str(fund_code).strip(),
        "fund_name": fund_name,
        "fund_type": fund_type,
        "holding_source": holding_source,
        "is_fund_of_funds": is_fund_of_funds,
        "disclosure_date": disclosure_date,
        "disclosure_age_days": age_days,
        "stale_level": _stale_level(age_days),
        "top_n": normalized_top_n,
        "holdings": normalized_holdings,
        "data_gap": _holding_data_gaps(
            disclosure_date=disclosure_date,
            holdings=normalized_holdings,
            raw_holdings=raw_holdings,
            is_fund_of_funds=is_fund_of_funds,
        ),
    }


def _select_fund_snapshot(
    artifacts: Iterable[dict[str, Any]],
    *,
    source_artifact_id: str | None,
) -> dict[str, Any]:
    snapshots = [
        dict(row)
        for row in artifacts
        if isinstance(row, dict)
        and str(row.get("artifact_type") or "") == "fund_snapshot"
        and (
            not source_artifact_id
            or str(row.get("artifact_id") or "") == str(source_artifact_id)
        )
    ]
    if not snapshots:
        raise FundSnapshotUnavailableError("没有可用的 active fund_snapshot artifact")
    return snapshots[-1]


def _select_fund_candidate(
    content: dict[str, Any],
    *,
    fund_code: str,
) -> dict[str, Any]:
    normalized_code = _normalize_code(fund_code)
    candidate = next(
        (
            _record(row)
            for row in list(content.get("funds") or [])
            if isinstance(row, dict)
            and _normalize_code(row.get("fund_code")) == normalized_code
        ),
        None,
    )
    if candidate is None:
        raise FundCandidateUnavailableError(f"snapshot 中不存在基金 {fund_code}")
    return candidate


def _holding_disclosure(
    candidate: dict[str, Any],
    *,
    is_fund_of_funds: bool,
) -> tuple[list[Any], str | None, str]:
    if is_fund_of_funds:
        return (
            list(candidate.get("lookthrough_holdings") or []),
            _date_text(candidate.get("lookthrough_period")),
            "target_etf_lookthrough",
        )
    return (
        list(candidate.get("top_holdings") or []),
        _date_text(candidate.get("holdings_period")),
        "direct_equity_disclosure",
    )


def _normalize_holdings(
    raw_holdings: list[Any],
    *,
    is_fund_of_funds: bool,
    holding_source: str,
    disclosure_date: str | None,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for position, raw in enumerate(raw_holdings, start=1):
        if not isinstance(raw, dict):
            continue
        code, market, supported = _security_identity(raw.get("code"))
        identity = code or str(raw.get("code") or "").strip()
        dedupe_key = f"{market}:{identity}"
        if not identity or dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        normalized.append(
            {
                "rank": _integer(raw.get("rank")) or position,
                "symbol": code or identity,
                "name": str(raw.get("name") or code or identity).strip(),
                "market": market,
                "supported": supported,
                "status": "queued" if supported else "unsupported",
                "fund_weight_pct": _number(
                    raw.get("effective_weight_pct")
                    if is_fund_of_funds
                    else raw.get("weight_pct")
                ),
                "target_security_weight_pct": (
                    _number(raw.get("weight_pct")) if is_fund_of_funds else None
                ),
                "holding_source": holding_source,
                "disclosure_date": disclosure_date,
            }
        )
    return normalized


def _stale_level(age_days: int | None) -> str:
    if age_days is None:
        return "unknown"
    if age_days > 180:
        return "confirmation_required"
    if age_days > 120:
        return "warning"
    return "current"


def _holding_data_gaps(
    *,
    disclosure_date: str | None,
    holdings: list[dict[str, Any]],
    raw_holdings: list[Any],
    is_fund_of_funds: bool,
) -> list[str]:
    gaps: list[str] = []
    if not disclosure_date:
        gaps.append("holding_disclosure_date_missing")
    if not holdings:
        gaps.append("no_analyzable_equity_holdings")
    if holdings and not any(row["supported"] for row in holdings):
        gaps.append("no_supported_ashare_holdings")
    if is_fund_of_funds and not raw_holdings:
        gaps.append("target_fund_lookthrough_unavailable")
    return gaps


def require_stale_confirmation(frozen: dict[str, Any], *, confirmed: bool) -> None:
    if (
        str(frozen.get("stale_level") or "") == "confirmation_required"
        and not confirmed
    ):
        raise StaleHoldingsConfirmationRequired(frozen)


def _is_equity_fund(
    *,
    fund_name: str,
    fund_type: str | None,
    candidate: dict[str, Any],
) -> bool:
    text = f"{fund_name} {fund_type or ''}".casefold()
    if any(token in text for token in ("债券", "货币", "商品", "黄金")):
        return False
    if "股票" in text or "equity" in text:
        return True
    if any(token in text for token in ("etf", "指数")):
        return bool(
            candidate.get("top_holdings") or candidate.get("lookthrough_holdings")
        )
    return False


def _security_identity(value: object) -> tuple[str | None, str, bool]:
    raw = str(value or "").strip().upper()
    match = _EQUITY_CODE.fullmatch(raw)
    if match is None:
        market = (
            "HK"
            if raw.startswith("HK")
            else "US"
            if any(char.isalpha() for char in raw)
            else "unknown"
        )
        return None, market, False
    code = match.group(1)
    if not code.startswith(_A_SHARE_PREFIXES):
        return code, "unsupported", False
    market = (
        "BJ"
        if code.startswith(("4", "8", "92"))
        else "SH"
        if code.startswith(("6",))
        else "SZ"
    )
    return code, market, True


def _normalize_code(value: object) -> str:
    raw = str(value or "").strip().upper()
    match = _EQUITY_CODE.fullmatch(raw)
    return match.group(1) if match else raw


def _date_text(value: object) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    match = re.search(
        r"(?<!\d)(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})(?:日)?(?!\d)", raw
    )
    if match is not None:
        candidate = (
            f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
        )
    else:
        quarter = re.search(
            r"(?<!\d)(\d{4})\s*年?\s*(?:第\s*)?([1-4一二三四])\s*(?:季度|季)(?!\d)",
            raw,
            flags=re.IGNORECASE,
        ) or re.search(r"(?<!\d)(\d{4})\s*年?\s*[Qq]([1-4])(?!\d)", raw)
        if quarter is None:
            return None
        quarter_number = {"一": 1, "二": 2, "三": 3, "四": 4}.get(
            quarter.group(2),
            int(quarter.group(2)) if quarter.group(2).isdigit() else 0,
        )
        quarter_end = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}.get(
            quarter_number
        )
        if quarter_end is None:
            return None
        candidate = f"{quarter.group(1)}-{quarter_end}"
    try:
        return date.fromisoformat(candidate).isoformat()
    except ValueError:
        return None


def _age_days(value: str | None, *, now: datetime | date | None) -> int | None:
    if not value:
        return None
    current = now or datetime.now(timezone.utc)
    current_date = current.date() if isinstance(current, datetime) else current
    return max(0, (current_date - date.fromisoformat(value)).days)


def _record(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _integer(value: object) -> int | None:
    number = _number(value)
    return int(number) if number is not None and number >= 1 else None


__all__ = [
    "FundCandidateUnavailableError",
    "FundHoldingReportError",
    "FundSnapshotUnavailableError",
    "FundTypeUnsupportedError",
    "StaleHoldingsConfirmationRequired",
    "freeze_fund_holdings",
    "require_stale_confirmation",
]
