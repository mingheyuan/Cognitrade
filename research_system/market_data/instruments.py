from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator


class AssetType(StrEnum):
    """Stable asset identities understood by the market-data boundary."""

    STOCK = "stock"
    EXCHANGE_FUND = "exchange_fund"
    OTC_FUND = "otc_fund"
    INDEX = "index"


class SecurityIdentityError(ValueError):
    """A public, machine-readable identity resolution failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)


class SecurityIdentity(BaseModel):
    """Canonical security identity used before a collection contract is chosen.

    ``symbol`` is deliberately namespace-safe: mainland exchange instruments use
    an exchange suffix, while OTC fund codes remain bare six-digit identifiers.
    The existing stock storage model can strip the suffix at its compatibility
    boundary, but callers must never infer an asset type from the bare code again.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str = Field(min_length=1, max_length=64)
    code: str = Field(min_length=1, max_length=32)
    asset_type: AssetType
    market: str = Field(min_length=1, max_length=32)
    exchange: str = Field(min_length=1, max_length=32)
    exchange_timezone: str = Field(min_length=1, max_length=64)
    security_type: str = Field(min_length=1, max_length=32)
    name: str | None = Field(default=None, max_length=256)
    currency: str = Field(default="CNY", min_length=3, max_length=8)
    resolution_source: str = Field(min_length=1, max_length=64)

    @field_validator("symbol", "code", "market", "exchange", "currency")
    @classmethod
    def _upper_identifiers(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("security_type", "exchange_timezone", "resolution_source")
    @classmethod
    def _strip_identifiers(cls, value: str) -> str:
        return value.strip()

    @computed_field
    @property
    def instrument_id(self) -> str:
        return f"{self.asset_type.value}:{self.exchange}:{self.code}"

    @property
    def is_fund(self) -> bool:
        return self.asset_type in {AssetType.EXCHANGE_FUND, AssetType.OTC_FUND}


@runtime_checkable
class FundSecurityMasterProtocol(Protocol):
    """Structural boundary implemented by the finance pack's FundSecurityMaster."""

    def resolve(
        self,
        query: str,
        *,
        supplied_codes: list[str],
        allow_network: bool = True,
    ) -> Any: ...


_A_SHARE_CODE = re.compile(
    r"^(?:(?P<prefix>SH|SZ|BJ))?(?P<code>\d{6})(?:\.(?P<suffix>SH|SZ|BJ))?$",
    re.IGNORECASE,
)
_HK_CODE = re.compile(
    r"^(?:HK(?P<prefix>\d{1,5})|SEHK\.(?P<sehk>\d{1,5})|(?P<suffix>\d{1,5})\.HK)$",
    re.IGNORECASE,
)
_US_CODE = re.compile(r"^(?P<ticker>[A-Z][A-Z0-9.\-]{0,9})$")

_SH_STOCK_PREFIXES = (
    "600",
    "601",
    "603",
    "605",
    "606",
    "688",
    "689",
)
_SZ_STOCK_PREFIXES = ("000", "001", "002", "003", "300", "301")
_BJ_STOCK_PREFIXES = ("4", "8", "92")
_SH_EXCHANGE_FUND_PREFIXES = (
    "500",
    "501",
    "502",
    "510",
    "511",
    "512",
    "513",
    "515",
    "516",
    "517",
    "518",
    "519",
    "560",
    "561",
    "562",
    "563",
    "588",
)
_SZ_EXCHANGE_FUND_PREFIXES = (
    "159",
    "160",
    "161",
    "162",
    "163",
    "164",
    "165",
    "166",
)
_CONVERTIBLE_BOND_PREFIXES = ("110", "111", "113", "118", "123", "127", "128")
_KNOWN_INDEX_CODES = frozenset({"000300", "000852", "000905", "399001", "399006"})
_FUND_HINTS = ("基金", "ETF", "LOF", "FUND")


class SecurityIdentityResolver:
    """Resolve an input into one canonical, typed security identity.

    An injected fund master is authoritative and is consulted before numeric
    prefix heuristics.  Network access is always caller-controlled; GET/report
    paths can therefore resolve from already supplied identity without causing
    hidden acquisition.
    """

    def __init__(self, fund_master: FundSecurityMasterProtocol | None = None) -> None:
        self.fund_master = fund_master

    def resolve(
        self,
        symbol: str,
        *,
        asset_type: AssetType | str | None = None,
        name: str | None = None,
        fund_type: str | None = None,
        query: str | None = None,
        allow_network: bool = False,
    ) -> SecurityIdentity:
        raw = str(symbol or "").strip().upper()
        if not raw:
            raise SecurityIdentityError("security_symbol_required", "证券代码不能为空")

        declared = self._asset_type(asset_type)
        if declared == AssetType.STOCK:
            return self.resolve_stock(raw, name=name)
        if declared in {AssetType.EXCHANGE_FUND, AssetType.OTC_FUND}:
            return self.resolve_fund(
                raw,
                asset_type=declared,
                name=name,
                fund_type=fund_type,
                resolution_source="explicit_asset_type",
            )
        if declared == AssetType.INDEX:
            return self._resolve_index(raw, name=name, source="explicit_asset_type")

        fund_match = self._fund_master_match(
            raw,
            query=query,
            allow_network=allow_network,
        )
        if fund_match is not None:
            return self.resolve_fund(
                raw,
                name=name or self._text(fund_match.get("fund_name")),
                fund_type=fund_type or self._text(fund_match.get("fund_type")),
                resolution_source="fund_security_master",
            )

        code_match = _A_SHARE_CODE.fullmatch(raw)
        if code_match is not None:
            code = code_match.group("code")
            hinted_fund = self._looks_like_fund(query, name, fund_type)
            if hinted_fund:
                return self.resolve_fund(
                    code,
                    name=name,
                    fund_type=fund_type,
                    resolution_source="explicit_fund_context",
                )
            if code in _KNOWN_INDEX_CODES:
                return self._resolve_index(code, name=name, source="known_index_code")
            if self._exchange_fund_exchange(code) is not None:
                return self.resolve_fund(
                    code,
                    name=name,
                    fund_type=fund_type,
                    resolution_source="exchange_fund_prefix",
                )
            if self._stock_exchange(code) is not None:
                return self.resolve_stock(raw, name=name)
            if code.startswith(_CONVERTIBLE_BOND_PREFIXES):
                raise SecurityIdentityError(
                    "security_identity_ambiguous",
                    f"证券代码 {code} 可能属于基金或债券命名空间，请显式提供资产类型或基金目录证据",
                )
            # Six-digit codes outside an exchange security namespace are public
            # fund codes in the fund-holding entry flow.  This is why 020464 is
            # an OTC fund rather than a synthetic Shenzhen stock.
            return self.resolve_fund(
                code,
                name=name,
                fund_type=fund_type,
                asset_type=AssetType.OTC_FUND,
                resolution_source="non_exchange_fund_code",
            )

        us_symbol = re.sub(r"\.(?:US|NYSE|NASDAQ)$", "", raw)
        if _HK_CODE.fullmatch(raw) or _US_CODE.fullmatch(us_symbol):
            return self.resolve_stock(raw, name=name)
        raise SecurityIdentityError(
            "security_identity_not_found",
            f"无法解析证券身份：{symbol}",
        )

    def resolve_candidates(
        self,
        symbol: str,
        *,
        asset_type: AssetType | str | None = None,
        name: str | None = None,
        fund_type: str | None = None,
        query: str | None = None,
        allow_network: bool = False,
    ) -> tuple[SecurityIdentity, ...]:
        """Resolve every credible identity without guessing across namespaces.

        A bare six-digit code can simultaneously identify an A-share and an OTC
        fund (for example ``000001``). Explicit asset types and exchange suffixes
        remain deterministic; an ``auto`` lookup returns both catalog-backed
        candidates so the caller must choose.
        """

        declared = self._asset_type(asset_type)
        if declared is not None or self._looks_like_fund(name, fund_type, query):
            return (
                self.resolve(
                    symbol,
                    asset_type=declared,
                    name=name,
                    fund_type=fund_type,
                    query=query,
                    allow_network=allow_network,
                ),
            )

        raw = str(symbol or "").strip().upper()
        code_match = _A_SHARE_CODE.fullmatch(raw)
        explicitly_exchanged = bool(
            code_match
            and (code_match.group("prefix") or code_match.group("suffix"))
        )
        if not allow_network or explicitly_exchanged or code_match is None:
            return (
                self.resolve(
                    symbol,
                    name=name,
                    query=query,
                    allow_network=False,
                ),
            )

        fund_match, catalog_source = self._fund_master_lookup(
            raw,
            query=query,
            allow_network=True,
        )
        if (
            fund_match is None
            and catalog_source == "fund_catalog_unavailable"
            and self._requires_fund_catalog_disambiguation(code_match.group("code"))
        ):
            raise SecurityIdentityError(
                "fund_catalog_unavailable",
                f"基金目录暂不可用，无法安全判定裸代码 {raw}；请显式指定资产类型或交易所后缀",
            )

        fund = None
        if fund_match is not None:
            fund = self.resolve_fund(
                raw,
                name=name or self._text(fund_match.get("fund_name")),
                fund_type=self._text(fund_match.get("fund_type")),
                resolution_source="fund_security_master",
            )

        try:
            primary = self.resolve(
                symbol,
                name=name,
                query=query,
                allow_network=False,
            )
        except SecurityIdentityError as exc:
            # Convertible-bond/fund namespaces such as 110011 cannot produce a
            # supported stock candidate. A catalog-backed fund match is then
            # sufficient to resolve the identity deterministically.
            if exc.code == "security_identity_ambiguous" and fund is not None:
                return (fund,)
            raise

        if fund is None:
            return (primary,)
        if fund.instrument_id == primary.instrument_id:
            return (fund,)
        return (primary, fund)

    def resolve_stock(self, symbol: str, *, name: str | None = None) -> SecurityIdentity:
        raw = str(symbol or "").strip().upper()
        hk_match = _HK_CODE.fullmatch(raw)
        if hk_match is not None:
            code = (
                hk_match.group("prefix")
                or hk_match.group("sehk")
                or hk_match.group("suffix")
                or ""
            ).zfill(5)
            return SecurityIdentity(
                symbol=f"{code}.HK",
                code=code,
                asset_type=AssetType.STOCK,
                market="HK_EQUITY",
                exchange="HKEX",
                exchange_timezone="Asia/Hong_Kong",
                security_type="stock",
                name=self._text(name),
                currency="HKD",
                resolution_source="stock_symbol",
            )

        a_share = _A_SHARE_CODE.fullmatch(raw)
        if a_share is not None:
            code = a_share.group("code")
            exchange = self._stock_exchange(code)
            if exchange is None:
                raise SecurityIdentityError(
                    "invalid_stock_symbol",
                    f"{code} 不属于受支持的 A 股股票代码范围",
                )
            prefix = (a_share.group("prefix") or "").upper()
            suffix = (a_share.group("suffix") or "").upper()
            if prefix and suffix and prefix != suffix:
                raise SecurityIdentityError(
                    "security_exchange_mismatch",
                    f"{symbol} 同时声明了冲突的交易所前后缀",
                )
            declared_exchange = prefix or suffix
            if declared_exchange and declared_exchange != exchange:
                raise SecurityIdentityError(
                    "security_exchange_mismatch",
                    f"{code} 的交易所应为 {exchange}，而不是 {declared_exchange}",
                )
            return SecurityIdentity(
                symbol=f"{code}.{exchange}",
                code=code,
                asset_type=AssetType.STOCK,
                market="CN_EQUITY",
                exchange=exchange,
                exchange_timezone="Asia/Shanghai",
                security_type="stock",
                name=self._text(name),
                currency="CNY",
                resolution_source="stock_symbol",
            )

        us_match = _US_CODE.fullmatch(re.sub(r"\.(?:US|NYSE|NASDAQ)$", "", raw))
        if us_match is not None:
            ticker = us_match.group("ticker")
            return SecurityIdentity(
                symbol=ticker,
                code=ticker,
                asset_type=AssetType.STOCK,
                market="US_EQUITY",
                exchange="US",
                exchange_timezone="America/New_York",
                security_type="stock",
                name=self._text(name),
                currency="USD",
                resolution_source="stock_symbol",
            )
        raise SecurityIdentityError("invalid_stock_symbol", f"不支持的股票代码：{symbol}")

    def resolve_fund(
        self,
        symbol: str,
        *,
        asset_type: AssetType | str | None = None,
        name: str | None = None,
        fund_type: str | None = None,
        resolution_source: str = "fund_context",
    ) -> SecurityIdentity:
        code = self._six_digit_code(symbol)
        declared = self._asset_type(asset_type)
        if declared not in {None, AssetType.EXCHANGE_FUND, AssetType.OTC_FUND}:
            raise SecurityIdentityError(
                "asset_type_mismatch",
                f"{declared.value} 不能用于基金身份",
            )
        inferred_exchange = self._exchange_fund_exchange(code)
        exchange_traded = self._is_exchange_fund(
            code,
            fund_type=fund_type,
            declared=declared,
        )
        resolved_asset = (
            AssetType.EXCHANGE_FUND if exchange_traded else AssetType.OTC_FUND
        )
        exchange = inferred_exchange if exchange_traded and inferred_exchange else "OTC"
        security_type = self._fund_security_type(fund_type, exchange_traded=exchange_traded)
        canonical = f"{code}.{exchange}" if exchange_traded else code
        return SecurityIdentity(
            symbol=canonical,
            code=code,
            asset_type=resolved_asset,
            market="CN_FUND",
            exchange=exchange,
            exchange_timezone="Asia/Shanghai",
            security_type=security_type,
            name=self._text(name),
            currency="CNY",
            resolution_source=resolution_source,
        )

    def _fund_master_match(
        self,
        symbol: str,
        *,
        query: str | None,
        allow_network: bool,
    ) -> dict[str, Any] | None:
        match, _source = self._fund_master_lookup(
            symbol,
            query=query,
            allow_network=allow_network,
        )
        return match

    def _fund_master_lookup(
        self,
        symbol: str,
        *,
        query: str | None,
        allow_network: bool,
    ) -> tuple[dict[str, Any] | None, str | None]:
        if self.fund_master is None:
            return None, None
        try:
            code = self._six_digit_code(symbol)
        except SecurityIdentityError:
            return None, None
        result = self.fund_master.resolve(
            query or code,
            supplied_codes=[code],
            allow_network=allow_network,
        )
        source = self._text(getattr(result, "source", None))
        for match in list(getattr(result, "matches", None) or []):
            if not isinstance(match, dict):
                continue
            if self._normalized_fund_code(match.get("fund_code")) == code:
                return dict(match), source
        fund_codes = [
            self._normalized_fund_code(value)
            for value in list(getattr(result, "fund_codes", None) or [])
        ]
        return ({"fund_code": code} if code in fund_codes else None), source

    @staticmethod
    def _requires_fund_catalog_disambiguation(code: str) -> bool:
        """Whether a bare code overlaps a supported non-fund namespace."""

        return bool(
            SecurityIdentityResolver._stock_exchange(code) is not None
            or code.startswith(_CONVERTIBLE_BOND_PREFIXES)
        )

    @staticmethod
    def _stock_exchange(code: str) -> str | None:
        if code.startswith(_SH_STOCK_PREFIXES):
            return "SH"
        if code.startswith(_SZ_STOCK_PREFIXES):
            return "SZ"
        if code.startswith(_BJ_STOCK_PREFIXES):
            return "BJ"
        return None

    @staticmethod
    def _exchange_fund_exchange(code: str) -> str | None:
        if code.startswith(_SH_EXCHANGE_FUND_PREFIXES):
            return "SH"
        if code.startswith(_SZ_EXCHANGE_FUND_PREFIXES):
            return "SZ"
        return None

    @classmethod
    def _is_exchange_fund(
        cls,
        code: str,
        *,
        fund_type: str | None,
        declared: AssetType | None,
    ) -> bool:
        if declared == AssetType.EXCHANGE_FUND:
            if cls._exchange_fund_exchange(code) is None:
                raise SecurityIdentityError(
                    "invalid_exchange_fund_symbol",
                    f"{code} 不是受支持的场内基金代码",
                )
            return True
        if declared == AssetType.OTC_FUND:
            return False
        normalized_type = str(fund_type or "").strip().upper()
        if "ETF联接" in str(fund_type or "") or "ETF FEEDER" in normalized_type:
            return False
        if "ETF" in normalized_type or "LOF" in normalized_type:
            return cls._exchange_fund_exchange(code) is not None
        return cls._exchange_fund_exchange(code) is not None

    @staticmethod
    def _fund_security_type(fund_type: str | None, *, exchange_traded: bool) -> str:
        normalized = str(fund_type or "").strip().upper()
        if "ETF联接" in str(fund_type or "") or "ETF FEEDER" in normalized:
            return "etf_feeder"
        if "ETF" in normalized:
            return "etf"
        if "LOF" in normalized:
            return "lof"
        return "exchange_fund" if exchange_traded else "mutual_fund"

    @classmethod
    def _resolve_index(
        cls,
        symbol: str,
        *,
        name: str | None,
        source: str,
    ) -> SecurityIdentity:
        code = cls._six_digit_code(symbol)
        exchange = "SH" if code.startswith("0") else "SZ"
        return SecurityIdentity(
            symbol=f"{code}.{exchange}",
            code=code,
            asset_type=AssetType.INDEX,
            market="CN_INDEX",
            exchange=exchange,
            exchange_timezone="Asia/Shanghai",
            security_type="index",
            name=cls._text(name),
            currency="CNY",
            resolution_source=source,
        )

    @staticmethod
    def _asset_type(value: AssetType | str | None) -> AssetType | None:
        if value is None or not str(value).strip():
            return None
        if isinstance(value, AssetType):
            return value
        aliases = {
            "equity": AssetType.STOCK,
            "etf": AssetType.EXCHANGE_FUND,
            "lof": AssetType.EXCHANGE_FUND,
            "mutual_fund": AssetType.OTC_FUND,
            "fund": AssetType.OTC_FUND,
        }
        normalized = str(value).strip().lower()
        try:
            return aliases.get(normalized) or AssetType(normalized)
        except ValueError as exc:
            raise SecurityIdentityError(
                "unsupported_asset_type",
                f"不支持的资产类型：{value}",
            ) from exc

    @staticmethod
    def _six_digit_code(value: Any) -> str:
        raw = str(value or "").strip().upper()
        match = _A_SHARE_CODE.fullmatch(raw)
        if match is None:
            raise SecurityIdentityError("invalid_fund_symbol", f"基金代码必须为六位数字：{value}")
        return match.group("code")

    @staticmethod
    def _normalized_fund_code(value: Any) -> str:
        raw = str(value or "").strip().upper().split(".", 1)[0]
        return raw.zfill(6) if raw.isdigit() and len(raw) <= 6 else ""

    @staticmethod
    def _looks_like_fund(*values: str | None) -> bool:
        text = " ".join(str(value or "") for value in values).upper()
        return any(token in text for token in _FUND_HINTS)

    @staticmethod
    def _text(value: Any) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None


__all__ = [
    "AssetType",
    "FundSecurityMasterProtocol",
    "SecurityIdentity",
    "SecurityIdentityError",
    "SecurityIdentityResolver",
]
