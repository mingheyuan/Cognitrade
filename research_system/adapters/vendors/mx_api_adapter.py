from __future__ import annotations

from typing import Any

import requests


class MxApiAdapter:
    """Authenticated 东方财富妙想 Skills Hub adapter aligned with UZI 3.9.4."""

    vendor_name = "mx_api"
    _BASE = "https://mkapi2.dfcfs.com/finskillshub/api/claw"

    def __init__(self, api_key: str) -> None:
        self.api_key = str(api_key or "").strip()
        if not self.api_key:
            raise ValueError("MX_APIKEY is required")

    def get_fundamental_snapshot(self, *, symbol: str) -> dict[str, Any]:
        payload = self._query(f"{symbol} 最新价 总市值 PE PB 所属行业 主营业务 财务指标")
        row = self._first_table_row(payload)
        if not row:
            raise ValueError(f"no MX API data returned for symbol {symbol}")
        return {
            "symbol": symbol,
            "info": row,
            "financial_abstract": row,
            "financial_analysis": {},
            "market_valuation": row,
            "financial_statements": {},
        }

    def get_news(self, *, query: str) -> dict[str, Any]:
        return self._post("news-search", {"query": query})

    def query_text(self, *, query: str) -> dict[str, Any]:
        """Return UZI's compact qualitative text projection with full provenance."""

        payload = self._query(query)
        data = payload.get("data") or {}
        inner = data.get("data") or {}
        result = inner.get("searchDataResultDTO") or {}
        tables = result.get("dataTableDTOList") or []
        parts: list[str] = []
        for table in tables[:2]:
            if not isinstance(table, dict):
                continue
            title = table.get("title") or table.get("entityName") or ""
            if title:
                parts.append(str(title)[:120])
        text = "；".join(parts)[:300] if parts else str(inner.get("entityName") or "")[:200]
        if not text:
            raise ValueError("MX API returned no qualitative text")
        return {"query": query, "text": text, "source_id": "mx_api"}

    def _query(self, query: str) -> dict[str, Any]:
        return self._post("query", {"toolQuery": query})

    def _post(self, endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
        response = requests.post(
            f"{self._BASE}/{endpoint}",
            headers={"Content-Type": "application/json", "apikey": self.api_key},
            json=body,
            timeout=30,
        )
        if response.status_code in {401, 403}:
            raise PermissionError("MX API credential rejected")
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") not in {None, 0}:
            raise ValueError("MX API returned no usable result")
        return dict(payload)

    @staticmethod
    def _search_result(payload: dict[str, Any]) -> dict[str, Any]:
        data = payload.get("data") or {}
        inner = data.get("data") or {}
        return dict(inner.get("searchDataResultDTO") or {})

    @classmethod
    def _first_table_row(cls, payload: dict[str, Any]) -> dict[str, Any]:
        tables = cls._search_result(payload).get("dataTableDTOList") or []
        if not tables:
            return {}
        item = tables[0]
        table = item.get("table") or {}
        name_map = item.get("nameMap") or {}
        if isinstance(name_map, list):
            name_map = {str(index): value for index, value in enumerate(name_map)}
        row: dict[str, Any] = {"_mx_entity": item.get("entityName")}
        for key, values in table.items():
            if key == "headName":
                continue
            label = name_map.get(key) or name_map.get(str(key)) or str(key)
            row[str(label)] = values[-1] if isinstance(values, list) and values else values
        return row
